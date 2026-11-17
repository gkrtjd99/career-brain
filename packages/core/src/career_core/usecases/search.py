"""브레인 검색: 키워드(pg_trgm + 부분 문자열) + 의미(pgvector) 결합.

- 공간 격리는 RLS가 보장한다. 검색 SQL에도 공간 조건을 따로 두지 않는다(세션이 이미 한 공간이다).
- 키워드는 원본 테이블을 매번 그대로 본다. 항상 최신이다.
- 의미 검색은 본문 해시가 지금 본문과 같은 벡터만 쓴다. 고친 주장의 옛 벡터는 쓰지 않는다.
- 두 순위는 RRF(reciprocal rank fusion)로 합친다. 점수 척도가 달라도 섞을 수 있다.
- 작은 개인 자료 기준으로 정확(전수) 검색을 한다. HNSW 색인은 측정 후 필요할 때 추가한다.
- 키워드 쪽 검색어는 공간 별칭 사전(기본 표 + `workspace_settings.tech_aliases`)으로 넓힌다: `패스트API`로 찾으면
  `FastAPI`·`fast api`도 그 낱말의 일치로 센다 (점수의 분모는 질문 낱말 수 그대로, RRF 합치기는 같다).
  넓힌 표기는 결과의 `expanded_terms`에 남는다. 영문 표기는 낱말 경계, 한글 표기는 앞에 한글이 붙지 않을 때만.
- 학습 메모(`study_note`)·오답(`study_mistake`)은 `kinds`로 요청하면 찾는다. 결과의 `record`는 `study`로,
  경험(`experience`)과 구분한다. 학습 기록은 경험의 근거가 아니다 (경험 제목·주장 상태를 붙이지 않는다).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from uuid import UUID

from sqlalchemy import text

from career_core.db import Database
from career_core.db.types import to_pgvector
from career_core.domain.korean import TechAliases, content_words, korean_analyzer, strip_particle
from career_core.llm import EMBED_QUERY, ModelCallError, ModelDenied
from career_core.llm.gateway import ModelGateway, scoped_gateway
from career_core.ports import ActorContext
from career_core.usecases.indexing import CURRENT_BLOCKS, MAX_TEXT_CHARS, STUDY_MISTAKE_BODY
from career_core.usecases.workspace_settings import workspace_aliases

# 기본 검색 대상(브레인: 경험·원문). 학습 기록은 `kinds`로 요청할 때만 (MCP·문서 근거 찾기의 기본값은 그대로).
KINDS = ("claim", "block", "answer")
STUDY_KINDS = ("study_note", "study_mistake")
ALL_KINDS = KINDS + STUDY_KINDS
# 결과 묶음 표시: 경험(주장·인터뷰 답) / 원문(파싱 블록) / 학습 기록(메모·오답, 경험 아님)
RECORDS = {
    "claim": "experience",
    "answer": "experience",
    "block": "source",
    "study_note": "study",
    "study_mistake": "study",
}
RRF_K = 60
# 의미 검색 순위의 비중. 합성 30문항에서 0.5~1.0 사이 차이가 거의 없어 표준 RRF(1.0)를 쓴다.
SEMANTIC_WEIGHT = 1.0
MIN_WORD_SIMILARITY = 0.3
MAX_QUERY_CHARS = 500
SNIPPET_CHARS = 240

ITEMS_SQL = f"""
WITH items AS (
    SELECT 'claim' AS kind, c.id, c.statement AS body, c.status,
           c.experience_id, ex.title AS experience_title,
           NULL::uuid AS source_id, NULL::text AS source_title, NULL::int AS page,
           NULL::text AS question, NULL::uuid AS parse_run_id, NULL::int AS ordinal,
           NULL::uuid AS study_item_id, NULL::text AS study_title
    FROM core.claims c JOIN core.experiences ex ON ex.id = c.experience_id
    WHERE c.status <> 'retired'
    UNION ALL
    SELECT 'block', b.id, b.text, NULL, NULL, NULL, s.id, s.title, b.page, NULL,
           b.parse_run_id, b.ordinal, NULL, NULL
    FROM core.document_blocks b
    JOIN core.parse_runs pr ON pr.id = b.parse_run_id
    JOIN core.source_versions sv ON sv.id = pr.source_version_id
    JOIN core.sources s ON s.id = sv.source_id
    WHERE b.parse_run_id IN ({CURRENT_BLOCKS})
    UNION ALL
    SELECT 'answer', a.id, a.answer, NULL, se.experience_id, ex.title, NULL, NULL, NULL, q.prompt,
           NULL, NULL, NULL, NULL
    FROM core.interview_answers a
    JOIN core.interview_questions q ON q.id = a.question_id
    JOIN core.interview_sessions se ON se.id = q.session_id
    LEFT JOIN core.experiences ex ON ex.id = se.experience_id
    UNION ALL
    SELECT 'study_note', ss.id, ss.notes, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
           NULL, NULL, si.id, si.title
    FROM core.study_sessions ss JOIN core.study_items si ON si.id = ss.study_item_id
    WHERE ss.notes IS NOT NULL AND btrim(ss.notes) <> ''
    UNION ALL
    SELECT 'study_mistake', m.id, {STUDY_MISTAKE_BODY}, NULL, NULL, NULL, NULL, NULL, NULL,
           m.question, NULL, NULL, m.study_item_id, COALESCE(si.title, sa.title)
    FROM core.study_mistakes m
    LEFT JOIN core.study_items si ON si.id = m.study_item_id
    LEFT JOIN core.study_attempts at ON at.id = m.attempt_id
    LEFT JOIN core.study_assessments sa ON sa.id = at.assessment_id
)
"""

# 낱말 일치 점수: 질문 낱말(묶음 g)마다 원래 표기(ILIKE) 또는 넓힌 별칭 표기(정규식) 중 하나라도 맞으면 1.
# 묶음 수가 분모라 별칭을 더해도 별칭이 없는 결과의 점수는 그대로다.
KEYWORD_SQL = (
    ITEMS_SQL
    + """
SELECT i.*,
       0.5 * word_similarity(CAST(:q AS text), i.body)
       + 0.5 * (SELECT count(DISTINCT t.g)
                FROM unnest(CAST(:pats AS text[]), CAST(:pat_groups AS int[]),
                            CAST(:pat_regex AS boolean[])) AS t(p, g, rx)
                WHERE CASE WHEN t.rx THEN i.body ~* t.p ELSE i.body ILIKE t.p END)::float
         / GREATEST(CAST(:ngroups AS int), 1) AS score
FROM items i
WHERE i.kind = ANY(:kinds)
  AND (word_similarity(CAST(:q AS text), i.body) >= :min_sim
       OR i.body ILIKE ANY(CAST(:likes AS text[]))
       OR i.body ~* ANY(CAST(:regexes AS text[])))
ORDER BY score DESC, i.kind, i.id
LIMIT :k
"""
)

SEMANTIC_SQL = (
    ITEMS_SQL
    + f"""
SELECT i.*, e.embedding <=> CAST(:qv AS vector) AS distance
FROM items i
JOIN core.embeddings e
  ON e.target_kind = i.kind AND e.target_id = i.id AND e.space = :space
 AND e.content_hash = encode(sha256(convert_to(left(i.body, {MAX_TEXT_CHARS}), 'UTF8')), 'hex')
WHERE i.kind = ANY(:kinds)
ORDER BY distance, i.kind, i.id
LIMIT :k
"""
)


@dataclass(frozen=True, slots=True)
class SearchHit:
    kind: str
    id: UUID
    snippet: str
    score: float
    keyword_rank: int | None = None
    semantic_rank: int | None = None
    claim_status: str | None = None
    experience_id: UUID | None = None
    experience_title: str | None = None
    source_id: UUID | None = None
    source_title: str | None = None
    page: int | None = None
    question: str | None = None
    # 원문(block) 결과만: 원본 보기를 열 위치. match_*는 블록 원문 안에서 처음 일치한 낱말의 글자 범위.
    block_id: UUID | None = None
    parse_run_id: UUID | None = None
    ordinal: int | None = None
    match_start: int | None = None
    match_end: int | None = None
    # experience | source | study. study(학습 메모·오답)는 경험이 아니다.
    record: str = "experience"
    # 학습 기록만: 그 기록의 학습 항목(없으면 None)과 항목·시험 제목
    study_item_id: UUID | None = None
    study_title: str | None = None


@dataclass(frozen=True, slots=True)
class ExpandedTerm:
    """별칭 사전으로 넓힌 질문 낱말: `term`(질문에 쓴 꼴)을 `spellings`로도 찾았다."""

    term: str
    canonical: str  # 대표 표기 (사용자 항목이면 쓴 그대로, 예: "FastAPI")
    spellings: list[str]


@dataclass(frozen=True, slots=True)
class SearchResult:
    query: str
    hits: list[SearchHit]
    # "hybrid" | "keyword". keyword면 semantic_note에 이유를 적는다.
    mode: str
    semantic_note: str | None = None
    notes: list[str] = field(default_factory=list)
    # 키워드 검색에 더한 별칭 표기 (없으면 빈 목록). 의미 검색(질문 임베딩)에는 넣지 않는다.
    expanded_terms: list[ExpandedTerm] = field(default_factory=list)


_HANGUL_TOKEN = re.compile(r"[가-힣]+")


def query_terms(query: str) -> list[str]:
    """두 글자 이상 낱말. 한국어 조사가 붙은 원문에도 부분 일치로 걸린다.

    형태소 분석기(선택 설치 `korean`, `CAREER_KOREAN_ANALYZER=kiwi`)가 있으면 한글 낱말을 내용어로 바꾼다
    ("결제시스템을" → 결제, 시스템). 없으면 원문 낱말 그대로 (기본).
    """
    morph = korean_analyzer().morphological
    seen: list[str] = []
    for raw in re.split(r"[\s,.;:!?()\[\]{}\"'`]+", query):
        raw = raw.strip()
        parts = content_words(raw) if morph and _HANGUL_TOKEN.fullmatch(raw) else []
        for t in parts or [raw]:
            if len(t) >= 2 and t.lower() not in (s.lower() for s in seen):
                seen.append(t)
    return seen[:12]


def _regex_escape(value: str) -> str:
    """PostgreSQL ARE와 Python re 둘 다에서 글자 그대로인 꼴 (영숫자·한글 밖의 글자 앞에 역슬래시)."""
    return "".join(c if c.isalnum() else "\\" + c for c in value)


def alias_pattern(spelling: str, aliases: TechAliases) -> str:
    """별칭 표기 하나의 정규식 (대소문자는 부르는 쪽이 무시). 표기는 첫 묶음.

    영문 표기는 앞뒤가 영숫자가 아닐 때만 ("ts"가 "tests"에 걸리지 않게), 한글 표기는 앞에 한글이 붙지 않고
    더 긴 표기의 앞부분이 아닐 때만 ("자바"가 "자바스크립트"에 걸리지 않게). domain.korean과 같은 규칙.
    """
    body = _regex_escape(spelling)
    if spelling.isascii():
        return f"(?:^|[^A-Za-z0-9])({body})(?:$|[^A-Za-z0-9])"
    longer = [_regex_escape(x[len(spelling) :]) for x in aliases.longer_hangul(spelling)]
    ahead = "".join(f"(?!{x})" for x in longer if x)
    return f"(?:^|[^가-힣])({body}){ahead}"


def expand_terms(
    query: str, terms: list[str], aliases: TechAliases
) -> tuple[list[ExpandedTerm], list[tuple[int, str]]]:
    """질문 낱말 → 같은 기술의 다른 표기. (화면용 목록, [(질문 낱말 번호, 정규식)])."""
    expanded: list[ExpandedTerm] = []
    patterns: list[tuple[int, str]] = []
    have = {t.lower() for t in terms}
    done: set[str] = set()

    def add(group: int, term: str, canon: str) -> None:
        if canon in done:
            return
        done.add(canon)
        names = [
            x
            for x in aliases.spellings(canon)
            if x.lower() != term.lower() and x.lower() not in have
        ]
        if not names:
            return
        shown = [aliases.display(canon) if x == canon else x for x in names]
        expanded.append(ExpandedTerm(term, aliases.display(canon), shown))
        patterns.extend((group, alias_pattern(x, aliases)) for x in names)

    # 여러 낱말 표기("아마존 웹 서비스", "fast api")는 질문 전체에서 찾고 첫 낱말의 묶음으로 센다
    low_query = " ".join(query.split()).lower()
    for phrase in aliases.phrases():
        if phrase in low_query:
            first = phrase.split()[0]
            group = next((i for i, t in enumerate(terms) if t.lower().startswith(first)), 0)
            add(group, phrase, aliases.canonical(phrase))
    for i, t in enumerate(terms):
        for cand in dict.fromkeys((t, strip_particle(t))):
            canon = aliases.lookup(cand)
            if canon is not None:
                add(i, cand, canon)
                break
    return expanded, patterns


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def snippet(
    body: str,
    terms: list[str],
    width: int = SNIPPET_CHARS,
    patterns: tuple[re.Pattern[str], ...] = (),
) -> str:
    body = " ".join(body.split())
    if len(body) <= width:
        return body
    match = first_match(body, terms, patterns)
    start = max(match[0] - width // 4, 0) if match else 0
    piece = body[start : start + width]
    return ("…" if start else "") + piece + ("…" if start + width < len(body) else "")


def first_match(
    body: str, terms: list[str], patterns: tuple[re.Pattern[str], ...] = ()
) -> tuple[int, int] | None:
    """원문에서 가장 앞에 나오는 검색 낱말(또는 넓힌 별칭 표기)의 글자 범위 (대소문자 무시). 없으면 None."""
    lower = body.lower()
    best: tuple[int, int] | None = None
    for t in terms:
        i = lower.find(t.lower())
        if i >= 0 and (best is None or i < best[0]):
            best = (i, i + len(t))
    for p in patterns:
        m = p.search(body)
        if m and (best is None or m.start(1) < best[0]):
            best = m.span(1)
    return best


def _hit(row, terms: list[str], patterns: tuple[re.Pattern[str], ...] = (), **extra) -> SearchHit:
    block = row.kind == "block"
    match = first_match(row.body, terms, patterns) if block else None
    return SearchHit(
        kind=row.kind,
        id=row.id,
        snippet=snippet(row.body, terms, patterns=patterns),
        score=0.0,
        claim_status=row.status,
        experience_id=row.experience_id,
        experience_title=row.experience_title,
        source_id=row.source_id,
        source_title=row.source_title,
        page=row.page,
        question=row.question,
        block_id=row.id if block else None,
        parse_run_id=row.parse_run_id if block else None,
        ordinal=row.ordinal if block else None,
        match_start=match[0] if match else None,
        match_end=match[1] if match else None,
        record=RECORDS[row.kind],
        study_item_id=row.study_item_id,
        study_title=row.study_title,
        **extra,
    )


async def search(
    db: Database,
    actor: ActorContext,
    query: str,
    *,
    gateway: ModelGateway | None = None,
    kinds: tuple[str, ...] = KINDS,
    limit: int = 10,
    expand_aliases: bool = True,
) -> SearchResult:
    """`expand_aliases=False`면 별칭으로 넓히지 않는다 (평가 비교용)."""
    query = " ".join((query or "").split())[:MAX_QUERY_CHARS]
    kinds = tuple(k for k in kinds if k in ALL_KINDS) or KINDS
    limit = max(1, min(limit, 50))
    if not query:
        return SearchResult(query, [], "keyword", "empty_query")
    terms = query_terms(query)
    likes = [_like(t) for t in terms] or [_like(query)]
    pool = max(limit * 3, 20)

    async with db.workspace_session(actor.workspace_id) as s:
        expanded: list[ExpandedTerm] = []
        alias_res: list[tuple[int, str]] = []
        if expand_aliases and terms:
            expanded, alias_res = expand_terms(query, terms, await workspace_aliases(s))
        keyword_rows = (
            await s.execute(
                text(KEYWORD_SQL),
                {
                    "q": query,
                    "likes": likes,
                    "pats": likes + [p for _, p in alias_res],
                    "pat_groups": list(range(len(likes))) + [g for g, _ in alias_res],
                    "pat_regex": [False] * len(likes) + [True] * len(alias_res),
                    "ngroups": len(likes),
                    "regexes": [p for _, p in alias_res],
                    "kinds": list(kinds),
                    "min_sim": MIN_WORD_SIMILARITY,
                    "k": pool,
                },
            )
        ).all()
    patterns = tuple(re.compile(p, re.IGNORECASE) for _, p in alias_res)

    semantic_rows: list = []
    note = None
    gateway = await scoped_gateway(gateway, actor.workspace_id)  # 화면에서 덮은 임베딩 라우트 (O22)
    space = gateway.embedding_space() if gateway else None
    if gateway is None or space is None:
        # 키만 없는 경우를 따로 알린다 (설정은 했는데 OPENAI_API_KEY 등이 비어 있음)
        missing_key = gateway is not None and gateway.embedding_status() == "api_key_missing"
        note = "embedding_api_key_missing" if missing_key else "no_embedding_route"
    elif gateway.embedding_leaves_host() and not actor.has_scope("egress"):
        note = "egress_scope_required"  # 검색어도 외부 API로 나가는 자료다
    else:
        try:
            emb = await gateway.embed(
                actor, [query], data_classes=frozenset({"private"}), input_type=EMBED_QUERY
            )
        except ModelDenied as exc:
            note = f"denied:{exc.code}"
        except ModelCallError as exc:
            note = f"error:{exc.code}"
        else:
            async with db.workspace_session(actor.workspace_id) as s:
                semantic_rows = (
                    await s.execute(
                        text(SEMANTIC_SQL),
                        {
                            "qv": to_pgvector(emb.vectors[0]),
                            "space": emb.space,
                            "kinds": list(kinds),
                            "k": pool,
                        },
                    )
                ).all()
            if not semantic_rows:
                note = "index_empty"

    merged: dict[tuple[str, UUID], SearchHit] = {}
    scores: dict[tuple[str, UUID], float] = {}
    for rank, row in enumerate(keyword_rows, start=1):
        key = (row.kind, row.id)
        merged[key] = _hit(row, terms, patterns, keyword_rank=rank)
        scores[key] = 1 / (RRF_K + rank)
    for rank, row in enumerate(semantic_rows, start=1):
        key = (row.kind, row.id)
        hit = merged.get(key)
        merged[key] = (
            replace(hit, semantic_rank=rank)
            if hit
            else _hit(row, terms, patterns, semantic_rank=rank)
        )
        scores[key] = scores.get(key, 0.0) + SEMANTIC_WEIGHT / (RRF_K + rank)
    # 점수가 같으면 원문 낱말이 실제로 들어 있는 쪽(키워드 순위가 있는 쪽)을 앞에 둔다.
    ordered = sorted(
        merged,
        key=lambda k: (-scores[k], merged[k].keyword_rank is None, k[0], str(k[1])),
    )[:limit]
    hits = [replace(merged[k], score=round(scores[k], 6)) for k in ordered]
    mode = "hybrid" if semantic_rows else "keyword"
    return SearchResult(query, hits, mode, None if semantic_rows else note, expanded_terms=expanded)

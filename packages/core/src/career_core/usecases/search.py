"""브레인 검색: 키워드(pg_trgm + 부분 문자열) + 의미(pgvector) 결합.

- 공간 격리는 RLS가 보장한다. 검색 SQL에도 공간 조건을 따로 두지 않는다(세션이 이미 한 공간이다).
- 키워드는 원본 테이블을 매번 그대로 본다. 항상 최신이다.
- 의미 검색은 본문 해시가 지금 본문과 같은 벡터만 쓴다. 고친 주장의 옛 벡터는 쓰지 않는다.
- 두 순위는 RRF(reciprocal rank fusion)로 합친다. 점수 척도가 달라도 섞을 수 있다.
- 작은 개인 자료 기준으로 정확(전수) 검색을 한다. HNSW 색인은 측정 후 필요할 때 추가한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from uuid import UUID

from sqlalchemy import text

from career_core.db import Database
from career_core.db.types import to_pgvector
from career_core.llm import EMBED_QUERY, ModelCallError, ModelDenied
from career_core.llm.gateway import ModelGateway
from career_core.ports import ActorContext
from career_core.usecases.indexing import CURRENT_BLOCKS, MAX_TEXT_CHARS

KINDS = ("claim", "block", "answer")
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
           NULL::text AS question
    FROM core.claims c JOIN core.experiences ex ON ex.id = c.experience_id
    WHERE c.status <> 'retired'
    UNION ALL
    SELECT 'block', b.id, b.text, NULL, NULL, NULL, s.id, s.title, b.page, NULL
    FROM core.document_blocks b
    JOIN core.parse_runs pr ON pr.id = b.parse_run_id
    JOIN core.source_versions sv ON sv.id = pr.source_version_id
    JOIN core.sources s ON s.id = sv.source_id
    WHERE b.parse_run_id IN ({CURRENT_BLOCKS})
    UNION ALL
    SELECT 'answer', a.id, a.answer, NULL, se.experience_id, ex.title, NULL, NULL, NULL, q.prompt
    FROM core.interview_answers a
    JOIN core.interview_questions q ON q.id = a.question_id
    JOIN core.interview_sessions se ON se.id = q.session_id
    LEFT JOIN core.experiences ex ON ex.id = se.experience_id
)
"""

KEYWORD_SQL = (
    ITEMS_SQL
    + """
SELECT i.*,
       0.5 * word_similarity(CAST(:q AS text), i.body)
       + 0.5 * (SELECT count(*) FROM unnest(CAST(:likes AS text[])) l WHERE i.body ILIKE l)::float
         / GREATEST(cardinality(CAST(:likes AS text[])), 1) AS score
FROM items i
WHERE i.kind = ANY(:kinds)
  AND (word_similarity(CAST(:q AS text), i.body) >= :min_sim OR i.body ILIKE ANY(CAST(:likes AS text[])))
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


@dataclass(frozen=True, slots=True)
class SearchResult:
    query: str
    hits: list[SearchHit]
    # "hybrid" | "keyword". keyword면 semantic_note에 이유를 적는다.
    mode: str
    semantic_note: str | None = None
    notes: list[str] = field(default_factory=list)


def query_terms(query: str) -> list[str]:
    """두 글자 이상 낱말. 한국어 조사가 붙은 원문에도 부분 일치로 걸린다."""
    seen: list[str] = []
    for t in re.split(r"[\s,.;:!?()\[\]{}\"'`]+", query):
        t = t.strip()
        if len(t) >= 2 and t.lower() not in (s.lower() for s in seen):
            seen.append(t)
    return seen[:12]


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def snippet(body: str, terms: list[str], width: int = SNIPPET_CHARS) -> str:
    body = " ".join(body.split())
    if len(body) <= width:
        return body
    lower = body.lower()
    positions = [lower.find(t.lower()) for t in terms if lower.find(t.lower()) >= 0]
    start = max(min(positions) - width // 4, 0) if positions else 0
    piece = body[start : start + width]
    return ("…" if start else "") + piece + ("…" if start + width < len(body) else "")


def _hit(row, terms: list[str], **extra) -> SearchHit:
    return SearchHit(
        kind=row.kind,
        id=row.id,
        snippet=snippet(row.body, terms),
        score=0.0,
        claim_status=row.status,
        experience_id=row.experience_id,
        experience_title=row.experience_title,
        source_id=row.source_id,
        source_title=row.source_title,
        page=row.page,
        question=row.question,
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
) -> SearchResult:
    query = " ".join((query or "").split())[:MAX_QUERY_CHARS]
    kinds = tuple(k for k in kinds if k in KINDS) or KINDS
    limit = max(1, min(limit, 50))
    if not query:
        return SearchResult(query, [], "keyword", "empty_query")
    terms = query_terms(query)
    pool = max(limit * 3, 20)

    async with db.workspace_session(actor.workspace_id) as s:
        keyword_rows = (
            await s.execute(
                text(KEYWORD_SQL),
                {
                    "q": query,
                    "likes": [_like(t) for t in terms] or [_like(query)],
                    "kinds": list(kinds),
                    "min_sim": MIN_WORD_SIMILARITY,
                    "k": pool,
                },
            )
        ).all()

    semantic_rows: list = []
    note = None
    space = gateway.embedding_space() if gateway else None
    if gateway is None or space is None:
        note = "no_embedding_route"
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
        merged[key] = _hit(row, terms, keyword_rank=rank)
        scores[key] = 1 / (RRF_K + rank)
    for rank, row in enumerate(semantic_rows, start=1):
        key = (row.kind, row.id)
        hit = merged.get(key)
        merged[key] = (
            replace(hit, semantic_rank=rank) if hit else _hit(row, terms, semantic_rank=rank)
        )
        scores[key] = scores.get(key, 0.0) + SEMANTIC_WEIGHT / (RRF_K + rank)
    # 점수가 같으면 원문 낱말이 실제로 들어 있는 쪽(키워드 순위가 있는 쪽)을 앞에 둔다.
    ordered = sorted(
        merged,
        key=lambda k: (-scores[k], merged[k].keyword_rank is None, k[0], str(k[1])),
    )[:limit]
    hits = [replace(merged[k], score=round(scores[k], 6)) for k in ordered]
    mode = "hybrid" if semantic_rows else "keyword"
    return SearchResult(query, hits, mode, None if semantic_rows else note)

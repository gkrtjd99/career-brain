"""c0026 백로그: kiwi 쉬면 내리기, 복합명사 낱말 일치, 기본 별칭 넓히기, 판정 캐시 대상 ID."""

import importlib.util
import json
import re
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from career_core.db import Claim, Experience
from career_core.db.models import Application, ApplicationDocument, JudgeCache
from career_core.domain import korean
from career_core.domain.documents import key_terms, matched_terms
from career_core.domain.korean import (
    BUILTIN_ALIASES,
    KiwiAnalyzer,
    RuleAnalyzer,
    found_spellings,
    idle_seconds,
    load_analyzer,
    use_analyzer,
)
from career_core.domain.workspace import Role, scopes_for_role
from career_core.evals import korean_eval
from career_core.llm import ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.ports import ActorContext
from career_core.usecases import applications as app_uc
from career_core.usecases import brain, judges
from career_core.usecases import postings as postings_uc
from career_core.usecases.search import alias_pattern, search
from career_core.usecases.workspaces import create_workspace
from sqlalchemy import delete, func, select

pytestmark = pytest.mark.anyio

HAS_KIWI = importlib.util.find_spec("kiwipiepy") is not None
needs_kiwi = pytest.mark.skipif(not HAS_KIWI, reason="선택 설치 korean (kiwipiepy) 없음")


# ---------------------------------------------------------------- 가짜 형태소 분석기


class FakeKiwi:
    """kiwipiepy 없이 시험한다: 아는 명사로 한글 묶음을 앞에서부터 나누고, 남는 글자는 조사(JKO)."""

    NOUNS = (
        "머신",
        "러닝",
        "모델",
        "서빙",
        "스터디",
        "논문",
        "결제",
        "시스템",
        "화면",
        "경험",
        "개발",
    )
    made = 0

    def __init__(self):
        FakeKiwi.made += 1
        self.calls = 0

    def tokenize(self, text):
        self.calls += 1
        out = []
        for m in re.finditer(r"[가-힣]+", text):
            i, run = 0, m.group()
            while i < len(run):
                noun = next((n for n in self.NOUNS if run.startswith(n, i)), None)
                form, tag = (noun, "NNG") if noun else (run[i], "JKO")
                out.append(SimpleNamespace(form=form, tag=tag, start=m.start() + i, len=len(form)))
                i += len(form)
        return out


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# ---------------------------------------------------------------- 1. kiwi 쉬면 내리기


def test_idle_seconds_env():
    assert idle_seconds({}) == 300
    assert idle_seconds({"CAREER_KOREAN_IDLE_SECONDS": "0"}) == 0
    assert idle_seconds({"CAREER_KOREAN_IDLE_SECONDS": "-3"}) == 0
    assert idle_seconds({"CAREER_KOREAN_IDLE_SECONDS": "12.5"}) == 12.5
    assert idle_seconds({"CAREER_KOREAN_IDLE_SECONDS": "x"}) == 300


def test_kiwi_unloads_after_idle_and_reloads():
    clock = Clock()
    a = KiwiAnalyzer(factory=FakeKiwi, idle=60, clock=clock)
    try:
        assert not a.loaded and a.loads == 0  # 처음 쓸 때 올린다
        assert a.content_words("결제시스템을") == ["결제", "시스템"]
        assert a.loaded and a.loads == 1
        clock.now += 30
        assert a.unload_if_idle() is False and a.loaded  # 아직 안 쉼
        a.content_words("머신러닝")  # 쓰면 다시 센다
        clock.now += 59
        assert a.unload_if_idle() is False and a.loaded
        clock.now += 2
        assert a.unload_if_idle() is True
        assert not a.loaded and a._cache == {}  # 결과 기억도 같이 내린다
        # 다시 쓰면 다시 올리고 결과는 같다
        assert a.content_words("결제시스템을") == ["결제", "시스템"]
        assert a.loads == 2
        assert a.tokens("결제를") == [("결제", "NNG"), ("를", "JKO")]
        assert a.content_spans("새 결제시스템") == [("결제", 2, 4), ("시스템", 4, 7)]
    finally:
        a.unload()
    assert not a.loaded and a._timer is None


def test_kiwi_idle_zero_keeps_model_and_one_timer_only():
    keep = KiwiAnalyzer(factory=FakeKiwi, idle=0, clock=Clock())
    keep.content_words("결제")
    assert keep._timer is None and keep.unload_if_idle() is False and keep.loaded

    a = KiwiAnalyzer(factory=FakeKiwi, idle=60, clock=Clock())
    try:
        before = threading.active_count()
        for i in range(50):
            a.content_words(f"결제 {i}")
        # 부를 때마다 타이머를 만들지 않는다
        assert threading.active_count() <= before + 1
    finally:
        a.unload()


def test_kiwi_idle_timer_really_unloads():
    a = KiwiAnalyzer(factory=FakeKiwi, idle=0.05)
    a.content_words("결제시스템")
    assert a.loaded
    deadline = time.monotonic() + 5
    while a.loaded and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not a.loaded
    a.content_words("결제시스템")
    assert a.loads == 2
    a.unload()


def test_load_analyzer_reads_idle_env(monkeypatch):
    monkeypatch.setattr(korean, "_analyzer_cache", {})
    monkeypatch.setenv("CAREER_KOREAN_IDLE_SECONDS", "7")
    made = KiwiAnalyzer(factory=FakeKiwi)
    assert made._idle == 7


# ---------------------------------------------------------------- 2. 복합명사 낱말 일치 (c19)


def test_compound_noun_counts_once_and_needs_all_parts():
    fake = KiwiAnalyzer(factory=FakeKiwi, idle=0)
    with use_analyzer(fake):
        terms = key_terms("머신러닝 모델 서빙 경험")
        assert terms.words == ("머신", "러닝", "모델", "서빙")
        assert terms.groups == (("머신", "러닝"), ("모델",), ("서빙",)) and terms.needed == 2
        # c19: 복합명사 하나(머신러닝)만 같으면 낱말 하나 — 기준 2개를 못 넘는다
        assert matched_terms("머신러닝 스터디에서 논문을 읽었다", terms) == []
        assert matched_terms("머신러닝 모델을 서빙했다", terms) == ["머신", "러닝", "모델", "서빙"]
        # 앞부분만 같으면 맞지 않는다 (결제시스템 ↔ 결제 화면). 띄어 써도 모든 부분이 있으면 맞는다
        one = key_terms("결제시스템 개발 경험")
        assert one.groups == (("결제", "시스템"),) and one.needed == 1
        assert matched_terms("결제 화면을 만들었다", one) == []
        assert matched_terms("결제 시스템을 만들었다", one) == ["결제", "시스템"]
    # 규칙 기반은 그대로 (어절 하나 = 낱말 하나, 묶음 없음)
    with use_analyzer(RuleAnalyzer()):
        rule = key_terms("머신러닝 모델 서빙 경험")
        assert rule.groups == () and rule.words == ("머신러닝", "모델", "서빙")
        assert matched_terms("머신러닝 스터디에서 논문을 읽었다", rule) == []


@needs_kiwi
def test_compound_noun_with_kiwi():
    with use_analyzer(load_analyzer("kiwi")):
        terms = key_terms("머신러닝 모델 서빙 경험")
        assert not matched_terms("머신러닝 스터디에서 논문을 매주 읽었다.", terms)
        assert not matched_terms("새 서버를 도입했다.", key_terms("캐시서버 도입"))
        assert matched_terms("캐시 서버를 도입해 응답을 줄였다.", key_terms("캐시서버 도입 경험"))


def test_korean_eval_sentences_numbers():
    fx = korean_eval.load_fixture()
    assert (len(fx["sentences"]), len(fx["sentence_pairs"])) == (50, 27)
    assert {c["type"] for c in fx["sentences"]} == {"posting", "resume"}
    rule = korean_eval.evaluate(RuleAnalyzer(), fx)
    assert rule["sentences"]["all"]["exact"] == 4 and rule["sentence_pairs"]["correct"] == 20
    if HAS_KIWI:
        kiwi = korean_eval.evaluate(load_analyzer("kiwi"), fx)
        assert kiwi["sentences"]["all"]["f1"] > 0.9 and kiwi["sentence_pairs"]["correct"] >= 20
        assert kiwi["pairs"]["correct"] >= 39


# ---------------------------------------------------------------- 3. 기본 별칭 넓히기


NEW_ALIASES = {
    "prometheus": "프로메테우스",
    "jira": "지라",
    "grafana": "그라파나",
    "confluence": "컨플루언스",
    "jenkins": "젠킨스",
    "gitlab": "깃랩",
    "github": "깃허브",
    "slack": "슬랙",
    "notion": "노션",
    "figma": "피그마",
    "kubernetes": "쿠버네티스",
    "airflow": "에어플로우",
    "mongodb": "몽고디비",
}


def test_builtin_aliases_widened_without_ambiguous_spellings():
    for canon, hangul in NEW_ALIASES.items():
        assert BUILTIN_ALIASES.lookup(hangul) == canon
        assert BUILTIN_ALIASES.lookup(canon.upper()) == canon
    # 흔한 낱말의 앞부분이 되는 표기는 넣지 않았다
    for word in ("깃", "뷰", "스칼라", "바벨", "사이프러스", "주피터"):
        assert BUILTIN_ALIASES.lookup(word) is None
    # 한 표기는 한 기술만 가리킨다
    owner: dict[str, str] = {}
    for canon, spellings in BUILTIN_ALIASES.table.items():
        for sp in (canon, *spellings):
            assert owner.setdefault(sp.lower(), canon) == canon, sp


def test_new_aliases_keep_whole_word_rules():
    def hit(spelling, text):
        return re.search(alias_pattern(spelling, BUILTIN_ALIASES), text, re.IGNORECASE)

    assert hit("지라", "지라 보드로 관리했다") and not hit("지라", "하지라도 끝냈다")
    assert hit("jira", "Jira 티켓") and not hit("jira", "jirafa")
    assert hit("슬랙", "슬랙으로 알렸다") and not hit("노션", "가노션")
    assert hit("mongo", "Mongo 클러스터") and not hit("mongo", "mongoose 모델")
    text = "프로메테우스와 그라파나로 지표 대시보드를 만들었다."
    assert found_spellings(text, "Prometheus") == ["프로메테우스"]
    assert found_spellings(text, "grafana") == ["그라파나"]
    # 문서 검사 단어 일치도 같은 표를 쓴다 (판정 기준선 c32·c59)
    assert matched_terms(
        "지라 보드로 스프린트를 관리하고 컨플루언스에 회의록을 남겼다.",
        key_terms("협업 툴(Jira, Confluence) 사용 경험"),
    )


async def test_search_expands_new_builtin_aliases(db):
    ws, sub = await create_workspace(db, name="A", owner_display_name="alice")
    owner = ActorContext(sub, ws, scopes_for_role(Role.OWNER))
    exp = await brain.create_experience(db, owner, title="합성 모니터링")
    await brain.add_claim(db, owner, exp, statement="프로메테우스로 지표를 모았다", kind="action")
    await brain.add_claim(db, owner, exp, statement="Jira 보드로 일정을 관리했다", kind="action")
    res = await search(db, owner, "Prometheus")
    assert [h.snippet for h in res.hits] == ["프로메테우스로 지표를 모았다"]
    assert res.expanded_terms[0].canonical == "prometheus"
    res = await search(db, owner, "지라")
    assert res.hits[0].snippet == "Jira 보드로 일정을 관리했다"
    # 넓히기는 키워드 쪽만: 끄면 Jira 주장을 찾지 못한다 (trigram 점수가 낮은 다른 결과는 있을 수 있다)
    off = await search(db, owner, "지라", expand_aliases=False)
    assert "Jira 보드로 일정을 관리했다" not in [h.snippet for h in off.hits]


# ---------------------------------------------------------------- 4. 판정 캐시 대상 ID (c0026)

JUDGE_CONFIG = parse_models_config("""
[endpoints.proxy]
preset = "cliproxyapi"
price_as = "openai"
data_classes = ["public", "private"]

[routes]
judge_claim = "proxy/gpt-5-mini"
judge_visibility = "proxy/gpt-5-mini"
""")


class FakeJudge:
    """돈이 드는 호출 없이: 후보·요건마다 "no"."""

    def __init__(self):
        self.requests = []

    async def complete(self, model, request):
        self.requests.append(request)
        body = request.messages[-1].content
        payload = json.loads(body[body.index("{") : body.rindex("}") + 1])
        items = payload.get("candidates") or payload.get("requirements") or []
        rows = [{"id": c["id"], "verdict": "no", "reason": "합성"} for c in items]
        return ModelCompletion(
            text=json.dumps({"results": rows}, ensure_ascii=False),
            model=model,
            stop_reason="end",
            parts=(UsagePart(model, TokenUsage(input_tokens=100, output_tokens=20)),),
        )


def gateway(db, client):
    return ModelGateway(
        db,
        JUDGE_CONFIG,
        PriceBook.load(),
        client_factory=lambda ep, key: client,
        env={"CLIPROXY_API_KEY": "k"},
    )


async def cache_rows(db, actor):
    async with db.workspace_session(actor.workspace_id) as s:
        return (await s.execute(select(JudgeCache).order_by(JudgeCache.created_at))).scalars().all()


async def test_deleting_claims_removes_their_judge_cache_rows(db, owner_engine):
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    owner = ActorContext(a, ws_a, scopes_for_role(Role.OWNER))
    other = ActorContext(b, ws_b, scopes_for_role(Role.OWNER))
    fake = FakeJudge()
    gw = gateway(db, fake)

    async def ask(actor, requirement, claims):
        call = judges.gateway_caller(gw, actor, db=db)
        cands = [(str(c), f"합성 주장 {i}") for i, c in enumerate(claims)]
        return await judges.judge_claims(
            call, requirement, cands, data_classes=frozenset({"private"}), claim_ids=claims
        )

    exp = await brain.create_experience(db, owner, title="합성 경험")
    exp2 = await brain.create_experience(db, owner, title="합성 경험 둘")
    c1 = await brain.add_claim(db, owner, exp, statement="Docker로 배포했다", kind="action")
    c2 = await brain.add_claim(db, owner, exp, statement="회의록을 썼다", kind="action")
    c3 = await brain.add_claim(db, owner, exp2, statement="Kafka를 운영했다", kind="action")
    await ask(owner, "Docker 사용 경험", [c1, c2])
    await ask(owner, "Kafka 운영 경험", [c3])
    o_exp = await brain.create_experience(db, other, title="다른 공간")
    o1 = await brain.add_claim(db, other, o_exp, statement="Docker로 배포했다", kind="action")
    await ask(other, "Docker 사용 경험", [o1])
    rows = await cache_rows(db, owner)
    assert [(set(r.claim_ids), r.document_ids) for r in rows] == [({c1, c2}, []), ({c3}, [])]

    # 같은 글(같은 입력 해시)을 다른 주장으로 꺼내면 그 주장도 대상에 더한다
    _, run = await ask(owner, "Kafka 운영 경험", [c1])
    assert run.cached == 1 and len(fake.requests) == 3
    assert [set(r.claim_ids) for r in await cache_rows(db, owner)] == [{c1, c2}, {c3, c1}]

    # 주장 하나를 지우면 그 주장이 든 응답만 지운다
    async with db.workspace_session(owner.workspace_id) as s:
        await s.execute(delete(Claim).where(Claim.id == c2))
    assert [set(r.claim_ids) for r in await cache_rows(db, owner)] == [{c3, c1}]
    # 경험을 지우면 CASCADE로 지워진 주장의 응답도 지운다
    async with db.workspace_session(owner.workspace_id) as s:
        await s.execute(delete(Experience).where(Experience.id == exp2))
    assert await cache_rows(db, owner) == []
    # 다른 공간의 응답은 그대로
    assert len(await cache_rows(db, other)) == 1

    # trigger는 두 표에, 문장 단위로, search_path 고정
    with owner_engine.connect() as conn:
        trig = (
            conn.exec_driver_sql(
                "SELECT tgrelid::regclass::text FROM pg_trigger WHERE tgname = 'judge_cache_forget' "
                "ORDER BY 1"
            )
            .scalars()
            .all()
        )
        conf = conn.exec_driver_sql(
            "SELECT proconfig, prosecdef FROM pg_proc WHERE proname = 'judge_cache_forget'"
        ).one()
    assert trig == ["core.application_documents", "core.claims"]
    assert conf == (["search_path=pg_catalog"], False)


async def test_deleting_application_documents_removes_visibility_cache(db):
    ws, sub = await create_workspace(db, name="A", owner_display_name="alice")
    owner = ActorContext(sub, ws, scopes_for_role(Role.OWNER))
    fake = FakeJudge()
    gw = gateway(db, fake)
    [r] = await postings_uc.save_urls(db, owner, ["https://jobs.example.test/j/c0026"])
    await postings_uc.confirm_posting(
        db,
        owner,
        r.posting_id,
        overrides={
            "title": "백엔드 (합성)",
            "company": "가상회사",
            "requirements": ["Docker 사용 경험"],
            "questions": [
                {"prompt": "지원 동기 (500자)", "char_limit": 500},
                {"prompt": "경력 소개 (500자)", "char_limit": 500},
            ],
        },
    )
    aid = await app_uc.create_application(db, owner, r.posting_id)
    docs = (await app_uc.get_application(db, owner, aid)).documents
    assert len(docs) == 2
    for d in docs:
        await app_uc.save_revision(
            db,
            owner,
            aid,
            d.id,
            base_revision=0,
            operations=[{"op": "insert", "text": f"컨테이너 이미지를 줄였습니다 ({d.id})."}],
        )
    # 문서 하나씩, 그리고 전부 합쳐서
    for d in docs:
        await judges.judge_application_visibility(db, gw, owner, aid, document_id=d.id)
    await judges.judge_application_visibility(db, gw, owner, aid)
    rows = await cache_rows(db, owner)
    assert [set(x.document_ids) for x in rows] == [{docs[0].id}, {docs[1].id}, {d.id for d in docs}]
    assert all(x.claim_ids == [] for x in rows)

    async with db.workspace_session(owner.workspace_id) as s:
        await s.execute(delete(ApplicationDocument).where(ApplicationDocument.id == docs[0].id))
    assert [set(x.document_ids) for x in await cache_rows(db, owner)] == [{docs[1].id}]
    # 지원을 지우면 CASCADE로 남은 문서의 응답도
    async with db.workspace_session(owner.workspace_id) as s:
        await s.execute(delete(Application).where(Application.id == aid))
    assert await cache_rows(db, owner) == []


async def test_cache_rows_without_targets_survive_deletes(db):
    ws, sub = await create_workspace(db, name="A", owner_display_name="alice")
    owner = ActorContext(sub, ws, scopes_for_role(Role.OWNER))
    gw = gateway(db, FakeJudge())
    call = judges.gateway_caller(gw, owner, db=db)
    # 대상 없이 부른 응답 (평가 스크립트처럼 주장 ID가 아닌 키)
    await judges.judge_claims(
        call, "Docker 사용 경험", [("k1", "합성")], data_classes=frozenset({"private"})
    )
    exp = await brain.create_experience(db, owner, title="합성")
    c = await brain.add_claim(db, owner, exp, statement="합성 주장", kind="action")
    async with db.workspace_session(owner.workspace_id) as s:
        await s.execute(delete(Claim).where(Claim.id == c))
        assert await s.scalar(select(func.count()).select_from(JudgeCache)) == 1
    # 문맥 없는 세션은 캐시를 보지 못한다 (FORCE RLS 그대로)
    async with db.workspace_session(uuid4()) as s:
        assert await s.scalar(select(func.count()).select_from(JudgeCache)) == 0

"""c0022 백로그: 검색어 별칭 넓히기, 한국어 형태소 분석(선택 설치), 판정 응답 캐시."""

import importlib.util
import json
import re
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.db import ModelCall
from career_core.db.base import utcnow
from career_core.db.models import JudgeCache
from career_core.domain import korean
from career_core.domain.documents import key_terms, korean_terms, lead_terms, matched_terms
from career_core.domain.korean import (
    BUILTIN_ALIASES,
    RuleAnalyzer,
    load_analyzer,
    merge_aliases,
    parse_alias_entries,
    use_analyzer,
)
from career_core.domain.study import requirement_query, requirement_terms
from career_core.domain.workspace import Role, scopes_for_role
from career_core.evals import korean_eval
from career_core.evals.search_eval import evaluate, load_fixture, seed
from career_core.interfaces.http import CoreDeps, build_router
from career_core.llm import ChatMessage, ModelCompletion, ModelRequest, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.ports import ActorContext
from career_core.usecases import brain, judges
from career_core.usecases import workspace_settings as ws_uc
from career_core.usecases.search import alias_pattern, expand_terms, query_terms, search
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio

HAS_KIWI = importlib.util.find_spec("kiwipiepy") is not None
needs_kiwi = pytest.mark.skipif(not HAS_KIWI, reason="선택 설치 korean (kiwipiepy) 없음")


@pytest.fixture
async def env(db):
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    tokens = {
        "owner": ActorContext(a, ws_a, scopes_for_role(Role.OWNER)),
        "editor": ActorContext(a, ws_a, scopes_for_role(Role.EDITOR)),
        "other": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
    }
    return {**tokens, "tokens": tokens}


class FakeResolver:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def client_for(db, env, models=None):
    deps = CoreDeps(identity=FakeResolver(env["tokens"]), db=db, models=models)
    app = FastAPI()
    app.include_router(build_router(deps))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t")


# ---------------------------------------------------------------- 1. 검색어 별칭 넓히기


def test_expand_terms_builtin_and_workspace_aliases():
    expanded, pats = expand_terms(
        "포스트그레스로 이전", ["포스트그레스로", "이전"], BUILTIN_ALIASES
    )
    [e] = expanded
    assert (e.term, e.canonical) == ("포스트그레스", "postgresql")  # 조사를 떼고 찾는다
    assert "postgresql" in e.spellings and "postgres" in e.spellings
    assert {g for g, _ in pats} == {0}  # 넓힌 표기는 원래 낱말의 묶음으로 센다

    table = merge_aliases(
        parse_alias_entries([{"term": "FastAPI", "aliases": ["패스트API", "fast api"]}])
    )
    [e] = expand_terms("패스트API 서버", ["패스트API", "서버"], table)[0]
    assert (e.canonical, e.spellings) == ("FastAPI", ["FastAPI", "fast api"])
    # 여러 낱말 표기는 질문 전체에서 찾는다
    [e] = expand_terms("fast api 서버", ["fast", "api", "서버"], table)[0]
    assert e.term == "fast api" and "FastAPI" in e.spellings
    # 별칭이 없는 질문은 넓히지 않는다
    assert expand_terms("배포 시간 단축", ["배포", "시간", "단축"], table) == ([], [])


def test_alias_patterns_respect_word_boundaries():
    def hit(spelling, text):
        return re.search(alias_pattern(spelling, BUILTIN_ALIASES), text, re.IGNORECASE)

    assert hit("ts", "TS로 옮겼다") and not hit("ts", "tests를 늘렸다")
    assert hit("k8s", "K8s 클러스터") and not hit("go", "google 검색")
    assert hit("자바", "자바로 만든 서버") and not hit("자바", "자바스크립트 화면")
    assert not hit("자바", "코자바 프로젝트")  # 앞에 한글이 붙으면 다른 낱말
    assert hit("node.js", "Node.js 서버") and not hit("node.js", "nodexjs")  # 점은 글자 그대로


async def test_search_expands_with_workspace_aliases(db, env):
    owner, other = env["owner"], env["other"]
    for actor in (owner, other):
        exp = await brain.create_experience(db, actor, title="합성 주문 서비스")
        await brain.add_claim(
            db, actor, exp, statement="FastAPI로 주문 API를 다시 작성했다", kind="action"
        )
        await brain.add_claim(
            db, actor, exp, statement="쿠버네티스 클러스터를 운영했다", kind="action"
        )
        await brain.add_claim(db, actor, exp, statement="배포 시간을 줄였다", kind="action")

    # 기본 표: kubernetes → 쿠버네티스
    res = await search(db, owner, "kubernetes")
    assert [h.snippet for h in res.hits] == ["쿠버네티스 클러스터를 운영했다"]
    assert res.expanded_terms[0].canonical == "kubernetes"
    assert "쿠버네티스" in res.expanded_terms[0].spellings
    off = await search(db, owner, "kubernetes", expand_aliases=False)
    assert off.hits == [] and off.expanded_terms == []

    # 공간 별칭: 패스트API → FastAPI (다른 공간에는 없다)
    assert (await search(db, owner, "패스트API")).expanded_terms == []
    await ws_uc.update_settings(
        db, owner, tech_aliases=[{"term": "FastAPI", "aliases": ["패스트API"]}]
    )
    res = await search(db, owner, "패스트API로 만든 것")
    assert res.hits[0].snippet == "FastAPI로 주문 API를 다시 작성했다"
    assert [(e.term, e.canonical, e.spellings) for e in res.expanded_terms] == [
        ("패스트API", "FastAPI", ["FastAPI"])
    ]
    assert (await search(db, other, "패스트API")).expanded_terms == []

    # 별칭이 없는 질문의 점수·순서는 넓히기와 상관없다 (RRF 합치기 그대로)
    a = await search(db, owner, "배포 시간")
    b = await search(db, owner, "배포 시간", expand_aliases=False)
    assert [(h.id, h.score) for h in a.hits] == [(h.id, h.score) for h in b.hits]

    async with client_for(db, env) as c:
        r = await c.get("/core/search", params={"q": "패스트API"}, headers=auth("owner"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["expanded_terms"] == [
            {"term": "패스트API", "canonical": "FastAPI", "spellings": ["FastAPI"]}
        ]
        assert body["hits"][0]["snippet"].startswith("FastAPI")


async def test_search_eval_alias_queries_do_not_regress(db, env, tmp_path):
    owner = env["owner"]
    fx = load_fixture()
    assert len(fx["queries"]) == 30 and len(fx["alias_queries"]) == 6
    await seed(db, LocalBlobStore(tmp_path / "blobs"), owner, fx)
    scores = (await evaluate(db, owner, fx, None))["keyword"]
    assert scores["keyword"].hit5 == 24 and scores["all"].hit5 == 28  # 이전과 같다
    assert scores["alias"].hit5 == scores["alias"].hit1 == 6
    assert scores["alias_off"].hit5 == 0


# ---------------------------------------------------------------- 2. 한국어 내용어 (규칙 / kiwi)


def test_rule_analyzer_is_the_default_and_unchanged(monkeypatch):
    monkeypatch.delenv("CAREER_KOREAN_ANALYZER", raising=False)
    assert korean.korean_analyzer().name == "rule"
    assert korean_terms("결제시스템을 개발했습니다") == ["결제시스템", "개발했습니다"]
    assert korean_terms("트래픽을 처리하는") == ["트래픽", "처리"]
    assert query_terms("결제시스템을 배포") == ["결제시스템을", "배포"]
    assert requirement_query("결제시스템 구축 경험") == "결제시스템 구축"
    # 여러 낱말 요건 낱말은 띄어쓰기를 보지 않는다 (규칙 기반도)
    keys = [t.key for t in requirement_terms("테스트코드 작성 경험, 대용량트래픽")]
    assert "테스트 코드" in keys and "대용량 트래픽" in keys
    # 한 낱말은 공백을 지우지 않는다 ("정보 안내"의 "보안")
    assert "보안" not in [t.key for t in requirement_terms("정보 안내 업무")]


def test_missing_kiwi_falls_back_to_rules(monkeypatch):
    class Broken:
        def __init__(self):
            raise ImportError("kiwipiepy")

    monkeypatch.setattr(korean, "KiwiAnalyzer", Broken)
    monkeypatch.setattr(korean, "_analyzer_cache", {})
    monkeypatch.setenv("CAREER_KOREAN_ANALYZER", "kiwi")
    assert korean.korean_analyzer().name == "rule"
    assert load_analyzer("rule").name == load_analyzer(None).name == "rule"


@needs_kiwi
def test_kiwi_content_words_and_checks():
    kiwi = load_analyzer("kiwi")
    assert kiwi.name == "kiwi" and kiwi.morphological
    with use_analyzer(kiwi):
        assert korean_terms("결제시스템을 배포했습니다") == ["결제", "시스템", "배포"]
        assert korean_terms("배치를 병렬화하여") == ["배치", "병렬화"]  # 파생 접미사는 붙인다
        assert "공정" not in korean_terms("공정한 평가")  # 형용사(-하다)는 내용어가 아님
        # 요건 가시성: 띄어쓰기가 달라도, 다른 낱말 속 글자는 아니다
        assert matched_terms("검색 엔진의 색인을 최적화했다", key_terms("검색엔진 최적화 경험"))
        assert not matched_terms("인사말을 시스템에 넣었다", key_terms("인사 시스템 경험"))
        assert matched_terms("Python으로 만들었다", key_terms("Python 경험"))  # 기술명은 그대로
        # 사실 변경(역할): "리더십"은 역할이 아니고 "이끌었다"는 역할이다
        assert not lead_terms("리더십 교육을 수료했다") - lead_terms("교육을 수료했다")
        assert "이끌" in lead_terms("프로젝트를 이끌었다")
        assert "주도" not in lead_terms("주도적으로 해결했다")
        # 검색어·요건 검색어도 내용어로
        assert query_terms("결제시스템을 배포") == ["결제", "시스템", "배포"]
        assert requirement_query("결제시스템 구축 경험") == "결제 시스템 구축"
        assert "데이터 분석" in [t.key for t in requirement_terms("데이터분석 경험")]
    with use_analyzer(RuleAnalyzer()):
        assert korean_terms("결제시스템을") == ["결제시스템"]


def test_korean_eval_numbers():
    fx = korean_eval.load_fixture()
    assert (len(fx["terms"]), len(fx["spacing"]), len(fx["pairs"]), len(fx["roles"])) == (
        85,
        15,
        40,
        14,
    )
    rule = korean_eval.evaluate(RuleAnalyzer(), fx)
    assert rule["pairs"]["correct"] == 25 and rule["spacing"]["same"] == 0
    assert rule["roles"]["correct"] == 10 and rule["terms"]["all"]["exact"] == 31
    if HAS_KIWI:
        kiwi = korean_eval.evaluate(load_analyzer("kiwi"), fx)
        assert kiwi["pairs"]["correct"] >= 37 and kiwi["spacing"]["same"] == 15
        assert kiwi["terms"]["all"]["f1"] > 0.95 and kiwi["roles"]["correct"] >= 12


# ---------------------------------------------------------------- 4. 판정 응답 캐시 (c0022)

JUDGE_CONFIG = parse_models_config("""
[endpoints.proxy]
preset = "cliproxyapi"
price_as = "openai"
data_classes = ["public", "private"]

[routes]
judge_claim = "proxy/gpt-5-mini"
judge_visibility = "proxy/gpt-5-mini"
""")


class CountingJudge:
    def __init__(self, text=None):
        self.requests = []
        self.text = text

    async def complete(self, model, request):
        self.requests.append(request)
        body = request.messages[-1].content
        payload = json.loads(body[body.index("{") : body.rindex("}") + 1])
        rows = [{"id": c["id"], "verdict": "no", "reason": "합성"} for c in payload["candidates"]]
        return ModelCompletion(
            text=self.text or json.dumps({"results": rows}, ensure_ascii=False),
            model=model,
            stop_reason="end",
            parts=(UsagePart(model, TokenUsage(input_tokens=300, output_tokens=60)),),
        )


def judge_gateway(db, client, config=JUDGE_CONFIG):
    return ModelGateway(
        db,
        config,
        PriceBook.load(),
        client_factory=lambda ep, key: client,
        env={"CLIPROXY_API_KEY": "k"},
    )


def test_cache_keys():
    req = ModelRequest(
        messages=(ChatMessage("user", "<data>{}</data>"),),
        system="s",
        data_classes=frozenset({"private"}),
    )
    same = ModelRequest(
        messages=(ChatMessage("user", "<data>{}</data>"),),
        system="s",
        data_classes=frozenset({"private"}),
    )
    other = ModelRequest(
        messages=(ChatMessage("user", "<data>{ }</data>"),),
        system="s",
        data_classes=frozenset({"private"}),
    )
    assert judges.input_hash(req) == judges.input_hash(same) != judges.input_hash(other)
    assert len(judges.input_hash(req)) == 64
    assert judges.prompt_version("a") != judges.prompt_version("b")
    assert judges.prompt_version("a").startswith(f"v{judges.PROMPT_VERSION}-")
    assert judges.cache_days({}) == 30 and judges.cache_days({"CAREER_JUDGE_CACHE_DAYS": "0"}) == 0
    assert judges.cache_days({"CAREER_JUDGE_CACHE_DAYS": "x"}) == 30


async def test_judge_cache_reuses_answers_per_workspace(db, env, owner_engine):
    owner, other = env["owner"], env["other"]
    fake = CountingJudge()
    gw = judge_gateway(db, fake)
    cands = [("a", "Docker로 배포했다"), ("b", "회의록을 썼다")]

    async def ask(actor, requirement="Docker 사용 경험", gateway=gw, **kw):
        call = judges.cached_caller(
            db, actor, judges.gateway_caller(gateway, actor), judges.route_model(gateway), **kw
        )
        return await judges.judge_claims(
            call, requirement, cands, data_classes=frozenset({"private"})
        )

    v1, run1 = await ask(owner)
    v2, run2 = await ask(owner)
    assert v1 == v2 and len(fake.requests) == 1
    assert (run1.calls, run1.cached) == (1, 0) and (run2.calls, run2.cached) == (0, 1)
    assert run2.cost_usd == "0.000000" and run2.cost_basis == "cached"
    async with db.workspace_session(owner.workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(ModelCall)) == 1  # 장부는 한 번
        row = await s.scalar(select(JudgeCache))
        assert row.hits == 1 and row.model == "proxy/gpt-5-mini" and row.purpose == "judge_claim"

    # 다른 공간은 다른 캐시 (RLS)
    await ask(other)
    assert len(fake.requests) == 2
    # 요건이 바뀌면 다른 입력 해시
    await ask(owner, "Kubernetes 운영 경험")
    assert len(fake.requests) == 3
    # 라우트 모델이 바뀌면 다른 키
    other_model = parse_models_config(
        '[endpoints.proxy]\npreset = "cliproxyapi"\ndata_classes = ["private"]\n'
        '[routes]\njudge_claim = "proxy/another"\n'
    )
    await ask(owner, gateway=judge_gateway(db, fake, other_model))
    assert len(fake.requests) == 4
    # 만료된 응답은 쓰지 않고, 새로 쓸 때 지운다
    await ask(owner, clock=lambda: utcnow() + timedelta(days=31))
    assert len(fake.requests) == 5
    # 보관 0일 = 캐시 안 씀
    await ask(owner, days=0)
    await ask(owner, days=0)
    assert len(fake.requests) == 7

    # 해석할 수 없는 응답은 담지 않는다
    bad = CountingJudge(text="모르겠습니다")
    gw_bad = judge_gateway(db, bad)
    await ask(owner, "Rust 경험", gateway=gw_bad)
    await ask(owner, "Rust 경험", gateway=gw_bad)
    assert len(bad.requests) == 2

    # 강제 RLS: 공간 문맥이 없으면 0행
    async with db.workspace_session(uuid4()) as s:
        assert await s.scalar(select(func.count()).select_from(JudgeCache)) == 0
    with owner_engine.connect() as conn:
        forced = conn.exec_driver_sql(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'core.judge_cache'::regclass"
        ).one()
    assert tuple(forced) == (True, True)

    async with client_for(db, env, gw) as c:
        r = await c.get("/core/judges/cache", headers=auth("owner"))
        assert r.status_code == 200 and r.json()["entries"] >= 1 and r.json()["days"] == 30
        assert (await c.get("/core/judges/cache", headers=auth("editor"))).status_code == 403
        r = await c.delete("/core/judges/cache", headers=auth("owner"))
        assert r.status_code == 200 and r.json()["deleted"] >= 1
        assert (await c.get("/core/judges/cache", headers=auth("owner"))).json()["entries"] == 0
    async with db.workspace_session(other.workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(JudgeCache)) == 1  # 다른 공간 그대로

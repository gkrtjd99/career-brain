"""검색 결과 다시 정렬 (routes.rerank, 선택). 네트워크·모델 파일 없이 가짜 클라이언트로 본다."""

import json

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import ModelCall
from career_core.domain.workspace import Role, scopes_for_role
from career_core.evals.search_eval import load_fixture, seed
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import parsing_handlers, run_until_idle
from career_core.llm import (
    ModelCallError,
    ModelCompletion,
    ModelDenied,
    RerankCompletion,
    TokenUsage,
    UsagePart,
)
from career_core.llm.config import ConfigError, parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.llm.rerank import parse_ranking
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.search import search
from career_core.usecases.sources import upload_source
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import select

pytestmark = pytest.mark.anyio

LOCAL_RR = """
[endpoints.local]
preset = "local"

[routes]
rerank = "local/fake-cross-encoder"

[rerank]
top_n = 3
"""
CHAT_RR = """
[endpoints.chat]
preset = "ollama"
data_classes = {classes}

[routes]
default = "chat/llama"
rerank = "chat/llama"

[rerank]
top_n = 20
"""
# 여러 결과가 걸리는 두 낱말 질문 (합성 30문항 자료)
QUERY = "배포 시간"
SECRET = "비밀표시 배포 시간 기록"


class FakeReranker:
    """보낸 순서를 거꾸로 (뒤 글일수록 점수가 크다). 실패를 흉내 낼 수 있다."""

    def __init__(self, fail: Exception | None = None, short: bool = False):
        self.fail = fail
        self.short = short
        self.calls: list[tuple[str, list[str]]] = []

    async def rerank(self, model, query, documents):
        self.calls.append((query, list(documents)))
        if self.fail is not None:
            raise self.fail
        scores = tuple(float(i) for i in range(len(documents)))
        return RerankCompletion(
            scores=scores[:-1] if self.short else scores,
            model=model,
            parts=(UsagePart(model, TokenUsage(input_tokens=1)),),
        )


class FakeChat:
    """대화 모델: 받은 목록을 거꾸로 정렬한 JSON을 돌려준다."""

    def __init__(self):
        self.requests = []

    async def complete(self, model, request):
        self.requests.append(request)
        n = request.messages[0].content.count("\n[")
        return ModelCompletion(
            text=json.dumps({"ranking": list(range(n, 0, -1))}),
            model=model,
            stop_reason="end",
            parts=(UsagePart(model, TokenUsage(input_tokens=10, output_tokens=5)),),
        )


def gw(db, text, client):
    return ModelGateway(
        db,
        parse_models_config(text),
        PriceBook.load(),
        client_factory=lambda ep, key: client,
        env={},
    )


@pytest.fixture
async def owner(db, tmp_path):
    ws, subject = await create_workspace(db, name="A", owner_display_name="alice")
    actor = ActorContext(subject, ws, scopes_for_role(Role.OWNER))
    blobs = LocalBlobStore(tmp_path / "blobs")
    await seed(db, blobs, actor, load_fixture())
    return actor, blobs


def order(result):
    return [(h.kind, h.id) for h in result.hits]


# --- 꺼져 있으면 그대로 ---------------------------------------------------------


async def test_rerank_off_keeps_rrf_order(db, owner):
    actor, _ = owner
    fake = FakeReranker()
    no_route = gw(db, '[endpoints.local]\npreset = "local"\n', fake)
    with_route = gw(db, LOCAL_RR, fake)
    for q in load_fixture()["queries"]:
        base = await search(db, actor, q["q"], limit=10)
        for gateway, rerank in ((None, True), (no_route, True), (with_route, False)):
            res = await search(db, actor, q["q"], gateway=gateway, limit=10, rerank=rerank)
            assert order(res) == order(base)
            assert [h.score for h in res.hits] == [h.score for h in base.hits]
            assert not res.reranked and res.rerank_note is None
            assert all(h.rerank_rank is None for h in res.hits)
    assert fake.calls == []
    async with db.workspace_session(actor.workspace_id) as s:
        assert (await s.execute(select(ModelCall))).scalars().all() == []


def test_rerank_config_rules():
    # 기본 라우트로 넘어가지 않는다 (정하지 않으면 다시 정렬하지 않음)
    cfg = parse_models_config('[endpoints.p]\npreset="ollama"\n[routes]\ndefault="p/m"\n')
    assert cfg.rerank_route() is None and cfg.rerank.top_n == 20
    # 로컬 엔드포인트는 임베딩·다시 정렬만
    cfg = parse_models_config(LOCAL_RR)
    assert cfg.rerank_route().model == "fake-cross-encoder" and cfg.rerank.top_n == 3
    with pytest.raises(ConfigError, match="embed and rerank"):
        parse_models_config('[endpoints.l]\npreset="local"\n[routes]\ndefault="l/m"\n')
    for bad in ("1", "51", '"20"'):
        with pytest.raises(ConfigError, match="top_n"):
            parse_models_config(f"[rerank]\ntop_n = {bad}\n")


def test_parse_ranking():
    assert parse_ranking('{"ranking": [3, 1]}', 3) == (4.0, 0.0, 5.0)
    assert parse_ranking('답: ```json\n{"ranking": ["2", 2, 9, true]}\n```', 2) == (0.0, 6.0)
    for bad in ("", "no json", '{"ranking": "1"}', '{"ranking": [7]}', '{"order": [1]}'):
        with pytest.raises(ModelCallError):
            parse_ranking(bad, 2)


# --- 로컬 cross-encoder ----------------------------------------------------------


async def test_local_rerank_reorders_only_top_n(db, owner):
    actor, _ = owner
    fake = FakeReranker()
    gateway = gw(db, LOCAL_RR, fake)
    base = await search(db, actor, QUERY, limit=10)
    assert len(base.hits) >= 5
    res = await search(db, actor, QUERY, gateway=gateway, limit=10, rerank=True)
    assert res.reranked and res.rerank_note is None
    # 앞 3개만 거꾸로, 나머지는 RRF 순서 그대로. 점수는 RRF 점수 그대로
    assert order(res)[:3] == order(base)[:3][::-1]
    assert order(res)[3:] == order(base)[3:]
    assert {h.id: h.score for h in res.hits} == {h.id: h.score for h in base.hits}
    assert [h.rerank_rank for h in res.hits[:3]] == [1, 2, 3]
    assert all(h.rerank_rank is None for h in res.hits[3:])
    # 보낸 글은 검색 결과의 발췌 그대로
    assert fake.calls == [(QUERY, [h.snippet for h in base.hits[:3]])]
    # limit이 top_n보다 작아도 앞 top_n개를 다시 정렬한 뒤 자른다
    small = await search(db, actor, QUERY, gateway=gateway, limit=2, rerank=True)
    assert order(small) == order(res)[:2]
    async with db.workspace_session(actor.workspace_id) as s:
        calls = (await s.execute(select(ModelCall))).scalars().all()
    assert {(c.purpose, c.status, c.billing) for c in calls} == {("rerank", "ok", "free")}


async def test_single_word_query_is_not_reranked(db, owner):
    actor, _ = owner
    fake = FakeReranker()
    base = await search(db, actor, "배포", limit=10)
    res = await search(db, actor, "배포", gateway=gw(db, LOCAL_RR, fake), limit=10, rerank=True)
    assert order(res) == order(base) and not res.reranked and res.rerank_note == "single_word"
    assert fake.calls == []


@pytest.mark.parametrize(
    ("fake", "note"),
    [
        (FakeReranker(fail=ModelCallError("model_not_found")), "error:model_not_found"),
        (FakeReranker(fail=RuntimeError("onnx")), "error:RuntimeError"),
        (FakeReranker(short=True), "error:rerank_count_mismatch"),
    ],
)
async def test_rerank_failure_falls_back_to_rrf_order(db, owner, fake, note):
    actor, _ = owner
    base = await search(db, actor, QUERY, limit=10)
    res = await search(db, actor, QUERY, gateway=gw(db, LOCAL_RR, fake), limit=10, rerank=True)
    assert order(res) == order(base)
    assert not res.reranked and res.rerank_note == note
    assert all(h.rerank_rank is None for h in res.hits)
    async with db.workspace_session(actor.workspace_id) as s:
        calls = (await s.execute(select(ModelCall))).scalars().all()
    assert [(c.purpose, c.status) for c in calls] == [("rerank", "error")]


# --- 대화 모델 라우트: 자료 등급·egress -------------------------------------------


async def _add_restricted(db, blobs, actor):
    await upload_source(
        db,
        blobs,
        actor,
        filename="secret.txt",
        data=SECRET.encode(),
        declared_mime="text/plain",
        max_bytes=1 << 20,
        data_class="restricted",
        title="합성 비공개 메모",
    )
    deps = ParsingDeps(
        db=db, blobs=blobs, egress=ProviderEgressPolicy(), local_parsers=default_local_parsers()
    )
    await run_until_idle(db, parsing_handlers(deps))


async def test_model_route_never_sends_restricted(db, owner):
    actor, blobs = owner
    await _add_restricted(db, blobs, actor)
    chat = FakeChat()
    gateway = gw(db, CHAT_RR.format(classes='["public", "private"]'), chat)
    base = await search(db, actor, QUERY, limit=10)
    secret_at = next(i for i, h in enumerate(base.hits) if "비밀표시" in h.snippet)
    res = await search(db, actor, QUERY, gateway=gateway, limit=10, rerank=True)
    assert res.reranked
    # 비공개(restricted) 결과는 보내지 않고 제자리에 둔다. 나머지는 거꾸로
    [request] = chat.requests
    assert "비밀표시" not in request.messages[0].content
    assert request.data_classes == frozenset({"private"})
    assert res.hits[secret_at].id == base.hits[secret_at].id
    others = [k for i, k in enumerate(order(base)) if i != secret_at]
    assert [k for i, k in enumerate(order(res)) if i != secret_at] == others[::-1]
    async with db.workspace_session(actor.workspace_id) as s:
        calls = (await s.execute(select(ModelCall))).scalars().all()
    assert [(c.purpose, c.status) for c in calls] == [("rerank", "ok")]


async def test_model_route_respects_egress_scope_and_endpoint_classes(db, owner):
    actor, _ = owner
    base = await search(db, actor, QUERY, limit=10)
    # egress 권한이 없으면 부르지 않는다
    chat = FakeChat()
    no_egress = ActorContext(actor.subject_id, actor.workspace_id, actor.scopes - {"egress"})
    gateway = gw(db, CHAT_RR.format(classes='["public", "private"]'), chat)
    res = await search(db, no_egress, QUERY, gateway=gateway, limit=10, rerank=True)
    assert order(res) == order(base) and res.rerank_note == "egress_scope_required"
    # 연결이 public만 허용하면 합성 자료(private)는 보낼 것이 없다
    public_only = gw(db, CHAT_RR.format(classes='["public"]'), chat)
    res = await search(db, actor, QUERY, gateway=public_only, limit=10, rerank=True)
    assert order(res) == order(base) and res.rerank_note == "nothing_to_send"
    assert chat.requests == []
    assert chat.requests == []


async def test_gateway_rerank_denies_restricted_even_if_listed(db, owner):
    """게이트웨이가 호출 전에 다시 확인한다: restricted는 연결 설정에 적어도 보내지 않는다."""
    actor, _ = owner
    chat = FakeChat()
    gateway = gw(db, CHAT_RR.format(classes='["public", "private", "restricted"]'), chat)
    with pytest.raises(ModelDenied, match="egress_denied"):
        await gateway.rerank(actor, "질문", ["a", "b"], data_classes=frozenset({"restricted"}))
    with pytest.raises(ModelDenied, match="data_class_missing"):
        await gateway.rerank(actor, "질문", ["a", "b"], data_classes=frozenset())
    assert chat.requests == []
    assert not gateway.rerank_allows(actor, "restricted") and gateway.rerank_allows(
        actor, "private"
    )
    async with db.workspace_session(actor.workspace_id) as s:
        calls = (await s.execute(select(ModelCall))).scalars().all()
    assert sorted(c.error_code for c in calls) == ["data_class_missing", "egress_denied"]
    assert {(c.purpose, c.status) for c in calls} == {("rerank", "denied")}


# --- HTTP: 검색 화면은 다시 정렬을 요청한다 ----------------------------------------


class _Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


async def test_search_http_reranks_when_route_set(db, owner):
    actor, blobs = owner
    fake = FakeReranker()
    app = FastAPI()
    app.include_router(
        build_router(
            CoreDeps(
                identity=_Tokens({"owner": actor}),
                db=db,
                blobs=blobs,
                max_upload_bytes=4096,
                models=gw(db, LOCAL_RR, fake),
            )
        )
    )
    base = await search(db, actor, QUERY, limit=10)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        h = {"Authorization": "Bearer owner"}
        body = (await c.get("/core/search", params={"q": QUERY}, headers=h)).json()
        assert body["reranked"] is True and body["rerank_note"] is None
        assert [x["id"] for x in body["hits"][:3]] == [str(x.id) for x in base.hits[:3]][::-1]
        assert [x["rerank_rank"] for x in body["hits"][:4]] == [1, 2, 3, None]
        body = (await c.get("/core/search", params={"q": "배포"}, headers=h)).json()
        assert body["reranked"] is False and body["rerank_note"] == "single_word"
    assert len(fake.calls) == 1

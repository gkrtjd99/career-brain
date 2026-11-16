"""O05 완료 기준: 합성 30문항에서 근거 확인, 공간 밖 결과 0건. 임베딩은 로컬·API 둘 다."""

import hashlib
import json
import math

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.embed_local import e5_prefix
from career_core.adapters.llm_openai import OpenAICompatibleClient
from career_core.db import Embedding, Job, ModelCall
from career_core.domain.workspace import Role, scopes_for_role
from career_core.evals.search_eval import evaluate, load_fixture, seed
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import index_handlers, run_until_idle
from career_core.llm import EmbeddingCompletion, TokenUsage, UsagePart
from career_core.llm.config import ConfigError, parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.ports import ActorContext
from career_core.usecases import brain
from career_core.usecases.indexing import IndexDeps
from career_core.usecases.search import query_terms, search, snippet
from career_core.usecases.sources import upload_source
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio

LOCAL = parse_models_config("""
[endpoints.local]
preset = "local"

[routes]
embed = "local/fake-multilingual"
""")
API = parse_models_config("""
[endpoints.openai]
preset = "openai"
data_classes = ["public", "private"]

[routes]
embed = "openai/text-embedding-3-small"

[embedding]
dimensions = 64
batch_size = 4
""")


class FakeEmbedder:
    """글자 2-gram 해시 벡터. 결정적이고, 겹치는 글자가 많을수록 가깝다."""

    def __init__(self, dims=64):
        self.dims = dims
        self.calls: list[tuple[str, list[str]]] = []

    def vector(self, text):
        v = [0.0] * self.dims
        t = text.lower().replace(" ", "")
        for i in range(len(t) - 1):
            h = int(hashlib.md5(t[i : i + 2].encode()).hexdigest(), 16)
            v[h % self.dims] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return tuple(x / n for x in v)

    async def embed(self, model, texts, *, input_type, dimensions):
        self.calls.append((input_type, list(texts)))
        usage = TokenUsage(input_tokens=sum(len(t) for t in texts))
        return EmbeddingCompletion(
            vectors=tuple(self.vector(t) for t in texts),
            model=model,
            parts=(UsagePart(model, usage),),
        )


def gateway(db, config, client):
    return ModelGateway(
        db,
        config,
        PriceBook.load(),
        client_factory=lambda ep, key: client,
        env={"OPENAI_API_KEY": "k"},
    )


@pytest.fixture
async def people(db):
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    return {
        "owner": ActorContext(a, ws_a, scopes_for_role(Role.OWNER)),
        "viewer": ActorContext(a, ws_a, scopes_for_role(Role.VIEWER)),
        "other": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
    }


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blobs")


async def index(db, gw):
    return await run_until_idle(db, index_handlers(IndexDeps(db, gw, per_step=8)))


async def rows(db, actor, model):
    async with db.workspace_session(actor.workspace_id) as s:
        return (await s.execute(select(model))).scalars().all()


# --- 순수 함수 -----------------------------------------------------------------


def test_query_terms_and_snippet():
    assert query_terms("배포  시간, 단축! 배포 a") == ["배포", "시간", "단축"]
    body = "가" * 300 + " 배포 시간을 단축했다 " + "나" * 300
    s = snippet(body, ["배포"])
    assert "배포 시간을" in s and s.startswith("…") and s.endswith("…")
    assert e5_prefix("intfloat/multilingual-e5-large", "x", "query") == "query: x"
    assert e5_prefix("intfloat/multilingual-e5-large", "x", "passage") == "passage: x"
    assert (
        e5_prefix("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "x", "query")
        == "x"
    )


def test_embedding_config_rules():
    with pytest.raises(ConfigError, match="local endpoints only serve the embed route"):
        parse_models_config('[endpoints.l]\npreset="local"\n[routes]\ndefault="l/m"\n')
    with pytest.raises(ConfigError, match="OpenAI-compatible or local"):
        parse_models_config('[endpoints.c]\npreset="anthropic"\n[routes]\nembed="c/m"\n')
    # 임베딩은 기본 라우트로 넘어가지 않는다
    cfg = parse_models_config('[endpoints.p]\npreset="cliproxyapi"\n[routes]\ndefault="p/gpt"\n')
    assert cfg.embedding_route() is None and cfg.route_for("embed") is not None


# --- 30문항 평가 ------------------------------------------------------------------


async def test_synthetic_eval_and_isolation(db, blobs, people):
    owner, other = people["owner"], people["other"]
    fx = load_fixture()
    assert len(fx["queries"]) == 30
    await seed(db, blobs, owner, fx)
    # 다른 공간에 같은 자료를 넣고 표시를 붙인다: 어떤 검색에서도 나오면 안 된다
    marked = json.loads(json.dumps(fx).replace("다.", "다. [B공간]"))
    await seed(db, blobs, other, marked)
    gw = gateway(db, LOCAL, FakeEmbedder())
    await index(db, gw)

    scores = await evaluate(db, owner, fx, gw)
    kw, hybrid = scores["keyword"], scores["hybrid"]
    # 원문 낱말이 들어간 질문은 키워드만으로 전부 찾는다
    assert kw["keyword"].hit5 == kw["keyword"].total == 24
    assert hybrid["keyword"].hit5 == 24
    for q in fx["queries"]:
        for gw_ in (None, gw):
            res = await search(db, owner, q["q"], gateway=gw_, limit=10)
            assert all("[B공간]" not in h.snippet for h in res.hits)
    mine = {h.id for h in (await search(db, owner, "배포 시간 단축", gateway=gw)).hits}
    theirs = {h.id for h in (await search(db, other, "배포 시간 단축", gateway=gw)).hits}
    assert mine and theirs and not mine & theirs


# --- 색인 ---------------------------------------------------------------------


async def test_index_is_incremental_and_fresh(db, blobs, people, caplog):
    owner = people["owner"]
    await seed(db, blobs, owner, load_fixture())
    fake = FakeEmbedder()
    gw = gateway(db, LOCAL, fake)
    with caplog.at_level("WARNING", logger="career_core.jobs.worker"):
        assert await index(db, gw) >= 2  # 8개씩 나눠서 여러 번 돈다
    # 색인 작업은 스스로 done으로 끝낸다 → lease를 잃은 것이 아니다 (경고 없음)
    assert "lease lost" not in caplog.text
    vectors = await rows(db, owner, Embedding)
    # 블록 13 + 주장 5(인터뷰 답에서 만든 것 포함) + 인터뷰 답 1
    assert len(vectors) == 19
    assert {v.space for v in vectors} == {"local:fake-multilingual:native"}
    calls = await rows(db, owner, ModelCall)
    assert {c.purpose for c in calls} == {"embed"} and all(c.status == "ok" for c in calls)

    fake.calls.clear()
    from career_core.usecases.index_queue import enqueue_index

    await enqueue_index(db, owner.workspace_id)
    await index(db, gw)
    mine = {b for exp in load_fixture()["sources"] for b in exp["blocks"]}
    assert not [t for _, texts in fake.calls for t in texts if t in mine]  # 그대로면 다시 안 만든다

    [exp] = [e for e in await brain.list_experiences(db, owner) if e.title == "지원 현황 트래커"]
    claim = (await brain.get_experience(db, owner, exp.id)).claims[0]
    await brain.edit_claim(db, owner, claim.id, statement="웹 크롤러 없이 지원 기록 앱을 만들었다")
    # 고친 직후에는 옛 벡터를 쓰지 않는다 (키워드는 바로 반영)
    res = await search(db, owner, "크롤러", gateway=gw, kinds=("claim",))
    assert [(h.id, h.semantic_rank) for h in res.hits][:1] == [(claim.id, None)]
    await index(db, gw)
    sent = [t for _, texts in fake.calls for t in texts]
    assert "웹 크롤러 없이 지원 기록 앱을 만들었다" in sent
    assert not [t for t in sent if t in mine]
    res = await search(db, owner, "크롤러", gateway=gw, kinds=("claim",))
    assert res.hits[0].id == claim.id and res.hits[0].semantic_rank is not None


async def test_restricted_is_indexed_only_locally(db, blobs, people):
    owner = people["owner"]
    await upload_source(
        db,
        blobs,
        owner,
        filename="secret.txt",
        data="비밀 연봉 계약서 내용".encode(),
        declared_mime="text/plain",
        max_bytes=1 << 20,
        data_class="restricted",
    )
    await upload_source(
        db,
        blobs,
        owner,
        filename="open.txt",
        data="공개 포트폴리오 소개".encode(),
        declared_mime="text/plain",
        max_bytes=1 << 20,
        data_class="public",
    )
    from career_core.adapters.parsers import default_local_parsers
    from career_core.jobs.worker import parsing_handlers
    from career_core.policies import ProviderEgressPolicy
    from career_core.usecases.parsing import ParsingDeps

    await run_until_idle(
        db,
        parsing_handlers(
            ParsingDeps(
                db=db,
                blobs=blobs,
                egress=ProviderEgressPolicy(),
                local_parsers=default_local_parsers(),
            )
        ),
    )
    api = FakeEmbedder()
    await index(db, gateway(db, API, api))
    # (다른 시험이 남긴 색인 작업도 같이 돌 수 있으므로 이 시험의 본문만 본다)
    sent = [t for _, texts in api.calls for t in texts]
    assert "공개 포트폴리오 소개" in sent
    assert "비밀 연봉 계약서 내용" not in sent  # restricted는 API로 보내지 않는다
    local = FakeEmbedder()
    from career_core.usecases.index_queue import enqueue_index

    await enqueue_index(db, owner.workspace_id)  # 임베딩 설정을 바꾸면 다시 색인을 요청한다
    await index(db, gateway(db, LOCAL, local))
    sent = [t for _, texts in local.calls for t in texts]
    assert "공개 포트폴리오 소개" in sent and "비밀 연봉 계약서 내용" in sent
    spaces = {v.space for v in await rows(db, owner, Embedding)}
    assert spaces == {"openai:text-embedding-3-small:64", "local:fake-multilingual:native"}


async def test_semantic_needs_egress_scope_for_api(db, blobs, people):
    owner, viewer = people["owner"], people["viewer"]
    await seed(db, blobs, owner, load_fixture())
    api = gateway(db, API, FakeEmbedder())
    await index(db, api)
    res = await search(db, viewer, "배포", gateway=api)
    assert (res.mode, res.semantic_note) == ("keyword", "egress_scope_required")
    assert res.hits
    res = await search(db, owner, "배포", gateway=api)
    assert res.mode == "hybrid"
    local = gateway(db, LOCAL, FakeEmbedder())
    res = await search(db, viewer, "배포", gateway=local)
    assert (res.mode, res.semantic_note) == ("keyword", "index_empty")  # 다른 공간: 아직 색인 전
    res = await search(db, owner, "배포", gateway=None)
    assert res.semantic_note == "no_embedding_route"


# --- OpenAI 임베딩 어댑터 -----------------------------------------------------------


async def test_openai_embeddings_adapter_and_cost(db, people):
    owner = people["owner"]
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        n = len(seen["body"]["input"])
        data = [
            {"object": "embedding", "index": i, "embedding": [float(i)] * 64}
            for i in reversed(range(n))
        ]
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": data,
                "model": "text-embedding-3-small",
                "usage": {"prompt_tokens": 500_000, "total_tokens": 500_000},
            },
        )

    endpoint = API.endpoints["openai"]
    client = OpenAICompatibleClient(
        endpoint, "k", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    gw = gateway(db, API, client)
    result = await gw.embed(
        owner, ["a", "b", "c"], data_classes=frozenset({"private"}), input_type="passage"
    )
    assert seen["path"].endswith("/embeddings")
    assert seen["body"] == {
        "model": "text-embedding-3-small",
        "input": ["a", "b", "c"],
        "encoding_format": "float",
        "dimensions": 64,
    }
    assert [v[0] for v in result.vectors] == [0.0, 1.0, 2.0]  # index 순서로 정렬
    assert (result.cost_usd, result.cost_basis) == ("0.01", "metered")  # 0.5M × $0.02
    [call] = await rows(db, owner, ModelCall)
    assert (call.purpose, call.input_tokens, call.output_tokens) == ("embed", 500_000, 0)

    with pytest.raises(Exception, match="egress_denied"):
        await gw.embed(owner, ["x"], data_classes=frozenset({"restricted"}), input_type="query")


# --- HTTP ---------------------------------------------------------------------


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


async def test_search_http(db, blobs, people):
    owner = people["owner"]
    await seed(db, blobs, owner, load_fixture())
    gw = gateway(db, LOCAL, FakeEmbedder())
    app = FastAPI()
    app.include_router(
        build_router(
            CoreDeps(
                identity=Tokens(
                    {"owner": owner, "viewer": people["viewer"], "other": people["other"]}
                ),
                db=db,
                blobs=blobs,
                max_upload_bytes=4096,
                models=gw,
            )
        )
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        h = {"Authorization": "Bearer viewer"}
        assert (await c.post("/core/search/reindex", headers=h)).status_code == 403
        r = await c.post("/core/search/reindex", headers={"Authorization": "Bearer owner"})
        assert r.status_code == 202
        await index(db, gw)
        r = await c.get("/core/search", params={"q": "Redis 캐시"}, headers=h)
        body = r.json()
        assert r.status_code == 200 and body["mode"] == "hybrid"
        top = body["hits"][0]
        assert "Redis" in top["snippet"] and top["source_title"] == "합성 이력서"
        r = await c.get("/core/search", params={"q": "배포", "kinds": "claim"}, headers=h)
        assert {x["kind"] for x in r.json()["hits"]} == {"claim"}
        assert all(x["experience_title"] for x in r.json()["hits"])
        r = await c.get(
            "/core/search", params={"q": "Redis 캐시"}, headers={"Authorization": "Bearer other"}
        )
        assert r.json()["hits"] == []
    async with db.workspace_session(owner.workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(Embedding)) == 19


# --- 학습 기록 검색 (메모·오답) -------------------------------------------------------


async def test_study_records_are_searchable_and_labelled(db, people):
    from career_core.usecases import study

    owner, other = people["owner"], people["other"]
    item = await study.create_item(db, owner, title="합성 캐시 공부")
    await study.log_session(
        db, owner, item, minutes=30, notes="합성 메모: 레디스 캐시 무효화 순서를 정리했다"
    )
    await study.add_mistake(
        db,
        owner,
        study_item_id=item,
        question="합성 문제: 트랜잭션 격리 수준",
        wrong_answer="READ UNCOMMITTED가 기본",
        correct_answer="READ COMMITTED가 기본",
    )
    exp = await brain.create_experience(db, owner, title="합성 경험")
    await brain.add_claim(db, owner, exp, statement="레디스 캐시로 응답을 줄였다", kind="result")

    # 기본 검색(브레인)에는 학습 기록이 섞이지 않는다
    res = await search(db, owner, "레디스 캐시")
    assert {h.kind for h in res.hits} == {"claim"}
    assert {h.record for h in res.hits} == {"experience"}

    kinds = ("claim", "study_note", "study_mistake")
    res = await search(db, owner, "레디스 캐시", kinds=kinds)
    note = next(h for h in res.hits if h.kind == "study_note")
    assert note.record == "study" and note.study_title == "합성 캐시 공부"
    assert note.experience_id is None and note.experience_title is None
    assert note.claim_status is None
    res = await search(db, owner, "격리 수준", kinds=kinds)
    [mistake] = [h for h in res.hits if h.kind == "study_mistake"]
    assert mistake.record == "study" and mistake.study_item_id == item
    assert mistake.question == "합성 문제: 트랜잭션 격리 수준"
    res = await search(db, owner, "READ COMMITTED", kinds=kinds)  # 정답 칸도 찾는다
    assert any(h.id == mistake.id for h in res.hits)
    assert (await search(db, other, "레디스 캐시", kinds=kinds)).hits == []

    # 의미 색인: 학습 기록은 private 등급 (API 임베딩이 private을 허용하면 보낸다)
    fake = FakeEmbedder()
    gw = gateway(db, API, fake)
    await index(db, gw)
    sent = [t for _, texts in fake.calls for t in texts]
    assert "합성 메모: 레디스 캐시 무효화 순서를 정리했다" in sent
    assert any(t.startswith("합성 문제: 트랜잭션 격리 수준\nREAD UNCOMMITTED") for t in sent)
    kinds_indexed = {v.target_kind for v in await rows(db, owner, Embedding)}
    assert {"study_note", "study_mistake", "claim"} <= kinds_indexed
    res = await search(db, owner, "캐시 무효화", gateway=gw, kinds=("study_note",))
    assert res.mode == "hybrid" and res.hits[0].semantic_rank is not None

    # 학습 항목을 지우면 그 메모·오답의 벡터도 지운다
    await study.delete_item(db, owner, item)
    assert {v.target_kind for v in await rows(db, owner, Embedding)} == {"claim"}


# --- 색인 실행 중 변경 ---------------------------------------------------------------


class EditingEmbedder(FakeEmbedder):
    """첫 호출 중에 주장을 고친다 (색인 작업이 대상을 고른 뒤 바뀐 자료)."""

    def __init__(self, on_first):
        super().__init__()
        self.on_first = on_first

    async def embed(self, model, texts, *, input_type, dimensions):
        if self.on_first is not None:
            hook, self.on_first = self.on_first, None
            await hook()
        return await super().embed(model, texts, input_type=input_type, dimensions=dimensions)


async def test_change_during_index_run_is_indexed(db, people):
    owner = people["owner"]
    exp = await brain.create_experience(db, owner, title="합성 경험")
    claim = await brain.add_claim(db, owner, exp, statement="처음 문장 (합성)", kind="result")

    async def edit():
        # 실행 중인 작업이 있으므로 이 수정의 색인 예약은 새 작업을 만들지 않는다
        await brain.edit_claim(db, owner, claim, statement="실행 중에 고친 문장 (합성)")

    fake = EditingEmbedder(edit)
    gw = gateway(db, LOCAL, fake)
    await run_until_idle(db, index_handlers(IndexDeps(db, gw)))  # 대상 1개 < per_step
    sent = [t for _, texts in fake.calls for t in texts]
    assert sent[0] == "처음 문장 (합성)" and "실행 중에 고친 문장 (합성)" in sent
    res = await search(db, owner, "고친 문장", gateway=gw, kinds=("claim",))
    assert res.hits[0].id == claim and res.hits[0].semantic_rank is not None
    async with db.workspace_session(owner.workspace_id) as s:
        statuses = (await s.execute(select(Job.status).where(Job.kind == "embed"))).scalars()
        assert set(statuses) == {"done"}


async def test_index_final_check_waits_for_pending_request(db, people):
    """예약 트랜잭션이 커밋되기 전이면 마지막 확인이 기다렸다가 그 변경을 본다."""
    import asyncio

    from career_core.db import Claim
    from career_core.usecases.index_queue import request_index
    from career_core.usecases.indexing import process_index
    from sqlalchemy import update

    owner = people["owner"]
    ws = owner.workspace_id
    exp = await brain.create_experience(db, owner, title="합성 경험")
    claim = await brain.add_claim(db, owner, exp, statement="옛 문장 (합성)", kind="result")
    fake = FakeEmbedder()
    deps = IndexDeps(db, gateway(db, LOCAL, fake))
    await run_until_idle(db, index_handlers(deps))
    async with db.workspace_session(ws) as s:  # 실행 중인 작업 하나 (worker가 집은 상태)
        s.add(Job(workspace_id=ws, kind="embed", target_id=ws, status="running"))

    fake.calls.clear()
    async with db.workspace_session(ws) as s:
        await s.execute(update(Claim).where(Claim.id == claim).values(statement="새 문장 (합성)"))
        await request_index(s, ws)  # 실행 중인 작업이 있어 새 작업은 생기지 않는다
        step = asyncio.create_task(process_index(deps, ws, ws))
        await asyncio.sleep(0.3)
        assert not step.done()  # 커밋 전: 마지막 확인이 lock을 기다린다
    outcome = await step
    assert not outcome.done  # 커밋된 변경을 보고 색인했다 → 한 번 더 확인
    assert [t for _, texts in fake.calls for t in texts] == ["새 문장 (합성)"]
    assert (await process_index(deps, ws, ws)).done
    async with db.workspace_session(ws) as s:
        # 남은 대상이 없다는 확인과 같은 트랜잭션에서 작업을 끝냈다 → 이후 예약은 새 작업이 된다
        statuses = (await s.execute(select(Job.status).where(Job.kind == "embed"))).scalars()
        assert set(statuses) == {"done"}
        await request_index(s, ws)
    async with db.workspace_session(ws) as s:
        queued = await s.scalar(
            select(func.count()).select_from(Job).where(Job.kind == "embed", Job.status == "queued")
        )
        assert queued == 1

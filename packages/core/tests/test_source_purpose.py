"""O20 (일부): 원본 용도 — 내 경험 자료·증빙·참고를 올릴 때 나눈다 (plan §19 원칙 1, §20).

- 증빙: 주장 추출 0건, 외부 모델·외부 임베딩 호출 0건, 등급은 늘 restricted, 검색·MCP에 글이 없다,
  원본 해시가 내보내기·복원 후 같다.
- 참고: 추출 0건, 기본 검색에 0건, ``include_reference``로 보이고 "참고"로 표시, 근거로 연결 불가.
- 용도 바꾸기: → 경험이면 추출을 큐에 넣고, 경험 → 증빙·참고면 확인 전 후보를 닫는다.
  확인된 주장이 이 원본을 근거로 쓰면 409 ``source_has_confirmed_claims``.

합성 자료만 쓴다. DB는 career_core.testing의 임시 DB.
"""

import hashlib
import importlib
import json
import secrets
from uuid import uuid4

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import Claim, Embedding, Experience, ExtractionRun, Job, ModelCall, Source
from career_core.db.migrate import upgrade
from career_core.domain.brain import ClaimRuleError
from career_core.domain.sources import SourcePurpose, suggest_purpose
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.interfaces.mcp.server import McpServer
from career_core.jobs.worker import (
    extraction_handlers,
    index_handlers,
    parsing_handlers,
    run_until_idle,
)
from career_core.llm import EmbeddingCompletion, ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext
from career_core.testing import admin_url
from career_core.usecases import brain, search, sources
from career_core.usecases.export import export_workspace, restore_workspace
from career_core.usecases.extraction import ExtractionDeps, request_extraction
from career_core.usecases.indexing import IndexDeps
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.anyio

# 외부(API) 모델과 외부 임베딩. restricted는 설정에 적어도 무시된다 (llm.config).
CONFIG = parse_models_config("""
[endpoints.proxy]
preset = "cliproxyapi"
price_as = "openai"
data_classes = ["public", "private", "restricted"]

[endpoints.openai]
preset = "openai"
data_classes = ["public", "private", "restricted"]

[routes]
default = "proxy/gpt-6.1-sol"
embed = "openai/text-embedding-3-small"

[embedding]
dimensions = 8
""")
ENV = {"CLIPROXY_API_KEY": "k", "OPENAI_API_KEY": "k"}

RESUME = (
    "Acme Corp, Backend Engineer, 2021.03 - 2023.02\n\n"
    "Built the order service in FastAPI and cut p95 latency from 800ms to 200ms."
)
PROOF = "정보처리기사 자격증 사본 (합성)\n\n위 사람은 정보처리기사 시험에 합격하였음을 증명합니다. 증명번호 ZEBRA-0000"
REFERENCE = "합격 자소서 모음 (합성)\n\n저는 고객 경험을 바꾸는 플라밍고 프로젝트를 이끌었습니다."
PNG = b"\x89PNG\r\n\x1a\n synthetic id photo bytes \x00\x01\x02 (proof)"


class FakeModel:
    """외부 모델·임베딩 대역. 받은 호출을 센다."""

    def __init__(self, reply: str):
        self.reply = reply
        self.completions: list = []
        self.embedded: list[str] = []

    async def complete(self, model, request):
        self.completions.append(request)
        return ModelCompletion(
            text=self.reply,
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, TokenUsage(input_tokens=100, output_tokens=50)),),
            request_id="req",
        )

    async def embed(self, model, texts, *, input_type, dimensions):
        self.embedded.extend(texts)
        return EmbeddingCompletion(
            vectors=tuple((1.0,) + (0.0,) * 7 for _ in texts),
            model=model,
            parts=(UsagePart(model, TokenUsage(input_tokens=len(texts))),),
        )


def reply_for(text):
    """원문 첫 줄 뒤 문장을 인용하는 추출 응답."""
    quote = text.split("\n\n")[1][:40]
    return json.dumps(
        {
            "experiences": [
                {
                    "title": "주문 서비스 (합성)",
                    "kind": "work",
                    "claims": [
                        {
                            "kind": "result",
                            "statement": "응답 시간을 줄였다",
                            "contribution": "personal",
                            "quotes": [{"block": 1, "text": quote}],
                        }
                    ],
                }
            ]
        }
    )


def gateway(db, client):
    return ModelGateway(
        db, CONFIG, PriceBook.load(), client_factory=lambda ep, key: client, env=ENV
    )


@pytest.fixture
async def people(db):
    ws_a, a = await create_workspace(db, name="A (합성)", owner_display_name="amy")
    ws_b, b = await create_workspace(db, name="B (합성)", owner_display_name="ben")
    return {
        "owner": ActorContext(a, ws_a, scopes_for_role(Role.OWNER)),
        "editor": ActorContext(a, ws_a, scopes_for_role(Role.EDITOR)),
        "viewer": ActorContext(a, ws_a, scopes_for_role(Role.VIEWER)),
        "other": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
    }


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blobs")


async def put(db, blobs, actor, text, *, name="cv.txt", purpose="experience", dc="private"):
    return await sources.upload_source(
        db,
        blobs,
        actor,
        filename=name,
        data=text.encode() if isinstance(text, str) else text,
        declared_mime="image/png" if name.endswith(".png") else "text/plain",
        max_bytes=1 << 20,
        data_class=dc,
        purpose=purpose,
    )


async def parse_all(db, blobs):
    deps = ParsingDeps(
        db=db, blobs=blobs, egress=ProviderEgressPolicy(), local_parsers=default_local_parsers()
    )
    await run_until_idle(db, parsing_handlers(deps))


async def extract_all(db, gw):
    await run_until_idle(db, extraction_handlers(ExtractionDeps(db=db, gateway=gw)))


async def count(db, actor, model, *where):
    async with db.workspace_session(actor.workspace_id) as s:
        return await s.scalar(select(func.count()).select_from(model).where(*where))


# ---------------------------------------------------------------- 제안


@pytest.mark.parametrize(
    ("filename", "mime", "head", "want"),
    [
        ("정보처리기사_자격증.pdf", "application/pdf", None, "proof"),
        ("TOEIC 성적표 2024.pdf", "application/pdf", None, "proof"),
        ("opic-result.pdf", "application/pdf", None, "proof"),
        ("졸업증명서.pdf", "application/pdf", None, "proof"),
        ("경력증명서_acme.docx", None, None, "proof"),
        ("증명사진.jpg", "image/jpeg", None, "proof"),
        ("passport scan.png", "image/png", None, "proof"),
        ("IMG_0001.png", "image/png", None, "proof"),  # 사진 파일은 증빙 쪽으로
        ("합격 자소서 모음.pdf", "application/pdf", None, "reference"),
        ("카카오 면접 후기.md", "text/markdown", None, "reference"),
        ("남의 이력서 예시.docx", None, None, "reference"),
        ("이력서_2024.pdf", "application/pdf", None, "experience"),
        ("프로젝트 회고.md", "text/markdown", None, "experience"),
        # 본문은 서류 자체임을 말하는 낱말만 본다 (이력서에도 "자격증"·"TOEIC"이 흔하다)
        ("scan.pdf", "application/pdf", "재직증명서\n성명: 합성", "proof"),
        ("doc.pdf", "application/pdf", "자격증: 정보처리기사\nTOEIC 900", "experience"),
        ("notes.txt", "text/plain", "2023 하반기 면접 후기 정리", "reference"),
        (None, None, None, "experience"),
    ],
)
def test_suggest_purpose(filename, mime, head, want):
    assert suggest_purpose(filename, mime, head).purpose == want


# ---------------------------------------------------------------- 증빙


async def test_proof_is_restricted_never_extracted_never_sent(db, blobs, people):
    owner = people["owner"]
    up = await put(db, blobs, owner, PROOF, name="자격증.txt", purpose="proof", dc="private")
    # 고른 등급과 상관없이 restricted
    assert (up.purpose, up.data_class) == ("proof", "restricted")
    await parse_all(db, blobs)
    with pytest.raises(ClaimRuleError, match="source_not_experience"):
        await request_extraction(db, owner, up.parse_run_id)

    # 요청 경로를 건너뛴 옛 대기 추출도 모델을 부르지 않고 닫힌다
    async with db.workspace_session(owner.workspace_id) as s:
        ext = ExtractionRun(
            id=uuid4(),
            workspace_id=owner.workspace_id,
            parse_run_id=up.parse_run_id,
            requested_by=owner.subject_id,
        )
        s.add(ext)
        await s.flush()
        s.add(Job(workspace_id=owner.workspace_id, kind="extract", target_id=ext.id))
    model = FakeModel(reply_for(PROOF))
    gw = gateway(db, model)
    await extract_all(db, gw)
    async with db.workspace_session(owner.workspace_id) as s:
        ext = await s.get(ExtractionRun, ext.id)
    assert (ext.status, ext.error_code) == ("failed", "source_not_experience")

    # 색인도 증빙 글을 보내지 않는다 (외부 임베딩이 restricted를 허용한다고 적어도)
    await put(db, blobs, owner, RESUME)
    await parse_all(db, blobs)
    await run_until_idle(db, index_handlers(IndexDeps(db, gw)))
    assert model.completions == []
    assert model.embedded and not any("ZEBRA" in t or "증명" in t for t in model.embedded)
    assert await count(db, owner, Claim) == 0
    assert await count(db, owner, ModelCall, ModelCall.purpose == "extract") == 0
    # 증빙 블록에는 벡터가 없다 (로컬 임베딩이어도 색인하지 않는다)
    async with db.workspace_session(owner.workspace_id) as s:
        proof_vectors = await s.scalar(
            text(
                "SELECT count(*) FROM core.embeddings e JOIN core.document_blocks b "
                "ON b.id = e.target_id WHERE b.parse_run_id = :r"
            ),
            {"r": up.parse_run_id},
        )
    assert proof_vectors == 0
    assert await count(db, owner, Embedding) > 0

    # 검색: 기본·참고 포함 어느 쪽에도 증빙 글은 없다
    for include in (False, True):
        res = await search.search(db, owner, "ZEBRA 증명번호", include_reference=include)
        assert not [h for h in res.hits if "ZEBRA" in h.snippet]

    # 근거로 쓸 수 없다
    async with db.workspace_session(owner.workspace_id) as s:
        block_id = await s.scalar(
            text("SELECT id FROM core.document_blocks WHERE parse_run_id = :r LIMIT 1"),
            {"r": up.parse_run_id},
        )
    with pytest.raises(ClaimRuleError, match="source_not_experience"):
        await brain.create_span(db, owner, block_id, 0, 3)

    # DB 제약: 증빙은 restricted만
    async with db.workspace_session(owner.workspace_id) as s:
        src = await s.get(Source, up.source_id)
        src.data_class = "private"
        with pytest.raises(Exception, match="ck_sources_proof_restricted"):
            await s.flush()


async def test_same_file_as_proof_raises_class_but_keeps_purpose(db, blobs, people):
    owner = people["owner"]
    first = await put(db, blobs, owner, RESUME)
    again = await put(db, blobs, owner, RESUME, purpose="proof")
    assert again.deduplicated and again.source_id == first.source_id
    # 용도는 처음 원본 그대로 (바꾸려면 PATCH), 등급은 더 엄격한 쪽으로 올린다
    assert (again.purpose, again.data_class) == ("experience", "restricted")


# ---------------------------------------------------------------- 참고


async def test_reference_is_not_extracted_and_hidden_from_default_search(db, blobs, people):
    owner = people["owner"]
    ref = await put(db, blobs, owner, REFERENCE, name="합격 자소서.txt", purpose="reference")
    mine = await put(db, blobs, owner, RESUME)
    await parse_all(db, blobs)
    with pytest.raises(ClaimRuleError, match="source_not_experience"):
        await request_extraction(db, owner, ref.parse_run_id)
    assert await request_extraction(db, owner, mine.parse_run_id)

    res = await search.search(db, owner, "플라밍고 프로젝트")
    assert [h for h in res.hits if h.source_id == ref.source_id] == []
    res = await search.search(db, owner, "플라밍고 프로젝트", include_reference=True)
    hits = [h for h in res.hits if h.source_id == ref.source_id]
    assert hits and {(h.record, h.purpose) for h in hits} == {("reference", "reference")}
    # 내 원본은 그대로 source
    res = await search.search(db, owner, "order service", include_reference=True)
    assert {h.record for h in res.hits if h.source_id == mine.source_id} == {"source"}

    # 참고 원본은 근거가 되지 않는다
    async with db.workspace_session(owner.workspace_id) as s:
        block_id = await s.scalar(
            text("SELECT id FROM core.document_blocks WHERE parse_run_id = :r LIMIT 1"),
            {"r": ref.parse_run_id},
        )
    with pytest.raises(ClaimRuleError, match="source_not_experience"):
        await brain.create_span(db, owner, block_id, 0, 3)


# ---------------------------------------------------------------- 용도 바꾸기 (HTTP)


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def client_for(db, blobs, people, gw=None):
    app = FastAPI()
    app.include_router(
        build_router(CoreDeps(identity=Tokens(people), db=db, blobs=blobs, models=gw))
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t")


async def test_http_upload_suggest_list_and_patch_rules(db, blobs, people):
    owner = people["owner"]
    model = FakeModel(reply_for(RESUME))
    gw = gateway(db, model)
    async with client_for(db, blobs, people, gw) as c:
        r = await c.post(
            "/core/sources/suggest-purpose",
            json={"filename": "TOEIC 성적표.pdf", "mime": "application/pdf"},
            headers=auth("viewer"),
        )
        assert r.json() == {"suggested_purpose": "proof", "matched": "성적표"}

        # 용도 없이 올리면 예전처럼 experience
        r = await c.post(
            "/core/sources",
            files={"file": ("cv.txt", RESUME.encode(), "text/plain")},
            headers=auth("owner"),
        )
        assert r.status_code == 200, r.text
        assert (r.json()["purpose"], r.json()["data_class"]) == ("experience", "private")
        cv = r.json()["source_id"]
        r = await c.post(
            "/core/sources",
            files={"file": ("x.txt", b"other", "text/plain")},
            data={"purpose": "mine"},
            headers=auth("owner"),
        )
        assert (r.status_code, r.json()["detail"]) == (422, "invalid_purpose")
        r = await c.post(
            "/core/sources",
            files={"file": ("면접 후기.txt", REFERENCE.encode(), "text/plain")},
            data={"purpose": "reference"},
            headers=auth("owner"),
        )
        ref = r.json()["source_id"]
        await parse_all(db, blobs)

        # 받은 자료함: 추출 전인 내 자료는 올라오고, 다 읽은 참고 자료는 안 올라온다
        r = await c.get("/core/sources", params={"attention": "true"}, headers=auth("viewer"))
        assert [(x["id"], x["attention"]) for x in r.json()] == [(cv, "not_extracted")]
        r = await c.get("/core/sources", params={"purpose": "reference"}, headers=auth("viewer"))
        assert [x["id"] for x in r.json()] == [ref]
        r = await c.get("/core/sources", params={"purpose": "x"}, headers=auth("viewer"))
        assert r.status_code == 422

        # 추출 → 후보 1건
        src = (await c.get(f"/core/sources/{cv}", headers=auth("viewer"))).json()
        run_id = src["versions"][0]["parse"]["run_id"]
        r = await c.post(f"/core/parse-runs/{run_id}/extractions", headers=auth("owner"))
        assert r.status_code == 202, r.text
        await extract_all(db, gw)
        src = (await c.get(f"/core/sources/{cv}", headers=auth("viewer"))).json()
        assert (src["purpose"], src["pending_claims"], src["confirmed_claims"]) == (
            "experience",
            1,
            0,
        )
        assert src["extraction"]["status"] == "complete"
        r = await c.get("/core/sources", params={"attention": "true"}, headers=auth("viewer"))
        assert [(x["id"], x["attention"], x["pending_claims"]) for x in r.json()] == [
            (cv, "pending_claims", 1)
        ]
        # 참고 원본은 추출 요청 자체가 409
        ref_run = (await c.get(f"/core/sources/{ref}", headers=auth("viewer"))).json()["versions"][
            0
        ]["parse"]["run_id"]
        r = await c.post(f"/core/parse-runs/{ref_run}/extractions", headers=auth("owner"))
        assert (r.status_code, r.json()["detail"]) == (409, "source_not_experience")

        # 경험 → 참고: 확인 전 후보와 AI 경험을 닫는다
        assert (
            await c.patch(
                f"/core/sources/{cv}", json={"purpose": "reference"}, headers=auth("viewer")
            )
        ).status_code == 403
        r = await c.patch(
            f"/core/sources/{cv}", json={"purpose": "reference"}, headers=auth("owner")
        )
        assert r.status_code == 200, r.text
        got = r.json()
        assert (got["purpose"], got["changed"], got["discarded_claims"]) == ("reference", True, 1)
        assert got["discarded_experiences"] == 1 and got["extraction_run_id"] is None
        assert await count(db, owner, Claim, Claim.status == "extracted") == 0
        assert await count(db, owner, Experience, Experience.status == "active") == 0
        # 같은 값이면 바뀜 없음
        r = await c.patch(
            f"/core/sources/{cv}", json={"purpose": "reference"}, headers=auth("owner")
        )
        assert r.json()["changed"] is False

        # 참고 → 경험: 추출을 다시 큐에 넣는다 (일반 추출 요청과 같은 작업)
        r = await c.patch(
            f"/core/sources/{cv}", json={"purpose": "experience"}, headers=auth("owner")
        )
        assert r.json()["extraction_run_id"] is not None
        await extract_all(db, gw)
        [exp] = await brain.list_experiences(db, owner)
        [claim] = (await brain.get_experience(db, owner, exp.id)).claims
        await brain.confirm_claim(db, owner, claim.id, contribution="personal")

        # 확인된 주장이 이 원본을 근거로 쓰면 바꾸지 않는다
        for target in ("proof", "reference"):
            r = await c.patch(
                f"/core/sources/{cv}", json={"purpose": target}, headers=auth("owner")
            )
            assert (r.status_code, r.json()) == (409, {"detail": "source_has_confirmed_claims"})
        async with db.workspace_session(owner.workspace_id) as s:
            assert (await s.get(Source, cv)).purpose == "experience"

        # 증빙은 restricted만, 모르는 용도는 422
        r = await c.patch(
            f"/core/sources/{ref}",
            json={"purpose": "proof", "data_class": "private"},
            headers=auth("owner"),
        )
        assert (r.status_code, r.json()["detail"]) == (422, "proof_requires_restricted")
        r = await c.patch(f"/core/sources/{ref}", json={"purpose": "x"}, headers=auth("owner"))
        assert r.status_code == 422
        r = await c.patch(f"/core/sources/{ref}", json={"purpose": "proof"}, headers=auth("owner"))
        assert (r.json()["purpose"], r.json()["data_class"]) == ("proof", "restricted")
        # 증빙에서 나와도 등급은 사람이 낮출 때까지 restricted
        r = await c.patch(
            f"/core/sources/{ref}", json={"purpose": "reference"}, headers=auth("owner")
        )
        assert r.json()["data_class"] == "restricted"

        # 다른 공간: 0건, 404
        r = await c.get("/core/sources", headers=auth("other"))
        assert r.json() == []
        r = await c.patch(f"/core/sources/{cv}", json={"purpose": "proof"}, headers=auth("other"))
        assert r.status_code == 404
        r = await c.get(f"/core/sources/{cv}", headers=auth("other"))
        assert r.status_code == 404
    # 이 시험의 모델 호출은 내 경험 자료의 추출 두 번뿐
    assert len(model.completions) == 2


async def test_workspace_isolation_of_purpose_rows(db, blobs, people):
    owner, other = people["owner"], people["other"]
    await put(db, blobs, owner, PROOF, name="자격증.txt", purpose="proof")
    await put(db, blobs, owner, REFERENCE, name="합격 자소서.txt", purpose="reference")
    await parse_all(db, blobs)
    for purpose in ("experience", "proof", "reference", None):
        assert await sources.list_sources(db, other, purpose=purpose) == []
    assert await count(db, other, Source) == 0
    assert len(await sources.list_sources(db, owner)) == 2
    res = await search.search(db, other, "플라밍고", include_reference=True)
    assert res.hits == []


# ---------------------------------------------------------------- MCP


async def test_mcp_marks_reference_and_hides_proof(db, blobs, people):
    owner = people["owner"]
    mine = await put(db, blobs, owner, RESUME)
    await put(db, blobs, owner, REFERENCE, name="합격 자소서.txt", purpose="reference")
    await parse_all(db, blobs)
    # 경험 자료에 근거 위치를 하나 만든 뒤 증빙으로 바꾼다 (확인된 주장이 없으니 바뀐다)
    async with db.workspace_session(owner.workspace_id) as s:
        block_id = await s.scalar(
            text("SELECT id FROM core.document_blocks WHERE parse_run_id = :r ORDER BY ordinal"),
            {"r": mine.parse_run_id},
        )
    span = await brain.create_span(db, owner, block_id, 0, 4)
    server = McpServer(CoreDeps(identity=Tokens({}), db=db, blobs=blobs))

    out = await server.call_tool("search_brain", {"query": "플라밍고 프로젝트"}, owner)
    assert out["structuredContent"]["hits"] == []
    out = await server.call_tool(
        "search_brain", {"query": "플라밍고 프로젝트", "include_reference": True}, owner
    )
    hits = out["structuredContent"]["hits"]
    assert hits and {h["record"] for h in hits} == {"reference"}
    assert all("not the user's experience" in h["note"] for h in hits)

    assert not (await server.call_tool("get_evidence", {"source_span_id": str(span)}, owner))[
        "isError"
    ]
    await sources.change_purpose(db, owner, mine.source_id, "proof")
    out = await server.call_tool("get_evidence", {"source_span_id": str(span)}, owner)
    assert out["isError"] and out["structuredContent"] == {"error": "proof_not_shared"}
    assert "Acme" not in json.dumps(out, ensure_ascii=False)
    out = await server.call_tool("search_brain", {"query": "Acme order service"}, owner)
    assert out["structuredContent"]["hits"] == []


# ---------------------------------------------------------------- 내보내기·복원


async def test_proof_original_round_trips_byte_identical(db, blobs, people, tmp_path):
    owner = people["owner"]
    up = await put(db, blobs, owner, PNG, name="증명사진.png", purpose="proof")
    want = hashlib.sha256(PNG).hexdigest()
    out = tmp_path / "a.tar.gz"
    await export_workspace(db, blobs, owner, out)
    target_blobs = LocalBlobStore(tmp_path / "restored")
    restored = await restore_workspace(db, target_blobs, out, new_ids=True)
    copy = ActorContext(restored.owners[0], restored.workspace_id, scopes_for_role(Role.OWNER))
    async with db.workspace_session(copy.workspace_id) as s:
        row = (
            await s.execute(
                text(
                    "SELECT s.purpose, s.data_class, v.sha256, v.blob_key FROM core.sources s "
                    "JOIN core.source_versions v ON v.source_id = s.id"
                )
            )
        ).one()
    assert (row.purpose, row.data_class, row.sha256) == ("proof", "restricted", want)
    data = await target_blobs.get(copy.workspace_id, row.blob_key)
    assert hashlib.sha256(data).hexdigest() == want and data == PNG
    assert up.source_id != (await sources.list_sources(db, copy))[0].id


# ---------------------------------------------------------------- migration


def test_c0032_existing_rows_become_experience(pg):
    mod = importlib.import_module("career_core.migrations.versions.c0032_source_purpose")
    assert (mod.revision, mod.down_revision) == ("c0032", "c0031")
    admin = admin_url()
    name = f"career_test_{secrets.token_hex(4)}"
    eng = create_engine(admin, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    owner = admin.set(database=name)
    ws, actor = uuid4(), uuid4()
    try:
        upgrade(owner, revision="c0031")
        e = create_engine(owner, poolclass=NullPool)
        with e.begin() as conn:
            conn.execute(text("INSERT INTO core.workspaces (id, name) VALUES (:w, 'm')"), {"w": ws})
            for dc in ("public", "private", "restricted"):
                conn.execute(
                    text(
                        "INSERT INTO core.sources (id, workspace_id, title, data_class, created_by) "
                        "VALUES (:i, :w, :t, :d, :a)"
                    ),
                    {"i": uuid4(), "w": ws, "t": f"옛 원본 {dc} (합성)", "d": dc, "a": actor},
                )
        upgrade(owner, revision="c0032")
        with e.begin() as conn:
            got = conn.execute(text("SELECT data_class, purpose FROM core.sources")).all()
            forced = conn.scalar(
                text(
                    "SELECT relforcerowsecurity FROM pg_class WHERE oid = 'core.sources'::regclass"
                )
            )
        assert sorted(tuple(r) for r in got) == [
            ("private", "experience"),
            ("public", "experience"),
            ("restricted", "experience"),
        ]
        assert forced is True
        with e.begin() as conn, pytest.raises(Exception, match="ck_sources_purpose"):
            conn.execute(text("UPDATE core.sources SET purpose = 'mine'"))
        with e.begin() as conn, pytest.raises(Exception, match="ck_sources_proof_restricted"):
            conn.execute(text("UPDATE core.sources SET purpose = 'proof'"))
        e.dispose()
    finally:
        with eng.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        eng.dispose()


def test_purpose_enum_matches_migration():
    mod = importlib.import_module("career_core.migrations.versions.c0032_source_purpose")
    assert tuple(p.value for p in SourcePurpose) == mod.PURPOSES

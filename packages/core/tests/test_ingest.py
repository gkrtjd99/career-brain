"""브레인에 넣기 (c0039, plan §9·§20): 넣은 것 고르기, 확인 대기, 이 기기에서 읽은 글, 사진의 모델 읽기.

- 고르기: 파일 이름·형식·앞부분 글(주민등록번호 모양 포함)과 주소 규칙. 모델을 부르지 않는다.
- 확인 대기: 확신하지 못한 것은 restricted로 올라가 받은 자료함 "확인 필요"에 남고, 사람이 확인하면 풀린다.
- 이 기기에서 읽은 글: 증빙 사진의 글을 붙이면 자격·이수 후보가 위치와 함께 나온다. 주민등록번호는 가린다.
  증빙은 외부 파서·모델로 가지 않는다 (대역이 센다).
- 증빙이 아닌 사진은 비전 라우트가 있을 때만 모델로 읽고, 증빙·확인 대기는 그 길을 쓰지 않는다.

합성 자료만 쓴다.
"""

import importlib
import json
import secrets
from uuid import uuid4

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import DocumentBlock, ModelCall, ParseRun, Source
from career_core.db.migrate import upgrade
from career_core.domain.ingest import (
    LayerBlock,
    TextLayer,
    TextLayerError,
    check_text_layer,
    classify_file,
    classify_url,
    company_hint_of,
    mask_resident_numbers,
)
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import parsing_handlers, run_until_idle
from career_core.llm import ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ACTOR_AGENT, ActorContext, ProviderParseStatus
from career_core.testing import admin_url
from career_core.usecases import credentials as cred_uc
from career_core.usecases import ingest, sources
from career_core.usecases.export import export_workspace, restore_workspace
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.anyio

PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic certificate scan (proof) \x00\x01\x02"
PNG2 = b"\x89PNG\r\n\x1a\n" + b"synthetic project screenshot \x03\x04"
PNG3 = b"\x89PNG\r\n\x1a\n" + b"synthetic unknown photo \x05\x06"

# 브라우저 OCR이 읽은 줄 (합성 자격증). 주민등록번호는 브라우저가 가리지 못했다고 치고 서버가 가린다
OCR_LINES = [
    ("국가기술자격증 (합성)", (40, 30, 520, 70)),
    ("자격종목: 정보처리기사", (40, 120, 480, 150)),
    ("자격번호: 23201010999Z", (40, 160, 480, 190)),
    ("합격일자: 2023.06.09", (40, 200, 480, 230)),
    ("성명: 김가상 주민등록번호: 900101-1234567", (40, 240, 600, 270)),
    ("위 사람은 국가기술자격법에 의하여 위 자격을 취득하였음을 증명합니다.", (40, 320, 900, 350)),
    ("한국산업인력공단 이사장", (300, 600, 700, 640)),
]


def layer(lines=OCR_LINES, *, masked=0, pages_total=1):
    return TextLayer(
        engine="tesseract.js 7 (kor+eng)",
        pages_total=pages_total,
        masked=masked,
        blocks=tuple(LayerBlock(1, t, box) for t, box in lines),
    )


# ---------------------------------------------------------------- 고르기 (순수)


@pytest.mark.parametrize(
    ("filename", "mime", "head", "want", "conf"),
    [
        # 본문의 서류 문장 → 증빙 (확신)
        (
            "scan.pdf",
            "application/pdf",
            "위 사람은 아래 과정을 수료하였음을 증명합니다.",
            "proof",
            "high",
        ),
        ("a.png", "image/png", "TOEIC Official Score Report\nLISTENING 450", "proof", "high"),
        ("b.png", "image/png", "수료증\n과정명: 백엔드 부트캠프", "proof", "high"),
        ("c.png", "image/png", "재직증명서\n회사명: 에이크미", "proof", "high"),
        ("d.png", "image/png", "졸업증명서 학교명: 가상대학교", "proof", "high"),
        ("e.png", "image/png", "합격증명서 (합성)", "proof", "high"),
        (
            "f.txt",
            "text/plain",
            "This is to certify that Kim completed the course",
            "proof",
            "high",
        ),
        # 주민등록번호 모양이면 남의 글처럼 보여도 증빙 (이 기기 밖으로 보내지 않는 쪽)
        ("면접 후기.txt", "text/plain", "성명 김가상 900101-1234567", "proof", "high"),
        ("x.txt", "text/plain", "주민번호 9001011234567", "proof", "high"),
        # 짧은 글의 자격·어학 낱말 → 증빙 (확인 필요)
        ("g.png", "image/png", "OPIc\n등급 IH\n2024.03.09", "proof", "low"),
        # 1인칭 서술 → 내 경험
        (
            "memo.txt",
            "text/plain",
            "저는 주문 서비스를 맡아 설계했고, 응답 시간을 800ms에서 200ms로 줄였습니다.",
            "experience",
            "high",
        ),
        ("memo2.txt", "text/plain", "운영 자동화를 담당했습니다.", "experience", "low"),
        ("이력서_2024.pdf", "application/pdf", None, "experience", "high"),
        ("my-portfolio.pdf", "application/pdf", None, "experience", "high"),
        ("kim_cv.pdf", "application/pdf", None, "experience", "high"),
        # 이력서 한가운데의 "자격증"·"TOEIC"은 증빙이 아니다 (긴 글, 1인칭)
        (
            "doc.pdf",
            "application/pdf",
            "자격증: 정보처리기사, TOEIC 900\n"
            + "저는 결제 시스템을 개발했고 장애를 줄였습니다. " * 30,
            "experience",
            "high",
        ),
        # 남의 글
        ("notes.txt", "text/plain", "2023 하반기 카카오 면접 후기 정리", "reference", "high"),
        ("합격 자소서 모음.pdf", "application/pdf", None, "reference", "high"),
        ("h.txt", "text/plain", "다른 지원자들의 후기를 모았다", "reference", "low"),
        # 글 없는 사진 → 증빙 (확인 필요)
        ("IMG_0001.png", "image/png", None, "proof", "low"),
        ("증명사진.jpg", "image/jpeg", None, "proof", "high"),
        (None, None, None, "experience", "low"),
    ],
)
def test_classify_file(filename, mime, head, want, conf):
    got = classify_file(filename, mime, head)
    assert (got.purpose.value, got.confidence) == (want, conf), got


def test_classify_file_resident_number_flag():
    assert classify_file("a.txt", "text/plain", "900101-1234567").resident_number
    assert not classify_file("a.txt", "text/plain", "2024-0101-12").resident_number


@pytest.mark.parametrize(
    ("url", "kind", "conf"),
    [
        ("https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=48123456", "posting", "high"),
        ("https://www.wanted.co.kr/wd/123456", "posting", "high"),
        ("https://www.jobkorea.co.kr/Recruit/GI_Read/45678901", "posting", "high"),
        ("https://boards.greenhouse.io/acme/jobs/4012345", "posting", "high"),
        ("https://jobs.lever.co/acme/2b1e-uuid", "posting", "high"),
        ("https://acme.career.greetinghr.com/o/98765", "posting", "high"),
        ("https://careers.acme.example/jobs/backend-engineer", "posting", "high"),
        ("https://acme.example/recruit/detail/77", "posting", "high"),
        ("https://acme.example/careers/12345", "posting", "high"),
        ("https://acme.example/apply?jobId=12", "posting", "high"),
        (
            "https://www.saramin.co.kr/",
            "posting",
            "low",
        ),  # 공고 사이트 첫 화면은 공고 한 건이 아니다
        ("https://acme.example/about", "company", "high"),
        ("https://acme.example/ko/company/overview", "company", "high"),
        ("https://ir.acme.example/ir/financials", "company", "high"),
        ("https://acme.example/culture", "company", "high"),
        ("https://acme.example/careers", "company", "low"),
        ("https://acme.example/", "company", "low"),
        ("https://acme.example/blog/2024/launch", "posting", "low"),
    ],
)
def test_classify_url(url, kind, conf):
    got = classify_url(url)
    assert (got.kind, got.confidence) == (kind, conf), got


def test_company_hint():
    assert company_hint_of("careers.acme.co.kr") == "acme"
    assert company_hint_of("acme.com") == "acme"
    assert company_hint_of("ir.zebra-labs.io") == "zebra-labs"
    assert company_hint_of("saramin.co.kr") is None
    assert company_hint_of("127.0.0.1") is None


def test_text_layer_rules_and_masking():
    got = check_text_layer(layer(), mime="image/png")
    joined = "\n".join(b.text for b in got.blocks)
    assert "900101-1234567" not in joined and "******-*******" in joined
    assert got.masked == 1
    assert mask_resident_numbers("a 900101-2234567 b 9001012234567")[1] == 2
    for bad, code in [
        ({"mime": "text/plain"}, "text_layer_not_needed"),
        ({"mime": "image/png", "layer": layer(pages_total=2)}, "invalid_page"),
        ({"mime": "image/png", "layer": layer([("글", (5, 5, 1, 1))])}, "invalid_bbox"),
        ({"mime": "image/png", "layer": layer([("   ", None)])}, "empty_text_layer"),
        ({"mime": "image/png", "layer": layer([("가" * 20_001, None)])}, "block_too_long"),
    ]:
        with pytest.raises(TextLayerError, match=code):
            check_text_layer(bad.get("layer", layer()), mime=bad["mime"])


# ---------------------------------------------------------------- DB


CONFIG = parse_models_config("""
[endpoints.proxy]
preset = "cliproxyapi"
price_as = "openai"
data_classes = ["public", "private", "restricted"]

[routes]
default = "proxy/gpt-6.1-sol"
source_ocr = "proxy/gpt-6.1-sol"
""")
NO_VISION = parse_models_config("""
[endpoints.proxy]
preset = "cliproxyapi"
price_as = "openai"
data_classes = ["public", "private"]

[routes]
default = "proxy/gpt-6.1-sol"
""")
ENV = {"CLIPROXY_API_KEY": "k"}


class FakeModel:
    def __init__(self, reply="프로젝트 화면 (합성)\n\n주문 대시보드 지연 200ms"):
        self.reply = reply
        self.completions: list = []

    async def complete(self, model, request):
        self.completions.append(request)
        return ModelCompletion(
            text=self.reply,
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, TokenUsage(input_tokens=100, output_tokens=50)),),
            request_id="req",
        )


class FakeOcr:
    """외부 파서 대역. 설정에 restricted를 적어도 증빙은 제출되면 안 된다."""

    name = "fakeocr"

    def __init__(self):
        self.submitted: list = []

    async def submit(self, data, mime_type, pages, idempotency_key):
        self.submitted.append(idempotency_key)
        return "job-1"

    async def poll(self, provider_job_id):
        return ProviderParseStatus(state="failed")


def gateway(db, client, config=CONFIG):
    return ModelGateway(
        db, config, PriceBook.load(), client_factory=lambda ep, key: client, env=ENV
    )


@pytest.fixture
async def people(db):
    ws_a, a = await create_workspace(db, name="A (합성)", owner_display_name="amy")
    ws_b, b = await create_workspace(db, name="B (합성)", owner_display_name="ben")
    return {
        "owner": ActorContext(a, ws_a, scopes_for_role(Role.OWNER)),
        "agent": ActorContext(a, ws_a, scopes_for_role(Role.OWNER), kind=ACTOR_AGENT),
        "other": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
    }


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blobs")


async def put(db, blobs, actor, data, *, name, mime="image/png", purpose="proof", **kw):
    return await sources.upload_source(
        db,
        blobs,
        actor,
        filename=name,
        data=data,
        declared_mime=mime,
        max_bytes=1 << 20,
        purpose=purpose,
        **kw,
    )


async def parse_all(db, blobs, *, gw=None, ocr=None):
    deps = ParsingDeps(
        db=db,
        blobs=blobs,
        egress=ProviderEgressPolicy({"fakeocr": ["public", "private", "restricted"]}),
        local_parsers=default_local_parsers(),
        provider=ocr,
        gateway=gw,
    )
    await run_until_idle(db, parsing_handlers(deps))


async def run_of(db, actor, run_id):
    async with db.workspace_session(actor.workspace_id) as s:
        return await s.get(ParseRun, run_id)


async def blocks_of(db, actor, run_id):
    async with db.workspace_session(actor.workspace_id) as s:
        return (
            (
                await s.execute(
                    select(DocumentBlock)
                    .where(DocumentBlock.parse_run_id == run_id)
                    .order_by(DocumentBlock.ordinal)
                )
            )
            .scalars()
            .all()
        )


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


def client_for(db, blobs, people, models=None):
    app = FastAPI()
    deps = CoreDeps(identity=Tokens(people), db=db, blobs=blobs, models=models)
    app.include_router(build_router(deps))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t")


def auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------- 이 기기에서 읽은 글 → 자격·이수 후보


async def test_device_text_layer_on_proof_image_extracts_credential_with_locations(
    db, blobs, people
):
    owner = people["owner"]
    model, ocr = FakeModel(), FakeOcr()
    up = await put(db, blobs, owner, PNG, name="scan.png")
    # 읽기가 먼저 끝난다: 사진은 이 서버가 글자를 못 읽는다 (외부 파서·모델은 증빙을 받지 않는다)
    await parse_all(db, blobs, gw=gateway(db, model), ocr=ocr)
    run = await run_of(db, owner, up.parse_run_id)
    assert (run.status, run.error_code) == ("failed", "egress_denied")
    assert ocr.submitted == [] and model.completions == []

    got = await ingest.attach_text_layer(db, owner, up.source_id, layer())
    assert (got.parse_status, got.missing_pages, got.masked) == ("complete", [], 1)
    run = await run_of(db, owner, up.parse_run_id)
    assert (run.status, run.error_code, run.missing_pages) == ("complete", None, [])
    assert run.text_layer["origin"] == "device"
    assert run.text_layer["masked_resident_numbers"] == 1
    blocks = await blocks_of(db, owner, up.parse_run_id)
    assert {b.origin for b in blocks} == {"device_ocr"}
    assert [b.ordinal for b in blocks] == list(range(len(OCR_LINES)))
    assert blocks[1].bbox == [40, 120, 480, 150] and blocks[1].page == 1
    assert not any("900101-1234567" in b.text for b in blocks)

    # 자격·이수 후보가 원문 위치와 함께 (모델 호출 0)
    [c] = await cred_uc.list_credentials(db, owner, reveal=True)
    assert (c.kind, c.name, c.number, c.status) == (
        "license",
        "정보처리기사",
        "23201010999Z",
        "extracted",
    )
    span = c.field_spans["number"]
    b = next(x for x in blocks if x.ordinal == span["block"])
    assert b.text[span["start"] : span["end"]] == "23201010999Z"
    assert b.bbox == [40, 160, 480, 190]
    # 가렸어도 "주민등록번호 보임"은 남는다
    [proof] = await cred_uc.list_proofs(db, owner)
    assert proof.resident_number is True
    # 원본 바이트는 그대로
    f = await cred_uc.read_original(db, blobs, owner, up.source_id)
    assert f.data == PNG
    async with db.workspace_session(owner.workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(ModelCall)) == 0


async def test_text_layer_before_parse_is_seen_by_worker(db, blobs, people):
    """올리기와 같은 요청에 붙인 글: worker가 그 쪽을 못 읽은 쪽으로 보지 않는다 (OCR·모델 호출 0)."""
    owner = people["owner"]
    model, ocr = FakeModel(), FakeOcr()
    up = await put(db, blobs, owner, PNG, name="scan.png", text_layer=layer())
    assert up.text_blocks == len(OCR_LINES)
    await parse_all(db, blobs, gw=gateway(db, model), ocr=ocr)
    run = await run_of(db, owner, up.parse_run_id)
    assert (run.status, run.missing_pages, run.pages_total) == ("complete", [], 1)
    assert ocr.submitted == [] and model.completions == []
    [c] = await cred_uc.list_credentials(db, owner)
    assert c.name == "정보처리기사" and c.field_spans


async def test_text_layer_rules_http_and_isolation(db, blobs, people):
    owner = people["owner"]
    tokens = {"t-a": owner, "t-b": people["other"]}
    img = await put(db, blobs, owner, PNG, name="scan.png")
    txt = await put(db, blobs, owner, b"plain text (synthetic)", name="a.txt", mime="text/plain")
    body = {
        "engine": "tesseract.js 7",
        "blocks": [{"page": 1, "text": t, "bbox": list(box)} for t, box in OCR_LINES],
        "masked": 0,
    }
    async with client_for(db, blobs, tokens) as c:
        # 다른 공간: 없는 것과 같다
        r = await c.put(f"/core/sources/{img.source_id}/text-layer", json=body, headers=auth("t-b"))
        assert r.status_code == 404
        r = await c.put(f"/core/sources/{txt.source_id}/text-layer", json=body, headers=auth("t-a"))
        assert (r.status_code, r.json()["detail"]) == (422, "text_layer_not_needed")
        bad = {**body, "blocks": [{"page": 1, "text": "x", "bbox": [1, 2, 3]}]}
        r = await c.put(f"/core/sources/{img.source_id}/text-layer", json=bad, headers=auth("t-a"))
        assert r.status_code == 422
        # 읽기 전 사진에는 붙는다 (worker가 끝낼 때 본다)
        r = await c.put(f"/core/sources/{img.source_id}/text-layer", json=body, headers=auth("t-a"))
        assert r.status_code == 200 and r.json()["parse_status"] == "pending"
        # 다시 붙이면 바꿔 넣는다 (쌓이지 않는다)
        r = await c.put(f"/core/sources/{img.source_id}/text-layer", json=body, headers=auth("t-a"))
        assert r.json()["blocks"] == len(OCR_LINES)
        assert len(await blocks_of(db, owner, img.parse_run_id)) == len(OCR_LINES)
        # 올리기와 함께 (multipart) + 다른 공간 원본 목록에는 없다
        r = await c.post(
            "/core/sources",
            files={"file": ("scan2.png", PNG3, "image/png")},
            data={"purpose": "proof", "text_layer": json.dumps(body)},
            headers=auth("t-a"),
        )
        assert r.status_code == 200 and r.json()["text_blocks"] == len(OCR_LINES)
        r = await c.get("/core/sources", headers=auth("t-b"))
        assert r.json() == []
    await parse_all(db, blobs)
    run = await run_of(db, owner, img.parse_run_id)
    assert run.status == "complete"


async def test_scanned_pdf_text_layer_waits_for_parse(db, blobs, people):
    owner = people["owner"]
    pdf = await put(db, blobs, owner, _blank_pdf(), name="scan.pdf", mime="application/pdf")
    with pytest.raises(TextLayerError, match="source_not_ready"):
        await ingest.attach_text_layer(db, owner, pdf.source_id, layer())
    await parse_all(db, blobs)
    run = await run_of(db, owner, pdf.parse_run_id)
    assert run.status == "failed" and run.missing_pages == [1]
    got = await ingest.attach_text_layer(db, owner, pdf.source_id, layer())
    assert got.parse_status == "complete"


def _blank_pdf() -> bytes:
    """글자 없는 1쪽 PDF (스캔본 대역)."""
    import io

    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- 사진의 모델 읽기


async def test_model_ocr_only_for_non_proof_images(db, blobs, people):
    owner = people["owner"]
    model = FakeModel()
    gw = gateway(db, model)
    proof = await put(db, blobs, owner, PNG, name="scan.png", purpose="proof")
    mine = await put(db, blobs, owner, PNG2, name="화면.png", purpose="experience")
    waiting = await put(
        db, blobs, owner, PNG3, name="IMG.png", purpose="proof", purpose_pending=True
    )
    await parse_all(db, blobs, gw=gw)
    # 모델은 내 경험 사진 하나만 읽었다
    assert len(model.completions) == 1
    req = model.completions[0]
    assert req.images[0].data == PNG2 and req.data_classes == frozenset({"private"})
    run = await run_of(db, owner, mine.parse_run_id)
    assert run.status == "complete" and run.text_layer["origin"] == "model"
    assert {b.origin for b in await blocks_of(db, owner, mine.parse_run_id)} == {"model_ocr:proxy"}
    [item] = [x for x in await sources.list_sources(db, owner) if x.id == mine.source_id]
    assert item.read_by == "model"
    for up in (proof, waiting):
        assert (await run_of(db, owner, up.parse_run_id)).status == "failed"
    async with db.workspace_session(owner.workspace_id) as s:
        purposes = list(await s.scalars(select(ModelCall.purpose)))
    assert purposes == ["source_ocr"]


async def test_model_ocr_needs_explicit_vision_route(db, blobs, people):
    owner = people["owner"]
    model = FakeModel()
    up = await put(db, blobs, owner, PNG2, name="화면.png", purpose="experience")
    await parse_all(db, blobs, gw=gateway(db, model, NO_VISION))
    assert model.completions == []
    run = await run_of(db, owner, up.parse_run_id)
    assert (run.status, run.error_code) == ("failed", "ocr_unavailable")
    caps = await ingest.capabilities(gateway(db, model, NO_VISION), owner)
    assert caps.model_ocr_classes == ()
    caps = await ingest.capabilities(gateway(db, model), owner)
    assert caps.model_ocr_classes == ("public", "private") and caps.model_ocr_route


# ---------------------------------------------------------------- 확인 대기


async def test_low_confidence_waits_in_inbox_until_human_confirms(db, blobs, people):
    owner, agent = people["owner"], people["agent"]
    sure = await put(db, blobs, owner, PNG, name="scan.png", text_layer=layer())
    unsure = await put(
        db,
        blobs,
        owner,
        b"OPIc IH 2024 (synthetic)",
        name="note.txt",
        mime="text/plain",
        purpose="proof",
        data_class="private",
        purpose_pending=True,
        purpose_hint="opic",
    )
    # 확인 대기는 restricted (증빙일 수 있다)
    assert (unsure.purpose_pending, unsure.data_class) == (True, "restricted")
    await parse_all(db, blobs)
    inbox = await sources.list_sources(db, owner, attention=True)
    assert [(x.id, x.attention, x.purpose_hint) for x in inbox] == [
        (unsure.source_id, "purpose_unconfirmed", "opic")
    ]
    assert sure.source_id not in {x.id for x in inbox}  # 확신한 것은 받은 자료함에 없다

    # 에이전트는 확인하지 못한다 (대기 그대로, 등급도 restricted 그대로)
    await sources.change_purpose(db, agent, unsure.source_id, "experience", data_class="private")
    async with db.workspace_session(owner.workspace_id) as s:
        src = await s.get(Source, unsure.source_id)
        assert (src.purpose_pending, src.data_class) == (True, "restricted")
        src.purpose = "proof"  # 시험용으로 되돌린다
    # 사람이 그대로 확인 → 풀린다
    got = await sources.change_purpose(db, owner, unsure.source_id, "proof")
    assert got.changed
    assert await sources.list_sources(db, owner, attention=True) == []
    async with db.workspace_session(owner.workspace_id) as s:
        src = await s.get(Source, unsure.source_id)
        assert (src.purpose_pending, src.data_class) == (False, "restricted")


async def test_confirm_as_experience_lowers_class_and_requests_extraction(db, blobs, people):
    owner = people["owner"]
    up = await put(
        db,
        blobs,
        owner,
        b"I wrote this memo (synthetic)",
        name="memo.txt",
        mime="text/plain",
        purpose="experience",
        purpose_pending=True,
    )
    await parse_all(db, blobs)
    got = await sources.change_purpose(
        db, owner, up.source_id, "experience", data_class="private", can_extract=True
    )
    assert (got.changed, got.data_class) == (True, "private")
    assert got.extraction_run_id is not None


async def test_classify_http_matches_company_in_this_workspace_only(db, blobs, people):
    owner, other = people["owner"], people["other"]
    from career_core.usecases import companies

    await companies.create_company(db, other, name="Zebra (합성)", homepage="https://zebra.example")
    await companies.create_company(
        db, owner, name="Acme (합성)", homepage="https://www.acme.example"
    )
    tokens = {"t-a": owner}
    async with client_for(db, blobs, tokens) as c:
        r = await c.post(
            "/core/ingest/classify",
            json={
                "items": [
                    {"key": "1", "url": "https://acme.example/about"},
                    {"key": "2", "url": "https://zebra.example/company"},
                    {"key": "3", "url": "https://www.wanted.co.kr/wd/1"},
                    {
                        "key": "4",
                        "filename": "scan.png",
                        "mime": "image/png",
                        "text_head": "수료증",
                    },
                ]
            },
            headers=auth("t-a"),
        )
        assert r.status_code == 200
        got = {x["key"]: x for x in r.json()}
        assert (got["1"]["kind"], got["1"]["company_name"]) == ("company", "Acme (합성)")
        assert got["2"]["company_id"] is None and got["2"]["company_hint"] == "zebra"
        assert (got["3"]["kind"], got["3"]["confidence"]) == ("posting", "high")
        assert (got["4"]["kind"], got["4"]["confidence"]) == ("proof", "high")
        r = await c.get("/core/ingest/capabilities", headers=auth("t-a"))
        assert r.json() == {"model_ocr_classes": [], "model_ocr_route": None}


# ---------------------------------------------------------------- 내보내기·migration


async def test_export_restore_keeps_text_layer_and_pending(db, blobs, people, tmp_path):
    owner = people["owner"]
    up = await put(db, blobs, owner, PNG, name="scan.png", text_layer=layer())
    await put(db, blobs, owner, PNG3, name="IMG.png", purpose_pending=True, purpose_hint="image")
    await parse_all(db, blobs)
    out = tmp_path / "a.tar.gz"
    await export_workspace(db, blobs, owner, out)
    target = LocalBlobStore(tmp_path / "restored")
    restored = await restore_workspace(db, target, out, new_ids=True)
    copy = ActorContext(restored.owners[0], restored.workspace_id, scopes_for_role(Role.OWNER))
    rows = {x.title: x for x in await sources.list_sources(db, copy)}
    assert rows["IMG.png"].purpose_pending and rows["IMG.png"].attention == "purpose_unconfirmed"
    scan = rows["scan.png"]
    assert scan.read_by == "device" and not scan.purpose_pending
    blocks = await blocks_of(db, copy, scan.parse_run_id)
    assert blocks[1].bbox == [40, 120, 480, 150]
    f = await cred_uc.read_original(db, target, copy, scan.id)
    assert f.data == PNG and scan.id != up.source_id


def test_c0039_migration(pg):
    mod = importlib.import_module("career_core.migrations.versions.c0039_ingest")
    assert (mod.revision, mod.down_revision) == ("c0039", "c0037")
    admin = admin_url()
    name = f"career_test_{secrets.token_hex(4)}"
    eng = create_engine(admin, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    owner = admin.set(database=name)
    ws, actor = uuid4(), uuid4()
    try:
        upgrade(owner, revision="c0036")
        e = create_engine(owner, poolclass=NullPool)
        with e.begin() as conn:
            conn.execute(text("INSERT INTO core.workspaces (id, name) VALUES (:w, 'm')"), {"w": ws})
            conn.execute(
                text(
                    "INSERT INTO core.sources (id, workspace_id, title, data_class, created_by) "
                    "VALUES (:i, :w, '옛 원본 (합성)', 'private', :a)"
                ),
                {"i": uuid4(), "w": ws, "a": actor},
            )
        upgrade(owner, revision="c0039")
        with e.begin() as conn:
            got = conn.execute(text("SELECT purpose_pending, purpose_hint FROM core.sources")).all()
        assert [tuple(r) for r in got] == [(False, None)]
        with e.begin() as conn, pytest.raises(Exception, match="ck_sources_pending_restricted"):
            conn.execute(text("UPDATE core.sources SET purpose_pending = true"))
        e.dispose()
    finally:
        with eng.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        eng.dispose()

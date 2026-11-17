"""O20: 증빙 서류함·자격·이수 기록·만료 일정 (c0034, plan §20).

- 증빙에서 뽑기는 로컬 규칙만: 주장 0건, 외부 모델·임베딩·OCR 호출 0건.
- 채운 칸마다 원문 위치가 있고 그 위치의 글이 값이다. 없는 값은 비운다. 만료일은 적혀 있을 때만.
- 주민등록번호는 표시만 하고 어떤 칸에도 들어가지 않는다.
- 번호는 끝 4자리만 (reveal은 쓰기 권한 + 감사 기록). 만료일이 오늘 일정 API에 뜬다.
- 원본 내려받기는 기록한 해시와 같은 바이트, 다른 공간은 404. 내보내기·복원 후 해시가 같다.
- MCP ``credential_list``에는 증빙 글이 없다.

합성 자료만 쓴다. DB는 career_core.testing의 임시 DB.
"""

import hashlib
import importlib
import json
import tarfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import PlainTextParser, default_local_parsers
from career_core.db import Claim, ExtractionRun, ModelCall
from career_core.db.models import AuditEvent, Credential
from career_core.domain.credentials import (
    CredentialRuleError,
    TextBlock,
    extract_credential,
    has_resident_number,
    mask_number,
)
from career_core.domain.study import local_date, zone
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
from career_core.ports import ACTOR_AGENT, ActorContext, ProviderParseStatus
from career_core.usecases import credentials as uc
from career_core.usecases import sources
from career_core.usecases.export import export_workspace, restore_workspace
from career_core.usecases.extraction import ExtractionDeps
from career_core.usecases.indexing import IndexDeps
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio

# ---------------------------------------------------------------- 합성 서류

LICENSE = """국가기술자격증 (합성)

자격종목: 정보처리기사
자격번호: 23201010999Z
합격일자: 2023.06.09
성명: 김가상 주민등록번호: 900101-1234567

위 사람은 국가기술자격법에 의하여 위 자격을 취득하였음을 증명합니다.

2023년 06월 16일
한국산업인력공단 이사장"""

TOEIC = """TOEIC Score Report (합성)

Registration No. 991234567
Test Date 2024.05.26
LISTENING 450 READING 400
TOTAL 850
성적 유효기간: 2024.05.26 ~ 2026.05.25

한국TOEIC위원회"""

# 유효기간이 적혀 있지 않다: 만료일을 계산하지 않는다 (보통 2년이어도)
OPIC = """OPIc 성적표 (합성)

시험일자: 2024년 3월 9일
등급: IH"""

COURSE = """수료증 (합성)

과정명: 클라우드 백엔드 부트캠프
교육기간: 2024.01.02 ~ 2024.06.28
교육시간: 960시간
위 사람은 위 과정을 수료하였으므로 이 증서를 수여합니다.
수료일 2024.06.28

가상교육원 원장"""

DEGREE = """졸업증명서 (합성)
학교명: 가상대학교
학위: 공학사
입학일: 2015.03.02
졸업일자: 2021.02.19

가상대학교 총장"""

EMPLOYMENT = """재직증명서 (합성)
회사명: (주)에이크미
재직기간: 2021.03.02 ~ 현재
주식회사 에이크미 대표이사 홍길동 (인)"""

AWARD = """상장 (합성)
대회명: 제3회 가상 해커톤
훈격: 최우수상
수상일 2023.11.30"""

# 달까지만 적힌 날짜·주민등록번호뿐인 번호 칸
VAGUE = """자격증 (합성)
자격명: 컴퓨터활용능력 1급
합격일: 2022.07
자격번호: 900101-1234567"""

RESUME = "이력서 (합성)\n\nAcme Corp 백엔드 개발자, 2021.03 - 2023.02. 주문 서비스를 만들었다."


def blocks_of(text: str) -> list[TextBlock]:
    return [
        TextBlock(i, b.text) for i, b in enumerate(PlainTextParser().parse(text.encode()).blocks)
    ]


def located(blocks: list[TextBlock], draft) -> dict:
    """칸마다 (값, 원문 위치의 글)."""
    out = {}
    for name, fv in draft.fields.items():
        b = blocks[fv.span.block]
        assert b.ordinal == fv.span.block
        out[name] = (fv.value, b.text[fv.span.start : fv.span.end])
    return out


# ---------------------------------------------------------------- 뽑기 규칙


@pytest.mark.parametrize(
    ("text", "kind", "want"),
    [
        (
            LICENSE,
            "license",
            {
                "name": "정보처리기사",
                "issuer": "한국산업인력공단",
                "number": "23201010999Z",
                "acquired_on": date(2023, 6, 9),
            },
        ),
        (
            TOEIC,
            "language",
            {
                "name": "TOEIC",
                "issuer": "한국TOEIC위원회",
                "number": "991234567",
                "acquired_on": date(2024, 5, 26),
                "expires_on": date(2026, 5, 25),
                "score": "850",
            },
        ),
        (OPIC, "language", {"name": "OPIc", "acquired_on": date(2024, 3, 9), "score": "IH"}),
        (
            COURSE,
            "course",
            {
                "name": "클라우드 백엔드 부트캠프",
                "issuer": "가상교육원",
                "acquired_on": date(2024, 6, 28),
                "started_on": date(2024, 1, 2),
                "ended_on": date(2024, 6, 28),
                "hours": 960,
            },
        ),
        (
            DEGREE,
            "degree",
            {
                "name": "공학사",
                "issuer": "가상대학교",
                "acquired_on": date(2021, 2, 19),
                "started_on": date(2015, 3, 2),
            },
        ),
        (
            EMPLOYMENT,
            "other",
            {"name": "재직증명서", "issuer": "(주)에이크미", "started_on": date(2021, 3, 2)},
        ),
        (
            AWARD,
            "award",
            {"name": "제3회 가상 해커톤", "score": "최우수상", "acquired_on": date(2023, 11, 30)},
        ),
        ("자격종목 | 정보처리기사 | 자격번호 | A-1234 | 합격일 | 2023-06-09", "license", None),
    ],
)
def test_extract_rules_fill_only_located_values(text, kind, want):
    blocks = blocks_of(text)
    draft = extract_credential(blocks)
    assert draft is not None and draft.kind == kind
    got = located(blocks, draft)
    for value, quoted in got.values():
        # 원문 위치의 글이 곧 값이다 (날짜·시간은 그 글을 읽은 값)
        if isinstance(value, str):
            assert quoted == value
        elif isinstance(value, int):
            assert quoted == str(value)
        else:
            assert value.year == int(quoted[:4])
    if want is not None:
        assert {k: v for k, (v, _) in got.items()} == want
    # 상세는 뽑지 않는다
    assert "detail" not in got


def test_no_guessed_expiry_vague_dates_and_no_resident_number():
    # 어학 성적인데 유효기간이 적혀 있지 않으면 만료일은 비운다
    assert extract_credential(blocks_of(OPIC)).value("expires_on") is None
    # 달까지만 적힌 날은 비우고, 주민등록번호 모양은 번호 칸에 넣지 않는다
    blocks = blocks_of(VAGUE)
    draft = extract_credential(blocks)
    assert draft.value("name") == "컴퓨터활용능력 1급"
    assert draft.value("acquired_on") is None and draft.value("number") is None
    assert has_resident_number(blocks)
    # 주민등록번호가 있는 자격증: 표시는 하고, 어느 칸에도 그 값이 없다
    blocks = blocks_of(LICENSE)
    assert has_resident_number(blocks)
    values = [str(fv.value) for fv in extract_credential(blocks).fields.values()]
    assert not any("1234567" in v or "900101" in v for v in values)
    assert not has_resident_number(blocks_of(TOEIC))
    # 서류가 아니면 후보가 없다
    assert extract_credential(blocks_of(RESUME)) is None
    assert extract_credential([]) is None


def test_mask_number():
    assert mask_number("23201010999Z") == "••••999Z"
    assert mask_number("1234") == "••••"
    assert mask_number(None) is None


# ---------------------------------------------------------------- 대역


class FakeModel:
    """외부 모델·임베딩 대역. 받은 호출을 센다."""

    def __init__(self):
        self.completions: list = []
        self.embedded: list[str] = []

    async def complete(self, model, request):
        self.completions.append(request)
        return ModelCompletion(
            text="{}",
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, TokenUsage(input_tokens=1, output_tokens=1)),),
            request_id="req",
        )

    async def embed(self, model, texts, *, input_type, dimensions):
        self.embedded.extend(texts)
        return EmbeddingCompletion(
            vectors=tuple((1.0,) + (0.0,) * 7 for _ in texts),
            model=model,
            parts=(UsagePart(model, TokenUsage(input_tokens=len(texts))),),
        )


class FakeOcr:
    """외부 OCR 대역. 설정에 restricted를 적어도 제출되면 안 된다."""

    name = "fakeocr"

    def __init__(self):
        self.submitted: list = []

    async def submit(self, data, mime_type, pages, idempotency_key):
        self.submitted.append(idempotency_key)
        return "job-1"

    async def poll(self, provider_job_id):
        return ProviderParseStatus(state="failed")


CONFIG = parse_models_config("""
[endpoints.openai]
preset = "openai"
data_classes = ["public", "private", "restricted"]

[routes]
default = "openai/gpt-6.1-sol"
embed = "openai/text-embedding-3-small"

[embedding]
dimensions = 8
""")


def gateway(db, client):
    return ModelGateway(
        db,
        CONFIG,
        PriceBook.load(),
        client_factory=lambda ep, key: client,
        env={"OPENAI_API_KEY": "k"},
    )


@pytest.fixture
async def people(db):
    ws_a, a = await create_workspace(db, name="A (합성)", owner_display_name="amy")
    ws_b, b = await create_workspace(db, name="B (합성)", owner_display_name="ben")
    return {
        "owner": ActorContext(a, ws_a, scopes_for_role(Role.OWNER)),
        "editor": ActorContext(a, ws_a, scopes_for_role(Role.EDITOR)),
        "viewer": ActorContext(a, ws_a, scopes_for_role(Role.VIEWER)),
        "agent": ActorContext(a, ws_a, scopes_for_role(Role.OWNER), kind=ACTOR_AGENT),
        "other": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
    }


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blobs")


async def put(db, blobs, actor, data, *, name, purpose="proof", mime="text/plain"):
    return await sources.upload_source(
        db,
        blobs,
        actor,
        filename=name,
        data=data.encode() if isinstance(data, str) else data,
        declared_mime=mime,
        max_bytes=1 << 20,
        purpose=purpose,
    )


async def parse_all(db, blobs, ocr=None):
    deps = ParsingDeps(
        db=db,
        blobs=blobs,
        egress=ProviderEgressPolicy({"fakeocr": ["public", "private", "restricted"]}),
        local_parsers=default_local_parsers(),
        provider=ocr,
    )
    await run_until_idle(db, parsing_handlers(deps))


async def count(db, actor, model, *where):
    async with db.workspace_session(actor.workspace_id) as s:
        return await s.scalar(select(func.count()).select_from(model).where(*where))


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def client_for(db, blobs, people):
    app = FastAPI()
    app.include_router(build_router(CoreDeps(identity=Tokens(people), db=db, blobs=blobs)))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t")


# ---------------------------------------------------------------- 자동 뽑기·외부 호출 0


async def test_proof_parse_extracts_candidate_locally_with_zero_external_calls(db, blobs, people):
    owner = people["owner"]
    model, ocr = FakeModel(), FakeOcr()
    gw = gateway(db, model)
    lic = await put(db, blobs, owner, LICENSE, name="정보처리기사 자격증.txt")
    toeic = await put(db, blobs, owner, TOEIC, name="토익 성적표.txt")
    photo = await put(
        db,
        blobs,
        owner,
        b"\x89PNG\r\n\x1a\n synthetic scan (proof)",
        name="자격증.png",
        mime="image/png",
    )
    await put(db, blobs, owner, RESUME, name="cv.txt", purpose="experience")
    await parse_all(db, blobs, ocr)
    await run_until_idle(db, extraction_handlers(ExtractionDeps(db=db, gateway=gw)))
    await run_until_idle(db, index_handlers(IndexDeps(db, gw)))

    # 외부 OCR·모델 호출 0 (증빙 사진도 OCR로 보내지 않는다), 증빙 글은 임베딩에도 없다
    assert ocr.submitted == []
    assert model.completions == []
    assert not any("정보처리기사" in t or "TOEIC" in t for t in model.embedded)
    assert await count(db, owner, ModelCall, ModelCall.purpose != "embed") == 0
    # 주장 0, 경험 추출 실행 0
    assert await count(db, owner, Claim) == 0
    assert await count(db, owner, ExtractionRun) == 0

    rows = await uc.list_credentials(db, owner, reveal=True)
    by_source = {r.source_id: r for r in rows}
    assert set(by_source) == {lic.source_id, toeic.source_id}  # 사진은 읽은 글이 없어 후보 없음
    c = by_source[lic.source_id]
    assert (c.kind, c.name, c.status, c.number) == (
        "license",
        "정보처리기사",
        "extracted",
        "23201010999Z",
    )
    assert set(c.field_spans) == {"name", "issuer", "number", "acquired_on"}
    t = by_source[toeic.source_id]
    assert (t.expires_on, t.score) == (date(2026, 5, 25), "850")
    # 원문 위치는 그 파싱 실행의 블록을 가리킨다
    async with db.workspace_session(owner.workspace_id) as s:
        from career_core.db import DocumentBlock

        span = c.field_spans["number"]
        text = await s.scalar(
            select(DocumentBlock.text).where(
                DocumentBlock.parse_run_id == c.parse_run_id,
                DocumentBlock.ordinal == span["block"],
            )
        )
    assert text[span["start"] : span["end"]] == "23201010999Z"

    # 사진: 읽은 글이 없으면 뽑기는 없음을 알리고 사람이 칸을 채운다
    res = await uc.extract_from_source(db, owner, photo.source_id)
    assert (res.credential_id, res.reason) == (None, "not_parsed")
    # 다시 뽑으면 옛 후보를 바꾼다 (하나만 남는다)
    res = await uc.extract_from_source(db, owner, lic.source_id)
    assert res.replaced == 1 and res.credential_id is not None
    assert await count(db, owner, Credential, Credential.source_id == lic.source_id) == 1
    assert ocr.submitted == [] and model.completions == []


# ---------------------------------------------------------------- HTTP


async def test_http_crud_confirm_mask_reveal_and_errors(db, blobs, people):
    owner = people["owner"]
    lic = await put(db, blobs, owner, LICENSE, name="자격증.txt")
    cv = await put(db, blobs, owner, RESUME, name="cv.txt", purpose="experience")
    await parse_all(db, blobs)
    async with client_for(db, blobs, people) as c:
        r = await c.get("/core/credentials", headers=auth("viewer"))
        assert r.status_code == 200, r.text
        [cand] = r.json()
        assert (cand["status"], cand["number"], cand["number_masked"]) == (
            "extracted",
            "••••999Z",
            True,
        )
        assert "23201010999Z" not in r.text
        assert cand["field_spans"]["number"]["block"] >= 0
        # reveal은 쓰기 권한이 있어야 한다
        r = await c.get("/core/credentials", params={"reveal": "true"}, headers=auth("viewer"))
        assert (r.status_code, r.json()) == (403, {"detail": "forbidden"})
        r = await c.get(
            f"/core/credentials/{cand['id']}", params={"reveal": "true"}, headers=auth("editor")
        )
        assert r.json()["number"] == "23201010999Z" and r.json()["number_masked"] is False
        assert (
            await count(db, owner, AuditEvent, AuditEvent.event == "credential_number_reveal") == 1
        )

        # 증빙 목록: 주민등록번호 표시, 이어진 후보
        r = await c.get("/core/proofs", headers=auth("viewer"))
        [proof] = r.json()
        assert (proof["id"], proof["resident_number"], proof["candidates"]) == (
            str(lic.source_id),
            True,
            1,
        )
        assert "900101" not in r.text

        # 확인: 사람만, 증빙이 이어져 있으면 evidence_supported
        with pytest.raises(CredentialRuleError, match="human_confirmation_required"):
            await uc.confirm_credential(db, people["agent"], cand["id"])
        r = await c.post(f"/core/credentials/{cand['id']}/confirm", headers=auth("viewer"))
        assert r.status_code == 403
        r = await c.post(f"/core/credentials/{cand['id']}/confirm", headers=auth("editor"))
        assert r.json()["status"] == "evidence_supported"

        # 고친 칸은 원문 위치를 잃는다 (사람이 적은 값)
        r = await c.patch(
            f"/core/credentials/{cand['id']}",
            json={"name": "정보처리기사 (합성)", "expires_on": "2030-01-01"},
            headers=auth("editor"),
        )
        assert r.status_code == 200, r.text
        got = (await c.get(f"/core/credentials/{cand['id']}", headers=auth("viewer"))).json()
        assert "name" not in got["field_spans"] and "number" in got["field_spans"]
        assert got["name"] == "정보처리기사 (합성)" and got["expires_on"] == "2030-01-01"
        # 증빙을 떼면 self_confirmed
        r = await c.patch(
            f"/core/credentials/{cand['id']}", json={"source_id": None}, headers=auth("editor")
        )
        got = (await c.get(f"/core/credentials/{cand['id']}", headers=auth("viewer"))).json()
        assert (got["status"], got["source_id"]) == ("self_confirmed", None)

        # 직접 넣기: 바로 확인된 기록. 규칙 위반은 422
        r = await c.post(
            "/core/credentials",
            json={"kind": "language", "name": "TOEIC Speaking", "score": "Level 7"},
            headers=auth("editor"),
        )
        assert r.status_code == 201, r.text
        manual = r.json()["id"]
        r = await c.get("/core/credentials", params={"kind": "language"}, headers=auth("viewer"))
        assert [(x["id"], x["status"]) for x in r.json()] == [(manual, "self_confirmed")]
        bad = [
            ({"kind": "x", "name": "a"}, 422, None),
            ({"kind": "license", "name": ""}, 422, None),
            (
                {
                    "kind": "license",
                    "name": "a",
                    "acquired_on": "2024-01-02",
                    "expires_on": "2023-01-01",
                },
                422,
                "expires_before_acquired",
            ),
            (
                {"kind": "license", "name": "a", "source_id": str(cv.source_id)},
                422,
                "source_not_proof",
            ),
            ({"kind": "license", "name": "a", "hours": 0}, 422, None),
        ]
        for body, code, detail in bad:
            r = await c.post("/core/credentials", json=body, headers=auth("editor"))
            assert r.status_code == code, (body, r.text)
            if detail:
                assert r.json() == {"detail": detail}
        r = await c.post(
            "/core/credentials", json={"kind": "license", "name": "a"}, headers=auth("viewer")
        )
        assert r.status_code == 403
        r = await c.patch(
            f"/core/credentials/{manual}", json={"name": None}, headers=auth("editor")
        )
        assert r.json() == {"detail": "credential_name_required"}

        # 뽑기: 증빙이 아니면 409, 없는 원본 404
        r = await c.post(
            f"/core/sources/{cv.source_id}/extract-credentials", headers=auth("editor")
        )
        assert (r.status_code, r.json()) == (409, {"detail": "source_not_proof"})
        r = await c.post(
            f"/core/sources/{lic.source_id}/extract-credentials", headers=auth("editor")
        )
        # 확인한 같은 기록이 있으면 후보를 다시 만들지 않는다
        assert r.status_code == 200 and r.json()["reason"] in ("already_recorded", None)

        # 다른 공간: 0건, 404
        assert (await c.get("/core/credentials", headers=auth("other"))).json() == []
        assert (await c.get("/core/proofs", headers=auth("other"))).json() == []
        for method, path in (
            ("GET", f"/core/credentials/{manual}"),
            ("PATCH", f"/core/credentials/{manual}"),
            ("POST", f"/core/credentials/{manual}/confirm"),
            ("DELETE", f"/core/credentials/{manual}"),
            ("POST", f"/core/sources/{lic.source_id}/extract-credentials"),
        ):
            r = await c.request(
                method, path, json={} if method == "PATCH" else None, headers=auth("other")
            )
            assert r.status_code == 404, (method, path, r.text)
        # 다른 공간의 원본은 이을 수 없다
        other_up = await put(db, blobs, people["other"], TOEIC, name="b.txt")
        r = await c.post(
            "/core/credentials",
            json={"kind": "language", "name": "x", "source_id": str(other_up.source_id)},
            headers=auth("editor"),
        )
        assert r.json() == {"detail": "source_not_found"}

        r = await c.delete(f"/core/credentials/{manual}", headers=auth("editor"))
        assert r.status_code == 204
        assert (
            await c.get(f"/core/credentials/{manual}", headers=auth("viewer"))
        ).status_code == 404


async def test_expiry_shows_in_agenda_window(db, blobs, people):
    owner = people["owner"]
    today = local_date(datetime.now(UTC), zone("Asia/Seoul"))
    for name, expires in (
        ("TOEIC (합성)", today + timedelta(days=10)),
        ("OPIc (합성)", today + timedelta(days=200)),
        ("JLPT (합성)", today - timedelta(days=1)),
        ("TEPS (합성)", None),
    ):
        await uc.create_credential(
            db, owner, {"kind": "language", "name": name, "expires_on": expires}
        )
    async with client_for(db, blobs, people) as c:
        r = await c.get("/core/credentials", params={"expiring_within": 62}, headers=auth("viewer"))
        assert [(x["name"], x["expiry"], x["days_left"]) for x in r.json()] == [
            ("TOEIC (합성)", "soon", 10)
        ]
        r = await c.get("/core/credentials", headers=auth("viewer"))
        marks = {x["name"]: x["expiry"] for x in r.json()}
        assert marks == {
            "TOEIC (합성)": "soon",
            "OPIc (합성)": None,
            "JLPT (합성)": "expired",
            "TEPS (합성)": None,
        }
        r = await c.get("/core/credentials", params={"tz": "Nowhere/City"}, headers=auth("viewer"))
        assert r.json() == {"detail": "invalid_timezone"}


# ---------------------------------------------------------------- 원본 내려받기


async def test_original_download_hash_and_isolation(db, blobs, people):
    owner = people["owner"]
    data = "증빙 원본 바이트 (합성)\n\n수료증\n과정명: 가상 과정".encode()
    up = await put(db, blobs, owner, data, name="수료증 사본.txt")
    async with client_for(db, blobs, people) as c:
        r = await c.get(f"/core/sources/{up.source_id}/original", headers=auth("viewer"))
        assert r.status_code == 200
        assert (
            r.content == data
            and hashlib.sha256(r.content).hexdigest() == r.headers["x-content-sha256"]
        )
        assert r.headers["content-type"].startswith("text/plain")
        assert "attachment" in r.headers["content-disposition"]
        assert "%EC%88%98%EB%A3%8C%EC%A6%9D" in r.headers["content-disposition"]  # 수료증
        assert r.headers["cache-control"] == "no-store"
        # 제한 등급 원본 내려받기는 감사 기록에 남는다
        assert (
            await count(db, owner, AuditEvent, AuditEvent.event == "source_original_download") == 1
        )
        # 다른 공간은 404, 토큰 없으면 401
        r = await c.get(f"/core/sources/{up.source_id}/original", headers=auth("other"))
        assert r.status_code == 404
        r = await c.get(f"/core/sources/{up.source_id}/original")
        assert r.status_code == 401
        # 저장소 바이트가 바뀌면 보내지 않는다
        async with db.workspace_session(owner.workspace_id) as s:
            from career_core.db import SourceVersion

            key = await s.scalar(
                select(SourceVersion.blob_key).where(SourceVersion.source_id == up.source_id)
            )
        path = next(Path(blobs.root).rglob(key))
        path.write_bytes(b"tampered")
        r = await c.get(f"/core/sources/{up.source_id}/original", headers=auth("viewer"))
        assert (r.status_code, r.json()) == (409, {"detail": "original_changed"})


# ---------------------------------------------------------------- 내보내기·복원


async def test_export_marks_proofs_and_restores_credentials(db, blobs, people, tmp_path):
    owner = people["owner"]
    up = await put(db, blobs, owner, TOEIC, name="토익.txt")
    await put(db, blobs, owner, RESUME, name="cv.txt", purpose="experience")
    await parse_all(db, blobs)
    [cand] = await uc.list_credentials(db, owner)
    await uc.confirm_credential(db, owner, cand.id)
    want = hashlib.sha256(TOEIC.encode()).hexdigest()

    out = tmp_path / "a.tar.gz"
    report = await export_workspace(db, blobs, owner, out)
    proofs = report.manifest["proofs"]
    assert proofs == {"sources": [str(up.source_id)], "blobs": [want]}
    marks = {b["key"]: b.get("purpose") for b in report.manifest["blobs"]}
    assert marks[want] == "proof" and list(marks.values()).count("proof") == 1
    with tarfile.open(out) as tar:
        names = tar.getnames()
    assert "tables/core.credentials.jsonl" in names

    target = LocalBlobStore(tmp_path / "restored")
    restored = await restore_workspace(db, target, out, new_ids=True)
    copy = ActorContext(restored.owners[0], restored.workspace_id, scopes_for_role(Role.OWNER))
    [row] = await uc.list_credentials(db, copy, reveal=True)
    assert (row.name, row.number, row.expires_on, row.status) == (
        "TOEIC",
        "991234567",
        date(2026, 5, 25),
        "evidence_supported",
    )
    assert row.id != cand.id and row.source_id != up.source_id
    assert set(row.field_spans) == set(cand.field_spans)
    f = await uc.read_original(db, target, copy, row.source_id)
    assert f.sha256 == want and hashlib.sha256(f.data).hexdigest() == want


# ---------------------------------------------------------------- MCP


async def test_mcp_credential_list_has_no_proof_text(db, blobs, people):
    owner = people["owner"]
    await put(db, blobs, owner, LICENSE, name="자격증.txt")
    await parse_all(db, blobs)
    server = McpServer(CoreDeps(identity=Tokens({}), db=db, blobs=blobs))
    out = await server.call_tool("credential_list", {}, owner)
    assert out["structuredContent"]["credentials"] == []  # 확인 전 후보는 기본으로 안 보인다
    out = await server.call_tool("credential_list", {"include_candidates": True}, owner)
    [item] = out["structuredContent"]["credentials"]
    assert (item["name"], item["number"], item["status"]) == (
        "정보처리기사",
        "••••999Z",
        "extracted",
    )
    dumped = json.dumps(out, ensure_ascii=False)
    for secret in (
        "23201010999Z",
        "900101",
        "김가상",
        "국가기술자격법",
        "field_spans",
        "parse_run_id",
    ):
        assert secret not in dumped
    out = await server.call_tool("credential_list", {"workspace_id": "x"}, owner)
    assert out["isError"]
    # 다른 공간의 토큰으로는 0건
    out = await server.call_tool("credential_list", {"include_candidates": True}, people["other"])
    assert out["structuredContent"]["credentials"] == []
    # 읽기 권한이 없으면 거부
    nobody = ActorContext(owner.subject_id, owner.workspace_id, frozenset())
    out = await server.call_tool("credential_list", {}, nobody)
    assert out["isError"]


# ---------------------------------------------------------------- migration


def test_c0034_revision():
    mod = importlib.import_module("career_core.migrations.versions.c0034_credentials")
    assert (mod.revision, mod.down_revision) == ("c0034", "c0033")

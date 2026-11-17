"""O14 참고 자료 보관함: 다른 사람의 자기소개서·이력서·면접 후기·직무 글 (plan §19.2).

- 참고 원본에서 경험 추출 0건, 참고 글을 문서 근거로 이으면 거부, 근거 없는 문장 검사에서 근거로 치지 않는다.
- 기본 검색에 참고 0건 ("참고 포함"을 켜면 "참고"로 표시).
- 문서 검사: 참고 글을 거의 그대로 옮긴 문장에 "참고 글과 비슷함", 상관없는 문장에는 없음.
- 개인정보: 저장 전에 찾아 보여 주고, 고르면 ○○○로 가려 저장한다.
- 구조 메모: 모델 후보는 사람이 확인한다 (AI 클라이언트는 확인 못 함). 원문 문장을 옮긴 후보 줄은 버린다.
- 다른 공간 0건, 내보내기·복원, MCP 출력(메모와 짧은 발췌만).

글은 모두 합성이다 (사람·학교·연락처 모두 지어낸 것). 네트워크는 쓰지 않는다.
"""

import importlib
import json
import tarfile

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import Claim, ModelCall, Source
from career_core.db.models import DocumentClaim, ReferenceItem, ReferenceNote
from career_core.domain.brain import ClaimRuleError
from career_core.domain.documents import (
    BlockInput,
    CheckContext,
    DocumentRuleError,
    check_document,
)
from career_core.domain.references import (
    REFERENCE_KINDS,
    ReferenceCorpus,
    ReferenceRuleError,
    mask_pii,
    pii_hits,
    similarity,
)
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import parsing_handlers, run_until_idle
from career_core.llm import ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ACTOR_AGENT, ActorContext
from career_core.usecases import applications as app_uc
from career_core.usecases import brain, search, sources
from career_core.usecases import companies as company_uc
from career_core.usecases import documents as docs_uc
from career_core.usecases import references as uc
from career_core.usecases.export import export_workspace, restore_workspace
from career_core.usecases.extraction import request_extraction
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import func, select, text

pytestmark = pytest.mark.anyio

# 합성 합격 자기소개서 (지어낸 사람·학교·연락처)
ESSAY = (
    "[문항 1] 지원 동기와 입사 후 포부를 적어 주십시오.\n\n"
    "이름: 가나다\n"
    "연락처: 010-1234-5678 / ganada.synthetic@example.test\n\n"
    "저는 가상대학교 산업공학과에서 물류 동아리 회장을 맡아 신입 회원 이탈률을 절반으로 줄였습니다. "
    "매주 회원 면담을 열어 불편한 점을 듣고 활동 일정을 다시 짰습니다.\n\n"
    "입사 후에는 물류 현장의 작은 낭비를 데이터로 찾아내는 엔지니어가 되겠습니다."
)
NEAR_COPY = "저는 대학 시절 물류 동아리 회장을 맡아서 신입 회원 이탈률을 절반으로 줄였습니다."
UNRELATED = "결제 배치를 Kotlin으로 옮기며 처리 시간을 40분에서 12분으로 줄였습니다."
REVIEW = "면접 후기 (합성)\n\n1차 면접에서는 협업 경험과 갈등을 해결한 방법을 자세히 물었습니다."

CONFIG = parse_models_config("""
[endpoints.proxy]
preset = "cliproxyapi"
price_as = "openai"
data_classes = ["public", "private"]
[routes]
default = "proxy/gpt-6.1-sol"
""")

NOTES_REPLY = json.dumps(
    {
        "notes": [
            {
                "question": "[문항 1] 지원 동기와 입사 후 포부를 적어 주십시오.",
                "structure": [
                    "경험으로 시작하는 두괄식",
                    "본인 행동",
                    "수치 결과",
                    # 원문 문장을 그대로 옮긴 줄: 버린다
                    "매주 회원 면담을 열어 불편한 점을 듣고 활동 일정을 다시 짰습니다",
                    "입사 후 포부로 마무리",
                ],
                "materials": ["동아리 운영 경험"],
                "competencies": ["문제 해결", "고객 지향"],
            },
            {"structure": "not a list"},
        ]
    },
    ensure_ascii=False,
)


class ScriptedLLM:
    def __init__(self, reply):
        self.reply = reply
        self.requests = []

    async def complete(self, model, request):
        self.requests.append(request)
        return ModelCompletion(
            text=self.reply,
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, TokenUsage(input_tokens=200, output_tokens=80)),),
        )


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def env(db, tmp_path):
    ws_a, a = await create_workspace(db, name="A (합성)", owner_display_name="amy")
    ws_b, b = await create_workspace(db, name="B (합성)", owner_display_name="ben")
    llm = ScriptedLLM(NOTES_REPLY)
    gw = ModelGateway(
        db,
        CONFIG,
        PriceBook.load(),
        client_factory=lambda ep, key: llm,
        env={"CLIPROXY_API_KEY": "k"},
    )
    return {
        "owner": ActorContext(a, ws_a, scopes_for_role(Role.OWNER)),
        "viewer": ActorContext(a, ws_a, scopes_for_role(Role.VIEWER)),
        "agent": ActorContext(a, ws_a, scopes_for_role(Role.OWNER), kind=ACTOR_AGENT),
        "other": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
        "gateway": gw,
        "llm": llm,
        "blobs": LocalBlobStore(tmp_path / "blobs"),
    }


async def parse_all(db, blobs):
    deps = ParsingDeps(
        db=db, blobs=blobs, egress=ProviderEgressPolicy(), local_parsers=default_local_parsers()
    )
    await run_until_idle(db, parsing_handlers(deps))


async def count(db, actor, model, *where):
    async with db.workspace_session(actor.workspace_id) as s:
        return await s.scalar(select(func.count()).select_from(model).where(*where))


def client_for(db, env, gw=None):
    tokens = {k: env[k] for k in ("owner", "viewer", "agent", "other")}
    app = FastAPI()
    app.include_router(
        build_router(CoreDeps(identity=Tokens(tokens), db=db, blobs=env["blobs"], models=gw))
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t")


# ---------------------------------------------------------------- 규칙 (DB 없이)


def test_pii_hits_and_masking():
    hits = pii_hits(ESSAY)
    assert [h.kind for h in hits] == ["name", "phone_number", "email_address", "school"]
    assert [h.value for h in hits][:2] == ["가나다", "010-1234-5678"]
    masked, n = mask_pii(ESSAY)
    assert n == 4
    for gone in ("가나다", "010-1234-5678", "ganada.synthetic", "가상대학교"):
        assert gone not in masked
    assert "이름: ○○○" in masked and "○○○대학교 산업공학과" in masked
    # 가린 글을 다시 보면 더 찾을 것이 없다
    assert pii_hits(masked) == []
    # 주민등록번호, 영문 이름, 학교 아닌 낱말
    other = "성명: Jane Doe\n번호 900101-1234567\n우리대학교 소개 글입니다."
    assert [h.kind for h in pii_hits(other)] == ["name", "resident_registration_number"]


def test_similarity_is_deterministic_and_ignores_unrelated_text():
    ref_sentence = "저는 가상대학교 산업공학과에서 물류 동아리 회장을 맡아 신입 회원 이탈률을 절반으로 줄였습니다."
    assert similarity(NEAR_COPY, ref_sentence) >= 0.6
    assert similarity(UNRELATED, ref_sentence) < 0.3
    # 짧은 문장은 보지 않는다
    assert similarity("감사합니다.", "감사합니다.") == 0.0
    corpus = ReferenceCorpus([("r1", None, "합격 자소서", ESSAY)])
    hit = corpus.best(NEAR_COPY)
    assert hit is not None and hit.reference_id == "r1" and hit.score >= 0.6
    assert corpus.best(UNRELATED) is None
    assert corpus.best(NEAR_COPY) == hit  # 같은 입력, 같은 결과

    # 문서 검사: 비슷한 문장만 짚고, 참고 문장 자체는 결과에 싣지 않는다
    result = check_document(
        CheckContext(references=corpus),
        [BlockInput("b1", NEAR_COPY), BlockInput("b2", UNRELATED)],
    )
    similar = [f for f in result["findings"] if f["code"] == "similar_to_reference"]
    assert [(f["block_id"], f["detail"]["reference_id"]) for f in similar] == [("b1", "r1")]
    assert "가상대학교" not in json.dumps(result, ensure_ascii=False)
    # 참고 글은 근거가 아니다: 경험 문장 두 개 모두 근거 없음
    assert result["summary"]["unsupported_statement"] == 2
    assert (
        check_document(CheckContext(), [BlockInput("b1", NEAR_COPY)])["summary"].get(
            "similar_to_reference"
        )
        is None
    )


def test_kinds_match_migration():
    mod = importlib.import_module("career_core.migrations.versions.c0035_references")
    assert mod.KINDS == REFERENCE_KINDS
    assert (mod.revision, mod.down_revision) == ("c0035", "c0033")


# ---------------------------------------------------------------- 두 칸 나누기


async def test_reference_never_becomes_claims_or_evidence(db, env):
    owner, blobs = env["owner"], env["blobs"]
    rid = await uc.create_reference(
        db, blobs, owner, kind="accepted_essay", title="합격 자소서 (합성)", text=ESSAY, mask=True
    )
    view = await uc.get_reference(db, owner, rid)
    assert view.pii_masked == 4 and "010-1234-5678" not in view.text
    assert view.source_id is not None and view.data_class == "private"
    # 붙여넣은 글은 가린 글로 참고 원본이 된다
    async with db.workspace_session(owner.workspace_id) as s:
        src = await s.get(Source, view.source_id)
    assert src.purpose == "reference"
    await parse_all(db, blobs)
    assert len(await sources.list_sources(db, owner, purpose="reference")) == 1
    async with db.workspace_session(owner.workspace_id) as s:
        run_id = await s.scalar(
            text(
                "SELECT r.id FROM core.parse_runs r JOIN core.source_versions v "
                "ON v.id = r.source_version_id WHERE v.source_id = :s"
            ),
            {"s": view.source_id},
        )
        block_id, block_text = (
            await s.execute(
                text(
                    "SELECT id, text FROM core.document_blocks WHERE parse_run_id = :r "
                    "ORDER BY ordinal LIMIT 1"
                ),
                {"r": run_id},
            )
        ).one()
    assert "010-1234-5678" not in block_text

    # 경험 추출 0건: 요청부터 거부
    with pytest.raises(ClaimRuleError, match="source_not_experience"):
        await request_extraction(db, owner, run_id)
    # 근거 위치도 만들 수 없다
    with pytest.raises(ClaimRuleError, match="source_not_experience"):
        await brain.create_span(db, owner, block_id, 0, 3)
    assert await count(db, owner, Claim) == 0

    # 문서 근거로 참고 글·메모를 이으면 거부 (근거는 주장만 가리킨다)
    nid = await uc.add_note(db, owner, rid, structure=["두괄식"], competencies=["협업"])
    doc = await docs_uc.create_shared_document(db, owner, kind="cover_letter", title="내 자소서")
    for bad in (rid, nid):
        with pytest.raises(DocumentRuleError, match="unknown_claim"):
            await app_uc.save_revision(
                db,
                owner,
                None,
                doc,
                base_revision=0,
                operations=[{"op": "insert", "text": NEAR_COPY, "claim_ids": [str(bad)]}],
            )
    await app_uc.save_revision(
        db,
        owner,
        None,
        doc,
        base_revision=0,
        operations=[
            {"op": "insert", "text": NEAR_COPY},
            {"op": "insert", "text": UNRELATED},
        ],
    )
    assert await count(db, owner, DocumentClaim) == 0

    # 문서 검사: 옮긴 문장에만 "참고 글과 비슷함", 근거로는 치지 않는다
    checked = await app_uc.validate_document(db, owner, None, doc)
    similar = [f for f in checked["findings"] if f["code"] == "similar_to_reference"]
    assert len(similar) == 1 and similar[0]["sentence"] == NEAR_COPY
    assert similar[0]["detail"]["reference_id"] == str(rid)
    assert similar[0]["detail"]["reference_title"] == "합격 자소서 (합성)"
    assert checked["summary"]["unsupported_statement"] == 2

    # 기본 검색: 참고 0건. 참고 포함이면 "참고"로
    res = await search.search(db, owner, "물류 동아리 회장")
    assert res.hits == []
    res = await search.search(db, owner, "물류 동아리 회장", include_reference=True)
    assert res.hits and {h.record for h in res.hits} == {"reference"}

    # 참고 글이 쓰는 원본은 경험 자료로 바꾸지 않는다 (남의 글이 추출로 흘러가지 않게)
    with pytest.raises(ClaimRuleError, match="source_has_reference_items"):
        await sources.change_purpose(db, owner, view.source_id, "experience")
    await uc.delete_reference(db, owner, rid)
    assert await count(db, owner, ReferenceNote) == 0
    # 참고 글을 지워도 원본은 남고, 그 글은 여전히 "참고 글과 비슷함"에 쓰인다
    checked = await app_uc.validate_document(db, owner, None, doc)
    [again] = [f for f in checked["findings"] if f["code"] == "similar_to_reference"]
    assert again["detail"]["reference_id"] is None
    assert again["detail"]["source_id"] == str(view.source_id)


async def test_reference_from_uploaded_source(db, env):
    owner, blobs = env["owner"], env["blobs"]
    mine = await sources.upload_source(
        db,
        blobs,
        owner,
        filename="cv.txt",
        data="Acme Corp 백엔드 개발자 (합성)\n\n주문 서비스를 만들었습니다.".encode(),
        declared_mime="text/plain",
        max_bytes=1 << 20,
    )
    ref = await sources.upload_source(
        db,
        blobs,
        owner,
        filename="면접 후기.txt",
        data=REVIEW.encode(),
        declared_mime="text/plain",
        max_bytes=1 << 20,
        purpose="reference",
        data_class="public",
    )
    # 읽기 전에는 만들 수 없다
    with pytest.raises(ReferenceRuleError, match="source_not_ready"):
        await uc.create_reference(
            db, blobs, owner, kind="interview_review", title="후기", source_id=ref.source_id
        )
    await parse_all(db, blobs)
    # 내 경험 자료에서는 만들 수 없다
    with pytest.raises(ReferenceRuleError, match="source_not_reference"):
        await uc.create_reference(
            db, blobs, owner, kind="essay", title="x", source_id=mine.source_id
        )
    with pytest.raises(ReferenceRuleError, match="text_or_source_required"):
        await uc.create_reference(db, blobs, owner, kind="essay", title="x")
    report = await uc.check_pii(db, owner, source_id=ref.source_id)
    assert report.total == 0
    cid = await company_uc.create_company(db, owner, name="가상물류")
    rid = await uc.create_reference(
        db,
        blobs,
        owner,
        kind="interview_review",
        title="가상물류 1차 면접 후기",
        source_id=ref.source_id,
        company_id=cid,
        role="물류 엔지니어",
        year=2025,
        source_note="취업 카페 (합성)",
    )
    view = await uc.get_reference(db, owner, rid)
    assert view.source_id == ref.source_id and view.data_class == "public"  # 원본 등급
    assert "협업 경험" in view.text and view.company_name == "가상물류"
    # 원본 목록의 원본은 하나 그대로 (붙여넣기처럼 새 원본을 만들지 않는다)
    assert await count(db, owner, Source, Source.purpose == "reference") == 1

    # 거르기: 종류·회사·연도·글자
    assert [r.id for r in await uc.list_references(db, owner, kind="interview_review")] == [rid]
    assert await uc.list_references(db, owner, kind="resume") == []
    assert [r.id for r in await uc.list_references(db, owner, company_id=cid, year=2025)] == [rid]
    assert await uc.list_references(db, owner, year=2024) == []
    assert [r.id for r in await uc.list_references(db, owner, q="갈등")] == [rid]
    assert [r.id for r in await uc.list_references(db, owner, q="가상물류")] == [rid]
    # 회사를 지우면 연결만 비운다
    await company_uc.delete_company(db, owner, cid)
    assert (await uc.get_reference(db, owner, rid)).company_id is None


# ---------------------------------------------------------------- 구조 메모


async def test_notes_are_candidates_until_a_person_confirms(db, env):
    owner, agent, gw = env["owner"], env["agent"], env["gateway"]
    rid = await uc.create_reference(
        db, None, owner, kind="accepted_essay", title="합성", text=ESSAY
    )
    assert (await uc.get_reference(db, owner, rid)).source_id is None  # 원본 저장소 없이
    # 모델이 없으면 0건, 사람이 직접 쓴다
    result = await uc.suggest_notes(db, None, owner, rid)
    assert (result.created, result.notes) == (0, ["no_model"])

    result = await uc.suggest_notes(db, gw, owner, rid)
    assert result.created == 1 and result.notes == ["llm_dropped:2"]
    [request] = env["llm"].requests
    assert request.data_classes == frozenset({"private"})
    async with db.workspace_session(owner.workspace_id) as s:
        [call] = (await s.scalars(select(ModelCall))).all()
    assert call.purpose == "reference_notes"  # 라우트가 없으면 default로, 장부는 이 용도
    [note] = (await uc.get_reference(db, owner, rid)).notes
    assert (note.status, note.origin) == ("candidate", "model")
    assert note.structure == [
        "경험으로 시작하는 두괄식",
        "본인 행동",
        "수치 결과",
        "입사 후 포부로 마무리",
    ]
    assert note.question.startswith("[문항 1]")

    # AI 클라이언트는 확인하지 못하고, AI가 쓴 메모는 후보
    with pytest.raises(ReferenceRuleError, match="human_confirmation_required"):
        await uc.confirm_note(db, agent, rid, note.id)
    agent_note = await uc.add_note(db, agent, rid, materials=["인턴 프로젝트"])
    human_note = await uc.add_note(db, owner, rid, competencies=["협업"])
    with pytest.raises(ReferenceRuleError, match="note_empty"):
        await uc.add_note(db, owner, rid, structure=["  "])
    assert await uc.confirm_note(db, owner, rid, note.id) == "confirmed"
    statuses = {n.id: n.status for n in (await uc.get_reference(db, owner, rid)).notes}
    assert statuses == {note.id: "confirmed", agent_note: "candidate", human_note: "confirmed"}
    await uc.update_note(db, owner, rid, note.id, materials=["동아리 운영", "동아리 운영"])
    [edited] = [n for n in (await uc.get_reference(db, owner, rid)).notes if n.id == note.id]
    assert edited.materials == ["동아리 운영"] and edited.status == "confirmed"

    # 제한 등급 참고 글은 외부 모델로 보내지 않는다 (게이트웨이가 거부)
    await uc.update_reference(db, owner, rid, data_class="restricted")
    result = await uc.suggest_notes(db, gw, owner, rid)
    assert result.created == 0 and result.notes[0].startswith("llm_denied:")
    assert len(env["llm"].requests) == 1


# ---------------------------------------------------------------- HTTP


async def test_http_routes_errors_and_isolation(db, env):
    async with client_for(db, env, env["gateway"]) as c:
        r = await c.post("/core/references/pii-check", json={"text": ESSAY}, headers=auth("viewer"))
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["total"] == 4 and got["by_kind"]["school"] == 1
        assert got["hits"][0] == {
            "kind": "name",
            "start": ESSAY.index("가나다"),
            "end": ESSAY.index("가나다") + 3,
            "value": "가나다",
            "replacement": "○○○",
        }
        body = {"kind": "accepted_essay", "title": "합성 합격 자소서", "text": ESSAY}
        r = await c.post("/core/references", json=body, headers=auth("viewer"))
        assert r.status_code == 403
        r = await c.post("/core/references", json={**body, "kind": "mine"}, headers=auth("owner"))
        assert r.status_code == 422
        r = await c.post("/core/references", json={**body, "year": 1800}, headers=auth("owner"))
        assert r.status_code == 422
        r = await c.post(
            "/core/references",
            json={"kind": "essay", "title": "x"},
            headers=auth("owner"),
        )
        assert (r.status_code, r.json()) == (422, {"detail": "text_or_source_required"})
        r = await c.post(
            "/core/references", json={**body, "mask_pii": True, "year": 2025}, headers=auth("owner")
        )
        assert r.status_code == 201, r.text
        rid = r.json()["id"]
        r = await c.post(
            "/core/references",
            json={"kind": "interview_review", "title": "면접 후기 (합성)", "text": REVIEW},
            headers=auth("owner"),
        )
        review = r.json()["id"]

        r = await c.get("/core/references", headers=auth("viewer"))
        assert [x["id"] for x in r.json()] == [review, rid]
        r = await c.get(
            "/core/references", params={"kind": "accepted_essay"}, headers=auth("viewer")
        )
        assert [(x["id"], x["pii_masked"], x["year"]) for x in r.json()] == [(rid, 4, 2025)]
        r = await c.get("/core/references", params={"kind": "x"}, headers=auth("viewer"))
        assert r.status_code == 422
        r = await c.get(f"/core/references/{rid}", headers=auth("viewer"))
        assert "○○○" in r.json()["text"] and r.json()["notes"] == []

        r = await c.patch(
            f"/core/references/{rid}",
            json={"role": "물류 엔지니어", "title": None},
            headers=auth("owner"),
        )
        assert (r.status_code, r.json()) == (422, {"detail": "title_required"})
        r = await c.patch(
            f"/core/references/{rid}", json={"role": "물류 엔지니어"}, headers=auth("owner")
        )
        assert r.status_code == 200
        r = await c.post(f"/core/references/{rid}/suggest-notes", headers=auth("owner"))
        assert r.json() == {"created": 1, "notes": ["llm_dropped:2"]}
        [note] = (await c.get(f"/core/references/{rid}", headers=auth("viewer"))).json()["notes"]
        r = await c.post(
            f"/core/references/{rid}/notes/{note['id']}/confirm", headers=auth("agent")
        )
        assert (r.status_code, r.json()) == (403, {"detail": "human_confirmation_required"})
        r = await c.post(
            f"/core/references/{rid}/notes/{note['id']}/confirm", headers=auth("owner")
        )
        assert r.json()["status"] == "confirmed"
        r = await c.post(
            f"/core/references/{rid}/notes",
            json={"structure": ["상황", "행동", "결과"]},
            headers=auth("owner"),
        )
        assert r.status_code == 201
        n2 = r.json()["id"]
        r = await c.patch(
            f"/core/references/{rid}/notes/{n2}",
            json={"competencies": ["끈기"]},
            headers=auth("owner"),
        )
        assert r.status_code == 200
        assert (
            await c.delete(f"/core/references/{rid}/notes/{n2}", headers=auth("owner"))
        ).status_code == 204
        r = await c.get("/core/references", headers=auth("viewer"))
        assert {x["id"]: x["notes_confirmed"] for x in r.json()} == {rid: 1, review: 0}

        # 원본에서 만들기: 참고 원본이 아니면 409
        up = await sources.upload_source(
            db,
            env["blobs"],
            env["owner"],
            filename="cv.txt",
            data="내 이력서 (합성)\n\n주문 서비스를 만들었습니다.".encode(),
            declared_mime="text/plain",
            max_bytes=1 << 20,
        )
        r = await c.post(
            "/core/references",
            json={"kind": "resume", "title": "x", "source_id": str(up.source_id)},
            headers=auth("owner"),
        )
        assert (r.status_code, r.json()) == (409, {"detail": "source_not_reference"})
        # 참고 글이 쓰는 원본의 용도는 바꾸지 않는다
        src = (await c.get(f"/core/references/{rid}", headers=auth("viewer"))).json()["source_id"]
        r = await c.patch(
            f"/core/sources/{src}", json={"purpose": "experience"}, headers=auth("owner")
        )
        assert (r.status_code, r.json()) == (409, {"detail": "source_has_reference_items"})

        # 다른 공간: 0건, 404
        r = await c.get("/core/references", headers=auth("other"))
        assert r.json() == []
        for method, path in (
            ("GET", f"/core/references/{rid}"),
            ("DELETE", f"/core/references/{rid}"),
            ("POST", f"/core/references/{rid}/suggest-notes"),
            ("POST", f"/core/references/{rid}/notes/{note['id']}/confirm"),
        ):
            r = await c.request(method, path, headers=auth("other"))
            assert (r.status_code, r.json()) == (404, {"detail": "not found"}), path
        r = await c.post(
            "/core/references",
            json={**body, "source_id": None, "company_id": "00000000-0000-0000-0000-000000000001"},
            headers=auth("owner"),
        )
        assert r.status_code == 404
        assert (
            await c.delete(f"/core/references/{review}", headers=auth("owner"))
        ).status_code == 204
    other = env["other"]
    assert await count(db, other, ReferenceItem) == 0
    assert await count(db, other, ReferenceNote) == 0
    assert (await search.search(db, other, "물류 동아리", include_reference=True)).hits == []


# ---------------------------------------------------------------- MCP


async def test_mcp_reference_tools_give_notes_not_text(db, env):
    from career_core.interfaces.mcp.tools import TOOLS_BY_NAME

    owner = env["owner"]
    rid = await uc.create_reference(
        db, env["blobs"], owner, kind="accepted_essay", title="합성 합격 자소서", text=ESSAY
    )
    await uc.suggest_notes(db, env["gateway"], owner, rid)
    await uc.add_note(db, owner, rid, competencies=["협업"])
    for name in ("reference_search", "reference_get"):
        tool = TOOLS_BY_NAME[name]
        assert tool.read_only and tool.untrusted and tool.action == "references.read"
        assert "NOT the user's experience" in tool.description
        assert "never copy" in tool.description

    tokens = {"owner": owner, "other": env["other"]}
    app = FastAPI()
    app.include_router(build_router(CoreDeps(identity=Tokens(tokens), db=db)))

    async def call(token, name, args):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
            res = await c.post(
                "/core/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": args},
                },
                headers=auth(token),
            )
        return res.json()["result"]

    found = (await call("owner", "reference_search", {"query": "동아리"}))["structuredContent"]
    assert found["record"] == "reference" and found["untrusted_content"] is True
    assert "NOT the user's experience" in found["note"]
    [hit] = found["references"]
    assert hit["id"] == str(rid) and len(hit["excerpt"]) <= 162 and "동아리" in hit["excerpt"]
    # 확인된 메모만 (모델 후보는 빠짐)
    assert [n["competencies"] for n in hit["notes"]] == [["협업"]]
    got = (await call("owner", "reference_get", {"reference_id": str(rid)}))["structuredContent"]
    assert "text" not in got and got["text_chars"] == len(ESSAY)
    assert len(got["excerpt"]) <= 162
    dumped = json.dumps(got, ensure_ascii=False)
    assert "입사 후에는 물류 현장의 작은 낭비" not in dumped  # 뒷부분 원문은 주지 않는다
    both = (
        await call("owner", "reference_get", {"reference_id": str(rid), "include_candidates": True})
    )["structuredContent"]
    assert len(both["notes"]) == 2
    assert (await call("other", "reference_search", {}))["structuredContent"]["references"] == []
    assert (await call("other", "reference_get", {"reference_id": str(rid)}))["isError"] is True
    # 내 경험 검색 도구에는 나오지 않는다
    await parse_all(db, env["blobs"])
    out = (await call("owner", "search_brain", {"query": "물류 동아리 회장"}))["structuredContent"]
    assert out["hits"] == []


# ---------------------------------------------------------------- 내보내기·복원


async def test_export_restore_keeps_references_marked(db, env, tmp_path):
    owner, blobs = env["owner"], env["blobs"]
    cid = await company_uc.create_company(db, owner, name="가상물류")
    rid = await uc.create_reference(
        db,
        blobs,
        owner,
        kind="accepted_essay",
        title="합성 합격 자소서",
        text=ESSAY,
        company_id=cid,
        year=2025,
        mask=True,
    )
    await uc.add_note(db, owner, rid, structure=["두괄식", "결과"], competencies=["문제 해결"])
    before = await uc.get_reference(db, owner, rid)
    out = tmp_path / "a.tar.gz"
    report = await export_workspace(db, blobs, owner, out)
    with tarfile.open(out, "r:gz") as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())
        sources_rows = [
            json.loads(line)
            for line in tar.extractfile("tables/core.sources.jsonl").read().decode().splitlines()
        ]
    rows = {t["name"]: t["rows"] for t in manifest["tables"]}
    assert (rows["core.reference_items"], rows["core.reference_notes"]) == (1, 1)
    assert {"core.reference_items", "core.reference_notes"} <= set(manifest["reference_tables"])
    assert [r["purpose"] for r in sources_rows] == ["reference"]

    copy = await restore_workspace(db, blobs, out, new_ids=True)
    assert copy.rows_total == report.rows_total
    actor = ActorContext(copy.owners[0], copy.workspace_id, scopes_for_role(Role.OWNER))
    [restored] = await uc.list_references(db, actor)
    assert restored.id != rid and restored.company_name == "가상물류"
    after = await uc.get_reference(db, actor, restored.id)
    assert (after.text, after.pii_masked, after.year) == (before.text, 4, 2025)
    assert [(n.structure, n.competencies, n.status) for n in after.notes] == [
        (["두괄식", "결과"], ["문제 해결"], "confirmed")
    ]
    assert after.source_id is not None and after.source_id != before.source_id

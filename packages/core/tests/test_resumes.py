"""이력서 판본 (c0047, O17, plan §19.5).

- 판본 계보: 마스터 → 직무별 → 공고별. 아래 판본은 위 판본의 revision을 복사하고 출처를 남긴다.
- 브레인에서 채우기: 확인한 사실에서 온 줄은 근거 연결을 유지한다 (resume_line_claims). 직접 쓴 줄은 근거 없음.
- 사실·기록이 바뀌면 오래됨/근거 끊김으로 보이고 조용히 바뀌지 않는다. 다시 채우기·새 문장으로는 명시적으로.
- 블라인드: HTML·DOCX·Markdown·JSON에 사진·생년월일·주소·학교 이름 0건 (바이트로 확인).
- 409, 같은 요청 키, 다른 공간 0건, 권한, MCP와 HTTP 같은 결과, 내보내기·복원 왕복.

모든 자료는 합성이다.
"""

import hashlib
import io
import json
import re
import struct
import tarfile
import zipfile
from datetime import date
from uuid import UUID, uuid4

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.db import JobPosting, JobPostingVersion
from career_core.db.models import ApplicationPrep, ResumeLineClaim, ResumeRevision, ResumeVersion
from career_core.domain.brain import ClaimStatus
from career_core.domain.resume_export import CSP, PAGINATE_JS, PAGINATE_SHA256
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.llm import ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.ports import ActorContext
from career_core.usecases import applications as app_uc
from career_core.usecases import brain
from career_core.usecases import credentials as credential_uc
from career_core.usecases import education as education_uc
from career_core.usecases import postings as posting_uc
from career_core.usecases import profiles as profile_uc
from career_core.usecases import resume_suggest as suggest_uc
from career_core.usecases import resumes as uc
from career_core.usecases.export import export_workspace, restore_workspace
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio

SCHOOL = "가상한빛대학교"
CAMPUS = "새솔캠퍼스"
ADDRESS = "가상시 가상구 합성로 12"
BIRTH = date(1994, 7, 21)


class FakeResolver:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def _seg(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def jpeg(w=354, h=472) -> bytes:
    """구조만 맞춘 합성 baseline JPEG (증명사진 규격 3×4)."""
    out = b"\xff\xd8" + _seg(0xE0, b"JFIF\x00\x01\x01\x01\x01\x2c\x01\x2c\x00\x00")
    out += _seg(0xDB, b"\x00" + bytes(i % 255 + 1 for i in range(64)))
    out += _seg(
        0xC0, b"\x08" + struct.pack(">HH", h, w) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    )
    out += _seg(0xC4, b"\x00" + bytes([1] + [0] * 15) + b"\x00")
    out += _seg(0xDA, b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00")
    return out + b"\x12\x34\xff\x00\x56\x00\xff\xd9"


async def saved_posting(db, actor, *, company, title, url, text="합성 공고 본문", questions=()):
    async with db.workspace_session(actor.workspace_id) as s:
        p = JobPosting(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            canonical_url=url,
            source_url=url,
            status="draft",
            created_by=actor.subject_id,
        )
        s.add(p)
        await s.flush()
        s.add(
            JobPostingVersion(
                id=uuid4(),
                workspace_id=actor.workspace_id,
                posting_id=p.id,
                version_no=1,
                final_url=url,
                method="manual_text",
                adapter="text",
                content_hash=uuid4().hex,
                text=text,
                extracted={},
                missing=[],
                changed_fields=[],
                created_by=actor.subject_id,
            )
        )
    await posting_uc.confirm_posting(
        db,
        actor,
        p.id,
        overrides={"company": company, "title": title, "questions": list(questions)},
    )
    return p.id


@pytest.fixture
async def env(db, tmp_path):
    ws_a, a = await create_workspace(db, name="A (합성)", owner_display_name="amy")
    ws_b, b = await create_workspace(db, name="B (합성)", owner_display_name="ben")
    owner = ActorContext(a, ws_a, scopes_for_role(Role.OWNER))
    editor = ActorContext(a, ws_a, scopes_for_role(Role.EDITOR))
    viewer = ActorContext(a, ws_a, scopes_for_role(Role.VIEWER))
    other = ActorContext(b, ws_b, scopes_for_role(Role.OWNER))
    blobs = LocalBlobStore(tmp_path / "blobs")
    await profile_uc.save_profile(
        db,
        owner,
        name="김합성",
        email="amy@example.test",
        phone="010-0000-0000",
        address=ADDRESS,
        birth_date=BIRTH,
        headline="백엔드 개발자",
        links=[("GitHub", "https://example.test/amy")],
    )
    photo = await profile_uc.upload_photo(db, blobs, owner, data=jpeg(), spec="cm-3x4")
    work = await brain.create_experience(
        db,
        owner,
        title="주문 서비스 개선",
        organization="가상커머스",
        role="백엔드 개발",
        kind="work",
        started_on="2021-03",
        ended_on="2023-02",
        tags=["Python"],
    )
    project = await brain.create_experience(
        db, owner, title="정산 배치 재작성", kind="project", started_on="2022-01", parent_id=work
    )
    result = await brain.add_claim(
        db,
        owner,
        work,
        statement="주문 API 지연을 800ms에서 200ms로 줄였다",
        kind="result",
        contribution="personal",
    )
    action = await brain.add_claim(
        db,
        owner,
        work,
        statement="결제 대기열을 Redis로 나눠 처리했다",
        kind="action",
        contribution="personal",
    )
    child_claim = await brain.add_claim(
        db,
        owner,
        project,
        statement="정산 배치를 4시간에서 40분으로 줄였다",
        kind="result",
        contribution="personal",
    )
    # 편집자가 쓴 주장은 확인 전 → 고를 수 없다
    pending = await brain.add_claim(
        db, editor, work, statement="확인 전 주장", kind="result", contribution="personal"
    )
    edu = await education_uc.create_education(
        db,
        owner,
        {
            "school": SCHOOL,
            "campus": CAMPUS,
            "major": "컴퓨터공학",
            "degree": "bachelor",
            "status": "graduated",
            "entered_on": "2013-03",
            "graduated_on": "2019-02",
        },
    )
    cred = await credential_uc.create_credential(
        db, owner, {"kind": "license", "name": "정보처리기사", "issuer": "가상인력공단"}
    )
    other_exp = await brain.create_experience(db, other, title="B의 경험")
    foreign = await brain.add_claim(
        db, other, other_exp, statement="B 공간의 주장", kind="result", contribution="personal"
    )
    posting = await saved_posting(
        db,
        owner,
        company="(주)가상회사",
        title="백엔드 개발자",
        url="https://jobs.example.test/r1",
        text="블라인드 채용으로 진행합니다. 사진·학교를 적지 마세요.",
        questions=[{"prompt": "지원 동기를 작성해 주세요. (500자 이내)", "char_limit": 500}],
    )
    application = await app_uc.create_application(db, owner, posting)
    tokens = {"owner": owner, "editor": editor, "viewer": viewer, "other": other}
    deps = CoreDeps(identity=FakeResolver(tokens), db=db, blobs=blobs)
    app = FastAPI()
    app.include_router(build_router(deps))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        yield {
            "c": c,
            "deps": deps,
            "owner": owner,
            "editor": editor,
            "viewer": viewer,
            "other": other,
            "blobs": blobs,
            "photo": photo.photo.id,
            "work": work,
            "project": project,
            "result": result,
            "action": action,
            "child_claim": child_claim,
            "pending": pending,
            "edu": edu,
            "cred": cred,
            "foreign": foreign,
            "application": application,
        }


def items(view, kind):
    [sec] = [s for s in view.content["sections"] if s["kind"] == kind]
    return sec["items"]


def by_ref(view, ref_type, ref_id):
    for sec in view.content["sections"]:
        for it in sec["items"]:
            if it.get("ref") and it["ref"] == {"type": ref_type, "id": str(ref_id)}:
                return it
    raise AssertionError((ref_type, ref_id))


async def master(db, env, *, claims=True):
    return await uc.create_version(db, env["owner"], kind="master", fill=True, fill_claims=claims)


# ---------------------------------------------------------------- 채우기·계보


async def test_fill_from_brain_keeps_claim_links(db, env):
    owner = env["owner"]
    mid = await master(db, env)
    view = await uc.get_version(db, owner, mid)
    assert view.summary.kind == "master" and view.revision_no == 1
    [profile] = items(view, "profile")
    assert profile["fields"]["name"] == "김합성" and profile["state"] == "ok"
    assert profile["fields"]["photo_id"] == str(env["photo"])
    work = by_ref(view, "experience", env["work"])
    assert work["fields"]["organization"] == "가상커머스" and work["state"] == "ok"
    # 확인한 사실(결과·한 일)만 줄로, 확인 전 주장은 빠진다
    assert {ln["claim_id"] for ln in work["lines"]} == {str(env["result"]), str(env["action"])}
    assert all(ln["origin"] == "claim" and ln["state"] == "ok" for ln in work["lines"])
    project = by_ref(view, "experience", env["project"])
    assert [ln["claim_id"] for ln in project["lines"]] == [str(env["child_claim"])]
    assert by_ref(view, "education", env["edu"])["fields"]["school"] == SCHOOL
    assert by_ref(view, "credential", env["cred"])["fields"]["name"] == "정보처리기사"
    assert "Python" in [it["fields"]["name"] for it in items(view, "skills")]
    # 줄 ↔ 확인한 사실 연결 표
    async with db.workspace_session(owner.workspace_id) as s:
        links = (
            await s.scalars(
                select(ResumeLineClaim.claim_id).where(
                    ResumeLineClaim.revision_id == view.revision_id
                )
            )
        ).all()
    assert set(links) == {env["result"], env["action"], env["child_claim"]}
    # 마스터는 하나
    with pytest.raises(uc.ResumeRuleError, match="master_exists"):
        await uc.create_version(db, owner, kind="master")
    # 다시 채우기: 더할 것이 없으면 새 revision을 만들지 않는다
    res = await uc.save(db, owner, mid, base_revision=1, operations=[{"op": "fill_all"}])
    assert (res.revision_no, res.created) == (1, False)
    # 확인 전 사실·다른 공간 사실·다른 경험의 사실은 줄로 넣을 수 없다
    work_id = work["id"]
    for claim, code in (
        (env["pending"], "claim_not_confirmed"),
        (env["foreign"], "unknown_claim"),
    ):
        with pytest.raises(uc.ResumeRuleError, match=code):
            await uc.save(
                db,
                owner,
                mid,
                base_revision=1,
                operations=[{"op": "add_line", "item_id": work_id, "claim_id": str(claim)}],
            )
    with pytest.raises(uc.ResumeRuleError, match="claim_not_in_experience"):
        await uc.save(
            db,
            owner,
            mid,
            base_revision=1,
            operations=[
                {"op": "add_line", "item_id": project["id"], "claim_id": str(env["result"])}
            ],
        )
    # 상위 경험은 아래 경험의 사실도 고를 수 있다
    res = await uc.save(
        db,
        owner,
        mid,
        base_revision=1,
        operations=[{"op": "add_line", "item_id": work_id, "claim_id": str(env["child_claim"])}],
    )
    assert res.revision_no == 2
    # 브레인 기록의 칸은 직접 고치지 않는다 (다시 채우기로만)
    with pytest.raises(uc.ResumeRuleError, match="ref_fields_locked"):
        await uc.save(
            db,
            owner,
            mid,
            base_revision=2,
            operations=[{"op": "update_item", "item_id": work_id, "fields": {"title": "바꿈"}}],
        )


async def test_lineage_copies_parent_and_keeps_source(db, env):
    owner = env["owner"]
    mid = await master(db, env)
    m1 = await uc.get_version(db, owner, mid)
    rid = await uc.create_version(
        db, owner, kind="role", parent_id=mid, target_role="백엔드", title="백엔드 이력서"
    )
    r = await uc.get_version(db, owner, rid)
    assert (r.revision_no, r.revision_origin, r.summary.parent_id) == (1, "copy", mid)
    assert r.summary.source_revision_no == 1 and r.summary.parent_revision == 1
    strip = lambda v: json.dumps(
        [
            [{k: x for k, x in it.items() if k != "state"} for it in s["items"]]
            for s in v.content["sections"]
        ]
    )
    assert strip(r) == strip(m1)  # 항목·줄·근거 연결·값 복사본이 그대로
    async with db.workspace_session(owner.workspace_id) as s:
        row = await s.get(ResumeVersion, rid)
        src = await s.get(ResumeRevision, row.source_revision_id)
        assert (src.version_id, src.revision_no) == (mid, 1)
        n = await s.scalar(
            select(func.count())
            .select_from(ResumeLineClaim)
            .where(ResumeLineClaim.revision_id == r.revision_id)
        )
    assert n == 3  # 복사한 revision도 줄 ↔ 사실 연결을 가진다
    # 위 판본을 고쳐도 아래 판본은 그대로. 목록은 위 판본이 그 뒤에 바뀐 것을 보인다
    work_item = by_ref(m1, "experience", env["work"])["id"]
    await uc.save(
        db,
        owner,
        mid,
        base_revision=1,
        operations=[{"op": "add_line", "item_id": work_item, "text": "직접 쓴 줄"}],
    )
    r_again = await uc.get_version(db, owner, rid)
    assert strip(r_again) == strip(r)
    [row] = [v for v in await uc.list_versions(db, owner) if v.id == rid]
    assert (row.source_revision_no, row.parent_revision) == (1, 2)
    # 공고별: 직무별에서, 지원에 이어서
    pid = await uc.create_version(
        db, owner, kind="posting", parent_id=rid, application_id=env["application"]
    )
    p = await uc.get_version(db, owner, pid)
    assert [x["id"] for x in p.lineage] == [str(mid), str(rid)]
    assert p.summary.application_id == env["application"]
    assert p.summary.title == "(주)가상회사 백엔드 개발자 이력서" or p.summary.title.endswith(
        "이력서"
    )
    assert [v.id for v in await uc.list_versions(db, owner, application_id=env["application"])] == [
        pid
    ]
    # 종류별 위 판본 규칙
    for kind, parent, code in (
        ("role", None, "parent_required"),
        ("role", rid, "invalid_parent"),
        ("posting", pid, "invalid_parent"),
    ):
        with pytest.raises(uc.ResumeRuleError, match=code):
            await uc.create_version(db, owner, kind=kind, parent_id=parent)
    with pytest.raises(uc.ResumeRuleError, match="application_needs_posting"):
        await uc.create_version(
            db, owner, kind="role", parent_id=mid, application_id=env["application"]
        )
    # 아래 판본이 있으면 지우지 않는다
    with pytest.raises(uc.ResumeRuleError, match="version_has_children"):
        await uc.delete_version(db, owner, rid)
    await uc.delete_version(db, owner, pid)
    await uc.delete_version(db, owner, rid)
    assert [v.id for v in await uc.list_versions(db, owner)] == [mid]


async def test_prep_order_for_posting_version(db, env):
    owner = env["owner"]
    mid = await master(db, env, claims=False)
    pid = await uc.create_version(
        db, owner, kind="posting", parent_id=mid, application_id=env["application"]
    )
    with pytest.raises(uc.ResumeRuleError, match="prep_not_chosen"):
        await uc.save(db, owner, pid, base_revision=1, operations=[{"op": "apply_prep_order"}])
    async with db.workspace_session(owner.workspace_id) as s:
        app = await s.get(app_uc.Application, env["application"])
        [doc] = (
            await s.scalars(
                select(app_uc.ApplicationDocument).where(
                    app_uc.ApplicationDocument.application_id == env["application"]
                )
            )
        ).all()
        s.add(
            ApplicationPrep(
                workspace_id=owner.workspace_id,
                application_id=app.id,
                posting_id=app.posting_id,
                mode="search",
                choices={
                    str(doc.id): [
                        {
                            "experience_id": str(env["project"]),
                            "claim_ids": [str(env["child_claim"])],
                            "chosen_at": "2026-01-01T00:00:00+00:00",
                            "chosen_by": str(owner.subject_id),
                        },
                    ]
                },
                generated_by=owner.subject_id,
                created_by=owner.subject_id,
            )
        )
    assert (await uc.get_version(db, owner, pid)).prep_choices == 1
    res = await uc.save(db, owner, pid, base_revision=1, operations=[{"op": "apply_prep_order"}])
    view = await uc.get_version(db, owner, res.revision_no and pid)
    [first, *_] = items(view, "project")
    assert first["ref"]["id"] == str(env["project"])
    # 이미 있던 항목: 고른 사실 줄은 사람이 고른다 (순서만 바꾼다)
    assert first["lines"] == []


# ---------------------------------------------------------------- 근거 없는 줄·검사·오래됨


async def test_human_lines_flagged_and_checked(db, env):
    owner = env["owner"]
    mid = await master(db, env)
    view = await uc.get_version(db, owner, mid)
    work = by_ref(view, "experience", env["work"])["id"]
    await uc.save(
        db,
        owner,
        mid,
        base_revision=1,
        operations=[
            {"op": "add_line", "item_id": work, "text": "항상 최선을 다하는 열정적인 인재입니다"},
            {
                "op": "add_item",
                "section": "activities",
                "fields": {"title": "합성 봉사 동아리", "period_text": "2018"},
            },
        ],
    )
    view = await uc.get_version(db, owner, mid)
    line = next(ln for ln in by_ref(view, "experience", env["work"])["lines"] if not ln["claim_id"])
    assert (line["origin"], line["state"]) == ("user", "none")
    assert view.summary.unsupported_lines == 1
    manual = items(view, "activities")[0]
    assert manual["state"] == "manual" and manual["ref"] is None
    check = await uc.check_version(db, owner, mid)
    codes = {c["code"]: c for c in check["checklist"]}
    assert codes["lines_without_evidence"]["targets"] == [line["id"]]
    assert "generic_lines" in codes
    assert any(f["block_id"] == line["id"] for f in check["findings"])
    assert "empty_required_section" not in codes
    # 필수 구역 숨기기 → 체크리스트
    await uc.save(
        db,
        owner,
        mid,
        base_revision=2,
        operations=[{"op": "set_section", "section": "education", "visible": False}],
    )
    codes = {c["code"] for c in (await uc.check_version(db, owner, mid))["checklist"]}
    assert "required_section_hidden" in codes
    # 쪽 수 한도
    await uc.update_settings(db, owner, mid, {"max_pages": 1})
    many = [
        {"op": "add_line", "item_id": work, "text": f"합성 줄 {n} " + "가나다라마바사 " * 20}
        for n in range(25)
    ]
    await uc.save(db, owner, mid, base_revision=3, operations=many)
    check = await uc.check_version(db, owner, mid)
    assert "page_overflow" in {c["code"] for c in check["checklist"]}
    assert check["estimated_pages"] > 1


async def test_stale_and_broken_claims_are_shown_not_changed(db, env):
    owner = env["owner"]
    mid = await master(db, env)
    view = await uc.get_version(db, owner, mid)
    lines = {ln["claim_id"]: ln for ln in by_ref(view, "experience", env["work"])["lines"]}
    result_line, action_line = lines[str(env["result"])], lines[str(env["action"])]
    # 사실 문장을 고친다 → 줄은 그대로, 상태가 오래됨
    await brain.edit_claim(
        db, owner, env["result"], statement="주문 API 지연을 800ms에서 150ms로 줄였다"
    )
    # 사실을 은퇴시킨다 → 근거 끊김
    await brain.set_claim_status(db, owner, env["action"], ClaimStatus.RETIRED)
    # 경험 기록을 고친다 → 항목이 오래됨
    await brain.update_experience(db, owner, env["work"], organization="가상커머스(주)")
    view = await uc.get_version(db, owner, mid)
    work = by_ref(view, "experience", env["work"])
    lines = {ln["id"]: ln for ln in work["lines"]}
    assert lines[result_line["id"]]["text"] == "주문 API 지연을 800ms에서 200ms로 줄였다"
    assert lines[result_line["id"]]["state"] == "stale"
    assert lines[action_line["id"]]["state"] == "broken"
    assert work["state"] == "stale" and work["fields"]["organization"] == "가상커머스"
    codes = {c["code"] for c in (await uc.check_version(db, owner, mid))["checklist"]}
    assert {"lines_stale", "lines_evidence_broken", "items_stale"} <= codes
    # 끊긴 근거로는 새 문장을 받을 수 없다
    with pytest.raises(uc.ResumeRuleError):
        await uc.save(
            db,
            owner,
            mid,
            base_revision=1,
            operations=[{"op": "refresh_line", "line_id": action_line["id"]}],
        )
    res = await uc.save(
        db,
        owner,
        mid,
        base_revision=1,
        operations=[
            {"op": "refresh_line", "line_id": result_line["id"]},
            {"op": "refill_item", "item_id": work["id"]},
            {"op": "remove_line", "line_id": action_line["id"]},
        ],
    )
    view = await uc.get_version(db, owner, mid)
    work = by_ref(view, "experience", env["work"])
    assert res.revision_no == 2 and work["state"] == "ok"
    assert work["fields"]["organization"] == "가상커머스(주)"
    fixed = {ln["id"]: ln for ln in work["lines"]}[result_line["id"]]
    assert (fixed["text"], fixed["state"]) == ("주문 API 지연을 800ms에서 150ms로 줄였다", "ok")
    # 지난 revision은 그대로 남는다
    old = await uc.get_version(db, owner, mid, revision=1)
    assert len(by_ref(old, "experience", env["work"])["lines"]) == 2
    # 연락처를 바꾸면 "오래된 연락처"
    await profile_uc.save_profile(
        db, owner, name="김합성", email="new@example.test", birth_date=BIRTH
    )
    codes = {c["code"] for c in (await uc.check_version(db, owner, mid))["checklist"]}
    assert "old_contact" in codes


async def test_conflict_and_idempotency(db, env):
    c, owner = env["c"], env["owner"]
    mid = await master(db, env, claims=False)
    view = await uc.get_version(db, owner, mid)
    work = by_ref(view, "experience", env["work"])["id"]
    op = [{"op": "add_line", "item_id": work, "text": "첫 줄"}]
    url = f"/core/resumes/{mid}/revisions"
    res = await c.post(
        url,
        json={"base_revision": 1, "operations": op, "idempotency_key": "k1"},
        headers=auth("owner"),
    )
    assert res.status_code == 200 and res.json()["revision_no"] == 2
    again = await c.post(
        url,
        json={"base_revision": 1, "operations": op, "idempotency_key": "k1"},
        headers=auth("owner"),
    )
    assert again.json() == {**res.json(), "created": False}
    stale = await c.post(url, json={"base_revision": 1, "operations": op}, headers=auth("owner"))
    assert stale.status_code == 409
    assert stale.json()["detail"] == {"code": "revision_conflict", "current_revision": 2}
    # 같은 내용이면 새 revision 없음
    same = await c.post(
        url,
        json={
            "base_revision": 2,
            "operations": [{"op": "set_section", "section": "work", "visible": True}],
        },
        headers=auth("owner"),
    )
    assert same.json()["created"] is False and same.json()["revision_no"] == 2
    bad = await c.post(
        url, json={"base_revision": 2, "operations": [{"op": "nope"}]}, headers=auth("owner")
    )
    assert bad.status_code == 422
    revs = (await c.get(url, headers=auth("owner"))).json()
    assert [r["revision_no"] for r in revs] == [2, 1]


# ---------------------------------------------------------------- 블라인드·내보내기


BLIND_SECRETS = (
    SCHOOL,
    CAMPUS,
    ADDRESS,
    "1994",
    "07.21",
    "data:image",
)


async def test_blind_exports_have_zero_personal_fields(db, env):
    c, owner = env["c"], env["owner"]
    mid = await master(db, env)
    view = await uc.get_version(db, owner, mid)
    work = by_ref(view, "experience", env["work"])["id"]
    # 줄 글에 학교 이름·생년월일을 적어도 블라인드 출력에서는 가린다
    await uc.save(
        db,
        owner,
        mid,
        base_revision=1,
        operations=[
            {"op": "add_line", "item_id": work, "text": f"{SCHOOL} 동문 세미나에서 발표했다"},
            {"op": "add_line", "item_id": work, "text": "1994년생 장남으로 성실합니다"},
            {
                "op": "update_item",
                "item_id": items(view, "profile")[0]["id"],
                "show": {"photo": True, "birth_date": True, "address": True},
            },
        ],
    )
    for template in ("one_column", "two_column", "korean_table"):
        await uc.update_settings(db, owner, mid, {"template": template, "blind": False})
        # 블라인드가 아니면 사진·생년월일·주소·학교가 들어간다 (시험이 실제로 보는지 확인)
        html = (await uc.export_version(db, env["blobs"], owner, mid, fmt="html")).data.decode()
        assert SCHOOL in html and ADDRESS in html and "data:image/jpeg" in html and "1994" in html
        docx = (await uc.export_version(db, env["blobs"], owner, mid, fmt="docx")).data
        with zipfile.ZipFile(io.BytesIO(docx)) as z:
            assert (
                any(n.startswith("word/media/") for n in z.namelist()) or template != "korean_table"
            )
        await uc.update_settings(db, owner, mid, {"blind": True})
        for fmt in ("html", "docx", "md", "json"):
            res = await uc.export_version(db, env["blobs"], owner, mid, fmt=fmt)
            text = uc.export_text(fmt, res.data)
            raw = (
                res.data
                if fmt != "docx"
                else b"".join(
                    zipfile.ZipFile(io.BytesIO(res.data)).read(n)
                    for n in zipfile.ZipFile(io.BytesIO(res.data)).namelist()
                )
            )
            for secret in BLIND_SECRETS:
                assert secret not in text, (template, fmt, secret)
                assert secret.encode() not in raw, (template, fmt, secret)
            if fmt == "docx":
                with zipfile.ZipFile(io.BytesIO(res.data)) as z:
                    assert not [n for n in z.namelist() if n.startswith("word/media/")]
            assert "blind_leak" not in res.summary, (template, fmt)
            assert "○○" in text  # 가린 자리
            assert "컴퓨터공학" in text  # 전공은 남는다
        # 미리보기 = 내보낸 HTML 파일 (같은 바이트)
        preview = await c.get(f"/core/resumes/{mid}/preview", headers=auth("owner"))
        exported = await c.get(f"/core/resumes/{mid}/export?format=html", headers=auth("owner"))
        assert preview.content == exported.content and preview.status_code == 200
        assert int(preview.headers["x-resume-masked"]) >= 2
    data = json.loads((await uc.export_version(db, env["blobs"], owner, mid, fmt="json")).data)
    assert data["meta"]["x-career-brain"]["blind"] is True
    assert [e["institution"] for e in data["education"]] == [""]
    assert "image" not in data["basics"]
    check = await uc.check_version(db, owner, mid)
    codes = {c["code"] for c in check["checklist"]}
    assert {"blind_masked", "blind_personal_words"} <= codes


async def test_blind_suggested_from_posting(db, env):
    owner = env["owner"]
    mid = await master(db, env, claims=False)
    pid = await uc.create_version(
        db, owner, kind="posting", parent_id=mid, application_id=env["application"]
    )
    assert (await uc.get_version(db, owner, mid)).blind_suggested is False
    assert (await uc.get_version(db, owner, pid)).blind_suggested is True
    codes = {c["code"] for c in (await uc.check_version(db, owner, pid))["checklist"]}
    assert "blind_suggested" in codes
    await uc.update_settings(db, owner, pid, {"blind": True})
    assert (await uc.get_version(db, owner, pid)).blind_suggested is False


async def test_html_is_self_contained_and_clean(db, env):
    owner = env["owner"]
    mid = await master(db, env)
    assert (
        PAGINATE_SHA256
        == __import__("base64").b64encode(hashlib.sha256(PAGINATE_JS.encode()).digest()).decode()
    )
    for template in ("one_column", "two_column", "korean_table"):
        await uc.update_settings(db, owner, mid, {"template": template})
        res = await uc.export_version(db, env["blobs"], owner, mid, fmt="html")
        html = res.data.decode()
        assert CSP in html and PAGINATE_JS in html
        # 밖에서 가져오는 것 없음, 숨김 서식 없음
        assert not re.search(r"(src|href)=\"https?://", html)
        assert "hidden_text_markup" not in res.summary and "prompt_injection" not in res.summary
        assert res.filename.endswith(".html") and not res.filename.endswith(".pdf.html")
        docx = await uc.export_version(db, env["blobs"], owner, mid, fmt="docx")
        assert docx.data[:2] == b"PK" and "hidden_text_markup" not in docx.summary
        md = await uc.export_version(db, env["blobs"], owner, mid, fmt="md")
        assert "주문 API 지연을 800ms에서 200ms로 줄였다" in md.data.decode()
    js = json.loads((await uc.export_version(db, env["blobs"], owner, mid, fmt="json")).data)
    assert js["basics"]["name"] == "김합성" and js["work"][0]["name"] == "가상커머스"
    assert js["work"][0]["startDate"] == "2021-03"


# ---------------------------------------------------------------- 권한·공간·MCP


async def test_permissions_and_isolation(db, env):
    c = env["c"]
    res = await c.post(
        "/core/resumes", json={"kind": "master", "fill": True}, headers=auth("owner")
    )
    assert res.status_code == 201
    mid = res.json()["id"]
    assert (await c.get("/core/resumes", headers=auth("viewer"))).status_code == 200
    assert (await c.get(f"/core/resumes/{mid}", headers=auth("viewer"))).status_code == 200
    for method, path, body in (
        ("POST", "/core/resumes", {"kind": "master"}),
        ("PATCH", f"/core/resumes/{mid}", {"blind": True}),
        ("DELETE", f"/core/resumes/{mid}", None),
        (
            "POST",
            f"/core/resumes/{mid}/revisions",
            {"base_revision": 1, "operations": [{"op": "fill_all"}]},
        ),
    ):
        assert (await c.request(method, path, json=body, headers=auth("viewer"))).status_code == 403
    # 편집자는 쓸 수 있지만 줄 다듬기(모델 호출)는 egress가 없어 못 한다
    patch = await c.patch(
        f"/core/resumes/{mid}", json={"title": "편집자 제목"}, headers=auth("editor")
    )
    assert patch.status_code == 200 and patch.json()["summary"]["title"] == "편집자 제목"
    sug = await c.post(
        f"/core/resumes/{mid}/suggestions", json={"base_revision": 1}, headers=auth("editor")
    )
    assert sug.status_code == 403
    sug = await c.post(
        f"/core/resumes/{mid}/suggestions", json={"base_revision": 1}, headers=auth("owner")
    )
    assert (sug.status_code, sug.json()["detail"]) == (503, "models_not_configured")
    assert (
        await c.post("/core/resumes", json={"kind": "master"}, headers=auth("owner"))
    ).status_code == 409
    # 다른 공간
    assert (await c.get("/core/resumes", headers=auth("other"))).json() == []
    for method, path in (
        ("GET", f"/core/resumes/{mid}"),
        ("GET", f"/core/resumes/{mid}/check"),
        ("GET", f"/core/resumes/{mid}/preview"),
        ("GET", f"/core/resumes/{mid}/picker"),
        ("GET", f"/core/resumes/{mid}/export?format=json"),
        ("DELETE", f"/core/resumes/{mid}"),
    ):
        assert (await c.request(method, path, headers=auth("other"))).status_code == 404, path
    res = await c.post(
        "/core/resumes", json={"kind": "role", "parent_id": mid}, headers=auth("other")
    )
    assert res.status_code == 404
    # 다른 공간의 기록은 고를 수 없다 (없는 기록)
    other_master = await uc.create_version(db, env["other"], kind="master")
    with pytest.raises(uc.ResumeRuleError):
        await uc.save(
            db,
            env["other"],
            other_master,
            base_revision=0,
            operations=[
                {
                    "op": "add_item",
                    "section": "work",
                    "ref": {"type": "experience", "id": str(env["work"])},
                }
            ],
        )
    picker = (await c.get(f"/core/resumes/{mid}/picker", headers=auth("owner"))).json()
    work = next(e for e in picker["experiences"] if e["id"] == str(env["work"]))
    assert work["added"] is True
    assert str(env["pending"]) not in {x["id"] for x in work["claims"]}
    assert str(env["child_claim"]) in {x["id"] for x in work["claims"]}  # 아래 경험의 사실도


async def mcp_call(c, token, name, arguments):
    res = await c.post(
        "/core/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=auth(token),
    )
    assert res.status_code == 200, res.text
    return res.json()["result"]


async def test_mcp_parity_and_restricted_profile(db, env):
    c, owner = env["c"], env["owner"]
    made = await mcp_call(
        c, "owner", "resume_create_version", {"kind": "master", "fill": True, "fill_claims": True}
    )
    assert not made["isError"], made
    mid = made["structuredContent"]["id"]
    listed = await mcp_call(c, "viewer", "resume_list", {})
    http = (await c.get("/core/resumes", headers=auth("viewer"))).json()
    assert [v["id"] for v in listed["structuredContent"]["versions"]] == [v["id"] for v in http]
    assert listed["structuredContent"]["untrusted_content"] is True
    got = (await mcp_call(c, "viewer", "resume_get", {"version_id": mid}))["structuredContent"]
    text = json.dumps(got, ensure_ascii=False)
    for secret in ("amy@example.test", "010-0000-0000", ADDRESS, "1994-07-21", str(env["photo"])):
        assert secret not in text
    [profile] = next(s for s in got["sections"] if s["kind"] == "profile")["items"]
    assert set(profile["fields"]) == {"name", "headline", "links"}
    assert SCHOOL in text  # 블라인드가 아니면 학교는 보인다
    await uc.update_settings(db, owner, UUID(mid), {"blind": True})
    got = (await mcp_call(c, "viewer", "resume_get", {"version_id": mid}))["structuredContent"]
    assert SCHOOL not in json.dumps(got, ensure_ascii=False) and got["blind"] is True
    # 같은 정책: 보기 전용은 쓰지 못한다 (HTTP와 같이)
    work = next(
        it
        for s in got["sections"]
        for it in s["items"]
        if it.get("ref") == {"type": "experience", "id": str(env["work"])}
    )
    edit = {
        "version_id": mid,
        "base_revision": 1,
        "operations": [{"op": "add_line", "item_id": work["id"], "text": "에이전트가 쓴 줄"}],
    }
    denied = await mcp_call(c, "viewer", "resume_edit", edit)
    assert denied["isError"] and denied["structuredContent"]["error"] == "forbidden"
    ok = await mcp_call(c, "editor", "resume_edit", edit)
    assert not ok["isError"] and ok["structuredContent"]["revision_no"] == 2
    stale = await mcp_call(c, "editor", "resume_edit", edit)
    assert stale["structuredContent"]["error"] == "revision_conflict"
    view = await uc.get_version(db, owner, UUID(mid))
    line = next(
        ln
        for ln in by_ref(view, "experience", env["work"])["lines"]
        if ln["text"] == "에이전트가 쓴 줄"
    )
    assert (line["origin"], line["claim_id"]) == ("ai", None)
    async with db.workspace_session(owner.workspace_id) as s:
        rev = await s.scalar(
            select(ResumeRevision).where(
                ResumeRevision.version_id == UUID(mid), ResumeRevision.revision_no == 2
            )
        )
    assert rev.origin == "agent"
    # 에이전트가 사실 줄의 글을 바꿔도 ai로 남는다
    claim_line = next(
        ln for ln in by_ref(view, "experience", env["work"])["lines"] if ln["claim_id"]
    )
    res = await mcp_call(
        c,
        "editor",
        "resume_edit",
        {
            "version_id": mid,
            "base_revision": 2,
            "operations": [
                {
                    "op": "update_line",
                    "line_id": claim_line["id"],
                    "text": "다듬은 문장",
                    "origin": "user",
                }
            ],
        },
    )
    assert not res["isError"]
    view = await uc.get_version(db, owner, UUID(mid))
    changed = {ln["id"]: ln for ln in by_ref(view, "experience", env["work"])["lines"]}[
        claim_line["id"]
    ]
    assert changed["origin"] == "ai"


# ---------------------------------------------------------------- 내보내기·복원


async def test_export_restore_roundtrip(db, env, tmp_path):
    owner = env["owner"]
    mid = await master(db, env)
    rid = await uc.create_version(db, owner, kind="role", parent_id=mid, target_role="백엔드")
    pid = await uc.create_version(
        db, owner, kind="posting", parent_id=rid, application_id=env["application"]
    )
    await uc.update_settings(db, owner, pid, {"blind": True, "template": "korean_table"})
    out = tmp_path / "a.tar.gz"
    report = await export_workspace(db, env["blobs"], owner, out)
    with tarfile.open(out, "r:gz") as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())
    rows = {t["name"]: t["rows"] for t in manifest["tables"]}
    assert (rows["core.resume_versions"], rows["core.resume_revisions"]) == (3, 3)
    assert rows["core.resume_line_claims"] == 9
    copy = await restore_workspace(db, LocalBlobStore(tmp_path / "r"), out, new_ids=True)
    assert copy.rows_total == report.rows_total
    [new_owner] = copy.owners
    actor = ActorContext(new_owner, copy.workspace_id, scopes_for_role(Role.OWNER))
    versions = {v.kind: v for v in await uc.list_versions(db, actor)}
    assert set(versions) == {"master", "role", "posting"}
    assert versions["master"].id != mid
    assert versions["role"].parent_id == versions["master"].id
    assert versions["posting"].parent_id == versions["role"].id
    assert versions["posting"].blind is True and versions["posting"].template == "korean_table"
    assert versions["posting"].application_id not in (None, env["application"])
    view = await uc.get_version(db, actor, versions["role"].id)
    assert view.summary.source_revision_no == 1
    # 근거 연결이 새 공간의 사실을 가리키고 상태가 그대로 ok
    lines = [ln for s in view.content["sections"] for it in s["items"] for ln in it["lines"]]
    assert lines and all(ln["state"] == "ok" for ln in lines if ln["claim_id"])
    html = (
        await uc.export_version(
            db, LocalBlobStore(tmp_path / "r"), actor, versions["posting"].id, fmt="html"
        )
    ).data
    assert SCHOOL.encode() not in html


# ---------------------------------------------------------------- 줄 다듬기 (모델은 가짜, 네트워크 없음)

CONFIG = parse_models_config("""
[endpoints.proxy]
preset = "cliproxyapi"
price_as = "openai"
data_classes = ["public", "private"]
[routes]
default = "proxy/gpt-6.1-sol"
""")


class SuggestingLLM:
    def __init__(self):
        self.requests = []

    async def complete(self, model, request):
        self.requests.append(request)
        body = request.messages[0].content
        data = json.loads(body.split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])
        out = []
        for p in data["paragraphs"]:
            if "800ms" in p["text"]:
                # 근거에 없는 수치 → 거절
                out.append(
                    {"block_id": p["block_id"], "text": "주문 API 지연을 90% 줄였다", "reason": "x"}
                )
            else:
                out.append(
                    {"block_id": p["block_id"], "text": p["text"] + " (다듬음)", "reason": "y"}
                )
        return ModelCompletion(
            text=json.dumps({"suggestions": out, "questions": []}, ensure_ascii=False),
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, TokenUsage(input_tokens=100, output_tokens=50)),),
        )


async def test_line_suggestions_use_document_path_and_are_not_stored(db, env):
    owner = env["owner"]
    llm = SuggestingLLM()
    gw = ModelGateway(
        db,
        CONFIG,
        PriceBook.load(),
        client_factory=lambda ep, key: llm,
        env={"CLIPROXY_API_KEY": "k"},
    )
    mid = await master(db, env)
    view = await uc.get_version(db, owner, mid)
    work = by_ref(view, "experience", env["work"])
    target = {ln["claim_id"]: ln["id"] for ln in work["lines"]}
    with pytest.raises(uc.ResumeConflict):
        await suggest_uc.suggest_lines(db, gw, owner, mid, base_revision=0)
    res = await suggest_uc.suggest_lines(
        db, gw, owner, mid, base_revision=1, line_ids=list(target.values())
    )
    status = {x.line_id: (x.status, [r["code"] for r in x.reject_reasons]) for x in res.suggestions}
    assert status[target[str(env["result"])]] == ("rejected", ["number"])
    assert status[target[str(env["action"])]][0] == "proposed"
    sent = llm.requests[0].messages[0].content
    # 프로필(연락처·주소·생년월일)은 모델로 가지 않는다
    for secret in ("amy@example.test", ADDRESS, "1994", "김합성"):
        assert secret not in sent
    # 저장하지 않는다
    assert (await uc.get_version(db, owner, mid)).revision_no == 1
    with pytest.raises(uc.ResumeRuleError, match="unknown_line"):
        await suggest_uc.suggest_lines(
            db, gw, owner, mid, base_revision=1, line_ids=["lnope000000"]
        )

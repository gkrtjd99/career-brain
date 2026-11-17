"""브레인 "나" 보강: 학력(c0040)·프로필 더하기(c0041)·기술 보기·공통 답변(c0042)·지난 제출본.

- 학력: 만들기·고치기·지우기, 학점 ≤ 만점(usecase와 DB CHECK), 자격·이수의 옛 학력 기록 옮기기.
- 프로필 더하기: 병역 칸 사이 규칙, restricted — MCP 도구 결과·모델 호출 경로에 없다.
- 기술 보기: 태그·확인한 "사용한 기술"·자격증에서 모으기, 기본 별칭 표로 묶기, 합치기·나누기.
- 공통 답변: 확인한 사실만 근거, 지원서에 넣기(끝에 더하기·새 문서·충돌 409·dry_run)와 쓴 곳 기록.
- 지난 제출본: 고정한 제출본만, 다른 회사 이름 경고, 이 글로 시작(공통 답변·다른 지원).
- 다른 공간 0건, 내보내기·복원 왕복, MCP 읽기 도구.

모든 자료는 합성이다.
"""

import json
import tarfile
from datetime import date
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.db import JobPosting, JobPostingVersion, ModelCall
from career_core.db.models import AnswerAssetUse, Credential, Education, Profile
from career_core.domain.blind import blind_sensitive_fields
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.ports import ActorContext
from career_core.usecases import answers as answers_uc
from career_core.usecases import applications as app_uc
from career_core.usecases import brain
from career_core.usecases import education as education_uc
from career_core.usecases import postings as posting_uc
from career_core.usecases import profiles as profile_uc
from career_core.usecases import skills as skills_uc
from career_core.usecases.export import export_workspace, restore_workspace
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.anyio


class FakeResolver:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


async def saved_posting(db, actor, *, company, title, url, questions=()):
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
                text="합성 공고 본문",
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
    exp = await brain.create_experience(
        db,
        owner,
        title="주문 서비스 개선",
        organization="가상커머스",
        kind="work",
        started_on="2021-03",
        ended_on="2023-02",
        tags=["Python", "FastAPI"],
    )
    side = await brain.create_experience(
        db,
        owner,
        title="사이드 프로젝트",
        kind="project",
        started_on="2024-01",
        ongoing=True,
        tags=["파이썬", "py3"],
    )
    confirmed = await brain.add_claim(
        db,
        owner,
        exp,
        statement="Kubernetes 클러스터 운영",
        kind="skill",
        contribution="personal",
    )
    result = await brain.add_claim(
        db,
        owner,
        exp,
        statement="주문 API 지연을 800ms에서 200ms로 줄였다",
        kind="result",
        contribution="personal",
    )
    # 편집자가 쓴 주장은 확인 전(extracted)
    pending = await brain.add_claim(
        db, editor, side, statement="Kafka로 이벤트 처리", kind="skill", contribution="personal"
    )
    other_exp = await brain.create_experience(db, other, title="B의 경험")
    foreign = await brain.add_claim(
        db, other, other_exp, statement="B 공간의 주장", kind="other", contribution="personal"
    )
    posting = await saved_posting(
        db,
        owner,
        company="(주)가상회사",
        title="백엔드 개발자",
        url="https://jobs.example.test/1",
        questions=[{"prompt": "지원 동기를 작성해 주세요. (500자 이내)", "char_limit": 500}],
    )
    other_posting = await saved_posting(
        db, owner, company="다른회사", title="서버 개발자", url="https://jobs.example.test/2"
    )
    tokens = {"owner": owner, "editor": editor, "viewer": viewer, "other": other}
    app = FastAPI()
    app.include_router(build_router(CoreDeps(identity=FakeResolver(tokens), db=db, blobs=blobs)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        yield {
            "c": c,
            "owner": owner,
            "editor": editor,
            "other": other,
            "blobs": blobs,
            "exp": exp,
            "side": side,
            "confirmed": confirmed,
            "result": result,
            "pending": pending,
            "foreign": foreign,
            "posting": posting,
            "other_posting": other_posting,
        }


# ---------------------------------------------------------------- 학력


async def test_education_crud_and_rules(db, env):
    c = env["c"]
    body = {
        "school": "  가상대학교 ",
        "campus": "서울",
        "major": "컴퓨터공학",
        "extra_major": "경영학",
        "extra_major_kind": "double",
        "degree": "bachelor",
        "status": "graduated",
        "transferred": True,
        "entered_on": "2015-03",
        "graduated_on": "2021-02-25",
        "gpa": 3.82,
        "gpa_scale": 4.5,
        "experience_id": str(env["exp"]),
    }
    res = await c.post("/core/education", json=body, headers=auth("owner"))
    assert res.status_code == 201, res.text
    eid = res.json()["id"]
    got = (await c.get(f"/core/education/{eid}", headers=auth("owner"))).json()
    assert got["school"] == "가상대학교" and got["graduated_on"] == "2021-02-01"
    assert (got["gpa"], got["gpa_scale"], got["transferred"]) == (3.82, 4.5, True)
    assert got["experience_title"] == "주문 서비스 개선"
    # 고등학교는 전공 없이
    hs = await c.post(
        "/core/education",
        json={"school": "가상고등학교", "degree": "high_school", "graduated_on": "2015-02"},
        headers=auth("owner"),
    )
    assert hs.status_code == 201
    rows = (await c.get("/core/education", headers=auth("owner"))).json()
    assert [r["school"] for r in rows] == ["가상대학교", "가상고등학교"]  # 최근 학교부터

    for patch, code in (
        ({"gpa": 4.6}, "gpa_over_scale"),
        ({"gpa": 4.4, "gpa_scale": 4.3}, "gpa_over_scale"),
        ({"gpa_scale": None}, "gpa_needs_scale"),
        ({"gpa": 3.5, "gpa_scale": 5}, "invalid_gpa_scale"),
        ({"entered_on": "2022-03"}, "graduated_before_entered"),
        ({"extra_major_kind": None}, "extra_major_needs_kind"),
        ({"school": "   "}, "school_required"),
        ({"source_id": str(uuid4())}, "source_not_found"),
        ({"degree": None}, "invalid_degree"),
        ({"entered_on": "2020-13"}, "invalid_month"),
    ):
        res = await c.patch(f"/core/education/{eid}", json=patch, headers=auth("owner"))
        assert (res.status_code, res.json()["detail"]) == (422, code), patch
    # 100점 만점 학점, 비우기
    res = await c.patch(
        f"/core/education/{eid}", json={"gpa": 91.5, "gpa_scale": 100}, headers=auth("owner")
    )
    assert (res.json()["gpa"], res.json()["gpa_scale"]) == (91.5, 100.0)
    res = await c.patch(
        f"/core/education/{eid}",
        json={"gpa": None, "gpa_scale": None, "campus": None, "status": "completed"},
        headers=auth("owner"),
    )
    assert res.json()["gpa"] is None and res.json()["campus"] is None
    assert res.json()["status"] == "completed"
    # DB도 학점 > 만점을 받지 않는다
    with pytest.raises(IntegrityError):
        async with db.workspace_session(env["owner"].workspace_id) as s:
            s.add(
                Education(
                    workspace_id=env["owner"].workspace_id,
                    school="직접 넣기",
                    degree="bachelor",
                    status="graduated",
                    gpa=4.4,
                    gpa_scale=4.3,
                    created_by=env["owner"].subject_id,
                )
            )
    # 권한·다른 공간
    assert (await c.get("/core/education", headers=auth("viewer"))).status_code == 200
    assert (await c.post("/core/education", json=body, headers=auth("viewer"))).status_code == 403
    assert (await c.get("/core/education", headers=auth("other"))).json() == []
    assert (await c.get(f"/core/education/{eid}", headers=auth("other"))).status_code == 404
    assert (await c.delete(f"/core/education/{eid}", headers=auth("other"))).status_code == 404
    assert (await c.delete(f"/core/education/{eid}", headers=auth("owner"))).status_code == 204
    assert len((await c.get("/core/education", headers=auth("owner"))).json()) == 1


async def test_degree_credential_moves_to_education(db, env):
    c = env["c"]
    made = await c.post(
        "/core/credentials",
        json={
            "kind": "degree",
            "name": "석사 학위증명서",
            "issuer": "가상대학교 대학원",
            "acquired_on": "2023-08-25",
            "started_on": "2021-03-02",
            "detail": "합성 메모",
        },
        headers=auth("owner"),
    )
    assert made.status_code == 201, made.text
    cid = made.json()["id"]
    lic = (
        await c.post(
            "/core/credentials",
            json={"kind": "license", "name": "정보처리기사", "acquired_on": "2022-06-10"},
            headers=auth("owner"),
        )
    ).json()["id"]
    res = await c.post(f"/core/credentials/{lic}/to-education", headers=auth("owner"))
    assert (res.status_code, res.json()["detail"]) == (409, "credential_not_degree")
    assert (
        await c.post(f"/core/credentials/{cid}/to-education", headers=auth("other"))
    ).status_code == 404
    res = await c.post(f"/core/credentials/{cid}/to-education", headers=auth("owner"))
    assert res.status_code == 200, res.text
    got = res.json()
    assert got["school"] == "가상대학교 대학원" and got["degree"] == "master"
    assert (got["status"], got["entered_on"], got["graduated_on"]) == (
        "graduated",
        "2021-03-01",
        "2023-08-01",
    )
    assert "석사 학위증명서" in got["note"] and "합성 메모" in got["note"]
    # 원래 기록은 지워졌다 (한 번만 옮긴다)
    assert (await c.get(f"/core/credentials/{cid}", headers=auth("owner"))).status_code == 404
    again = await c.post(f"/core/credentials/{cid}/to-education", headers=auth("owner"))
    assert again.status_code == 404
    async with db.workspace_session(env["owner"].workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(Credential)) == 1


# ---------------------------------------------------------------- 프로필 더하기


async def test_profile_details_rules_and_restricted(db, env):
    c = env["c"]
    empty = (await c.get("/core/profile/details", headers=auth("owner"))).json()
    assert empty["military_status"] is None and empty["desired_roles"] == []
    res = await c.patch(
        "/core/profile/details",
        json={
            "military_status": "served",
            "military_branch": "육군",
            "military_rank": "병장",
            "military_started_on": "2016-03",
            "military_ended_on": "2017-12",
            "veteran": False,
            "desired_roles": ["백엔드 개발", " 백엔드 개발 ", "데이터 엔지니어"],
            "desired_locations": ["서울", "판교"],
            "desired_salary": "합성연봉 협의 가능",
            "available_from": "즉시",
            "employment_types": ["intern", "regular", "regular"],
        },
        headers=auth("owner"),
    )
    assert res.status_code == 200, res.text
    got = res.json()
    assert got["military_started_on"] == "2016-03-01"
    assert got["desired_roles"] == ["백엔드 개발", "데이터 엔지니어"]
    assert got["employment_types"] == ["regular", "intern"]
    assert got["disability"] is None  # 적지 않은 칸은 그대로 비어 있다
    # 프로필의 다른 칸은 그대로 (PUT /profile과 따로 저장한다)
    await c.put("/core/profile", json={"name": "김합성"}, headers=auth("owner"))
    assert (await c.get("/core/profile/details", headers=auth("owner"))).json()[
        "military_branch"
    ] == "육군"
    # 면제로 바꾸면 복무 칸은 비우고 면제 사유만
    res = await c.patch(
        "/core/profile/details",
        json={"military_status": "exempt", "military_exemption_reason": "합성 사유"},
        headers=auth("owner"),
    )
    got = res.json()
    assert got["military_branch"] is None and got["military_started_on"] is None
    assert got["military_exemption_reason"] == "합성 사유"
    res = await c.patch(
        "/core/profile/details", json={"military_status": "not_served"}, headers=auth("owner")
    )
    assert res.json()["military_exemption_reason"] is None
    for patch, code in (
        (
            {
                "military_status": "served",
                "military_started_on": "2018-01",
                "military_ended_on": "2017-01",
            },
            "invalid_military_period",
        ),
        ({"desired_roles": [f"직무{i}" for i in range(11)]}, "invalid_desired_roles"),
        ({"desired_locations": ["가" * 51]}, "invalid_desired_locations"),
        ({"military_started_on": "2018-13"}, "invalid_military_period"),
    ):
        res = await c.patch("/core/profile/details", json=patch, headers=auth("owner"))
        assert (res.status_code, res.json()["detail"]) == (422, code), patch
    assert (
        await c.patch("/core/profile/details", json={}, headers=auth("viewer"))
    ).status_code == 403
    assert (await c.get("/core/profile/details", headers=auth("other"))).json()[
        "desired_roles"
    ] == []
    await c.patch(
        "/core/profile/details",
        json={"military_status": "served", "military_branch": "육군", "disability": True},
        headers=auth("owner"),
    )

    # MCP: 어떤 읽기 도구에도 병역·희망 조건이 나오지 않는다 (profile_summary 포함)
    async def call(name, arguments=None):
        res = await c.post(
            "/core/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            },
            headers=auth("owner"),
        )
        return res.json()["result"]

    for name in ("profile_summary", "education_list", "skills_overview", "answer_asset_list"):
        raw = json.dumps(await call(name), ensure_ascii=False)
        for secret in (
            "육군",
            "합성연봉",
            "판교",
            "데이터 엔지니어",
            "military",
            "desired",
            "disab",
        ):
            assert secret not in raw, (name, secret)
    summary = (await call("profile_summary"))["structuredContent"]
    assert set(summary) >= {"name", "headline", "links"}
    # 모델 호출이 없다
    async with db.workspace_session(env["owner"].workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(ModelCall)) == 0


def test_no_model_path_reads_restricted_profile_fields():
    """모델 요청을 만드는 코드는 프로필(ORM)·병역·희망 조건 칸을 읽지 않는다 (정적 확인)."""
    root = Path(__file__).resolve().parents[1] / "src" / "career_core"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "ModelRequest(" not in text:
            continue
        for word in ("Profile,", "Profile)", "military_", "desired_", "profile_details"):
            if word in text:
                offenders.append((path.name, word))
    assert not offenders


def test_blind_fields_point_at_real_columns():
    from career_core.db.models import Credential as Cred
    from career_core.db.models import Education as Edu

    tables = {"profiles": Profile, "education": Edu, "credentials": Cred}
    for item in blind_sensitive_fields(include_optional=True):
        table, _, column = item.partition(".")
        table = table.split("[")[0]
        if column == "*":
            continue
        assert column in tables[table].__table__.columns, item


# ---------------------------------------------------------------- 기술 보기


async def test_skills_overview_aggregates_and_merges(db, env):
    c, owner = env["c"], env["owner"]
    await c.post(
        "/core/credentials",
        json={"kind": "license", "name": "정보처리기사", "acquired_on": "2022-06-10"},
        headers=auth("owner"),
    )
    await c.post(
        "/core/credentials",
        json={"kind": "award", "name": "합성 해커톤 대상"},
        headers=auth("owner"),
    )
    skills = {s["key"]: s for s in (await c.get("/core/skills", headers=auth("owner"))).json()}
    py = skills["python"]
    # "Python" 태그와 "파이썬" 태그는 기본 별칭 표로 한 기술
    assert py["name"] == "Python" and py["count"] == 2
    assert {e["title"] for e in py["experiences"]} == {"주문 서비스 개선", "사이드 프로젝트"}
    assert (py["first_year"], py["last_year"]) == (2021, date.today().year)  # 진행 중이면 올해
    assert set(py["spellings"]) == {"Python", "파이썬"}
    # 확인한 "사용한 기술" 문장에서 기술명 (확인 전 Kafka는 빠진다)
    k8s = skills["kubernetes"]
    assert k8s["claims"] == 1 and k8s["experiences"][0]["via"] == ["claim"]
    assert "kafka" not in skills
    # 자격증은 기술로, 수상은 아니다
    assert skills["정보처리기사"]["credentials"][0]["name"] == "정보처리기사"
    assert skills["정보처리기사"]["last_year"] == 2022
    assert "합성 해커톤 대상" not in skills
    assert skills["py3"]["count"] == 1

    # 합치기: py3 → Python (기술명 별칭 표에 사용자 항목)
    res = await c.post(
        "/core/skills/merge", json={"into": "Python", "names": ["py3"]}, headers=auth("owner")
    )
    assert res.json() == {"name": "Python"}
    skills = {s.key: s for s in await skills_uc.skills_overview(db, owner)}
    assert "py3" not in skills and skills["python"].merged == ["py3"]
    assert skills["python"].count == 2  # 같은 경험이라 수는 그대로, 표기만 더해진다
    assert "py3" in skills["python"].spellings
    settings = (await c.get("/core/workspace-settings", headers=auth("owner"))).json()
    assert settings["tech_aliases"] == [{"term": "Python", "aliases": ["py3"]}]
    # 다시 합쳐도 그대로, 기본 표의 별칭("파이썬")은 이미 같은 기술
    await skills_uc.merge_skills(db, owner, "python", ["py3", "파이썬"])
    assert (await c.get("/core/workspace-settings", headers=auth("owner"))).json()[
        "tech_aliases"
    ] == [{"term": "Python", "aliases": ["py3"]}]
    # 나누기
    assert (
        await c.post("/core/skills/split", json={"name": "py3"}, headers=auth("owner"))
    ).status_code == 204
    assert "py3" in {s.key for s in await skills_uc.skills_overview(db, owner)}
    res = await c.post("/core/skills/split", json={"name": "py3"}, headers=auth("owner"))
    assert (res.status_code, res.json()["detail"]) == (422, "not_merged")
    # 권한: 보기는 읽기만, 합치기는 설정 쓰기
    assert (await c.get("/core/skills", headers=auth("viewer"))).status_code == 200
    res = await c.post(
        "/core/skills/merge", json={"into": "Python", "names": ["py3"]}, headers=auth("viewer")
    )
    assert res.status_code == 403
    assert (await c.get("/core/skills", headers=auth("other"))).json() == []


# ---------------------------------------------------------------- 공통 답변


async def test_answer_assets_link_only_confirmed_claims_and_record_use(db, env):
    c, owner = env["c"], env["owner"]
    body = {
        "kind": "conflict",
        "title": "갈등을 푼 경험",
        "body": "배포 방식으로 팀과 의견이 갈렸습니다.\n\n지표를 함께 보고 합의했습니다.",
        "claim_ids": [str(env["result"])],
    }
    for ids, code in (
        ([str(env["pending"])], "claim_not_confirmed"),
        ([str(env["foreign"])], "unknown_claim"),
        ([str(uuid4())], "unknown_claim"),
    ):
        res = await c.post(
            "/core/answer-assets", json={**body, "claim_ids": ids}, headers=auth("owner")
        )
        assert (res.status_code, res.json()["detail"]) == (422, code)
    res = await c.post("/core/answer-assets", json=body, headers=auth("owner"))
    assert res.status_code == 201, res.text
    aid = res.json()["id"]
    got = (await c.get(f"/core/answer-assets/{aid}", headers=auth("owner"))).json()
    assert got["chars"] == len(
        "배포 방식으로 팀과 의견이 갈렸습니다.\n\n지표를 함께 보고 합의했습니다."
    )
    assert got["chars_without_spaces"] < got["chars"]
    assert [x["statement"] for x in got["claims"]] == ["주문 API 지연을 800ms에서 200ms로 줄였다"]
    assert got["claims"][0]["confirmed"] is True and got["uses"] == []
    [summary] = (await c.get("/core/answer-assets", headers=auth("owner"))).json()
    assert (summary["claims"], summary["uses"], summary["kind"]) == (1, 0, "conflict")
    # 근거 바꾸기도 확인한 사실만
    res = await c.patch(
        f"/core/answer-assets/{aid}",
        json={"claim_ids": [str(env["pending"])]},
        headers=auth("owner"),
    )
    assert res.json()["detail"] == "claim_not_confirmed"
    res = await c.patch(
        f"/core/answer-assets/{aid}",
        json={"claim_ids": [str(env["result"]), str(env["confirmed"])], "title": "갈등 경험"},
        headers=auth("owner"),
    )
    assert len(res.json()["claims"]) == 2 and res.json()["title"] == "갈등 경험"

    # 지원서에 넣기: 지원 문항 끝에 더한다
    app_id = await app_uc.create_application(db, owner, env["posting"])
    view = await app_uc.get_application(db, owner, app_id)
    doc_id = view.documents[0].id
    place = {"application_id": str(app_id), "document_id": str(doc_id), "base_revision": 0}
    dry = await c.post(
        f"/core/answer-assets/{aid}/use", json={**place, "dry_run": True}, headers=auth("owner")
    )
    assert dry.status_code == 200 and dry.json()["revision_no"] is None
    assert (await app_uc.get_document(db, owner, app_id, doc_id)).current_revision == 0
    res = await c.post(f"/core/answer-assets/{aid}/use", json=place, headers=auth("owner"))
    assert res.status_code == 200, res.text
    assert (res.json()["revision_no"], res.json()["created_document"]) == (1, False)
    doc = await app_uc.get_document(db, owner, app_id, doc_id)
    assert [b.text for b in doc.blocks] == [
        "배포 방식으로 팀과 의견이 갈렸습니다.",
        "지표를 함께 보고 합의했습니다.",
    ]
    assert set(doc.blocks[0].claim_ids) == {str(env["result"]), str(env["confirmed"])}
    assert {b.origin for b in doc.blocks} == {"user"}
    # 오래된 revision으로 넣으면 409
    stale = await c.post(f"/core/answer-assets/{aid}/use", json=place, headers=auth("owner"))
    assert stale.status_code == 409 and stale.json()["detail"]["current_revision"] == 1
    # 새 문서로 넣기 (문항 문구가 있으면 공고 문항 문서), 편집자도 넣을 수 있다
    res = await c.post(
        f"/core/answer-assets/{aid}/use",
        json={"application_id": str(app_id), "question_prompt": "갈등 경험 (300자)"},
        headers=auth("editor"),
    )
    assert res.json()["created_document"] is True
    new_doc = await app_uc.get_document(db, owner, app_id, res.json()["document_id"])
    assert (new_doc.kind, new_doc.char_limit) == ("question", 300)
    got = (await c.get(f"/core/answer-assets/{aid}", headers=auth("owner"))).json()
    assert len(got["uses"]) == 2 and got["uses"][0]["application_title"] == view.title
    # 보기 권한은 넣을 수 없다, 다른 공간은 답변을 못 본다
    assert (
        await c.post(f"/core/answer-assets/{aid}/use", json=place, headers=auth("viewer"))
    ).status_code == 403
    assert (await c.get(f"/core/answer-assets/{aid}", headers=auth("other"))).status_code == 404
    assert (await c.get("/core/answer-assets", headers=auth("other"))).json() == []
    res = await c.post(
        f"/core/answer-assets/{aid}/use",
        json={"application_id": str(app_id)},
        headers=auth("other"),
    )
    assert res.status_code == 404
    # 지우면 쓴 곳 기록도 함께, 넣은 글은 그대로
    assert (await c.delete(f"/core/answer-assets/{aid}", headers=auth("owner"))).status_code == 204
    async with db.workspace_session(owner.workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(AnswerAssetUse)) == 0
    assert (await app_uc.get_document(db, owner, app_id, doc_id)).current_revision == 1


async def test_answer_use_warns_about_other_company_names(db, env):
    owner = env["owner"]
    aid = await answers_uc.create_answer(
        db,
        owner,
        kind="motivation",
        title="지원 동기 틀",
        body="다른회사의 서비스를 쓰며 가상회사의 문제를 생각했습니다.",
    )
    app_id = await app_uc.create_application(db, owner, env["posting"])
    got = await answers_uc.use_answer(db, owner, aid, app_id, dry_run=True)
    # 이 지원의 회사(가상회사, 공고는 "(주)가상회사")는 빼고 다른 회사만
    assert got.company_mentions == ["다른회사"]
    other_app = await app_uc.create_application(db, owner, env["other_posting"])
    got = await answers_uc.use_answer(db, owner, aid, other_app, dry_run=True)
    assert got.company_mentions == ["(주)가상회사"]


# ---------------------------------------------------------------- 지난 제출본


async def _submitted_app(db, env, text):
    owner = env["owner"]
    app_id = await app_uc.create_application(db, owner, env["posting"])
    doc_id = (await app_uc.get_application(db, owner, app_id)).documents[0].id
    await app_uc.save_revision(
        db,
        owner,
        app_id,
        doc_id,
        base_revision=0,
        operations=[
            {"op": "insert", "text": line, "claim_ids": [str(env["result"])]}
            for line in text.split("\n")
        ],
    )
    sub = await app_uc.submit_application(db, owner, app_id, revisions={doc_id: 1})
    return app_id, doc_id, sub.id


async def test_submitted_documents_lists_only_frozen_and_starts_new_writing(db, env):
    c, owner = env["c"], env["owner"]
    text = "가상회사에 지원한 이유는 주문 지연을 줄인 경험 때문입니다.\n입사 후 결제 안정성을 높이겠습니다."
    app_id, doc_id, sub_id = await _submitted_app(db, env, text)
    # 제출하지 않은 지원의 문서는 나오지 않는다
    draft = await app_uc.create_application(db, owner, env["other_posting"])
    draft_doc = await app_uc.add_document(db, owner, draft, kind="other", title="초안")
    await app_uc.save_revision(
        db, owner, draft, draft_doc, base_revision=0, operations=[{"op": "insert", "text": "초안"}]
    )
    rows = (await c.get("/core/submitted-documents", headers=auth("owner"))).json()
    assert len(rows) == 1
    row = rows[0]
    assert (row["company"], row["role"], row["application_id"]) == (
        "(주)가상회사",
        "백엔드 개발자",
        str(app_id),
    )
    assert row["question"] == "지원 동기를 작성해 주세요. (500자 이내)"
    assert row["chars"] == len(text) and row["char_limit"] == 500
    view = (
        await c.get(f"/core/submitted-documents/{sub_id}/{doc_id}", headers=auth("owner"))
    ).json()
    assert view["text"] == text and view["company_mentions"] == ["(주)가상회사"]
    assert view["evidence"][0]["statement"] == "주문 API 지연을 800ms에서 200ms로 줄였다"
    assert view["blocks"][0]["claim_ids"] == [str(env["result"])]
    assert (await c.get("/core/submitted-documents", headers=auth("other"))).json() == []
    assert (
        await c.get(f"/core/submitted-documents/{sub_id}/{doc_id}", headers=auth("other"))
    ).status_code == 404
    assert (
        await c.get(f"/core/submitted-documents/{sub_id}/{uuid4()}", headers=auth("owner"))
    ).status_code == 404

    # 이 글로 시작 → 새 공통 답변 (근거는 확인한 사실만 따라온다)
    res = await c.post(
        f"/core/submitted-documents/{sub_id}/{doc_id}/to-answer",
        json={"kind": "motivation"},
        headers=auth("owner"),
    )
    assert res.status_code == 201, res.text
    started = res.json()
    assert started["company_mentions"] == ["(주)가상회사"] and started["claims_dropped"] == 0
    answer = (
        await c.get(f"/core/answer-assets/{started['answer_id']}", headers=auth("owner"))
    ).json()
    assert answer["title"] == "지원 동기를 작성해 주세요. (500자 이내)"
    assert answer["body"] == text and answer["source_submission_id"] == str(sub_id)
    assert [x["id"] for x in answer["claims"]] == [str(env["result"])]
    view = (
        await c.get(f"/core/submitted-documents/{sub_id}/{doc_id}", headers=auth("owner"))
    ).json()
    assert view["answers"] == [{"id": started["answer_id"], "title": answer["title"]}]

    # 이 글로 시작 → 다른 회사 지원 문서: 원래 회사 이름을 알린다
    target = await app_uc.create_application(db, owner, env["other_posting"])
    res = await c.post(
        f"/core/submitted-documents/{sub_id}/{doc_id}/use",
        json={"application_id": str(target), "question_prompt": "지원 동기 (700자)"},
        headers=auth("owner"),
    )
    assert res.status_code == 200, res.text
    assert res.json()["company_mentions"] == ["(주)가상회사"]
    copied = await app_uc.get_document(db, owner, target, res.json()["document_id"])
    assert [b.text for b in copied.blocks] == text.split("\n")
    assert copied.blocks[0].claim_ids == [str(env["result"])]
    # 제출한 지원에는 넣을 수 없다
    res = await c.post(
        f"/core/submitted-documents/{sub_id}/{doc_id}/use",
        json={"application_id": str(app_id)},
        headers=auth("owner"),
    )
    assert (res.status_code, res.json()["detail"]) == (409, "application_locked")


# ---------------------------------------------------------------- MCP·내보내기


async def test_mcp_read_tools(db, env):
    from career_core.interfaces.mcp.tools import TOOLS_BY_NAME

    c, owner = env["c"], env["owner"]
    for name, action in (
        ("education_list", "education.read"),
        ("skills_overview", "brain.read"),
        ("answer_asset_list", "answers.read"),
        ("answer_asset_get", "answers.read"),
    ):
        tool = TOOLS_BY_NAME[name]
        assert tool.read_only and tool.action == action
    await education_uc.create_education(
        db, owner, {"school": "가상대학교", "gpa": 3.5, "gpa_scale": 4.5}
    )
    aid = await answers_uc.create_answer(
        db, owner, kind="intro", title="1분 자기소개", body="합성 자기소개 본문입니다."
    )

    async def call(name, arguments=None, token="owner"):
        res = await c.post(
            "/core/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            },
            headers=auth(token),
        )
        return res.json()["result"]

    edu = (await call("education_list"))["structuredContent"]["education"]
    assert edu[0]["school"] == "가상대학교" and edu[0]["gpa"] in ("3.50", 3.5)
    listed = (await call("answer_asset_list", {"kind": "intro"}))["structuredContent"]["answers"]
    assert [a["title"] for a in listed] == ["1분 자기소개"]
    assert (await call("answer_asset_list", {"kind": "failure"}))["structuredContent"][
        "answers"
    ] == []
    got = (await call("answer_asset_get", {"answer_id": str(aid)}))["structuredContent"]
    assert got["body"] == "합성 자기소개 본문입니다."
    skills = (await call("skills_overview"))["structuredContent"]["skills"]
    assert "python" in {s["key"] for s in skills}
    # 다른 공간의 답변은 없음
    assert (await call("answer_asset_get", {"answer_id": str(aid)}, token="other"))["isError"]


async def test_export_restore_roundtrip(db, env, tmp_path):
    owner, blobs = env["owner"], env["blobs"]
    eid = await education_uc.create_education(
        db,
        owner,
        {
            "school": "가상대학교",
            "major": "컴퓨터공학",
            "gpa": "4.12",
            "gpa_scale": "4.3",
            "entered_on": "2015-03",
        },
    )
    await profile_uc.update_profile_details(
        db,
        owner,
        {"military_status": "served", "military_branch": "공군", "desired_roles": ["백엔드"]},
    )
    aid = await answers_uc.create_answer(
        db,
        owner,
        kind="aspiration",
        title="입사 후 포부",
        body="합성 포부",
        claim_ids=[env["result"]],
    )
    app_id = await app_uc.create_application(db, owner, env["posting"])
    await answers_uc.use_answer(db, owner, aid, app_id)

    out = tmp_path / "a.tar.gz"
    report = await export_workspace(db, blobs, owner, out)
    with tarfile.open(out, "r:gz") as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())
    rows = {t["name"]: t["rows"] for t in manifest["tables"]}
    assert (rows["core.education"], rows["core.answer_assets"]) == (1, 1)
    assert (rows["core.answer_asset_claims"], rows["core.answer_asset_uses"]) == (1, 1)

    copy = await restore_workspace(db, LocalBlobStore(tmp_path / "r"), out, new_ids=True)
    assert copy.rows_total == report.rows_total
    [new_owner] = copy.owners
    actor = ActorContext(new_owner, copy.workspace_id, scopes_for_role(Role.OWNER))
    [edu] = await education_uc.list_education(db, actor)
    assert edu.id != eid and (edu.school, str(edu.gpa), str(edu.gpa_scale)) == (
        "가상대학교",
        "4.12",
        "4.30",
    )
    assert edu.entered_on == date(2015, 3, 1)
    details = await profile_uc.get_profile_details(db, actor)
    assert (details.military_branch, details.desired_roles) == ("공군", ["백엔드"])
    [answer] = await answers_uc.list_answers(db, actor)
    assert answer.id != aid and (answer.claims, answer.uses) == (1, 1)
    full = await answers_uc.get_answer(db, actor, answer.id)
    assert full.claims[0].statement == "주문 API 지연을 800ms에서 200ms로 줄였다"
    assert full.uses[0].application_id != app_id

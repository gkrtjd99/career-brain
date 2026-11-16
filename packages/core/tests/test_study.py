"""O09 완료 기준: 공고 요구부터 학습 결과까지 연결, 계획과 경험 분리. (모든 데이터는 합성)"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from career_core.db import (
    Claim,
    Experience,
    StudyAttempt,
    StudyItem,
    StudyRequirement,
    StudyReview,
)
from career_core.domain.study import (
    AssessmentKind,
    ReviewGrade,
    ReviewState,
    StudyRuleError,
    classify_step,
    next_review,
    split_process,
)
from career_core.domain.workspace import Role, ScopePermissionPolicy, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.ports import ActorContext
from career_core.usecases import brain
from career_core.usecases import postings as postings_uc
from career_core.usecases import study as uc
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, ProgrammingError

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)

FIELDS = {
    "title": "백엔드 개발자 (합성)",
    "company": "가상회사",
    "requirements": ["Python 또는 Go 경험", "SQL 이해", "Docker 사용 경험"],
    "preferred": ["AWS 운영 경험"],
    "process": ["[전형 절차] 서류 - 코딩테스트 - 과제 전형 - 직무 면접"],
}


async def saved_posting(db, actor, url="https://jobs.example.test/b/1", fields=FIELDS):
    [r] = await postings_uc.save_urls(db, actor, [url])
    await postings_uc.confirm_posting(db, actor, r.posting_id, overrides=fields)
    return r.posting_id


async def count(db, actor, model):
    async with db.workspace_session(actor.workspace_id) as s:
        return await s.scalar(select(func.count()).select_from(model))


@pytest.fixture
async def env(db):
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    return {
        "owner": ActorContext(a, ws_a, scopes_for_role(Role.OWNER)),
        "editor": ActorContext(a, ws_a, scopes_for_role(Role.EDITOR)),
        "viewer": ActorContext(a, ws_a, scopes_for_role(Role.VIEWER)),
        "other": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
    }


# --- 규칙 ---------------------------------------------------------------------


def test_split_and_classify_process():
    assert split_process("[전형 절차] 서류 - 코딩테스트 - 과제 전형 - 직무 면접") == [
        "서류",
        "코딩테스트",
        "과제 전형",
        "직무 면접",
    ]
    assert split_process("1. 서류전형 → 2. 인적성검사 → 3. 면접") == [
        "서류전형",
        "인적성검사",
        "면접",
    ]
    assert split_process("take-home 과제 > 기술 면접") == ["take-home 과제", "기술 면접"]
    assert classify_step("코딩테스트") == AssessmentKind.CODING_TEST
    assert classify_step("take-home 과제") == AssessmentKind.ASSIGNMENT
    assert classify_step("인적성검사") == AssessmentKind.WRITTEN_TEST
    assert classify_step("직무 면접") == AssessmentKind.INTERVIEW
    assert classify_step("서류") is None  # 모르면 추측하지 않는다


def test_review_schedule_is_deterministic():
    s = ReviewState()
    days = []
    for g in (ReviewGrade.GOOD, ReviewGrade.GOOD, ReviewGrade.GOOD, ReviewGrade.GOOD):
        s = next_review(s, g)
        days.append(s.interval_days)
    assert days == [1, 3, 7, 17]
    assert s.ease == 2500
    s = next_review(s, ReviewGrade.AGAIN)
    assert (s.repetitions, s.interval_days, s.ease) == (0, 1, 2300)
    s = next_review(s, ReviewGrade.HARD)
    assert (s.repetitions, s.interval_days, s.ease) == (1, 1, 2150)
    s = next_review(s, ReviewGrade.EASY)
    assert (s.interval_days, s.ease) == (2, 2300)  # 1 × 2.15 × 1.3 = 2.795 → 내림 2
    # 배율 하한과 최대 간격
    low = ReviewState(5, 10, 1300)
    assert next_review(low, ReviewGrade.HARD).ease == 1300
    assert next_review(ReviewState(9, 300, 3000), ReviewGrade.GOOD).interval_days == 365
    # 같은 입력 → 같은 결과
    assert next_review(ReviewState(3, 7, 2500), ReviewGrade.GOOD) == next_review(
        ReviewState(3, 7, 2500), ReviewGrade.GOOD
    )


def test_study_scopes():
    policy = ScopePermissionPolicy()

    def can(role, action):
        return policy.allows(ActorContext(uuid4(), uuid4(), scopes_for_role(role)), action)

    assert can(Role.VIEWER, "study.read") and not can(Role.VIEWER, "study.write")
    assert can(Role.EDITOR, "study.write") and can(Role.OWNER, "study.write")


# --- 공고 요건 → 학습 → 결과 ---------------------------------------------------


async def test_chain_from_posting_requirement_to_result(db, env):
    owner = env["owner"]
    pid = await saved_posting(db, owner)

    # 이미 확정된 경험 주장이 있는 요건은 사용자가 직접 연결한다
    exp = await brain.create_experience(db, owner, title="가상 주문 시스템 (합성)")
    py_claim = await brain.add_claim(
        db,
        owner,
        exp,
        statement="Python으로 주문 API를 만들었다",
        kind="skill",
        contribution="personal",
    )
    await uc.link_claim(
        db, owner, pid, field="requirements", text="Python 또는 Go 경험", claim_id=py_claim
    )

    plan = await uc.plan_from_posting(db, owner, pid)
    assert len(plan.items_created) == 3  # SQL, Docker, AWS (Python은 확정 주장 있음)
    assert len(plan.assessments_created) == 2  # 코딩테스트, 과제 전형 (서류·면접 제외)
    again = await uc.plan_from_posting(db, owner, pid)
    assert (again.items_created, again.assessments_created) == ([], [])  # 다시 눌러도 그대로

    cov = await uc.posting_coverage(db, owner, pid)
    by_text = {r.text: r for r in cov.requirements}
    assert by_text["Python 또는 Go 경험"].coverage == "evidenced"
    assert by_text["SQL 이해"].coverage == "studying"
    assert by_text["AWS 운영 경험"].field == "preferred"
    proc = by_text[FIELDS["process"][0]]
    assert [(st.step, st.kind) for st in proc.steps] == [
        ("서류", None),
        ("코딩테스트", "coding_test"),
        ("과제 전형", "assignment"),
        ("직무 면접", "interview"),
    ]
    assert {a.title for a in proc.assessments} == {"코딩테스트", "과제 전형"}
    assert cov.counts == {
        "evidenced": 1,
        "claim_pending": 0,
        "studied": 0,
        "studying": 3,
        "uncovered": 0,
    }

    # 학습 기록·응시·오답
    docker = by_text["Docker 사용 경험"].items[0].id
    await uc.log_session(
        db,
        owner,
        docker,
        minutes=90,
        started_at=T0,
        notes="컨테이너 기초",
        resources=[{"title": "공식 문서 튜토리얼", "url": None}],
    )
    coding = next(a.id for a in proc.assessments if a.kind == "coding_test")
    attempt = await uc.record_attempt(
        db, owner, assessment_id=coding, result="failed", score=70, max_score=100, taken_at=T0
    )
    await uc.add_mistake(
        db, owner, attempt_id=attempt, question="합성 문제 3: 구간 합", reason="time", now=T0
    )
    practice = await uc.record_attempt(
        db, owner, study_item_id=docker, result="passed", taken_at=T0 + timedelta(hours=2)
    )
    assert await uc.update_item(db, owner, docker, status="done") == "done"

    item = await uc.get_item(db, owner, docker)
    assert item.requirement.text == "Docker 사용 경험"
    assert item.requirement.posting_title == FIELDS["title"]
    assert item.total_minutes == 90 and item.attempts[0].result == "passed"
    detail = await uc.get_assessment(db, owner, coding)
    assert detail.last_attempt.score == Decimal("70.00")
    assert detail.mistakes[0].reason == "time"

    cov = await uc.posting_coverage(db, owner, pid)
    by_text = {r.text: r for r in cov.requirements}
    assert by_text["Docker 사용 경험"].coverage == "studied"  # 학습함 ≠ 입증함
    proc = by_text[FIELDS["process"][0]]
    coding_row = next(a for a in proc.assessments if a.id == coding)
    assert coding_row.last_attempt.result == "failed"

    # 사용자가 직접 경험 후보로 올린다 → 확인 대기. 주인(approve)이어도 확정되지 않는다.
    claim_id = await uc.propose_claim(
        db,
        owner,
        docker,
        experience_id=exp,
        statement="Docker로 합성 서비스를 컨테이너화했다",
        attempt_id=practice,
    )
    async with db.workspace_session(owner.workspace_id) as s:
        claim = await s.get(Claim, claim_id)
        assert (claim.status, claim.contribution, claim.confirmed_at) == (
            "extracted",
            "unknown",
            None,
        )
    cov = await uc.posting_coverage(db, owner, pid)
    assert {r.text: r for r in cov.requirements}["Docker 사용 경험"].coverage == "claim_pending"
    assert (await uc.get_item(db, owner, docker)).claim_candidates[0].claim_id == claim_id

    # 확정은 Brain 확인 흐름에서만
    await brain.confirm_claim(db, owner, claim_id, contribution="personal")
    cov = await uc.posting_coverage(db, owner, pid)
    assert {r.text: r for r in cov.requirements}["Docker 사용 경험"].coverage == "evidenced"


async def test_plan_and_experience_are_separate(db, env):
    owner, editor = env["owner"], env["editor"]
    pid = await saved_posting(db, owner)
    exp = await brain.create_experience(db, owner, title="합성 경험")
    claims0 = await count(db, owner, Claim)
    exps0 = await count(db, owner, Experience)

    item = await uc.create_item(db, editor, posting_id=pid, field="requirements", text="SQL 이해")
    await uc.log_session(db, editor, item, minutes=30)
    att = await uc.record_attempt(
        db, editor, study_item_id=item, result="passed", score=10, max_score=10
    )
    await uc.add_mistake(
        db, editor, study_item_id=item, question="합성 JOIN 문제", reason="concept"
    )
    await uc.update_item(db, editor, item, status="done")
    await uc.plan_from_posting(db, editor, pid)
    # 학습 진행·응시·오답·완료는 주장·경험을 만들지 않는다
    assert await count(db, owner, Claim) == claims0
    assert await count(db, owner, Experience) == exps0

    # 결과가 없는 학습 항목은 후보로 올릴 수 없다
    todo = await uc.create_item(db, editor, title="합성 주제")
    with pytest.raises(StudyRuleError, match="no_study_result"):
        await uc.propose_claim(db, editor, todo, experience_id=exp, statement="합성 문장")
    other_att = await uc.record_attempt(db, editor, study_item_id=todo, result="passed")
    with pytest.raises(StudyRuleError, match="attempt_not_for_item"):
        await uc.propose_claim(
            db, editor, item, experience_id=exp, statement="합성 문장", attempt_id=other_att
        )
    with pytest.raises(StudyRuleError, match="statement_required"):
        await uc.propose_claim(db, editor, item, experience_id=exp, statement="  ")
    claim_id = await uc.propose_claim(
        db, editor, item, experience_id=exp, statement="SQL JOIN을 학습했다", attempt_id=att
    )
    assert await count(db, owner, Claim) == claims0 + 1
    async with db.workspace_session(owner.workspace_id) as s:
        assert (await s.get(Claim, claim_id)).status == "extracted"

    # 확정하지 않은 공고·공고에 없는 요건은 학습 계획의 근거가 아니다
    [draft] = await postings_uc.save_urls(db, owner, ["https://jobs.example.test/draft"])
    with pytest.raises(StudyRuleError, match="posting_not_saved"):
        await uc.plan_from_posting(db, owner, draft.posting_id)
    with pytest.raises(StudyRuleError, match="requirement_not_in_posting"):
        await uc.create_item(db, owner, posting_id=pid, field="requirements", text="Rust 경험")
    with pytest.raises(StudyRuleError, match="score_above_max"):
        await uc.record_attempt(
            db, owner, study_item_id=item, result="passed", score=11, max_score=10
        )


async def test_requirement_text_kept_after_reconfirm(db, env):
    owner = env["owner"]
    pid = await saved_posting(db, owner)
    item = await uc.create_item(db, owner, posting_id=pid, field="preferred", text="AWS 운영 경험")
    await postings_uc.confirm_posting(
        db, owner, pid, overrides={**FIELDS, "preferred": ["GCP 운영 경험"]}
    )
    cov = await uc.posting_coverage(db, owner, pid)
    stale = [r for r in cov.requirements if not r.current]
    assert [(r.text, r.items[0].id) for r in stale] == [("AWS 운영 경험", item)]
    assert {r.text for r in cov.requirements if r.current and r.field == "preferred"} == {
        "GCP 운영 경험"
    }
    # 요건 문장은 고칠 수 없다
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with db.workspace_session(owner.workspace_id) as s:
            await s.execute(update(StudyRequirement).values(text="고친 요건"))


# --- 복습 ---------------------------------------------------------------------


async def test_review_due_list(db, env):
    owner = env["owner"]
    item = await uc.create_item(db, owner, title="합성 알고리즘")
    m1 = await uc.add_mistake(db, owner, study_item_id=item, question="합성 문제 1", now=T0)
    m2 = await uc.add_mistake(
        db, owner, study_item_id=item, question="합성 문제 2", now=T0 + timedelta(hours=1)
    )
    assert await uc.due_reviews(db, owner, now=T0 + timedelta(hours=23)) == []
    due = await uc.due_reviews(db, owner, now=T0 + timedelta(days=1, hours=1))
    assert [d.id for d in due] == [m1, m2]
    assert due[0].study_item_title == "합성 알고리즘"

    day1 = T0 + timedelta(days=1)
    r = await uc.review_mistake(db, owner, m1, "good", now=day1)
    assert (r.interval_days, r.due_at) == (1, day1 + timedelta(days=1))
    r = await uc.review_mistake(db, owner, m1, "good", now=day1 + timedelta(days=1))
    assert (r.repetitions, r.interval_days) == (2, 3)
    r = await uc.review_mistake(db, owner, m1, "again", now=day1 + timedelta(days=4))
    assert (r.repetitions, r.interval_days, r.ease) == (0, 1, 2300)
    assert [d.id for d in await uc.due_reviews(db, owner, now=day1 + timedelta(days=4))] == [m2]

    await uc.set_mistake_status(db, owner, m2, "resolved")
    later = await uc.due_reviews(db, owner, now=day1 + timedelta(days=30))
    assert [d.id for d in later] == [m1]  # 다 익힌 오답은 복습 목록에서 빠진다
    with pytest.raises(StudyRuleError, match="mistake_resolved"):
        await uc.review_mistake(db, owner, m2, "good")
    with pytest.raises(StudyRuleError, match="invalid_grade"):
        await uc.review_mistake(db, owner, m1, "perfect")
    assert await count(db, owner, StudyReview) == 3
    # 복습·응시 기록은 고치지 않는다
    for model, values in ((StudyReview, {"grade": "easy"}), (StudyAttempt, {"result": "passed"})):
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with db.workspace_session(owner.workspace_id) as s:
                await s.execute(update(model).values(**values))


# --- 격리 ---------------------------------------------------------------------


async def test_study_isolation(db, env):
    owner, other = env["owner"], env["other"]
    pid = await saved_posting(db, owner)
    exp = await brain.create_experience(db, owner, title="합성 경험")
    claim = await brain.add_claim(db, owner, exp, statement="합성 주장", kind="skill")
    item = await uc.create_item(db, owner, posting_id=pid, field="requirements", text="SQL 이해")
    a = await uc.create_assessment(db, owner, title="코딩테스트", kind="coding_test")
    await uc.add_mistake(db, owner, study_item_id=item, question="합성 문제", now=T0)

    with pytest.raises(uc.NotFound):
        await uc.get_item(db, other, item)
    with pytest.raises(uc.NotFound):
        await uc.log_session(db, other, item, minutes=10)
    with pytest.raises(uc.NotFound):
        await uc.record_attempt(db, other, assessment_id=a, result="passed")
    with pytest.raises(uc.NotFound):
        await uc.plan_from_posting(db, other, pid)
    with pytest.raises(uc.NotFound):
        await uc.posting_coverage(db, other, pid)
    other_pid = await saved_posting(db, other)
    with pytest.raises(uc.NotFound):  # 다른 공간의 주장은 연결할 수 없다
        await uc.link_claim(
            db, other, other_pid, field="requirements", text="SQL 이해", claim_id=claim
        )
    assert await uc.list_items(db, other) == []
    assert await uc.list_assessments(db, other) == []
    assert await uc.due_reviews(db, other, now=T0 + timedelta(days=5)) == []

    # DB도 UUID만 맞는 교차 연결을 거부한다 (복합 FK)
    async with db.workspace_session(owner.workspace_id) as s:
        req_id = await s.scalar(select(StudyRequirement.id))
    with pytest.raises(IntegrityError):
        async with db.workspace_session(other.workspace_id) as s:
            s.add(
                StudyItem(
                    workspace_id=other.workspace_id,
                    title="침범",
                    kind="skill",
                    status="planned",
                    requirement_id=req_id,
                    created_by=other.subject_id,
                )
            )


# --- HTTP ---------------------------------------------------------------------


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


async def test_study_http(db, env):
    owner_actor = env["owner"]
    pid = await saved_posting(db, owner_actor)
    exp = await brain.create_experience(db, owner_actor, title="합성 경험")
    [draft] = await postings_uc.save_urls(db, owner_actor, ["https://jobs.example.test/draft"])
    app = FastAPI()
    app.include_router(
        build_router(
            CoreDeps(
                identity=Tokens(
                    {"owner": owner_actor, "viewer": env["viewer"], "other": env["other"]}
                ),
                db=db,
            )
        )
    )
    owner, viewer, other = (
        {"Authorization": "Bearer owner"},
        {"Authorization": "Bearer viewer"},
        {"Authorization": "Bearer other"},
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        assert (await c.get("/core/study/items")).status_code == 401
        r = await c.post(f"/core/study/postings/{pid}/plan", headers=viewer)
        assert r.status_code == 403
        r = await c.post(f"/core/study/postings/{draft.posting_id}/plan", headers=owner)
        assert (r.status_code, r.json()["detail"]) == (409, "posting_not_saved")
        r = await c.post(
            f"/core/study/postings/{pid}/plan",
            json={"lines": [{"field": "requirements", "text": "SQL 이해"}]},
            headers=owner,
        )
        assert r.status_code == 201
        [item_id] = r.json()["items_created"]
        assert r.json()["assessments_created"] == []

        r = await c.get(f"/core/study/postings/{pid}/coverage", headers=viewer)
        assert r.status_code == 200
        rows = {x["text"]: x for x in r.json()["requirements"]}
        assert rows["SQL 이해"]["coverage"] == "studying"
        assert rows["Docker 사용 경험"]["coverage"] == "uncovered"
        assert r.json()["basis"] == "user_links"

        r = await c.post(
            f"/core/study/items/{item_id}/sessions",
            json={"minutes": 45, "notes": "합성 노트", "resources": [{"title": "합성 자료"}]},
            headers=owner,
        )
        assert r.status_code == 201
        r = await c.post(
            "/core/study/assessments",
            json={
                "title": "코딩테스트",
                "kind": "coding_test",
                "posting_id": str(pid),
                "process_text": FIELDS["process"][0],
            },
            headers=owner,
        )
        assessment_id = r.json()["id"]
        r = await c.post(
            "/core/study/attempts",
            json={
                "assessment_id": assessment_id,
                "result": "failed",
                "score": "55.5",
                "max_score": "100",
            },
            headers=owner,
        )
        assert r.status_code == 201
        attempt_id = r.json()["id"]
        r = await c.post(
            "/core/study/mistakes",
            json={"attempt_id": attempt_id, "question": "합성 문제", "reason": "misread"},
            headers=owner,
        )
        mistake_id = r.json()["id"]
        r = await c.get(f"/core/study/assessments/{assessment_id}", headers=viewer)
        body = r.json()
        assert body["last_attempt"]["score"] == "55.50"
        assert body["requirement"]["field"] == "process"
        assert body["mistakes"][0]["id"] == mistake_id

        until = (datetime.now(UTC) + timedelta(days=2)).isoformat()
        r = await c.get("/core/study/reviews/due", params={"until": until}, headers=viewer)
        assert [d["id"] for d in r.json()] == [mistake_id]
        r = await c.post(
            f"/core/study/mistakes/{mistake_id}/reviews", json={"grade": "x"}, headers=owner
        )
        assert (r.status_code, r.json()["detail"]) == (422, "invalid_grade")
        r = await c.post(
            f"/core/study/mistakes/{mistake_id}/reviews", json={"grade": "good"}, headers=owner
        )
        assert r.json()["interval_days"] == 1

        r = await c.post(
            f"/core/study/items/{item_id}/claim-candidates",
            json={"experience_id": str(exp), "statement": "SQL 학습 결과 (합성)"},
            headers=owner,
        )
        assert (r.status_code, r.json()["detail"]) == (409, "no_study_result")
        r = await c.patch(f"/core/study/items/{item_id}", json={"status": "done"}, headers=owner)
        assert r.json()["status"] == "done"
        r = await c.post(
            f"/core/study/items/{item_id}/claim-candidates",
            json={"experience_id": str(exp), "statement": "SQL 학습 결과 (합성)"},
            headers=owner,
        )
        assert r.status_code == 201 and r.json()["status"] == "extracted"
        claim_id = r.json()["claim_id"]

        r = await c.get(f"/core/study/items/{item_id}", headers=viewer)
        body = r.json()
        assert body["total_minutes"] == 45
        assert body["claim_candidates"][0]["claim_id"] == claim_id
        r = await c.get("/core/study/items", params={"status": "done"}, headers=viewer)
        assert [x["id"] for x in r.json()] == [item_id]

        r = await c.get(f"/core/study/postings/{pid}/coverage", headers=viewer)
        row = next(x for x in r.json()["requirements"] if x["text"] == "SQL 이해")
        assert row["coverage"] == "claim_pending"
        r = await c.delete(
            f"/core/study/requirements/{row['requirement_id']}/claims/{claim_id}", headers=owner
        )
        assert r.status_code == 204
        r = await c.post(
            f"/core/study/postings/{pid}/claims",
            json={"field": "requirements", "text": "SQL 이해", "claim_id": claim_id},
            headers=owner,
        )
        assert r.json()["requirement_id"] == row["requirement_id"]

        # 다른 공간
        assert (await c.get(f"/core/study/items/{item_id}", headers=other)).status_code == 404
        assert (
            await c.get(f"/core/study/postings/{pid}/coverage", headers=other)
        ).status_code == 404
        assert (await c.get("/core/study/reviews/due", headers=other)).json() == []

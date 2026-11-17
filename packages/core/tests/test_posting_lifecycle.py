"""공고 생애주기: 접수 시작(opens_at)·접수 상태·채용 시기·목록 거름/정렬·지원 요약·한꺼번에 보관.

시각은 모두 고정한다 (지금 = 인자). 공고·문구는 합성.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from career_core.db import JobPosting
from career_core.db.base import utcnow
from career_core.domain.postings import (
    FieldName,
    Lifecycle,
    posting_lifecycle,
    posting_season,
)
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.ports import ActorContext
from career_core.usecases import applications as apps_uc
from career_core.usecases import postings as uc
from career_core.usecases import workspace_settings as ws_settings
from career_core.usecases.applications import current_stage
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI

SEOUL = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")


def at(text: str, tz=SEOUL) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=tz)


# --- 규칙 (시각 고정) ------------------------------------------------------------------


def test_deadline_today_is_open_until_end_of_day():
    # 날짜만 있는 마감은 그날 끝까지 접수 중
    assert posting_lifecycle("2026-11-17", None, at("2026-11-17T00:00"), SEOUL) == Lifecycle.OPEN
    assert posting_lifecycle("2026-11-17", None, at("2026-11-17T23:59:59"), SEOUL) == Lifecycle.OPEN
    assert posting_lifecycle("2026-11-17", None, at("2026-11-18T00:00"), SEOUL) == Lifecycle.CLOSED
    assert posting_lifecycle("2026-11-30", None, at("2026-11-17T12:00"), SEOUL) == Lifecycle.OPEN
    # 시각이 있는 마감은 그 시각까지
    assert (
        posting_lifecycle("2026-11-17T18:00:00+09:00", None, at("2026-11-17T17:59"), SEOUL)
        == Lifecycle.OPEN
    )
    assert (
        posting_lifecycle("2026-11-17T18:00:00+09:00", None, at("2026-11-17T18:00"), SEOUL)
        == Lifecycle.CLOSED
    )
    # 시간대 없는 시각은 공간 시간대의 벽시계
    assert posting_lifecycle("2026-11-17T18:00", None, at("2026-11-17T18:30"), SEOUL) == (
        Lifecycle.CLOSED
    )


def test_today_follows_workspace_timezone():
    # 서울 11/18 01:00 = 뉴욕 11/17 11:00. 마감 11/17은 서울 공간에선 지났고 뉴욕 공간에선 오늘이다.
    now = at("2026-11-18T01:00")
    assert posting_lifecycle("2026-11-17", None, now, SEOUL) == Lifecycle.CLOSED
    assert posting_lifecycle("2026-11-17", None, now, NY) == Lifecycle.OPEN
    # 접수 시작도 같다: 서울 11/18 시작은 이미 시작, 뉴욕 11/18 시작은 아직
    assert posting_lifecycle("2026-11-30", "2026-11-18", now, SEOUL) == Lifecycle.OPEN
    assert posting_lifecycle("2026-11-30", "2026-11-18", now, NY) == Lifecycle.UPCOMING


def test_upcoming_always_unknown():
    now = at("2026-11-17T09:00")
    assert posting_lifecycle("2026-12-10", "2026-12-01", now, SEOUL) == Lifecycle.UPCOMING
    assert posting_lifecycle("2026-12-10", "2026-11-17", now, SEOUL) == Lifecycle.OPEN  # 오늘 시작
    assert (
        posting_lifecycle("2026-12-10", "2026-11-17T10:00", now, SEOUL) == Lifecycle.UPCOMING
    )  # 오늘 10시 시작, 지금 9시
    assert posting_lifecycle("always", None, now, SEOUL) == Lifecycle.ALWAYS
    assert posting_lifecycle("always", "2026-12-01", now, SEOUL) == Lifecycle.UPCOMING
    assert posting_lifecycle(None, None, now, SEOUL) == Lifecycle.UNKNOWN
    # 시작만 지난 공고는 마감을 모른다 (접수 중으로 단정하지 않는다)
    assert posting_lifecycle(None, "2026-11-01", now, SEOUL) == Lifecycle.UNKNOWN
    assert posting_lifecycle("내일까지", None, now, SEOUL) == Lifecycle.UNKNOWN
    # 마감이 지났으면 시작 값이 이상해도 마감
    assert posting_lifecycle("2026-11-01", "2026-12-01", now, SEOUL) == Lifecycle.CLOSED


def test_season_by_deadline_then_opens_then_saved():
    saved = at("2026-03-02T10:00")
    assert posting_season("2026-06-30", None, saved, SEOUL) == "2026-H1"
    assert posting_season("2026-07-01", None, saved, SEOUL) == "2026-H2"
    # 시각이 있는 마감은 공간 시간대 날짜로: UTC 6/30 16:00 = 서울 7/1
    assert posting_season("2026-06-30T16:00:00+00:00", None, saved, SEOUL) == "2026-H2"
    assert posting_season("always", "2026-09-01", saved, SEOUL) == "2026-H2"
    assert posting_season("always", None, saved, SEOUL) == "2026-H1"
    assert posting_season(None, None, None, SEOUL) is None


def test_extraction_never_guesses_opens_at():
    text = "[접수 기간] 2026.11.10 ~ 2026.11.24 23:59\n게시일 2026.11.01\n상시 채용 아님"
    raw = {
        "deadline": {"value": "2026-11-24T23:59", "quote": "2026.11.10 ~ 2026.11.24 23:59"},
        "opens_at": {"value": "2026-11-10", "quote": "2026.11.10 ~ 2026.11.24 23:59"},
    }
    fields, dropped = uc.verify_llm_fields(raw, text)
    assert fields["opens_at"]["value"] == "2026-11-10" and dropped == 0
    assert fields["opens_at"]["evidence"]["kind"] == "quote"
    # 원문에 접수 시작이 없으면 비운다: 게시일·다른 날짜로 지어낸 값은 버린다
    text2 = "[마감] 2026.11.24\n게시일 2026.11.01"
    fields, dropped = uc.verify_llm_fields(
        {
            "deadline": {"value": "2026-11-24", "quote": "[마감] 2026.11.24"},
            "opens_at": {"value": "2026-11-17", "quote": "[마감] 2026.11.24"},
        },
        text2,
    )
    assert "opens_at" not in fields and dropped == 1
    fields, _ = uc.verify_llm_fields(
        {"opens_at": {"value": "always", "quote": "상시 채용 아님"}}, text
    )
    assert "opens_at" not in fields
    fields, _ = uc.verify_llm_fields(
        {"deadline": {"value": "2026-11-24", "quote": "[마감] 2026.11.24"}}, text2
    )
    assert FieldName.OPENS_AT not in fields


def test_current_stage_picks_first_pending_else_last():
    def st(pos, outcome, name):
        return SimpleNamespace(
            position=pos, outcome=outcome, name=name, kind="interview", scheduled_at=None
        )

    assert current_stage([]) is None
    rows = [st(2, "pending", "2차 면접"), st(0, "passed", "서류"), st(1, "passed", "1차 면접")]
    assert current_stage(rows)["name"] == "2차 면접"
    done = [st(0, "passed", "서류"), st(1, "failed", "1차 면접")]
    assert current_stage(done) == {
        "kind": "interview",
        "name": "1차 면접",
        "outcome": "failed",
        "scheduled_at": None,
    }


# --- 목록·보관 (DB) ---------------------------------------------------------------


@pytest.fixture
async def env(db):
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    return {
        "owner": ActorContext(a, ws_a, scopes_for_role(Role.OWNER)),
        "viewer": ActorContext(a, ws_a, scopes_for_role(Role.VIEWER)),
        "other": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
    }


async def add(db, actor, title, *, status="saved", saved=None, **fields):
    pid = uuid4()
    values = {"title": title, **fields}
    async with db.workspace_session(actor.workspace_id) as s:
        s.add(
            JobPosting(
                id=pid,
                workspace_id=actor.workspace_id,
                canonical_url=f"https://jobs.example.test/{pid.hex[:8]}",
                source_url=f"https://jobs.example.test/{pid.hex[:8]}",
                status=status,
                fields=values if status != "draft" else {},
                evidence={},
                saved_at=saved or (utcnow() if status == "saved" else None),
                saved_by=actor.subject_id if status == "saved" else None,
                created_by=actor.subject_id,
            )
        )
    return pid


NOW = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)  # 서울 11/17 12:00


@pytest.mark.anyio
async def test_list_filters_and_sorts_by_lifecycle(db, env):
    o = env["owner"]
    a_late = await add(db, o, "늦은 마감", deadline="2026-12-20")
    a_soon = await add(db, o, "곧 마감", deadline="2026-11-17")  # 오늘
    a_time = await add(db, o, "시각 마감", deadline="2026-11-18T10:00:00+09:00")
    up_far = await add(db, o, "먼 예정", opens_at="2026-12-15", deadline="2026-12-31")
    up_near = await add(db, o, "가까운 예정", opens_at="2026-11-20", deadline="2026-11-30")
    c_old = await add(db, o, "오래된 마감", deadline="2026-03-01")
    c_new = await add(db, o, "최근 마감", deadline="2026-11-16")
    al_old = await add(db, o, "상시 1", deadline="always", saved=datetime(2026, 1, 5, tzinfo=UTC))
    al_new = await add(db, o, "상시 2", deadline="always", saved=datetime(2026, 9, 5, tzinfo=UTC))
    unk = await add(db, o, "날짜 없음")
    await add(db, env["other"], "남의 공고", deadline="2026-11-30")

    async def ids(lc):
        return [p.id for p in await uc.list_postings(db, o, lifecycle=lc, now=NOW)]

    assert await ids("open") == [a_soon, a_time, a_late]
    assert await ids("upcoming") == [up_near, up_far]
    assert await ids("closed") == [c_new, c_old]
    assert await ids("always") == [al_new, al_old]
    assert await ids("unknown") == [unk]
    every = await uc.list_postings(db, o, now=NOW)
    assert len(every) == 10  # 다른 공간 0건
    by = {p.id: p for p in every}
    assert by[c_old].season == "2026-H1" and by[c_new].season == "2026-H2"
    assert by[up_near].opens_at == "2026-11-20"
    with pytest.raises(uc.PostingRuleError):
        await uc.list_postings(db, o, lifecycle="soon")


@pytest.mark.anyio
async def test_list_respects_workspace_timezone(db, env):
    o = env["owner"]
    pid = await add(db, o, "11/17 마감", deadline="2026-11-17")
    now = datetime(2026, 11, 17, 16, 0, tzinfo=UTC)  # 서울 11/18 01:00, 뉴욕 11/17 11:00
    [p] = await uc.list_postings(db, o, now=now)
    assert p.lifecycle == "closed"
    await ws_settings.update_settings(db, o, timezone="America/New_York")
    [p] = await uc.list_postings(db, o, now=now)
    assert (p.id, p.lifecycle) == (pid, "open")
    view = await uc.get_posting(db, o, pid, now=now)
    assert view.lifecycle == "open" and view.season == "2026-H2"


@pytest.mark.anyio
async def test_application_summary_on_postings(db, env):
    o = env["owner"]
    pid = await add(
        db,
        o,
        "문항 있는 공고",
        deadline="2026-11-30",
        questions=[
            {"prompt": "지원 동기 (500자)", "char_limit": 500},
            {"prompt": "협업 경험", "char_limit": None},
        ],
    )
    plain = await add(db, o, "지원 전 공고", deadline="2026-11-30")
    aid = await apps_uc.create_application(db, o, pid)
    rows = {p.id: p for p in await uc.list_postings(db, o, now=NOW)}
    app = rows[pid].application
    assert (app.id, app.status, app.questions, app.answered, app.stage) == (
        aid,
        "draft",
        2,
        0,
        None,
    )
    assert rows[plain].application is None
    assert (await uc.get_posting(db, o, pid)).application.id == aid
    # 읽기 권한이 없는 행위자에게는 지원 요약을 주지 않는다
    nobody = ActorContext(o.subject_id, o.workspace_id, frozenset())
    rows = {p.id: p for p in await uc.list_postings(db, nobody, now=NOW)}
    assert rows[pid].application is None
    # 지원 목록에도 지금 전형 단계 칸이 있다 (단계가 없으면 None)
    [summary] = await apps_uc.list_applications(db, o)
    assert summary.stage is None


@pytest.mark.anyio
async def test_bulk_archive_and_restore(db, env):
    o, other = env["owner"], env["other"]
    p1 = await add(db, o, "하나", deadline="2026-11-30")
    p2 = await add(db, o, "둘", status="draft")
    theirs = await add(db, other, "남의 것", deadline="2026-11-30")
    changed = await uc.archive_postings(db, o, [p1, p2, p1, theirs])
    assert set(changed) == {p1, p2}  # 다른 공간 id는 바뀌지 않는다
    assert await uc.archive_postings(db, o, [p1]) == []  # 이미 보관
    rows = {p.id: p.status for p in await uc.list_postings(db, o)}
    assert rows == {p1: "archived", p2: "archived"}
    assert [p.status for p in await uc.list_postings(db, other)] == ["saved"]
    # 보관 풀기: 저장한 적 있으면 저장 공고, 아니면 확인 전
    assert set(await uc.archive_postings(db, o, [p1, p2], archived=False)) == {p1, p2}
    rows = {p.id: p.status for p in await uc.list_postings(db, o)}
    assert rows == {p1: "saved", p2: "draft"}
    assert [p.id for p in await uc.list_postings(db, o, status="draft")] == [p2]


class Tokens:
    def __init__(self, table):
        self.table = table

    async def resolve(self, credentials):
        return self.table.get(credentials)


@pytest.mark.anyio
async def test_lifecycle_http(db, env):
    o = env["owner"]
    p1 = await add(db, o, "열린 공고", deadline="2999-01-01")
    p2 = await add(db, o, "마감 공고", deadline="2000-01-01")
    app = FastAPI()
    app.include_router(
        build_router(CoreDeps(identity=Tokens({"owner": o, "viewer": env["viewer"]}), db=db))
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        owner, viewer = {"Authorization": "Bearer owner"}, {"Authorization": "Bearer viewer"}
        r = await c.get("/core/job-postings?lifecycle=open", headers=viewer)
        assert [x["id"] for x in r.json()] == [str(p1)]
        assert r.json()[0]["lifecycle"] == "open" and r.json()[0]["application"] is None
        r = await c.get("/core/job-postings?lifecycle=nope", headers=viewer)
        assert r.status_code == 422
        r = await c.post("/core/job-postings/archive", json={"ids": [str(p2)]}, headers=viewer)
        assert r.status_code == 403
        r = await c.post("/core/job-postings/archive", json={"ids": [str(p2)]}, headers=owner)
        assert r.json() == {"changed": [str(p2)]}
        r = await c.get(f"/core/job-postings/{p2}", headers=viewer)
        assert (r.json()["status"], r.json()["lifecycle"]) == ("archived", "closed")
        r = await c.post("/core/job-postings/unarchive", json={"ids": [str(p2)]}, headers=owner)
        assert r.json() == {"changed": [str(p2)]}

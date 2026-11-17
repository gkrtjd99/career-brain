"""O19: 경험 구조와 찾기 — 종류·상하(한 단계)·월 단위 기간·태그, 거름·정렬·쪽, 브레인 색인.

합성 자료만 쓴다. DB는 career_core.testing의 임시 DB.
"""

import importlib
import secrets
from datetime import date
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from career_core.adapters.blobstore import LocalBlobStore
from career_core.db import Experience
from career_core.db.migrate import CORE_CHAIN, upgrade
from career_core.domain.brain import ClaimRuleError
from career_core.domain.experiences import Period, normalize_tags, parse_month, parse_period
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.ports import ActorContext
from career_core.testing import admin_url
from career_core.usecases import brain
from career_core.usecases.brain import ExperienceFilter
from career_core.usecases.extraction import _Experience, structure_candidate
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import create_engine, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.anyio


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


# ---------------------------------------------------------------- 원문 기간 읽기


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("2023.03 – 2024.08", Period(date(2023, 3, 1), date(2024, 8, 1), False)),
        ("2023-03 ~ 2024-08", Period(date(2023, 3, 1), date(2024, 8, 1), False)),
        ("2023.03-2024.08", Period(date(2023, 3, 1), date(2024, 8, 1), False)),
        ("2021.03 - 2023.02", Period(date(2021, 3, 1), date(2023, 2, 1), False)),
        ("2023/3 - 2023/9", Period(date(2023, 3, 1), date(2023, 9, 1), False)),
        ("2023. 3. ~ 2024. 8.", Period(date(2023, 3, 1), date(2024, 8, 1), False)),
        ("2023년 3월 - 2024년 8월", Period(date(2023, 3, 1), date(2024, 8, 1), False)),
        ("2023.03 – 2024.08 (1년 6개월)", Period(date(2023, 3, 1), date(2024, 8, 1), False)),
        ("2024.10 – 현재", Period(date(2024, 10, 1), None, True)),
        ("2024.10 ~ 재직 중", Period(date(2024, 10, 1), None, True)),
        ("2022-01 - Present", Period(date(2022, 1, 1), None, True)),
        # 모호하면 비운다 (추측하지 않는다)
        ("2023", None),
        ("2021 - 2023", None),  # 연도만
        ("23.03 - 24.08", None),  # 두 자리 연도
        ("2024.10", None),  # 달 하나 (시작인지 한 달인지 모름)
        ("2024.08 - 2023.03", None),  # 거꾸로
        ("2023.13 - 2024.02", None),  # 없는 달
        ("2023.03.15 - 2024.08.20", None),  # 일까지 (월 단위가 아님)
        ("2023 봄 - 2024 여름", None),
        ("약 2년", None),
        ("2023.03 - 2024.08, 2025.01 - 현재", None),  # 기간 둘
        ("since 2023.03", None),
        ("", None),
        (None, None),
    ],
)
def test_backfill_period_parsing(raw, want):
    assert parse_period(raw) == want


def test_tag_normalization():
    assert normalize_tags(["  PostgreSQL ", "postgresql", "Fast  API", "", "   ", "React"]) == [
        "PostgreSQL",
        "Fast API",
        "React",
    ]
    assert normalize_tags(None) == []
    with pytest.raises(ClaimRuleError, match="tag_too_long"):
        normalize_tags(["x" * 41])
    assert normalize_tags(["x" * 40]) == ["x" * 40]
    with pytest.raises(ClaimRuleError, match="too_many_tags"):
        normalize_tags([f"t{i}" for i in range(31)])
    # 같은 것을 빼고 30개면 받는다
    assert len(normalize_tags([f"t{i}" for i in range(30)] + ["T0", "t1"])) == 30
    # 모델 후보는 거부 대신 버린다
    assert normalize_tags(["x" * 41, "ok"], strict=False) == ["ok"]
    assert len(normalize_tags([f"t{i}" for i in range(40)], strict=False)) == 30


def test_month_input():
    assert parse_month("2024-03") == date(2024, 3, 1)
    assert parse_month("") is None and parse_month(None) is None
    for bad in ("2024-3", "2024-13", "2024/03", "03-2024", "2024-03-01"):
        with pytest.raises(ClaimRuleError, match="invalid_month"):
            parse_month(bad)


# ---------------------------------------------------------------- migration 채우기


def _alembic(conn):
    cfg = Config()
    cfg.set_main_option("script_location", str(CORE_CHAIN.script_location))
    cfg.attributes["connection"] = conn
    cfg.attributes["schema"] = CORE_CHAIN.schema
    return cfg


def test_c0030_backfills_only_unambiguous_periods(pg):
    mod = importlib.import_module("career_core.migrations.versions.c0030_experience_structure")
    before = mod.down_revision
    admin = admin_url()
    name = f"career_test_{secrets.token_hex(4)}"
    eng = create_engine(admin, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    owner = admin.set(database=name)
    ws, actor = uuid4(), uuid4()
    periods = {
        "clear": "2023.03 – 2024.08",
        "ongoing": "2024.10 – 현재",
        "year_only": "2021 - 2023",
        "none": None,
    }
    try:
        upgrade(owner, revision=before)
        e = create_engine(owner, poolclass=NullPool)
        with e.begin() as conn:
            conn.execute(text("INSERT INTO core.workspaces (id, name) VALUES (:w, 'm')"), {"w": ws})
            for title, period in periods.items():
                conn.execute(
                    text(
                        "INSERT INTO core.experiences (id, workspace_id, title, period_text, "
                        "status, origin, created_by) VALUES (:i, :w, :t, :p, 'active', 'user', :a)"
                    ),
                    {"i": uuid4(), "w": ws, "t": title, "p": period, "a": actor},
                )
        upgrade(owner, revision=mod.revision)
        with e.begin() as conn:
            rows = {
                r.title: r
                for r in conn.execute(
                    text(
                        "SELECT title, period_text, kind, parent_id, started_on, ended_on, "
                        "ongoing, tags FROM core.experiences"
                    )
                )
            }
            forced = conn.scalar(
                text(
                    "SELECT relforcerowsecurity FROM pg_class "
                    "WHERE oid = 'core.experiences'::regclass"
                )
            )
        assert forced is True
        clear, ongoing = rows["clear"], rows["ongoing"]
        assert (clear.started_on, clear.ended_on, clear.ongoing) == (
            date(2023, 3, 1),
            date(2024, 8, 1),
            False,
        )
        assert (ongoing.started_on, ongoing.ended_on, ongoing.ongoing) == (
            date(2024, 10, 1),
            None,
            True,
        )
        for title in ("year_only", "none"):
            assert (rows[title].started_on, rows[title].ended_on, rows[title].ongoing) == (
                None,
                None,
                False,
            )
        # 원문은 그대로, 나머지 칸은 기본값
        assert {t: r.period_text for t, r in rows.items()} == periods
        assert {(r.kind, r.parent_id, tuple(r.tags)) for r in rows.values()} == {
            ("other", None, ())
        }
        with e.connect() as conn:
            command.downgrade(_alembic(conn), before)
            conn.commit()
        with e.begin() as conn:
            cols = set(
                conn.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'core' AND table_name = 'experiences'"
                    )
                )
            )
        assert not cols & {"kind", "parent_id", "started_on", "ended_on", "ongoing", "tags"}
        e.dispose()
    finally:
        with eng.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        eng.dispose()


# ---------------------------------------------------------------- 만들기·고치기·상하


async def test_create_and_update_structure(db, people):
    owner = people["owner"]
    work = await brain.create_experience(
        db,
        owner,
        title="합성 물류 회사",
        organization="Acme 물류",
        kind="work",
        started_on="2021-03",
        ended_on="2023-02",
        period_text="2021.03 - 2023.02",
        tags=["PostgreSQL", " postgresql ", "Kafka"],
    )
    proj = await brain.create_experience(
        db, owner, title="주문 처리 개편", kind="project", parent_id=work, started_on="2022-01"
    )
    view = await brain.get_experience(db, owner, work)
    assert (view.kind, view.started_on, view.ended_on, view.ongoing) == (
        "work",
        "2021-03",
        "2023-02",
        False,
    )
    assert view.tags == ["PostgreSQL", "Kafka"]
    assert [c.id for c in view.children] == [proj]
    child = await brain.get_experience(db, owner, proj)
    assert (child.parent_id, child.parent_title) == (work, "합성 물류 회사")

    # 진행 중으로 바꾸면 끝 달을 비운다
    await brain.update_experience(db, owner, work, ongoing=True)
    view = await brain.get_experience(db, owner, work)
    assert (view.ended_on, view.ongoing) == (None, True)
    await brain.update_experience(db, owner, work, ongoing=False, ended_on="2023-02", tags=[])
    view = await brain.get_experience(db, owner, work)
    assert (view.ended_on, view.ongoing, view.tags) == ("2023-02", False, [])
    # 준 칸만 고친다
    await brain.update_experience(db, owner, work, title="합성 물류 회사 (고침)")
    view = await brain.get_experience(db, owner, work)
    assert (view.title, view.kind, view.started_on) == ("합성 물류 회사 (고침)", "work", "2021-03")

    for kwargs, code in (
        ({"kind": "job"}, "invalid_experience_kind"),
        ({"started_on": "2021-13"}, "invalid_month"),
        ({"started_on": "2023-03", "ended_on": "2023-02"}, "end_before_start"),
        ({"ended_on": "2023-02", "ongoing": True}, "ongoing_with_end"),
        ({"tags": ["x" * 41]}, "tag_too_long"),
        ({"title": "  "}, "title_required"),
    ):
        with pytest.raises(ClaimRuleError, match=code):
            await brain.update_experience(db, owner, work, **kwargs)
    with pytest.raises(ClaimRuleError, match="end_without_start"):
        await brain.create_experience(db, owner, title="끝만", ended_on="2023-02")


async def test_hierarchy_is_one_level(db, people):
    owner = people["owner"]
    work = await brain.create_experience(db, owner, title="경력 (합성)", kind="work")
    proj = await brain.create_experience(
        db, owner, title="프로젝트 (합성)", kind="project", parent_id=work
    )
    other_work = await brain.create_experience(db, owner, title="다른 경력 (합성)", kind="work")

    # 자식의 자식은 안 된다
    with pytest.raises(ClaimRuleError, match="parent_is_child"):
        await brain.create_experience(db, owner, title="손자", parent_id=proj)
    # 자식이 있는 경험은 자식이 될 수 없다
    with pytest.raises(ClaimRuleError, match="experience_has_children"):
        await brain.update_experience(db, owner, work, parent_id=other_work)
    # 자기 자신
    with pytest.raises(ClaimRuleError, match="parent_self"):
        await brain.update_experience(db, owner, other_work, parent_id=other_work)
    # 옮기기와 떼기는 된다
    await brain.update_experience(db, owner, proj, parent_id=other_work)
    assert (await brain.get_experience(db, owner, proj)).parent_id == other_work
    await brain.update_experience(db, owner, proj, parent_id=None)
    assert (await brain.get_experience(db, owner, proj)).parent_id is None

    # usecase를 거치지 않아도 DB trigger가 막는다
    await brain.update_experience(db, owner, proj, parent_id=work)
    grandchild = await brain.create_experience(db, owner, title="맨 위 (합성)")
    with pytest.raises(IntegrityError, match="experience_parent_is_child"):
        async with db.workspace_session(owner.workspace_id) as s:
            await s.execute(
                update(Experience).where(Experience.id == grandchild).values(parent_id=proj)
            )
    with pytest.raises(IntegrityError, match="experience_has_children"):
        async with db.workspace_session(owner.workspace_id) as s:
            await s.execute(
                update(Experience).where(Experience.id == work).values(parent_id=other_work)
            )
    # 부모를 지우면 자식은 맨 위로
    async with db.workspace_session(owner.workspace_id) as s:
        await s.execute(text("DELETE FROM core.experiences WHERE id = :i"), {"i": work})
    assert (await brain.get_experience(db, owner, proj)).parent_id is None


async def test_cross_workspace_parent_is_rejected(db, people):
    theirs = await brain.create_experience(
        db, people["other"], title="남의 경력 (합성)", kind="work"
    )
    with pytest.raises(ClaimRuleError, match="parent_not_found"):
        await brain.create_experience(db, people["owner"], title="내 프로젝트", parent_id=theirs)
    mine = await brain.create_experience(db, people["owner"], title="내 경험 (합성)")
    with pytest.raises(ClaimRuleError, match="parent_not_found"):
        await brain.update_experience(db, people["owner"], mine, parent_id=theirs)
    # DB 복합 FK도 막는다 (같은 공간의 경험만)
    with pytest.raises(IntegrityError):
        async with db.workspace_session(people["owner"].workspace_id) as s:
            await s.execute(
                update(Experience).where(Experience.id == mine).values(parent_id=theirs)
            )


# ---------------------------------------------------------------- 거름·정렬·쪽


async def _seed(db, owner):
    work = await brain.create_experience(
        db,
        owner,
        title="Acme 백엔드 (합성)",
        organization="Acme",
        kind="work",
        started_on="2021-03",
        ended_on="2023-02",
        tags=["Python", "PostgreSQL"],
    )
    proj = await brain.create_experience(
        db,
        owner,
        title="주문 처리 개편 (합성)",
        organization="Acme",
        kind="project",
        parent_id=work,
        started_on="2022-01",
        ended_on="2022-06",
        tags=["Kafka"],
    )
    school = await brain.create_experience(
        db,
        owner,
        title="컴퓨터공학 학사 (합성)",
        organization="합성대학교",
        kind="education",
        started_on="2016-03",
        ended_on="2020-02",
    )
    now = await brain.create_experience(
        db,
        owner,
        title="오픈소스 기여 (합성)",
        kind="activity",
        started_on="2024-10",
        ongoing=True,
        tags=["python", "Rust"],
    )
    undated = await brain.create_experience(db, owner, title="날짜 모름 (합성)")
    await brain.add_claim(db, owner, proj, statement="주문 큐를 Kafka로 옮겼다", kind="action")
    await brain.add_claim(
        db, owner, work, statement="결제 API를 만들었다", kind="action", contribution="personal"
    )
    return {"work": work, "proj": proj, "school": school, "now": now, "undated": undated}


async def test_filters_and_sort(db, people):
    owner = people["owner"]
    ids = await _seed(db, owner)
    await brain.create_experience(db, people["other"], title="남의 경험 Python", tags=["Python"])

    async def titles(**kw):
        return [e.id for e in await brain.list_experiences(db, owner, ExperienceFilter(**kw))]

    # 기본: 시작 달 최근 먼저, 날짜 없는 것은 끝. 다른 공간 0건
    assert await titles() == [
        ids["now"],
        ids["proj"],
        ids["work"],
        ids["school"],
        ids["undated"],
    ]
    assert await titles(kinds=("work", "education")) == [ids["work"], ids["school"]]
    assert await titles(top_level=True) == [ids["now"], ids["work"], ids["school"], ids["undated"]]
    assert await titles(parent_id=ids["work"]) == [ids["proj"]]
    # 태그: 하나라도 (대소문자 무시)
    assert await titles(tags=("PYTHON",)) == [ids["now"], ids["work"]]
    assert await titles(tags=("kafka", "rust")) == [ids["now"], ids["proj"]]
    # 연도: 기간이 겹친다 (진행 중은 오늘까지)
    assert await titles(year=2022) == [ids["proj"], ids["work"]]
    assert await titles(year=2019) == [ids["school"]]
    assert await titles(year=date.today().year) == [ids["now"]]
    assert await titles(organization="acme") == [ids["proj"], ids["work"]]
    # 확인 상태
    assert await titles(review="needs_review") == [ids["proj"]]
    assert await titles(review="confirmed") == [ids["work"]]
    assert await titles(review="no_claims") == [ids["now"], ids["school"], ids["undated"]]
    # 글자: 제목·조직·역할·태그. LIKE 특수 문자는 글자 그대로
    assert await titles(q="kafka") == [ids["proj"]]
    assert await titles(q="합성대") == [ids["school"]]
    assert await titles(q="%") == []
    # 정렬
    assert await titles(sort="title") == [
        ids["work"],
        ids["undated"],
        ids["now"],
        ids["proj"],
        ids["school"],
    ]
    await brain.update_experience(db, owner, ids["school"], tags=["C"])
    assert (await titles(sort="updated"))[0] == ids["school"]
    with pytest.raises(ClaimRuleError, match="invalid_sort"):
        await titles(sort="random")

    rows = await brain.list_experiences(db, owner, ExperienceFilter(kinds=("work",)))
    [work] = rows
    assert (work.children_count, work.needs_review, work.claim_counts) == (
        1,
        0,
        {"self_confirmed": 1},
    )
    [proj] = await brain.list_experiences(db, owner, ExperienceFilter(parent_id=ids["work"]))
    assert (proj.parent_title, proj.needs_review, proj.tags) == ("Acme 백엔드 (합성)", 1, ["Kafka"])


async def test_300_experiences_paginate(db, people):
    owner = people["owner"]
    ws, me = await create_workspace(db, name="300 (합성)", owner_display_name="pat")
    owner = ActorContext(me, ws, scopes_for_role(Role.OWNER))
    kinds = ("work", "project", "education", "activity", "other")
    async with db.workspace_session(ws) as s:
        for i in range(300):
            year = 2000 + i % 25
            s.add(
                Experience(
                    id=uuid4(),
                    workspace_id=ws,
                    title=f"합성 경험 {i:03d}",
                    kind=kinds[i % 5],
                    started_on=date(year, 1 + i % 12, 1),
                    ended_on=date(year, 12, 1),
                    tags=[f"tag{i % 7}"],
                    origin="user",
                    created_by=me,
                )
            )
    seen = []
    total = None
    for offset in range(0, 400, 64):
        page, total = await brain.page_experiences(
            db, owner, ExperienceFilter(limit=64, offset=offset)
        )
        seen += page
    assert total == 300 and len(seen) == 300
    assert len({e.id for e in seen}) == 300
    starts = [e.started_on for e in seen]
    assert starts == sorted(starts, reverse=True)
    # 거른 수도 쪽과 무관하게 전체 수
    page, total = await brain.page_experiences(
        db, owner, ExperienceFilter(kinds=("work",), tags=("tag3",), limit=5)
    )
    want = [i for i in range(300) if i % 5 == 0 and i % 7 == 3]
    assert total == len(want) and len(page) == 5
    page, total = await brain.page_experiences(db, owner, ExperienceFilter(year=2010, limit=1000))
    assert total == len([i for i in range(300) if 2000 + i % 25 == 2010])
    by_title = await brain.list_experiences(db, owner, ExperienceFilter(sort="title", limit=1000))
    assert [e.title for e in by_title] == [f"합성 경험 {i:03d}" for i in range(300)]

    overview = await brain.brain_overview(db, owner)
    assert overview.experiences == 300
    assert overview.kinds == dict.fromkeys(kinds, 60)
    assert (overview.year_min, overview.year_max) == (2000, 2024)
    assert overview.tags[0].count == 43 and len(overview.tags) == 7
    assert len(overview.recent) == 5


async def test_overview_counts(db, people):
    owner = people["owner"]
    ids = await _seed(db, owner)
    o = await brain.brain_overview(db, owner)
    assert o.experiences == 5
    assert o.kinds == {"work": 1, "project": 1, "education": 1, "activity": 1, "other": 1}
    assert (o.needs_review, o.pending_claims, o.undated) == (1, 1, 1)
    assert (o.year_min, o.year_max) == (2016, date.today().year)
    assert [(t.tag, t.count) for t in o.tags][:1] == [("Python", 2)]
    assert {t.tag for t in o.tags} == {"Python", "PostgreSQL", "Kafka", "Rust"}
    assert o.recent[0].id == ids["undated"]
    empty = await brain.brain_overview(db, people["other"])
    assert (empty.experiences, empty.year_min, empty.tags) == (0, None, [])


# ---------------------------------------------------------------- 추출 후보


def test_extraction_structure_comes_only_from_source_text():
    class B:
        def __init__(self, text):
            self.text = text

    blocks = [B("Acme Corp, Backend Engineer, 2021.03 - 2023.02"), B("Built it with FastAPI.")]
    item = _Experience(
        title="x",
        period="2021.03 - 2023.02",
        kind="work",
        tags=["fastapi", "Kubernetes", "FastAPI"],
    )
    got = structure_candidate(item, blocks)
    assert (got.kind, got.started_on, got.ended_on, got.ongoing) == (
        "work",
        date(2021, 3, 1),
        date(2023, 2, 1),
        False,
    )
    assert got.tags == ["fastapi"]  # 원문에 없는 Kubernetes는 버린다
    # 모델이 원문에 없는 기간을 지어내면 날짜를 채우지 않는다
    invented = structure_candidate(_Experience(title="x", period="2020.01 - 2020.12"), blocks)
    assert (invented.started_on, invented.ended_on) == (None, None)
    # 모르는 종류는 other
    assert structure_candidate(_Experience(title="x", kind="job"), blocks).kind == "other"


# ---------------------------------------------------------------- HTTP


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def client_for(db, tmp_path, people):
    app = FastAPI()
    app.include_router(
        build_router(
            CoreDeps(
                identity=Tokens(people),
                db=db,
                blobs=LocalBlobStore(tmp_path / "blobs"),
                max_upload_bytes=4096,
            )
        )
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t")


async def test_http_experience_structure(db, people, tmp_path):
    ids = await _seed(db, people["owner"])
    theirs = await brain.create_experience(db, people["other"], title="남의 경력", kind="work")
    async with client_for(db, tmp_path, people) as c:
        r = await c.get(
            "/core/experiences",
            params=[("kind", "work"), ("kind", "project"), ("sort", "start")],
            headers=auth("viewer"),
        )
        assert r.status_code == 200, r.text
        assert r.headers["x-total-count"] == "2"
        assert [e["id"] for e in r.json()] == [str(ids["proj"]), str(ids["work"])]
        work = r.json()[1]
        assert {k: work[k] for k in ("kind", "started_on", "ended_on", "ongoing", "tags")} == {
            "kind": "work",
            "started_on": "2021-03",
            "ended_on": "2023-02",
            "ongoing": False,
            "tags": ["Python", "PostgreSQL"],
        }
        assert (work["children_count"], work["parent_id"]) == (1, None)
        r = await c.get(
            "/core/experiences",
            params=[("tag", "rust"), ("tag", "kafka"), ("limit", "1"), ("offset", "1")],
            headers=auth("viewer"),
        )
        assert r.headers["x-total-count"] == "2" and len(r.json()) == 1
        r = await c.get(
            "/core/experiences", params={"status": "needs_review"}, headers=auth("viewer")
        )
        assert [e["id"] for e in r.json()] == [str(ids["proj"])]
        r = await c.get("/core/experiences", params={"year": 2019}, headers=auth("viewer"))
        assert [e["id"] for e in r.json()] == [str(ids["school"])]
        r = await c.get(
            "/core/experiences",
            params={"q": "오픈소스", "top_level": "true"},
            headers=auth("viewer"),
        )
        assert [e["id"] for e in r.json()] == [str(ids["now"])]
        for bad in ({"sort": "x"}, {"status": "x"}, {"year": 1}, {"limit": 0}, {"kind": "job"}):
            r = await c.get("/core/experiences", params=bad, headers=auth("viewer"))
            assert r.status_code == 422, bad

        r = await c.get("/core/brain/overview", headers=auth("viewer"))
        assert r.status_code == 200, r.text
        o = r.json()
        assert o["kinds"] == {"work": 1, "project": 1, "education": 1, "activity": 1, "other": 1}
        assert (o["needs_review"], o["pending_claims"], o["undated"]) == (1, 1, 1)
        assert o["tags"][0] == {"tag": "Python", "count": 2}
        assert len(o["recent"]) == 5 and o["year_min"] == 2016

        body = {
            "title": "새 프로젝트 (합성)",
            "kind": "project",
            "parent_id": str(ids["work"]),
            "started_on": "2022-07",
            "ongoing": True,
            "tags": ["Go", " go "],
        }
        assert (
            await c.post("/core/experiences", json=body, headers=auth("viewer"))
        ).status_code == 403
        r = await c.post("/core/experiences", json=body, headers=auth("editor"))
        assert r.status_code == 201, r.text
        new_id = r.json()["id"]
        r = await c.get(f"/core/experiences/{new_id}", headers=auth("viewer"))
        got = r.json()
        assert (got["parent_title"], got["tags"], got["ongoing"], got["ended_on"]) == (
            "Acme 백엔드 (합성)",
            ["Go"],
            True,
            None,
        )
        r = await c.get(f"/core/experiences/{ids['work']}", headers=auth("viewer"))
        assert {ch["id"] for ch in r.json()["children"]} == {str(ids["proj"]), new_id}

        # 검증 오류 코드
        cases = [
            ({**body, "parent_id": str(ids["proj"])}, 409, "parent_is_child"),
            ({**body, "parent_id": str(theirs)}, 422, "parent_not_found"),
            ({**body, "parent_id": str(uuid4())}, 422, "parent_not_found"),
            ({**body, "kind": "job"}, 422, "invalid_experience_kind"),
            ({**body, "ended_on": "2022-01", "ongoing": False}, 422, "end_before_start"),
            ({**body, "ended_on": "2023-01"}, 422, "ongoing_with_end"),
            ({**body, "tags": [f"t{i}" for i in range(31)]}, 422, "too_many_tags"),
            ({**body, "tags": ["x" * 41]}, 422, "tag_too_long"),
        ]
        for payload, code, detail in cases:
            r = await c.post("/core/experiences", json=payload, headers=auth("editor"))
            assert (r.status_code, r.json()["detail"]) == (code, detail), payload
        r = await c.post(
            "/core/experiences", json={**body, "started_on": "2022-7"}, headers=auth("editor")
        )
        assert r.status_code == 422

        # 고치기: 준 칸만, 돌려주는 값은 고친 경험
        r = await c.patch(
            f"/core/experiences/{ids['undated']}",
            json={"kind": "other", "started_on": "2019-05", "tags": ["SQL"]},
            headers=auth("editor"),
        )
        assert r.status_code == 200, r.text
        assert (r.json()["started_on"], r.json()["tags"], r.json()["title"]) == (
            "2019-05",
            ["SQL"],
            "날짜 모름 (합성)",
        )
        r = await c.patch(
            f"/core/experiences/{ids['work']}",
            json={"parent_id": str(ids["school"])},
            headers=auth("editor"),
        )
        assert (r.status_code, r.json()["detail"]) == (409, "experience_has_children")
        r = await c.patch(
            f"/core/experiences/{ids['work']}", json={"kind": None}, headers=auth("editor")
        )
        assert r.status_code == 422
        r = await c.patch(
            f"/core/experiences/{theirs}", json={"title": "x"}, headers=auth("editor")
        )
        assert r.status_code == 404
        r = await c.patch(
            f"/core/experiences/{ids['proj']}", json={"parent_id": None}, headers=auth("editor")
        )
        assert (r.status_code, r.json()["parent_id"]) == (200, None)

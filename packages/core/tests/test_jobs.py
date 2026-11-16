"""작업 큐 운영: heartbeat(lease 연장), NOTIFY 깨우기, 주기 유지보수. 모든 자료는 합성이다.

작업 종류는 시험마다 새로 만든다 (claim_job은 공간을 가리지 않고 종류로만 집는다).
"""

import asyncio
import threading
import time
from datetime import timedelta
from uuid import uuid4

import pytest
from career_core.db import Job, JobPosting
from career_core.db.base import utcnow
from career_core.domain.workspace import Role, scopes_for_role
from career_core.jobs.maintenance import (
    MAINTENANCE_LOCK_KEY,
    MaintenanceTask,
    maintenance_loop,
    run_maintenance,
    workspace_ids,
)
from career_core.jobs.wake import JobWaker, libpq_kwargs, parse_payload
from career_core.jobs.worker import JobHandler, run_once
from career_core.ports import ActorContext
from career_core.usecases.parsing import StepOutcome
from career_core.usecases.workspaces import create_workspace
from sqlalchemy import func, select, text

pytestmark = pytest.mark.anyio


@pytest.fixture
async def owner(db):
    ws, actor = await create_workspace(db, name="A", owner_display_name="alice")
    return ActorContext(actor, ws, scopes_for_role(Role.OWNER))


def new_kind() -> str:
    return f"test-{uuid4().hex[:12]}"


async def enqueue(db, ws, kind, *, delay: float = 0) -> Job:
    async with db.workspace_session(ws) as s:
        job = Job(
            workspace_id=ws,
            kind=kind,
            target_id=uuid4(),
            available_at=utcnow() + timedelta(seconds=delay),
        )
        s.add(job)
    return job


async def job_row(db, ws, job_id) -> Job:
    async with db.workspace_session(ws) as s:
        return await s.get(Job, job_id)


# ---------------------------------------------------------------- heartbeat


async def _race(db, kind, *, heartbeat: float, work_seconds: float = 2.5):
    """worker A가 lease(1초)보다 오래 일하는 동안 worker B가 계속 집으려 한다."""
    calls = {"a": 0, "b": 0}

    async def slow(ws, target):
        calls["a"] += 1
        await asyncio.sleep(work_seconds)
        raise RuntimeError("late failure")  # lease를 잃었다면 결과를 남기면 안 된다

    async def fast(ws, target):
        calls["b"] += 1
        return StepOutcome(done=True)

    a = asyncio.create_task(
        run_once(db, {kind: JobHandler(run=slow)}, lease_seconds=1, heartbeat_seconds=heartbeat)
    )
    await asyncio.sleep(0.1)
    while not a.done():
        await run_once(db, {kind: JobHandler(run=fast)}, lease_seconds=1, heartbeat_seconds=0)
        await asyncio.sleep(0.2)
    await a
    return calls


async def test_heartbeat_keeps_a_long_job_from_being_stolen(db, owner):
    kind = new_kind()
    job = await enqueue(db, owner.workspace_id, kind)
    calls = await _race(db, kind, heartbeat=0.25)
    row = await job_row(db, owner.workspace_id, job.id)
    assert calls == {"a": 1, "b": 0}
    assert (row.status, row.attempts, row.last_error) == ("queued", 1, "RuntimeError")


async def test_without_heartbeat_the_lease_expires_and_the_loser_does_not_overwrite(db, owner):
    kind = new_kind()
    job = await enqueue(db, owner.workspace_id, kind)
    calls = await _race(db, kind, heartbeat=0)
    row = await job_row(db, owner.workspace_id, job.id)
    # B가 lease가 끝난 작업을 다시 집어 끝냈다. 늦게 끝난 A의 실패는 B의 결과를 덮지 않는다.
    assert calls == {"a": 1, "b": 1}
    assert (row.status, row.attempts, row.last_error) == ("done", 2, None)


async def test_heartbeat_extends_a_run_step_longer_than_the_lease(db, owner):
    """실행(run)의 한 단계가 lease보다 길어도 다른 worker가 그 단계를 다시 하지 않는다."""
    from career_core.domain.runs import RUN_JOB
    from career_core.jobs.worker import run_handlers
    from career_core.usecases import runs as run_uc
    from career_core.usecases.runs import RunDeps, StepResult, Workflow

    steps = []

    async def long_step(ctx):
        steps.append(ctx.step)
        await asyncio.sleep(2.5)
        return StepResult(position={"done": True})

    wf = Workflow("long", "runs.start", (long_step,), validate=lambda d: d)
    deps = RunDeps(db=db, workflows={"long": wf})
    run = await run_uc.start_run(deps, owner, "long", {})
    handlers = run_handlers(deps)
    assert set(handlers) == {RUN_JOB}

    a = asyncio.create_task(run_once(db, handlers, lease_seconds=1, heartbeat_seconds=0.25))
    await asyncio.sleep(0.1)
    while not a.done():
        await run_once(db, handlers, lease_seconds=1)
        await asyncio.sleep(0.2)
    assert await a
    view = await run_uc.get_run(db, owner, run.id)
    assert (view.status, view.resumes, steps) == ("done", 0, [0])


# ---------------------------------------------------------------- NOTIFY 깨우기


def test_payload_parsing():
    assert parse_payload("parse 0") == ("parse", 0.0)
    assert parse_payload("run 12.5") == ("run", 12.5)
    assert parse_payload("garbage") is None
    assert parse_payload("x nan") is None
    assert parse_payload("x -1") is None


def test_libpq_kwargs_keep_password_out_of_a_url_string():
    kw = libpq_kwargs("postgresql://u:p%40ss@db.example:6543/brain?sslmode=require")
    assert kw == {
        "host": "db.example",
        "port": 6543,
        "user": "u",
        "password": "p@ss",
        "dbname": "brain",
        "sslmode": "require",
    }


async def test_enqueue_wakes_a_listening_worker(db, owner):
    kind = new_kind()
    async with JobWaker(db.engine.url, [kind]) as waker:
        assert await waker.wait_listening(5)
        stop = asyncio.Event()
        await waker.wait(stop, 5)  # 연결 직후의 한 번 확인 신호
        waker.clear()
        waiting = asyncio.create_task(waker.wait(stop, 30))
        await asyncio.sleep(0.05)
        t0 = time.monotonic()
        await enqueue(db, owner.workspace_id, kind)
        await asyncio.wait_for(waiting, 5)
        assert time.monotonic() - t0 < 2
        assert waker.notifications == 1


async def test_other_kinds_and_rolled_back_jobs_do_not_wake(db, owner):
    kind = new_kind()
    async with JobWaker(db.engine.url, [kind]) as waker:
        assert await waker.wait_listening(5)
        stop = asyncio.Event()
        await waker.wait(stop, 5)
        waker.clear()
        await enqueue(db, owner.workspace_id, new_kind())
        with pytest.raises(RuntimeError):
            async with db.workspace_session(owner.workspace_id) as s:
                s.add(Job(workspace_id=owner.workspace_id, kind=kind, target_id=uuid4()))
                await s.flush()
                raise RuntimeError("rollback")
        t0 = time.monotonic()
        await waker.wait(stop, 0.5)
        assert time.monotonic() - t0 >= 0.45
        assert waker.notifications == 0


async def test_delayed_job_wakes_when_it_becomes_available(db, owner):
    kind = new_kind()
    async with JobWaker(db.engine.url, [kind]) as waker:
        assert await waker.wait_listening(5)
        stop = asyncio.Event()
        await waker.wait(stop, 5)
        waker.clear()
        await enqueue(db, owner.workspace_id, kind, delay=1.0)
        await asyncio.sleep(0.2)  # 알림 도착
        t0 = time.monotonic()
        await waker.wait(stop, 30)
        waited = time.monotonic() - t0
        assert 0.5 < waited < 2.0
        handled = []

        async def ok(ws, target):
            handled.append(target)
            return StepOutcome(done=True)

        assert await run_once(db, {kind: JobHandler(run=ok)})  # 깨어난 시점에는 집을 수 있다


async def test_heartbeat_does_not_notify(db, owner):
    """lease 연장(locked_until만 바꿈)은 알림을 보내지 않는다 (trigger는 status·available_at만)."""
    kind = new_kind()
    job = await enqueue(db, owner.workspace_id, kind)
    async with JobWaker(db.engine.url, [kind]) as waker:
        assert await waker.wait_listening(5)
        await waker.wait(asyncio.Event(), 5)
        async with db.workspace_session(owner.workspace_id) as s:
            row = await s.get(Job, job.id)
            row.locked_until = utcnow()
        await asyncio.sleep(0.3)
        assert waker.notifications == 0


# ---------------------------------------------------------------- 주기 유지보수


async def old_draft(db, ws, actor_id, url):
    async with db.workspace_session(ws) as s:
        s.add(
            JobPosting(
                id=uuid4(),
                workspace_id=ws,
                canonical_url=url,
                source_url=url,
                status="draft",
                created_by=actor_id,
                created_at=utcnow() - timedelta(days=8),
            )
        )


async def drafts(db, ws) -> int:
    async with db.workspace_session(ws) as s:
        return await s.scalar(select(func.count()).select_from(JobPosting))


async def test_maintenance_purges_every_workspace(db):
    spaces = []
    for name in ("A", "B"):
        ws, actor = await create_workspace(db, name=name, owner_display_name="me")
        await old_draft(db, ws, actor, f"https://jobs.example.com/{uuid4().hex}")
        spaces.append(ws)
    assert set(spaces) <= set(await workspace_ids(db))

    report = await run_maintenance(db)
    assert report.ran and report.errors == 0
    assert report.counts["purge_unconfirmed_postings"] >= 2
    assert [await drafts(db, ws) for ws in spaces] == [0, 0]
    again = await run_maintenance(db)  # 멱등
    assert again.ran and again.counts["purge_unconfirmed_postings"] == 0


async def test_maintenance_skips_while_another_worker_holds_the_lock(db, owner):
    seen = []

    async def task(db_, ws):
        seen.append(ws)
        return 0

    tasks = [MaintenanceTask("t", task)]
    async with db.engine.connect() as other, other.begin():
        assert await other.scalar(
            text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": MAINTENANCE_LOCK_KEY}
        )
        assert (await run_maintenance(db, tasks)).ran is False
    assert seen == []
    assert (await run_maintenance(db, tasks)).ran is True
    assert owner.workspace_id in seen


async def test_concurrent_maintenance_runs_once(db, owner):
    started = []

    async def slow(db_, ws):
        started.append(ws)
        await asyncio.sleep(0.05)
        return 1

    tasks = [MaintenanceTask("t", slow)]
    reports = await asyncio.gather(*(run_maintenance(db, tasks) for _ in range(3)))
    assert sorted(r.ran for r in reports) == [False, False, True]
    assert len(started) == len(set(started))


async def test_one_failing_workspace_does_not_stop_the_rest(db, owner):
    other, _ = await create_workspace(db, name="B", owner_display_name="bob")
    done = []

    async def flaky(db_, ws):
        if ws == owner.workspace_id:
            raise RuntimeError("boom")
        done.append(ws)
        return 1

    report = await run_maintenance(db, [MaintenanceTask("t", flaky)])
    assert report.errors == 1 and other in done


async def test_maintenance_loop_runs_on_interval_and_can_be_off(db, owner):
    stop = asyncio.Event()
    assert await maintenance_loop(db, stop, interval_seconds=0) == 0
    count = 0

    async def task(db_, ws):
        nonlocal count
        if ws == owner.workspace_id:
            count += 1
        return 0

    loop = asyncio.create_task(
        maintenance_loop(
            db,
            stop,
            interval_seconds=0.2,
            first_delay_seconds=0,
            tasks=[MaintenanceTask("t", task)],
        )
    )
    # 공유 DB에는 공간이 많아 한 번 도는 데 걸리는 시간이 들쭉날쭉하다: 두 번 돌 때까지 기다린다
    deadline = asyncio.get_running_loop().time() + 10
    while count < 2 and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)
    stop.set()
    runs = await asyncio.wait_for(loop, 10)
    assert runs >= 2 and count >= 2


async def test_workspace_listing_does_not_open_workspaces_to_the_app_role(db, owner):
    """공간 목록 함수의 설정을 앱 계정이 직접 켜도 workspaces 행은 보이지 않는다 (정책은 소유자 전용)."""
    async with db.system_session() as s:
        await s.execute(text("SELECT set_config('career.maintenance_scan', 'on', true)"))
        assert await s.scalar(text("SELECT count(*) FROM core.workspaces")) == 0


# ---------------------------------------------------------------- 다음 작업 예정 시각 (c0020)


async def test_next_due_covers_queued_and_expired_lease_jobs(db, owner):
    from career_core.jobs.worker import next_due_seconds

    kind = new_kind()
    assert await next_due_seconds(db, [kind]) is None
    await enqueue(db, owner.workspace_id, kind, delay=30)
    assert 25 < await next_due_seconds(db, [kind]) <= 30
    await enqueue(db, owner.workspace_id, new_kind(), delay=0)  # 다른 종류는 세지 않는다
    assert 25 < await next_due_seconds(db, [kind]) <= 30
    async with db.workspace_session(owner.workspace_id) as s:
        s.add(
            Job(
                workspace_id=owner.workspace_id,
                kind=kind,
                target_id=uuid4(),
                status="running",
                attempts=1,
                locked_until=utcnow() + timedelta(seconds=5),
            )
        )
    assert 3 < await next_due_seconds(db, [kind]) <= 5  # lease가 끝나면 다시 집을 수 있다
    await enqueue(db, owner.workspace_id, kind, delay=-10)
    assert await next_due_seconds(db, [kind]) == 0  # 지난 시각은 0


async def test_next_due_exposes_only_one_timestamp(db, owner):
    """공간 문맥 없이 부르는 함수지만 시각 하나만 돌려준다 (공간·대상 ID·행 수 없음)."""
    kind = new_kind()
    other, _ = await create_workspace(db, name="B", owner_display_name="bob")
    await enqueue(db, other, kind, delay=60)
    async with db.system_session() as s:
        result = await s.scalar(
            text(
                "SELECT pg_get_function_result(p.oid) FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'core' AND p.proname = 'next_job_due'"
            )
        )
        assert result == "timestamp with time zone"
        assert await s.scalar(text("SELECT core.next_job_due(:k)"), {"k": [kind]}) is not None
        assert await s.scalar(text("SELECT count(*) FROM core.jobs")) == 0  # 직접 조회는 막힘


# ---------------------------------------------------------------- 유지보수 마지막 실행 (c0020)


async def test_scheduled_maintenance_runs_once_per_interval_across_workers(db, owner):
    """worker 여러 개가 각자 주기 실행을 해도 간격 안에서는 한 번만 돈다 (잠금만으로는 차례마다 돈다)."""
    from career_core.jobs.maintenance import maintenance_status

    seen = []

    async def task(db_, ws):
        seen.append(ws)
        return 1 if ws == owner.workspace_id else 0

    tasks = [MaintenanceTask("t", task)]
    first = await run_maintenance(db, tasks, interval_seconds=3600)  # 지금 바로 (명령)
    assert first.ran and first.next_at is not None
    seen.clear()
    later = [await run_maintenance(db, tasks, interval_seconds=3600, force=False) for _ in range(3)]
    assert [r.skipped for r in later] == ["not_due"] * 3 and seen == []
    assert all(r.next_at == first.next_at for r in later)

    status = await maintenance_status(db, owner.workspace_id)
    assert status.interval_seconds == 3600 and not status.running
    assert status.next_at == first.next_at
    assert status.last_finished_at >= status.last_started_at
    assert (status.counts, status.errors) == ({"t": 1}, 0)  # 이 공간의 결과만

    # 한 일이 없던 공간은 행을 쓰지 않는다: 이번 실행의 결과는 빈 값, 마지막 정리 시각은 그대로
    owner_changed = status.last_changed_at
    other, _ = await create_workspace(db, name="B", owner_display_name="bob")
    await run_maintenance(
        db, [MaintenanceTask("t", lambda db_, ws: _zero())], interval_seconds=3600
    )
    mine, theirs = (
        await maintenance_status(db, owner.workspace_id),
        await maintenance_status(db, other),
    )
    assert (mine.counts, mine.last_changed_at) == ({}, owner_changed)
    assert (theirs.counts, theirs.last_changed_at) == ({}, None)
    # 짧은 간격이면 다시 차례가 온다
    again = await run_maintenance(db, tasks, interval_seconds=0.01, force=False)
    assert again.ran


async def _zero() -> int:
    return 0


async def test_maintenance_results_are_workspace_scoped(db, owner):
    other, _ = await create_workspace(db, name="B", owner_display_name="bob")

    async def one(db_, ws):
        return 1

    await run_maintenance(db, [MaintenanceTask("t", one)])
    async with db.workspace_session(owner.workspace_id) as s:
        rows = (await s.execute(text("SELECT workspace_id FROM core.maintenance_results"))).all()
    assert [r.workspace_id for r in rows] == [owner.workspace_id]
    async with db.workspace_session(other) as s:
        rows = (await s.execute(text("SELECT workspace_id FROM core.maintenance_results"))).all()
    assert [r.workspace_id for r in rows] == [other]
    async with db.system_session() as s:
        assert await s.scalar(text("SELECT count(*) FROM core.maintenance_results")) == 0
    # 설치 전체 일정 표는 앱 계정이 직접 읽을 수 없다 (함수로 시각만)
    with pytest.raises(Exception, match="permission denied"):
        async with db.system_session() as s:
            await s.execute(text("SELECT * FROM core.maintenance_schedule"))


async def test_maintenance_loop_waits_for_the_next_run_another_worker_set(db, owner, monkeypatch):
    """간격 안에 다른 worker가 이미 했으면 DB가 알려 준 다음 예정 시각까지 쉰다."""
    from career_core.jobs import maintenance as m

    calls = []

    async def task(db_, ws):
        calls.append(time.monotonic())
        return 0

    tasks = [MaintenanceTask("t", task)]
    await run_maintenance(db, tasks, interval_seconds=1.0)  # 다른 worker가 방금 했다
    calls.clear()
    monkeypatch.setattr(m.random, "uniform", lambda a, b: 0.0 if a == 0 else a)
    stop = asyncio.Event()
    t0 = time.monotonic()
    loop = asyncio.create_task(
        maintenance_loop(db, stop, interval_seconds=1.0, first_delay_seconds=0, tasks=tasks)
    )
    while not calls and time.monotonic() - t0 < 5:
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(loop, 5)
    # 첫 시도는 간격 안이라 건너뛰고, 다음 예정 시각(약 1초 뒤)에 한다. 간격×0.9 다시 시도가 아니다
    assert calls and 0.7 < calls[0] - t0 < 1.6


# ---------------------------------------------------------------- CPU를 오래 쓰는 단계와 heartbeat


class _Competitor(threading.Thread):
    """다른 프로세스의 worker 흉내: 자기 이벤트 루프·DB 연결로 0.2초마다 같은 종류를 집으려 한다."""

    def __init__(self, url, kind: str) -> None:
        super().__init__(daemon=True)
        self.url, self.kind = url, kind
        self.claimed: list = []
        self.stop = threading.Event()

    def run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        from career_core.db import Database
        from sqlalchemy.pool import NullPool

        other = Database(self.url, poolclass=NullPool)
        try:
            while not self.stop.is_set():
                async with other.system_session() as s:
                    row = (
                        await s.execute(
                            text("SELECT * FROM core.claim_job(:k, 1)"), {"k": [self.kind]}
                        )
                    ).first()
                if row is not None:
                    self.claimed.append(row.job_id)
                await asyncio.sleep(0.2)
        finally:
            await other.dispose()


class _BlockingParser:
    """CPU를 오래 쓰는 파서 흉내 (큰 스캔 PDF): 호출한 스레드를 2.5초 막는다."""

    def __init__(self) -> None:
        self.threads: list[str] = []

    def parse(self, data: bytes):
        from career_core.ports import LocalParseResult, ParsedBlock

        self.threads.append(threading.current_thread().name)
        time.sleep(2.5)
        return LocalParseResult(
            blocks=(ParsedBlock(page=None, text="합성 본문"),), pages_total=None
        )


async def _parse_job(db, owner, tmp_path, parser):
    from career_core.adapters.blobstore import LocalBlobStore
    from career_core.policies import ProviderEgressPolicy
    from career_core.usecases.parsing import ParsingDeps
    from career_core.usecases.sources import upload_source

    blobs = LocalBlobStore(tmp_path)
    up = await upload_source(
        db,
        blobs,
        owner,
        filename="note.txt",
        data=f"합성 메모 {uuid4()}".encode(),
        declared_mime="text/plain",
        max_bytes=1024,
    )
    deps = ParsingDeps(
        db=db,
        blobs=blobs,
        egress=ProviderEgressPolicy(),
        local_parsers={"text/plain": parser},
    )
    return up, deps


async def test_heartbeat_keeps_the_lease_during_a_cpu_heavy_parse(pg, db, owner, tmp_path):
    """파싱(동기 CPU 작업)은 스레드에서 돈다: 그동안 이벤트 루프의 heartbeat가 lease(1초)를 계속 늘려
    다른 worker(별도 스레드·연결)가 그 작업을 다시 집지 못한다."""
    from career_core.jobs.worker import parsing_handlers
    from career_core.usecases.sources import PARSE_JOB

    parser = _BlockingParser()
    up, deps = await _parse_job(db, owner, tmp_path, parser)
    rival = _Competitor(pg.app, PARSE_JOB)
    rival.start()
    try:
        assert await run_once(db, parsing_handlers(deps), lease_seconds=1, heartbeat_seconds=0.25)
    finally:
        rival.stop.set()
        rival.join(5)
    assert rival.claimed == []
    assert parser.threads and parser.threads[0] != threading.current_thread().name
    async with db.workspace_session(owner.workspace_id) as s:
        job = await s.scalar(select(Job).where(Job.target_id == up.parse_run_id))
    assert (job.status, job.attempts) == ("done", 1)


async def test_a_step_that_blocks_the_loop_loses_its_lease(pg, db, owner):
    """대조: 같은 일을 이벤트 루프에서 하면 heartbeat가 못 돌아 lease가 끝나고 다른 worker가 집는다."""
    kind = new_kind()
    await enqueue(db, owner.workspace_id, kind)

    async def blocking(ws, target):
        time.sleep(2.5)  # noqa: ASYNC251 - 일부러 루프를 막는다
        return StepOutcome(done=True)

    rival = _Competitor(pg.app, kind)
    rival.start()
    try:
        await run_once(
            db, {kind: JobHandler(run=blocking)}, lease_seconds=1, heartbeat_seconds=0.25
        )
    finally:
        rival.stop.set()
        rival.join(5)
    assert (
        rival.claimed
    )  # lease가 끝나 다른 worker가 다시 집었다 (이 시험에서는 그 lease도 끝나 여러 번)

"""worker 루프 + LISTEN/NOTIFY 깨우기 + 같이 도는 유지보수. 실제 DB, 합성 작업."""

import asyncio
import time
from uuid import uuid4

import pytest
from career_core.db import Database, Job
from career_core.jobs.wake import JobWaker
from career_core.jobs.worker import JobHandler, run_once
from career_core.usecases.parsing import StepOutcome
from career_core.usecases.workspaces import create_workspace
from personal_api.runner import (
    WorkerRuntime,
    job_steps,
    maintenance_interval_seconds,
    run_runtime,
    worker_listen,
    worker_poll_seconds,
)
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.anyio


async def enqueue(db, ws, kind):
    async with db.workspace_session(ws) as s:
        s.add(Job(workspace_id=ws, kind=kind, target_id=uuid4()))


def handlers_for(kind, seen):
    async def ok(ws, target):
        seen.append(time.monotonic())
        return StepOutcome(done=True)

    return {kind: JobHandler(run=ok)}


async def _run_until(rt, stop, seen, n, *, poll, timeout):
    loop = asyncio.create_task(run_runtime(rt, stop, poll_seconds=poll))
    deadline = time.monotonic() + timeout
    while len(seen) < n and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    stop.set()
    return await asyncio.wait_for(loop, 5)


async def test_idle_worker_with_long_poll_wakes_on_notify(db):
    ws, _ = await create_workspace(db, name="w", owner_display_name="me")
    kind, seen = f"t-{uuid4().hex[:10]}", []
    handlers = handlers_for(kind, seen)
    calls = 0

    async def step():
        nonlocal calls
        calls += 1
        return await run_once(db, handlers)

    async with JobWaker(db.engine.url, handlers) as waker:
        assert await waker.wait_listening(5)
        stop = asyncio.Event()
        rt = WorkerRuntime(step, waker)
        task = asyncio.create_task(run_runtime(rt, stop, poll_seconds=30))
        await asyncio.sleep(0.3)  # 유휴 상태로 들어감
        idle_calls = calls
        t0 = time.monotonic()
        await enqueue(db, ws, kind)
        while not seen and time.monotonic() - t0 < 5:
            await asyncio.sleep(0.01)
        stop.set()
        assert await asyncio.wait_for(task, 5) == 1
    assert seen and seen[0] - t0 < 1.0  # fallback 30초가 아니라 알림으로 바로
    assert idle_calls <= 2  # 시작 확인 + 연결 직후 확인. 1초마다 확인하지 않는다


async def test_lost_notifications_fall_back_to_polling(db):
    """LISTEN 연결이 안 되는(알림을 전부 놓치는) worker도 fallback 간격마다 작업을 집는다."""
    ws, _ = await create_workspace(db, name="w", owner_display_name="me")
    kind, seen = f"t-{uuid4().hex[:10]}", []
    handlers = handlers_for(kind, seen)
    dead = db.engine.url.set(port=1)  # 닫힌 포트: 연결 실패만 계속
    async with JobWaker(dead, handlers, reconnect_max_seconds=0.1) as waker:
        stop = asyncio.Event()
        rt = WorkerRuntime(lambda: run_once(db, handlers), waker)
        task = asyncio.create_task(run_runtime(rt, stop, poll_seconds=0.3))
        await asyncio.sleep(0.1)
        await enqueue(db, ws, kind)
        t0 = time.monotonic()
        while not seen and time.monotonic() - t0 < 5:
            await asyncio.sleep(0.01)
        stop.set()
        assert await asyncio.wait_for(task, 5) == 1
        assert not waker.listening


async def test_listener_reconnects_after_the_connection_is_killed(pg, db):
    ws, _ = await create_workspace(db, name="w", owner_display_name="me")
    kind, seen = f"t-{uuid4().hex[:10]}", []
    handlers = handlers_for(kind, seen)
    admin = Database(pg.owner, poolclass=NullPool)
    try:
        async with JobWaker(db.engine.url, handlers, reconnect_max_seconds=0.2) as waker:
            assert await waker.wait_listening(5)
            async with admin.system_session() as s:
                from sqlalchemy import text

                await s.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND query LIKE 'LISTEN career_jobs%'"
                    )
                )
            deadline = time.monotonic() + 5
            while waker.listening and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert await waker.wait_listening(5)  # 다시 연결
            stop = asyncio.Event()
            await waker.wait(stop, 5)
            waker.clear()
            waiting = asyncio.create_task(waker.wait(stop, 30))
            await enqueue(db, ws, kind)
            await asyncio.wait_for(waiting, 5)
    finally:
        await admin.dispose()


async def test_job_steps_runs_maintenance_next_to_the_worker(pg, monkeypatch):
    ran = []

    async def fake_loop(db, stop, *, interval_seconds, **kw):
        ran.append(interval_seconds)
        await stop.wait()
        return 0

    monkeypatch.setattr("personal_api.runner.maintenance_loop", fake_loop)
    source = job_steps(
        pg.app.render_as_string(hide_password=False), lambda d: {}, maintenance_seconds=5
    )
    stop = asyncio.Event()
    async with source() as rt:
        assert rt.waker is None
        task = asyncio.create_task(run_runtime(rt, stop, poll_seconds=0.05))
        await asyncio.sleep(0.1)
        stop.set()
        await asyncio.wait_for(task, 5)
    assert ran == [5]


@pytest.mark.parametrize(
    ("env", "listen", "poll", "maintenance"),
    [
        ({}, True, 30.0, 21600.0),
        ({"CAREER_WORKER_LISTEN": "0"}, False, 1.0, 21600.0),
        (
            {"CAREER_WORKER_POLL_SECONDS": "5", "CAREER_MAINTENANCE_INTERVAL_SECONDS": "0"},
            True,
            5.0,
            0.0,
        ),
    ],
)
def test_worker_env_defaults(monkeypatch, env, listen, poll, maintenance):
    for name in (
        "CAREER_WORKER_LISTEN",
        "CAREER_WORKER_POLL_SECONDS",
        "CAREER_MAINTENANCE_INTERVAL_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    got = worker_listen()
    assert (got, worker_poll_seconds(got), maintenance_interval_seconds()) == (
        listen,
        poll,
        maintenance,
    )

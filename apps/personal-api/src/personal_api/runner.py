"""작업 worker 루프.

- `personal-api worker`: 이 루프를 단독 프로세스로 돈다 (SIGTERM이면 지금 작업을 끝내고 멈춤).
- `personal-api serve`: 같은 루프를 API 프로세스 안의 별도 스레드(자기 이벤트 루프)에서 돈다.
  파싱·HTML 추출처럼 CPU를 잠깐 쓰는 작업이 API 이벤트 루프를 막지 않게 하려는 분리다.

DB가 잠시 내려가도 루프는 죽지 않고 간격을 늘려 다시 시도한다. 처리 중이던 작업은
lease(기본 300초, 처리 중에는 heartbeat로 연장)가 지나면 다른 worker가 다시 집는다.

대기 작업이 없을 때는 ``LISTEN career_jobs``(career_core.jobs.wake)로 새 작업 알림을 기다리고,
알림을 놓쳐도 되도록 긴 간격(기본 30초)마다 한 번 확인한다. LISTEN을 끄면 짧은 간격(기본 1초) 확인.
같은 이벤트 루프에서 주기 유지보수(career_core.jobs.maintenance)도 돈다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Protocol

from career_core.db import Database
from career_core.jobs.maintenance import maintenance_loop
from career_core.jobs.wake import JobWaker
from career_core.jobs.worker import JobHandler, next_due_seconds, run_once

log = logging.getLogger("personal_api.worker")

Step = Callable[[], Awaitable[bool]]
"""작업 하나를 처리했으면 True, 대기 작업이 없으면 False."""

MAX_ERROR_BACKOFF = 60.0
# 예정 시각이 이미 지났는데 집지 못했을 때(다른 worker가 먼저 집음, 시계 차이) 바로 다시 돌지 않게
MIN_DUE_DELAY = 0.05
DEFAULT_MAINTENANCE_SECONDS = 6 * 3600


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def worker_listen() -> bool:
    """``CAREER_WORKER_LISTEN`` (기본 1): 새 작업을 LISTEN/NOTIFY로 바로 알아챈다."""
    return _flag("CAREER_WORKER_LISTEN", True)


def worker_poll_seconds(listen: bool = False) -> float:
    """대기 작업이 없을 때 다시 확인할 때까지 쉬는 시간. 짧을수록 유휴 쿼리가 는다.

    LISTEN을 쓰면 알림을 놓쳤을 때의 안전 확인이라 길게(기본 30초), 아니면 기본 1초.
    """
    return float(os.environ.get("CAREER_WORKER_POLL_SECONDS", "30" if listen else "1"))


def maintenance_interval_seconds() -> float:
    """``CAREER_MAINTENANCE_INTERVAL_SECONDS`` (기본 6시간, 0이면 끔): 주기 유지보수 간격."""
    return float(
        os.environ.get("CAREER_MAINTENANCE_INTERVAL_SECONDS", str(DEFAULT_MAINTENANCE_SECONDS))
    )


class Waker(Protocol):
    def clear(self) -> None: ...

    async def wait(self, stop: asyncio.Event, timeout: float) -> None: ...


Background = Callable[[asyncio.Event], Awaitable[object]]
"""worker와 같은 루프에서 ``stop``까지 도는 일 (주기 유지보수)."""

NextDue = Callable[[], Awaitable[float | None]]
"""다음 작업을 집을 수 있을 때까지 남은 초 (없으면 None). ``career_core.jobs.worker.next_due_seconds``."""


@dataclass(frozen=True, slots=True)
class WorkerRuntime:
    step: Step
    waker: Waker | None = None
    background: Sequence[Background] = ()
    next_due: NextDue | None = None


async def _idle_delay(poll_seconds: float, next_due: NextDue | None) -> float:
    """쉴 시간: 다음 예정 시각까지, 단 ``poll_seconds``를 넘지 않는다 (조회 실패면 그냥 간격)."""
    if next_due is None:
        return poll_seconds
    try:
        left = await next_due()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - 조회 실패는 간격 확인으로 대신한다
        log.debug("next due lookup failed (%s)", type(exc).__name__)
        return poll_seconds
    if left is None:
        return poll_seconds
    return min(poll_seconds, max(left, MIN_DUE_DELAY))


async def work_loop(
    step: Step,
    stop: asyncio.Event,
    *,
    poll_seconds: float = 1.0,
    waker: Waker | None = None,
    next_due: NextDue | None = None,
) -> int:
    """``stop``이 켜질 때까지 작업을 처리한다. 처리한 작업 수를 돌려준다.

    ``waker``가 있으면 대기 작업이 없을 때 알림이나 ``poll_seconds`` 중 먼저 오는 것까지 쉰다.
    ``next_due``가 있으면 알림이 없는 예정 작업(lease 만료·미리 예약된 작업)의 시각에도 깨어난다.
    """
    processed = 0
    errors = 0
    while not stop.is_set():
        try:
            if waker is not None:
                waker.clear()  # 이 뒤에 온 알림은 다음 대기를 바로 끝낸다
            did = await step()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - DB 끊김 등: 루프는 살아서 다시 시도한다
            errors += 1
            delay = min(min(poll_seconds, 1.0) * 2**errors, MAX_ERROR_BACKOFF)
            log.warning("worker step failed (%s); retrying in %.0fs", type(exc).__name__, delay)
            await _wait(stop, delay)
            continue
        errors = 0
        if did:
            processed += 1
            continue
        delay = await _idle_delay(poll_seconds, next_due)
        if waker is not None:
            await waker.wait(stop, delay)
        else:
            await _wait(stop, delay)
    return processed


async def run_runtime(rt: WorkerRuntime, stop: asyncio.Event, *, poll_seconds: float) -> int:
    """작업 루프 + 같이 도는 일. 정상 종료면 같이 도는 일도 끝나길 기다리고, 취소면 같이 취소한다."""
    extra = [asyncio.create_task(fn(stop)) for fn in rt.background]
    try:
        processed = await work_loop(
            rt.step, stop, poll_seconds=poll_seconds, waker=rt.waker, next_due=rt.next_due
        )
    except BaseException:
        for t in extra:
            t.cancel()
        await asyncio.gather(*extra, return_exceptions=True)
        raise
    await asyncio.gather(*extra, return_exceptions=True)
    return processed


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    with suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)


async def run_worker(
    db: Database,
    handlers: Mapping[str, JobHandler],
    *,
    poll_seconds: float = 1.0,
    listen: bool = False,
    maintenance_seconds: float = 0,
) -> int:
    """단독 worker. SIGTERM/SIGINT를 받으면 지금 작업을 마치고 끝난다."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError, RuntimeError, ValueError):
            loop.add_signal_handler(sig, stop.set)
    async with _runtime(db, handlers, listen, maintenance_seconds) as rt:
        return await run_runtime(rt, stop, poll_seconds=poll_seconds)


@asynccontextmanager
async def _runtime(
    db: Database, handlers: Mapping[str, JobHandler], listen: bool, maintenance_seconds: float
) -> AsyncIterator[WorkerRuntime]:
    async with AsyncExitStack() as stack:
        waker = None
        if listen:
            waker = await stack.enter_async_context(JobWaker(db.engine.url, handlers))
        background: list[Background] = []
        if maintenance_seconds > 0:
            background.append(
                lambda stop: maintenance_loop(db, stop, interval_seconds=maintenance_seconds)
            )
        yield WorkerRuntime(
            lambda: run_once(db, handlers),
            waker,
            background,
            # 짧은 간격 확인(LISTEN 끔)이면 조회를 늘릴 뿐이라 쓰지 않는다
            next_due=(lambda: next_due_seconds(db, handlers)) if listen else None,
        )


StepSource = Callable[[], AbstractAsyncContextManager[Step | WorkerRuntime]]
"""worker 스레드의 이벤트 루프 안에서 열린다 (DB 엔진은 루프에 묶이므로 거기서 만든다)."""


def job_steps(
    database_url: str,
    build: Callable[[Database], Mapping[str, JobHandler]],
    *,
    listen: bool = False,
    maintenance_seconds: float = 0,
) -> StepSource:
    @asynccontextmanager
    async def source() -> AsyncIterator[WorkerRuntime]:
        db = Database(database_url, pool_size=2, max_overflow=2)
        try:
            handlers = build(db)
            async with _runtime(db, handlers, listen, maintenance_seconds) as rt:
                yield rt
        finally:
            await db.dispose()

    return source


class BackgroundWorker:
    """API 프로세스 안에서 도는 worker 스레드. ``start()`` / ``stop()``."""

    def __init__(self, steps: StepSource, *, poll_seconds: float = 1.0) -> None:
        self._steps = steps
        self._poll = poll_seconds
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._task: asyncio.Task[int] | None = None
        self._ready = threading.Event()
        self.processed = 0
        self.error: BaseException | None = None

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("worker already started")
        self._thread = threading.Thread(target=self._run, name="career-worker", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except asyncio.CancelledError:
            pass
        except BaseException as exc:  # noqa: BLE001 - 스레드 경계: 기록만 하고 API는 계속 돈다
            self.error = exc
            log.error("in-process worker stopped: %s", type(exc).__name__)
        finally:
            self._ready.set()

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._task = asyncio.current_task()
        self._ready.set()
        async with self._steps() as got:
            rt = got if isinstance(got, WorkerRuntime) else WorkerRuntime(got)
            self.processed = await run_runtime(rt, self._stop, poll_seconds=self._poll)

    def stop(self, timeout: float = 10.0) -> None:
        """지금 작업을 ``timeout``초까지 기다린 뒤, 안 끝나면 취소한다 (lease가 나중에 회수)."""
        thread, loop = self._thread, self._loop
        if thread is None or loop is None or not thread.is_alive():
            return
        with suppress(RuntimeError):  # 루프가 이미 닫힘
            loop.call_soon_threadsafe(self._stop.set)
        thread.join(timeout)
        if thread.is_alive() and self._task is not None:
            log.warning(
                "in-process worker did not stop in %.0fs; cancelling the current job", timeout
            )
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(self._task.cancel)
            thread.join(5)

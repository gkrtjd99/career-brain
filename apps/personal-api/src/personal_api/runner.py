"""작업 worker 루프.

- `personal-api worker`: 이 루프를 단독 프로세스로 돈다 (SIGTERM이면 지금 작업을 끝내고 멈춤).
- `personal-api serve`: 같은 루프를 API 프로세스 안의 별도 스레드(자기 이벤트 루프)에서 돈다.
  파싱·HTML 추출처럼 CPU를 잠깐 쓰는 작업이 API 이벤트 루프를 막지 않게 하려는 분리다.

DB가 잠시 내려가도 루프는 죽지 않고 간격을 늘려 다시 시도한다. 처리 중이던 작업은
lease(기본 300초)가 지나면 다른 worker가 다시 집는다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress

from career_core.db import Database
from career_core.jobs.worker import JobHandler, run_once

log = logging.getLogger("personal_api.worker")

Step = Callable[[], Awaitable[bool]]
"""작업 하나를 처리했으면 True, 대기 작업이 없으면 False."""

MAX_ERROR_BACKOFF = 60.0


def worker_poll_seconds() -> float:
    """대기 작업이 없을 때 다시 볼 때까지 쉬는 시간. 짧을수록 반응이 빠르고 유휴 쿼리가 는다."""
    return float(os.environ.get("CAREER_WORKER_POLL_SECONDS", "1"))


async def work_loop(step: Step, stop: asyncio.Event, *, poll_seconds: float = 1.0) -> int:
    """``stop``이 켜질 때까지 작업을 처리한다. 처리한 작업 수를 돌려준다."""
    processed = 0
    errors = 0
    while not stop.is_set():
        try:
            did = await step()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - DB 끊김 등: 루프는 살아서 다시 시도한다
            errors += 1
            delay = min(poll_seconds * 2**errors, MAX_ERROR_BACKOFF)
            log.warning("worker step failed (%s); retrying in %.0fs", type(exc).__name__, delay)
            await _wait(stop, delay)
            continue
        errors = 0
        if did:
            processed += 1
        else:
            await _wait(stop, poll_seconds)
    return processed


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    with suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)


async def run_worker(
    db: Database, handlers: Mapping[str, JobHandler], *, poll_seconds: float = 1.0
) -> int:
    """단독 worker. SIGTERM/SIGINT를 받으면 지금 작업을 마치고 끝난다."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError, RuntimeError, ValueError):
            loop.add_signal_handler(sig, stop.set)
    return await work_loop(lambda: run_once(db, handlers), stop, poll_seconds=poll_seconds)


StepSource = Callable[[], AbstractAsyncContextManager[Step]]
"""worker 스레드의 이벤트 루프 안에서 열린다 (DB 엔진은 루프에 묶이므로 거기서 만든다)."""


def job_steps(
    database_url: str, build: Callable[[Database], Mapping[str, JobHandler]]
) -> StepSource:
    @asynccontextmanager
    async def source() -> AsyncIterator[Step]:
        db = Database(database_url, pool_size=2, max_overflow=2)
        try:
            handlers = build(db)
            yield lambda: run_once(db, handlers)
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
        async with self._steps() as step:
            self.processed = await work_loop(step, self._stop, poll_seconds=self._poll)

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

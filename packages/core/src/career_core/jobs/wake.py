"""유휴 worker 깨우기: PostgreSQL ``LISTEN career_jobs``.

``core.jobs``에 대기 행이 생기면 trigger(c0015)가 ``NOTIFY career_jobs, '<kind> <초>'``를 보낸다.
``<초>``는 그 작업을 집을 수 있을 때까지 남은 시간(DB 시계 기준)이다. 0이면 바로 깨우고,
양수(재시도 대기·한도 대기)면 그 시각에 깨어나도록 기억해 둔다.

알림은 힌트일 뿐이다. 연결이 끊기거나 알림을 놓쳐도(LISTEN 전 commit, 재연결 사이,
lease가 끝난 running 작업처럼 알림이 없는 경우) worker는 ``wait``의 긴 간격(fallback)마다
``claim_job``을 다시 부르므로 작업은 늦어질 뿐 빠지지 않는다. 재연결하면 한 번 바로 확인한다.

LISTEN 연결은 일반 풀과 별도의 autocommit 연결 하나다 (PgBouncer transaction 모드에서는
LISTEN이 동작하지 않으므로 ``CAREER_WORKER_LISTEN=0``으로 끄고 짧은 간격 확인을 쓴다).
"""

from __future__ import annotations

import asyncio
import heapq
import logging
import math
from collections.abc import Iterable
from contextlib import suppress
from typing import Any, Self

from sqlalchemy import URL

from career_core.db.session import normalize_url

log = logging.getLogger(__name__)

JOB_CHANNEL = "career_jobs"
# 기억해 둘 미래 시각 수의 상한. 넘치면 버리고 fallback 확인에 맡긴다.
MAX_PENDING_DUE = 1024
# 반올림·전달 지연으로 DB보다 조금 일찍 깨는 것을 막는 여유
DUE_SLACK_SECONDS = 0.01


def parse_payload(payload: str) -> tuple[str, float] | None:
    """``'<kind> <초>'`` → (kind, 초). 형식이 다르면 None (그래도 깨우기는 한다)."""
    kind, _, delay = payload.strip().rpartition(" ")
    try:
        seconds = float(delay)
    except ValueError:
        return None
    if not kind or not math.isfinite(seconds) or seconds < 0:
        return None
    return kind, seconds


def libpq_kwargs(url: str | URL) -> dict[str, Any]:
    """SQLAlchemy URL → psycopg 연결 인자 (비밀번호를 문자열 URL로 다시 만들지 않는다)."""
    u = normalize_url(url)
    kw: dict[str, Any] = {
        "host": u.host,
        "port": u.port,
        "user": u.username,
        "password": u.password,
        "dbname": u.database,
    }
    kw.update({k: v for k, v in u.query.items() if isinstance(v, str)})
    return {k: v for k, v in kw.items() if v is not None}


class JobWaker:
    """worker 하나의 깨우기 신호. ``async with JobWaker(url, kinds) as w:`` 안에서 쓴다.

    - ``clear()``: 작업을 집기 직전에 부른다. 그 뒤에 온 알림은 다음 ``wait``를 바로 끝낸다.
    - ``wait(stop, timeout)``: 알림·미래 작업 시각·``stop``·``timeout`` 중 먼저 오는 것까지 쉰다.
    """

    def __init__(
        self,
        url: str | URL,
        kinds: Iterable[str] | None = None,
        *,
        reconnect_max_seconds: float = 30.0,
    ) -> None:
        self._conninfo = libpq_kwargs(url)
        self._kinds = frozenset(kinds) if kinds is not None else None
        self._reconnect_max = reconnect_max_seconds
        self._event = asyncio.Event()
        self._resched = asyncio.Event()  # 새 미래 시각이 생겼다: 자고 있는 wait가 마감을 다시 계산
        self._due: list[float] = []
        self._task: asyncio.Task[None] | None = None
        self._listening = asyncio.Event()
        self.notifications = 0

    @property
    def listening(self) -> bool:
        return self._listening.is_set()

    async def wait_listening(self, timeout: float) -> bool:
        """테스트·시작 확인용: LISTEN이 걸릴 때까지 기다린다."""
        with suppress(TimeoutError):
            await asyncio.wait_for(self._listening.wait(), timeout)
        return self.listening

    async def __aenter__(self) -> Self:
        self._task = asyncio.create_task(self._listen(), name="career-job-listener")
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def clear(self) -> None:
        self._event.clear()

    def notify(self, payload: str) -> None:
        """알림 하나 반영. 연결 없이 테스트에서도 부를 수 있다."""
        parsed = parse_payload(payload)
        if parsed is None:
            self._event.set()
            return
        kind, seconds = parsed
        if self._kinds is not None and kind not in self._kinds:
            return
        self.notifications += 1
        if seconds <= 0:
            self._event.set()
        elif len(self._due) < MAX_PENDING_DUE:
            loop = asyncio.get_running_loop()
            heapq.heappush(self._due, loop.time() + seconds + DUE_SLACK_SECONDS)
            self._resched.set()

    async def wait(self, stop: asyncio.Event, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while not (self._event.is_set() or stop.is_set()):
            now = loop.time()
            deadline = min(end, self._due[0]) if self._due else end
            if deadline <= now:
                break
            self._resched.clear()
            waiters = [
                asyncio.ensure_future(self._event.wait()),
                asyncio.ensure_future(stop.wait()),
                asyncio.ensure_future(self._resched.wait()),
            ]
            try:
                await asyncio.wait(
                    waiters, timeout=deadline - now, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                for w in waiters:
                    w.cancel()
        now = loop.time()
        while self._due and self._due[0] <= now:
            heapq.heappop(self._due)

    async def _listen(self) -> None:
        import psycopg

        delay = 1.0
        while True:
            try:
                conn = await psycopg.AsyncConnection.connect(
                    **self._conninfo,
                    autocommit=True,
                    # 말없이 끊긴 연결을 알아채도록 (알림을 기다리는 동안 쿼리가 없다)
                    keepalives=1,
                    keepalives_idle=60,
                    keepalives_interval=10,
                    keepalives_count=3,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - DB 재시작 등: fallback 확인이 일을 이어 간다
                log.warning("job listener: connect failed (%s)", type(exc).__name__)
            else:
                try:
                    async with conn:
                        await conn.execute(f"LISTEN {JOB_CHANNEL}")
                        self._listening.set()
                        delay = 1.0
                        self._event.set()  # 연결 전·끊긴 동안 놓친 알림 대신 한 번 확인
                        async for n in conn.notifies():
                            self.notify(n.payload)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log.warning("job listener: connection lost (%s)", type(exc).__name__)
                finally:
                    self._listening.clear()
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._reconnect_max)

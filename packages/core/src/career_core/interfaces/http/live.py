"""시스템 화면 실시간 갱신: Server-Sent Events ``GET /core/live`` (실행·승인 상태 변경 알림).

흐름::

    runs·approvals 변경 ─▶ trigger(c0019) ─▶ NOTIFY career_state, 'runs' | 'approvals'
                                              │ (프로세스마다 LISTEN 연결 하나: StateHints)
    SSE 연결마다 ─▶ 힌트를 받으면 **그 공간 문맥(RLS)** 에서 요약값(행 수·updated_at)을 다시 읽고
                  바뀐 경우에만 ``event: runs`` / ``event: approvals``를 보낸다 ─▶ 웹이 목록을 다시 읽는다

- 알림 내용은 표 이름뿐이다. LISTEN은 어떤 역할이든 할 수 있으므로 공간·대상 ID를 넣지 않는다. 다른 공간의
  변경도 힌트로는 오지만, 다시 읽은 요약값이 같으면 아무것도 보내지 않는다 (다른 공간의 활동 시각도 새지
  않는다). 이벤트 데이터는 그 공간 요약값(불투명 문자열)뿐이고 실제 내용은 기존 API로 다시 읽는다.
- 인증: 다른 경로와 같은 ``Authorization: Bearer`` 토큰과 같은 ``IdentityResolver``·권한(``runs.read``,
  승인은 ``approvals.read``가 있을 때만). 토큰을 URL에 넣지 않는다 (브라우저 ``EventSource``는 헤더를
  못 붙이므로 웹은 ``fetch`` 스트림으로 읽는다). 연결 중에도 주기적으로 토큰을 다시 확인해 폐기되면
  ``event: unauthorized``를 보내고 닫는다.
- 알림은 힌트일 뿐이다: LISTEN이 안 되면(PgBouncer transaction 모드 등) 서버가 짧은 간격(5초)으로, 되면 긴
  간격(30초)으로 요약값을 다시 본다. 연결은 ``max_seconds``(기본 300초) 뒤 닫히고 클라이언트가 다시 붙는다
  (프록시 유휴 시간·토큰 재확인). 웹은 이 스트림이 끊기면 예전처럼 몇 초 간격 다시 읽기로 돌아간다.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager, suppress
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import URL, text

from career_core.jobs.wake import libpq_kwargs
from career_core.ports import ActorContext

log = logging.getLogger(__name__)

STATE_CHANNEL = "career_state"  # c0019 notify_state_change
CHANNELS = ("runs", "approvals")
# 요약값을 다시 볼 간격: 알림을 받고 있으면 놓친 알림 대비로 길게, 아니면 짧게 (polling fallback)
CHECK_LISTENING_SECONDS = 30.0
CHECK_POLLING_SECONDS = 5.0
PING_SECONDS = 15.0
REAUTH_SECONDS = 15.0
# 힌트가 몰려 올 때(한 실행의 여러 단계) 한 번에 묶는 시간
DEBOUNCE_SECONDS = 0.2
DEFAULT_MAX_SECONDS = 300
RETRY_MS = 5000

_FINGERPRINT_SQL = {
    "runs": "SELECT count(*), coalesce(sum(extract(epoch FROM updated_at)), 0), max(updated_at) "
    "FROM core.runs",
    "approvals": "SELECT count(*), coalesce(sum(extract(epoch FROM updated_at)), 0), "
    "max(updated_at) FROM core.approvals",
}


class Subscription:
    """SSE 연결 하나가 받은 힌트(표 이름 집합)."""

    def __init__(self) -> None:
        self._pending: set[str] = set()
        self._event = asyncio.Event()

    def push(self, payload: str) -> None:
        self._pending.add(payload if payload in CHANNELS else "*")
        self._event.set()

    def drain(self) -> set[str]:
        got, self._pending = self._pending, set()
        self._event.clear()
        return got

    async def wait(self, timeout: float) -> set[str]:
        if not self._event.is_set():
            with suppress(TimeoutError):
                await asyncio.wait_for(self._event.wait(), max(0.0, timeout))
        return self.drain()


class StateHints:
    """프로세스 하나의 ``LISTEN career_state`` 연결 하나를 SSE 연결들에 나눠 준다.

    첫 구독 때 연결하고 마지막 구독이 끝나면 닫는다. 연결이 끊기면 다시 붙고, 붙을 때마다 모든 구독에
    "다시 확인" 힌트를 보낸다 (끊긴 동안 놓친 알림 대신).
    """

    def __init__(self, url: str | URL, *, reconnect_max_seconds: float = 30.0) -> None:
        self._conninfo = libpq_kwargs(url)
        self._reconnect_max = reconnect_max_seconds
        self._subs: set[Subscription] = set()
        self._task: asyncio.Task[None] | None = None
        self._listening = asyncio.Event()

    @property
    def listening(self) -> bool:
        return self._listening.is_set()

    @property
    def subscribers(self) -> int:
        return len(self._subs)

    async def wait_listening(self, timeout: float) -> bool:
        with suppress(TimeoutError):
            await asyncio.wait_for(self._listening.wait(), timeout)
        return self.listening

    def publish(self, payload: str) -> None:
        for sub in list(self._subs):
            sub.push(payload)

    @contextmanager
    def subscribe(self) -> Iterator[Subscription]:
        sub = Subscription()
        self._subs.add(sub)
        if self._task is None or self._task.done():
            self._listening = asyncio.Event()
            self._task = asyncio.create_task(self._listen(), name="career-state-listener")
        try:
            yield sub
        finally:
            self._subs.discard(sub)
            if not self._subs and self._task is not None:
                self._task.cancel()
                self._task = None
                self._listening.clear()

    async def _listen(self) -> None:
        import psycopg

        delay = 1.0
        while True:
            try:
                conn = await psycopg.AsyncConnection.connect(
                    **self._conninfo,
                    autocommit=True,
                    keepalives=1,
                    keepalives_idle=60,
                    keepalives_interval=10,
                    keepalives_count=3,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - LISTEN이 안 되면 짧은 간격 확인으로 버틴다
                log.warning("state listener: connect failed (%s)", type(exc).__name__)
            else:
                try:
                    async with conn:
                        await conn.execute(f"LISTEN {STATE_CHANNEL}")
                        self._listening.set()
                        delay = 1.0
                        self.publish("*")
                        async for n in conn.notifies():
                            self.publish(n.payload)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log.warning("state listener: connection lost (%s)", type(exc).__name__)
                finally:
                    self._listening.clear()
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._reconnect_max)


async def fingerprints(db, actor: ActorContext, channels: tuple[str, ...]) -> dict[str, str]:
    """그 공간(RLS)의 표별 요약값: 행 수·updated_at 합·최댓값. 행이 생기거나 바뀌면 달라진다."""
    out: dict[str, str] = {}
    async with db.workspace_session(actor.workspace_id) as s:
        for ch in channels:
            n, total, latest = (await s.execute(text(_FINGERPRINT_SQL[ch]))).one()
            out[ch] = f"{n}:{total}:{latest.isoformat() if latest else '-'}"
    return out


def _event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _bearer(request: Request) -> str:
    scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def add_live_routes(router: APIRouter, deps, authorize) -> StateHints | Any:
    """``deps.live``(힌트 공급자)가 없으면 DB 주소로 ``StateHints``를 만든다. 쓰는 공급자를 돌려준다."""
    hints = getattr(deps, "live", None) or StateHints(deps.db.engine.url)
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )

    async def current(token: str) -> ActorContext | None:
        if not token:
            return None
        actor = await deps.identity.resolve(token)
        if actor is None or not deps.permissions.allows(actor, "runs.read"):
            return None
        return actor

    @router.get("/live", include_in_schema=False)
    async def live(
        request: Request,
        max_seconds: Annotated[int, Query(ge=1, le=3600)] = DEFAULT_MAX_SECONDS,
    ) -> StreamingResponse:
        token = _bearer(request)
        actor = await deps.identity.resolve(token) if token else None
        if actor is None:
            raise unauthorized
        if not deps.permissions.allows(actor, "runs.read"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
        channels = tuple(
            ch
            for ch in CHANNELS
            if ch == "runs" or deps.permissions.allows(actor, "approvals.read")
        )
        first = await fingerprints(deps.db, actor, channels)

        async def stream() -> AsyncIterator[str]:
            loop = asyncio.get_running_loop()
            state = dict(first)
            with hints.subscribe() as sub:
                yield f"retry: {RETRY_MS}\n\n"
                yield _event("ready", {"channels": list(channels), "listening": hints.listening})
                start = loop.time()
                deadline = start + max_seconds
                next_ping = start + PING_SECONDS
                next_auth = start + REAUTH_SECONDS
                next_check = start + CHECK_POLLING_SECONDS
                while True:
                    now = loop.time()
                    if now >= deadline:
                        break
                    got = await sub.wait(min(deadline, next_ping, next_check) - now)
                    if got:
                        await asyncio.sleep(DEBOUNCE_SECONDS)
                        got |= sub.drain()
                    now = loop.time()
                    if got or now >= next_check:
                        if now >= next_auth:
                            if await current(token) is None:
                                yield _event("unauthorized", {})
                                return
                            next_auth = now + REAUTH_SECONDS
                        fresh = await fingerprints(deps.db, actor, channels)
                        for ch in channels:
                            if fresh[ch] != state[ch]:
                                yield _event(ch, {"v": fresh[ch]})
                        state = fresh
                        interval = (
                            CHECK_LISTENING_SECONDS if hints.listening else CHECK_POLLING_SECONDS
                        )
                        next_check = now + interval
                    if now >= next_ping:
                        yield ": ping\n\n"
                        next_ping = now + PING_SECONDS
                yield _event("bye", {"reconnect": True})

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return hints


__all__ = [
    "CHANNELS",
    "STATE_CHANNEL",
    "StateHints",
    "Subscription",
    "add_live_routes",
    "fingerprints",
]

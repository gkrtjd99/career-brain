"""주기 유지보수: 모든 공간에 정리 작업(기본: 미확정 공고 초안 정리)을 돈다.

- 공간 목록은 ``core.maintenance_workspace_ids()``(SECURITY DEFINER, c0015)로 ID만 받는다.
  작업 자체는 공간마다 그 공간 문맥(``workspace_session``)에서 한다.
- 여러 worker가 같이 돌아도 한 번에 하나만 한다: 트랜잭션 advisory lock
  (``pg_try_advisory_xact_lock``)을 못 얻으면 이번 차례는 건너뛴다. 프로세스가 죽으면 연결과
  함께 잠금도 풀린다.
- 작업은 멱등이어야 한다 (같은 기준 시각이면 두 번 해도 결과가 같다). 공간 하나·작업 하나가
  실패해도 나머지는 계속한다.
- ``pg_dump``는 여기서 하지 않는다 (DB 소유자 계정·서버 밖 보관이 필요). cron/systemd timer:
  ``docs/operations/backup.md``.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text

from career_core.db.session import Database

log = logging.getLogger(__name__)

# 유지보수 advisory lock 키 (임의 고정값, 다른 잠금과 겹치지 않게)
MAINTENANCE_LOCK_KEY = 0x63617265_00000001


@dataclass(frozen=True, slots=True)
class MaintenanceTask:
    name: str
    # (db, workspace_id) → 처리한 행 수
    run: Callable[[Database, UUID], Awaitable[int]]


@dataclass(slots=True)
class MaintenanceReport:
    ran: bool  # False면 다른 worker가 하는 중이라 건너뜀
    workspaces: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    errors: int = 0


def default_tasks() -> tuple[MaintenanceTask, ...]:
    from career_core.usecases.audit import prune_security_records
    from career_core.usecases.postings import purge_unconfirmed

    return (
        MaintenanceTask("purge_unconfirmed_postings", purge_unconfirmed),
        # 감사 기록 보존 기간(CAREER_AUDIT_RETENTION_DAYS) + 만료된 OAuth 토큰 기록 (c0019)
        MaintenanceTask("prune_audit_and_tokens", prune_security_records),
    )


async def workspace_ids(db: Database) -> list[UUID]:
    async with db.system_session() as s:
        return list(await s.scalars(text("SELECT * FROM core.maintenance_workspace_ids()")))


async def run_maintenance(
    db: Database, tasks: Sequence[MaintenanceTask] | None = None
) -> MaintenanceReport:
    tasks = default_tasks() if tasks is None else tasks
    async with db.engine.connect() as lock_conn, lock_conn.begin():
        got = await lock_conn.scalar(
            text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": MAINTENANCE_LOCK_KEY}
        )
        if not got:
            return MaintenanceReport(ran=False)
        report = MaintenanceReport(ran=True, counts={t.name: 0 for t in tasks})
        ids = await workspace_ids(db)
        report.workspaces = len(ids)
        for ws in ids:
            for task in tasks:
                try:
                    report.counts[task.name] += await task.run(db, ws)
                except Exception as exc:  # noqa: BLE001 - 한 공간의 실패가 나머지를 막지 않는다
                    report.errors += 1
                    log.warning("maintenance %s failed (%s)", task.name, type(exc).__name__)
    return report


async def maintenance_loop(
    db: Database,
    stop: asyncio.Event,
    *,
    interval_seconds: float,
    first_delay_seconds: float = 60.0,
    tasks: Sequence[MaintenanceTask] | None = None,
) -> int:
    """``stop``까지 ``interval_seconds``마다 유지보수를 한다 (0 이하면 바로 끝). 실행 횟수를 돌려준다.

    여러 worker가 같은 간격으로 시작해도 몰리지 않게 간격에 ±10% 흔들림을 준다.
    """
    if interval_seconds <= 0:
        return 0
    runs = 0
    delay = first_delay_seconds
    while not await _stopped(stop, delay):
        try:
            report = await run_maintenance(db, tasks)
        except Exception as exc:  # noqa: BLE001 - DB 끊김 등: 다음 차례에 다시
            log.warning("maintenance run failed (%s)", type(exc).__name__)
        else:
            if report.ran:
                runs += 1
                log.info(
                    "maintenance: %d workspace(s) %s errors=%d",
                    report.workspaces,
                    " ".join(f"{k}={v}" for k, v in report.counts.items()),
                    report.errors,
                )
        delay = interval_seconds * random.uniform(0.9, 1.1)
    return runs


async def _stopped(stop: asyncio.Event, seconds: float) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), max(0.0, seconds))
    except TimeoutError:
        return False
    return True

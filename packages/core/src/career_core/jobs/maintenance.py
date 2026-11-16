"""주기 유지보수: 모든 공간에 정리 작업(기본: 미확정 공고 초안 정리)을 돈다.

- 공간 목록은 ``core.maintenance_workspace_ids()``(SECURITY DEFINER, c0015)로 ID만 받는다.
  작업 자체는 공간마다 그 공간 문맥(``workspace_session``)에서 한다.
- 여러 worker가 같이 돌아도 한 번에 하나만 한다: 트랜잭션 advisory lock
  (``pg_try_advisory_xact_lock``)을 못 얻으면 이번 차례는 건너뛴다. 프로세스가 죽으면 연결과
  함께 잠금도 풀린다.
- 작업은 멱등이어야 한다 (같은 기준 시각이면 두 번 해도 결과가 같다). 공간 하나·작업 하나가
  실패해도 나머지는 계속한다.
- 마지막 실행 기록(c0020): 설치 전체의 시작·끝 시각과 간격은 ``core.maintenance_schedule``
  (SECURITY DEFINER 함수로만), 공간마다의 처리 수는 ``core.maintenance_results``에 둔다.
  주기 실행(``maintenance_loop``)은 ``maintenance_begin``이 간격 안이라고 하면 건너뛰고 그 다음
  예정 시각까지 쉰다. 그래서 worker가 N개여도 간격마다 한 번 돈다 (잠금은 동시 실행만 막는다).
- ``pg_dump``는 여기서 하지 않는다 (DB 소유자 계정·서버 밖 보관이 필요). cron/systemd timer:
  ``docs/operations/backup.md``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text

from career_core.db.base import utcnow
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
    ran: bool  # False면 건너뜀 (``skipped``: 다른 worker가 하는 중 / 간격 안)
    workspaces: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    errors: int = 0
    skipped: str | None = None  # "locked" | "not_due"
    next_at: datetime | None = None  # 간격을 쓸 때 다음 예정 시각 (DB 시계)


def default_tasks() -> tuple[MaintenanceTask, ...]:
    from career_core.usecases.postings import purge_unconfirmed

    return (MaintenanceTask("purge_unconfirmed_postings", purge_unconfirmed),)


async def workspace_ids(db: Database) -> list[UUID]:
    async with db.system_session() as s:
        return list(await s.scalars(text("SELECT * FROM core.maintenance_workspace_ids()")))


async def run_maintenance(
    db: Database,
    tasks: Sequence[MaintenanceTask] | None = None,
    *,
    interval_seconds: float | None = None,
    force: bool = True,
) -> MaintenanceReport:
    """모든 공간에 유지보수를 한 번 한다.

    ``force=False``(주기 실행)면 마지막 시작에서 ``interval_seconds``가 지나지 않았을 때 건너뛴다.
    ``force=True``(명령·버튼)는 간격과 상관없이 하고, 그 시각을 마지막 실행으로 남긴다.
    """
    tasks = default_tasks() if tasks is None else tasks
    async with db.engine.connect() as lock_conn, lock_conn.begin():
        got = await lock_conn.scalar(
            text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": MAINTENANCE_LOCK_KEY}
        )
        if not got:
            return MaintenanceReport(ran=False, skipped="locked")
        begin = await _begin(db, interval_seconds, force)
        if not begin.started:
            return MaintenanceReport(ran=False, skipped="not_due", next_at=begin.next_at)
        report = MaintenanceReport(
            ran=True, counts={t.name: 0 for t in tasks}, next_at=begin.next_at
        )
        ids = await workspace_ids(db)
        report.workspaces = len(ids)
        for ws in ids:
            counts: dict[str, int] = {}
            errors = 0
            for task in tasks:
                try:
                    counts[task.name] = await task.run(db, ws)
                except Exception as exc:  # noqa: BLE001 - 한 공간의 실패가 나머지를 막지 않는다
                    errors += 1
                    log.warning("maintenance %s failed (%s)", task.name, type(exc).__name__)
                else:
                    report.counts[task.name] += counts[task.name]
            report.errors += errors
            if not errors and not any(counts.values()):
                continue  # 한 일이 없는 공간은 쓰지 않는다 (공간 수만큼 쓰지 않게)
            try:
                await _record(db, ws, counts, errors)
            except Exception as exc:  # noqa: BLE001 - 기록 실패는 정리 결과를 바꾸지 않는다
                log.warning("maintenance record failed (%s)", type(exc).__name__)
        async with db.system_session() as s:
            await s.execute(text("SELECT core.maintenance_finish(:t)"), {"t": begin.started_at})
    return report


async def _begin(db: Database, interval_seconds: float | None, force: bool):
    async with db.system_session() as s:
        return (
            await s.execute(
                text("SELECT * FROM core.maintenance_begin(:iv, :force)"),
                {"iv": float(interval_seconds or 0), "force": force},
            )
        ).one()


async def _record(db: Database, ws: UUID, counts: dict[str, int], errors: int) -> None:
    """이 공간에서 무언가 한 마지막 실행 1행 (이 공간 문맥 안에서, 다른 공간 결과는 섞지 않는다).

    시각은 DB 시계(``now()``)라 같은 실행의 ``maintenance_schedule.last_started_at``보다 늦다.
    """
    async with db.workspace_session(ws) as s:
        await s.execute(
            text(
                "INSERT INTO core.maintenance_results (id, workspace_id, ran_at, counts, errors) "
                "VALUES (:id, :ws, now(), CAST(:counts AS jsonb), :errors) "
                "ON CONFLICT (workspace_id) DO UPDATE SET ran_at = EXCLUDED.ran_at, "
                "counts = EXCLUDED.counts, errors = EXCLUDED.errors"
            ),
            {
                "id": uuid4(),
                "ws": ws,
                "counts": json.dumps(counts),
                "errors": errors,
            },
        )


@dataclass(frozen=True, slots=True)
class MaintenanceStatus:
    """유지보수 화면용. 일정은 설치 전체(시각만), 결과는 이 공간 것만."""

    last_started_at: datetime | None
    last_finished_at: datetime | None
    interval_seconds: float | None
    next_at: datetime | None
    running: bool
    counts: dict[str, int]  # 마지막 실행에서 이 공간에 한 일 (없으면 빈 dict)
    errors: int
    last_changed_at: datetime | None  # 이 공간에서 무언가 정리한 마지막 실행 시각


async def maintenance_status(db: Database, workspace_id: UUID) -> MaintenanceStatus:
    async with db.workspace_session(workspace_id) as s:
        sched = (await s.execute(text("SELECT * FROM core.maintenance_status()"))).first()
        mine = (
            await s.execute(text("SELECT ran_at, counts, errors FROM core.maintenance_results"))
        ).first()
    started = sched.last_started_at if sched else None
    finished = sched.last_finished_at if sched else None
    # 한 일이 없던 공간은 행을 쓰지 않으므로, 마지막 실행보다 오래된 행은 그 실행의 결과가 아니다
    latest = mine is not None and started is not None and mine.ran_at >= started
    return MaintenanceStatus(
        last_started_at=started,
        last_finished_at=finished,
        interval_seconds=sched.interval_seconds if sched else None,
        next_at=sched.next_at if sched else None,
        running=started is not None and (finished is None or finished < started),
        counts=dict(mine.counts) if latest else {},
        errors=mine.errors if latest else 0,
        last_changed_at=mine.ran_at if mine else None,
    )


async def maintenance_loop(
    db: Database,
    stop: asyncio.Event,
    *,
    interval_seconds: float,
    first_delay_seconds: float = 60.0,
    tasks: Sequence[MaintenanceTask] | None = None,
) -> int:
    """``stop``까지 ``interval_seconds``마다 유지보수를 한다 (0 이하면 바로 끝). 실행 횟수를 돌려준다.

    다른 worker가 간격 안에 이미 했으면 DB가 알려 준 다음 예정 시각까지 쉰다.
    여러 worker가 같은 시각에 몰리지 않게 간격의 0~5%(차례가 아니면 ±10%) 흔들림을 준다.
    """
    if interval_seconds <= 0:
        return 0
    runs = 0
    delay = first_delay_seconds
    while not await _stopped(stop, delay):
        delay = interval_seconds * random.uniform(0.9, 1.1)
        try:
            report = await run_maintenance(
                db, tasks, interval_seconds=interval_seconds, force=False
            )
        except Exception as exc:  # noqa: BLE001 - DB 끊김 등: 다음 차례에 다시
            log.warning("maintenance run failed (%s)", type(exc).__name__)
            continue
        if report.ran:
            runs += 1
            log.info(
                "maintenance: %d workspace(s) %s errors=%d",
                report.workspaces,
                " ".join(f"{k}={v}" for k, v in report.counts.items()),
                report.errors,
            )
        if report.next_at is not None:
            until = (report.next_at - utcnow()).total_seconds()
            delay = min(max(until, 0.0) + interval_seconds * random.uniform(0, 0.05), delay * 2)
    return runs


async def _stopped(stop: asyncio.Event, seconds: float) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), max(0.0, seconds))
    except TimeoutError:
        return False
    return True

"""PostgreSQL 작업 큐 worker.

``core.claim_job``(SECURITY DEFINER)이 공간 문맥 없이 다음 작업 하나를 잠그고,
처리와 결과 기록은 그 작업의 공간 문맥 안에서 한다. lease가 지난 running 작업은
worker가 죽은 것으로 보고 다시 집는다.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import text

from career_core.db.base import utcnow
from career_core.db.models import Job
from career_core.db.session import Database
from career_core.usecases.parsing import StepOutcome

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class JobHandler:
    run: Callable[[UUID, UUID], Awaitable[StepOutcome]]
    give_up: Callable[[UUID, UUID], Awaitable[None]] | None = None


async def run_once(
    db: Database, handlers: Mapping[str, JobHandler], *, lease_seconds: int = 300
) -> bool:
    """작업 하나를 처리했으면 True, 대기 중인 작업이 없으면 False."""
    async with db.system_session() as s:
        row = (
            await s.execute(
                text("SELECT * FROM core.claim_job(:kinds, :lease)"),
                {"kinds": list(handlers), "lease": lease_seconds},
            )
        ).first()
    if row is None:
        return False
    handler = handlers[row.kind]
    try:
        outcome = await handler.run(row.workspace_id, row.target_id)
    except Exception as exc:  # noqa: BLE001 - worker 경계: 어떤 실패든 기록하고 다음 작업으로 간다
        log.warning("job %s (%s) failed: %s", row.job_id, row.kind, type(exc).__name__)
        give_up = row.attempts >= MAX_ATTEMPTS
        async with db.workspace_session(row.workspace_id) as s:
            job = await s.get(Job, row.job_id)
            job.last_error = type(exc).__name__
            job.locked_until = None
            if give_up:
                job.status = "failed"
            else:
                job.status = "queued"
                job.available_at = utcnow() + timedelta(seconds=2**row.attempts)
        if give_up and handler.give_up is not None:
            await handler.give_up(row.workspace_id, row.target_id)
        return True

    async with db.workspace_session(row.workspace_id) as s:
        job = await s.get(Job, row.job_id)
        job.locked_until = None
        if outcome.done:
            job.status = "done"
        else:
            # 외부 제공자 대기는 실패가 아니므로 시도 횟수를 늘리지 않는다.
            job.status = "queued"
            job.attempts = 0
            job.available_at = utcnow() + timedelta(seconds=outcome.retry_after or 0)
    return True


async def run_until_idle(
    db: Database, handlers: Mapping[str, JobHandler], *, limit: int = 1000
) -> int:
    count = 0
    while count < limit and await run_once(db, handlers):
        count += 1
    return count


def parsing_handlers(deps) -> dict[str, JobHandler]:
    from career_core.usecases.parsing import give_up_parse_run, process_parse_run
    from career_core.usecases.sources import PARSE_JOB

    return {
        PARSE_JOB: JobHandler(
            run=lambda ws, target: process_parse_run(deps, ws, target),
            give_up=lambda ws, target: give_up_parse_run(deps, ws, target),
        )
    }


def extraction_handlers(deps) -> dict[str, JobHandler]:
    from career_core.usecases.extraction import (
        EXTRACT_JOB,
        give_up_extraction,
        process_extraction,
    )

    return {
        EXTRACT_JOB: JobHandler(
            run=lambda ws, target: process_extraction(deps, ws, target),
            give_up=lambda ws, target: give_up_extraction(deps, ws, target),
        )
    }


def index_handlers(deps) -> dict[str, JobHandler]:
    from career_core.usecases.indexing import INDEX_JOB, process_index

    return {INDEX_JOB: JobHandler(run=lambda ws, target: process_index(deps, ws, target))}

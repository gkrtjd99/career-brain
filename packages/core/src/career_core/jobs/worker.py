"""PostgreSQL 작업 큐 worker.

``core.claim_job``(SECURITY DEFINER)이 공간 문맥 없이 다음 작업 하나를 잠그고,
처리와 결과 기록은 그 작업의 공간 문맥 안에서 한다. lease가 지난 running 작업은
worker가 죽은 것으로 보고 다시 집는다.

처리하는 동안에는 heartbeat가 lease를 주기적으로 늘린다 (lease보다 오래 걸리는 단계·실행이
도중에 다른 worker에게 넘어가 두 번 돌지 않게). heartbeat가 멈추는 것은 worker가 죽었을 때뿐이다.
lease를 잃은 뒤(다른 worker가 다시 집음) 끝난 처리는 결과를 덮어쓰지 않는다 (``attempts``와
c0023 ``lease_token``으로 구분). 처리하는 동안 lease는 문맥 변수(``career_core.jobs.lease``)에 있어
핸들러가 자기 트랜잭션 안에서 같은 확인을 할 수 있다. 모든 처리기(실행·파싱·추출·색인·공고 가져오기)가
결과를 저장하는 트랜잭션에서 ``guard_lease``를 부른다 (``docs/architecture/runs.md`` 처리기 표).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, text, update

from career_core.db.base import utcnow
from career_core.db.models import Job
from career_core.db.session import Database
from career_core.jobs.lease import Lease, LeaseLost, reset_lease, set_lease
from career_core.usecases.parsing import StepOutcome

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class JobHandler:
    run: Callable[[UUID, UUID], Awaitable[StepOutcome]]
    give_up: Callable[[UUID, UUID], Awaitable[None]] | None = None


async def run_once(
    db: Database,
    handlers: Mapping[str, JobHandler],
    *,
    lease_seconds: int = 300,
    heartbeat_seconds: float | None = None,
) -> bool:
    """작업 하나를 처리했으면 True, 대기 중인 작업이 없으면 False.

    ``heartbeat_seconds``: lease 연장 간격 (기본 lease의 1/3, 0이면 끔 — 시험용).
    """
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
    every = lease_seconds / 3 if heartbeat_seconds is None else heartbeat_seconds
    lease = set_lease(
        Lease(row.job_id, row.target_id, row.attempts, getattr(row, "lease_token", None))
    )
    try:
        outcome = await _with_heartbeat(
            db, row, lease_seconds, every, handler.run(row.workspace_id, row.target_id)
        )
    except LeaseLost:
        # 핸들러가 저장하려다 lease를 잃은 것을 알았다: 그 트랜잭션은 되돌려졌다. 작업 행도 건드리지 않는다
        # (새 주인이 있으면 그쪽이, lease가 지났을 뿐이면 다음 worker가 저장된 위치부터 다시 한다).
        log.warning("job %s (%s): lease lost, step result discarded", row.job_id, row.kind)
        return True
    except Exception as exc:  # noqa: BLE001 - worker 경계: 어떤 실패든 기록하고 다음 작업으로 간다
        log.warning("job %s (%s) failed: %s", row.job_id, row.kind, type(exc).__name__)
        give_up = row.attempts >= MAX_ATTEMPTS
        async with db.workspace_session(row.workspace_id) as s:
            job = await _owned(s, row)
            if job is None:
                return True
            job.last_error = type(exc).__name__
            job.locked_until = None
            if give_up:
                job.status = "failed"
            else:
                job.status = "queued"
                job.available_at = utcnow() + timedelta(seconds=2**row.attempts)
        if give_up and handler.give_up is not None:
            # 위에서 이 worker가 작업을 ``failed``로 닫았다 (lease 확인을 이미 통과). 핸들러의 저장 함수가
            # 부르는 ``guard_lease``는 이제 ``running``이 아닌 행을 보고 거부하므로 lease 문맥을 비운다.
            cleared = set_lease(None)
            try:
                await handler.give_up(row.workspace_id, row.target_id)
            finally:
                reset_lease(cleared)
        return True
    finally:
        reset_lease(lease)

    async with db.workspace_session(row.workspace_id) as s:
        job = await _owned(s, row, finished_by_handler=outcome.done)
        if job is None:
            return True
        job.locked_until = None
        if outcome.done:
            job.status = "done"
        else:
            # 외부 제공자 대기는 실패가 아니므로 시도 횟수를 늘리지 않는다.
            job.status = "queued"
            job.attempts = 0
            job.available_at = utcnow() + timedelta(seconds=outcome.retry_after or 0)
    return True


async def _owned(s, row, *, finished_by_handler: bool = False) -> Job | None:
    """아직 이 worker가 집은 그 작업이면 잠근 행, lease를 잃었으면 None.

    ``finished_by_handler``: 핸들러가 끝났다고 답했고 행이 이미 ``done``이면(색인 작업은
    "남은 대상 없음" 확인과 같은 트랜잭션에서 스스로 끝낸다, ``index_queue.finish_running``)
    lease를 잃은 것이 아니므로 경고 없이 None.
    """
    job = await s.get(Job, row.job_id, with_for_update=True)
    token = getattr(row, "lease_token", None)
    same = job is not None and job.attempts == row.attempts
    if same and token is not None:
        # attempts만으로는 부족하다: 대기(retry_after)가 attempts를 0으로 되돌린다 (c0023)
        same = job.lease_token == token
    if not same or job.status != "running":
        if not (finished_by_handler and same and job.status == "done"):
            log.warning("job %s (%s): lease lost, result not recorded", row.job_id, row.kind)
        return None
    return job


async def _with_heartbeat(db: Database, row, lease_seconds: int, every: float, work):
    if every <= 0:
        return await work
    beat = asyncio.create_task(_heartbeat(db, row, lease_seconds, every))
    try:
        return await work
    finally:
        beat.cancel()
        with suppress(asyncio.CancelledError):
            await beat


async def _heartbeat(db: Database, row, lease_seconds: int, every: float) -> None:
    """``every``초마다 lease를 ``lease_seconds``초 뒤로 민다. 잃었으면(다른 worker가 집음) 멈춘다."""
    while True:
        await asyncio.sleep(every)
        try:
            async with db.workspace_session(row.workspace_id) as s:
                res = await s.execute(
                    update(Job)
                    .where(
                        Job.id == row.job_id,
                        Job.status == "running",
                        Job.attempts == row.attempts,
                        *(
                            [Job.lease_token == row.lease_token]
                            if getattr(row, "lease_token", None) is not None
                            else []
                        ),
                    )
                    .values(locked_until=func.now() + timedelta(seconds=lease_seconds))
                )
        except Exception as exc:  # noqa: BLE001 - 잠깐의 DB 오류: 다음 박동에서 다시 (lease에 여유가 있다)
            log.warning("job %s heartbeat failed (%s)", row.job_id, type(exc).__name__)
            continue
        if res.rowcount == 0:
            log.warning("job %s (%s): lease lost to another worker", row.job_id, row.kind)
            return


async def next_due_seconds(db: Database, kinds) -> float | None:
    """이 worker가 집을 작업 종류 중 가장 이른 예정 시각까지 남은 초 (없으면 None, 지났으면 0).

    ``core.next_job_due``(c0020, SECURITY DEFINER)는 시각 하나만 돌려준다: 대기 작업의
    ``available_at``, lease가 끝날 running 작업의 ``locked_until``. 알림이 오지 않는 경우
    (죽은 worker의 lease 만료, worker 시작 전에 예약된 미래 작업)를 fallback 간격보다 일찍 집는다.
    """
    async with db.system_session() as s:
        left = await s.scalar(
            text("SELECT extract(epoch FROM core.next_job_due(:kinds) - clock_timestamp())"),
            {"kinds": list(kinds)},
        )
    return None if left is None else max(0.0, float(left))


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


def posting_handlers(deps) -> dict[str, JobHandler]:
    """공고 가져오기와, 같은 가져오기 경로를 쓰는 회사 페이지 가져오기 (O13). ``deps``는 ``PostingDeps``."""
    from career_core.usecases.companies import (
        COMPANY_FETCH_JOB,
        give_up_company_fetch,
        process_company_fetch,
    )
    from career_core.usecases.postings import FETCH_JOB, give_up_fetch, process_fetch

    return {
        FETCH_JOB: JobHandler(
            run=lambda ws, target: process_fetch(deps, ws, target),
            give_up=lambda ws, target: give_up_fetch(deps, ws, target),
        ),
        COMPANY_FETCH_JOB: JobHandler(
            run=lambda ws, target: process_company_fetch(deps, ws, target),
            give_up=lambda ws, target: give_up_company_fetch(deps, ws, target),
        ),
    }


def run_handlers(deps) -> dict[str, JobHandler]:
    """O08 실행(run). ``deps``는 ``career_core.usecases.runs.RunDeps``."""
    from career_core.domain.runs import RUN_JOB
    from career_core.usecases.runs import give_up_run, process_run

    return {
        RUN_JOB: JobHandler(
            run=lambda ws, target: process_run(deps, ws, target),
            give_up=lambda ws, target: give_up_run(deps, ws, target),
        )
    }


def restore_handlers(deps) -> dict[str, JobHandler]:
    """웹 복원 확인·합치기 (c0027). ``deps``는 ``career_core.usecases.restore_jobs.RestoreDeps``."""
    from career_core.usecases.restore_jobs import RESTORE_JOB, process_restore

    return {RESTORE_JOB: JobHandler(run=lambda ws, target: process_restore(deps, ws, target))}

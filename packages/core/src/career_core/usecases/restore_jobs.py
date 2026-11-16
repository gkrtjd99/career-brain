"""웹 복원 작업: 작업 폴더, 상태 정리, worker 처리 (c0024 상태 표, c0027 worker 큐).

API(``interfaces/http/restore.py``)는 파일을 받아 작업 폴더에 두고, 확인·합치기를 시작하면 상태를
``verifying``/``applying``으로 바꾸면서 같은 트랜잭션에서 작업 큐에 ``restore`` 작업을 넣는다.
worker가 그 작업을 집어 ``merge_workspace``를 돌린다. 그래서 작업 폴더는 API와 worker가 모두 읽을 수
있는 곳이어야 한다 (``CAREER_RESTORE_DIR``, 개인 앱 기본 ``data/restore``).

- 생존 신호: worker는 실행 중 ``heartbeat``초(기본 2)마다 진행과 ``heartbeat_at``을 쓴다. 읽는 쪽은 신호가
  ``stale``초(기본 60)보다 오래된 실행 중 작업을 ``failed``/``interrupted``로 닫는다. 단, 큐에서 아직
  기다리는 작업(worker가 바쁨)과, worker가 막 집어 lease가 살아 있는데 아직 시작 표시를 하지 않은 작업은
  끊긴 것으로 보지 않는다. 닫힌 작업은 worker가 나중에 집어도 돌지 않는다 (``runner`` 조건).
- 합치기는 한 트랜잭션이라 끊기면 아무것도 남지 않는다 — 같은 파일로 다시 확인하면 된다. 자동 재시도는
  하지 않는다 (큐 작업은 실패를 기록하지 않고 늘 끝냄으로 돌려준다).
- age 신원: API가 시작 요청의 신원을 작업 폴더의 0600 파일(``identity-key.txt``)로 두고, worker가 쓴 뒤
  지운다. worker가 집기 전까지 폴더에 남아 있다 (끊김 정리·작업 지우기·폴더 TTL 정리 때도 지운다).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update

from career_core.db.models import Job, Membership
from career_core.db.models import RestoreJob as RestoreJobRow
from career_core.db.session import Database
from career_core.domain.workspace import scopes_for_role
from career_core.ports import ActorContext, BlobStore, PermissionPolicy
from career_core.usecases.export import ExportError, MergeReport, merge_workspace
from career_core.usecases.parsing import StepOutcome

log = logging.getLogger(__name__)

RESTORE_JOB = "restore"
RESTORE_DIR_ENV = "CAREER_RESTORE_DIR"
TTL_SECONDS = 3600
MAX_JOBS = 8  # 공유 폴더의 작업 폴더 수 상한 (디스크 보호)
HEARTBEAT_SECONDS = 2.0
STALE_SECONDS = 60.0
RUNNING = ("verifying", "applying")
_PREFIX = "career-restore-upload-"
_IDENTITY = "identity-key.txt"


def _now() -> datetime:
    return datetime.now(UTC)


def check_timing(heartbeat: float, stale: float) -> None:
    """신호 간격·끊김 판정 확인: 판정이 간격의 3배 이상이어야 신호 하나를 놓쳐도 끊김으로 보지 않는다."""
    if heartbeat <= 0 or stale < 3 * heartbeat:
        raise ValueError(
            f"restore heartbeat ({heartbeat}s) must be > 0 and stale ({stale}s) >= 3 × heartbeat"
        )


# ---------------------------------------------------------------- 작업 폴더


class RestoreFolder:
    """공유 폴더 안의 작업 폴더들 (``career-restore-upload-<작업 ID>``, 0700). 경로는 작업 ID에서 정해진다."""

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        ttl: float = TTL_SECONDS,
        max_jobs: int = MAX_JOBS,
    ) -> None:
        self.root = Path(root or os.environ.get(RESTORE_DIR_ENV) or tempfile.gettempdir())
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.ttl = ttl
        self.max_jobs = max_jobs

    def dir(self, job_id: UUID) -> Path:
        return self.root / f"{_PREFIX}{job_id}"

    def archive(self, job_id: UUID) -> Path:
        return self.dir(job_id) / "archive"

    def deletions_file(self, job_id: UUID) -> Path:
        return self.dir(job_id) / "deletions.jsonl"

    def identity_file(self, job_id: UUID) -> Path:
        return self.dir(job_id) / _IDENTITY

    def touch(self, job_id: UUID) -> None:
        with contextlib.suppress(OSError):
            os.utime(self.dir(job_id))

    def remove(self, job_id: UUID) -> None:
        shutil.rmtree(self.dir(job_id), ignore_errors=True)

    def remove_identity(self, job_id: UUID) -> None:
        self.identity_file(job_id).unlink(missing_ok=True)

    def write_identity(self, job_id: UUID, keys: list[str] | None) -> bool:
        """worker가 읽을 0600 신원 파일 (있던 것은 바꾼다). 키가 없으면 지우고 False."""
        path = self.identity_file(job_id)
        path.unlink(missing_ok=True)
        if not keys:
            return False
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(keys) + "\n")
        return True

    def job_dirs(self) -> list[Path]:
        try:
            return [p for p in self.root.glob(f"{_PREFIX}*") if p.is_dir()]
        except OSError:
            return []

    def sweep(self) -> None:
        """오래 손대지 않은 작업 폴더를 지운다 (비정상 종료, 다른 프로세스가 남긴 것 포함)."""
        cutoff = time.time() - self.ttl
        for p in self.job_dirs():
            try:
                if p.stat().st_mtime < cutoff:
                    shutil.rmtree(p, ignore_errors=True)
            except OSError:
                continue

    def new_dir(self) -> UUID | None:
        """새 작업 폴더. 작업 폴더가 ``max_jobs``개면 None."""
        self.sweep()
        if len(self.job_dirs()) >= self.max_jobs:
            return None
        job_id = uuid4()
        self.dir(job_id).mkdir(mode=0o700)
        return job_id


# ---------------------------------------------------------------- 상태 정리 (읽는 쪽)


async def queue_state(s, job_id: UUID) -> tuple[str | None, datetime | None]:
    """그 복원 작업의 큐 작업 상태 (``queued``·``running``·None)와 lease 끝 시각."""
    row = (
        await s.execute(
            select(Job.status, Job.locked_until)
            .where(
                Job.kind == RESTORE_JOB,
                Job.target_id == job_id,
                Job.status.in_(("queued", "running")),
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    ).first()
    return (row.status, row.locked_until) if row else (None, None)


async def settle(
    s, row: RestoreJobRow, folder: RestoreFolder, *, stale: float
) -> RestoreJobRow | None:
    """읽을 때의 정리: 오래 안 쓴 작업 지우기(None), 생존 신호가 끊긴 실행 중 작업 닫기 (같은 세션, 잠근 행)."""
    now = _now()
    if row.status not in RUNNING:
        if row.updated_at < now - timedelta(seconds=folder.ttl):
            await s.execute(delete(RestoreJobRow).where(RestoreJobRow.id == row.id))
            await asyncio.to_thread(folder.remove, row.id)
            return None
        return row
    if row.heartbeat_at is not None and row.heartbeat_at >= now - timedelta(seconds=stale):
        return row
    queued, lease_until = await queue_state(s, row.id)
    if queued == "queued":
        return row  # worker를 기다리는 중 (worker가 바쁘거나 아직 없음)
    if queued == "running" and row.runner is None and lease_until and lease_until > now:
        return row  # worker가 막 집었다 (아직 시작 표시 전)
    phase = row.phase or row.status
    row.status, row.error = "failed", "interrupted"
    row.error_detail = f"stopped during {phase} (the worker restarted or died)"
    row.runner = row.heartbeat_at = None
    row.updated_at = now
    await asyncio.to_thread(folder.remove_identity, row.id)
    return row


async def enqueue(s, job_id: UUID, workspace_id: UUID) -> None:
    """큐에 ``restore`` 작업을 넣는다. 같은 대상의 남은 큐 작업(끊긴 실행)은 먼저 닫는다."""
    await s.execute(
        update(Job)
        .where(
            Job.kind == RESTORE_JOB,
            Job.target_id == job_id,
            Job.status.in_(("queued", "running")),
        )
        .values(status="failed", last_error="superseded", locked_until=None)
    )
    s.add(Job(workspace_id=workspace_id, kind=RESTORE_JOB, target_id=job_id))


async def cancel_queued(s, job_id: UUID) -> bool:
    """아직 worker가 집지 않은 큐 작업을 닫는다 (집었으면 False)."""
    res = await s.execute(
        update(Job)
        .where(Job.kind == RESTORE_JOB, Job.target_id == job_id, Job.status == "queued")
        .values(status="failed", last_error="cancelled")
    )
    return res.rowcount > 0


# ---------------------------------------------------------------- worker


@dataclass(frozen=True, slots=True)
class RestoreDeps:
    db: Database
    blobs: BlobStore
    root: str | os.PathLike[str] | None = None
    heartbeat: float = HEARTBEAT_SECONDS
    permissions: PermissionPolicy | None = None


@dataclass(slots=True)
class _Live:
    """도는 작업의 진행 (묶음 읽기 스레드에서도 부른다: 값만 바꾼다)."""

    phase: str | None = "read"
    done: int = 0
    total: int = 0

    def __call__(self, phase: str, done: int, total: int) -> None:
        self.phase, self.done, self.total = phase, done, total


def report_json(report: MergeReport) -> dict:
    """DB(jsonb)에 둘 보고: 계산 칸을 더하고 UUID 등은 글자로."""
    out = asdict(report)
    out.update(
        rows_added=report.rows_added,
        rows_skipped=report.rows_skipped,
        deletions_skipped=report.deletions_skipped,
        tables=[{**asdict(t), "skipped": t.skipped} for t in report.tables],
    )
    return json.loads(json.dumps(out, default=str))


def near_choice_map(choices: list | None) -> dict[tuple[str, str], str]:
    return {(c["table"], c["incoming_id"]): c["existing_id"] for c in choices or ()}


async def process_restore(deps: RestoreDeps, workspace_id: UUID, job_id: UUID) -> StepOutcome:
    """큐의 ``restore`` 작업 하나: 시작 표시 → 합치기(확인이면 dry-run) → 결과 기록. 늘 끝냄으로 돌려준다."""
    folder = RestoreFolder(deps.root)
    runner = f"pid{os.getpid()}-{uuid4().hex[:8]}"
    async with deps.db.workspace_session(workspace_id) as s:
        row = await s.scalar(
            select(RestoreJobRow).where(RestoreJobRow.id == job_id).with_for_update()
        )
        if row is None or row.status not in RUNNING or row.runner is not None:
            # 지웠거나, 끊김으로 닫혔거나, 다른 worker가 돌고 있다
            await asyncio.to_thread(folder.remove_identity, job_id)
            return StepOutcome(done=True)
        who = row.started_by or row.actor_id
        role = await s.scalar(
            select(Membership.role).where(
                Membership.workspace_id == workspace_id, Membership.actor_id == who
            )
        )
        row.runner, row.heartbeat_at, row.updated_at = runner, _now(), _now()
        row.phase, row.done, row.total = "read", 0, 0
        dry_run = row.status == "verifying"
        on_duplicate = row.on_duplicate or "skip"
        deletions = row.deletions > 0
        choices = near_choice_map(row.near_choices) if not dry_run else {}
    actor = ActorContext(who, workspace_id, scopes_for_role(role or ""))
    live = _Live()
    ticker = asyncio.create_task(_beat(deps, folder, workspace_id, job_id, runner, live))
    identity = folder.identity_file(job_id)
    final: dict = {}
    try:
        has_identity = await asyncio.to_thread(identity.is_file)
        report = await merge_workspace(
            deps.db,
            deps.blobs,
            actor,
            folder.archive(job_id),
            on_duplicate=on_duplicate,
            dry_run=dry_run,
            via="http",
            identities=[identity] if has_identity else None,
            deletion_logs=[folder.deletions_file(job_id)] if deletions else (),
            permissions=deps.permissions,
            progress=live,
            near_duplicates=choices,
            # 푼 묶음도 작업 폴더에 (시스템 임시가 작은 tmpfs여도 묶음 크기만큼 쓸 수 있게)
            staging_dir=folder.dir(job_id),
        )
    except ExportError as exc:
        final = {"status": "failed", "error": exc.code, "error_detail": exc.detail[:300] or None}
    except asyncio.CancelledError:
        final = {"status": "failed", "error": "cancelled", "error_detail": None}
        raise
    except Exception:
        log.exception("restore job %s failed", job_id)
        final = {"status": "failed", "error": "internal_error", "error_detail": None}
    else:
        final = {
            "status": "verified" if dry_run else "done",
            "report": report_json(report),
            "error": None,
            "error_detail": None,
        }
        if not dry_run:
            # 합쳤으면 올린 파일은 더 쓸 일이 없다 (보고만 남긴다)
            await asyncio.to_thread(folder.remove, job_id)
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker
        await asyncio.to_thread(folder.remove_identity, job_id)
        ok = final.get("status") != "failed"
        final.update(
            phase=None if ok else live.phase,
            done=live.done,
            total=live.total,
            runner=None,
            heartbeat_at=None,
        )
        try:
            async with deps.db.workspace_session(workspace_id) as s:
                await s.execute(
                    update(RestoreJobRow)
                    .where(RestoreJobRow.id == job_id, RestoreJobRow.runner == runner)
                    .values(**final, updated_at=_now())
                )
        except Exception:
            log.warning("restore job %s: could not record the result", job_id, exc_info=True)
    return StepOutcome(done=True)


async def _beat(
    deps: RestoreDeps,
    folder: RestoreFolder,
    workspace_id: UUID,
    job_id: UUID,
    runner: str,
    live: _Live,
) -> None:
    """진행·생존 신호를 ``heartbeat``초마다 DB에 (화면·다른 프로세스가 본다)."""
    while True:
        await asyncio.sleep(deps.heartbeat)
        try:
            async with deps.db.workspace_session(workspace_id) as s:
                await s.execute(
                    update(RestoreJobRow)
                    .where(
                        RestoreJobRow.id == job_id,
                        RestoreJobRow.status.in_(RUNNING),
                        RestoreJobRow.runner == runner,
                    )
                    .values(
                        phase=live.phase,
                        done=live.done,
                        total=live.total,
                        heartbeat_at=_now(),
                        updated_at=_now(),
                    )
                )
            await asyncio.to_thread(folder.touch, job_id)
        except Exception:
            log.warning("restore job %s heartbeat failed", job_id, exc_info=True)


__all__ = [
    "HEARTBEAT_SECONDS",
    "RESTORE_DIR_ENV",
    "RESTORE_JOB",
    "RUNNING",
    "STALE_SECONDS",
    "TTL_SECONDS",
    "RestoreDeps",
    "RestoreFolder",
    "cancel_queued",
    "check_timing",
    "enqueue",
    "near_choice_map",
    "process_restore",
    "queue_state",
    "report_json",
    "settle",
]

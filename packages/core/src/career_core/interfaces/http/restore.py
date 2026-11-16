"""웹에서 복원하기: 묶음을 올려 지금 공간에 합친다 (c0018, 작업 상태 DB c0024). 모두 소유자(workspace-admin)만.

흐름 (작업 하나 = 올린 묶음 하나, 공간마다 하나):

    POST   /maintenance/restore                본문 = 묶음 파일 그대로 (application/octet-stream) → 작업
    GET    /maintenance/restore                이 공간의 지금 작업 (없으면 404). 화면을 다시 열 때 이어 보기
    POST   /maintenance/restore/{id}/deletions 본문 = 삭제 기록 JSONL (선택, 묶음보다 나중에 받은 것)
    POST   /maintenance/restore/{id}/verify    {on_duplicate, identity?} → 확인(dry-run)을 배경으로 시작
    GET    /maintenance/restore/{id}           상태·단계·진행·확인 보고 (화면이 몇 초마다 읽는다)
    POST   /maintenance/restore/{id}/apply     {on_duplicate, identity?, archive_sha256} → 합치기 시작
    DELETE /maintenance/restore/{id}           작업과 임시 파일 지우기 (실행 중이면 409)

- **작업 상태는 DB**(``core.restore_jobs``, c0024)에 있다. 다른 API 프로세스도 같은 작업을 읽고 확인·합치기를
  시작할 수 있고, 재시작 뒤에도 남는다. 배경 작업(확인·합치기)은 시작한 프로세스에서 돌며 몇 초마다 진행과
  생존 신호(``heartbeat_at``)를 쓴다. 신호가 ``STALE_SECONDS``보다 오래된 실행 중 작업은 읽는 쪽이
  ``failed``/``interrupted``로 닫는다 (재시작·죽은 프로세스). 합치기는 한 트랜잭션이라 끊기면 아무것도
  남지 않는다 — 같은 파일로 다시 확인하면 된다.
- 올린 파일은 공유 폴더(``CoreDeps.restore_dir`` → ``CAREER_RESTORE_DIR`` → 시스템 임시 디렉터리)의
  ``career-restore-upload-<작업 ID>`` 폴더(0700)에만 쓴다. 경로는 작업 ID에서 정해지고 DB에 두지 않는다.
  끝나거나(``done``), 지우거나, ``TTL_SECONDS`` 동안 손대지 않으면 폴더째 지운다. 작업을 읽거나 돌릴 때마다
  폴더 시각을 고치고, 업로드·시작 때 그 시각이 TTL보다 오래된 폴더를 지운다 (다른 프로세스가 남긴 것도).
  여러 프로세스가 쓰면 이 폴더를 모두가 보는 곳(같은 볼륨)으로 둔다.
- 본문 크기: 앱 전체 상한(``BodySizeLimit``)은 이 경로만 ``max_restore_bytes``로 넓힌다
  (``RESTORE_UPLOAD_PATH``). 처리 함수도 받으면서 세어 넘으면 413.
- age 신원(비밀 키)은 요청 본문으로 받아 그 요청의 배경 작업 동안만 작업 폴더의 0600 파일로 두고 지운다.
  DB에는 남기지 않는다 (확인·합치기 때 각각 보낸다).
- 합치기는 확인을 마친 같은 묶음(sha256)·같은 정책이어야 시작한다. 상태 전이는 조건부 UPDATE 하나라
  두 프로세스가 같은 작업을 동시에 시작하지 못한다.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

import asyncio
import contextlib
import hashlib
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, SecretStr
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from career_core.db.models import RestoreJob as RestoreJobRow
from career_core.ports import ActorContext
from career_core.usecases.export import ExportError, MergeReport, merge_workspace, read_deletions

log = logging.getLogger(__name__)

RESTORE_UPLOAD_PATH = "/core/maintenance/restore"  # 앱이 붙이는 접두어(예: /api) 뒤
RESTORE_DIR_ENV = "CAREER_RESTORE_DIR"
TTL_SECONDS = 3600
MAX_JOBS = 8  # 공유 폴더의 작업 폴더 수 상한 (디스크 보호)
HEARTBEAT_SECONDS = 2.0
STALE_SECONDS = 60.0
MAX_DELETIONS_BYTES = 16 << 20
_PREFIX = "career-restore-upload-"
_IDENTITY_LINE = re.compile(r"AGE-SECRET-KEY-1[02-9AC-HJ-NP-Z]{58}")
_CHUNK_TO_DISK = 1 << 20
_ACCEPTED_TYPES = ("application/octet-stream", "application/gzip", "application/x-gzip")
_RUNNING = ("verifying", "applying")

Status = Literal["uploaded", "verifying", "verified", "applying", "done", "failed"]


@dataclass(slots=True)
class _Live:
    """이 프로세스에서 도는 작업의 진행 (묶음 읽기 스레드에서도 부른다: 값만 바꾼다)."""

    phase: str | None = "queued"
    done: int = 0
    total: int = 0

    def __call__(self, phase: str, done: int, total: int) -> None:
        self.phase, self.done, self.total = phase, done, total


def _now() -> datetime:
    return datetime.now(UTC)


class RestoreJobs:
    """복원 작업 상태(DB)와 작업 폴더(공유 폴더). 배경 작업은 이 프로세스의 것만 들고 있다."""

    def __init__(
        self,
        db,
        *,
        root: str | os.PathLike[str] | None = None,
        ttl: float = TTL_SECONDS,
        max_jobs: int = MAX_JOBS,
        heartbeat: float = HEARTBEAT_SECONDS,
        stale: float = STALE_SECONDS,
    ) -> None:
        self.db = db
        self.root = Path(root or os.environ.get(RESTORE_DIR_ENV) or tempfile.gettempdir())
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.ttl = ttl
        self.max_jobs = max_jobs
        self.heartbeat = heartbeat
        self.stale = stale
        # 진단용 프로세스 표시 (DB의 runner). 개인 정보가 없게 pid + 임의 값만.
        self.runner = f"pid{os.getpid()}-{uuid4().hex[:8]}"
        self.tasks: dict[UUID, asyncio.Task] = {}
        self.live: dict[UUID, _Live] = {}
        _sweep_stale_dirs(self.root, ttl)

    # ------------------------------------------------------------ 폴더

    def dir(self, job_id: UUID) -> Path:
        return self.root / f"{_PREFIX}{job_id}"

    def archive(self, job_id: UUID) -> Path:
        return self.dir(job_id) / "archive"

    def deletions_file(self, job_id: UUID) -> Path:
        return self.dir(job_id) / "deletions.jsonl"

    def _touch_dir(self, job_id: UUID) -> None:
        with contextlib.suppress(OSError):
            os.utime(self.dir(job_id))

    def _remove_dir(self, job_id: UUID) -> None:
        shutil.rmtree(self.dir(job_id), ignore_errors=True)

    def new_dir(self) -> UUID:
        """새 작업 폴더 (0700). 공유 폴더의 작업 폴더가 ``max_jobs``개면 429."""
        _sweep_stale_dirs(self.root, self.ttl)
        if len(_job_dirs(self.root)) >= self.max_jobs:
            raise HTTPException(429, "too_many_restore_jobs")
        job_id = uuid4()
        self.dir(job_id).mkdir(mode=0o700)
        return job_id

    def running_here(self, job_id: UUID) -> bool:
        task = self.tasks.get(job_id)
        return task is not None and not task.done()

    # ------------------------------------------------------------ DB 상태

    def _stale_cut(self) -> datetime:
        return _now() - timedelta(seconds=self.stale)

    async def _settle(self, s, row: RestoreJobRow) -> RestoreJobRow | None:
        """읽을 때의 정리: 오래 안 쓴 작업 지우기(None), 생존 신호가 끊긴 실행 중 작업 닫기."""
        now = _now()
        if row.status not in _RUNNING and row.updated_at < now - timedelta(seconds=self.ttl):
            await s.execute(delete(RestoreJobRow).where(RestoreJobRow.id == row.id))
            await asyncio.to_thread(self._remove_dir, row.id)
            return None
        if (
            row.status in _RUNNING
            and not self.running_here(row.id)
            and (row.heartbeat_at is None or row.heartbeat_at < self._stale_cut())
        ):
            phase = row.phase or row.status
            row.status, row.error = "failed", "interrupted"
            row.error_detail = f"stopped during {phase} (server restarted or the process died)"
            row.runner = row.heartbeat_at = None
            row.updated_at = now
        return row

    async def current(self, actor: ActorContext) -> RestoreJobRow | None:
        async with self.db.workspace_session(actor.workspace_id) as s:
            row = await s.scalar(select(RestoreJobRow).with_for_update())
            return None if row is None else await self._settle(s, row)

    async def get(self, actor: ActorContext, job_id: UUID, *, touch: bool = True) -> RestoreJobRow:
        async with self.db.workspace_session(actor.workspace_id) as s:
            row = await s.scalar(
                select(RestoreJobRow).where(RestoreJobRow.id == job_id).with_for_update()
            )
            if row is not None:
                row = await self._settle(s, row)
            if row is not None and touch:
                row.updated_at = _now()
        # 세션 밖에서 (정리한 내용은 commit된 뒤에)
        if row is None:
            raise HTTPException(404, "restore_job_not_found")
        if touch:
            await asyncio.to_thread(self._touch_dir, job_id)
        return row

    async def precheck(self, actor: ActorContext) -> None:
        """업로드를 받기 전에: 같은 공간에서 실행 중이면 409."""
        row = await self.current(actor)
        if row is not None and row.status in _RUNNING:
            raise HTTPException(409, "restore_busy")

    async def insert(
        self,
        actor: ActorContext,
        job_id: UUID,
        *,
        size: int,
        sha256: str,
        encrypted: bool,
        filename: str | None,
    ) -> RestoreJobRow:
        """받은 파일로 작업을 만든다. 같은 공간의 옛 작업(실행 중이 아닌 것)은 지우고 바꾼다."""
        try:
            async with self.db.workspace_session(actor.workspace_id) as s:
                old = await s.scalar(select(RestoreJobRow).with_for_update())
                if old is not None:
                    old = await self._settle(s, old)
                if old is not None:
                    if old.status in _RUNNING:
                        raise HTTPException(409, "restore_busy")
                    await s.execute(delete(RestoreJobRow).where(RestoreJobRow.id == old.id))
                    await s.flush()
                now = _now()
                row = RestoreJobRow(
                    id=job_id,
                    workspace_id=actor.workspace_id,
                    actor_id=actor.subject_id,
                    status="uploaded",
                    bytes=size,
                    sha256=sha256,
                    encrypted=encrypted,
                    filename=filename,
                    created_at=now,
                    updated_at=now,
                )
                s.add(row)
        except IntegrityError as exc:  # 다른 프로세스가 같은 공간에 먼저 넣었다
            raise HTTPException(409, "restore_busy") from exc
        if old is not None:
            await asyncio.to_thread(self._remove_dir, old.id)
        return row

    async def update(self, actor: ActorContext, job_id: UUID, **values) -> None:
        async with self.db.workspace_session(actor.workspace_id) as s:
            await s.execute(
                update(RestoreJobRow)
                .where(RestoreJobRow.id == job_id)
                .values(**values, updated_at=_now())
            )

    async def begin(
        self, actor: ActorContext, job_id: UUID, *, expect: str, status: str, clear_report: bool
    ) -> RestoreJobRow:
        """``expect`` 상태일 때만 실행 상태로 바꾼다 (조건부 UPDATE 하나: 두 프로세스가 같이 시작하지 못한다)."""
        values = {
            "status": status,
            "phase": "queued",
            "done": 0,
            "total": 0,
            "error": None,
            "error_detail": None,
            "runner": self.runner,
            "heartbeat_at": _now(),
            "updated_at": _now(),
        }
        if clear_report:
            values["report"] = None
        async with self.db.workspace_session(actor.workspace_id) as s:
            row = await s.scalar(
                update(RestoreJobRow)
                .where(RestoreJobRow.id == job_id, RestoreJobRow.status == expect)
                .values(**values)
                .returning(RestoreJobRow)
            )
        if row is None:
            raise HTTPException(409, "restore_busy")
        return row

    async def discard(self, actor: ActorContext, job_id: UUID) -> None:
        async with self.db.workspace_session(actor.workspace_id) as s:
            await s.execute(
                delete(RestoreJobRow).where(
                    RestoreJobRow.id == job_id, RestoreJobRow.status.not_in(_RUNNING)
                )
            )
        await asyncio.to_thread(self._remove_dir, job_id)

    def close(self) -> None:
        """이 프로세스의 배경 작업을 멈춘다. 상태·파일은 남는다 (다음 시작에 ``interrupted``로 보인다)."""
        for task in self.tasks.values():
            if not task.done():
                task.cancel()


def _job_dirs(root: Path) -> list[Path]:
    try:
        return [p for p in root.glob(f"{_PREFIX}*") if p.is_dir()]
    except OSError:
        return []


def _sweep_stale_dirs(root: Path, ttl: float) -> None:
    """오래 손대지 않은 작업 폴더를 지운다 (비정상 종료, 다른 프로세스가 남긴 것 포함)."""
    cutoff = time.time() - ttl
    for p in _job_dirs(root):
        try:
            if p.stat().st_mtime < cutoff:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            continue


# ---------------------------------------------------------------- 응답 모양


class MergeTableOut(BaseModel):
    name: str
    archive_rows: int
    added: int
    skipped: int
    skipped_by: dict[str, int]
    sha256: str


class SkippedDeletionOut(BaseModel):
    target_table: str
    target_id: str
    reason: str
    detail: str


class MergeReportOut(BaseModel):
    dry_run: bool
    workspace_id: UUID
    workspace_name: str
    source_workspace_id: UUID
    source_workspace_name: str
    on_duplicate: str
    archive_sha256: str
    encrypted: bool
    format_upgraded_from: int | None
    rows_added: int
    rows_skipped: int
    blobs: int
    actors_mapped: int
    tables: list[MergeTableOut]
    not_merged: dict[str, str]
    requeued: dict[str, int]
    deletions_applied: int
    deletions_skipped: int
    skipped_deletions: list[SkippedDeletionOut]
    notes: list[str]
    # c0024: 옛 migration head에서 바꿔 읽은 묶음, 합친 뒤 닫은 열린 승인 수
    schema_converted_from: list[str] | None = None
    approvals_invalidated: int = 0


class RestoreJobOut(BaseModel):
    id: UUID
    status: Status
    phase: str | None
    done: int
    total: int
    bytes: int
    sha256: str
    encrypted: bool
    filename: str | None
    deletions: int
    on_duplicate: str | None
    report: MergeReportOut | None
    error: str | None
    error_detail: str | None
    created_at: datetime
    updated_at: datetime
    # 실행 중이면 마지막 생존 신호 (c0024)
    heartbeat_at: datetime | None = None


class VerifyIn(BaseModel):
    on_duplicate: Literal["skip", "fail"] = "skip"
    # 암호화한 묶음이면 age 신원 파일 내용 (AGE-SECRET-KEY-1…). 저장하지 않는다.
    identity: SecretStr | None = None


class ApplyIn(VerifyIn):
    # 확인 화면에서 본 묶음의 sha256 (다른 파일로 바뀌지 않았음을 확인)
    archive_sha256: str


def report_dict(report: MergeReport) -> dict:
    out = asdict(report)
    out.update(
        rows_added=report.rows_added,
        rows_skipped=report.rows_skipped,
        deletions_skipped=report.deletions_skipped,
        tables=[{**asdict(t), "skipped": t.skipped} for t in report.tables],
    )
    # DB(jsonb)에 둘 수 있게 UUID 등을 글자로
    return MergeReportOut.model_validate(out).model_dump(mode="json")


def _out(job: RestoreJobRow, live: _Live | None = None) -> RestoreJobOut:
    phase, done, total = job.phase, job.done, job.total
    if live is not None and job.status in _RUNNING:
        phase, done, total = live.phase, live.done, live.total  # 이 프로세스의 더 새 진행
    return RestoreJobOut(
        id=job.id,
        status=job.status,
        phase=phase,
        done=done,
        total=total,
        bytes=job.bytes,
        sha256=job.sha256,
        encrypted=job.encrypted,
        filename=job.filename,
        deletions=job.deletions,
        on_duplicate=job.on_duplicate,
        report=MergeReportOut.model_validate(job.report) if job.report else None,
        error=job.error,
        error_detail=job.error_detail,
        created_at=job.created_at,
        updated_at=job.updated_at,
        heartbeat_at=job.heartbeat_at,
    )


# ---------------------------------------------------------------- 경로


def _identity_keys(identity: SecretStr | None) -> list[str] | None:
    """age 신원 내용 확인 (주석·빈 줄 빼고 모두 ``AGE-SECRET-KEY-1…``). 틀리면 작업 전에 400."""
    if identity is None:
        return None
    lines = [x.strip() for x in identity.get_secret_value().splitlines()]
    keys = [x for x in lines if x and not x.startswith("#")]
    if not keys or not all(_IDENTITY_LINE.fullmatch(k) for k in keys):
        raise HTTPException(400, "invalid_identity")
    return keys


def _write_identity(job_dir: Path, keys: list[str] | None) -> Path | None:
    """배경 작업 동안만 쓰는 0600 신원 파일 (작업이 끝나면 지운다)."""
    if not keys:
        return None
    path = job_dir / f"identity-{uuid4().hex}.txt"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(keys) + "\n")
    return path


async def _receive(request: Request, dst: Path, limit: int) -> tuple[int, str]:
    """본문을 받으면서 바로 파일에 쓴다 (메모리에 다 올리지 않는다). 넘치면 413.

    쓸 때마다 폴더 시각도 고친다 (긴 업로드 중에 다른 프로세스의 정리가 폴더를 지우지 않게).
    """
    h = hashlib.sha256()
    size = 0
    buf = bytearray()
    f = await asyncio.to_thread(open, dst, "wb")

    def flush(data: bytes) -> None:
        f.write(data)
        with contextlib.suppress(OSError):
            os.utime(dst.parent)

    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, "too_large")
            h.update(chunk)
            buf += chunk
            if len(buf) >= _CHUNK_TO_DISK:
                await asyncio.to_thread(flush, bytes(buf))
                buf.clear()
        if buf:
            await asyncio.to_thread(flush, bytes(buf))
    finally:
        await asyncio.to_thread(f.close)
    return size, h.hexdigest()


def add_restore_routes(router: APIRouter, deps, authorize) -> RestoreJobs:
    jobs = RestoreJobs(deps.db, root=getattr(deps, "restore_dir", None))
    owner = Annotated[ActorContext, Depends(authorize("workspace.restore"))]
    limit = int(getattr(deps, "max_restore_bytes", 2 << 30))

    async def beat(job_id: UUID, actor: ActorContext, live: _Live) -> None:
        """진행·생존 신호를 몇 초마다 DB에 (다른 프로세스·화면이 본다)."""
        while True:
            await asyncio.sleep(jobs.heartbeat)
            try:
                async with deps.db.workspace_session(actor.workspace_id) as s:
                    await s.execute(
                        update(RestoreJobRow)
                        .where(
                            RestoreJobRow.id == job_id,
                            RestoreJobRow.status.in_(_RUNNING),
                            RestoreJobRow.runner == jobs.runner,
                        )
                        .values(
                            phase=live.phase,
                            done=live.done,
                            total=live.total,
                            heartbeat_at=_now(),
                            updated_at=_now(),
                        )
                    )
                await asyncio.to_thread(jobs._touch_dir, job_id)
            except Exception:  # 신호 하나를 놓쳐도 작업은 계속한다
                log.warning("restore job %s heartbeat failed", job_id, exc_info=True)

    async def run(
        job_id: UUID,
        actor: ActorContext,
        body: VerifyIn,
        keys: list[str] | None,
        deletions: bool,
        *,
        dry_run: bool,
    ) -> None:
        live = jobs.live[job_id]
        ticker = asyncio.create_task(beat(job_id, actor, live))
        identity = None
        final: dict = {}
        try:
            identity = await asyncio.to_thread(_write_identity, jobs.dir(job_id), keys)
            report = await merge_workspace(
                deps.db,
                deps.blobs,
                actor,
                jobs.archive(job_id),
                on_duplicate=body.on_duplicate,
                dry_run=dry_run,
                via="http",
                identities=[identity] if identity else None,
                deletion_logs=[jobs.deletions_file(job_id)] if deletions else (),
                permissions=deps.permissions,
                progress=live,
            )
        except ExportError as exc:
            final = {
                "status": "failed",
                "error": exc.code,
                "error_detail": exc.detail[:300] or None,
            }
        except HTTPException as exc:
            final = {"status": "failed", "error": str(exc.detail), "error_detail": None}
        except asyncio.CancelledError:
            final = {"status": "failed", "error": "cancelled", "error_detail": None}
            raise
        except Exception:
            log.exception("restore job %s failed", job_id)
            final = {"status": "failed", "error": "internal_error", "error_detail": None}
        else:
            final = {
                "status": "verified" if dry_run else "done",
                "report": report_dict(report),
                "on_duplicate": body.on_duplicate,
                "error": None,
                "error_detail": None,
            }
            if not dry_run:
                # 합쳤으면 올린 파일은 더 쓸 일이 없다 (보고만 남긴다)
                await asyncio.to_thread(jobs._remove_dir, job_id)
        finally:
            ticker.cancel()
            if identity is not None:
                identity.unlink(missing_ok=True)
            ok = final.get("status") != "failed"
            final.update(
                phase=None if ok else live.phase,
                done=live.done,
                total=live.total,
                runner=None,
                heartbeat_at=None,
            )
            try:
                await jobs.update(actor, job_id, **final)
            except Exception:  # 끝 상태를 못 쓰면 생존 신호가 끊겨 interrupted로 보인다
                log.warning("restore job %s: could not record the result", job_id, exc_info=True)
            jobs.live.pop(job_id, None)
            jobs.tasks.pop(job_id, None)

    async def start(
        job: RestoreJobRow, actor: ActorContext, body: VerifyIn, *, dry_run: bool
    ) -> RestoreJobRow:
        keys = _identity_keys(body.identity)
        if not await asyncio.to_thread(jobs.archive(job.id).is_file):
            await jobs.update(
                actor,
                job.id,
                status="failed",
                error="upload_missing",
                error_detail="the uploaded file is gone (cleaned up); upload it again",
            )
            raise HTTPException(410, "upload_missing")
        row = await jobs.begin(
            actor,
            job.id,
            expect=job.status,
            status="verifying" if dry_run else "applying",
            clear_report=dry_run,
        )
        jobs.live[job.id] = _Live()
        jobs.tasks[job.id] = asyncio.create_task(
            run(job.id, actor, body, keys, job.deletions > 0, dry_run=dry_run)
        )
        return row

    @router.post("/maintenance/restore", response_model=RestoreJobOut, status_code=201)
    async def upload(request: Request, actor: owner) -> RestoreJobOut:
        """묶음 파일을 본문 그대로 올린다 (``Content-Type: application/octet-stream``).

        ``X-Filename`` 머리말(선택)은 화면 표시용이다. 확인은 ``/verify``로 따로 시작한다.
        """
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype not in _ACCEPTED_TYPES:
            raise HTTPException(415, "unsupported_media_type")
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            raise HTTPException(413, "too_large")
        await jobs.precheck(actor)
        job_id = await asyncio.to_thread(jobs.new_dir)
        try:
            size, digest = await _receive(request, jobs.archive(job_id), limit)
            if size == 0:
                raise HTTPException(400, "empty_archive")
            from career_core.adapters.age_crypt import is_encrypted

            encrypted = await asyncio.to_thread(is_encrypted, jobs.archive(job_id))
            name = request.headers.get("x-filename")
            row = await jobs.insert(
                actor,
                job_id,
                size=size,
                sha256=digest,
                encrypted=encrypted,
                filename=name[:200] if name else None,
            )
        except BaseException:
            await asyncio.to_thread(jobs._remove_dir, job_id)
            raise
        return _out(row)

    @router.get("/maintenance/restore", response_model=RestoreJobOut)
    async def current(actor: owner) -> RestoreJobOut:
        """이 공간의 지금 작업 (화면을 다시 열거나 다른 프로세스로 요청이 가도 이어 본다)."""
        row = await jobs.current(actor)
        if row is None:
            raise HTTPException(404, "restore_job_not_found")
        return _out(row, jobs.live.get(row.id))

    @router.post("/maintenance/restore/{job_id}/deletions", response_model=RestoreJobOut)
    async def upload_deletions(job_id: UUID, request: Request, actor: owner) -> RestoreJobOut:
        """묶음보다 나중에 받은 삭제 기록(``/maintenance/deletions``의 JSONL). 합친 행에만 적용한다."""
        job = await jobs.get(actor, job_id)
        if job.status in _RUNNING or job.status == "done":
            raise HTTPException(
                409, "restore_busy" if job.status in _RUNNING else "restore_finished"
            )
        if not await asyncio.to_thread(jobs.dir(job_id).is_dir):
            raise HTTPException(410, "upload_missing")
        path = jobs.deletions_file(job_id)
        size, _ = await _receive(request, path, MAX_DELETIONS_BYTES)
        try:
            _, rows = await asyncio.to_thread(read_deletions, path)
        except ExportError as exc:
            path.unlink(missing_ok=True)
            await jobs.update(actor, job_id, deletions=0)
            raise HTTPException(400, exc.code) from exc
        values: dict = {"deletions": len(rows) if size else 0}
        # 삭제 기록이 바뀌면 이전 확인은 더 이상 맞지 않는다
        if job.status == "verified":
            values.update(status="uploaded", report=None)
        await jobs.update(actor, job_id, **values)
        return _out(await jobs.get(actor, job_id, touch=False))

    @router.get("/maintenance/restore/{job_id}", response_model=RestoreJobOut)
    async def status(job_id: UUID, actor: owner) -> RestoreJobOut:
        return _out(await jobs.get(actor, job_id), jobs.live.get(job_id))

    @router.post(
        "/maintenance/restore/{job_id}/verify", response_model=RestoreJobOut, status_code=202
    )
    async def verify(job_id: UUID, body: VerifyIn, actor: owner) -> RestoreJobOut:
        job = await jobs.get(actor, job_id)
        if job.status in _RUNNING:
            raise HTTPException(409, "restore_busy")
        if job.status == "done":
            raise HTTPException(409, "restore_finished")
        if job.encrypted and body.identity is None:
            raise HTTPException(400, "encrypted_archive")
        return _out(await start(job, actor, body, dry_run=True), jobs.live.get(job_id))

    @router.post(
        "/maintenance/restore/{job_id}/apply", response_model=RestoreJobOut, status_code=202
    )
    async def apply(job_id: UUID, body: ApplyIn, actor: owner) -> RestoreJobOut:
        job = await jobs.get(actor, job_id)
        if job.status in _RUNNING:
            raise HTTPException(409, "restore_busy")
        if job.status != "verified" or job.report is None or not job.report["dry_run"]:
            raise HTTPException(409, "not_verified")
        if body.archive_sha256 != job.sha256:
            raise HTTPException(409, "archive_changed")
        if body.on_duplicate != job.on_duplicate:
            raise HTTPException(409, "policy_changed")
        if job.encrypted and body.identity is None:
            raise HTTPException(400, "encrypted_archive")
        return _out(await start(job, actor, body, dry_run=False), jobs.live.get(job_id))

    @router.delete("/maintenance/restore/{job_id}", status_code=204)
    async def discard(job_id: UUID, actor: owner) -> Response:
        job = await jobs.get(actor, job_id, touch=False)
        if job.status in _RUNNING:
            raise HTTPException(409, "restore_busy")
        await jobs.discard(actor, job_id)
        return Response(status_code=204)

    return jobs


__all__ = [
    "HEARTBEAT_SECONDS",
    "RESTORE_DIR_ENV",
    "RESTORE_UPLOAD_PATH",
    "STALE_SECONDS",
    "RestoreJobs",
    "add_restore_routes",
]

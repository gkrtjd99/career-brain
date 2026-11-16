"""웹에서 복원하기: 묶음을 올려 지금 공간에 합친다 (c0018). 모두 소유자(workspace-admin)만.

흐름 (작업 하나 = 올린 묶음 하나, 이 API 프로세스 안의 배경 작업):

    POST   /maintenance/restore                본문 = 묶음 파일 그대로 (application/octet-stream) → 작업
    POST   /maintenance/restore/{id}/deletions 본문 = 삭제 기록 JSONL (선택, 묶음보다 나중에 받은 것)
    POST   /maintenance/restore/{id}/verify    {on_duplicate, identity?} → 확인(dry-run)을 배경으로 시작
    GET    /maintenance/restore/{id}           상태·단계·진행·확인 보고 (화면이 몇 초마다 읽는다)
    POST   /maintenance/restore/{id}/apply     {on_duplicate, identity?, archive_sha256} → 합치기 시작
    DELETE /maintenance/restore/{id}           작업과 임시 파일 지우기 (실행 중이면 409)

- 올린 파일은 이 작업의 임시 폴더(``tempfile.mkdtemp``, 0700)에만 쓴다. 끝나거나(``done``), 지우거나,
  ``TTL_SECONDS`` 동안 손대지 않으면 폴더째 지운다. 프로세스가 끝날 때도 지우고, 다음 시작 때
  남은 옛 폴더(같은 접두어, TTL 지남)를 지운다.
- 본문 크기: 앱 전체 상한(``BodySizeLimit``)은 이 경로만 ``max_restore_bytes``로 넓힌다
  (``RESTORE_UPLOAD_PATH``). 처리 함수도 받으면서 세어 넘으면 413.
- age 신원(비밀 키)은 요청 본문으로 받아 그 요청의 배경 작업 동안만 임시 폴더의 0600 파일로 두고 지운다.
  작업 상태에는 남기지 않는다 (확인·합치기 때 각각 보낸다).
- 합치기는 확인을 마친 같은 묶음(sha256)·같은 정책이어야 시작한다. 한 공간에 작업은 하나만.
- 여러 프로세스(worker 여러 개)로 띄우면 작업 상태가 프로세스마다 따로다. 개인 설치는 한 프로세스다.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

import asyncio
import atexit
import hashlib
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, SecretStr

from career_core.ports import ActorContext
from career_core.usecases.export import ExportError, MergeReport, merge_workspace, read_deletions

log = logging.getLogger(__name__)

RESTORE_UPLOAD_PATH = "/core/maintenance/restore"  # 앱이 붙이는 접두어(예: /api) 뒤
TTL_SECONDS = 3600
MAX_JOBS = 8
MAX_DELETIONS_BYTES = 16 << 20
_PREFIX = "career-restore-upload-"
_IDENTITY_LINE = re.compile(r"AGE-SECRET-KEY-1[02-9AC-HJ-NP-Z]{58}")
_CHUNK_TO_DISK = 1 << 20
_ACCEPTED_TYPES = ("application/octet-stream", "application/gzip", "application/x-gzip")

Status = Literal["uploaded", "verifying", "verified", "applying", "done", "failed"]


@dataclass(slots=True)
class RestoreJob:
    id: UUID
    workspace_id: UUID
    actor_id: UUID
    dir: Path
    bytes: int = 0
    sha256: str = ""
    encrypted: bool = False
    filename: str | None = None
    deletions: int = 0  # 올린 삭제 기록 줄 수
    status: str = "uploaded"
    phase: str | None = None
    done: int = 0
    total: int = 0
    on_duplicate: str | None = None  # 확인(또는 합치기)한 정책
    report: dict | None = None  # 마지막 확인(dry-run) 또는 합치기 결과
    error: str | None = None
    error_detail: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    touched: float = field(default_factory=time.monotonic)
    task: asyncio.Task | None = None

    @property
    def archive(self) -> Path:
        return self.dir / "archive"

    @property
    def deletions_file(self) -> Path:
        return self.dir / "deletions.jsonl"

    @property
    def running(self) -> bool:
        return self.status in ("verifying", "applying")

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC)
        self.touched = time.monotonic()

    def progress(self, phase: str, done: int, total: int) -> None:
        # 묶음 읽기 스레드에서도 부른다: 값만 바꾼다
        self.phase, self.done, self.total = phase, done, total
        self.touch()


class RestoreJobs:
    """이 프로세스의 복원 작업들. 공간마다 하나."""

    def __init__(self, *, ttl: float = TTL_SECONDS, max_jobs: int = MAX_JOBS) -> None:
        self.ttl = ttl
        self.max_jobs = max_jobs
        self.jobs: dict[UUID, RestoreJob] = {}
        _sweep_stale_dirs(ttl)
        atexit.register(self.close)

    def expire(self) -> None:
        now = time.monotonic()
        for job in list(self.jobs.values()):
            if not job.running and now - job.touched > self.ttl:
                self.discard(job)

    def create(self, actor: ActorContext) -> RestoreJob:
        self.expire()
        for job in list(self.jobs.values()):
            if job.workspace_id == actor.workspace_id:
                if job.running:
                    raise HTTPException(409, "restore_busy")
                self.discard(job)  # 같은 공간의 옛 작업은 새 업로드로 바꾼다
        if len(self.jobs) >= self.max_jobs:
            raise HTTPException(429, "too_many_restore_jobs")
        job = RestoreJob(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            actor_id=actor.subject_id,
            dir=Path(tempfile.mkdtemp(prefix=_PREFIX)),
        )
        self.jobs[job.id] = job
        return job

    def get(self, actor: ActorContext, job_id: UUID) -> RestoreJob:
        self.expire()
        job = self.jobs.get(job_id)
        if job is None or job.workspace_id != actor.workspace_id:
            raise HTTPException(404, "restore_job_not_found")
        job.touch()
        return job

    def discard(self, job: RestoreJob) -> None:
        if job.task is not None and not job.task.done():
            job.task.cancel()
        self.jobs.pop(job.id, None)
        shutil.rmtree(job.dir, ignore_errors=True)

    def close(self) -> None:
        for job in list(self.jobs.values()):
            self.discard(job)


def _sweep_stale_dirs(ttl: float) -> None:
    """지난 실행이 남긴(비정상 종료) 업로드 폴더를 지운다."""
    root = Path(tempfile.gettempdir())
    cutoff = time.time() - ttl
    try:
        stale = [p for p in root.glob(f"{_PREFIX}*") if p.is_dir() and p.stat().st_mtime < cutoff]
    except OSError:
        return
    for p in stale:
        shutil.rmtree(p, ignore_errors=True)


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
    return out


def _out(job: RestoreJob) -> RestoreJobOut:
    return RestoreJobOut(
        id=job.id,
        status=job.status,
        phase=job.phase,
        done=job.done,
        total=job.total,
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


def _write_identity(job: RestoreJob, keys: list[str] | None) -> Path | None:
    """배경 작업 동안만 쓰는 0600 신원 파일 (작업이 끝나면 지운다)."""
    if not keys:
        return None
    path = job.dir / f"identity-{uuid4().hex}.txt"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(keys) + "\n")
    return path


async def _receive(request: Request, dst: Path, limit: int) -> tuple[int, str]:
    """본문을 받으면서 바로 파일에 쓴다 (메모리에 다 올리지 않는다). 넘치면 413."""
    h = hashlib.sha256()
    size = 0
    buf = bytearray()
    f = await asyncio.to_thread(open, dst, "wb")
    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, "too_large")
            h.update(chunk)
            buf += chunk
            if len(buf) >= _CHUNK_TO_DISK:
                await asyncio.to_thread(f.write, bytes(buf))
                buf.clear()
        if buf:
            await asyncio.to_thread(f.write, bytes(buf))
    finally:
        await asyncio.to_thread(f.close)
    return size, h.hexdigest()


def add_restore_routes(router: APIRouter, deps, authorize) -> RestoreJobs:
    jobs = RestoreJobs()
    owner = Annotated[ActorContext, Depends(authorize("workspace.restore"))]
    limit = int(getattr(deps, "max_restore_bytes", 2 << 30))

    async def run(
        job: RestoreJob,
        actor: ActorContext,
        body: VerifyIn,
        keys: list[str] | None,
        *,
        dry_run: bool,
    ) -> None:
        identity = None
        try:
            identity = await asyncio.to_thread(_write_identity, job, keys)
            report = await merge_workspace(
                deps.db,
                deps.blobs,
                actor,
                job.archive,
                on_duplicate=body.on_duplicate,
                dry_run=dry_run,
                via="http",
                identities=[identity] if identity else None,
                deletion_logs=[job.deletions_file] if job.deletions else (),
                permissions=deps.permissions,
                progress=job.progress,
            )
        except ExportError as exc:
            job.status, job.error, job.error_detail = "failed", exc.code, exc.detail[:300] or None
        except HTTPException as exc:
            job.status, job.error, job.error_detail = "failed", str(exc.detail), None
        except asyncio.CancelledError:
            job.status, job.error = "failed", "cancelled"
            raise
        except Exception:
            log.exception("restore job %s failed", job.id)
            job.status, job.error, job.error_detail = "failed", "internal_error", None
        else:
            job.report = report_dict(report)
            job.on_duplicate = body.on_duplicate
            job.status = "verified" if dry_run else "done"
            job.error = job.error_detail = None
            if not dry_run:
                # 합쳤으면 올린 파일은 더 쓸 일이 없다 (보고만 남긴다)
                await asyncio.to_thread(shutil.rmtree, job.dir, True)
        finally:
            if identity is not None:
                identity.unlink(missing_ok=True)
            job.phase = None if job.status != "failed" else job.phase
            job.touch()

    def start(job: RestoreJob, actor: ActorContext, body: VerifyIn, *, dry_run: bool) -> None:
        keys = _identity_keys(body.identity)
        job.status = "verifying" if dry_run else "applying"
        job.error = job.error_detail = None
        job.phase, job.done, job.total = "queued", 0, 0
        job.touch()
        job.task = asyncio.create_task(run(job, actor, body, keys, dry_run=dry_run))

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
        job = jobs.create(actor)
        try:
            job.bytes, job.sha256 = await _receive(request, job.archive, limit)
            if job.bytes == 0:
                raise HTTPException(400, "empty_archive")
            from career_core.adapters.age_crypt import is_encrypted

            job.encrypted = await asyncio.to_thread(is_encrypted, job.archive)
        except BaseException:
            jobs.discard(job)
            raise
        name = request.headers.get("x-filename")
        job.filename = name[:200] if name else None
        job.touch()
        return _out(job)

    @router.post("/maintenance/restore/{job_id}/deletions", response_model=RestoreJobOut)
    async def upload_deletions(job_id: UUID, request: Request, actor: owner) -> RestoreJobOut:
        """묶음보다 나중에 받은 삭제 기록(``/maintenance/deletions``의 JSONL). 합친 행에만 적용한다."""
        job = jobs.get(actor, job_id)
        if job.running or job.status == "done":
            raise HTTPException(409, "restore_busy" if job.running else "restore_finished")
        size, _ = await _receive(request, job.deletions_file, MAX_DELETIONS_BYTES)
        try:
            _, rows = await asyncio.to_thread(read_deletions, job.deletions_file)
        except ExportError as exc:
            job.deletions_file.unlink(missing_ok=True)
            job.deletions = 0
            raise HTTPException(400, exc.code) from exc
        job.deletions = len(rows) if size else 0
        # 삭제 기록이 바뀌면 이전 확인은 더 이상 맞지 않는다
        if job.status == "verified":
            job.status, job.report = "uploaded", None
        job.touch()
        return _out(job)

    @router.get("/maintenance/restore/{job_id}", response_model=RestoreJobOut)
    async def status(job_id: UUID, actor: owner) -> RestoreJobOut:
        return _out(jobs.get(actor, job_id))

    @router.post(
        "/maintenance/restore/{job_id}/verify", response_model=RestoreJobOut, status_code=202
    )
    async def verify(job_id: UUID, body: VerifyIn, actor: owner) -> RestoreJobOut:
        job = jobs.get(actor, job_id)
        if job.running:
            raise HTTPException(409, "restore_busy")
        if job.status == "done":
            raise HTTPException(409, "restore_finished")
        if job.encrypted and body.identity is None:
            raise HTTPException(400, "encrypted_archive")
        job.report = None
        start(job, actor, body, dry_run=True)
        return _out(job)

    @router.post(
        "/maintenance/restore/{job_id}/apply", response_model=RestoreJobOut, status_code=202
    )
    async def apply(job_id: UUID, body: ApplyIn, actor: owner) -> RestoreJobOut:
        job = jobs.get(actor, job_id)
        if job.running:
            raise HTTPException(409, "restore_busy")
        if job.status != "verified" or job.report is None or not job.report["dry_run"]:
            raise HTTPException(409, "not_verified")
        if body.archive_sha256 != job.sha256:
            raise HTTPException(409, "archive_changed")
        if body.on_duplicate != job.on_duplicate:
            raise HTTPException(409, "policy_changed")
        if job.encrypted and body.identity is None:
            raise HTTPException(400, "encrypted_archive")
        start(job, actor, body, dry_run=False)
        return _out(job)

    @router.delete("/maintenance/restore/{job_id}", status_code=204)
    async def discard(job_id: UUID, actor: owner) -> Response:
        job = jobs.get(actor, job_id)
        if job.running:
            raise HTTPException(409, "restore_busy")
        jobs.discard(job)
        return Response(status_code=204)

    return jobs


__all__ = [
    "RESTORE_UPLOAD_PATH",
    "RestoreJob",
    "RestoreJobs",
    "add_restore_routes",
]

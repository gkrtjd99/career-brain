"""웹에서 복원하기: 묶음을 올려 지금 공간에 합친다 (c0018, 작업 상태 DB c0024, worker 큐 c0027). 소유자만.

흐름 (작업 하나 = 올린 묶음 하나, 공간마다 하나):

    POST   /maintenance/restore                본문 = 묶음 파일 그대로 (application/octet-stream) → 작업
    GET    /maintenance/restore                이 공간의 지금 작업 (없으면 404). 화면을 다시 열 때 이어 보기
    POST   /maintenance/restore/{id}/deletions 본문 = 삭제 기록 JSONL (선택, 묶음보다 나중에 받은 것)
    POST   /maintenance/restore/{id}/verify    {on_duplicate, identity?} → 확인(dry-run)을 worker 큐에
    GET    /maintenance/restore/{id}           상태·단계·진행·확인 보고 (화면이 몇 초마다 읽는다)
    POST   /maintenance/restore/{id}/apply     {on_duplicate, identity?, archive_sha256, near_duplicates?}
                                               → 합치기를 worker 큐에
    DELETE /maintenance/restore/{id}           작업과 임시 파일 지우기 (실행 중이면 409, 큐에서 기다리면 취소)

- **작업 상태는 DB**(``core.restore_jobs``, c0024)에 있고, 확인·합치기는 **worker**가 돈다 (c0027: 큐 작업
  ``restore``, ``career_core.usecases.restore_jobs.process_restore``). 시작은 조건부 UPDATE와 큐 INSERT를 한
  트랜잭션에서 하므로 두 요청이 같은 작업을 같이 시작하지 못한다. worker는 진행과 생존 신호(``heartbeat_at``)를
  쓰고, 신호가 ``stale``초보다 오래된 실행 중 작업은 읽는 쪽이 ``failed``/``interrupted``로 닫는다
  (큐에서 기다리는 작업은 닫지 않는다). 합치기는 한 트랜잭션이라 끊기면 아무것도 남지 않는다.
- 올린 파일은 공유 폴더(``CoreDeps.restore_dir`` → ``CAREER_RESTORE_DIR`` → 시스템 임시 디렉터리)의
  ``career-restore-upload-<작업 ID>`` 폴더(0700)에만 쓴다. **API와 worker가 모두 읽는 곳**이어야 한다.
  끝나거나(``done``), 지우거나, ``TTL_SECONDS`` 동안 손대지 않으면 폴더째 지운다.
- 본문 크기: 앱 전체 상한(``BodySizeLimit``)은 이 경로만 ``max_restore_bytes``로 넓힌다
  (``RESTORE_UPLOAD_PATH``). 처리 함수도 받으면서 세어 넘으면 413.
- age 신원(비밀 키)은 시작 요청 본문으로 받아 작업 폴더의 0600 파일로 두고, worker가 쓴 뒤 지운다.
  DB에는 남기지 않는다 (확인·합치기 때 각각 보낸다).
- 합치기는 확인을 마친 같은 묶음(sha256)·같은 정책이어야 시작한다. 비슷한 행(``near_duplicates``)은 확인 보고에
  오른 쌍에서만 고를 수 있고, 고른 쌍(들어오는 행 건너뛰기)만 ``near_choices``에 둔다.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

import asyncio
import contextlib
import hashlib
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from career_core.db.models import RestoreJob as RestoreJobRow
from career_core.ports import ActorContext
from career_core.usecases.export import (
    NEAR_KEEP_BOTH,
    NEAR_SKIP_INCOMING,
    ExportError,
    MergeReport,
    read_deletions,
)
from career_core.usecases.restore_jobs import (
    HEARTBEAT_SECONDS,
    MAX_JOBS,
    RESTORE_DIR_ENV,
    RUNNING,
    STALE_SECONDS,
    TTL_SECONDS,
    RestoreFolder,
    cancel_queued,
    enqueue,
    report_json,
    settle,
)

RESTORE_UPLOAD_PATH = "/core/maintenance/restore"  # 앱이 붙이는 접두어(예: /api) 뒤
MAX_DELETIONS_BYTES = 16 << 20
_IDENTITY_LINE = re.compile(r"AGE-SECRET-KEY-1[02-9AC-HJ-NP-Z]{58}")
_CHUNK_TO_DISK = 1 << 20
_ACCEPTED_TYPES = ("application/octet-stream", "application/gzip", "application/x-gzip")
_RUNNING = RUNNING

Status = Literal["uploaded", "verifying", "verified", "applying", "done", "failed"]


def _now() -> datetime:
    return datetime.now(UTC)


class RestoreJobs:
    """복원 작업 상태(DB)와 작업 폴더(공유 폴더). 확인·합치기는 worker 큐로 넘긴다."""

    def __init__(
        self,
        db,
        *,
        root: str | None = None,
        ttl: float = TTL_SECONDS,
        max_jobs: int = MAX_JOBS,
        stale: float = STALE_SECONDS,
    ) -> None:
        self.db = db
        self.folder = RestoreFolder(root, ttl=ttl, max_jobs=max_jobs)
        self.root = self.folder.root
        self.ttl = ttl
        self.stale = stale
        self.folder.sweep()

    # ------------------------------------------------------------ 폴더

    def dir(self, job_id: UUID) -> Path:
        return self.folder.dir(job_id)

    def archive(self, job_id: UUID) -> Path:
        return self.folder.archive(job_id)

    def deletions_file(self, job_id: UUID) -> Path:
        return self.folder.deletions_file(job_id)

    def _remove_dir(self, job_id: UUID) -> None:
        self.folder.remove(job_id)

    def new_dir(self) -> UUID:
        """새 작업 폴더 (0700). 공유 폴더의 작업 폴더가 ``max_jobs``개면 429."""
        job_id = self.folder.new_dir()
        if job_id is None:
            raise HTTPException(429, "too_many_restore_jobs")
        return job_id

    # ------------------------------------------------------------ DB 상태

    async def _settle(self, s, row: RestoreJobRow) -> RestoreJobRow | None:
        return await settle(s, row, self.folder, stale=self.stale)

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
            await asyncio.to_thread(self.folder.touch, job_id)
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
        self,
        actor: ActorContext,
        job_id: UUID,
        *,
        expect: str,
        status: str,
        clear_report: bool,
        on_duplicate: str,
        near_choices: list[dict] | None = None,
    ) -> RestoreJobRow:
        """``expect`` 상태일 때만 실행 상태로 바꾸고 같은 트랜잭션에서 worker 큐에 넣는다.

        조건부 UPDATE 하나라 두 요청이 같은 작업을 같이 시작하지 못한다. ``heartbeat_at``은 큐에 넣은 시각
        (worker가 집으면 worker의 신호로 바뀐다).
        """
        values = {
            "status": status,
            "phase": "queued",
            "done": 0,
            "total": 0,
            "error": None,
            "error_detail": None,
            "runner": None,
            "heartbeat_at": _now(),
            "updated_at": _now(),
            "started_by": actor.subject_id,
            "on_duplicate": on_duplicate,
            "near_choices": near_choices or None,
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
            if row is not None:
                await enqueue(s, job_id, actor.workspace_id)
        if row is None:
            raise HTTPException(409, "restore_busy")
        return row

    async def discard(self, actor: ActorContext, job_id: UUID) -> None:
        """작업과 폴더를 지운다. 실행 중이면 큐에서 아직 기다릴 때만 (취소), 아니면 409."""
        async with self.db.workspace_session(actor.workspace_id) as s:
            row = await s.scalar(
                select(RestoreJobRow).where(RestoreJobRow.id == job_id).with_for_update()
            )
            if row is not None and row.status in _RUNNING and not await cancel_queued(s, job_id):
                raise HTTPException(409, "restore_busy")
            await s.execute(delete(RestoreJobRow).where(RestoreJobRow.id == job_id))
        await asyncio.to_thread(self._remove_dir, job_id)

    def close(self) -> None:
        """예전 API 호환: 확인·합치기는 worker가 돌므로 이 프로세스에서 멈출 것이 없다."""


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


class NearDuplicateOut(BaseModel):
    """정체 칸만 다른 (들어오는 행, 기존 행) 한 쌍 (c0027). 값은 정체 칸만, 긴 글은 잘림."""

    table: str
    incoming_id: str
    existing_id: str
    columns: list[str]
    incoming: dict[str, Any]
    existing: dict[str, Any]
    decision: Literal["keep_both", "skip_incoming"] = NEAR_KEEP_BOTH


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
    # c0027: 비슷한 행 쌍 (상한까지)과 전체 수
    near_duplicates: list[NearDuplicateOut] = []
    near_duplicates_total: int = 0


class NearChoiceIn(BaseModel):
    """비슷한 행 한 쌍의 처리. 기본(``keep_both``)은 지금처럼 둘 다 둔다."""

    table: str = Field(max_length=100)
    incoming_id: UUID
    existing_id: UUID
    decision: Literal["keep_both", "skip_incoming"] = NEAR_SKIP_INCOMING


class NearChoiceOut(BaseModel):
    table: str
    incoming_id: str
    existing_id: str


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
    # c0027: 합치기에서 들어오는 행을 건너뛴 비슷한 행 쌍
    near_choices: list[NearChoiceOut] = []


class VerifyIn(BaseModel):
    on_duplicate: Literal["skip", "fail"] = "skip"
    # 암호화한 묶음이면 age 신원 파일 내용 (AGE-SECRET-KEY-1…). 저장하지 않는다.
    identity: SecretStr | None = None


class ApplyIn(VerifyIn):
    # 확인 화면에서 본 묶음의 sha256 (다른 파일로 바뀌지 않았음을 확인)
    archive_sha256: str
    # 확인 보고의 비슷한 행 쌍마다 고른 처리 (없거나 keep_both면 둘 다 둔다)
    near_duplicates: list[NearChoiceIn] = Field(default=[], max_length=1000)


def report_dict(report: MergeReport) -> dict:
    """보고를 응답 모양으로 (DB jsonb에 둘 수 있게 UUID 등을 글자로)."""
    return MergeReportOut.model_validate(report_json(report)).model_dump(mode="json")


def _out(job: RestoreJobRow, live=None) -> RestoreJobOut:
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
        heartbeat_at=job.heartbeat_at,
        near_choices=[NearChoiceOut.model_validate(c) for c in job.near_choices or ()],
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


def _near_choices(job: RestoreJobRow, choices: list[NearChoiceIn]) -> list[dict]:
    """고른 쌍이 확인 보고에 오른 쌍인지 보고, 건너뛰기를 고른 쌍만 돌려준다."""
    listed = {
        (p["table"], p["incoming_id"], p["existing_id"])
        for p in (job.report or {}).get("near_duplicates", [])
    }
    out = []
    for c in choices:
        pair = (c.table, str(c.incoming_id), str(c.existing_id))
        if pair not in listed:
            raise HTTPException(409, "near_duplicate_unknown")
        if c.decision == NEAR_SKIP_INCOMING:
            out.append({"table": pair[0], "incoming_id": pair[1], "existing_id": pair[2]})
    return out


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
    stale = getattr(deps, "restore_stale_seconds", None) or STALE_SECONDS
    jobs = RestoreJobs(deps.db, root=getattr(deps, "restore_dir", None), stale=stale)
    owner = Annotated[ActorContext, Depends(authorize("workspace.restore"))]
    limit = int(getattr(deps, "max_restore_bytes", 2 << 30))

    async def start(
        job: RestoreJobRow,
        actor: ActorContext,
        body: VerifyIn,
        *,
        dry_run: bool,
        near_choices: list[dict] | None = None,
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
        # worker가 읽을 신원 파일 (worker가 쓴 뒤 지운다). 시작하지 못하면 바로 지운다.
        await asyncio.to_thread(jobs.folder.write_identity, job.id, keys)
        try:
            return await jobs.begin(
                actor,
                job.id,
                expect=job.status,
                status="verifying" if dry_run else "applying",
                clear_report=dry_run,
                on_duplicate=body.on_duplicate,
                near_choices=near_choices,
            )
        except BaseException:
            await asyncio.to_thread(jobs.folder.remove_identity, job.id)
            raise

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
        return _out(row)

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
        return _out(await jobs.get(actor, job_id))

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
        return _out(await start(job, actor, body, dry_run=True))

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
        choices = _near_choices(job, body.near_duplicates)
        return _out(await start(job, actor, body, dry_run=False, near_choices=choices))

    @router.delete("/maintenance/restore/{job_id}", status_code=204)
    async def discard(job_id: UUID, actor: owner) -> Response:
        await jobs.get(actor, job_id, touch=False)
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

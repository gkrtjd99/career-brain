"""원본 업로드. 같은 공간의 같은 바이트는 한 번만 저장·처리한다."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from career_core.db.models import Job, ParseRun, Source, SourceVersion
from career_core.db.session import Database
from career_core.domain.sources import PIPELINE_VERSION, DataClass, ParseStatus
from career_core.ports import ActorContext, BlobStore
from career_core.uploads import UploadRejected, clean_filename, resolve_mime

PARSE_JOB = "parse"


@dataclass(frozen=True, slots=True)
class UploadResult:
    source_id: UUID
    version_id: UUID
    parse_run_id: UUID | None
    deduplicated: bool


async def _existing(db: Database, workspace_id: UUID, sha256: str) -> UploadResult | None:
    async with db.workspace_session(workspace_id) as s:
        version = await s.scalar(select(SourceVersion).where(SourceVersion.sha256 == sha256))
        if version is None:
            return None
        run_id = await s.scalar(
            select(ParseRun.id).where(
                ParseRun.source_version_id == version.id,
                ParseRun.pipeline_version == PIPELINE_VERSION,
            )
        )
        return UploadResult(version.source_id, version.id, run_id, deduplicated=True)


async def upload_source(
    db: Database,
    blobs: BlobStore,
    actor: ActorContext,
    *,
    filename: str | None,
    data: bytes,
    declared_mime: str | None,
    max_bytes: int,
    title: str | None = None,
    data_class: str = DataClass.PRIVATE,
) -> UploadResult:
    if not data:
        raise UploadRejected("empty", 400)
    if len(data) > max_bytes:
        raise UploadRejected("too_large", 413)
    try:
        dc = DataClass(data_class)
    except ValueError as exc:
        raise UploadRejected("invalid_data_class", 422) from exc
    name = clean_filename(filename)
    mime = resolve_mime(data, declared_mime, name)
    sha256 = hashlib.sha256(data).hexdigest()
    ws = actor.workspace_id

    if found := await _existing(db, ws, sha256):
        return found

    # 파일 쓰기는 DB 트랜잭션 밖에서. 내용 주소라 중복 쓰기는 무해하다.
    key = await blobs.put(ws, sha256, data, mime)
    source = Source(
        id=uuid4(),
        workspace_id=ws,
        title=(title or name)[:300],
        data_class=dc,
        created_by=actor.subject_id,
    )
    version = SourceVersion(
        id=uuid4(),
        workspace_id=ws,
        source_id=source.id,
        sha256=sha256,
        size_bytes=len(data),
        mime_type=mime,
        original_filename=name,
        blob_key=key,
    )
    run = ParseRun(
        id=uuid4(),
        workspace_id=ws,
        source_version_id=version.id,
        pipeline_version=PIPELINE_VERSION,
        status=ParseStatus.PENDING,
        requested_by=actor.subject_id,
    )
    try:
        async with db.workspace_session(ws) as s:
            # 복합 FK는 ORM 관계로 선언하지 않았으므로 순서대로 flush한다.
            for row in (
                source,
                version,
                run,
                Job(workspace_id=ws, kind=PARSE_JOB, target_id=run.id),
            ):
                s.add(row)
                await s.flush()
    except IntegrityError:
        # 동시에 같은 파일이 올라온 경우: 먼저 커밋된 쪽을 돌려준다.
        if found := await _existing(db, ws, sha256):
            return found
        raise
    return UploadResult(source.id, version.id, run.id, deduplicated=False)

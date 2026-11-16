"""올린 DOCX·PDF의 숨김 글 검사 (c0016). 경고만 한다 — 파일을 고치거나 막지 않는다.

- ``check_file``: 요청에 실린 파일을 검사만 하고 저장하지 않는다 (다른 도구로 만든 이력서·포트폴리오를
  제출 전에 확인). 공간 자료를 읽지 않는다.
- ``check_source``: 이미 올린 원본(``sources``)의 한 버전을 검사한다. 원본 업로드가 받는 형식 중에서는
  PDF와 DOCX(c0023부터 원본 형식)가 대상이다. 다른 형식은 ``unsupported_type``.

검사는 CPU 작업이라 이벤트 루프를 막지 않게 스레드에서 돈다.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from career_core.db import Database
from career_core.db.models import Source, SourceVersion
from career_core.domain.hidden_text import FileInspection, InspectError, inspect_file
from career_core.ports import ActorContext, BlobStore
from career_core.usecases.brain import NotFound


@dataclass(frozen=True, slots=True)
class FileCheckResult:
    format: str
    filename: str | None
    bytes: int
    pages: int | None
    pages_checked: int | None
    chars: int
    findings: list[dict]
    summary: dict[str, int]
    source_id: UUID | None = None
    version_id: UUID | None = None


def _result(
    insp: FileInspection,
    filename: str | None,
    size: int,
    source_id: UUID | None = None,
    version_id: UUID | None = None,
) -> FileCheckResult:
    return FileCheckResult(
        insp.format,
        filename,
        size,
        insp.pages,
        insp.pages_checked,
        insp.chars,
        [f.as_dict() for f in insp.findings],
        insp.summary(),
        source_id,
        version_id,
    )


async def check_file(data: bytes, filename: str | None, *, max_bytes: int) -> FileCheckResult:
    if not data:
        raise InspectError("empty")
    if len(data) > max_bytes:
        raise InspectError("too_large")
    insp = await asyncio.to_thread(inspect_file, data, filename)
    return _result(insp, filename, len(data))


async def check_source(
    db: Database,
    blobs: BlobStore,
    actor: ActorContext,
    source_id: UUID,
    *,
    version_id: UUID | None = None,
) -> FileCheckResult:
    """원본의 한 버전(기본: 가장 최근)을 검사한다."""
    async with db.workspace_session(actor.workspace_id) as s:
        if await s.get(Source, source_id) is None:
            raise NotFound("source")
        q = select(SourceVersion).where(SourceVersion.source_id == source_id)
        if version_id is not None:
            q = q.where(SourceVersion.id == version_id)
        version = await s.scalar(q.order_by(SourceVersion.created_at.desc()).limit(1))
        if version is None:
            raise NotFound("source_version")
    data = await blobs.get(actor.workspace_id, version.blob_key)
    insp = await asyncio.to_thread(inspect_file, data, version.original_filename)
    return _result(insp, version.original_filename, len(data), source_id, version.id)


__all__ = ["FileCheckResult", "InspectError", "check_file", "check_source"]

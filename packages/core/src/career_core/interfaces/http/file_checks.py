"""올린 DOCX·PDF의 숨김 글 검사 경로 (c0016). 경고만 하고 파일을 고치거나 막지 않는다.

- ``POST /file-checks/hidden-text`` (multipart ``file``): 검사만 하고 저장하지 않는다.
- ``GET /sources/{source_id}/hidden-text?version_id=``: 이미 올린 원본 한 버전.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from career_core.ports import ActorContext
from career_core.usecases import file_checks as uc
from career_core.usecases.brain import NotFound

_STATUS = {
    "empty": 400,
    "too_large": 413,
    "unsupported_type": 415,
}


class FileCheckOut(BaseModel):
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


def _error(exc: uc.InspectError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(exc.code, 422), detail=exc.code)


def add_file_check_routes(router: APIRouter, deps, authorize) -> None:
    max_bytes = getattr(deps, "max_upload_bytes", 20 * 1024 * 1024)
    checker = Annotated[ActorContext, Depends(authorize("applications.read"))]

    @router.post("/file-checks/hidden-text", response_model=FileCheckOut)
    async def check_file(actor: checker, file: Annotated[UploadFile, File()]) -> FileCheckOut:
        """제출할 DOCX·PDF를 검사만 한다 (저장하지 않는다): 숨김·흰 글씨·작은 글자·쪽 밖 글·지시문."""
        data = await file.read(max_bytes + 1)
        try:
            res = await uc.check_file(data, file.filename, max_bytes=max_bytes)
        except uc.InspectError as exc:
            raise _error(exc) from exc
        return FileCheckOut.model_validate(res, from_attributes=True)

    if deps.blobs is None:
        return
    source_reader = Annotated[ActorContext, Depends(authorize("sources.read"))]

    @router.get("/sources/{source_id}/hidden-text", response_model=FileCheckOut)
    async def check_source(
        actor: source_reader, source_id: UUID, version_id: UUID | None = None
    ) -> FileCheckOut:
        try:
            res = await uc.check_source(
                deps.db, deps.blobs, actor, source_id, version_id=version_id
            )
        except NotFound as exc:
            raise HTTPException(status_code=404, detail="not found") from exc
        except uc.InspectError as exc:
            raise _error(exc) from exc
        return FileCheckOut.model_validate(res, from_attributes=True)


__all__ = ["add_file_check_routes"]

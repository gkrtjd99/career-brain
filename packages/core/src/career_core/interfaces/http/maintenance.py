"""유지보수·내보내기 경로 (O11). 모두 소유자(workspace-admin)만.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from career_core.jobs.maintenance import maintenance_status
from career_core.ports import ActorContext
from career_core.usecases.export import ExportError, export_deletions, export_workspace
from career_core.usecases.maintenance import RunView, maintenance_info, purge_drafts


class RunOut(BaseModel):
    kind: str
    via: str
    created_at: datetime
    archive_sha256: str
    archive_bytes: int
    rows_total: int
    blobs_total: int


class BlobUsageOut(BaseModel):
    referenced: int
    files: int | None
    bytes: int | None


class AutoMaintenanceOut(BaseModel):
    """주기 유지보수 (c0020). 일정은 설치 전체의 시각만, 결과 수는 이 공간 것만."""

    last_started_at: datetime | None
    last_finished_at: datetime | None
    interval_seconds: float | None
    next_at: datetime | None
    running: bool
    counts: dict[str, int]  # 마지막 실행에서 이 공간에 한 일
    errors: int
    last_changed_at: datetime | None  # 이 공간에서 무언가 정리한 마지막 실행


class MaintenanceOut(BaseModel):
    tables: dict[str, int]
    excluded: dict[str, int]
    rows_total: int
    blobs: BlobUsageOut
    last_export: RunOut | None
    last_restore: RunOut | None
    drafts_purgeable: int
    draft_retention_days: int
    export_available: bool
    auto: AutoMaintenanceOut | None = None


class PurgeOut(BaseModel):
    deleted: int


def _run(run: RunView | None) -> RunOut | None:
    return None if run is None else RunOut.model_validate(run, from_attributes=True)


def add_maintenance_routes(router: APIRouter, deps, authorize) -> None:
    @router.get("/maintenance", response_model=MaintenanceOut)
    async def maintenance(
        actor: Annotated[ActorContext, Depends(authorize("maintenance.read"))],
    ) -> MaintenanceOut:
        info = await maintenance_info(deps.db, actor.workspace_id, deps.blobs)
        return MaintenanceOut(
            tables=info.tables,
            excluded=info.excluded,
            rows_total=info.rows_total,
            blobs=BlobUsageOut(
                referenced=info.blobs_referenced, files=info.blob_files, bytes=info.blob_bytes
            ),
            last_export=_run(info.last_export),
            last_restore=_run(info.last_restore),
            drafts_purgeable=info.drafts_purgeable,
            draft_retention_days=info.draft_retention_days,
            export_available=deps.blobs is not None,
            auto=AutoMaintenanceOut.model_validate(
                await maintenance_status(deps.db, actor.workspace_id), from_attributes=True
            ),
        )

    @router.post("/maintenance/purge-drafts", response_model=PurgeOut)
    async def purge(
        actor: Annotated[ActorContext, Depends(authorize("maintenance.purge"))],
    ) -> PurgeOut:
        return PurgeOut(deleted=await purge_drafts(deps.db, actor.workspace_id))

    @router.get("/maintenance/deletions", response_class=FileResponse)
    async def deletions(
        actor: Annotated[ActorContext, Depends(authorize("workspace.export"))],
    ) -> FileResponse:
        """삭제 기록(JSONL). 묶음보다 자주 받아 두면 오래된 묶음을 복원한 뒤 다시 적용한다."""
        tmp = Path(tempfile.mkdtemp(prefix="career-deletions-"))
        name = f"career-deletions-{datetime.now(UTC):%Y%m%d-%H%M%S}.jsonl"
        try:
            await export_deletions(deps.db, actor, tmp / name, permissions=deps.permissions)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return FileResponse(
            tmp / name,
            media_type="application/x-ndjson",
            filename=name,
            headers={"Cache-Control": "no-store"},
            background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True),
        )

    if deps.blobs is None:
        return  # 원본 저장소 없이는 온전한 묶음을 만들 수 없다

    @router.get("/maintenance/export", response_class=FileResponse)
    async def export(
        actor: Annotated[ActorContext, Depends(authorize("workspace.export"))],
        recipient: Annotated[list[str] | None, Query()] = None,
    ) -> FileResponse:
        """``recipient``(age 공개 키, 여러 번 가능)를 주면 암호화한 묶음(``.tar.gz.age``)을 돌려준다."""
        tmp = Path(tempfile.mkdtemp(prefix="career-export-"))
        name = f"career-export-{datetime.now(UTC):%Y%m%d-%H%M%S}.tar.gz"
        if recipient:
            name += ".age"
        try:
            report = await export_workspace(
                deps.db,
                deps.blobs,
                actor,
                tmp / name,
                via="http",
                permissions=deps.permissions,
                recipients=recipient,
            )
        except ExportError as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            if exc.code == "forbidden":
                raise HTTPException(403, "forbidden") from exc
            raise HTTPException(400, exc.code) from exc
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return FileResponse(
            report.path,
            media_type="application/octet-stream" if report.encrypted else "application/gzip",
            filename=name,
            headers={"X-Archive-SHA256": report.sha256, "Cache-Control": "no-store"},
            background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True),
        )

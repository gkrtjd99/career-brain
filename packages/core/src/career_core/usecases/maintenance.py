"""유지보수 정보 (O11): 자료 수, 원본 용량, 마지막 내보내기·복원, 정리 대기 초안."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, text

from career_core.db.base import utcnow
from career_core.db.models import ExportRun, JobPosting
from career_core.db.session import Database
from career_core.domain.postings import PostingStatus
from career_core.ports import BlobStore
from career_core.usecases.export import build_plan, read_catalog, scope_sql
from career_core.usecases.postings import UNCONFIRMED_DAYS, purge_unconfirmed


@dataclass(frozen=True, slots=True)
class RunView:
    kind: str
    via: str
    created_at: datetime
    archive_sha256: str
    archive_bytes: int
    rows_total: int
    blobs_total: int


@dataclass(frozen=True, slots=True)
class MaintenanceInfo:
    tables: dict[str, int]  # 내보내기에 들어가는 표
    excluded: dict[str, int]  # 들어가지 않는 표 (다시 만들 수 있는 것)
    rows_total: int
    blobs_referenced: int
    blob_files: int | None  # 저장소가 세어 줄 수 있을 때만
    blob_bytes: int | None
    last_export: RunView | None
    last_restore: RunView | None
    drafts_purgeable: int
    draft_retention_days: int


def _view(run: ExportRun | None) -> RunView | None:
    if run is None:
        return None
    return RunView(
        kind=run.kind,
        via=run.via,
        created_at=run.created_at,
        archive_sha256=run.archive_sha256,
        archive_bytes=run.archive_bytes,
        rows_total=run.rows_total,
        blobs_total=run.blobs_total,
    )


async def maintenance_info(
    db: Database, workspace_id: UUID, blobs: BlobStore | None = None, *, now: datetime | None = None
) -> MaintenanceInfo:
    cutoff = (now or utcnow()) - timedelta(days=UNCONFIRMED_DAYS)
    async with db.workspace_session(workspace_id) as s:
        conn = await s.connection()
        tables = await read_catalog(conn)
        plan = build_plan(tables)
        params = {"ws": str(workspace_id)}
        counts: dict[str, int] = {}
        blob_keys: set[str] = set()
        for t in plan.tables:
            where = scope_sql(t)
            counts[t.name] = await s.scalar(
                text(f"SELECT count(*) FROM {t.sql} AS t WHERE {where}"), params
            )
            for col in t.blob_columns:
                keys = await s.scalars(
                    text(
                        f'SELECT DISTINCT t."{col}" FROM {t.sql} AS t '
                        f'WHERE {where} AND t."{col}" IS NOT NULL'
                    ),
                    params,
                )
                blob_keys.update(keys)
        excluded = {}
        for t in tables:
            if t.name in plan.excluded and t.scope is not None:
                excluded[t.name] = await s.scalar(
                    text(f"SELECT count(*) FROM {t.sql} AS t WHERE {scope_sql(t)}"), params
                )
        last = {}
        # 합치기(merge, c0018)도 복원으로 보여 준다 (kind는 그대로 남는다)
        for kind, kinds in (("export", ("export",)), ("restore", ("restore", "merge"))):
            last[kind] = await s.scalar(
                select(ExportRun)
                .where(ExportRun.kind.in_(kinds))
                .order_by(ExportRun.created_at.desc())
                .limit(1)
            )
        purgeable = await s.scalar(
            select(func.count())
            .select_from(JobPosting)
            .where(JobPosting.status == PostingStatus.DRAFT, JobPosting.created_at < cutoff)
        )
    files = size = None
    usage = getattr(blobs, "disk_usage", None)
    if usage is not None:
        files, size = await usage(workspace_id)
    return MaintenanceInfo(
        tables=counts,
        excluded=excluded,
        rows_total=sum(counts.values()),
        blobs_referenced=len(blob_keys),
        blob_files=files,
        blob_bytes=size,
        last_export=_view(last["export"]),
        last_restore=_view(last["restore"]),
        drafts_purgeable=purgeable or 0,
        draft_retention_days=UNCONFIRMED_DAYS,
    )


async def purge_drafts(db: Database, workspace_id: UUID, *, now: datetime | None = None) -> int:
    """확정하지 않은 공고 초안 정리 (보존 규칙 7일). 지운 수를 돌려준다."""
    return await purge_unconfirmed(db, workspace_id, now=now)

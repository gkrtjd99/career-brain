"""원본 업로드. 같은 공간의 같은 바이트는 한 번만 저장·처리한다."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from career_core.db.models import ExtractionRun, Job, ParseRun, Source, SourceVersion
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


_STRICTNESS = {DataClass.PUBLIC: 0, DataClass.PRIVATE: 1, DataClass.RESTRICTED: 2}


async def _existing(
    db: Database, workspace_id: UUID, sha256: str, data_class: DataClass
) -> UploadResult | None:
    async with db.workspace_session(workspace_id) as s:
        version = await s.scalar(select(SourceVersion).where(SourceVersion.sha256 == sha256))
        if version is None:
            return None
        # 같은 파일을 더 엄격한 등급으로 다시 올리면 등급을 올린다 (낮추지는 않는다).
        source = await s.get(Source, version.source_id)
        if _STRICTNESS[data_class] > _STRICTNESS[DataClass(source.data_class)]:
            source.data_class = data_class.value
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

    if found := await _existing(db, ws, sha256, dc):
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
        if found := await _existing(db, ws, sha256, dc):
            return found
        raise
    return UploadResult(source.id, version.id, run.id, deduplicated=False)


# ---------------------------------------------------------------- 목록


@dataclass(frozen=True, slots=True)
class SourceListItem:
    id: UUID
    title: str
    data_class: str
    created_at: datetime
    versions: int
    latest_version_id: UUID | None
    original_filename: str | None
    mime_type: str | None
    size_bytes: int | None
    # 최신 버전의 최신 파이프라인 읽기 결과
    parse_run_id: UUID | None
    parse_status: str | None
    pages_total: int | None
    missing_pages: list[int]
    parse_error: str | None
    # 그 읽기 결과로 요청한 마지막 경험 추출
    extraction_run_id: UUID | None
    extraction_status: str | None
    claims_created: int | None


async def list_sources(
    db: Database, actor: ActorContext, *, limit: int = 100
) -> list[SourceListItem]:
    """올린 원본 목록 (최근 것부터)과 각 원본 최신 버전의 읽기·추출 상태."""
    limit = max(1, min(limit, 500))
    async with db.workspace_session(actor.workspace_id) as s:
        sources = (
            (
                await s.execute(
                    select(Source).order_by(Source.created_at.desc(), Source.id).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        ids = [x.id for x in sources]
        versions: dict[UUID, list[SourceVersion]] = {}
        if ids:
            for v in (
                await s.execute(
                    select(SourceVersion)
                    .where(SourceVersion.source_id.in_(ids))
                    .order_by(SourceVersion.created_at.desc(), SourceVersion.id)
                )
            ).scalars():
                versions.setdefault(v.source_id, []).append(v)
        latest_ids = [vs[0].id for vs in versions.values()]
        runs: dict[UUID, ParseRun] = {}
        if latest_ids:
            for r in (
                await s.execute(
                    select(ParseRun)
                    .where(ParseRun.source_version_id.in_(latest_ids))
                    .order_by(ParseRun.pipeline_version, ParseRun.created_at)
                )
            ).scalars():
                runs[r.source_version_id] = r  # 마지막(가장 높은 파이프라인)이 남는다
        run_ids = [r.id for r in runs.values()]
        extractions: dict[UUID, ExtractionRun] = {}
        if run_ids:
            for e in (
                await s.execute(
                    select(ExtractionRun)
                    .where(ExtractionRun.parse_run_id.in_(run_ids))
                    .order_by(ExtractionRun.created_at)
                )
            ).scalars():
                extractions[e.parse_run_id] = e
    out = []
    for src in sources:
        vs = versions.get(src.id, [])
        v = vs[0] if vs else None
        r = runs.get(v.id) if v else None
        e = extractions.get(r.id) if r else None
        out.append(
            SourceListItem(
                id=src.id,
                title=src.title,
                data_class=src.data_class,
                created_at=src.created_at,
                versions=len(vs),
                latest_version_id=v.id if v else None,
                original_filename=v.original_filename if v else None,
                mime_type=v.mime_type if v else None,
                size_bytes=v.size_bytes if v else None,
                parse_run_id=r.id if r else None,
                parse_status=r.status if r else None,
                pages_total=r.pages_total if r else None,
                missing_pages=list(r.missing_pages or []) if r else [],
                parse_error=r.error_code if r else None,
                extraction_run_id=e.id if e else None,
                extraction_status=e.status if e else None,
                claims_created=e.claims_created if e else None,
            )
        )
    return out

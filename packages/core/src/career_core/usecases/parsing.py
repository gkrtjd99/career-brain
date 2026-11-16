"""파싱 실행. worker가 parse 작업마다 ``process_parse_run``을 호출한다.

순서: 로컬 추출 → 텍스트 없는 쪽이 있으면 외부 파서 후보 → EgressPolicy 검사 → 제출
→ provider_job_id 저장 → 이후 실행은 상태 조회만 한다 (재시작해도 다시 제출하지 않음).
네트워크 호출 동안 DB 트랜잭션을 열어 두지 않는다.

lease 확인(``jobs.lease.guard_lease``): 블록 교체·끝 기록·제공자 작업 ID 저장은 같은 트랜잭션에서 먼저
작업 행을 잠그고 이 worker가 아직 lease를 쥐고 있는지 본다. 큰 PDF를 오래 읽다 lease를 잃은 옛 worker가
새 worker의 결과(제공자 대기·끝난 상태)를 되돌리지 않는다. 외부 제출 직전에도 한 번 본다 (중복 제출 줄이기).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, func, select

from career_core.db.models import DocumentBlock, ParseRun, Source, SourceVersion
from career_core.db.session import Database
from career_core.domain.sources import TERMINAL_PARSE_STATUSES, ParseStatus
from career_core.jobs.lease import guard_lease
from career_core.ports import (
    ActorContext,
    BlobStore,
    EgressPolicy,
    HiddenTextParser,
    LocalParser,
    LocalParseResult,
    ParsedBlock,
    ParseError,
    ParserProvider,
)
from career_core.usecases.index_queue import request_index

LOCAL_ORIGIN = "local"
EGRESS_PURPOSE = "parse"


@dataclass(frozen=True, slots=True)
class ParsingDeps:
    db: Database
    blobs: BlobStore
    egress: EgressPolicy
    local_parsers: Mapping[str, LocalParser]
    provider: ParserProvider | None = None
    poll_delay_seconds: float = 5.0


@dataclass(frozen=True, slots=True)
class StepOutcome:
    done: bool
    retry_after: float | None = None


@dataclass(frozen=True, slots=True)
class _RunView:
    status: str
    provider_job_id: str | None
    requested_pages: list[int]
    requested_by: UUID
    blob_key: str
    mime_type: str
    data_class: str
    hidden_text: str = "keep"


async def _load(db: Database, ws: UUID, run_id: UUID) -> _RunView | None:
    async with db.workspace_session(ws) as s:
        run = await s.get(ParseRun, run_id)
        if run is None:
            return None
        version = await s.get(SourceVersion, run.source_version_id)
        source = await s.get(Source, version.source_id)
        return _RunView(
            status=run.status,
            provider_job_id=run.provider_job_id,
            requested_pages=list(run.requested_pages or []),
            requested_by=run.requested_by,
            blob_key=version.blob_key,
            mime_type=version.mime_type,
            data_class=source.data_class,
            hidden_text=run.hidden_text or "keep",
        )


async def _replace_blocks(
    db: Database, ws: UUID, run_id: UUID, origin: str, blocks: Iterable[ParsedBlock], *, start: int
) -> None:
    async with db.workspace_session(ws) as s:
        await guard_lease(s, run_id)
        await s.execute(
            delete(DocumentBlock).where(
                DocumentBlock.parse_run_id == run_id, DocumentBlock.origin == origin
            )
        )
        for i, b in enumerate(blocks):
            s.add(
                DocumentBlock(
                    workspace_id=ws,
                    parse_run_id=run_id,
                    page=b.page,
                    ordinal=start + i,
                    origin=origin,
                    text=b.text,
                )
            )


async def _finish(
    db: Database,
    ws: UUID,
    run_id: UUID,
    *,
    missing: list[int],
    error_code: str | None,
    pages_total: int | None = None,
) -> StepOutcome:
    async with db.workspace_session(ws) as s:
        await guard_lease(s, run_id)
        run = await s.get(ParseRun, run_id)
        has_blocks = bool(
            await s.scalar(
                select(func.count())
                .select_from(DocumentBlock)
                .where(DocumentBlock.parse_run_id == run_id)
            )
        )
        if not missing and has_blocks:
            run.status = ParseStatus.COMPLETE
        elif has_blocks:
            run.status = ParseStatus.PARTIAL
        else:
            run.status = ParseStatus.FAILED
            error_code = error_code or "no_text"
        run.missing_pages = sorted(missing)
        run.error_code = error_code
        if has_blocks:
            await request_index(s, ws)
        if pages_total is not None:
            run.pages_total = pages_total
    return StepOutcome(done=True)


def _missing_pages(result: LocalParseResult) -> list[int]:
    if result.pages_total is None:
        return []
    return sorted(set(range(1, result.pages_total + 1)) - result.pages_with_text)


async def process_parse_run(deps: ParsingDeps, workspace_id: UUID, run_id: UUID) -> StepOutcome:
    view = await _load(deps.db, workspace_id, run_id)
    if view is None or view.status in TERMINAL_PARSE_STATUSES:
        return StepOutcome(done=True)
    if view.provider_job_id:
        return await _poll_provider(deps, workspace_id, run_id, view)

    data = await deps.blobs.get(workspace_id, view.blob_key)
    parser = deps.local_parsers.get(view.mime_type)
    if parser is None:
        return await _finish(
            deps.db, workspace_id, run_id, missing=[1], error_code="unsupported_type"
        )
    try:
        # PDF(최대 300쪽)·HTML 파싱은 동기 CPU 작업: 스레드에서 해 heartbeat가 lease를 계속 늘리게 한다
        # c0029: 숨김 서식 글을 빼기로 한 파싱은 그것을 할 수 있는 추출기에서만 (지금은 DOCX)
        parse = (
            parser.parse_visible
            if view.hidden_text == "exclude" and isinstance(parser, HiddenTextParser)
            else parser.parse
        )
        result = await asyncio.to_thread(parse, data)
    except ParseError as exc:
        return await _finish(deps.db, workspace_id, run_id, missing=[], error_code=exc.code)

    await _replace_blocks(deps.db, workspace_id, run_id, LOCAL_ORIGIN, result.blocks, start=0)
    missing = _missing_pages(result)
    if not missing:
        return await _finish(
            deps.db,
            workspace_id,
            run_id,
            missing=[],
            error_code=None,
            pages_total=result.pages_total,
        )

    provider = deps.provider
    if provider is None:
        return await _finish(
            deps.db,
            workspace_id,
            run_id,
            missing=missing,
            error_code="ocr_unavailable",
            pages_total=result.pages_total,
        )
    actor = ActorContext(subject_id=view.requested_by, workspace_id=workspace_id)
    if not deps.egress.allows(actor, provider.name, view.data_class, EGRESS_PURPOSE):
        return await _finish(
            deps.db,
            workspace_id,
            run_id,
            missing=missing,
            error_code="egress_denied",
            pages_total=result.pages_total,
        )

    # lease를 잃은 worker는 제출하지 않는다 (새 worker가 이미 제출했을 수 있다)
    async with deps.db.workspace_session(workspace_id) as s:
        await guard_lease(s, run_id)
    # 제출과 ID 저장 사이에 worker가 죽으면 다시 제출될 수 있다. idempotency_key로 제공자 쪽 중복을 줄인다.
    provider_job_id = await provider.submit(
        data, view.mime_type, missing, idempotency_key=str(run_id)
    )
    async with deps.db.workspace_session(workspace_id) as s:
        await guard_lease(s, run_id)
        run = await s.get(ParseRun, run_id)
        run.provider = provider.name
        run.provider_job_id = provider_job_id
        run.requested_pages = missing
        run.pages_total = result.pages_total
        run.status = ParseStatus.WAITING_PROVIDER
    return StepOutcome(done=False, retry_after=deps.poll_delay_seconds)


async def _poll_provider(deps: ParsingDeps, ws: UUID, run_id: UUID, view: _RunView) -> StepOutcome:
    provider = deps.provider
    if provider is None:
        return StepOutcome(done=False, retry_after=deps.poll_delay_seconds)
    status = await provider.poll(view.provider_job_id)
    if status.state == "pending":
        return StepOutcome(done=False, retry_after=deps.poll_delay_seconds)
    if status.state != "done":
        return await _finish(
            deps.db, ws, run_id, missing=view.requested_pages, error_code="provider_failed"
        )

    requested = set(view.requested_pages)
    returned = set(status.returned_pages) & requested
    blocks = [b for b in status.blocks if b.page in returned]
    async with deps.db.workspace_session(ws) as s:
        start = await s.scalar(
            select(func.coalesce(func.max(DocumentBlock.ordinal), -1) + 1).where(
                DocumentBlock.parse_run_id == run_id, DocumentBlock.origin == LOCAL_ORIGIN
            )
        )
    await _replace_blocks(deps.db, ws, run_id, f"provider:{provider.name}", blocks, start=start)
    still_missing = sorted(requested - returned)
    return await _finish(
        deps.db,
        ws,
        run_id,
        missing=still_missing,
        error_code="provider_partial" if still_missing else None,
    )


async def give_up_parse_run(deps: ParsingDeps, workspace_id: UUID, run_id: UUID) -> None:
    async with deps.db.workspace_session(workspace_id) as s:
        run = await s.get(ParseRun, run_id)
        if run is not None and run.status not in TERMINAL_PARSE_STATUSES:
            run.status = ParseStatus.FAILED
            run.error_code = "worker_error"

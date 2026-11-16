"""공고 저장 경로.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from career_core.domain.postings import PostingRuleError
from career_core.ports import ActorContext
from career_core.usecases import postings as uc
from career_core.usecases.brain import NotFound


class SaveIn(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=uc.MAX_URLS)


class SaveOut(BaseModel):
    url: str
    posting_id: UUID | None
    fetch_run_id: UUID | None
    deduplicated: bool
    error: str | None


class SummaryOut(BaseModel):
    id: UUID
    canonical_url: str
    status: str
    title: str | None
    company: str | None
    deadline: str | None
    pending_changes: list[str]
    last_fetch_status: str | None
    last_fetch_error: str | None


class RunOut(BaseModel):
    id: UUID
    status: str
    error_code: str | None
    http_status: int | None
    unchanged: bool
    version_id: UUID | None
    candidates: list[dict]
    notes: list[str]
    created_at: datetime


class VersionOut(BaseModel):
    id: UUID
    version_no: int
    method: str
    adapter: str
    final_url: str
    extracted: dict
    missing: list[str]
    changed_fields: list[str]
    has_screenshot: bool
    created_at: datetime
    text: str | None


class PostingOut(BaseModel):
    id: UUID
    canonical_url: str
    status: str
    fields: dict
    evidence: dict
    pending_changes: list[str]
    confirmed_version_id: UUID | None
    saved_at: datetime | None
    latest: VersionOut | None
    runs: list[RunOut]


class ConfirmIn(BaseModel):
    version_id: UUID | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)
    clear: list[str] = Field(default_factory=list)


class ManualIn(BaseModel):
    text: str = Field(min_length=1, max_length=uc.MAX_MANUAL_CHARS)


class StatusOut(BaseModel):
    id: UUID
    status: str


def _rule(exc: PostingRuleError) -> HTTPException:
    return HTTPException(status_code=422, detail=exc.code)


def add_posting_routes(router: APIRouter, deps, authorize) -> None:
    db = deps.db
    reader = Annotated[ActorContext, Depends(authorize("jobs.read"))]
    writer = Annotated[ActorContext, Depends(authorize("jobs.write"))]

    @router.post("/job-postings", response_model=list[SaveOut], status_code=202)
    async def save(actor: writer, body: SaveIn) -> list[SaveOut]:
        try:
            results = await uc.save_urls(db, actor, body.urls)
        except PostingRuleError as exc:
            raise _rule(exc) from exc
        return [SaveOut.model_validate(r, from_attributes=True) for r in results]

    @router.get("/job-postings", response_model=list[SummaryOut])
    async def index(actor: reader) -> list[SummaryOut]:
        return [
            SummaryOut.model_validate(p, from_attributes=True)
            for p in await uc.list_postings(db, actor)
        ]

    @router.get("/job-postings/{posting_id}", response_model=PostingOut)
    async def detail(actor: reader, posting_id: UUID) -> PostingOut:
        try:
            view = await uc.get_posting(db, actor, posting_id)
        except NotFound as exc:
            raise HTTPException(404, "not found") from exc
        return PostingOut.model_validate(view, from_attributes=True)

    @router.get("/job-postings/{posting_id}/versions/{version_id}", response_model=VersionOut)
    async def version(actor: reader, posting_id: UUID, version_id: UUID) -> VersionOut:
        try:
            view = await uc.get_version(db, actor, posting_id, version_id)
        except NotFound as exc:
            raise HTTPException(404, "not found") from exc
        return VersionOut.model_validate(view, from_attributes=True)

    @router.post("/job-postings/{posting_id}/refetch", response_model=StatusOut, status_code=202)
    async def refetch(actor: writer, posting_id: UUID) -> StatusOut:
        try:
            run_id = await uc.refetch(db, actor, posting_id)
        except NotFound as exc:
            raise HTTPException(404, "not found") from exc
        return StatusOut(id=run_id, status="pending")

    @router.post(
        "/job-postings/{posting_id}/manual-text", response_model=StatusOut, status_code=201
    )
    async def manual(actor: writer, posting_id: UUID, body: ManualIn) -> StatusOut:
        try:
            run_id = await uc.add_manual_text(
                uc.PostingDeps(db=db, fetcher=None, gateway=deps.models),
                actor,
                posting_id,
                body.text,
            )
        except NotFound as exc:
            raise HTTPException(404, "not found") from exc
        except PostingRuleError as exc:
            raise _rule(exc) from exc
        return StatusOut(id=run_id, status="done")

    @router.post("/job-postings/{posting_id}/confirm", response_model=StatusOut)
    async def confirm(actor: writer, posting_id: UUID, body: ConfirmIn) -> StatusOut:
        try:
            status = await uc.confirm_posting(
                db,
                actor,
                posting_id,
                version_id=body.version_id,
                overrides=body.overrides,
                clear=body.clear,
            )
        except NotFound as exc:
            raise HTTPException(404, "not found") from exc
        except PostingRuleError as exc:
            raise _rule(exc) from exc
        return StatusOut(id=posting_id, status=status)

    @router.post("/job-postings/{posting_id}/archive", response_model=StatusOut)
    async def archive(actor: writer, posting_id: UUID) -> StatusOut:
        try:
            status = await uc.archive_posting(db, actor, posting_id)
        except NotFound as exc:
            raise HTTPException(404, "not found") from exc
        return StatusOut(id=posting_id, status=status)

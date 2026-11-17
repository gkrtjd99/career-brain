"""이력서 판본 경로 (c0047, O17, plan §19.5).

읽기 ``resume.read``, 쓰기 ``resume.write``. 줄 다듬기(모델 호출)는 ``documents.ai``도 있어야 한다.

- ``GET /resumes`` (``?application_id=``), ``POST /resumes`` (마스터·직무별·공고별 만들기, 직무별·공고별은 위 판본 복사)
- ``GET|PATCH|DELETE /resumes/{id}`` — PATCH는 설정(제목·직무·템플릿·블라인드·쪽 수 한도·이은 지원)만
- ``POST /resumes/{id}/revisions`` — 수정 연산 묶음. ``base_revision``이 최신이 아니면 409
  ``{"code": "revision_conflict", "current_revision": n}``. ``GET /resumes/{id}/revisions``
- ``GET /resumes/{id}/picker`` — 항목 더하기 시트의 브레인 기록
- ``GET /resumes/{id}/check`` — 체크리스트 + 줄 검사
- ``GET /resumes/{id}/preview`` — 미리보기 HTML (내보낸 HTML 파일과 같은 바이트)
- ``GET /resumes/{id}/export?format=html|docx|md|json`` (+ ``/export/check``)
- ``POST /resumes/{id}/suggestions`` — 줄 다듬기 수정안 (저장하지 않는다)

오류는 ``{"detail": code}``: 없는 판본 404 ``not found``. ``master_exists``·``version_has_children`` 409.
그 밖의 규칙 422 (``parent_required``, ``invalid_parent``, ``claim_not_confirmed``, ``ref_fields_locked`` …).

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from career_core.domain.resume import MAX_OPERATIONS, ResumeConflict, ResumeRuleError
from career_core.llm import ModelCallError, ModelDenied
from career_core.ports import ActorContext
from career_core.usecases import resume_suggest as suggest_uc
from career_core.usecases import resumes as uc
from career_core.usecases.brain import NotFound

_RULE_STATUS = {
    "master_exists": status.HTTP_409_CONFLICT,
    "version_has_children": status.HTTP_409_CONFLICT,
    "models_not_configured": status.HTTP_503_SERVICE_UNAVAILABLE,
}


def _error(exc: ResumeRuleError) -> HTTPException:
    if isinstance(exc, ResumeConflict):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "current_revision": exc.current},
        )
    return HTTPException(status_code=_RULE_STATUS.get(exc.code, 422), detail=exc.code)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


Kind = Literal["master", "role", "posting"]
Template = Literal["one_column", "two_column", "korean_table"]


class CreateIn(BaseModel):
    kind: Kind
    title: str | None = Field(default=None, max_length=400)
    parent_id: UUID | None = None
    # 복사할 위 판본의 revision (없으면 최신)
    parent_revision: int | None = Field(default=None, ge=0)
    target_role: str | None = Field(default=None, max_length=400)
    application_id: UUID | None = None
    template: Template | None = None
    # 마스터: 브레인에서 한 번에 채우기 (claims면 확인한 사실도 줄로)
    fill: bool = False
    fill_claims: bool = False


class SettingsPatch(BaseModel):
    title: str | None = Field(default=None, max_length=400)
    target_role: str | None = Field(default=None, max_length=400)
    template: Template | None = None
    blind: bool | None = None
    max_pages: int | None = Field(default=None, ge=1, le=10)
    application_id: UUID | None = None


class SaveIn(BaseModel):
    base_revision: int = Field(ge=0)
    operations: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_OPERATIONS)
    reason: str | None = Field(default=None, max_length=1000)
    idempotency_key: str | None = Field(default=None, max_length=200)


class SuggestIn(BaseModel):
    base_revision: int = Field(ge=0)
    line_ids: list[str] | None = Field(default=None, max_length=uc.MAX_SUGGEST_LINES)
    instruction: str | None = Field(default=None, max_length=uc.MAX_INSTRUCTION_CHARS)


class CreatedOut(BaseModel):
    id: UUID


class SummaryOut(BaseModel):
    id: UUID
    kind: str
    title: str
    target_role: str | None
    parent_id: UUID | None
    parent_title: str | None
    source_revision_no: int | None
    parent_revision: int | None
    application_id: UUID | None
    application_title: str | None
    template: str
    blind: bool
    max_pages: int
    current_revision: int
    items: int
    lines: int
    unsupported_lines: int
    children: int
    created_at: datetime
    updated_at: datetime


class VersionOut(BaseModel):
    summary: SummaryOut
    revision_no: int
    revision_id: UUID | None
    revision_origin: str | None
    content: dict[str, Any]
    lineage: list[dict[str, Any]]
    blind_suggested: bool
    prep_choices: int


class RevisionOut(BaseModel):
    id: UUID
    revision_no: int
    created: bool


class RevisionSummaryOut(BaseModel):
    id: UUID
    revision_no: int
    origin: str
    reason: str | None
    content_hash: str
    created_by: UUID
    created_at: datetime


class ExportCheckOut(BaseModel):
    filename: str
    media_type: str
    format: str
    revision: int
    bytes: int
    findings: list[dict[str, Any]]
    summary: dict[str, int]


class LineSuggestionOut(BaseModel):
    line_id: str
    original_text: str
    suggested_text: str
    reason: str | None
    status: str
    reject_reasons: list[dict[str, Any]]


class SuggestOut(BaseModel):
    revision: int
    suggestions: list[LineSuggestionOut]
    questions: list[str]
    dropped: int
    model_call_id: UUID | None


Fmt = Annotated[Literal["html", "docx", "md", "json"], Query(alias="format")]


def add_resume_routes(router: APIRouter, deps, authorize) -> None:
    db = deps.db
    reader = Annotated[ActorContext, Depends(authorize("resume.read"))]
    writer = Annotated[ActorContext, Depends(authorize("resume.write"))]
    ai = Annotated[ActorContext, Depends(authorize("documents.ai"))]
    base = "/resumes/{version_id}"

    async def _view(
        actor: ActorContext, version_id: UUID, revision: int | None = None
    ) -> VersionOut:
        try:
            view = await uc.get_version(db, actor, version_id, revision=revision)
        except NotFound as exc:
            raise _not_found() from exc
        return VersionOut.model_validate(view, from_attributes=True)

    @router.get("/resumes", response_model=list[SummaryOut])
    async def list_resumes(actor: reader, application_id: UUID | None = None) -> list[SummaryOut]:
        rows = await uc.list_versions(db, actor, application_id=application_id)
        return [SummaryOut.model_validate(r, from_attributes=True) for r in rows]

    @router.post("/resumes", response_model=CreatedOut, status_code=201)
    async def create(actor: writer, body: CreateIn) -> CreatedOut:
        try:
            new_id = await uc.create_version(db, actor, **body.model_dump())
        except NotFound as exc:
            raise _not_found() from exc
        except ResumeRuleError as exc:
            raise _error(exc) from exc
        return CreatedOut(id=new_id)

    @router.get(base, response_model=VersionOut)
    async def get(actor: reader, version_id: UUID, revision: int | None = None) -> VersionOut:
        return await _view(actor, version_id, revision)

    @router.patch(base, response_model=VersionOut)
    async def patch(actor: writer, version_id: UUID, body: SettingsPatch) -> VersionOut:
        try:
            await uc.update_settings(db, actor, version_id, body.model_dump(exclude_unset=True))
        except NotFound as exc:
            raise _not_found() from exc
        except ResumeRuleError as exc:
            raise _error(exc) from exc
        return await _view(actor, version_id)

    @router.delete(base, status_code=204)
    async def delete(actor: writer, version_id: UUID) -> Response:
        try:
            await uc.delete_version(db, actor, version_id)
        except NotFound as exc:
            raise _not_found() from exc
        except ResumeRuleError as exc:
            raise _error(exc) from exc
        return Response(status_code=204)

    @router.post(base + "/revisions", response_model=RevisionOut)
    async def save(actor: writer, version_id: UUID, body: SaveIn) -> RevisionOut:
        try:
            res = await uc.save(db, actor, version_id, **body.model_dump())
        except NotFound as exc:
            raise _not_found() from exc
        except ResumeRuleError as exc:
            raise _error(exc) from exc
        return RevisionOut.model_validate(res, from_attributes=True)

    @router.get(base + "/revisions", response_model=list[RevisionSummaryOut])
    async def revisions(actor: reader, version_id: UUID) -> list[RevisionSummaryOut]:
        try:
            rows = await uc.list_revisions(db, actor, version_id)
        except NotFound as exc:
            raise _not_found() from exc
        return [RevisionSummaryOut.model_validate(r, from_attributes=True) for r in rows]

    @router.get(base + "/picker")
    async def picker(actor: reader, version_id: UUID) -> dict[str, Any]:
        try:
            return await uc.picker(db, actor, version_id)
        except NotFound as exc:
            raise _not_found() from exc

    @router.get(base + "/check")
    async def check(actor: reader, version_id: UUID, revision: int | None = None) -> dict[str, Any]:
        try:
            return await uc.check_version(db, actor, version_id, revision=revision)
        except NotFound as exc:
            raise _not_found() from exc

    @router.get(base + "/preview")
    async def preview(actor: reader, version_id: UUID, revision: int | None = None) -> Response:
        """미리보기 HTML. 화면은 이 글을 sandbox iframe의 srcdoc으로 띄운다 (인쇄도 그 안에서)."""
        try:
            data, masked = await uc.preview_html(
                db, deps.blobs, actor, version_id, revision=revision
            )
        except NotFound as exc:
            raise _not_found() from exc
        return Response(
            content=data,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store", "X-Resume-Masked": str(masked)},
        )

    async def _export(actor, version_id, fmt, revision):
        try:
            return await uc.export_version(
                db, deps.blobs, actor, version_id, fmt=fmt, revision=revision
            )
        except NotFound as exc:
            raise _not_found() from exc
        except ResumeRuleError as exc:
            raise _error(exc) from exc

    @router.get(base + "/export/check", response_model=ExportCheckOut)
    async def export_check(
        actor: reader, version_id: UUID, fmt: Fmt, revision: int | None = None
    ) -> ExportCheckOut:
        res = await _export(actor, version_id, fmt, revision)
        return ExportCheckOut(
            filename=res.filename,
            media_type=res.media_type,
            format=res.format,
            revision=res.revision,
            bytes=len(res.data),
            findings=res.findings,
            summary=res.summary,
        )

    @router.get(base + "/export")
    async def export(
        actor: reader, version_id: UUID, fmt: Fmt, revision: int | None = None
    ) -> Response:
        res = await _export(actor, version_id, fmt, revision)
        return Response(
            content=res.data,
            media_type=res.media_type,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(res.filename)}",
                "X-Export-Findings": str(len(res.findings)),
                "X-Export-Codes": ",".join(res.summary),
                "Cache-Control": "no-store",
            },
        )

    @router.post(base + "/suggestions", response_model=SuggestOut)
    async def suggest(actor: ai, version_id: UUID, body: SuggestIn, _w: writer) -> SuggestOut:
        if deps.models is None:
            raise HTTPException(status_code=503, detail="models_not_configured")
        try:
            res = await suggest_uc.suggest_lines(
                db,
                deps.models,
                actor,
                version_id,
                base_revision=body.base_revision,
                line_ids=body.line_ids,
                instruction=body.instruction,
            )
        except NotFound as exc:
            raise _not_found() from exc
        except ResumeRuleError as exc:
            raise _error(exc) from exc
        except ModelDenied as exc:
            raise HTTPException(status_code=409, detail=exc.code) from exc
        except ModelCallError as exc:
            raise HTTPException(status_code=502, detail=exc.code) from exc
        return SuggestOut.model_validate(res, from_attributes=True)

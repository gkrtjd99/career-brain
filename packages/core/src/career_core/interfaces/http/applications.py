"""지원·문서·수정안·제출본 경로 (O07).

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from career_core.domain.documents import DocumentRuleError, RevisionConflict
from career_core.domain.runs import ApprovalError
from career_core.interfaces.http.runs import approval_error
from career_core.llm import ModelCallError, ModelDenied
from career_core.ports import ActorContext
from career_core.usecases import applications as uc
from career_core.usecases.brain import NotFound

_RULE_STATUS = {
    "application_locked": status.HTTP_409_CONFLICT,
    "already_submitted": status.HTTP_409_CONFLICT,
    "posting_not_saved": status.HTTP_409_CONFLICT,
    "suggestion_not_open": status.HTTP_409_CONFLICT,
    "suggestion_stale": status.HTTP_409_CONFLICT,
    "invalid_transition": status.HTTP_409_CONFLICT,
    "submit_requires_approve": status.HTTP_403_FORBIDDEN,
    "invalid_model_output": status.HTTP_502_BAD_GATEWAY,
}


def _error(exc: DocumentRuleError) -> HTTPException:
    if isinstance(exc, RevisionConflict):
        detail: Any = {"code": exc.code, "current_revision": exc.current, "stale": exc.stale}
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    return HTTPException(status_code=_RULE_STATUS.get(exc.code, 422), detail=exc.code)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


# ---------------------------------------------------------------- schemas


class ApplicationIn(BaseModel):
    posting_id: UUID
    title: str | None = Field(default=None, max_length=300)


class CreatedOut(BaseModel):
    id: UUID


class ApplicationSummaryOut(BaseModel):
    id: UUID
    posting_id: UUID
    title: str
    status: str
    company: str | None
    job_title: str | None
    deadline: str | None
    documents: int
    submitted_at: datetime | None
    created_at: datetime
    questions: int
    unwritten: int


class DocumentSummaryOut(BaseModel):
    id: UUID
    kind: str
    title: str
    question_index: int | None
    question_prompt: str | None
    char_limit: int | None
    byte_limit: int | None
    current_revision: int
    counts: dict | None


class ApplicationOut(BaseModel):
    id: UUID
    posting_id: UUID
    posting_version_id: UUID | None
    title: str
    status: str
    result_note: str | None
    submitted_at: datetime | None
    posting: dict
    posting_pending_changes: list[str]
    documents: list[DocumentSummaryOut]
    submission_id: UUID | None


class StatusIn(BaseModel):
    status: Literal["passed", "rejected", "withdrawn"]
    note: str | None = Field(default=None, max_length=2000)


class StatusOut(BaseModel):
    id: UUID
    status: str


class DocumentIn(BaseModel):
    kind: str
    title: str = Field(min_length=1, max_length=300)
    question_prompt: str | None = Field(default=None, max_length=2000)
    char_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    byte_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    count_rule: dict[str, str] | None = None


class DocumentPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    char_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    byte_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    count_rule: dict[str, str] | None = None


class BlockOut(BaseModel):
    id: str
    text: str
    origin: str
    claim_ids: list[str]


class DocumentOut(BaseModel):
    id: UUID
    application_id: UUID
    kind: str
    title: str
    question_index: int | None
    question_prompt: str | None
    char_limit: int | None
    byte_limit: int | None
    count_rule: dict
    current_revision: int
    revision_no: int
    revision_id: UUID | None
    blocks: list[BlockOut]
    text: str
    counts: dict
    ai_ratio: float
    editable: bool


class RevisionSummaryOut(BaseModel):
    id: UUID
    revision_no: int
    origin: str
    reason: str | None
    content_hash: str
    counts: dict
    created_by: UUID
    created_at: datetime


class BlockIn(BaseModel):
    id: str | None = Field(default=None, max_length=100)
    text: str = Field(max_length=10_000)
    claim_ids: list[UUID] = Field(default_factory=list, max_length=20)


class OperationIn(BaseModel):
    op: Literal["replace", "insert", "delete", "link", "set_blocks"]
    block_id: str | None = Field(default=None, max_length=100)
    text: str | None = Field(default=None, max_length=10_000)
    index: int | None = Field(default=None, ge=0)
    claim_ids: list[UUID] | None = Field(default=None, max_length=20)
    blocks: list[BlockIn] | None = Field(default=None, max_length=200)


class RevisionIn(BaseModel):
    base_revision: int = Field(ge=0)
    operations: list[OperationIn] = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=1000)
    idempotency_key: str | None = Field(default=None, max_length=200)


class RevisionOut(BaseModel):
    id: UUID
    revision_no: int
    created: bool


class SuggestIn(BaseModel):
    base_revision: int = Field(ge=0)
    block_ids: list[str] | None = Field(default=None, max_length=20)
    instruction: str | None = Field(default=None, max_length=500)


class SuggestionOut(BaseModel):
    id: UUID
    block_id: str
    base_revision_id: UUID
    original_text: str
    suggested_text: str
    reason: str | None
    status: str
    reject_reasons: list[dict]
    applied_revision_id: UUID | None
    created_at: datetime


class SuggestOut(BaseModel):
    suggestions: list[SuggestionOut]
    questions: list[str]
    dropped: int
    model_call_id: UUID | None


class ApplyIn(BaseModel):
    base_revision: int = Field(ge=0)


class CountIn(BaseModel):
    # 편집 중인 문단 글. 저장하지 않고 서버 코드로 센다.
    blocks: list[str] = Field(default_factory=list, max_length=200)


class ValidateIn(BaseModel):
    revision: int | None = Field(default=None, ge=0)
    extra_cliches: list[str] = Field(default_factory=list, max_length=100)


class SubmitIn(BaseModel):
    # 사용자가 확인한 문서별 revision 번호. 최신과 다르면 409.
    revisions: dict[UUID, int]
    note: str | None = Field(default=None, max_length=2000)
    # O08: 사람이 승인한 제출 묶음. 승인 당시와 대상·내용이 다르면 409 (그 승인은 무효가 된다)
    approval_id: UUID | None = None


class SubmissionOut(BaseModel):
    id: UUID
    application_id: UUID
    posting_id: UUID
    posting_version_id: UUID | None
    posting: dict
    documents: list[dict]
    checks: dict
    content_hash: str
    note: str | None
    submitted_by: UUID
    submitted_at: datetime


def _op(o: OperationIn) -> dict:
    d = o.model_dump(exclude_none=True)
    if o.claim_ids is not None:
        d["claim_ids"] = [str(c) for c in o.claim_ids]
    if o.blocks is not None:
        d["blocks"] = [
            {"id": b.id, "text": b.text, "claim_ids": [str(c) for c in b.claim_ids]}
            for b in o.blocks
        ]
    return d


def add_application_routes(router: APIRouter, deps, authorize) -> None:
    db = deps.db
    reader = Annotated[ActorContext, Depends(authorize("applications.read"))]
    writer = Annotated[ActorContext, Depends(authorize("applications.write"))]
    ai = Annotated[ActorContext, Depends(authorize("documents.ai"))]
    base = "/applications/{application_id}"
    doc_path = base + "/documents/{document_id}"

    @router.post("/applications", response_model=CreatedOut, status_code=201)
    async def create(actor: writer, body: ApplicationIn) -> CreatedOut:
        try:
            aid = await uc.create_application(db, actor, body.posting_id, title=body.title)
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        return CreatedOut(id=aid)

    @router.get("/applications", response_model=list[ApplicationSummaryOut])
    async def index(actor: reader) -> list[ApplicationSummaryOut]:
        return [
            ApplicationSummaryOut.model_validate(a, from_attributes=True)
            for a in await uc.list_applications(db, actor)
        ]

    @router.get(base, response_model=ApplicationOut)
    async def detail(actor: reader, application_id: UUID) -> ApplicationOut:
        try:
            view = await uc.get_application(db, actor, application_id)
        except NotFound as exc:
            raise _not_found() from exc
        return ApplicationOut.model_validate(view, from_attributes=True)

    @router.post(base + "/status", response_model=StatusOut)
    async def set_status(actor: writer, application_id: UUID, body: StatusIn) -> StatusOut:
        try:
            st = await uc.set_status(db, actor, application_id, body.status, note=body.note)
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        return StatusOut(id=application_id, status=st)

    @router.post(base + "/documents", response_model=CreatedOut, status_code=201)
    async def add_document(actor: writer, application_id: UUID, body: DocumentIn) -> CreatedOut:
        try:
            did = await uc.add_document(db, actor, application_id, **body.model_dump())
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        return CreatedOut(id=did)

    @router.patch(doc_path, response_model=DocumentOut)
    async def patch_document(
        actor: writer, application_id: UUID, document_id: UUID, body: DocumentPatch
    ) -> DocumentOut:
        try:
            await uc.update_document(
                db, actor, application_id, document_id, body.model_dump(exclude_unset=True)
            )
            view = await uc.get_document(db, actor, application_id, document_id)
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        return DocumentOut.model_validate(view, from_attributes=True)

    @router.get(doc_path, response_model=DocumentOut)
    async def get_document(
        actor: reader, application_id: UUID, document_id: UUID, revision: int | None = None
    ) -> DocumentOut:
        try:
            view = await uc.get_document(db, actor, application_id, document_id, revision=revision)
        except NotFound as exc:
            raise _not_found() from exc
        return DocumentOut.model_validate(view, from_attributes=True)

    @router.get(doc_path + "/revisions", response_model=list[RevisionSummaryOut])
    async def revisions(
        actor: reader, application_id: UUID, document_id: UUID
    ) -> list[RevisionSummaryOut]:
        try:
            revs = await uc.list_revisions(db, actor, application_id, document_id)
        except NotFound as exc:
            raise _not_found() from exc
        return [RevisionSummaryOut.model_validate(r, from_attributes=True) for r in revs]

    @router.post(doc_path + "/revisions", response_model=RevisionOut, status_code=201)
    async def save(
        actor: writer, application_id: UUID, document_id: UUID, body: RevisionIn
    ) -> RevisionOut:
        try:
            res = await uc.save_revision(
                db,
                actor,
                application_id,
                document_id,
                base_revision=body.base_revision,
                operations=[_op(o) for o in body.operations],
                reason=body.reason,
                idempotency_key=body.idempotency_key,
            )
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        return RevisionOut(id=res.id, revision_no=res.revision_no, created=res.created)

    @router.post(doc_path + "/count")
    async def count(actor: reader, application_id: UUID, document_id: UUID, body: CountIn) -> dict:
        if sum(len(b) for b in body.blocks) > 60_000:
            raise HTTPException(status_code=422, detail="document_too_long")
        try:
            return await uc.count_draft(db, actor, application_id, document_id, body.blocks)
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc

    @router.post(doc_path + "/validate")
    async def validate(
        actor: reader, application_id: UUID, document_id: UUID, body: ValidateIn
    ) -> dict:
        try:
            return await uc.validate_document(
                db,
                actor,
                application_id,
                document_id,
                revision=body.revision,
                extra_cliches=body.extra_cliches,
            )
        except NotFound as exc:
            raise _not_found() from exc

    @router.post(doc_path + "/suggestions", response_model=SuggestOut, status_code=201)
    async def suggest(
        actor: ai, application_id: UUID, document_id: UUID, body: SuggestIn
    ) -> SuggestOut:
        if deps.models is None:
            raise HTTPException(status_code=503, detail="models_not_configured")
        try:
            res = await uc.suggest_edits(
                db,
                deps.models,
                actor,
                application_id,
                document_id,
                base_revision=body.base_revision,
                block_ids=body.block_ids,
                instruction=body.instruction,
            )
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        except ModelDenied as exc:
            raise HTTPException(status_code=409, detail=exc.code) from exc
        except ModelCallError as exc:
            raise HTTPException(status_code=502, detail=exc.code) from exc
        return SuggestOut.model_validate(res, from_attributes=True)

    @router.get(doc_path + "/suggestions", response_model=list[SuggestionOut])
    async def suggestions(
        actor: reader, application_id: UUID, document_id: UUID
    ) -> list[SuggestionOut]:
        try:
            rows = await uc.list_suggestions(db, actor, application_id, document_id)
        except NotFound as exc:
            raise _not_found() from exc
        return [SuggestionOut.model_validate(r, from_attributes=True) for r in rows]

    @router.post(
        doc_path + "/suggestions/{suggestion_id}/apply", response_model=RevisionOut, status_code=201
    )
    async def apply(
        actor: writer, application_id: UUID, document_id: UUID, suggestion_id: UUID, body: ApplyIn
    ) -> RevisionOut:
        try:
            res = await uc.apply_suggestion(
                db,
                actor,
                application_id,
                document_id,
                suggestion_id,
                base_revision=body.base_revision,
            )
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        return RevisionOut(id=res.id, revision_no=res.revision_no, created=res.created)

    @router.post(doc_path + "/suggestions/{suggestion_id}/dismiss", response_model=StatusOut)
    async def dismiss(
        actor: writer, application_id: UUID, document_id: UUID, suggestion_id: UUID
    ) -> StatusOut:
        try:
            st = await uc.dismiss_suggestion(db, actor, application_id, document_id, suggestion_id)
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        return StatusOut(id=suggestion_id, status=st)

    @router.get(base + "/checks")
    async def checks(actor: reader, application_id: UUID) -> dict:
        try:
            return await uc.application_checks(db, actor, application_id)
        except NotFound as exc:
            raise _not_found() from exc

    @router.post(base + "/submit", response_model=SubmissionOut, status_code=201)
    async def submit(actor: writer, application_id: UUID, body: SubmitIn) -> SubmissionOut:
        # 승인 묶음 없이 제출하면 제출자가 승인 행위를 한다 (applications.submit = approve, 사람만).
        # 승인 묶음(O08)이 있으면 사람이 미리 승인했으므로 편집 권한으로 실행한다.
        action = (
            "applications.submit" if body.approval_id is None else "applications.submit_approved"
        )
        if not deps.permissions.allows(actor, action):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
        try:
            view = await uc.submit_application(
                db,
                actor,
                application_id,
                revisions=body.revisions,
                note=body.note,
                approval_id=body.approval_id,
            )
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        except ApprovalError as exc:
            raise approval_error(exc) from exc
        return SubmissionOut.model_validate(view, from_attributes=True)

    @router.get(base + "/submission", response_model=SubmissionOut)
    async def submission(actor: reader, application_id: UUID) -> SubmissionOut:
        try:
            view = await uc.get_submission(db, actor, application_id)
        except NotFound as exc:
            raise _not_found() from exc
        return SubmissionOut.model_validate(view, from_attributes=True)

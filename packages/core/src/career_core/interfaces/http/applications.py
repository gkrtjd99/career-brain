"""지원·문서·수정안·제출본 경로 (O07) + 공용 문서·문서 설정·내보내기·전형 단계 (c0012).

문서 경로(본문·revision·검사·수정안·내보내기)는 두 곳에 같은 모양으로 붙는다.

- 지원 문서: ``/applications/{application_id}/documents/{document_id}/...``
- 공용 문서: ``/documents/{document_id}/...``

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from career_core.domain.documents import DocumentRuleError, RevisionConflict
from career_core.domain.runs import ApprovalError
from career_core.interfaces.http.runs import approval_error
from career_core.llm import ModelCallError, ModelDenied
from career_core.ports import ActorContext
from career_core.usecases import application_stages as stages_uc
from career_core.usecases import applications as uc
from career_core.usecases import documents as docs_uc
from career_core.usecases.brain import NotFound

_RULE_STATUS = {
    "application_locked": status.HTTP_409_CONFLICT,
    "already_submitted": status.HTTP_409_CONFLICT,
    "posting_not_saved": status.HTTP_409_CONFLICT,
    "suggestion_not_open": status.HTTP_409_CONFLICT,
    "suggestion_stale": status.HTTP_409_CONFLICT,
    "invalid_transition": status.HTTP_409_CONFLICT,
    "not_submitted": status.HTTP_409_CONFLICT,
    "submit_requires_approve": status.HTTP_403_FORBIDDEN,
    "invalid_model_output": status.HTTP_502_BAD_GATEWAY,
    "models_not_configured": status.HTTP_503_SERVICE_UNAVAILABLE,
}


def _error(exc: DocumentRuleError) -> HTTPException:
    if isinstance(exc, RevisionConflict):
        detail: Any = {"code": exc.code, "current_revision": exc.current, "stale": exc.stale}
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    if isinstance(exc, uc.SensitiveUnacknowledged):
        detail = {
            "code": exc.code,
            "sensitive_hash": exc.sensitive_hash,
            "findings": exc.findings,
        }
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
    # 지금 전형 단계 {kind, name, outcome, scheduled_at} (단계가 없으면 null)
    stage: dict | None = None


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
    source: dict | None = None


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


class SharedDocumentIn(BaseModel):
    kind: Literal["resume", "cover_letter", "portfolio", "other"]
    title: str = Field(min_length=1, max_length=300)
    char_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    byte_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    count_rule: dict[str, str] | None = None


class SharedDocumentOut(BaseModel):
    id: UUID
    kind: str
    title: str
    char_limit: int | None
    byte_limit: int | None
    current_revision: int
    counts: dict | None
    copies: int
    created_at: datetime
    updated_at: datetime


class CopyIn(BaseModel):
    source_document_id: UUID
    # 없으면 원본의 최신 revision
    revision: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, max_length=300)


class DocumentPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    char_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    byte_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    count_rule: dict[str, str] | None = None


class SpanOut(BaseModel):
    start: int
    end: int
    claim_ids: list[str]


class BlockOut(BaseModel):
    id: str
    text: str
    origin: str
    claim_ids: list[str]
    # 문장 단위 근거. 비어 있으면 문단 단위
    spans: list[SpanOut] = Field(default_factory=list)


class DocumentOut(BaseModel):
    id: UUID
    application_id: UUID | None
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
    source: dict | None = None


class RevisionSummaryOut(BaseModel):
    id: UUID
    revision_no: int
    origin: str
    reason: str | None
    content_hash: str
    counts: dict
    created_by: UUID
    created_at: datetime


class SpanIn(BaseModel):
    # 블록 글 안의 위치 [start, end) — 코드 포인트, 저장할 때와 같은 NFC 기준
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    claim_ids: list[UUID] = Field(min_length=1, max_length=20)


class BlockIn(BaseModel):
    id: str | None = Field(default=None, max_length=100)
    text: str = Field(max_length=10_000)
    claim_ids: list[UUID] = Field(default_factory=list, max_length=20)
    # 없으면(null) 기존 span을 새 글에서 다시 찾는다. []이면 문장 단위 근거를 지운다.
    spans: list[SpanIn] | None = Field(default=None, max_length=50)


class OperationIn(BaseModel):
    op: Literal["replace", "insert", "delete", "link", "link_spans", "set_blocks"]
    block_id: str | None = Field(default=None, max_length=100)
    text: str | None = Field(default=None, max_length=10_000)
    index: int | None = Field(default=None, ge=0)
    claim_ids: list[UUID] | None = Field(default=None, max_length=20)
    spans: list[SpanIn] | None = Field(default=None, max_length=50)
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
    # 같은 키로 다시 보내면 모델을 부르지 않고 처음 만든 수정안을 돌려준다
    idempotency_key: str | None = Field(default=None, max_length=200)


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
    replayed: bool = False
    request_id: UUID | None = None


class SuggestionRequestOut(BaseModel):
    """수정안 요청 1회 기록 (c0016). 질문은 모델이 사실 대신 물어본 것 — 근거가 아니다."""

    id: UUID
    document_id: UUID
    block_ids: list[str]
    questions: list[str]
    proposed: int
    rejected: int
    dropped: int
    run_id: UUID | None
    model_call_id: UUID | None
    created_at: datetime


class ApplyIn(BaseModel):
    base_revision: int = Field(ge=0)


class CountIn(BaseModel):
    # 편집 중인 문단 글. 저장하지 않고 서버 코드로 센다.
    blocks: list[str] = Field(default_factory=list, max_length=200)


class ValidateIn(BaseModel):
    revision: int | None = Field(default=None, ge=0)
    # 공간 설정의 상투어 목록(/document-settings)에 이 요청에서만 더한다
    extra_cliches: list[str] = Field(default_factory=list, max_length=100)


class SubmitIn(BaseModel):
    # 사용자가 확인한 문서별 revision 번호. 최신과 다르면 409.
    revisions: dict[UUID, int]
    note: str | None = Field(default=None, max_length=2000)
    # O08: 사람이 승인한 제출 묶음. 승인 당시와 대상·내용이 다르면 409 (그 승인은 무효가 된다)
    approval_id: UUID | None = None
    # c0012: 제출 전 민감 정보 경고를 확인했다는 표시 (GET .../sensitive-check의 sensitive_hash)
    sensitive_ack: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


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


class ContactsIn(BaseModel):
    emails: list[str] = Field(default_factory=list, max_length=10)
    phones: list[str] = Field(default_factory=list, max_length=10)


class SettingsIn(BaseModel):
    # 전체 교체. null이면 기본 목록으로 되돌린다. 보내지 않으면 그대로
    cliches: list[str] | None = Field(default=None, max_length=300)
    own_contacts: ContactsIn | None = None


class SettingsOut(BaseModel):
    cliches: list[str]
    customized: bool
    default_cliches: list[str]
    own_contacts: dict[str, list[str]]
    updated_at: datetime | None


class ExportCheckOut(BaseModel):
    filename: str
    media_type: str
    format: str
    revision: int
    bytes: int
    findings: list[dict]
    summary: dict[str, int]


class StageIn(BaseModel):
    kind: Literal["document", "test", "assignment", "interview", "final", "other"]
    name: str | None = Field(default=None, max_length=200)
    scheduled_at: datetime | None = None
    outcome: Literal["pending", "passed", "failed", "withdrawn", "cancelled"] = "pending"
    note: str | None = Field(default=None, max_length=2000)


class StagePatch(BaseModel):
    kind: Literal["document", "test", "assignment", "interview", "final", "other"] | None = None
    name: str | None = Field(default=None, max_length=200)
    scheduled_at: datetime | None = None
    outcome: Literal["pending", "passed", "failed", "withdrawn", "cancelled"] | None = None
    note: str | None = Field(default=None, max_length=2000)
    position: int | None = Field(default=None, ge=0, le=1000)


class StageOut(BaseModel):
    id: UUID
    application_id: UUID
    submission_id: UUID
    position: int
    kind: str
    name: str
    scheduled_at: datetime | None
    outcome: str
    decided_at: datetime | None
    note: str | None
    created_at: datetime
    updated_at: datetime


def _spans(spans: list[SpanIn] | None) -> list[dict] | None:
    if spans is None:
        return None
    return [
        {"start": sp.start, "end": sp.end, "claim_ids": [str(c) for c in sp.claim_ids]}
        for sp in spans
    ]


def _op(o: OperationIn) -> dict:
    d = o.model_dump(exclude_none=True)
    if o.claim_ids is not None:
        d["claim_ids"] = [str(c) for c in o.claim_ids]
    if o.spans is not None:
        d["spans"] = _spans(o.spans)
    if o.blocks is not None:
        d["blocks"] = [
            {
                "id": b.id,
                "text": b.text,
                "claim_ids": [str(c) for c in b.claim_ids],
                "spans": _spans(b.spans),
            }
            for b in o.blocks
        ]
    return d


# ---------------------------------------------------------------- 문서 경로 (지원·공용 공통)


def _app_scope(application_id: UUID) -> UUID:
    return application_id


def _shared_scope() -> None:
    return None


def _add_document_routes(router: APIRouter, deps, authorize, doc_path: str, scope_dep) -> None:
    """문서 하나에 붙는 경로. scope는 지원 ID(지원 문서) 또는 None(공용 문서)."""
    db = deps.db
    reader = Annotated[ActorContext, Depends(authorize("applications.read"))]
    writer = Annotated[ActorContext, Depends(authorize("applications.write"))]
    ai = Annotated[ActorContext, Depends(authorize("documents.ai"))]
    Scope = Annotated[UUID | None, Depends(scope_dep)]

    @router.patch(doc_path, response_model=DocumentOut)
    async def patch_document(
        actor: writer, scope: Scope, document_id: UUID, body: DocumentPatch
    ) -> DocumentOut:
        try:
            await uc.update_document(
                db, actor, scope, document_id, body.model_dump(exclude_unset=True)
            )
            view = await uc.get_document(db, actor, scope, document_id)
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        return DocumentOut.model_validate(view, from_attributes=True)

    @router.get(doc_path, response_model=DocumentOut)
    async def get_document(
        actor: reader, scope: Scope, document_id: UUID, revision: int | None = None
    ) -> DocumentOut:
        try:
            view = await uc.get_document(db, actor, scope, document_id, revision=revision)
        except NotFound as exc:
            raise _not_found() from exc
        return DocumentOut.model_validate(view, from_attributes=True)

    @router.get(doc_path + "/revisions", response_model=list[RevisionSummaryOut])
    async def revisions(actor: reader, scope: Scope, document_id: UUID) -> list[RevisionSummaryOut]:
        try:
            revs = await uc.list_revisions(db, actor, scope, document_id)
        except NotFound as exc:
            raise _not_found() from exc
        return [RevisionSummaryOut.model_validate(r, from_attributes=True) for r in revs]

    @router.post(doc_path + "/revisions", response_model=RevisionOut, status_code=201)
    async def save(actor: writer, scope: Scope, document_id: UUID, body: RevisionIn) -> RevisionOut:
        try:
            res = await uc.save_revision(
                db,
                actor,
                scope,
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
    async def count(actor: reader, scope: Scope, document_id: UUID, body: CountIn) -> dict:
        if sum(len(b) for b in body.blocks) > 60_000:
            raise HTTPException(status_code=422, detail="document_too_long")
        try:
            return await uc.count_draft(db, actor, scope, document_id, body.blocks)
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc

    @router.post(doc_path + "/validate")
    async def validate(actor: reader, scope: Scope, document_id: UUID, body: ValidateIn) -> dict:
        try:
            return await uc.validate_document(
                db,
                actor,
                scope,
                document_id,
                revision=body.revision,
                extra_cliches=body.extra_cliches,
            )
        except NotFound as exc:
            raise _not_found() from exc

    @router.post(doc_path + "/suggestions", response_model=SuggestOut, status_code=201)
    async def suggest(actor: ai, scope: Scope, document_id: UUID, body: SuggestIn) -> SuggestOut:
        if deps.models is None:
            raise HTTPException(status_code=503, detail="models_not_configured")
        try:
            res = await uc.suggest_edits(
                db,
                deps.models,
                actor,
                scope,
                document_id,
                base_revision=body.base_revision,
                block_ids=body.block_ids,
                instruction=body.instruction,
                idempotency_key=body.idempotency_key,
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
    async def suggestions(actor: reader, scope: Scope, document_id: UUID) -> list[SuggestionOut]:
        try:
            rows = await uc.list_suggestions(db, actor, scope, document_id)
        except NotFound as exc:
            raise _not_found() from exc
        return [SuggestionOut.model_validate(r, from_attributes=True) for r in rows]

    @router.get(doc_path + "/suggestion-requests", response_model=list[SuggestionRequestOut])
    async def suggestion_requests(
        actor: reader,
        scope: Scope,
        document_id: UUID,
        run_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> list[SuggestionRequestOut]:
        """수정안 요청 기록(최근 것부터): 모델이 되물은 질문·개수. ``run_id``로 일괄 실행 것만."""
        try:
            rows = await uc.list_suggestion_requests(
                db, actor, scope, document_id, run_id=run_id, limit=limit
            )
        except NotFound as exc:
            raise _not_found() from exc
        return [SuggestionRequestOut.model_validate(r, from_attributes=True) for r in rows]

    @router.post(
        doc_path + "/suggestions/{suggestion_id}/apply", response_model=RevisionOut, status_code=201
    )
    async def apply(
        actor: writer, scope: Scope, document_id: UUID, suggestion_id: UUID, body: ApplyIn
    ) -> RevisionOut:
        try:
            res = await uc.apply_suggestion(
                db, actor, scope, document_id, suggestion_id, base_revision=body.base_revision
            )
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        return RevisionOut(id=res.id, revision_no=res.revision_no, created=res.created)

    @router.post(doc_path + "/suggestions/{suggestion_id}/dismiss", response_model=StatusOut)
    async def dismiss(
        actor: writer, scope: Scope, document_id: UUID, suggestion_id: UUID
    ) -> StatusOut:
        try:
            st = await uc.dismiss_suggestion(db, actor, scope, document_id, suggestion_id)
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        return StatusOut(id=suggestion_id, status=st)

    Fmt = Annotated[Literal["md", "docx", "html"], Query(alias="format")]

    async def _export(actor, scope, document_id, fmt, revision) -> docs_uc.ExportResult:
        try:
            return await docs_uc.export_document(
                db, actor, scope, document_id, fmt=fmt, revision=revision
            )
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc

    @router.get(doc_path + "/export/check", response_model=ExportCheckOut)
    async def export_check(
        actor: reader, scope: Scope, document_id: UUID, fmt: Fmt, revision: int | None = None
    ) -> ExportCheckOut:
        """내보낼 파일을 만들어 검사만 한다 (파일은 돌려주지 않는다). 화면은 경고를 보이고 확인받은 뒤 받는다."""
        res = await _export(actor, scope, document_id, fmt, revision)
        return ExportCheckOut(
            filename=res.filename,
            media_type=res.media_type,
            format=res.format,
            revision=res.revision,
            bytes=len(res.data),
            findings=res.findings,
            summary=res.summary,
        )

    @router.get(doc_path + "/export")
    async def export(
        actor: reader, scope: Scope, document_id: UUID, fmt: Fmt, revision: int | None = None
    ) -> Response:
        """파일. 검사 경고 수·코드는 헤더(X-Export-Findings, X-Export-Codes)에 같이 싣는다."""
        res = await _export(actor, scope, document_id, fmt, revision)
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


# ---------------------------------------------------------------- 등록


def add_application_routes(router: APIRouter, deps, authorize) -> None:
    db = deps.db
    reader = Annotated[ActorContext, Depends(authorize("applications.read"))]
    writer = Annotated[ActorContext, Depends(authorize("applications.write"))]
    base = "/applications/{application_id}"

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

    @router.post(base + "/documents/copy", response_model=CreatedOut, status_code=201)
    async def copy_document(actor: writer, application_id: UUID, body: CopyIn) -> CreatedOut:
        """공용 문서의 revision을 이 지원의 새 문서로 복사한다 (출처를 기억한다)."""
        try:
            did = await docs_uc.copy_to_application(
                db,
                actor,
                application_id,
                body.source_document_id,
                revision=body.revision,
                title=body.title,
            )
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        return CreatedOut(id=did)

    _add_document_routes(router, deps, authorize, base + "/documents/{document_id}", _app_scope)

    @router.get(base + "/checks")
    async def checks(actor: reader, application_id: UUID) -> dict:
        try:
            return await uc.application_checks(db, actor, application_id)
        except NotFound as exc:
            raise _not_found() from exc

    @router.get(base + "/sensitive-check")
    async def sensitive_check(actor: reader, application_id: UUID) -> dict:
        """제출 전 재검사: 주민등록번호·본인 것이 아닌 전화·이메일·대외비 표시. 지우지 않고 경고만."""
        try:
            return await uc.sensitive_check(db, actor, application_id)
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
                sensitive_ack=body.sensitive_ack,
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

    # ---- 제출본 내보내기 (c0016): 불변 스냅샷을 문서 내보내기와 같은 writer·검사로

    SubmissionFmt = Annotated[Literal["md", "docx", "html"], Query(alias="format")]

    async def _export_submission(actor, application_id, fmt) -> docs_uc.ExportResult:
        try:
            return await docs_uc.export_submission(db, actor, application_id, fmt=fmt)
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc

    @router.get(base + "/submission/export/check", response_model=ExportCheckOut)
    async def submission_export_check(
        actor: reader, application_id: UUID, fmt: SubmissionFmt
    ) -> ExportCheckOut:
        res = await _export_submission(actor, application_id, fmt)
        return ExportCheckOut(
            filename=res.filename,
            media_type=res.media_type,
            format=res.format,
            revision=res.revision,
            bytes=len(res.data),
            findings=res.findings,
            summary=res.summary,
        )

    @router.get(base + "/submission/export")
    async def submission_export(
        actor: reader, application_id: UUID, fmt: SubmissionFmt
    ) -> Response:
        res = await _export_submission(actor, application_id, fmt)
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

    # ---- 전형 단계 (제출본에 묶인 일정·결과. 원인은 기록하지 않고 판단하지 않는다)

    @router.get(base + "/stages", response_model=list[StageOut])
    async def stages(actor: reader, application_id: UUID) -> list[StageOut]:
        try:
            rows = await stages_uc.list_stages(db, actor, application_id)
        except NotFound as exc:
            raise _not_found() from exc
        return [StageOut.model_validate(r, from_attributes=True) for r in rows]

    @router.post(base + "/stages", response_model=StageOut, status_code=201)
    async def add_stage(actor: writer, application_id: UUID, body: StageIn) -> StageOut:
        try:
            view = await stages_uc.add_stage(db, actor, application_id, **body.model_dump())
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        return StageOut.model_validate(view, from_attributes=True)

    @router.patch(base + "/stages/{stage_id}", response_model=StageOut)
    async def patch_stage(
        actor: writer, application_id: UUID, stage_id: UUID, body: StagePatch
    ) -> StageOut:
        try:
            view = await stages_uc.update_stage(
                db, actor, application_id, stage_id, body.model_dump(exclude_unset=True)
            )
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        return StageOut.model_validate(view, from_attributes=True)

    @router.delete(base + "/stages/{stage_id}", status_code=204)
    async def delete_stage(actor: writer, application_id: UUID, stage_id: UUID) -> Response:
        try:
            await stages_uc.delete_stage(db, actor, application_id, stage_id)
        except NotFound as exc:
            raise _not_found() from exc
        return Response(status_code=204)

    # ---- 공용 문서·문서 설정

    @router.post("/documents", response_model=CreatedOut, status_code=201)
    async def create_shared(actor: writer, body: SharedDocumentIn) -> CreatedOut:
        try:
            did = await docs_uc.create_shared_document(db, actor, **body.model_dump())
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        return CreatedOut(id=did)

    @router.get("/documents", response_model=list[SharedDocumentOut])
    async def list_shared(actor: reader) -> list[SharedDocumentOut]:
        return [
            SharedDocumentOut.model_validate(d, from_attributes=True)
            for d in await docs_uc.list_shared_documents(db, actor)
        ]

    _add_document_routes(router, deps, authorize, "/documents/{document_id}", _shared_scope)

    @router.get("/document-settings", response_model=SettingsOut)
    async def get_settings(actor: reader) -> SettingsOut:
        return SettingsOut.model_validate(
            await docs_uc.get_settings(db, actor), from_attributes=True
        )

    @router.put("/document-settings", response_model=SettingsOut)
    async def put_settings(actor: writer, body: SettingsIn) -> SettingsOut:
        changes: dict[str, Any] = {}
        if "cliches" in body.model_fields_set:
            changes["cliches"] = body.cliches
        if "own_contacts" in body.model_fields_set:
            changes["own_contacts"] = (
                body.own_contacts.model_dump() if body.own_contacts is not None else None
            )
        try:
            view = await docs_uc.update_settings(db, actor, **changes)
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        return SettingsOut.model_validate(view, from_attributes=True)

    # c0016 올린 DOCX·PDF 숨김 글 검사 (경고만)
    from career_core.interfaces.http.file_checks import add_file_check_routes

    add_file_check_routes(router, deps, authorize)

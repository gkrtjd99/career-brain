"""학습·시험·오답·복습 경로 (O09).

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from career_core.domain.study import StudyRuleError
from career_core.ports import ActorContext
from career_core.usecases import study as uc
from career_core.usecases.brain import NotFound

_RULE_STATUS = {
    "posting_not_saved": status.HTTP_409_CONFLICT,
    "mistake_resolved": status.HTTP_409_CONFLICT,
    "no_study_result": status.HTTP_409_CONFLICT,
}


def _rule(exc: StudyRuleError) -> HTTPException:
    return HTTPException(status_code=_RULE_STATUS.get(exc.code, 422), detail=exc.code)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


# ---------------------------------------------------------------- schemas


class RequirementRefOut(BaseModel):
    id: UUID
    posting_id: UUID
    field: str
    text: str
    posting_title: str | None


class ItemIn(BaseModel):
    title: str | None = Field(default=None, max_length=uc.MAX_TITLE)
    kind: str = "skill"
    notes: str | None = Field(default=None, max_length=uc.MAX_TEXT)
    # 공고 요건 한 줄에서 만들 때: 저장 공고 ID + 항목(requirements/preferred/process) + 그 줄
    posting_id: UUID | None = None
    field: str | None = None
    text: str | None = Field(default=None, max_length=uc.MAX_TEXT)
    assessment_id: UUID | None = None


class ItemPatch(BaseModel):
    title: str | None = Field(default=None, max_length=uc.MAX_TITLE)
    kind: str | None = None
    status: str | None = None
    notes: str | None = Field(default=None, max_length=uc.MAX_TEXT)


class ItemSummaryOut(BaseModel):
    id: UUID
    title: str
    kind: str
    status: str
    requirement: RequirementRefOut | None
    assessment_id: UUID | None
    total_minutes: int
    open_mistakes: int
    completed_at: datetime | None
    updated_at: datetime


class ResourceIn(BaseModel):
    title: str = Field(min_length=1, max_length=uc.MAX_TITLE)
    url: str | None = Field(default=None, max_length=2000)


class SessionIn(BaseModel):
    minutes: int = Field(gt=0, le=1440)
    started_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=uc.MAX_TEXT)
    resources: list[ResourceIn] = Field(default_factory=list, max_length=uc.MAX_RESOURCES)


class SessionOut(BaseModel):
    id: UUID
    started_at: datetime
    minutes: int
    notes: str | None
    resources: list[dict]


class AttemptIn(BaseModel):
    result: str
    assessment_id: UUID | None = None
    study_item_id: UUID | None = None
    score: Decimal | None = None
    max_score: Decimal | None = None
    taken_at: datetime | None = None
    minutes: int | None = Field(default=None, gt=0, le=1440)
    notes: str | None = Field(default=None, max_length=uc.MAX_TEXT)


class AttemptOut(BaseModel):
    id: UUID
    assessment_id: UUID | None
    study_item_id: UUID | None
    taken_at: datetime
    result: str
    score: Decimal | None
    max_score: Decimal | None
    minutes: int | None
    notes: str | None


class MistakeIn(BaseModel):
    question: str = Field(min_length=1, max_length=uc.MAX_TEXT)
    reason: str = "unknown"
    attempt_id: UUID | None = None
    study_item_id: UUID | None = None
    wrong_answer: str | None = Field(default=None, max_length=uc.MAX_TEXT)
    correct_answer: str | None = Field(default=None, max_length=uc.MAX_TEXT)
    reason_note: str | None = Field(default=None, max_length=uc.MAX_TEXT)


class MistakePatch(BaseModel):
    status: str


class MistakeOut(BaseModel):
    id: UUID
    attempt_id: UUID | None
    study_item_id: UUID | None
    question: str
    wrong_answer: str | None
    correct_answer: str | None
    reason: str
    reason_note: str | None
    status: str
    repetitions: int
    interval_days: int
    due_at: datetime
    last_reviewed_at: datetime | None


class CandidateOut(BaseModel):
    claim_id: UUID
    attempt_id: UUID | None
    statement: str
    status: str


class ItemOut(BaseModel):
    id: UUID
    title: str
    kind: str
    status: str
    notes: str | None
    requirement: RequirementRefOut | None
    assessment_id: UUID | None
    completed_at: datetime | None
    total_minutes: int
    sessions: list[SessionOut]
    attempts: list[AttemptOut]
    mistakes: list[MistakeOut]
    claim_candidates: list[CandidateOut]


class AssessmentIn(BaseModel):
    title: str = Field(min_length=1, max_length=uc.MAX_TITLE)
    kind: str
    scheduled_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=uc.MAX_TEXT)
    posting_id: UUID | None = None
    process_text: str | None = Field(default=None, max_length=uc.MAX_TEXT)


class AssessmentPatch(BaseModel):
    title: str | None = Field(default=None, max_length=uc.MAX_TITLE)
    status: str | None = None
    scheduled_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=uc.MAX_TEXT)


class AssessmentOut(BaseModel):
    id: UUID
    title: str
    kind: str
    status: str
    scheduled_at: datetime | None
    notes: str | None
    requirement: RequirementRefOut | None
    attempts: list[AttemptOut]
    items: list[UUID]
    mistakes: list[MistakeOut]
    last_attempt: AttemptOut | None


class ReviewIn(BaseModel):
    grade: str


class ReviewOut(BaseModel):
    mistake_id: UUID
    grade: str
    repetitions: int
    interval_days: int
    ease: int
    due_at: datetime


class DueOut(BaseModel):
    id: UUID
    question: str
    wrong_answer: str | None
    correct_answer: str | None
    reason: str
    reason_note: str | None
    due_at: datetime
    repetitions: int
    interval_days: int
    study_item_id: UUID | None
    study_item_title: str | None
    attempt_id: UUID | None


class MistakeListOut(BaseModel):
    id: UUID
    question: str
    wrong_answer: str | None
    correct_answer: str | None
    reason: str
    reason_note: str | None
    status: str
    due_at: datetime
    repetitions: int
    interval_days: int
    last_reviewed_at: datetime | None
    study_item_id: UUID | None
    study_item_title: str | None
    attempt_id: UUID | None
    created_at: datetime


class LineIn(BaseModel):
    field: str
    text: str = Field(min_length=1, max_length=uc.MAX_TEXT)


class PlanIn(BaseModel):
    # 비우면 기본 규칙(확정 주장·진행 중 학습이 없는 요건 + 시험 단계)으로 만든다.
    lines: list[LineIn] | None = Field(default=None, max_length=200)
    include_process: bool | None = None


class PlanOut(BaseModel):
    items_created: list[UUID]
    assessments_created: list[UUID]
    skipped: int


class LinkIn(BaseModel):
    field: str
    text: str = Field(min_length=1, max_length=uc.MAX_TEXT)
    claim_id: UUID


class LinkOut(BaseModel):
    requirement_id: UUID
    claim_id: UUID


class CandidateIn(BaseModel):
    experience_id: UUID
    statement: str = Field(min_length=1, max_length=uc.MAX_TEXT)
    kind: str = "skill"
    attempt_id: UUID | None = None


class CandidateCreatedOut(BaseModel):
    claim_id: UUID
    status: str


class LinkedClaimOut(BaseModel):
    id: UUID
    statement: str
    status: str


class LinkedItemOut(BaseModel):
    id: UUID
    title: str
    status: str
    last_attempt: AttemptOut | None


class LinkedAssessmentOut(BaseModel):
    id: UUID
    title: str
    kind: str
    status: str
    scheduled_at: datetime | None
    last_attempt: AttemptOut | None


class StepOut(BaseModel):
    step: str
    kind: str | None


class RequirementCoverageOut(BaseModel):
    field: str
    text: str
    requirement_id: UUID | None
    current: bool
    coverage: str
    claims: list[LinkedClaimOut]
    items: list[LinkedItemOut]
    assessments: list[LinkedAssessmentOut]
    steps: list[StepOut]


class CoverageOut(BaseModel):
    posting_id: UUID
    title: str | None
    company: str | None
    status: str
    requirements: list[RequirementCoverageOut]
    counts: dict[str, int]
    basis: str


class IdOut(BaseModel):
    id: UUID


class StatusOut(BaseModel):
    id: UUID
    status: str


def _out(model: type[BaseModel], value) -> BaseModel:
    return model.model_validate(value, from_attributes=True)


def add_study_routes(router: APIRouter, deps, authorize) -> None:
    db = deps.db
    reader = Annotated[ActorContext, Depends(authorize("study.read"))]
    writer = Annotated[ActorContext, Depends(authorize("study.write"))]

    async def call(fn, *args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except NotFound as exc:
            raise _not_found() from exc
        except StudyRuleError as exc:
            raise _rule(exc) from exc

    # --- 공고 → 준비 항목, 요건 충족 보기

    @router.get("/study/postings/{posting_id}/coverage", response_model=CoverageOut)
    async def coverage(actor: reader, posting_id: UUID) -> CoverageOut:
        return _out(CoverageOut, await call(uc.posting_coverage, db, actor, posting_id))

    @router.post("/study/postings/{posting_id}/plan", response_model=PlanOut, status_code=201)
    async def plan(actor: writer, posting_id: UUID, body: PlanIn | None = None) -> PlanOut:
        body = body or PlanIn()
        lines = None if body.lines is None else [(x.field, x.text) for x in body.lines]
        result = await call(
            uc.plan_from_posting,
            db,
            actor,
            posting_id,
            selections=lines,
            include_process=body.include_process,
        )
        return _out(PlanOut, result)

    @router.post("/study/postings/{posting_id}/claims", response_model=LinkOut, status_code=201)
    async def link(actor: writer, posting_id: UUID, body: LinkIn) -> LinkOut:
        req_id = await call(
            uc.link_claim,
            db,
            actor,
            posting_id,
            field=body.field,
            text=body.text,
            claim_id=body.claim_id,
        )
        return LinkOut(requirement_id=req_id, claim_id=body.claim_id)

    @router.delete("/study/requirements/{requirement_id}/claims/{claim_id}", status_code=204)
    async def unlink(actor: writer, requirement_id: UUID, claim_id: UUID) -> Response:
        await call(uc.unlink_claim, db, actor, requirement_id, claim_id)
        return Response(status_code=204)

    # --- 학습 항목

    @router.post("/study/items", response_model=IdOut, status_code=201)
    async def create_item(actor: writer, body: ItemIn) -> IdOut:
        item_id = await call(uc.create_item, db, actor, **body.model_dump())
        return IdOut(id=item_id)

    @router.get("/study/items", response_model=list[ItemSummaryOut])
    async def list_items(actor: reader, status: str | None = None) -> list[ItemSummaryOut]:
        rows = await call(uc.list_items, db, actor, status=status)
        return [_out(ItemSummaryOut, r) for r in rows]

    @router.get("/study/items/{item_id}", response_model=ItemOut)
    async def get_item(actor: reader, item_id: UUID) -> ItemOut:
        return _out(ItemOut, await call(uc.get_item, db, actor, item_id))

    @router.patch("/study/items/{item_id}", response_model=StatusOut)
    async def patch_item(actor: writer, item_id: UUID, body: ItemPatch) -> StatusOut:
        new = await call(uc.update_item, db, actor, item_id, **body.model_dump())
        return StatusOut(id=item_id, status=new)

    @router.post("/study/items/{item_id}/sessions", response_model=IdOut, status_code=201)
    async def add_session(actor: writer, item_id: UUID, body: SessionIn) -> IdOut:
        data = body.model_dump()
        session_id = await call(uc.log_session, db, actor, item_id, **data)
        return IdOut(id=session_id)

    @router.post(
        "/study/items/{item_id}/claim-candidates",
        response_model=CandidateCreatedOut,
        status_code=201,
    )
    async def propose(actor: writer, item_id: UUID, body: CandidateIn) -> CandidateCreatedOut:
        claim_id = await call(uc.propose_claim, db, actor, item_id, **body.model_dump())
        return CandidateCreatedOut(claim_id=claim_id, status="extracted")

    # --- 시험·응시

    @router.post("/study/assessments", response_model=IdOut, status_code=201)
    async def create_assessment(actor: writer, body: AssessmentIn) -> IdOut:
        return IdOut(id=await call(uc.create_assessment, db, actor, **body.model_dump()))

    @router.get("/study/assessments", response_model=list[AssessmentOut])
    async def list_assessments(actor: reader) -> list[AssessmentOut]:
        return [_out(AssessmentOut, a) for a in await call(uc.list_assessments, db, actor)]

    @router.get("/study/assessments/{assessment_id}", response_model=AssessmentOut)
    async def get_assessment(actor: reader, assessment_id: UUID) -> AssessmentOut:
        return _out(AssessmentOut, await call(uc.get_assessment, db, actor, assessment_id))

    @router.patch("/study/assessments/{assessment_id}", response_model=StatusOut)
    async def patch_assessment(
        actor: writer, assessment_id: UUID, body: AssessmentPatch
    ) -> StatusOut:
        new = await call(uc.update_assessment, db, actor, assessment_id, **body.model_dump())
        return StatusOut(id=assessment_id, status=new)

    @router.post("/study/attempts", response_model=IdOut, status_code=201)
    async def record_attempt(actor: writer, body: AttemptIn) -> IdOut:
        return IdOut(id=await call(uc.record_attempt, db, actor, **body.model_dump()))

    # --- 오답·복습

    @router.post("/study/mistakes", response_model=IdOut, status_code=201)
    async def add_mistake(actor: writer, body: MistakeIn) -> IdOut:
        return IdOut(id=await call(uc.add_mistake, db, actor, **body.model_dump()))

    @router.get("/study/mistakes", response_model=list[MistakeListOut])
    async def list_mistakes(
        actor: reader,
        status: str | None = None,
        limit: Annotated[int, Query(ge=1, le=uc.DUE_LIMIT)] = 100,
    ) -> list[MistakeListOut]:
        rows = await call(uc.list_mistakes, db, actor, status=status, limit=limit)
        return [_out(MistakeListOut, r) for r in rows]

    @router.patch("/study/mistakes/{mistake_id}", response_model=StatusOut)
    async def patch_mistake(actor: writer, mistake_id: UUID, body: MistakePatch) -> StatusOut:
        new = await call(uc.set_mistake_status, db, actor, mistake_id, body.status)
        return StatusOut(id=mistake_id, status=new)

    @router.post("/study/mistakes/{mistake_id}/reviews", response_model=ReviewOut)
    async def review(actor: writer, mistake_id: UUID, body: ReviewIn) -> ReviewOut:
        return _out(ReviewOut, await call(uc.review_mistake, db, actor, mistake_id, body.grade))

    @router.get("/study/reviews/due", response_model=list[DueOut])
    async def due(
        actor: reader,
        until: datetime | None = None,
        limit: Annotated[int, Query(ge=1, le=uc.DUE_LIMIT)] = 50,
    ) -> list[DueOut]:
        rows = await call(uc.due_reviews, db, actor, now=until, limit=limit)
        return [_out(DueOut, r) for r in rows]

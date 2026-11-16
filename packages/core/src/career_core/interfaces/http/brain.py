"""경험·주장·근거·인터뷰 경로.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from career_core.domain.brain import ClaimRuleError, ClaimStatus
from career_core.llm import ModelCallError, ModelDenied
from career_core.ports import ActorContext
from career_core.usecases import brain, extraction, interviews, wiki

_RULE_STATUS = {
    "confirm_requires_approve": status.HTTP_403_FORBIDDEN,
    "parse_not_finished": status.HTTP_409_CONFLICT,
    "claim_retired": status.HTTP_409_CONFLICT,
    "interview_closed": status.HTTP_409_CONFLICT,
}


def _rule(exc: ClaimRuleError) -> HTTPException:
    return HTTPException(status_code=_RULE_STATUS.get(exc.code, 422), detail=exc.code)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


# ---------------------------------------------------------------- schemas


class ExperienceIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    organization: str | None = Field(default=None, max_length=300)
    role: str | None = Field(default=None, max_length=300)
    period_text: str | None = Field(default=None, max_length=100)


class ExperienceSummaryOut(BaseModel):
    id: UUID
    title: str
    organization: str | None
    role: str | None
    period_text: str | None
    origin: str
    claim_counts: dict[str, int]
    needs_review: int


class EvidenceOut(BaseModel):
    id: UUID
    kind: str
    span_id: UUID | None
    quote: str | None
    page: int | None
    source_title: str | None
    answer_id: UUID | None
    question: str | None
    answer: str | None


class ClaimOut(BaseModel):
    id: UUID
    kind: str
    statement: str
    contribution: str
    status: str
    origin: str
    confirmed_at: datetime | None
    evidence: list[EvidenceOut]


class ExperienceOut(BaseModel):
    id: UUID
    title: str
    organization: str | None
    role: str | None
    period_text: str | None
    origin: str
    status: str
    claims: list[ClaimOut]


class ClaimIn(BaseModel):
    statement: str = Field(min_length=1, max_length=4000)
    kind: str = "other"
    contribution: str = "unknown"
    span_ids: list[UUID] = Field(default_factory=list, max_length=20)


class ClaimPatch(BaseModel):
    statement: str | None = Field(default=None, min_length=1, max_length=4000)
    kind: str | None = None
    contribution: str | None = None


class ConfirmIn(BaseModel):
    contribution: str | None = None
    statement: str | None = Field(default=None, min_length=1, max_length=4000)


class StatusOut(BaseModel):
    id: UUID
    status: str


class SpanIn(BaseModel):
    block_id: UUID
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)


class SpanRefIn(BaseModel):
    span_id: UUID


class SpanOut(BaseModel):
    id: UUID
    quote: str
    start_char: int
    end_char: int
    block_id: UUID
    block_text: str
    page: int | None
    ordinal: int
    parse_run_id: UUID
    source_id: UUID
    source_title: str
    original_filename: str


class ExtractionOut(BaseModel):
    id: UUID
    parse_run_id: UUID
    status: str
    error_code: str | None
    blocks_total: int
    blocks_sent: int
    experiences_created: int
    claims_created: int
    claims_dropped: int


class InterviewIn(BaseModel):
    experience_id: UUID | None = None


class QuestionOut(BaseModel):
    id: UUID
    ordinal: int
    focus: str
    prompt: str
    origin: str
    answers: list[str]


class InterviewOut(BaseModel):
    id: UUID
    experience_id: UUID | None
    status: str
    questions: list[QuestionOut]


class ClaimBriefOut(BaseModel):
    id: UUID
    statement: str
    status: str
    kind: str
    contribution: str
    experience_id: UUID
    experience_title: str


class InterviewSummaryOut(BaseModel):
    id: UUID
    experience_id: UUID | None
    experience_title: str | None
    status: str
    questions: int
    answered: int
    created_at: datetime
    last_answer_at: datetime | None


class AiQuestionsIn(BaseModel):
    count: int = Field(default=3, ge=1, le=interviews.MAX_AI_QUESTIONS)


class AnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=8000)
    make_claim: bool = True
    statement: str | None = Field(default=None, min_length=1, max_length=4000)
    kind: str = "other"
    contribution: str = "unknown"
    experience_id: UUID | None = None


class AnswerOut(BaseModel):
    answer_id: UUID
    claim_id: UUID | None
    claim_status: str | None


class WikiSectionOut(BaseModel):
    kind: str
    title: str
    claims: list[ClaimOut]


class WikiPageOut(BaseModel):
    experience_id: UUID
    title: str
    organization: str | None
    role: str | None
    period_text: str | None
    sections: list[WikiSectionOut]
    confirmed: int
    pending: int


def _interview_out(view: interviews.InterviewView) -> InterviewOut:
    return InterviewOut(
        id=view.id,
        experience_id=view.experience_id,
        status=view.status,
        questions=[
            QuestionOut(
                id=q.id,
                ordinal=q.ordinal,
                focus=q.focus,
                prompt=q.prompt,
                origin=q.origin,
                answers=[a for _, a in q.answers],
            )
            for q in view.questions
        ],
    )


# ---------------------------------------------------------------- routes


def add_brain_routes(router: APIRouter, deps, authorize) -> None:
    db = deps.db
    reader = Annotated[ActorContext, Depends(authorize("brain.read"))]
    writer = Annotated[ActorContext, Depends(authorize("brain.write"))]
    confirmer = Annotated[ActorContext, Depends(authorize("claims.confirm"))]
    extractor = Annotated[ActorContext, Depends(authorize("brain.extract"))]
    asker = Annotated[ActorContext, Depends(authorize("interview.ai"))]

    @router.get("/experiences", response_model=list[ExperienceSummaryOut])
    async def list_experiences(actor: reader) -> list[ExperienceSummaryOut]:
        return [
            ExperienceSummaryOut(
                id=e.id,
                title=e.title,
                organization=e.organization,
                role=e.role,
                period_text=e.period_text,
                origin=e.origin,
                claim_counts=e.claim_counts,
                needs_review=e.needs_review,
            )
            for e in await brain.list_experiences(db, actor)
        ]

    @router.post("/experiences", response_model=StatusOut, status_code=201)
    async def create_experience(actor: writer, body: ExperienceIn) -> StatusOut:
        try:
            exp_id = await brain.create_experience(db, actor, **body.model_dump())
        except ClaimRuleError as exc:
            raise _rule(exc) from exc
        return StatusOut(id=exp_id, status="active")

    @router.get("/experiences/{experience_id}", response_model=ExperienceOut)
    async def get_experience(actor: reader, experience_id: UUID) -> ExperienceOut:
        try:
            view = await brain.get_experience(db, actor, experience_id)
        except brain.NotFound as exc:
            raise _not_found() from exc
        return ExperienceOut.model_validate(view, from_attributes=True)

    @router.get("/claims", response_model=list[ClaimBriefOut])
    async def claims_by_ids(
        actor: reader,
        ids: Annotated[str, Query(max_length=8000, description="쉼표로 구분한 주장 ID")],
    ) -> list[ClaimBriefOut]:
        try:
            wanted = list(dict.fromkeys(UUID(x.strip()) for x in ids.split(",") if x.strip()))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid_claim_ids") from exc
        if len(wanted) > brain.MAX_BRIEF_IDS:
            raise HTTPException(status_code=422, detail="too_many_ids")
        rows = await brain.claims_by_ids(db, actor, wanted)
        return [ClaimBriefOut.model_validate(r, from_attributes=True) for r in rows]

    @router.post("/experiences/{experience_id}/claims", response_model=StatusOut, status_code=201)
    async def add_claim(actor: writer, experience_id: UUID, body: ClaimIn) -> StatusOut:
        try:
            claim_id = await brain.add_claim(db, actor, experience_id, **body.model_dump())
            view = await brain.get_experience(db, actor, experience_id)
        except brain.NotFound as exc:
            raise _not_found() from exc
        except ClaimRuleError as exc:
            raise _rule(exc) from exc
        claim = next(c for c in view.claims if c.id == claim_id)
        return StatusOut(id=claim_id, status=claim.status)

    @router.patch("/claims/{claim_id}", response_model=StatusOut)
    async def edit_claim(actor: writer, claim_id: UUID, body: ClaimPatch) -> StatusOut:
        try:
            new = await brain.edit_claim(db, actor, claim_id, **body.model_dump())
        except brain.NotFound as exc:
            raise _not_found() from exc
        except ClaimRuleError as exc:
            raise _rule(exc) from exc
        return StatusOut(id=claim_id, status=new)

    @router.post("/claims/{claim_id}/confirm", response_model=StatusOut)
    async def confirm_claim(actor: confirmer, claim_id: UUID, body: ConfirmIn) -> StatusOut:
        try:
            new = await brain.confirm_claim(db, actor, claim_id, **body.model_dump())
        except brain.NotFound as exc:
            raise _not_found() from exc
        except ClaimRuleError as exc:
            raise _rule(exc) from exc
        return StatusOut(id=claim_id, status=new)

    for verb, target in (("dispute", ClaimStatus.DISPUTED), ("retire", ClaimStatus.RETIRED)):

        async def change(actor: writer, claim_id: UUID, _target=target) -> StatusOut:
            try:
                new = await brain.set_claim_status(db, actor, claim_id, _target)
            except brain.NotFound as exc:
                raise _not_found() from exc
            except ClaimRuleError as exc:
                raise _rule(exc) from exc
            return StatusOut(id=claim_id, status=new)

        router.add_api_route(
            f"/claims/{{claim_id}}/{verb}",
            change,
            methods=["POST"],
            response_model=StatusOut,
            name=f"{verb}_claim",
        )

    @router.post("/claims/{claim_id}/evidence", response_model=StatusOut)
    async def attach_span(actor: writer, claim_id: UUID, body: SpanRefIn) -> StatusOut:
        try:
            new = await brain.attach_span(db, actor, claim_id, body.span_id)
        except brain.NotFound as exc:
            raise _not_found() from exc
        return StatusOut(id=claim_id, status=new)

    @router.post("/source-spans", response_model=StatusOut, status_code=201)
    async def create_span(actor: writer, body: SpanIn) -> StatusOut:
        try:
            span_id = await brain.create_span(
                db, actor, body.block_id, body.start_char, body.end_char
            )
        except brain.NotFound as exc:
            raise _not_found() from exc
        except ClaimRuleError as exc:
            raise _rule(exc) from exc
        return StatusOut(id=span_id, status="created")

    @router.get("/source-spans/{span_id}", response_model=SpanOut)
    async def get_span(actor: reader, span_id: UUID) -> SpanOut:
        try:
            view = await brain.get_span(db, actor, span_id)
        except brain.NotFound as exc:
            raise _not_found() from exc
        return SpanOut.model_validate(view, from_attributes=True)

    @router.post("/parse-runs/{run_id}/extractions", response_model=StatusOut, status_code=202)
    async def request_extraction(actor: extractor, run_id: UUID) -> StatusOut:
        try:
            ext_id = await extraction.request_extraction(db, actor, run_id)
        except brain.NotFound as exc:
            raise _not_found() from exc
        except ClaimRuleError as exc:
            raise _rule(exc) from exc
        return StatusOut(id=ext_id, status="pending")

    @router.get("/extraction-runs/{run_id}", response_model=ExtractionOut)
    async def get_extraction(actor: reader, run_id: UUID) -> ExtractionOut:
        try:
            view = await extraction.get_extraction(db, actor, run_id)
        except brain.NotFound as exc:
            raise _not_found() from exc
        return ExtractionOut.model_validate(view, from_attributes=True)

    @router.post("/interviews", response_model=InterviewOut, status_code=201)
    async def start_interview(actor: writer, body: InterviewIn) -> InterviewOut:
        try:
            sid = await interviews.start_interview(db, actor, body.experience_id)
        except brain.NotFound as exc:
            raise _not_found() from exc
        return _interview_out(await interviews.get_interview(db, actor, sid))

    @router.get("/interviews", response_model=list[InterviewSummaryOut])
    async def list_interviews(
        actor: reader,
        experience_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[InterviewSummaryOut]:
        rows = await interviews.list_interviews(db, actor, experience_id=experience_id, limit=limit)
        return [InterviewSummaryOut.model_validate(r, from_attributes=True) for r in rows]

    @router.get("/interviews/{session_id}", response_model=InterviewOut)
    async def get_interview(actor: reader, session_id: UUID) -> InterviewOut:
        try:
            return _interview_out(await interviews.get_interview(db, actor, session_id))
        except brain.NotFound as exc:
            raise _not_found() from exc

    @router.post("/interviews/{session_id}/ai-questions", response_model=InterviewOut)
    async def ai_questions(actor: asker, session_id: UUID, body: AiQuestionsIn) -> InterviewOut:
        if deps.models is None:
            raise HTTPException(status_code=503, detail="models_not_configured")
        try:
            await interviews.add_ai_questions(db, deps.models, actor, session_id, count=body.count)
            return _interview_out(await interviews.get_interview(db, actor, session_id))
        except brain.NotFound as exc:
            raise _not_found() from exc
        except ModelDenied as exc:
            raise HTTPException(status_code=409, detail=exc.code) from exc
        except ModelCallError as exc:
            raise HTTPException(status_code=502, detail=exc.code) from exc
        except ClaimRuleError as exc:
            raise HTTPException(status_code=502, detail=exc.code) from exc

    @router.post(
        "/interview-questions/{question_id}/answers", response_model=AnswerOut, status_code=201
    )
    async def answer(actor: writer, question_id: UUID, body: AnswerIn) -> AnswerOut:
        try:
            res = await interviews.answer_question(db, actor, question_id, **body.model_dump())
        except brain.NotFound as exc:
            raise _not_found() from exc
        except ClaimRuleError as exc:
            raise _rule(exc) from exc
        return AnswerOut(
            answer_id=res.answer_id, claim_id=res.claim_id, claim_status=res.claim_status
        )

    @router.get("/wiki/experiences/{experience_id}", response_model=WikiPageOut)
    async def wiki_page(actor: reader, experience_id: UUID) -> WikiPageOut:
        try:
            page = await wiki.wiki_page(db, actor, experience_id)
        except brain.NotFound as exc:
            raise _not_found() from exc
        return WikiPageOut.model_validate(page, from_attributes=True)

    @router.get("/wiki/experiences/{experience_id}/markdown")
    async def wiki_markdown(actor: reader, experience_id: UUID) -> Response:
        try:
            page = await wiki.wiki_page(db, actor, experience_id)
        except brain.NotFound as exc:
            raise _not_found() from exc
        return Response(wiki.render_markdown(page), media_type="text/markdown; charset=utf-8")

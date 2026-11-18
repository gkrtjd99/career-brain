"""경험·주장·근거·인터뷰 경로.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

import dataclasses
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from career_core.domain.brain import ClaimRuleError, ClaimStatus
from career_core.llm import ModelCallError, ModelDenied
from career_core.ports import ActorContext
from career_core.usecases import brain, experience_merge, extraction, interviews, wiki

_RULE_STATUS = {
    "confirm_requires_approve": status.HTTP_403_FORBIDDEN,
    "parse_not_finished": status.HTTP_409_CONFLICT,
    "claim_retired": status.HTTP_409_CONFLICT,
    "interview_closed": status.HTTP_409_CONFLICT,
    # c0030: 상하는 한 단계뿐 (지금 상태와 부딪힘)
    "parent_is_child": status.HTTP_409_CONFLICT,
    "experience_has_children": status.HTTP_409_CONFLICT,
    # c0032: 증빙·참고 원본은 근거가 되지 않고 경험 추출 대상도 아니다
    "source_not_experience": status.HTTP_409_CONFLICT,
    # c0050: 합치기·지우기
    "merge_requires_approve": status.HTTP_403_FORBIDDEN,
    "experience_not_active": status.HTTP_409_CONFLICT,
    "merge_parent_into_child": status.HTTP_409_CONFLICT,
    "merge_children_into_child": status.HTTP_409_CONFLICT,
}


def _rule(exc: ClaimRuleError) -> HTTPException:
    if isinstance(exc, experience_merge.ExperienceInUse):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "references": exc.references},
        )
    return HTTPException(status_code=_RULE_STATUS.get(exc.code, 422), detail=exc.code)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


# ---------------------------------------------------------------- schemas


MONTH = r"^\d{4}-\d{2}$"
Tags = Annotated[list[Annotated[str, Field(max_length=200)]], Field(max_length=100)]


class ExperienceIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    organization: str | None = Field(default=None, max_length=300)
    role: str | None = Field(default=None, max_length=300)
    period_text: str | None = Field(default=None, max_length=100)
    # c0030 (O19). 달은 YYYY-MM. 태그는 정규화 뒤 30개·40자 이하 (넘으면 422 too_many_tags·tag_too_long)
    kind: str = "other"
    parent_id: UUID | None = None
    started_on: str | None = Field(default=None, pattern=MONTH)
    ended_on: str | None = Field(default=None, pattern=MONTH)
    ongoing: bool = False
    tags: Tags = Field(default_factory=list)


class ExperiencePatch(BaseModel):
    """준 칸만 고친다. null은 비우기 (제목은 비울 수 없다)."""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    organization: str | None = Field(default=None, max_length=300)
    role: str | None = Field(default=None, max_length=300)
    period_text: str | None = Field(default=None, max_length=100)
    kind: str | None = None
    parent_id: UUID | None = None
    started_on: str | None = Field(default=None, pattern=MONTH)
    ended_on: str | None = Field(default=None, pattern=MONTH)
    ongoing: bool | None = None
    tags: Tags | None = None


class ExperienceSummaryOut(BaseModel):
    id: UUID
    title: str
    organization: str | None
    role: str | None
    period_text: str | None
    origin: str
    claim_counts: dict[str, int]
    needs_review: int
    kind: str
    parent_id: UUID | None
    parent_title: str | None
    started_on: str | None
    ended_on: str | None
    ongoing: bool
    tags: list[str]
    children_count: int
    updated_at: datetime | None
    # c0050: "같은 경험일 수 있음" (추출이 확실하지 않아 새로 만든 경험 → 그 기존 경험)
    possible_duplicate_of: UUID | None = None
    possible_duplicate_title: str | None = None


class ChildExperienceOut(BaseModel):
    id: UUID
    title: str
    kind: str
    role: str | None
    started_on: str | None
    ended_on: str | None
    ongoing: bool


class RecentExperienceOut(BaseModel):
    id: UUID
    title: str
    kind: str
    organization: str | None
    updated_at: datetime


class TagCountOut(BaseModel):
    tag: str
    count: int


class BrainOverviewOut(BaseModel):
    experiences: int
    kinds: dict[str, int]
    needs_review: int
    pending_claims: int
    undated: int
    year_min: int | None
    year_max: int | None
    recent: list[RecentExperienceOut]
    tags: list[TagCountOut]


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
    # 인용이 주장을 받치지 않아 보인다 (숫자·핵심 낱말이 인용에 없다). 화면: "인용이 약함"
    weak_quote: bool = False


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
    kind: str
    parent_id: UUID | None
    parent_title: str | None
    started_on: str | None
    ended_on: str | None
    ongoing: bool
    tags: list[str]
    children: list[ChildExperienceOut]
    updated_at: datetime | None
    # c0050: 합친 경험이면(status retired) 대상 경험. 화면은 대상으로 옮겨 보여 준다
    merged_into_id: UUID | None = None
    merged_at: datetime | None = None
    possible_duplicate_of: UUID | None = None
    possible_duplicate_title: str | None = None


class MergeIn(BaseModel):
    source_ids: list[UUID] = Field(min_length=1, max_length=experience_merge.MAX_MERGE)


class MergeOut(BaseModel):
    target_id: UUID
    merged: list[UUID]
    claims_moved: int
    claims_folded: int
    children_moved: int
    links_moved: dict[str, int]


class DeleteExperienceOut(BaseModel):
    id: UUID
    claims_deleted: int
    unlinked: dict[str, int]


class BatchConfirmIn(BaseModel):
    """한 경험(``experience_id``)·한 원본(``source_id``)·고른 ID(``claim_ids``) 중 하나만."""

    contribution: Literal["personal", "team", "as_is"]
    experience_id: UUID | None = None
    source_id: UUID | None = None
    claim_ids: list[UUID] | None = Field(default=None, max_length=brain.MAX_BATCH_CONFIRM)


class BatchConfirmedOut(BaseModel):
    id: UUID
    status: str


class BatchSkippedOut(BaseModel):
    id: UUID
    reason: str


class BatchConfirmOut(BaseModel):
    confirmed: list[BatchConfirmedOut]
    skipped: list[BatchSkippedOut]
    truncated: bool


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
    source_purpose: str = "experience"


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
    # c0050: 기존 경험에 붙인 수, 같은 사실에 근거로 붙인 수, 경험이 아닌 줄의 제안
    # (education: 학력 제안, skills: 경험에 옮기지 못한 기술명, possible_duplicates, filtered)
    experiences_matched: int = 0
    claims_attached: int = 0
    suggestions: dict = Field(default_factory=dict)


class SuggestionsOut(BaseModel):
    education: list[dict]
    skills: list[str]


class DismissSuggestionIn(BaseModel):
    """지울 제안: 기술명(대소문자 무시) 또는 학력의 학교 이름(법인 표시·공백·문장 부호 무시)."""

    kind: Literal["skill", "education"]
    value: str = Field(min_length=1, max_length=300)


class InterviewIn(BaseModel):
    experience_id: UUID | None = None


class QuestionOut(BaseModel):
    id: UUID
    ordinal: int
    focus: str
    prompt: str
    origin: str
    answers: list[str]
    # c0023: 이 질문을 만든 일괄 실행 (없으면 직접 요청·템플릿)
    run_id: UUID | None = None


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
    # c0023: 이 세션에 질문을 만든 일괄 실행들
    run_ids: list[UUID] = []


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
                run_id=q.run_id,
            )
            for q in view.questions
        ],
    )


class RecentOrganizeOut(BaseModel):
    since: datetime
    sources: int
    experiences_created: int
    experiences_matched: int
    claims_created: int
    claims_attached: int


class OrganizeStatusOut(BaseModel):
    """브레인 정리 상태 한 줄 (c0052, ``usecases.organize``). 세기만 한다."""

    processing: int
    needs_purpose: int
    failed: int
    pending_claims: int
    review_experiences: int
    duplicates: int
    profile_values: int
    education: int
    skills: int
    credentials: int
    expiring: int
    recent: RecentOrganizeOut
    # on · off · no_permission · no_model · egress_denied · api_key_missing
    auto_extract: str


# ---------------------------------------------------------------- routes


def add_brain_routes(router: APIRouter, deps, authorize) -> None:
    db = deps.db
    reader = Annotated[ActorContext, Depends(authorize("brain.read"))]
    writer = Annotated[ActorContext, Depends(authorize("brain.write"))]
    confirmer = Annotated[ActorContext, Depends(authorize("claims.confirm"))]
    extractor = Annotated[ActorContext, Depends(authorize("brain.extract"))]
    asker = Annotated[ActorContext, Depends(authorize("interview.ai"))]

    @router.get("/experiences", response_model=list[ExperienceSummaryOut])
    async def list_experiences(
        actor: reader,
        response: Response,
        kind: Annotated[list[str] | None, Query(max_length=10)] = None,
        parent_id: UUID | None = None,
        top_level: bool = False,
        tag: Annotated[list[str] | None, Query(max_length=30)] = None,
        year: Annotated[int | None, Query(ge=1900, le=2200)] = None,
        org: Annotated[str | None, Query(max_length=300)] = None,
        status: Annotated[Literal["needs_review", "confirmed", "no_claims"] | None, Query()] = None,
        q: Annotated[str | None, Query(max_length=200)] = None,
        sort: Literal["start", "updated", "title"] = "start",
        duplicates: bool = False,
        limit: Annotated[int, Query(ge=1, le=brain.MAX_LIST_LIMIT)] = brain.MAX_LIST_LIMIT,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[ExperienceSummaryOut]:
        """활성 경험. 거름·정렬·쪽. 거른 전체 수는 ``X-Total-Count`` 머리글."""
        f = brain.ExperienceFilter(
            kinds=tuple(kind or ()),
            parent_id=parent_id,
            top_level=top_level,
            tags=tuple(tag or ()),
            year=year,
            organization=org,
            review=status,
            q=q,
            sort=sort,
            duplicates=duplicates,
            limit=limit,
            offset=offset,
        )
        try:
            rows, total = await brain.page_experiences(db, actor, f)
        except ClaimRuleError as exc:
            raise _rule(exc) from exc
        response.headers["X-Total-Count"] = str(total)
        return [
            ExperienceSummaryOut.model_validate(
                {**dataclasses.asdict(e), "needs_review": e.needs_review}
            )
            for e in rows
        ]

    @router.get("/brain/overview", response_model=BrainOverviewOut)
    async def brain_overview(actor: reader) -> BrainOverviewOut:
        view = await brain.brain_overview(db, actor)
        return BrainOverviewOut.model_validate(view, from_attributes=True)

    @router.get("/brain/organize", response_model=OrganizeStatusOut)
    async def organize_status(actor: reader) -> OrganizeStatusOut:
        """넣은 자료를 정리한 상태: 진행 중·확인할 것·지난 하루의 정리 결과 (c0052)."""
        from career_core.usecases.organize import organize_status as status_of

        view = await status_of(
            db, deps.models, actor, can_extract=deps.permissions.allows(actor, "brain.extract")
        )
        return OrganizeStatusOut.model_validate(view, from_attributes=True)

    @router.post("/experiences", response_model=StatusOut, status_code=201)
    async def create_experience(actor: writer, body: ExperienceIn) -> StatusOut:
        try:
            exp_id = await brain.create_experience(db, actor, **body.model_dump())
        except ClaimRuleError as exc:
            raise _rule(exc) from exc
        return StatusOut(id=exp_id, status="active")

    @router.patch("/experiences/{experience_id}", response_model=ExperienceOut)
    async def update_experience(
        actor: writer, experience_id: UUID, body: ExperiencePatch
    ) -> ExperienceOut:
        fields = body.model_dump(exclude_unset=True)
        if fields.get("kind", "") is None or fields.get("ongoing", False) is None:
            raise HTTPException(status_code=422, detail="null_not_allowed")
        try:
            await brain.update_experience(db, actor, experience_id, **fields)
            view = await brain.get_experience(db, actor, experience_id)
        except brain.NotFound as exc:
            raise _not_found() from exc
        except ClaimRuleError as exc:
            raise _rule(exc) from exc
        return ExperienceOut.model_validate(view, from_attributes=True)

    @router.get("/experiences/{experience_id}", response_model=ExperienceOut)
    async def get_experience(actor: reader, experience_id: UUID) -> ExperienceOut:
        try:
            view = await brain.get_experience(db, actor, experience_id)
        except brain.NotFound as exc:
            raise _not_found() from exc
        return ExperienceOut.model_validate(view, from_attributes=True)

    @router.post("/experiences/{experience_id}/merge", response_model=MergeOut)
    async def merge_experiences(actor: confirmer, experience_id: UUID, body: MergeIn) -> MergeOut:
        """``source_ids`` 경험을 이 경험으로 합친다 (사실·근거·아래 경험·연결을 옮기고, 합친 경험은 보관)."""
        try:
            r = await experience_merge.merge_experiences(db, actor, experience_id, body.source_ids)
        except brain.NotFound as exc:
            raise _not_found() from exc
        except ClaimRuleError as exc:
            raise _rule(exc) from exc
        return MergeOut.model_validate(r, from_attributes=True)

    @router.delete("/experiences/{experience_id}", response_model=DeleteExperienceOut)
    async def delete_experience(
        actor: writer, experience_id: UUID, include_confirmed: bool = False
    ) -> DeleteExperienceOut:
        """쓰이는 경험은 지우지 않는다: 409 ``{"code": "experience_in_use", "references": {…}}``.
        확인한 사실이 있으면 ``include_confirmed=true``로 다시 보내야 한다 (확인 권한 필요)."""
        try:
            r = await experience_merge.delete_experience(
                db, actor, experience_id, include_confirmed=include_confirmed
            )
        except brain.NotFound as exc:
            raise _not_found() from exc
        except ClaimRuleError as exc:
            raise _rule(exc) from exc
        return DeleteExperienceOut.model_validate(r, from_attributes=True)

    @router.delete("/experiences/{experience_id}/duplicate-hint", status_code=204)
    async def dismiss_duplicate(actor: writer, experience_id: UUID) -> Response:
        """ "같은 경험일 수 있음" 표시를 지운다 (다른 경험이다)."""
        try:
            await experience_merge.dismiss_duplicate(db, actor, experience_id)
        except brain.NotFound as exc:
            raise _not_found() from exc
        return Response(status_code=204)

    @router.post("/claims/confirm-batch", response_model=BatchConfirmOut)
    async def confirm_claims(actor: confirmer, body: BatchConfirmIn) -> BatchConfirmOut:
        try:
            r = await brain.confirm_claims(db, actor, **body.model_dump())
        except brain.NotFound as exc:
            raise _not_found() from exc
        except ClaimRuleError as exc:
            raise _rule(exc) from exc
        return BatchConfirmOut(
            confirmed=[BatchConfirmedOut(id=i, status=st) for i, st in r.confirmed],
            skipped=[BatchSkippedOut(id=i, reason=why) for i, why in r.skipped],
            truncated=r.truncated,
        )

    @router.get("/extraction-suggestions", response_model=SuggestionsOut)
    async def extraction_suggestions(actor: reader) -> SuggestionsOut:
        """추출이 경험으로 만들지 않은 줄: 학력 제안(아직 학력에 없는 학교), 경험에 옮기지 못한 기술명."""
        view = await extraction.list_suggestions(db, actor)
        return SuggestionsOut.model_validate(view)

    @router.post("/extraction-suggestions/dismiss", status_code=204)
    async def dismiss_extraction_suggestion(actor: writer, body: DismissSuggestionIn) -> Response:
        """제안 하나를 지운다 (공간의 모든 추출 실행에서). 없는 값이어도 204."""
        await extraction.dismiss_suggestion(db, actor, body.kind, body.value)
        return Response(status_code=204)

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
        except ClaimRuleError as exc:
            raise _rule(exc) from exc
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
        run_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[InterviewSummaryOut]:
        rows = await interviews.list_interviews(
            db, actor, experience_id=experience_id, run_id=run_id, limit=limit
        )
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

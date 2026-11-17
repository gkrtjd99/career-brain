"""공통 답변·지난 제출본·기술 보기 경로 (c0042, plan §19.4, §20 "나 › 내 글", "나 › 기술").

공통 답변: 읽기 ``answers.read``, 쓰기 ``answers.write``. "지원서에 넣기"는 지원 문서를 쓰므로
``applications.write``. 지난 제출본은 ``applications.read`` (넣기는 ``applications.write``, 새 공통 답변으로
시작하기는 ``answers.write``). 기술 보기는 ``brain.read``, 합치기·나누기는 기술명 별칭 표(공간 설정)를 바꾸므로
``settings.write``.

오류는 ``{"detail": code}``: 없는 답변·제출본·지원·문서 404 ``not found``. 넣을 문서의 ``base_revision``이 최신이
아니면 409 ``revision_conflict`` (``current_revision`` 포함), 제출한 지원 409 ``application_locked``. 그 밖의 규칙 422
(``claim_not_confirmed``, ``unknown_claim``, ``too_many_claims``, ``title_required``, ``body_too_long``,
``nothing_to_copy``, ``invalid_skill_name``, ``not_merged``, ``alias_conflict`` …).

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from career_core.domain.documents import DocumentRuleError
from career_core.interfaces.http.applications import _error as _document_error
from career_core.ports import ActorContext
from career_core.usecases import answers as uc
from career_core.usecases import skills as skills_uc
from career_core.usecases.brain import NotFound

Kind = Literal["intro", "motivation", "personality", "conflict", "failure", "aspiration", "other"]


def _rule(exc: uc.AnswerRuleError | skills_uc.SkillRuleError) -> HTTPException:
    return HTTPException(status_code=422, detail=exc.code)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not found")


class AnswerIn(BaseModel):
    kind: Kind = "other"
    title: str = Field(min_length=1, max_length=uc.MAX_TITLE * 2)
    body: str = Field(default="", max_length=uc.MAX_BODY * 2)
    claim_ids: list[UUID] = Field(default_factory=list, max_length=uc.MAX_CLAIMS * 2)


class AnswerPatch(BaseModel):
    kind: Kind | None = None
    title: str | None = Field(default=None, min_length=1, max_length=uc.MAX_TITLE * 2)
    body: str | None = Field(default=None, max_length=uc.MAX_BODY * 2)
    claim_ids: list[UUID] | None = Field(default=None, max_length=uc.MAX_CLAIMS * 2)


class ClaimRefOut(BaseModel):
    id: UUID
    statement: str
    kind: str
    status: str
    confirmed: bool
    experience_id: UUID
    experience_title: str


class UseRefOut(BaseModel):
    application_id: UUID
    application_title: str
    document_id: UUID
    document_title: str
    created_at: datetime


class AnswerSummaryOut(BaseModel):
    id: UUID
    kind: str
    title: str
    excerpt: str
    chars: int
    chars_without_spaces: int
    claims: int
    uses: int
    updated_at: datetime


class AnswerOut(BaseModel):
    id: UUID
    kind: str
    title: str
    body: str
    chars: int
    chars_without_spaces: int
    claims: list[ClaimRefOut]
    uses: list[UseRefOut]
    source_submission_id: UUID | None
    source_document_id: UUID | None
    created_at: datetime
    updated_at: datetime


class CreatedOut(BaseModel):
    id: UUID


class PlaceIn(BaseModel):
    """지원 문서에 넣기. ``document_id``가 있으면 그 문서 끝에 더한다 (``base_revision`` 필수, 최신이어야 함).
    없으면 새 문서를 만든다 (``question_prompt``가 있으면 공고 문항 문서). ``dry_run``이면 쓰지 않고 경고만."""

    application_id: UUID
    document_id: UUID | None = None
    base_revision: int | None = Field(default=None, ge=0)
    question_prompt: str | None = Field(default=None, max_length=2000)
    char_limit: int | None = Field(default=None, ge=1, le=100_000)
    dry_run: bool = False


class PlaceOut(BaseModel):
    application_id: UUID
    document_id: UUID | None
    revision_no: int | None
    created_document: bool
    # 글에 보이는 다른 회사 이름 (이 지원의 회사는 뺀다). 막지 않고 알리기만 한다
    company_mentions: list[str]
    claims_dropped: int


class SubmittedDocumentOut(BaseModel):
    submission_id: UUID
    application_id: UUID
    application_title: str
    company: str | None
    role: str | None
    document_id: UUID
    kind: str
    title: str
    question: str | None
    char_limit: int | None
    chars: int | None
    submitted_at: datetime


class SubmittedBlockOut(BaseModel):
    id: str | None
    text: str
    origin: str
    claim_ids: list[str]


class SubmittedDocumentViewOut(SubmittedDocumentOut):
    text: str
    blocks: list[SubmittedBlockOut]
    evidence: list[dict[str, Any]]
    company_mentions: list[str]
    answers: list[dict[str, str]]


class StartAnswerIn(BaseModel):
    kind: Kind = "other"
    title: str | None = Field(default=None, max_length=uc.MAX_TITLE * 2)


class StartAnswerOut(BaseModel):
    answer_id: UUID
    company_mentions: list[str]
    claims_dropped: int


class SkillExperienceOut(BaseModel):
    id: UUID
    title: str
    kind: str
    started_on: date | None
    ended_on: date | None
    ongoing: bool
    via: list[str]


class SkillCredentialOut(BaseModel):
    id: UUID
    name: str
    kind: str
    acquired_on: date | None


class SkillOut(BaseModel):
    key: str
    name: str
    spellings: list[str]
    merged: list[str]
    count: int
    claims: int
    first_year: int | None
    last_year: int | None
    experiences: list[SkillExperienceOut]
    credentials: list[SkillCredentialOut]


class MergeIn(BaseModel):
    into: str = Field(min_length=1, max_length=80)
    names: list[str] = Field(min_length=1, max_length=20)


class MergeOut(BaseModel):
    name: str


class SplitIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def _place_out(r: uc.PlaceResult) -> PlaceOut:
    return PlaceOut.model_validate(r, from_attributes=True)


def add_answer_routes(router: APIRouter, deps, authorize) -> None:
    reader = Annotated[ActorContext, Depends(authorize("answers.read"))]
    writer = Annotated[ActorContext, Depends(authorize("answers.write"))]
    app_reader = Annotated[ActorContext, Depends(authorize("applications.read"))]
    app_writer = Annotated[ActorContext, Depends(authorize("applications.write"))]
    brain_reader = Annotated[ActorContext, Depends(authorize("brain.read"))]
    settings_writer = Annotated[ActorContext, Depends(authorize("settings.write"))]

    # ------------------------------------------------------------ 공통 답변

    @router.get("/answer-assets", response_model=list[AnswerSummaryOut])
    async def list_answers(actor: reader) -> list[AnswerSummaryOut]:
        return [
            AnswerSummaryOut.model_validate(a, from_attributes=True)
            for a in await uc.list_answers(deps.db, actor)
        ]

    @router.post("/answer-assets", response_model=CreatedOut, status_code=201)
    async def create_answer(body: AnswerIn, actor: writer) -> CreatedOut:
        try:
            new_id = await uc.create_answer(deps.db, actor, **body.model_dump())
        except uc.AnswerRuleError as exc:
            raise _rule(exc) from exc
        return CreatedOut(id=new_id)

    @router.get("/answer-assets/{answer_id}", response_model=AnswerOut)
    async def get_answer(answer_id: UUID, actor: reader) -> AnswerOut:
        try:
            return AnswerOut.model_validate(
                await uc.get_answer(deps.db, actor, answer_id), from_attributes=True
            )
        except NotFound as exc:
            raise _not_found() from exc

    @router.patch("/answer-assets/{answer_id}", response_model=AnswerOut)
    async def patch_answer(answer_id: UUID, body: AnswerPatch, actor: writer) -> AnswerOut:
        try:
            await uc.update_answer(deps.db, actor, answer_id, body.model_dump(exclude_unset=True))
            view = await uc.get_answer(deps.db, actor, answer_id)
        except NotFound as exc:
            raise _not_found() from exc
        except uc.AnswerRuleError as exc:
            raise _rule(exc) from exc
        return AnswerOut.model_validate(view, from_attributes=True)

    @router.delete("/answer-assets/{answer_id}", status_code=204)
    async def delete_answer(answer_id: UUID, actor: writer) -> None:
        try:
            await uc.delete_answer(deps.db, actor, answer_id)
        except NotFound as exc:
            raise _not_found() from exc

    @router.post("/answer-assets/{answer_id}/use", response_model=PlaceOut)
    async def use_answer(answer_id: UUID, body: PlaceIn, actor: app_writer) -> PlaceOut:
        try:
            result = await uc.use_answer(
                deps.db,
                actor,
                answer_id,
                body.application_id,
                document_id=body.document_id,
                base_revision=body.base_revision,
                question_prompt=body.question_prompt,
                char_limit=body.char_limit,
                dry_run=body.dry_run,
            )
        except NotFound as exc:
            raise _not_found() from exc
        except uc.AnswerRuleError as exc:
            raise _rule(exc) from exc
        except DocumentRuleError as exc:
            raise _document_error(exc) from exc
        return _place_out(result)

    # ------------------------------------------------------------ 지난 제출본

    @router.get("/submitted-documents", response_model=list[SubmittedDocumentOut])
    async def list_submitted(actor: app_reader) -> list[SubmittedDocumentOut]:
        return [
            SubmittedDocumentOut.model_validate(d, from_attributes=True)
            for d in await uc.list_submitted_documents(deps.db, actor)
        ]

    @router.get(
        "/submitted-documents/{submission_id}/{document_id}",
        response_model=SubmittedDocumentViewOut,
    )
    async def get_submitted(
        submission_id: UUID, document_id: UUID, actor: app_reader
    ) -> SubmittedDocumentViewOut:
        try:
            view = await uc.get_submitted_document(deps.db, actor, submission_id, document_id)
        except NotFound as exc:
            raise _not_found() from exc
        return SubmittedDocumentViewOut.model_validate(view, from_attributes=True)

    @router.post(
        "/submitted-documents/{submission_id}/{document_id}/to-answer",
        response_model=StartAnswerOut,
        status_code=201,
    )
    async def start_answer(
        submission_id: UUID, document_id: UUID, body: StartAnswerIn, actor: writer
    ) -> StartAnswerOut:
        try:
            got = await uc.start_answer_from_submission(
                deps.db, actor, submission_id, document_id, kind=body.kind, title=body.title
            )
        except NotFound as exc:
            raise _not_found() from exc
        except uc.AnswerRuleError as exc:
            raise _rule(exc) from exc
        return StartAnswerOut.model_validate(got, from_attributes=True)

    @router.post("/submitted-documents/{submission_id}/{document_id}/use", response_model=PlaceOut)
    async def use_submitted(
        submission_id: UUID, document_id: UUID, body: PlaceIn, actor: app_writer
    ) -> PlaceOut:
        try:
            result = await uc.use_submission_document(
                deps.db,
                actor,
                submission_id,
                document_id,
                body.application_id,
                target_document_id=body.document_id,
                base_revision=body.base_revision,
                question_prompt=body.question_prompt,
                char_limit=body.char_limit,
                dry_run=body.dry_run,
            )
        except NotFound as exc:
            raise _not_found() from exc
        except uc.AnswerRuleError as exc:
            raise _rule(exc) from exc
        except DocumentRuleError as exc:
            raise _document_error(exc) from exc
        return _place_out(result)

    # ------------------------------------------------------------ 기술 보기

    @router.get("/skills", response_model=list[SkillOut])
    async def list_skills(actor: brain_reader) -> list[SkillOut]:
        return [
            SkillOut.model_validate(v, from_attributes=True)
            for v in await skills_uc.skills_overview(deps.db, actor)
        ]

    @router.post("/skills/merge", response_model=MergeOut)
    async def merge_skills(body: MergeIn, actor: settings_writer) -> MergeOut:
        try:
            name = await skills_uc.merge_skills(deps.db, actor, body.into, body.names)
        except skills_uc.SkillRuleError as exc:
            raise _rule(exc) from exc
        return MergeOut(name=name)

    @router.post("/skills/split", status_code=204)
    async def split_skill(body: SplitIn, actor: settings_writer) -> None:
        try:
            await skills_uc.split_skill(deps.db, actor, body.name)
        except skills_uc.SkillRuleError as exc:
            raise _rule(exc) from exc

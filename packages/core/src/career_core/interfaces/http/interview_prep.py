"""면접 준비 경로 (O16, plan §19.4). ``/applications/{application_id}/interview-prep``.

- ``GET``: 예상 질문·출처·쓸 경험·답변 메모 (없으면 ``status = none``). 출처가 바뀌었으면 ``stale``.
- ``POST``: (다시) 만들기. 쓰기 권한. 모델은 ``interview_prep.ai``(egress) 권한이 있고 게이트웨이가 있을 때만
  쓰고, 아니면 규칙 질문 + 검색 순위(``mode = search``). 답변 메모는 건드리지 않는다.
- ``POST .../questions``: 질문 직접 더하기. ``DELETE .../questions/{question_id}``: 지우기.
- ``PUT .../questions/{question_id}/answer``: 사람이 쓴 답변 메모 저장 (빈 글이면 지움).
- ``PUT/DELETE .../questions/{question_id}/answer-asset``: 공통 답변 잇기·끊기 ("공통 답변에서 가져오기").
- ``POST .../questions/{question_id}/save-answer``: 답변 메모로 새 공통 답변을 만들고 잇기 ("공통 답변으로 저장").
- ``POST .../questions/{question_id}/feedback``: 사람이 쓴 답에 모델 의견 (요청할 때만, 저장하지 않음).

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from career_core.llm import ModelCallError, ModelDenied
from career_core.ports import ActorContext
from career_core.usecases import interview_prep as uc
from career_core.usecases.answers import AnswerRuleError
from career_core.usecases.brain import NotFound

_RULE_STATUS = {
    "prep_not_ready": status.HTTP_409_CONFLICT,
    "too_many_questions": status.HTTP_409_CONFLICT,
    "models_not_configured": status.HTTP_503_SERVICE_UNAVAILABLE,
    "invalid_model_output": status.HTTP_502_BAD_GATEWAY,
}

CommonKind = Literal[
    "intro", "motivation", "personality", "conflict", "failure", "aspiration", "other"
]


def _error(exc: uc.InterviewPrepError | AnswerRuleError) -> HTTPException:
    return HTTPException(status_code=_RULE_STATUS.get(exc.code, 422), detail=exc.code)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


class LinkedAnswerOut(BaseModel):
    id: UUID
    kind: str
    title: str
    body: str
    chars: int
    chars_without_spaces: int


class QuestionOut(BaseModel):
    id: UUID
    position: int
    source_kind: str
    origin: str
    question: str
    source: dict[str, Any]
    common_kind: str | None
    suggestions: list[dict[str, Any]]
    answer: str
    chars: int
    chars_without_spaces: int
    answer_updated_at: datetime | None
    answer_asset: LinkedAnswerOut | None
    stale: bool
    stale_reasons: list[str]


class InterviewPrepOut(BaseModel):
    application_id: UUID
    status: str
    mode: str | None
    generated_at: datetime | None
    submission_id: UUID | None
    current_submission_id: UUID | None
    stale: bool
    stale_reasons: list[str]
    questions: list[QuestionOut]
    answered: int
    notes: list[str]
    model_call_id: UUID | None


class GenerateIn(BaseModel):
    use_model: bool = True


class AddQuestionIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class AnswerIn(BaseModel):
    answer: str = Field(max_length=uc.MAX_ANSWER_CHARS * 2)


class LinkIn(BaseModel):
    answer_id: UUID


class SaveAnswerIn(BaseModel):
    title: str | None = Field(default=None, max_length=400)
    kind: CommonKind | None = None


class CreatedOut(BaseModel):
    id: UUID


class FeedbackIn(BaseModel):
    instruction: str | None = Field(default=None, max_length=1000)


class FeedbackOut(BaseModel):
    question_id: UUID
    points: list[dict[str, Any]]
    dropped: int
    model_call_id: UUID | None


def add_interview_prep_routes(router: APIRouter, deps, authorize) -> None:
    db = deps.db
    reader = Annotated[ActorContext, Depends(authorize("interview_prep.read"))]
    writer = Annotated[ActorContext, Depends(authorize("interview_prep.write"))]
    ai = Annotated[ActorContext, Depends(authorize("interview_prep.ai"))]
    base = "/applications/{application_id}/interview-prep"
    one = base + "/questions/{question_id}"

    async def view(actor: ActorContext, application_id: UUID) -> InterviewPrepOut:
        return InterviewPrepOut.model_validate(
            await uc.get(db, actor, application_id), from_attributes=True
        )

    @router.get(base, response_model=InterviewPrepOut)
    async def get_prep(actor: reader, application_id: UUID) -> InterviewPrepOut:
        try:
            return await view(actor, application_id)
        except NotFound as exc:
            raise _not_found() from exc

    @router.post(base, response_model=InterviewPrepOut)
    async def generate(actor: writer, application_id: UUID, body: GenerateIn) -> InterviewPrepOut:
        # 모델에 보내는 것은 interview_prep.ai(egress) 권한이 있을 때만. 없으면 규칙 질문으로 만든다.
        allowed = body.use_model and deps.permissions.allows(actor, "interview_prep.ai")
        try:
            res = await uc.generate(db, deps.models, actor, application_id, use_model=allowed)
        except NotFound as exc:
            raise _not_found() from exc
        return InterviewPrepOut.model_validate(res, from_attributes=True)

    @router.post(base + "/questions", response_model=CreatedOut, status_code=201)
    async def add_question(actor: writer, application_id: UUID, body: AddQuestionIn) -> CreatedOut:
        try:
            return CreatedOut(id=await uc.add_question(db, actor, application_id, body.question))
        except NotFound as exc:
            raise _not_found() from exc
        except uc.InterviewPrepError as exc:
            raise _error(exc) from exc

    @router.delete(one, status_code=204)
    async def delete_question(actor: writer, application_id: UUID, question_id: UUID) -> Response:
        try:
            await uc.delete_question(db, actor, application_id, question_id)
        except NotFound as exc:
            raise _not_found() from exc
        return Response(status_code=204)

    @router.put(one + "/answer", status_code=204)
    async def set_answer(
        actor: writer, application_id: UUID, question_id: UUID, body: AnswerIn
    ) -> Response:
        try:
            await uc.set_answer(db, actor, application_id, question_id, body.answer)
        except NotFound as exc:
            raise _not_found() from exc
        except uc.InterviewPrepError as exc:
            raise _error(exc) from exc
        return Response(status_code=204)

    @router.put(one + "/answer-asset", status_code=204)
    async def link(
        actor: writer, application_id: UUID, question_id: UUID, body: LinkIn
    ) -> Response:
        try:
            await uc.link_answer_asset(db, actor, application_id, question_id, body.answer_id)
        except NotFound as exc:
            raise _not_found() from exc
        return Response(status_code=204)

    @router.delete(one + "/answer-asset", status_code=204)
    async def unlink(actor: writer, application_id: UUID, question_id: UUID) -> Response:
        try:
            await uc.unlink_answer_asset(db, actor, application_id, question_id)
        except NotFound as exc:
            raise _not_found() from exc
        return Response(status_code=204)

    @router.post(one + "/save-answer", response_model=CreatedOut, status_code=201)
    async def save_answer(
        actor: writer, application_id: UUID, question_id: UUID, body: SaveAnswerIn
    ) -> CreatedOut:
        # 공통 답변을 만드는 일이라 answers.write도 있어야 한다 (지금 역할에서는 같은 범위)
        if not deps.permissions.allows(actor, "answers.write"):
            raise HTTPException(status_code=403, detail="forbidden")
        try:
            new_id = await uc.save_as_answer_asset(
                db, actor, application_id, question_id, title=body.title, kind=body.kind
            )
        except NotFound as exc:
            raise _not_found() from exc
        except (uc.InterviewPrepError, AnswerRuleError) as exc:
            raise _error(exc) from exc
        return CreatedOut(id=new_id)

    @router.post(one + "/feedback", response_model=FeedbackOut)
    async def feedback(
        actor: ai, application_id: UUID, question_id: UUID, body: FeedbackIn
    ) -> FeedbackOut:
        try:
            res = await uc.answer_feedback(
                db, deps.models, actor, application_id, question_id, instruction=body.instruction
            )
        except NotFound as exc:
            raise _not_found() from exc
        except uc.InterviewPrepError as exc:
            raise _error(exc) from exc
        except ModelDenied as exc:
            raise HTTPException(status_code=409, detail=exc.code) from exc
        except ModelCallError as exc:
            raise HTTPException(status_code=502, detail=exc.code) from exc
        return FeedbackOut.model_validate(res, from_attributes=True)

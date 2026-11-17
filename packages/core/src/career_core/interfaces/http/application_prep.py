"""공고별 지원 준비 문서 경로 (O15, plan §19.3). ``/applications/{application_id}/prep``.

- ``GET``: 준비 문서 (없으면 ``status = none``). 공고·경험이 바뀌었으면 ``stale``.
- ``POST``: (다시) 만들기. 편집 권한. 모델은 ``documents.ai`` 권한이 있고 게이트웨이가 있을 때만 쓰고,
  아니면 검색 순위(``mode = search``)로 만든다. ``use_model = false``로 모델 없이 만들 수도 있다.
- ``POST .../questions/{document_id}/choose``: 이 경험으로 쓰기. 고른 경험을 기록할 뿐 글을 넣지 않는다.
- ``DELETE .../questions/{document_id}/choose/{experience_id}``: 고른 것 취소.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from career_core.domain.documents import DocumentRuleError
from career_core.ports import ActorContext
from career_core.usecases import application_prep as uc
from career_core.usecases.brain import NotFound

_RULE_STATUS = {
    "application_locked": status.HTTP_409_CONFLICT,
    "prep_not_ready": status.HTTP_409_CONFLICT,
    "not_a_candidate": 422,
}


def _error(exc: DocumentRuleError) -> HTTPException:
    return HTTPException(status_code=_RULE_STATUS.get(exc.code, 422), detail=exc.code)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


class PrepOut(BaseModel):
    application_id: UUID
    status: str
    mode: str | None
    generated_at: datetime | None
    stale: bool
    stale_reasons: list[str]
    stale_experiences: list[str]
    questions: list[dict[str, Any]]
    requirements: list[dict[str, Any]]
    company: dict[str, Any] | None
    choices: dict[str, Any]
    overlaps: list[dict[str, Any]]
    notes: list[str]
    model_call_id: UUID | None
    new_questions: list[str]


class GenerateIn(BaseModel):
    use_model: bool = True


class ChooseIn(BaseModel):
    experience_id: UUID
    claim_ids: list[str] | None = Field(default=None, max_length=20)


class ChoiceOut(BaseModel):
    document_id: UUID
    experience_id: UUID
    claim_ids: list[str]


def add_prep_routes(router: APIRouter, deps, authorize) -> None:
    db = deps.db
    reader = Annotated[ActorContext, Depends(authorize("applications.read"))]
    writer = Annotated[ActorContext, Depends(authorize("applications.write"))]
    base = "/applications/{application_id}/prep"

    @router.get(base, response_model=PrepOut)
    async def get_prep(actor: reader, application_id: UUID) -> PrepOut:
        try:
            view = await uc.get_prep(db, actor, application_id)
        except NotFound as exc:
            raise _not_found() from exc
        return PrepOut.model_validate(view, from_attributes=True)

    @router.post(base, response_model=PrepOut)
    async def generate(actor: writer, application_id: UUID, body: GenerateIn) -> PrepOut:
        # 모델에 보내는 것은 documents.ai(egress) 권한이 있을 때만. 없으면 검색 순위로 만든다.
        allowed = body.use_model and deps.permissions.allows(actor, "documents.ai")
        try:
            view = await uc.generate_prep(db, deps.models, actor, application_id, use_model=allowed)
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        return PrepOut.model_validate(view, from_attributes=True)

    @router.post(base + "/questions/{document_id}/choose", response_model=ChoiceOut)
    async def choose(
        actor: writer, application_id: UUID, document_id: UUID, body: ChooseIn
    ) -> ChoiceOut:
        try:
            res = await uc.choose_experience(
                db,
                actor,
                application_id,
                document_id,
                body.experience_id,
                claim_ids=body.claim_ids,
            )
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        return ChoiceOut.model_validate(res, from_attributes=True)

    @router.delete(base + "/questions/{document_id}/choose/{experience_id}", status_code=204)
    async def unchoose(
        actor: writer, application_id: UUID, document_id: UUID, experience_id: UUID
    ) -> Response:
        try:
            await uc.unchoose_experience(db, actor, application_id, document_id, experience_id)
        except NotFound as exc:
            raise _not_found() from exc
        except DocumentRuleError as exc:
            raise _error(exc) from exc
        return Response(status_code=204)

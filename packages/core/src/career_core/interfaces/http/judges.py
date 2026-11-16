"""판정 모델 경로 (참고 의견): 지원 문서의 요건 가시성.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from career_core.interfaces.http.study import JudgeRunOut, JudgeVerdictOut
from career_core.ports import ActorContext
from career_core.usecases import judges as uc
from career_core.usecases.brain import NotFound


class VisibilityJudgeIn(BaseModel):
    # 없으면 지원의 모든 문서를 합쳐 본다
    document_id: UUID | None = None


class VisibilityRowOut(BaseModel):
    kind: str
    requirement: str
    baseline_verdict: str
    document_terms: list[str]
    judge: JudgeVerdictOut | None
    agrees: bool | None


class VisibilityJudgeOut(BaseModel):
    application_id: UUID
    document_ids: list[UUID]
    rows: list[VisibilityRowOut]
    judge: JudgeRunOut
    blocks_withheld: int
    basis: str


def add_judge_routes(router: APIRouter, deps, authorize) -> None:
    # 문서 글을 외부 모델로 보내므로 documents.ai(egress) 범위
    judge_actor = Annotated[ActorContext, Depends(authorize("documents.ai"))]

    @router.post(
        "/applications/{application_id}/visibility-judge", response_model=VisibilityJudgeOut
    )
    async def visibility_judge(
        actor: judge_actor, application_id: UUID, body: VisibilityJudgeIn | None = None
    ) -> VisibilityJudgeOut:
        """요건 가시성: 단어 일치 기준선 + 판정 모델 의견. 저장하지 않고 검사 판정도 바꾸지 않는다."""
        body = body or VisibilityJudgeIn()
        try:
            result = await uc.judge_application_visibility(
                deps.db, deps.models, actor, application_id, document_id=body.document_id
            )
        except NotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found") from exc
        return VisibilityJudgeOut.model_validate(result, from_attributes=True)

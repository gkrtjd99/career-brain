"""판정 모델 경로 (참고 의견): 지원 문서의 요건 가시성, 인재상 맞춤 확인.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from career_core.interfaces.http.study import JudgeRunOut, JudgeVerdictOut
from career_core.ports import ActorContext
from career_core.usecases import judges as uc
from career_core.usecases import talent_fit as talent_uc
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


class JudgeCacheOut(BaseModel):
    entries: int
    hits: int
    saved_usd: str | None
    days: int  # 보관 일수 (CAREER_JUDGE_CACHE_DAYS, 0 = 캐시 안 씀)


class JudgeCacheClearedOut(BaseModel):
    deleted: int


class VisibilityJudgeOut(BaseModel):
    application_id: UUID
    document_ids: list[UUID]
    rows: list[VisibilityRowOut]
    judge: JudgeRunOut
    blocks_withheld: int
    basis: str


class TalentFitIn(BaseModel):
    # 없으면 지원의 문항 문서마다 본다
    document_id: UUID | None = None


class TraitFitOut(BaseModel):
    document_id: UUID
    level: str  # absent | claimed | shown
    score: float  # 0~2
    confidence: float | None


class TraitRowOut(BaseModel):
    text: str
    source: str  # posting | company
    confirmed: bool
    fact_id: UUID | None
    fits: list[TraitFitOut]
    best: str | None


class TalentFitOut(BaseModel):
    application_id: UUID
    document_ids: list[UUID]
    traits: list[TraitRowOut]
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
        """요건 가시성: 단어 일치 기준선 + 판정 모델 의견. 문서에 저장하지 않고 검사 판정도 바꾸지 않는다.

        같은 요건·같은 글이면 응답 캐시(c0022)에서 꺼낸다 (`judge.cached`)."""
        body = body or VisibilityJudgeIn()
        try:
            result = await uc.judge_application_visibility(
                deps.db, deps.models, actor, application_id, document_id=body.document_id
            )
        except NotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found") from exc
        return VisibilityJudgeOut.model_validate(result, from_attributes=True)

    @router.post("/applications/{application_id}/talent-fit", response_model=TalentFitOut)
    async def talent_fit(
        actor: judge_actor, application_id: UUID, body: TalentFitIn | None = None
    ) -> TalentFitOut:
        """인재상 맞춤 확인: 인재상 줄마다 "이 글이 한 일로 보여 주는가"를 판정 모델(Jev)에 묻는다.

        참고 의견이다. 문서·검사 결과에 저장하지 않는다."""
        body = body or TalentFitIn()
        try:
            result = await talent_uc.judge_talent_fit(
                deps.db, deps.models, actor, application_id, document_id=body.document_id
            )
        except NotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found") from exc
        return TalentFitOut.model_validate(result, from_attributes=True)

    @router.get("/judges/cache", response_model=JudgeCacheOut)
    async def judge_cache(actor: judge_actor) -> JudgeCacheOut:
        """판정 응답 캐시(c0022) 크기와 꺼내 쓴 횟수. 같은 질문은 다시 부르지 않는다."""
        stats = await uc.cache_stats(deps.db, actor)
        return JudgeCacheOut(
            entries=stats.entries, hits=stats.hits, saved_usd=stats.saved_usd, days=uc.cache_days()
        )

    @router.delete("/judges/cache", response_model=JudgeCacheClearedOut)
    async def clear_judge_cache(actor: judge_actor) -> JudgeCacheClearedOut:
        """이 공간의 판정 응답 캐시를 지운다 (다음 판정은 모델을 다시 부른다)."""
        return JudgeCacheClearedOut(deleted=await uc.clear_cache(deps.db, actor))

"""지원서 초안 경로 (plan §22). ``/applications/{application_id}/draft``.

- ``POST``: 초안 쓰기 실행(workflow ``draft_application``)을 시작한다. 본문: 문항(``document_ids``, 없으면 모든
  문항), 요청 글(``instruction``), ``overwrite``(기본 false: 글이 없는 문항만). 시작 권한은 ``documents.ai``
  (egress), 문서에 쓰는 것은 ``applications.write``. 실행은 ``GET /core/runs/{id}``로 본다.
- ``POST .../estimate``: 같은 본문으로 시작 전 참고 비용 (``/core/runs/workflows/draft_application/estimate``와 같다).
- ``GET``: 문서마다 지금 글에 기댄 사실(인용·원본), 쓴 경험, 확인 전 사실에 기댄 문장, 마지막 초안 실행 ID.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from career_core.domain.documents import DocumentRuleError
from career_core.domain.runs import RunRuleError
from career_core.interfaces.http.runs import RunEstimateOut, RunOut, run_error
from career_core.ports import ActorContext
from career_core.usecases import application_draft as uc
from career_core.usecases import run_estimates as estimate_uc
from career_core.usecases import runs as run_uc
from career_core.usecases.brain import NotFound


class DraftIn(BaseModel):
    document_ids: list[UUID] | None = Field(default=None, max_length=30)
    instruction: str | None = Field(default=None, max_length=500)
    overwrite: bool = False
    idempotency_key: str | None = Field(default=None, max_length=200)


class DraftDocumentOut(BaseModel):
    document_id: UUID
    revision_no: int
    drafted: bool
    claims: list[dict[str, Any]]
    experiences: list[dict[str, Any]]
    unconfirmed_sentences: list[dict[str, Any]]


class DraftOut(BaseModel):
    application_id: UUID
    documents: list[DraftDocumentOut]
    unconfirmed_total: int
    run_id: UUID | None


def _input(application_id: UUID, body: DraftIn) -> dict[str, Any]:
    return {
        "application_id": str(application_id),
        "document_ids": [str(d) for d in body.document_ids] if body.document_ids else None,
        "instruction": body.instruction,
        "overwrite": body.overwrite,
    }


def add_draft_routes(router: APIRouter, deps, authorize) -> None:
    db = deps.db
    run_deps = run_uc.RunDeps(db=db, gateway=deps.models, permissions=deps.permissions)
    reader = Annotated[ActorContext, Depends(authorize("applications.read"))]
    writer = Annotated[ActorContext, Depends(authorize("applications.write"))]
    base = "/applications/{application_id}/draft"

    @router.get(base, response_model=DraftOut)
    async def view(actor: reader, application_id: UUID) -> DraftOut:
        try:
            v = await uc.draft_view(db, actor, application_id)
        except NotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found") from exc
        return DraftOut.model_validate(v, from_attributes=True)

    @router.post(base, response_model=RunOut, status_code=202)
    async def start(actor: writer, application_id: UUID, body: DraftIn) -> RunOut:
        try:
            v = await run_uc.start_run(
                run_deps,
                actor,
                uc.DRAFT_WORKFLOW,
                _input(application_id, body),
                idempotency_key=body.idempotency_key,
            )
        except RunRuleError as exc:
            raise run_error(exc) from exc
        return RunOut.model_validate(v, from_attributes=True)

    @router.post(base + "/estimate", response_model=RunEstimateOut)
    async def estimate(actor: reader, application_id: UUID, body: DraftIn) -> RunEstimateOut:
        wf = run_deps.registry()[uc.DRAFT_WORKFLOW]
        try:
            clean = wf.validate(_input(application_id, body))
            sizes = await wf.input_sizes(db, actor, clean)
            est = await estimate_uc.estimate_run(db, actor, wf.name, len(sizes), sizes=sizes)
        except RunRuleError as exc:
            raise run_error(exc) from exc
        except (NotFound, DocumentRuleError) as exc:
            raise HTTPException(status_code=422, detail="invalid_input") from exc
        return RunEstimateOut.model_validate(est, from_attributes=True)

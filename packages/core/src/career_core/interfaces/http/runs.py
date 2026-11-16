"""실행·승인 경로 (O08).

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from career_core.domain.runs import ApprovalError, RunRuleError
from career_core.ports import ActorContext
from career_core.usecases import approvals as approval_uc
from career_core.usecases import run_estimates as estimate_uc
from career_core.usecases import runs as run_uc
from career_core.usecases.brain import NotFound

_RUN_STATUS = {
    "forbidden": status.HTTP_403_FORBIDDEN,
    "unknown_workflow": status.HTTP_404_NOT_FOUND,
    "run_finished": status.HTTP_409_CONFLICT,
    "run_not_active": status.HTTP_409_CONFLICT,
    "run_not_interrupted": status.HTTP_409_CONFLICT,
    "idempotency_key_reused": status.HTTP_409_CONFLICT,
}
_APPROVAL_STATUS = {
    "forbidden": status.HTTP_403_FORBIDDEN,
    "human_approval_required": status.HTTP_403_FORBIDDEN,
    "unknown_action": status.HTTP_404_NOT_FOUND,
    "approval_not_pending": status.HTTP_409_CONFLICT,
    "approval_expired": status.HTTP_409_CONFLICT,
    "target_changed": status.HTTP_409_CONFLICT,
    "payload_changed": status.HTTP_409_CONFLICT,
    "payload_mismatch": status.HTTP_409_CONFLICT,
    "idempotency_key_reused": status.HTTP_409_CONFLICT,
}


def run_error(exc: RunRuleError) -> HTTPException:
    return HTTPException(status_code=_RUN_STATUS.get(exc.code, 422), detail=exc.code)


def approval_error(exc: ApprovalError) -> HTTPException:
    code = _APPROVAL_STATUS.get(exc.code)
    if code is None and exc.code.startswith("approval_"):
        code = status.HTTP_409_CONFLICT
    return HTTPException(status_code=code or 422, detail=exc.code)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


class RunIn(BaseModel):
    workflow: str = Field(max_length=100)
    input: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=200)


class RunOut(BaseModel):
    id: UUID
    workflow: str
    status: str
    step: int
    steps_total: int | None
    position: dict
    result: dict | None
    stop_requested: str | None
    wait_reason: str | None
    retry_at: datetime | None
    approval_id: UUID | None
    error_code: str | None
    cost_usd: str
    cost_unknown: bool
    model_calls: int
    resumes: int
    actor_kind: str
    created_by: UUID
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class RunEventOut(BaseModel):
    seq: int
    kind: str
    status: str | None
    step: int | None
    code: str | None
    data: dict
    created_at: datetime


class WorkflowOut(BaseModel):
    name: str
    action: str
    steps: int
    description: str


class RunEstimateOut(BaseModel):
    workflow: str
    units: int
    calls: int
    per_call_usd: str | None
    estimate_usd: str | None
    based_on_runs: int
    based_on_calls: int
    cost_unknown: bool
    basis: str


class ApprovalIn(BaseModel):
    action: str = Field(max_length=100)
    target_id: UUID
    payload: dict[str, Any]
    # 요청자가 본 내용의 해시 (선택). 서버가 계산한 값과 다르면 409
    payload_hash: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=1000)
    expires_in_minutes: int = Field(default=24 * 60, ge=1, le=7 * 24 * 60)
    idempotency_key: str | None = Field(default=None, max_length=200)


class DecisionIn(BaseModel):
    # 승인 화면에 보여준 내용의 해시. 보낸 것과 저장된 것이 다르면 승인하지 않는다.
    payload_hash: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=1000)


class ApprovalOut(BaseModel):
    id: UUID
    action: str
    target_kind: str
    target_id: UUID
    target_version: str
    payload_hash: str
    payload: dict
    summary: dict
    status: str
    reason: str | None
    requested_by: UUID
    requested_via: str
    run_id: UUID | None
    expires_at: datetime
    decided_by: UUID | None
    decided_at: datetime | None
    decision_note: str | None
    used_at: datetime | None
    used_result_id: UUID | None
    invalid_reason: str | None
    created_at: datetime


class SummaryChangeOut(BaseModel):
    path: str
    kind: str
    before: Any = None
    after: Any = None
    label: str | None = None


class ApprovalDiffOut(BaseModel):
    approval: ApprovalOut
    requested: dict
    current: dict | None
    target_changed: bool
    changes: list[SummaryChangeOut]


def _run(v) -> RunOut:
    return RunOut.model_validate(v, from_attributes=True)


def _approval(v) -> ApprovalOut:
    return ApprovalOut.model_validate(v, from_attributes=True)


def add_run_routes(router: APIRouter, deps, authorize) -> None:
    db = deps.db
    run_deps = run_uc.RunDeps(db=db, gateway=deps.models, permissions=deps.permissions)
    reader = Annotated[ActorContext, Depends(authorize("runs.read"))]
    starter = Annotated[ActorContext, Depends(authorize("runs.start"))]
    controller = Annotated[ActorContext, Depends(authorize("runs.control"))]

    async def call(fn, *args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except NotFound as exc:
            raise _not_found() from exc
        except RunRuleError as exc:
            raise run_error(exc) from exc

    @router.get("/runs/workflows", response_model=list[WorkflowOut])
    async def workflows(actor: reader) -> list[WorkflowOut]:
        return [
            WorkflowOut(name=w.name, action=w.action, steps=len(w.steps), description=w.description)
            for w in run_deps.registry().values()
        ]

    @router.get("/runs/workflows/{workflow}/estimate", response_model=RunEstimateOut)
    async def estimate(
        actor: reader, workflow: str, units: Annotated[int, Query(ge=1, le=1000)] = 1
    ) -> RunEstimateOut:
        """c0023 시작 전 참고 비용: 이 공간의 같은 workflow 지난 실행의 호출 1회 평균 × 계획한 호출 수."""
        if workflow not in run_deps.registry():
            raise run_error(RunRuleError("unknown_workflow"))
        est = await call(estimate_uc.estimate_run, db, actor, workflow, units)
        return RunEstimateOut.model_validate(est, from_attributes=True)

    @router.post("/runs", response_model=RunOut, status_code=202)
    async def start(actor: starter, body: RunIn) -> RunOut:
        view = await call(
            run_uc.start_run,
            run_deps,
            actor,
            body.workflow,
            body.input,
            idempotency_key=body.idempotency_key,
        )
        return _run(view)

    @router.get("/runs", response_model=list[RunOut])
    async def index(
        actor: reader,
        status_filter: Annotated[str | None, Query(alias="status")] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> list[RunOut]:
        return [
            _run(v)
            for v in await call(run_uc.list_runs, db, actor, status=status_filter, limit=limit)
        ]

    @router.get("/runs/{run_id}", response_model=RunOut)
    async def detail(actor: reader, run_id: UUID) -> RunOut:
        return _run(await call(run_uc.get_run, db, actor, run_id))

    @router.get("/runs/{run_id}/events", response_model=list[RunEventOut])
    async def events(
        actor: reader, run_id: UUID, after: Annotated[int, Query(ge=0)] = 0
    ) -> list[RunEventOut]:
        rows = await call(run_uc.run_events, db, actor, run_id, after=after)
        return [RunEventOut.model_validate(e, from_attributes=True) for e in rows]

    @router.post("/runs/{run_id}/cancel", response_model=RunOut)
    async def cancel(actor: controller, run_id: UUID) -> RunOut:
        return _run(await call(run_uc.cancel_run, db, actor, run_id))

    @router.post("/runs/{run_id}/interrupt", response_model=RunOut)
    async def interrupt(actor: controller, run_id: UUID) -> RunOut:
        return _run(await call(run_uc.interrupt_run, db, actor, run_id))

    @router.post("/runs/{run_id}/resume", response_model=RunOut, status_code=202)
    async def resume(actor: controller, run_id: UUID) -> RunOut:
        return _run(await call(run_uc.resume_run, db, actor, run_id))


def add_approval_routes(router: APIRouter, deps, authorize) -> None:
    db = deps.db
    reader = Annotated[ActorContext, Depends(authorize("approvals.read"))]
    requester = Annotated[ActorContext, Depends(authorize("approvals.request"))]
    decider = Annotated[ActorContext, Depends(authorize("approvals.decide"))]

    async def call(fn, *args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except NotFound as exc:
            raise _not_found() from exc
        except ApprovalError as exc:
            raise approval_error(exc) from exc

    @router.post("/approvals", response_model=ApprovalOut, status_code=201)
    async def request(actor: requester, body: ApprovalIn) -> ApprovalOut:
        view = await call(
            approval_uc.request_approval,
            db,
            actor,
            action=body.action,
            target_id=body.target_id,
            payload=body.payload,
            payload_hash=body.payload_hash,
            reason=body.reason,
            expires_in=timedelta(minutes=body.expires_in_minutes),
            idempotency_key=body.idempotency_key,
            permissions=deps.permissions,
        )
        return _approval(view)

    @router.get("/approvals", response_model=list[ApprovalOut])
    async def index(
        actor: reader,
        status_filter: Annotated[str | None, Query(alias="status")] = "pending",
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[ApprovalOut]:
        rows = await call(
            approval_uc.list_approvals, db, actor, status=status_filter or None, limit=limit
        )
        return [_approval(v) for v in rows]

    @router.get("/approvals/{approval_id}", response_model=ApprovalOut)
    async def detail(actor: reader, approval_id: UUID) -> ApprovalOut:
        return _approval(await call(approval_uc.get_approval, db, actor, approval_id))

    @router.get("/approvals/{approval_id}/diff", response_model=ApprovalDiffOut)
    async def diff(actor: reader, approval_id: UUID) -> ApprovalDiffOut:
        """승인 화면의 변경 비교: 요청 때 요약과 지금 대상의 요약."""
        d = await call(approval_uc.approval_diff, db, actor, approval_id)
        return ApprovalDiffOut(
            approval=_approval(d.approval),
            requested=d.requested,
            current=d.current,
            target_changed=d.target_changed,
            changes=[SummaryChangeOut.model_validate(c, from_attributes=True) for c in d.changes],
        )

    @router.post("/approvals/{approval_id}/approve", response_model=ApprovalOut)
    async def approve(actor: decider, approval_id: UUID, body: DecisionIn) -> ApprovalOut:
        view = await call(
            approval_uc.decide_approval,
            db,
            actor,
            approval_id,
            approve=True,
            payload_hash=body.payload_hash,
            note=body.note,
            permissions=deps.permissions,
        )
        return _approval(view)

    @router.post("/approvals/{approval_id}/reject", response_model=ApprovalOut)
    async def reject(actor: decider, approval_id: UUID, body: DecisionIn) -> ApprovalOut:
        view = await call(
            approval_uc.decide_approval,
            db,
            actor,
            approval_id,
            approve=False,
            payload_hash=body.payload_hash,
            note=body.note,
            permissions=deps.permissions,
        )
        return _approval(view)


__all__ = ["add_approval_routes", "add_run_routes", "approval_error", "run_error"]

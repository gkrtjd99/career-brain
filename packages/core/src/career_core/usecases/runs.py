"""실행(run): 오래 걸리는 AI·여러 단계 작업의 상태·사건·비용·재개 위치 (O08).

- 실행 하나 = ``core.runs`` 1행 + 작업 큐(``core.jobs``, kind ``run``) 1건. worker가 단계를 차례로 돈다.
- 단계가 끝날 때마다 재개 위치(``step``, ``position``)를 같은 트랜잭션에 저장한다. worker가 단계 도중에
  죽으면 lease가 지난 뒤 다른 worker가 그 단계부터 다시 한다. 그래서 단계의 부수효과는 멱등이어야 한다
  (멱등 키 ``ctx.effect_key(...)``, 정규화 URL 중복 방지처럼 usecase가 이미 가진 규칙).
- 취소·중단은 단계 사이에서 반영한다. 실행 중인 단계를 강제로 끊지 않는다.
- 외부 한도(rate limit)는 같은 엔드포인트로 기다렸다가 다시 시도하고, 사유(``wait_reason``)를 보여준다.
  다른 제공자·유료 경로로 바꾸지 않는다. 기다려도 안 풀리는 한도·예산은 사유를 남기고 실패한다.
- worker는 시작한 사람의 범위를 그대로 믿지 않는다. 매 단계 현재 역할의 범위와 시작 당시 범위의
  교집합을 쓴다 (역할이 줄면 실행도 줄어든 범위로, 멤버가 아니면 실패).
- 비용(c0016): ``StepContext.model``은 장부(``model_calls``) 행에 실행 ID·단계를 붙인다. 실행 비용은
  단계를 저장할 때마다 그 장부 행에서 다시 계산한다(``cost_from_ledger``). 단계 도중 worker가 죽어
  그 시도의 비용이 실행에 더해지지 못했어도, 재개 때(그리고 다음 저장 때) 장부에서 맞춰진다.
- lease 소유 확인(c0023): worker가 실행 행에 쓰는 트랜잭션은 먼저 작업 행을 잠그고 이 worker가 아직
  lease를 쥐고 있는지 본다(``jobs.lease.guard_lease``: 같은 ``attempts``·``lease_token``, 안 지난
  ``locked_until``). lease를 잃은 옛 worker의 단계 결과(위치·결과·상태·사건)는 버려진다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from career_core.db import Database, Job, Membership, ModelCall
from career_core.db.base import utcnow
from career_core.db.models import Run, RunEvent
from career_core.domain.runs import (
    FAIL_ERROR_CODES,
    RUN_JOB,
    TERMINAL_RUN_STATUSES,
    WAIT_ERROR_CODES,
    RunEventKind,
    RunRuleError,
    RunStatus,
    StopRequest,
    wait_seconds,
)
from career_core.domain.workspace import ScopePermissionPolicy, scopes_for_role
from career_core.jobs.lease import guard_lease
from career_core.llm.types import ModelCallError, ModelDenied, ModelRequest, ModelResult
from career_core.ports import ActorContext, PermissionPolicy
from career_core.usecases.brain import NotFound
from career_core.usecases.parsing import StepOutcome

MAX_IDEMPOTENCY_KEY = 200
LIST_LIMIT = 100


# ---------------------------------------------------------------- 단계 정의


class StepFailed(Exception):
    """단계가 더 진행할 수 없음. ``code``가 실행의 실패 사유가 된다."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class StepResult:
    # run.position에 합칠 값 (ID·번호·짧은 결과만)
    position: dict[str, Any] | None = None
    # 같은 단계를 한 번 더 (목록을 하나씩 처리하는 단계)
    repeat: bool = False
    # 작업 전체를 여기서 끝낸다
    done: bool = False
    result: dict[str, Any] | None = None
    # 이 승인이 결정될 때까지 멈춘다 (결정되면 다음 단계부터)
    wait_approval: UUID | None = None


@dataclass(slots=True)
class StepContext:
    deps: RunDeps
    actor: ActorContext
    run_id: UUID
    input: dict[str, Any]
    position: dict[str, Any]
    step: int
    cost_usd: Decimal = Decimal(0)
    cost_unknown: bool = False
    model_calls: int = 0

    def effect_key(self, name: str) -> str:
        """부수효과 멱등 키. 같은 실행·같은 이름이면 재개 후에도 같다."""
        return f"run:{self.run_id}:{name}"

    async def model(self, purpose: str, request: ModelRequest) -> ModelResult:
        """게이트웨이를 거친 모델 호출. 비용은 실행에 쌓인다 (실패·대기여도)."""
        gateway = self.deps.gateway
        if gateway is None:
            raise StepFailed("no_model_gateway")
        # 장부 행에 실행·단계를 남긴다: 이 단계가 끝나기 전에 worker가 죽어도 재개 때 비용을 맞춘다
        result = await gateway.run(
            self.actor, purpose, request, run_id=self.run_id, run_step=self.step
        )
        self.model_calls += 1
        if result.cost_usd is None:
            self.cost_unknown = self.cost_unknown or result.cost_basis == "unknown"
        elif result.cost_basis not in ("list_price_equivalent", "none"):
            # 구독 프록시의 정가 환산액은 실지출이 아니다
            self.cost_usd += Decimal(result.cost_usd)
        return result


Step = Callable[[StepContext], Awaitable[StepResult]]


@dataclass(frozen=True, slots=True)
class Workflow:
    name: str
    # 시작에 필요한 행동 (ACTION_SCOPES). HTTP·MCP 모두 같은 PermissionPolicy로 검사한다.
    action: str
    steps: tuple[Step, ...]
    validate: Callable[[dict[str, Any]], dict[str, Any]]
    description: str = ""
    # 시작 전 예상 비용용 단위별 입력 글자 수 (검증한 입력 → 단위마다 하나, 계획 단계가 ``position.chars``에
    # 같은 값을 남긴다). 없으면 예상은 호출 1회 평균만 쓴다 (``usecases/run_estimates.py``)
    input_sizes: Callable[[Database, ActorContext, dict[str, Any]], Awaitable[list[int]]] | None = (
        None
    )


@dataclass(frozen=True, slots=True)
class RunDeps:
    db: Database
    gateway: Any = None  # ModelGateway | None
    # 없으면 기본 workflow (career_core.workflows.builtin.WORKFLOWS)
    workflows: Mapping[str, Workflow] | None = None
    permissions: PermissionPolicy = field(default_factory=ScopePermissionPolicy)
    # 외부 한도 대기: base·2^n초, max_waits번 기다린 뒤에도 안 되면 실패
    wait_base_seconds: float = 30.0
    max_waits: int = 6

    def registry(self) -> Mapping[str, Workflow]:
        if self.workflows is not None:
            return self.workflows
        from career_core.workflows.builtin import WORKFLOWS

        return WORKFLOWS


# ---------------------------------------------------------------- 보기


@dataclass(frozen=True, slots=True)
class RunView:
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


@dataclass(frozen=True, slots=True)
class RunEventView:
    seq: int
    kind: str
    status: str | None
    step: int | None
    code: str | None
    data: dict
    created_at: datetime


def _view(r: Run) -> RunView:
    return RunView(
        r.id,
        r.workflow,
        r.status,
        r.step,
        r.steps_total,
        r.position,
        r.result,
        r.stop_requested,
        r.wait_reason,
        r.retry_at,
        r.approval_id,
        r.error_code,
        str(r.cost_usd),
        r.cost_unknown,
        r.model_calls,
        r.resumes,
        r.actor_kind,
        r.created_by,
        r.created_at,
        r.started_at,
        r.finished_at,
        r.updated_at,
    )


# ---------------------------------------------------------------- 공통 도우미


async def add_event(
    s: AsyncSession,
    run: Run,
    kind: RunEventKind,
    *,
    code: str | None = None,
    data: dict | None = None,
) -> None:
    """실행 행을 잠근 트랜잭션 안에서만 부른다 (seq 경합 방지)."""
    seq = await s.scalar(
        select(func.coalesce(func.max(RunEvent.seq), 0)).where(RunEvent.run_id == run.id)
    )
    s.add(
        RunEvent(
            id=uuid4(),
            workspace_id=run.workspace_id,
            run_id=run.id,
            seq=seq + 1,
            kind=kind.value,
            status=run.status,
            step=run.step,
            code=code,
            data=data or {},
        )
    )


async def _lock(s: AsyncSession, run_id: UUID) -> Run | None:
    return await s.scalar(select(Run).where(Run.id == run_id).with_for_update())


def _enqueue(s: AsyncSession, run: Run, *, delay: float = 0) -> None:
    s.add(
        Job(
            workspace_id=run.workspace_id,
            kind=RUN_JOB,
            target_id=run.id,
            available_at=utcnow() + timedelta(seconds=delay),
        )
    )


async def _finish(s: AsyncSession, run: Run, status: RunStatus, *, code: str | None = None) -> None:
    run.status = status.value
    run.finished_at = utcnow()
    run.stop_requested = None
    run.wait_reason = None
    run.retry_at = None
    if status == RunStatus.FAILED:
        run.error_code = code or "failed"
    await add_event(
        s, run, RunEventKind.ERROR if status == RunStatus.FAILED else RunEventKind.STATUS, code=code
    )


async def wake_run(s: AsyncSession, run_id: UUID, approval_id: UUID) -> bool:
    """승인이 결정되면 그 승인을 기다리던 실행을 다시 큐에 넣는다 (같은 트랜잭션)."""
    run = await _lock(s, run_id)
    if run is None or run.status != RunStatus.WAITING_APPROVAL or run.approval_id != approval_id:
        return False
    run.status = RunStatus.QUEUED.value
    await add_event(s, run, RunEventKind.STATUS, code="approval_decided")
    _enqueue(s, run)
    return True


# ---------------------------------------------------------------- 시작·조회·제어


async def start_run(
    deps: RunDeps,
    actor: ActorContext,
    workflow: str,
    input: dict[str, Any] | None = None,
    *,
    idempotency_key: str | None = None,
) -> RunView:
    wf = deps.registry().get(workflow)
    if wf is None:
        raise RunRuleError("unknown_workflow")
    if not deps.permissions.allows(actor, wf.action):
        raise RunRuleError("forbidden")
    key = (idempotency_key or "").strip() or None
    if key and len(key) > MAX_IDEMPOTENCY_KEY:
        raise RunRuleError("invalid_idempotency_key")
    normalized = wf.validate(dict(input or {}))
    async with deps.db.workspace_session(actor.workspace_id) as s:
        if key:
            existing = await s.scalar(select(Run).where(Run.idempotency_key == key))
            if existing is not None:
                if existing.workflow != wf.name or existing.input != normalized:
                    raise RunRuleError("idempotency_key_reused")
                return _view(existing)
        run = Run(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            workflow=wf.name,
            status=RunStatus.QUEUED.value,
            input=normalized,
            position={},
            step=0,
            steps_total=len(wf.steps),
            waits=0,
            cost_usd=Decimal(0),
            cost_unknown=False,
            model_calls=0,
            cost_from_ledger=True,
            resumes=0,
            actor_kind=actor.kind,
            actor_scopes=sorted(actor.scopes),
            idempotency_key=key,
            created_by=actor.subject_id,
        )
        s.add(run)
        await s.flush()
        await add_event(s, run, RunEventKind.STATUS)
        _enqueue(s, run)
    return _view(run)


async def get_run(db: Database, actor: ActorContext, run_id: UUID) -> RunView:
    async with db.workspace_session(actor.workspace_id) as s:
        run = await s.get(Run, run_id)
        if run is None:
            raise NotFound("run")
        return _view(run)


async def list_runs(
    db: Database, actor: ActorContext, *, status: str | None = None, limit: int = 20
) -> list[RunView]:
    q = select(Run).order_by(Run.created_at.desc()).limit(max(1, min(limit, LIST_LIMIT)))
    if status:
        try:
            q = q.where(Run.status == RunStatus(status).value)
        except ValueError as exc:
            raise RunRuleError("invalid_status") from exc
    async with db.workspace_session(actor.workspace_id) as s:
        return [_view(r) for r in (await s.execute(q)).scalars()]


async def run_events(
    db: Database, actor: ActorContext, run_id: UUID, *, after: int = 0
) -> list[RunEventView]:
    async with db.workspace_session(actor.workspace_id) as s:
        if await s.get(Run, run_id) is None:
            raise NotFound("run")
        rows = (
            await s.execute(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.seq > after)
                .order_by(RunEvent.seq)
                .limit(500)
            )
        ).scalars()
        return [
            RunEventView(e.seq, e.kind, e.status, e.step, e.code, e.data, e.created_at)
            for e in rows
        ]


async def cancel_run(db: Database, actor: ActorContext, run_id: UUID) -> RunView:
    """대기·중단·승인 대기 중이면 바로 취소, 실행 중이면 이번 단계가 끝난 뒤 취소."""
    from career_core.usecases.approvals import close_for_run

    async with db.workspace_session(actor.workspace_id) as s:
        run = await _lock(s, run_id)
        if run is None:
            raise NotFound("run")
        if run.status in TERMINAL_RUN_STATUSES:
            raise RunRuleError("run_finished")
        if run.status == RunStatus.RUNNING:
            run.stop_requested = StopRequest.CANCEL.value
            await add_event(s, run, RunEventKind.STATUS, code="cancel_requested")
        else:
            await _finish(s, run, RunStatus.CANCELLED, code="cancelled_by_user")
            await close_for_run(s, run.id, "run_cancelled")
        return _view(run)


async def interrupt_run(db: Database, actor: ActorContext, run_id: UUID) -> RunView:
    """잠시 멈춤. 실행 중이면 이번 단계가 끝난 뒤 멈춘다. ``resume_run``으로 이어서 한다."""
    async with db.workspace_session(actor.workspace_id) as s:
        run = await _lock(s, run_id)
        if run is None:
            raise NotFound("run")
        if run.status == RunStatus.RUNNING:
            run.stop_requested = StopRequest.INTERRUPT.value
            await add_event(s, run, RunEventKind.STATUS, code="interrupt_requested")
        elif run.status == RunStatus.QUEUED:
            run.status = RunStatus.INTERRUPTED.value
            run.wait_reason = None
            run.retry_at = None
            await add_event(s, run, RunEventKind.STATUS, code="interrupted_by_user")
        else:
            raise RunRuleError("run_not_active")
        return _view(run)


async def resume_run(db: Database, actor: ActorContext, run_id: UUID) -> RunView:
    async with db.workspace_session(actor.workspace_id) as s:
        run = await _lock(s, run_id)
        if run is None:
            raise NotFound("run")
        if run.status != RunStatus.INTERRUPTED:
            raise RunRuleError("run_not_interrupted")
        run.status = RunStatus.QUEUED.value
        run.stop_requested = None
        run.waits = 0
        await add_event(s, run, RunEventKind.STATUS, code="resumed_by_user")
        _enqueue(s, run)
        return _view(run)


# ---------------------------------------------------------------- worker


async def _actor(s: AsyncSession, run: Run) -> ActorContext | None:
    role = await s.scalar(select(Membership.role).where(Membership.actor_id == run.created_by))
    if role is None:
        return None
    scopes = scopes_for_role(role) & frozenset(run.actor_scopes)
    return ActorContext(run.created_by, run.workspace_id, scopes, kind=run.actor_kind)


# 실지출로 세지 않는 비용 근거 (StepContext.model과 같은 규칙)
_NOT_SPENT = ("list_price_equivalent", "none")


@dataclass(frozen=True, slots=True)
class LedgerCost:
    model_calls: int
    cost_usd: Decimal
    cost_unknown: bool


async def ledger_cost(s: AsyncSession, run_id: UUID) -> LedgerCost:
    """장부에서 이 실행의 성공한 호출 수·실지출·단가 미상 여부를 센다 (``StepContext.model``과 같은 규칙)."""
    ok = ModelCall.status == "ok"
    spent = and_(ok, ModelCall.cost_usd.isnot(None), ModelCall.cost_basis.notin_(_NOT_SPENT))
    unknown = and_(ok, ModelCall.cost_usd.is_(None), ModelCall.cost_basis == "unknown")
    row = (
        await s.execute(
            select(
                func.count().filter(ok),
                func.coalesce(func.sum(ModelCall.cost_usd).filter(spent), 0),
                func.coalesce(func.bool_or(unknown), False),
            ).where(ModelCall.run_id == run_id)
        )
    ).one()
    return LedgerCost(int(row[0]), Decimal(row[1]), bool(row[2]))


async def reconcile_cost(s: AsyncSession, run: Run) -> LedgerCost | None:
    """실행 비용을 장부 기준으로 맞춘다. 바뀐 만큼(호출 수·비용)을 돌려준다. 예전 실행은 건드리지 않는다."""
    if not run.cost_from_ledger:
        return None
    led = await ledger_cost(s, run.id)
    delta = LedgerCost(
        led.model_calls - run.model_calls,
        led.cost_usd - (run.cost_usd or Decimal(0)),
        led.cost_unknown and not run.cost_unknown,
    )
    run.model_calls = led.model_calls
    run.cost_usd = led.cost_usd
    run.cost_unknown = led.cost_unknown
    return delta


async def _add_cost(s: AsyncSession, run: Run, ctx: StepContext) -> None:
    if run.cost_from_ledger:
        # 장부가 원장이다: 이번 시도 + 앞서 죽은 시도의 호출까지 같은 값으로 맞는다.
        # lease를 잃은 옛 worker가 늦게 저장해도 두 번 더해지지 않는다.
        await reconcile_cost(s, run)
        return
    run.cost_usd = (run.cost_usd or Decimal(0)) + ctx.cost_usd
    run.cost_unknown = run.cost_unknown or ctx.cost_unknown
    run.model_calls += ctx.model_calls


async def _apply_stop(s: AsyncSession, run: Run) -> bool:
    from career_core.usecases.approvals import close_for_run

    if run.stop_requested == StopRequest.CANCEL:
        await _finish(s, run, RunStatus.CANCELLED, code="cancelled_by_user")
        await close_for_run(s, run.id, "run_cancelled")
        return True
    if run.stop_requested == StopRequest.INTERRUPT:
        run.status = RunStatus.INTERRUPTED.value
        run.stop_requested = None
        await add_event(s, run, RunEventKind.STATUS, code="interrupted_by_user")
        return True
    return False


async def process_run(deps: RunDeps, ws: UUID, run_id: UUID) -> StepOutcome:
    """작업 큐 handler. 끝·승인 대기·중단이면 done, 한도 대기면 retry_after."""
    db = deps.db
    async with db.workspace_session(ws) as s:
        # 작업 행 → 실행 행 순서로 잠근다 (lease 확인, 모든 쓰기 트랜잭션에서 같은 순서)
        await guard_lease(s, run_id)
        run = await _lock(s, run_id)
        if run is None or run.status not in (RunStatus.QUEUED, RunStatus.RUNNING):
            return StepOutcome(done=True)
        if await _apply_stop(s, run):
            return StepOutcome(done=True)
        if run.status == RunStatus.RUNNING:
            # 앞선 worker가 단계 도중에 멈췄다: 저장된 위치부터 다시 한다.
            # 그 시도의 모델 비용은 장부에만 있다 → 장부에서 실행 비용을 맞춘다 (c0016)
            run.resumes += 1
            data: dict[str, Any] = {"step": run.step}
            delta = await reconcile_cost(s, run)
            if delta is not None and (delta.model_calls or delta.cost_usd or delta.cost_unknown):
                data["reconciled"] = {
                    "model_calls": delta.model_calls,
                    "cost_usd": str(delta.cost_usd),
                }
            await add_event(s, run, RunEventKind.RESUMED, data=data)
        else:
            run.status = RunStatus.RUNNING.value
            run.started_at = run.started_at or utcnow()
            await add_event(s, run, RunEventKind.STATUS)
        run.wait_reason = None
        run.retry_at = None
        workflow_name = run.workflow

    wf = deps.registry().get(workflow_name)
    while True:
        async with db.workspace_session(ws) as s:
            await guard_lease(s, run_id)
            run = await s.get(Run, run_id)
            if run.status != RunStatus.RUNNING:
                return StepOutcome(done=True)
            if wf is None:
                await _finish(s, await _lock(s, run_id), RunStatus.FAILED, code="unknown_workflow")
                return StepOutcome(done=True)
            if run.step >= len(wf.steps):
                run = await _lock(s, run_id)
                await _finish(s, run, RunStatus.DONE)
                return StepOutcome(done=True)
            actor = await _actor(s, run)
            if actor is None:
                run = await _lock(s, run_id)
                await _finish(s, run, RunStatus.FAILED, code="actor_revoked")
                return StepOutcome(done=True)
            ctx = StepContext(deps, actor, run.id, dict(run.input), dict(run.position), run.step)

        outcome: StepResult | None = None
        failure: str | None = None
        wait: str | None = None
        try:
            outcome = await wf.steps[ctx.step](ctx)
        except StepFailed as exc:
            failure = exc.code
        except ModelDenied as exc:
            # 예산·단가 미상·egress: 기다려도 풀리지 않는다
            failure = exc.code
        except ModelCallError as exc:
            if exc.code in FAIL_ERROR_CODES:
                failure = exc.code
            elif exc.code in WAIT_ERROR_CODES or exc.retryable:
                wait = exc.code
            else:
                failure = exc.code
        except Exception as exc:
            # 예상 못 한 오류: 사유를 남기고 작업 큐의 재시도에 맡긴다 (같은 위치부터 다시)
            async with db.workspace_session(ws) as s:
                await guard_lease(s, run_id)
                run = await _lock(s, run_id)
                await _add_cost(s, run, ctx)
                await add_event(s, run, RunEventKind.ERROR, code=type(exc).__name__)
            raise

        async with db.workspace_session(ws) as s:
            # lease를 잃은 worker(오래 멈췄던 프로세스 등)의 단계 결과는 여기서 버려진다 (LeaseLost → 되돌림)
            await guard_lease(s, run_id)
            run = await _lock(s, run_id)
            await _add_cost(s, run, ctx)
            if failure is not None:
                await _finish(s, run, RunStatus.FAILED, code=failure)
                return StepOutcome(done=True)
            if wait is not None:
                if run.waits >= deps.max_waits:
                    await _finish(s, run, RunStatus.FAILED, code=wait)
                    return StepOutcome(done=True)
                delay = wait_seconds(run.waits, deps.wait_base_seconds)
                run.waits += 1
                run.status = RunStatus.QUEUED.value
                run.wait_reason = wait
                run.retry_at = utcnow() + timedelta(seconds=delay)
                await add_event(
                    s, run, RunEventKind.WAIT, code=wait, data={"seconds": delay, "n": run.waits}
                )
                # 같은 작업 행이 retry_after 뒤에 다시 잡힌다 (새 작업을 만들지 않는다)
                return StepOutcome(done=False, retry_after=delay)
            assert outcome is not None
            run.position = {**run.position, **(outcome.position or {})}
            run.waits = 0
            last = run.step + 1 >= len(wf.steps)
            if outcome.wait_approval is not None:
                run.step += 1
                run.status = RunStatus.WAITING_APPROVAL.value
                run.approval_id = outcome.wait_approval
                await add_event(
                    s, run, RunEventKind.STATUS, data={"approval_id": str(outcome.wait_approval)}
                )
                await _apply_stop(s, run)
                return StepOutcome(done=True)
            if outcome.done or (last and not outcome.repeat):
                run.step += 0 if outcome.repeat else 1
                run.result = outcome.result if outcome.result is not None else run.result
                await _finish(s, run, RunStatus.DONE)
                return StepOutcome(done=True)
            if not outcome.repeat:
                run.step += 1
            await add_event(s, run, RunEventKind.STEP, data=_step_data(outcome))
            if await _apply_stop(s, run):
                return StepOutcome(done=True)


def _step_data(outcome: StepResult) -> dict:
    keys = sorted((outcome.position or {}).keys())
    return {"updated": keys} if keys else {}


async def give_up_run(deps: RunDeps, ws: UUID, run_id: UUID) -> None:
    """같은 단계가 계속 예외로 끝났다: 실행을 실패로 닫는다 (조용히 남겨두지 않는다)."""
    async with deps.db.workspace_session(ws) as s:
        run = await _lock(s, run_id)
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            return
        await _finish(s, run, RunStatus.FAILED, code="step_error")


__all__ = [
    "LedgerCost",
    "RunDeps",
    "RunEventView",
    "RunView",
    "Step",
    "StepContext",
    "StepFailed",
    "StepResult",
    "Workflow",
    "add_event",
    "cancel_run",
    "get_run",
    "give_up_run",
    "interrupt_run",
    "ledger_cost",
    "list_runs",
    "process_run",
    "reconcile_cost",
    "resume_run",
    "run_events",
    "start_run",
    "wake_run",
]

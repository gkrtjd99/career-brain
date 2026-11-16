"""승인 (O08, plan §11).

승인 1건은 행위(action)·대상(target_kind, target_id)·대상 버전(target_version)·내용 해시(payload_hash)·
만료 시각·승인자·멱등 키에 묶인다.

- 요청: 편집 권한이 있는 사람이나 AI(agent)가 한다. 요청은 승인이 아니다.
- 결정: ``approve`` 범위를 가진 사람(human actor)만 한다. agent actor는 범위가 있어도 결정할 수 없다
  (``ActorContext.has_scope``가 agent의 approve를 막고, DB CHECK가 사람 결정만 approved로 둔다).
- 사용: 승인을 쓰는 usecase(예: 제출)가 자기 트랜잭션 안에서 ``consume_approval``을 부른다. 그 순간의
  대상 버전·내용 해시를 다시 계산해 요청 때와 다르면 쓰지 않고 무효(invalidated)로 표시한다.
  승인은 한 번만 쓴다 (``used``).
- 행위별 대상 버전 계산은 ``ApprovalBinder``가 맡는다. 등록되지 않은 행위는 승인을 요청할 수 없다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from career_core.db import Database
from career_core.db.base import utcnow
from career_core.db.models import Approval
from career_core.domain.runs import (
    INVALIDATING_CODES,
    OPEN_APPROVAL_STATUSES,
    ApprovalError,
    ApprovalStatus,
    content_hash,
)
from career_core.domain.workspace import ScopePermissionPolicy
from career_core.ports import ActorContext, PermissionPolicy
from career_core.usecases.brain import NotFound

DEFAULT_TTL = timedelta(hours=24)
MIN_TTL = timedelta(minutes=1)
MAX_TTL = timedelta(days=7)
MAX_REASON_CHARS = 1000
MAX_KEY_CHARS = 200
LIST_LIMIT = 100


@dataclass(frozen=True, slots=True)
class ApprovalBinder:
    """행위 하나의 승인 규칙."""

    action: str
    target_kind: str
    # 요청자에게 필요한 행동 (대상을 준비할 권한). 결정은 항상 approvals.decide.
    request_action: str
    # 내용 정규화. 잘못된 값이면 ApprovalError("invalid_payload")
    normalize: Callable[[dict[str, Any]], dict[str, Any]]
    # (대상 버전 해시, 승인 화면용 요약). 대상이 없으면 NotFound
    version: Callable[[AsyncSession, UUID], Awaitable[tuple[str, dict[str, Any]]]]


def default_binders() -> Mapping[str, ApprovalBinder]:
    from career_core.usecases.applications import (
        SUBMIT_ACTION,
        submission_payload,
        submission_target,
    )

    return {
        SUBMIT_ACTION: ApprovalBinder(
            action=SUBMIT_ACTION,
            target_kind="application",
            request_action="applications.write",
            normalize=submission_payload,
            version=submission_target,
        )
    }


def _binder(action: str, binders: Mapping[str, ApprovalBinder] | None) -> ApprovalBinder:
    binder = (binders or default_binders()).get(action)
    if binder is None:
        raise ApprovalError("unknown_action")
    return binder


@dataclass(frozen=True, slots=True)
class ApprovalView:
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


def _view(a: Approval) -> ApprovalView:
    return ApprovalView(
        a.id,
        a.action,
        a.target_kind,
        a.target_id,
        a.target_version,
        a.payload_hash,
        a.payload,
        a.summary,
        a.status,
        a.reason,
        a.requested_by,
        a.requested_via,
        a.run_id,
        a.expires_at,
        a.decided_by,
        a.decided_at,
        a.decision_note,
        a.used_at,
        a.used_result_id,
        a.invalid_reason,
        a.created_at,
    )


async def _lock(s: AsyncSession, approval_id: UUID) -> Approval | None:
    return await s.scalar(select(Approval).where(Approval.id == approval_id).with_for_update())


def _close(row: Approval, status: ApprovalStatus, reason: str | None = None) -> None:
    row.status = status.value
    if status in (ApprovalStatus.INVALIDATED, ApprovalStatus.EXPIRED):
        row.invalid_reason = reason or status.value


async def _refresh(
    s: AsyncSession, row: Approval, binders: Mapping[str, ApprovalBinder] | None, now: datetime
) -> str | None:
    """열린 승인이 아직 유효한지 본다. 만료·대상 변경이면 닫고 사유를 돌려준다."""
    if row.status not in OPEN_APPROVAL_STATUSES:
        return None
    if now >= row.expires_at:
        _close(row, ApprovalStatus.EXPIRED, "expired")
        return "approval_expired"
    binder = _binder(row.action, binders)
    try:
        version, _ = await binder.version(s, row.target_id)
    except NotFound:
        _close(row, ApprovalStatus.INVALIDATED, "target_missing")
        return "target_changed"
    if version != row.target_version:
        _close(row, ApprovalStatus.INVALIDATED, "target_changed")
        return "target_changed"
    return None


async def close_for_run(s: AsyncSession, run_id: UUID, reason: str) -> None:
    """실행이 취소되면 그 실행이 요청한 열린 승인도 닫는다."""
    rows = (
        await s.execute(
            select(Approval)
            .where(Approval.run_id == run_id, Approval.status.in_(OPEN_APPROVAL_STATUSES))
            .with_for_update()
        )
    ).scalars()
    for row in rows:
        _close(row, ApprovalStatus.INVALIDATED, reason)


# ---------------------------------------------------------------- 요청


async def request_approval(
    db: Database,
    actor: ActorContext,
    *,
    action: str,
    target_id: UUID,
    payload: dict[str, Any],
    payload_hash: str | None = None,
    reason: str | None = None,
    expires_in: timedelta | None = None,
    idempotency_key: str | None = None,
    run_id: UUID | None = None,
    permissions: PermissionPolicy | None = None,
    binders: Mapping[str, ApprovalBinder] | None = None,
    now: datetime | None = None,
) -> ApprovalView:
    """승인 요청을 만든다 (승인이 아니다). 같은 멱등 키면 처음 요청을 돌려준다."""
    policy = permissions or ScopePermissionPolicy()
    binder = _binder(action, binders)
    if not policy.allows(actor, "approvals.request") or not policy.allows(
        actor, binder.request_action
    ):
        raise ApprovalError("forbidden")
    ttl = expires_in or DEFAULT_TTL
    if not (MIN_TTL <= ttl <= MAX_TTL):
        raise ApprovalError("invalid_expiry")
    key = (idempotency_key or "").strip() or None
    if key and len(key) > MAX_KEY_CHARS:
        raise ApprovalError("invalid_idempotency_key")
    normalized = binder.normalize(dict(payload or {}))
    digest = content_hash(normalized)
    if payload_hash is not None and payload_hash != digest:
        # 요청자가 본 내용과 서버가 받은 내용이 다르다
        raise ApprovalError("payload_mismatch")
    now = now or utcnow()
    async with db.workspace_session(actor.workspace_id) as s:
        if key:
            existing = await s.scalar(select(Approval).where(Approval.idempotency_key == key))
            if existing is not None:
                if (existing.action, existing.target_id, existing.payload_hash) != (
                    action,
                    target_id,
                    digest,
                ):
                    raise ApprovalError("idempotency_key_reused")
                return _view(existing)
        version, summary = await binder.version(s, target_id)
        row = Approval(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            action=action,
            target_kind=binder.target_kind,
            target_id=target_id,
            target_version=version,
            payload_hash=digest,
            payload=normalized,
            summary=summary,
            status=ApprovalStatus.PENDING.value,
            reason=(reason or "").strip()[:MAX_REASON_CHARS] or None,
            requested_by=actor.subject_id,
            requested_via=actor.kind,
            run_id=run_id,
            expires_at=now + ttl,
            idempotency_key=key,
            created_at=now,
        )
        s.add(row)
    return _view(row)


# ---------------------------------------------------------------- 조회


async def get_approval(
    db: Database,
    actor: ActorContext,
    approval_id: UUID,
    *,
    binders: Mapping[str, ApprovalBinder] | None = None,
    now: datetime | None = None,
) -> ApprovalView:
    """조회할 때 만료·대상 변경을 반영한다 (승인 화면이 낡은 승인을 보여주지 않게)."""
    async with db.workspace_session(actor.workspace_id) as s:
        row = await _lock(s, approval_id)
        if row is None:
            raise NotFound("approval")
        await _refresh(s, row, binders, now or utcnow())
        return _view(row)


async def list_approvals(
    db: Database,
    actor: ActorContext,
    *,
    status: str | None = ApprovalStatus.PENDING.value,
    limit: int = 50,
    binders: Mapping[str, ApprovalBinder] | None = None,
    now: datetime | None = None,
) -> list[ApprovalView]:
    q = (
        select(Approval)
        .order_by(Approval.created_at.desc())
        .limit(max(1, min(limit, LIST_LIMIT)))
        .with_for_update()
    )
    if status:
        try:
            q = q.where(Approval.status == ApprovalStatus(status).value)
        except ValueError as exc:
            raise ApprovalError("invalid_status") from exc
    now = now or utcnow()
    async with db.workspace_session(actor.workspace_id) as s:
        rows = list((await s.execute(q)).scalars())
        for row in rows:
            await _refresh(s, row, binders, now)
        return [_view(r) for r in rows if not status or r.status == status]


# ---------------------------------------------------------------- 결정 (사람만)


async def decide_approval(
    db: Database,
    actor: ActorContext,
    approval_id: UUID,
    *,
    approve: bool,
    payload_hash: str | None = None,
    note: str | None = None,
    permissions: PermissionPolicy | None = None,
    binders: Mapping[str, ApprovalBinder] | None = None,
    now: datetime | None = None,
) -> ApprovalView:
    """``payload_hash``: 승인 화면이 보여준 내용의 해시. 다르면 승인하지 않는다."""
    from career_core.usecases.runs import wake_run

    policy = permissions or ScopePermissionPolicy()
    # agent actor는 approve 범위가 있어도 has_scope가 거짓이다. 그래도 한 번 더 명시적으로 막는다.
    if not actor.is_human or not policy.allows(actor, "approvals.decide"):
        raise ApprovalError("human_approval_required")
    now = now or utcnow()
    problem: str | None = None
    async with db.workspace_session(actor.workspace_id) as s:
        row = await _lock(s, approval_id)
        if row is None:
            raise NotFound("approval")
        if row.status != ApprovalStatus.PENDING:
            raise ApprovalError("approval_not_pending")
        if payload_hash is not None and payload_hash != row.payload_hash:
            raise ApprovalError("payload_mismatch")
        problem = await _refresh(s, row, binders, now)
        if problem is None:
            row.status = (ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED).value
            row.decided_by = actor.subject_id
            row.decided_via = actor.kind
            row.decided_at = now
            row.decision_note = (note or "").strip()[:MAX_REASON_CHARS] or None
        if row.run_id is not None:
            # 승인·거부·무효 어느 쪽이든 기다리던 실행이 다음 단계에서 결과를 본다
            await wake_run(s, row.run_id, row.id)
        view = _view(row)
    if problem is not None:
        raise ApprovalError(problem)
    return view


# ---------------------------------------------------------------- 사용 (소비자 트랜잭션 안)


async def consume_approval(
    s: AsyncSession,
    actor: ActorContext,
    approval_id: UUID,
    *,
    action: str,
    target_id: UUID,
    payload: dict[str, Any],
    binders: Mapping[str, ApprovalBinder] | None = None,
    now: datetime | None = None,
) -> Approval:
    """승인을 한 번 쓴다. 문제가 있으면 ApprovalError를 던진다 (호출자 트랜잭션은 롤백된다).

    무효 사유(대상·내용 변경, 만료)는 ``record_rejection``으로 따로 남긴다.
    """
    row = await _lock(s, approval_id)
    if row is None:
        raise ApprovalError("approval_not_found")
    if row.action != action or row.target_id != target_id:
        raise ApprovalError("approval_mismatch")
    if row.status != ApprovalStatus.APPROVED:
        raise ApprovalError(f"approval_{row.status}")
    binder = _binder(action, binders)
    now = now or utcnow()
    if now >= row.expires_at:
        raise ApprovalError("approval_expired")
    if content_hash(binder.normalize(dict(payload))) != row.payload_hash:
        raise ApprovalError("payload_changed")
    version, _ = await binder.version(s, target_id)
    if version != row.target_version:
        raise ApprovalError("target_changed")
    row.status = ApprovalStatus.USED.value
    row.used_by = actor.subject_id
    row.used_at = now
    return row


async def record_rejection(db: Database, workspace_id: UUID, approval_id: UUID, code: str) -> None:
    """``consume_approval``이 거절한 뒤(롤백 후) 무효·만료를 남긴다. 다시 쓸 수 없게 한다."""
    if code not in INVALIDATING_CODES and code != "approval_expired":
        return
    async with db.workspace_session(workspace_id) as s:
        row = await _lock(s, approval_id)
        if row is None or row.status not in OPEN_APPROVAL_STATUSES:
            return
        if code == "approval_expired":
            _close(row, ApprovalStatus.EXPIRED, "expired")
        else:
            _close(row, ApprovalStatus.INVALIDATED, code)


__all__ = [
    "ApprovalBinder",
    "ApprovalView",
    "close_for_run",
    "consume_approval",
    "decide_approval",
    "default_binders",
    "get_approval",
    "list_approvals",
    "record_rejection",
    "request_approval",
]

"""감사 기록 조회 (시스템 화면). 쓰기는 ``AuditSink`` 포트(기본 ``adapters.audit.DbAuditSink``)가 한다."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from career_core.db.models import AuditEvent
from career_core.db.session import Database
from career_core.domain.runs import canonical_json
from career_core.domain.workspace import ScopePermissionPolicy
from career_core.ports import ActorContext, PermissionPolicy

LIST_LIMIT = 200


class AuditForbidden(PermissionError):
    code = "forbidden"


def args_digest(arguments: Any) -> str:
    """인자의 정규 JSON(키 순서 무관) sha256. 원문 인자 대신 이것만 남긴다."""
    return hashlib.sha256(canonical_json(arguments).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AuditEventView:
    id: UUID
    created_at: datetime
    actor_id: UUID | None
    actor_kind: str
    channel: str
    event: str
    tool: str | None
    status: str
    error_code: str | None
    duration_ms: int
    args_sha256: str | None


@dataclass(frozen=True, slots=True)
class AuditPage:
    events: list[AuditEventView]
    # 지난 24시간 도구별 (호출 수, 오류 수)
    recent: dict[str, tuple[int, int]]


async def list_audit_events(
    db: Database,
    actor: ActorContext,
    *,
    limit: int = 50,
    before: datetime | None = None,
    tool: str | None = None,
    status: str | None = None,
    since: datetime | None = None,
    permissions: PermissionPolicy | None = None,
) -> AuditPage:
    if not (permissions or ScopePermissionPolicy()).allows(actor, "audit.read"):
        raise AuditForbidden("forbidden")
    q = select(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
    if before is not None:
        q = q.where(AuditEvent.created_at < before)
    if tool:
        q = q.where(AuditEvent.tool == tool)
    if status in ("ok", "error"):
        q = q.where(AuditEvent.status == status)
    q = q.limit(max(1, min(limit, LIST_LIMIT)))
    summary = select(
        AuditEvent.tool,
        func.count(),
        func.count().filter(AuditEvent.status == "error"),
    ).group_by(AuditEvent.tool)
    if since is not None:
        summary = summary.where(AuditEvent.created_at >= since)
    async with db.workspace_session(actor.workspace_id) as s:
        rows = (await s.execute(q)).scalars().all()
        recent = {(t or "-"): (n, e) for t, n, e in (await s.execute(summary)).all()}
    return AuditPage(
        events=[
            AuditEventView(
                r.id,
                r.created_at,
                r.actor_id,
                r.actor_kind,
                r.channel,
                r.event,
                r.tool,
                r.status,
                r.error_code,
                r.duration_ms,
                r.args_sha256,
            )
            for r in rows
        ],
        recent=recent,
    )


__all__ = ["AuditEventView", "AuditForbidden", "AuditPage", "args_digest", "list_audit_events"]

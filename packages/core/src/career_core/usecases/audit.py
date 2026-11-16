"""감사 기록 조회 (시스템 화면)와 보존 기간 정리. 쓰기는 ``AuditSink`` 포트(기본 ``adapters.audit.DbAuditSink``)가 한다.

보존 (c0019): ``CAREER_AUDIT_RETENTION_DAYS``(기본 180일, 0이면 계속 보관)보다 오래된 행을 주기 유지보수가
지운다. 앱 역할은 ``audit_events``에 SELECT·INSERT만 있으므로 지우기는 소유자 권한 함수
``core.purge_audit_events(keep_days)``로만 한다. 그 함수는 호출한 트랜잭션의 공간 문맥 안에서, 최소 7일보다
오래된 행만 지운다 (앱 계정이 뚫려도 최근 기록을 지울 수 없게).
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text

from career_core.db.models import AuditEvent
from career_core.db.session import Database
from career_core.domain.runs import canonical_json
from career_core.domain.workspace import ScopePermissionPolicy
from career_core.ports import ActorContext, PermissionPolicy

LIST_LIMIT = 200
# 보존 기간 (일). 0 = 계속 보관. DB 함수가 MIN_RETENTION_DAYS보다 짧은 값을 거부한다 (c0019).
DEFAULT_RETENTION_DAYS = 180
MIN_RETENTION_DAYS = 7
RETENTION_ENV = "CAREER_AUDIT_RETENTION_DAYS"

log = logging.getLogger(__name__)


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


def retention_days(raw: str | None = None) -> int:
    """``CAREER_AUDIT_RETENTION_DAYS`` → 보존 일수. 비우면 기본 180, 0이면 계속 보관(정리 안 함).

    1–6은 최소 7일로 올린다 (DB 함수가 더 짧은 값을 거부한다). 숫자가 아니면 기본값.
    """
    raw = os.environ.get(RETENTION_ENV) if raw is None else raw
    if raw is None or not raw.strip():
        return DEFAULT_RETENTION_DAYS
    try:
        days = int(raw.strip())
    except ValueError:
        log.warning("%s=%r is not a number; using %d", RETENTION_ENV, raw, DEFAULT_RETENTION_DAYS)
        return DEFAULT_RETENTION_DAYS
    if days <= 0:
        return 0
    return max(days, MIN_RETENTION_DAYS)


async def purge_audit_events(db: Database, workspace_id: UUID, keep_days: int) -> int:
    """그 공간에서 ``keep_days``보다 오래된 감사 기록을 지운다. 0 이하면 아무것도 안 한다. 지운 수."""
    if keep_days <= 0:
        return 0
    async with db.workspace_session(workspace_id) as s:
        n = await s.scalar(
            text("SELECT core.purge_audit_events(:d)"), {"d": max(keep_days, MIN_RETENTION_DAYS)}
        )
    return int(n or 0)


async def prune_security_records(db: Database, workspace_id: UUID) -> int:
    """주기 유지보수 한 단계: 보존 기간이 지난 감사 기록 + 만료된 OAuth 토큰 기록."""
    from career_core.usecases.oauth_identities import prune_oauth_tokens

    return await purge_audit_events(db, workspace_id, retention_days()) + await prune_oauth_tokens(
        db, workspace_id
    )


__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "MIN_RETENTION_DAYS",
    "AuditEventView",
    "AuditForbidden",
    "AuditPage",
    "args_digest",
    "list_audit_events",
    "prune_security_records",
    "purge_audit_events",
    "retention_days",
]

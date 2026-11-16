"""감사 기록 경로 (MCP 도구 호출). 소유자(workspace-admin)만.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from career_core.ports import ActorContext
from career_core.usecases.audit import list_audit_events


class AuditEventOut(BaseModel):
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


class ToolStatOut(BaseModel):
    tool: str
    calls: int
    errors: int


class AuditOut(BaseModel):
    events: list[AuditEventOut]
    # 지난 24시간 도구별 호출·오류 수
    last_24h: list[ToolStatOut]


def add_audit_routes(router: APIRouter, deps, authorize) -> None:
    @router.get("/audit", response_model=AuditOut)
    async def audit(
        actor: Annotated[ActorContext, Depends(authorize("audit.read"))],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        before: datetime | None = None,
        tool: Annotated[str | None, Query(max_length=100)] = None,
        status: Annotated[str | None, Query(pattern="^(ok|error)$")] = None,
    ) -> AuditOut:
        page = await list_audit_events(
            deps.db,
            actor,
            limit=limit,
            before=before,
            tool=tool,
            status=status,
            since=datetime.now(UTC) - timedelta(hours=24),
            permissions=deps.permissions,
        )
        return AuditOut(
            events=[AuditEventOut.model_validate(e, from_attributes=True) for e in page.events],
            last_24h=[
                ToolStatOut(tool=t, calls=n, errors=e)
                for t, (n, e) in sorted(page.recent.items(), key=lambda kv: -kv[1][0])
            ],
        )


__all__ = ["add_audit_routes"]

"""원본 목록·최근 활동 경로 (대시보드용 읽기 전용).

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from career_core.ports import ActorContext
from career_core.usecases import activity as activity_uc
from career_core.usecases import sources as sources_uc

# 활동 종류 → 그 종류를 보는 데 필요한 행동. 권한이 없는 종류는 목록에서 뺀다.
_KIND_ACTIONS = {
    "claim": "brain.read",
    "posting": "jobs.read",
    "fetch_run": "jobs.read",
    "document_revision": "applications.read",
    "study_attempt": "study.read",
}


class SourceListOut(BaseModel):
    id: UUID
    title: str
    data_class: str
    created_at: datetime
    versions: int
    latest_version_id: UUID | None
    original_filename: str | None
    mime_type: str | None
    size_bytes: int | None
    parse_run_id: UUID | None
    parse_status: str | None
    pages_total: int | None
    missing_pages: list[int]
    parse_error: str | None
    extraction_run_id: UUID | None
    extraction_status: str | None
    claims_created: int | None
    # c0032: 용도, 이 원본의 확인 전 후보 수, 받은 자료함에 둘 이유 (없으면 다 처리됨)
    purpose: str = "experience"
    pending_claims: int = 0
    attention: str | None = None


class ActivityOut(BaseModel):
    kind: str
    action: str
    id: UUID
    at: datetime
    title: str
    status: str | None
    detail: str | None
    refs: dict[str, Any]


def add_activity_routes(router: APIRouter, deps, authorize) -> None:
    db = deps.db
    source_reader = Annotated[ActorContext, Depends(authorize("sources.read"))]
    any_reader = Annotated[ActorContext, Depends(authorize("workspace.read"))]

    @router.get("/sources", response_model=list[SourceListOut])
    async def list_sources(
        actor: source_reader,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        purpose: Literal["experience", "proof", "reference"] | None = None,
        attention: Annotated[
            bool, Query(description="받은 자료함: 처리 중·실패·일부만·확인할 후보가 있는 원본만")
        ] = False,
    ) -> list[SourceListOut]:
        rows = await sources_uc.list_sources(
            db, actor, limit=limit, purpose=purpose, attention=attention
        )
        return [SourceListOut.model_validate(r, from_attributes=True) for r in rows]

    @router.get("/activity", response_model=list[ActivityOut])
    async def recent_activity(
        actor: any_reader,
        limit: Annotated[int, Query(ge=1, le=activity_uc.MAX_LIMIT)] = 30,
        before: datetime | None = None,
        kinds: Annotated[str | None, Query(description=",".join(activity_uc.KINDS))] = None,
    ) -> list[ActivityOut]:
        wanted = tuple(k.strip() for k in kinds.split(",")) if kinds else activity_uc.KINDS
        allowed = tuple(
            k
            for k in wanted
            if k in _KIND_ACTIONS and deps.permissions.allows(actor, _KIND_ACTIONS[k])
        )
        if not allowed:
            return []
        rows = await activity_uc.recent_activity(
            db, actor, limit=limit, before=before, kinds=allowed
        )
        return [ActivityOut.model_validate(r, from_attributes=True) for r in rows]

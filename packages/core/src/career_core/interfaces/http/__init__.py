"""공통 FastAPI router. 각 실행 앱이 ``CoreDeps``를 주입해 include_router로 조립한다.

workspace는 요청 body·path·query로 받지 않는다. 항상 ``IdentityResolver``가 확정한
``ActorContext.workspace_id``를 쓴다.
"""

# NOTE: `from __future__ import annotations`를 쓰지 않는다. build_router 안에서 정의한
# 의존성을 Annotated로 참조하므로 FastAPI가 런타임에 타입을 해석할 수 있어야 한다.

from dataclasses import dataclass, field
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select

from career_core import __version__
from career_core.db.models import Actor, Membership, Workspace
from career_core.db.session import Database
from career_core.domain.workspace import ScopePermissionPolicy
from career_core.ports import ActorContext, IdentityResolver, PermissionPolicy


@dataclass(frozen=True, slots=True)
class CoreDeps:
    identity: IdentityResolver
    db: Database
    permissions: PermissionPolicy = field(default_factory=ScopePermissionPolicy)


class WorkspaceOut(BaseModel):
    id: UUID
    name: str


class MeOut(BaseModel):
    actor_id: UUID
    workspace: WorkspaceOut
    scopes: list[str]


class MemberOut(BaseModel):
    actor_id: UUID
    display_name: str
    role: str


_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="authentication required",
    headers={"WWW-Authenticate": "Bearer"},
)


def build_router(deps: CoreDeps | None = None) -> APIRouter:
    """``deps`` 없이 만들면 공개 health만 노출한다 (인증이 필요한 경로는 등록하지 않음)."""
    router = APIRouter(prefix="/core", tags=["core"])

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "core_version": __version__}

    if deps is None:
        return router

    bearer = HTTPBearer(auto_error=False)

    async def current_actor(
        cred: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> ActorContext:
        if cred is None or not cred.credentials:
            raise _UNAUTHORIZED
        actor = await deps.identity.resolve(cred.credentials)
        if actor is None:
            raise _UNAUTHORIZED
        return actor

    def authorize(action: str):
        async def dependency(
            actor: Annotated[ActorContext, Depends(current_actor)],
        ) -> ActorContext:
            if not deps.permissions.allows(actor, action):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
            return actor

        return dependency

    @router.get("/me", response_model=MeOut)
    async def me(actor: Annotated[ActorContext, Depends(authorize("workspace.read"))]) -> MeOut:
        async with deps.db.workspace_session(actor.workspace_id) as s:
            ws = await s.get(Workspace, actor.workspace_id)
        if ws is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workspace not found")
        return MeOut(
            actor_id=actor.subject_id,
            workspace=WorkspaceOut(id=ws.id, name=ws.name),
            scopes=sorted(actor.scopes),
        )

    @router.get("/members", response_model=list[MemberOut])
    async def members(
        actor: Annotated[ActorContext, Depends(authorize("members.read"))],
    ) -> list[MemberOut]:
        async with deps.db.workspace_session(actor.workspace_id) as s:
            rows = (
                await s.execute(
                    select(Membership.actor_id, Actor.display_name, Membership.role)
                    .join(Actor, Actor.id == Membership.actor_id)
                    .order_by(Membership.created_at)
                )
            ).all()
        return [
            MemberOut(actor_id=r.actor_id, display_name=r.display_name, role=r.role) for r in rows
        ]

    return router


__all__ = ["CoreDeps", "build_router"]

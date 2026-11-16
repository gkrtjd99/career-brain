"""공통 FastAPI router. 각 실행 앱이 ``CoreDeps``를 주입해 include_router로 조립한다.

workspace는 요청 body·path·query로 받지 않는다. 항상 ``IdentityResolver``가 확정한
``ActorContext.workspace_id``를 쓴다.
"""

# NOTE: `from __future__ import annotations`를 쓰지 않는다. build_router 안에서 정의한
# 의존성을 Annotated로 참조하므로 FastAPI가 런타임에 타입을 해석할 수 있어야 한다.

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select

from career_core import __version__
from career_core.db.models import (
    Actor,
    DocumentBlock,
    Membership,
    ParseRun,
    Source,
    SourceVersion,
    Workspace,
)
from career_core.db.session import Database
from career_core.domain.workspace import ScopePermissionPolicy
from career_core.interfaces.http.brain import add_brain_routes
from career_core.interfaces.http.usage import add_usage_routes
from career_core.llm.gateway import ModelGateway
from career_core.ports import ActorContext, BlobStore, IdentityResolver, PermissionPolicy
from career_core.uploads import UploadRejected
from career_core.usecases.sources import upload_source


@dataclass(frozen=True, slots=True)
class CoreDeps:
    identity: IdentityResolver
    db: Database
    permissions: PermissionPolicy = field(default_factory=ScopePermissionPolicy)
    # 없으면 원본 업로드 경로를 등록하지 않는다.
    blobs: BlobStore | None = None
    max_upload_bytes: int = 20 * 1024 * 1024
    # 사용량 응답에 같이 보여줄 월 예산 (models.toml [budget]).
    monthly_budget_usd: Decimal | None = None
    # 없으면 AI 보충 질문 경로가 503을 돌려준다. 추출은 worker가 처리한다.
    models: ModelGateway | None = None


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


class UploadOut(BaseModel):
    source_id: UUID
    version_id: UUID
    parse_run_id: UUID | None
    deduplicated: bool


class ParseOut(BaseModel):
    run_id: UUID
    status: str
    pages_total: int | None
    missing_pages: list[int]
    error_code: str | None


class VersionOut(BaseModel):
    id: UUID
    sha256: str
    mime_type: str
    size_bytes: int
    original_filename: str
    parse: ParseOut | None


class SourceOut(BaseModel):
    id: UUID
    title: str
    data_class: str
    versions: list[VersionOut]


class BlockOut(BaseModel):
    id: UUID
    page: int | None
    ordinal: int
    origin: str
    text: str


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

    add_usage_routes(router, deps, authorize)
    add_brain_routes(router, deps, authorize)
    if deps.blobs is not None:
        _add_source_routes(router, deps, authorize)

    return router


def _add_source_routes(router: APIRouter, deps: CoreDeps, authorize) -> None:
    blobs = deps.blobs
    not_found = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    @router.post("/sources", response_model=UploadOut)
    async def upload(
        actor: Annotated[ActorContext, Depends(authorize("sources.write"))],
        file: Annotated[UploadFile, File()],
        title: Annotated[str | None, Form()] = None,
        data_class: Annotated[str, Form()] = "private",
    ) -> UploadOut:
        data = await file.read(deps.max_upload_bytes + 1)
        try:
            result = await upload_source(
                deps.db,
                blobs,
                actor,
                filename=file.filename,
                data=data,
                declared_mime=file.content_type,
                max_bytes=deps.max_upload_bytes,
                title=title,
                data_class=data_class,
            )
        except UploadRejected as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from exc
        return UploadOut(
            source_id=result.source_id,
            version_id=result.version_id,
            parse_run_id=result.parse_run_id,
            deduplicated=result.deduplicated,
        )

    @router.get("/sources/{source_id}", response_model=SourceOut)
    async def get_source(
        source_id: UUID, actor: Annotated[ActorContext, Depends(authorize("sources.read"))]
    ) -> SourceOut:
        async with deps.db.workspace_session(actor.workspace_id) as s:
            source = await s.get(Source, source_id)
            if source is None:
                raise not_found
            rows = (
                await s.execute(
                    select(SourceVersion, ParseRun)
                    .outerjoin(ParseRun, ParseRun.source_version_id == SourceVersion.id)
                    .where(SourceVersion.source_id == source_id)
                    .order_by(SourceVersion.created_at, ParseRun.pipeline_version)
                )
            ).all()
        versions = [
            VersionOut(
                id=v.id,
                sha256=v.sha256,
                mime_type=v.mime_type,
                size_bytes=v.size_bytes,
                original_filename=v.original_filename,
                parse=None
                if r is None
                else ParseOut(
                    run_id=r.id,
                    status=r.status,
                    pages_total=r.pages_total,
                    missing_pages=list(r.missing_pages or []),
                    error_code=r.error_code,
                ),
            )
            for v, r in rows
        ]
        return SourceOut(
            id=source.id, title=source.title, data_class=source.data_class, versions=versions
        )

    @router.get("/parse-runs/{run_id}/blocks", response_model=list[BlockOut])
    async def get_blocks(
        run_id: UUID, actor: Annotated[ActorContext, Depends(authorize("sources.read"))]
    ) -> list[BlockOut]:
        async with deps.db.workspace_session(actor.workspace_id) as s:
            if await s.get(ParseRun, run_id) is None:
                raise not_found
            blocks = (
                await s.execute(
                    select(DocumentBlock)
                    .where(DocumentBlock.parse_run_id == run_id)
                    .order_by(DocumentBlock.ordinal)
                )
            ).scalars()
            return [
                BlockOut(id=b.id, page=b.page, ordinal=b.ordinal, origin=b.origin, text=b.text)
                for b in blocks
            ]


__all__ = ["CoreDeps", "build_router"]

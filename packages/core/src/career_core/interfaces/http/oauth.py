"""원격 MCP OAuth 연결·토큰 관리 경로 (시스템 화면). 공간 소유자(사람)만.

- ``GET /core/oauth``: 설정 요약(켜졌는지, issuer, 자원 주소, introspection·jti 필수 여부) + 연결 + 최근 토큰.
- ``POST /core/oauth/links`` ``{subject, issuer?, label?}``: (issuer, subject) → 요청한 소유자. issuer를
  안 주면 설정한 issuer.
- ``POST /core/oauth/links/{id}/revoke``: 연결 끊기 (그 사용자의 모든 토큰이 다음 요청부터 401).
- ``POST /core/oauth/tokens/{id}/revoke``: 토큰 하나를 폐기 목록에 (만료 전이라도 다음 요청부터 401).

토큰 원문은 어디에도 없다. 목록에 나오는 것은 ``jti`` 또는 토큰 sha256뿐이다.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from career_core.ports import ActorContext
from career_core.usecases.oauth_identities import (
    OAuthLinkError,
    link_oauth_identity,
    list_oauth_identities,
    list_oauth_tokens,
    revoke_oauth_identity,
    revoke_oauth_token,
)

_STATUS = {
    "forbidden": status.HTTP_403_FORBIDDEN,
    "subject_already_linked": status.HTTP_409_CONFLICT,
}


def _error(exc: OAuthLinkError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(exc.code, 422), detail=exc.code)


class OAuthConfigOut(BaseModel):
    enabled: bool
    issuer: str | None
    resource: str | None
    jwks: bool
    introspection: bool
    require_jti: bool
    # c0025: JWT도 introspection으로 확인하는지, introspection 캐시 시간(초) = 인가 서버 폐기 반영 지연 상한
    introspect_jwt: bool = False
    introspection_cache_seconds: int | None = None


class OAuthLinkOut(BaseModel):
    id: UUID
    actor_id: UUID
    issuer: str
    subject: str
    label: str
    created_at: datetime
    revoked_at: datetime | None


class OAuthTokenOut(BaseModel):
    id: UUID
    identity_id: UUID
    issuer: str
    subject: str
    token_ref: str
    ref_kind: str
    via: str
    first_seen_at: datetime
    last_seen_at: datetime
    expires_at: datetime | None
    uses: int
    revoked_at: datetime | None


class OAuthOut(BaseModel):
    config: OAuthConfigOut
    links: list[OAuthLinkOut]
    tokens: list[OAuthTokenOut]


class OAuthLinkIn(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    issuer: str | None = Field(default=None, max_length=500)
    label: str | None = Field(default=None, max_length=100)


class RevokedOut(BaseModel):
    revoked: bool


def add_oauth_routes(router: APIRouter, deps, authorize) -> None:
    oauth = getattr(deps, "mcp_oauth", None)
    cfg = oauth.config if oauth is not None else None
    config_out = OAuthConfigOut(
        enabled=cfg is not None,
        issuer=cfg.issuer if cfg else None,
        resource=cfg.resource if cfg else None,
        jwks=bool(cfg and cfg.jwks_url),
        introspection=bool(cfg and cfg.introspection_url),
        require_jti=bool(cfg and cfg.require_jti),
        introspect_jwt=bool(cfg and cfg.introspect_jwt),
        introspection_cache_seconds=(
            cfg.introspection_cache_seconds if cfg and cfg.introspection_url else None
        ),
    )
    Owner = Annotated[ActorContext, Depends(authorize("oauth.link"))]

    @router.get("/oauth", response_model=OAuthOut)
    async def oauth_overview(
        actor: Owner, limit: Annotated[int, Query(ge=1, le=200)] = 50
    ) -> OAuthOut:
        try:
            links = await list_oauth_identities(deps.db, actor)
            tokens = await list_oauth_tokens(deps.db, actor, limit=limit)
        except OAuthLinkError as exc:
            raise _error(exc) from exc
        return OAuthOut(
            config=config_out,
            links=[OAuthLinkOut.model_validate(v, from_attributes=True) for v in links],
            tokens=[OAuthTokenOut.model_validate(v, from_attributes=True) for v in tokens],
        )

    @router.post("/oauth/links", response_model=OAuthLinkOut, status_code=201)
    async def oauth_link(body: OAuthLinkIn, actor: Owner) -> OAuthLinkOut:
        issuer = (body.issuer or "").strip() or (cfg.issuer if cfg else "")
        if not issuer:
            raise HTTPException(status_code=422, detail="issuer_required")
        try:
            view = await link_oauth_identity(
                deps.db,
                actor,
                issuer=issuer,
                subject=body.subject,
                label=body.label or "oauth",
                permissions=deps.permissions,
            )
        except OAuthLinkError as exc:
            raise _error(exc) from exc
        return OAuthLinkOut.model_validate(view, from_attributes=True)

    @router.post("/oauth/links/{link_id}/revoke", response_model=RevokedOut)
    async def oauth_unlink(link_id: UUID, actor: Owner) -> RevokedOut:
        try:
            return RevokedOut(revoked=await revoke_oauth_identity(deps.db, actor, link_id))
        except OAuthLinkError as exc:
            raise _error(exc) from exc

    @router.post("/oauth/tokens/{token_id}/revoke", response_model=RevokedOut)
    async def oauth_revoke_token(token_id: UUID, actor: Owner) -> RevokedOut:
        try:
            return RevokedOut(revoked=await revoke_oauth_token(deps.db, actor, token_id))
        except OAuthLinkError as exc:
            raise _error(exc) from exc


__all__ = ["add_oauth_routes"]

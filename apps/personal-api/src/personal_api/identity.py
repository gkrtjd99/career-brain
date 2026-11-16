"""개인용 로그인 어댑터: 공간 범위 API 토큰.

- 토큰 원문은 저장하지 않고 SHA-256만 저장한다 (고엔트로피 랜덤 토큰).
- 조회는 ``personal.lookup_api_token`` SECURITY DEFINER 함수 하나로만 한다.
  앱 역할은 RLS 때문에 다른 공간의 토큰 행을 직접 읽을 수 없다.
- 웹 대시보드용 쿠키 세션은 O10에서 추가한다.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from uuid import UUID, uuid4

from career_core.db import Database, Membership
from career_core.db.base import utcnow
from career_core.domain.workspace import scopes_for_role
from career_core.ports import ActorContext
from sqlalchemy import DateTime, MetaData, Text, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

PERSONAL_SCHEMA = "personal"
TOKEN_PREFIX = "cbp_"


class PersonalBase(DeclarativeBase):
    metadata = MetaData(schema=PERSONAL_SCHEMA)


class ApiToken(PersonalBase):
    __tablename__ = "api_tokens"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    actor_id: Mapped[UUID]
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    label: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_token(db: Database, *, workspace_id: UUID, actor_id: UUID, label: str) -> str:
    """새 토큰을 만들고 원문을 한 번만 반환한다."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    async with db.workspace_session(workspace_id) as s:
        s.add(
            ApiToken(
                workspace_id=workspace_id,
                actor_id=actor_id,
                token_hash=hash_token(token),
                label=label,
            )
        )
    return token


async def revoke_token(db: Database, *, workspace_id: UUID, token: str) -> bool:
    async with db.workspace_session(workspace_id) as s:
        row = await s.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token)))
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = utcnow()
    return True


class PersonalTokenResolver:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def resolve(self, credential: str) -> ActorContext | None:
        if not credential.startswith(TOKEN_PREFIX):
            return None
        async with self.db.system_session() as s:
            row = (
                await s.execute(
                    text("SELECT workspace_id, actor_id FROM personal.lookup_api_token(:h)"),
                    {"h": hash_token(credential)},
                )
            ).first()
        if row is None:
            return None
        async with self.db.workspace_session(row.workspace_id) as s:
            role = await s.scalar(
                select(Membership.role).where(Membership.actor_id == row.actor_id)
            )
        if role is None:
            return None
        return ActorContext(
            subject_id=row.actor_id, workspace_id=row.workspace_id, scopes=scopes_for_role(role)
        )


MCP_PATH = "/api/core/mcp"


def mcp_oauth_from_settings(settings, db: Database):
    """``CAREER_OAUTH_ISSUER``가 있으면 원격 MCP용 외부 JWT 검증을 켠다. 없으면 None (API 토큰만).

    필요한 값: ``CAREER_PUBLIC_URL``(자원 주소의 앞부분), ``CAREER_OAUTH_JWKS_URL``.
    """
    if not settings.oauth_issuer:
        return None
    from career_core.interfaces.mcp.oauth import (
        McpOAuth,
        OAuthConfig,
        OAuthConfigError,
        OAuthIdentityResolver,
    )

    if not settings.public_url or not settings.oauth_jwks_url:
        raise OAuthConfigError(
            "CAREER_OAUTH_ISSUER needs CAREER_PUBLIC_URL and CAREER_OAUTH_JWKS_URL"
        )
    config = OAuthConfig(
        resource=settings.public_url.rstrip("/") + MCP_PATH,
        issuer=settings.oauth_issuer,
        jwks_url=settings.oauth_jwks_url,
        audience=settings.oauth_audience,
    )
    return McpOAuth(config=config, resolver=OAuthIdentityResolver(db, config))

"""개인용 로그인 어댑터: 공간 범위 API 토큰.

- 토큰 원문은 저장하지 않고 SHA-256만 저장한다 (고엔트로피 랜덤 토큰).
- 조회는 ``personal.lookup_api_token`` SECURITY DEFINER 함수 하나로만 한다.
  앱 역할은 RLS 때문에 다른 공간의 토큰 행을 직접 읽을 수 없다.
- 만료일(선택, p0002)이 지난 토큰은 조회 함수가 찾지 않는다. 마지막 사용 시각은 요청마다 쓰지 않고
  ``TOUCH_INTERVAL``(5분)에 한 번만 갱신한다 (조회 함수가 지금 값을 같이 돌려준다).
- 웹 대시보드용 쿠키 세션은 O10에서 추가한다.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from career_core.db import Database, Membership
from career_core.db.base import utcnow
from career_core.domain.workspace import scopes_for_role
from career_core.ports import ActorContext, ApiTokenRecord
from sqlalchemy import DateTime, MetaData, Text, func, or_, select, text, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

PERSONAL_SCHEMA = "personal"
TOKEN_PREFIX = "cbp_"
# 마지막 사용 시각 갱신 간격: 요청마다 쓰지 않는다
TOUCH_INTERVAL = timedelta(minutes=5)
MAX_EXPIRES_DAYS = 3650


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
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_token(
    db: Database,
    *,
    workspace_id: UUID,
    actor_id: UUID,
    label: str,
    expires_days: int | None = None,
) -> str:
    """새 토큰을 만들고 원문을 한 번만 반환한다. ``expires_days``가 있으면 그 날 수 뒤에 만료."""
    token, _ = await _issue(db, workspace_id, actor_id, label, expires_days)
    return token


async def _issue(
    db: Database, workspace_id: UUID, actor_id: UUID, label: str, expires_days: int | None
) -> tuple[str, ApiToken]:
    if expires_days is not None and not 1 <= expires_days <= MAX_EXPIRES_DAYS:
        raise ValueError(f"expires_days must be 1..{MAX_EXPIRES_DAYS}")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = utcnow()
    row = ApiToken(
        id=uuid4(),
        workspace_id=workspace_id,
        actor_id=actor_id,
        token_hash=hash_token(token),
        label=label,
        created_at=now,
        expires_at=now + timedelta(days=expires_days) if expires_days else None,
    )
    async with db.workspace_session(workspace_id) as s:
        s.add(row)
    return token, row


async def revoke_token(
    db: Database,
    *,
    workspace_id: UUID,
    token: str | None = None,
    token_id: UUID | None = None,
) -> bool:
    """토큰 원문 또는 ID 하나로 폐기한다. 없거나 이미 폐기됐으면 False. 다른 공간 토큰은 RLS로 안 보인다."""
    if (token is None) == (token_id is None):
        raise ValueError("pass exactly one of token, token_id")
    cond = ApiToken.id == token_id if token is None else ApiToken.token_hash == hash_token(token)
    async with db.workspace_session(workspace_id) as s:
        row = await s.scalar(select(ApiToken).where(cond).with_for_update())
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = utcnow()
    return True


@dataclass(frozen=True, slots=True)
class TokenInfo:
    """토큰 목록 한 줄. 원문·해시는 넣지 않는다."""

    id: UUID
    label: str
    actor_id: UUID
    created_at: datetime
    revoked_at: datetime | None
    current: bool  # 지금 요청에 쓴 토큰
    expires_at: datetime | None = None
    last_used_at: datetime | None = None

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= utcnow()

    def record(self) -> ApiTokenRecord:
        return ApiTokenRecord(
            id=self.id,
            label=self.label,
            actor_id=self.actor_id,
            created_at=self.created_at,
            expires_at=self.expires_at,
            last_used_at=self.last_used_at,
            revoked_at=self.revoked_at,
        )


def _info(r: ApiToken, current: str | None) -> TokenInfo:
    return TokenInfo(
        r.id,
        r.label,
        r.actor_id,
        r.created_at,
        r.revoked_at,
        r.token_hash == current,
        r.expires_at,
        r.last_used_at,
    )


async def list_tokens(
    db: Database, *, workspace_id: UUID, current_token: str | None = None
) -> list[TokenInfo]:
    current = hash_token(current_token) if current_token else None
    async with db.workspace_session(workspace_id) as s:
        rows = (await s.scalars(select(ApiToken).order_by(ApiToken.created_at))).all()
    return [_info(r, current) for r in rows]


def _touch(token_id: UUID):
    return (
        update(ApiToken)
        .where(
            ApiToken.id == token_id,
            or_(
                ApiToken.last_used_at.is_(None),
                ApiToken.last_used_at < func.now() - TOUCH_INTERVAL,
            ),
        )
        .values(last_used_at=func.now())
    )


async def touch_token(db: Database, *, workspace_id: UUID, token_id: UUID) -> bool:
    """마지막 사용 시각을 지금으로. 이미 ``TOUCH_INTERVAL`` 안에 갱신했으면 쓰지 않는다 (False)."""
    async with db.workspace_session(workspace_id) as s:
        res = await s.execute(_touch(token_id))
    return res.rowcount > 0


class PersonalTokenResolver:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def resolve(self, credential: str) -> ActorContext | None:
        if not credential.startswith(TOKEN_PREFIX):
            return None
        async with self.db.system_session() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT workspace_id, actor_id, token_id, last_used_at "
                        "FROM personal.lookup_api_token(:h)"
                    ),
                    {"h": hash_token(credential)},
                )
            ).first()
        if row is None:
            return None
        async with self.db.workspace_session(row.workspace_id) as s:
            role = await s.scalar(
                select(Membership.role).where(Membership.actor_id == row.actor_id)
            )
            # 마지막 사용 시각: 조회한 값이 오래됐을 때만 쓴다 (요청마다 쓰지 않는다)
            stale = row.last_used_at is None or row.last_used_at < utcnow() - TOUCH_INTERVAL
            if role is not None and stale:
                await s.execute(_touch(row.token_id))
        if role is None:
            return None
        return ActorContext(
            subject_id=row.actor_id,
            workspace_id=row.workspace_id,
            scopes=scopes_for_role(role),
            credential_id=row.token_id,
        )


class PersonalTokenAdmin:
    """``career_core.ports.ApiTokenAdmin`` 구현: 웹 설정 화면의 토큰 목록·발급·폐기.

    새 토큰은 요청한 사람(소유자) 자신의 토큰이다. 권한 검사(``tokens.*``)는 HTTP 경로가 한다.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    async def list(self, actor: ActorContext) -> list[ApiTokenRecord]:
        rows = await list_tokens(self.db, workspace_id=actor.workspace_id)
        return [r.record() for r in rows]

    async def issue(
        self, actor: ActorContext, *, label: str, expires_days: int | None
    ) -> tuple[ApiTokenRecord, str]:
        token, row = await _issue(
            self.db, actor.workspace_id, actor.subject_id, label, expires_days
        )
        return _info(row, None).record(), token

    async def revoke(self, actor: ActorContext, token_id: UUID) -> bool:
        return await revoke_token(self.db, workspace_id=actor.workspace_id, token_id=token_id)


MCP_PATH = "/api/core/mcp"


def mcp_oauth_from_settings(settings, db: Database):
    """``CAREER_OAUTH_ISSUER``가 있으면 원격 MCP용 외부 JWT 검증을 켠다. 없으면 None (API 토큰만).

    필요한 값: ``CAREER_PUBLIC_URL``(자원 주소의 앞부분), 그리고 ``CAREER_OAUTH_JWKS_URL``(JWT) 또는
    ``CAREER_OAUTH_INTROSPECTION_URL`` + ``CAREER_OAUTH_CLIENT_ID``·``CAREER_OAUTH_CLIENT_SECRET``(불투명 토큰).
    API 토큰(``cbp_``)은 인가 서버로 보내지 않는다.
    """
    if not settings.oauth_issuer:
        return None
    from career_core.interfaces.mcp.oauth import (
        McpOAuth,
        OAuthConfig,
        OAuthConfigError,
        OAuthIdentityResolver,
    )

    introspection = getattr(settings, "oauth_introspection_url", None)
    if not settings.public_url or not (settings.oauth_jwks_url or introspection):
        raise OAuthConfigError(
            "CAREER_OAUTH_ISSUER needs CAREER_PUBLIC_URL and CAREER_OAUTH_JWKS_URL "
            "(or CAREER_OAUTH_INTROSPECTION_URL)"
        )
    config = OAuthConfig(
        resource=settings.public_url.rstrip("/") + MCP_PATH,
        issuer=settings.oauth_issuer,
        jwks_url=settings.oauth_jwks_url,
        audience=settings.oauth_audience,
        introspection_url=introspection,
        introspection_client_id=getattr(settings, "oauth_client_id", None),
        introspection_client_secret=getattr(settings, "oauth_client_secret", None),
        require_jti=bool(getattr(settings, "oauth_require_jti", False)),
        local_token_prefixes=(TOKEN_PREFIX,),
    )
    return McpOAuth(config=config, resolver=OAuthIdentityResolver(db, config))

"""외부 OAuth 신원 연결 (원격 MCP). 공간 소유자가 (issuer, subject)를 자기 actor에 연결한다.

토큰이 공간을 정하는 근거가 이 연결이다. 토큰 안의 값(공간 ID 등)은 쓰지 않는다.

c0019: 받은 토큰을 ``core.oauth_tokens``에 남긴다 (``jti``, 없으면 토큰 sha256. 원문은 없음).
- 폐기 목록: 소유자가 토큰 하나를 폐기하면(``revoke_oauth_token``) 그 토큰은 만료 전이라도 다음 요청부터
  거부된다. 연결을 끊으면(``revoke_oauth_identity``) 그 사용자의 모든 토큰이 거부된다.
- 같은 ``jti``가 다른 연결(다른 subject)로 오면 거부한다 (``jti_conflict``). 같은 토큰을 여러 번 쓰는 것은
  정상이다: 액세스 토큰은 만료까지 요청마다 다시 보내는 bearer 토큰이라 한 번만 쓰게 할 수 없다 (mcp.md).
- 정리: 만료가 지난 기록은 주기 유지보수가 지운다 (``prune_oauth_tokens``). 만료된 토큰은 어차피 검증에서
  거부되므로 폐기 기록도 더 필요 없다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from career_core.db.base import utcnow
from career_core.db.models import OAuthIdentity, OAuthToken
from career_core.db.session import Database
from career_core.domain.workspace import ScopePermissionPolicy
from career_core.ports import ActorContext, PermissionPolicy

MAX_CHARS = 500


class OAuthLinkError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class OAuthIdentityView:
    id: UUID
    actor_id: UUID
    issuer: str
    subject: str
    label: str
    created_at: datetime
    revoked_at: datetime | None


def _view(r: OAuthIdentity) -> OAuthIdentityView:
    return OAuthIdentityView(
        r.id, r.actor_id, r.issuer, r.subject, r.label, r.created_at, r.revoked_at
    )


def _clean(value: str, name: str) -> str:
    value = (value or "").strip()
    if not value or len(value) > MAX_CHARS:
        raise OAuthLinkError(f"invalid_{name}")
    return value


async def link_oauth_identity(
    db: Database,
    actor: ActorContext,
    *,
    issuer: str,
    subject: str,
    label: str = "oauth",
    permissions: PermissionPolicy | None = None,
) -> OAuthIdentityView:
    """(issuer, subject) → 이 actor. 사람 소유자만. 다른 공간에 이미 연결돼 있으면 거부."""
    policy = permissions or ScopePermissionPolicy()
    if not actor.is_human or not policy.allows(actor, "oauth.link"):
        raise OAuthLinkError("forbidden")
    issuer, subject = _clean(issuer, "issuer"), _clean(subject, "subject")
    async with db.system_session() as s:
        taken = (
            await s.execute(
                text("SELECT workspace_id, actor_id FROM core.lookup_oauth_identity(:i, :s)"),
                {"i": issuer, "s": subject},
            )
        ).first()
    if taken is not None:
        if (taken.workspace_id, taken.actor_id) == (actor.workspace_id, actor.subject_id):
            async with db.workspace_session(actor.workspace_id) as s:
                row = await s.scalar(
                    select(OAuthIdentity).where(
                        OAuthIdentity.issuer == issuer,
                        OAuthIdentity.subject == subject,
                        OAuthIdentity.revoked_at.is_(None),
                    )
                )
                return _view(row)
        raise OAuthLinkError("subject_already_linked")
    async with db.workspace_session(actor.workspace_id) as s:
        row = OAuthIdentity(
            workspace_id=actor.workspace_id,
            actor_id=actor.subject_id,
            issuer=issuer,
            subject=subject,
            label=(label or "oauth").strip()[:100] or "oauth",
        )
        s.add(row)
    return _view(row)


async def list_oauth_identities(db: Database, actor: ActorContext) -> list[OAuthIdentityView]:
    if not ScopePermissionPolicy().allows(actor, "oauth.link"):
        raise OAuthLinkError("forbidden")
    async with db.workspace_session(actor.workspace_id) as s:
        rows = (
            (await s.execute(select(OAuthIdentity).order_by(OAuthIdentity.created_at)))
            .scalars()
            .all()
        )
    return [_view(r) for r in rows]


async def revoke_oauth_identity(db: Database, actor: ActorContext, identity_id: UUID) -> bool:
    """연결을 끊는다. 그 뒤로 같은 subject의 토큰은 이 공간에 들어오지 못한다."""
    if not actor.is_human or not ScopePermissionPolicy().allows(actor, "oauth.link"):
        raise OAuthLinkError("forbidden")
    async with db.workspace_session(actor.workspace_id) as s:
        row = await s.get(OAuthIdentity, identity_id)
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = utcnow()
    return True


# ---------------------------------------------------------------- 토큰 기록·폐기 (c0019)

TOKEN_LIST_LIMIT = 200
# exp를 모르는 토큰(introspection 응답에 exp 없음)의 기록은 마지막으로 본 뒤 이만큼 둔다
UNKNOWN_EXPIRY_KEEP = timedelta(days=30)
# 만료 뒤에도 잠깐 둔다 (검증 여유·시계 차이)
EXPIRED_KEEP = timedelta(days=1)


def token_ref(token: str, jti: object) -> tuple[str, str]:
    """(token_ref, ref_kind). ``jti``가 쓸 만한 문자열이면 그 값, 아니면 토큰의 sha256."""
    if isinstance(jti, str) and 0 < len(jti) <= MAX_CHARS:
        return jti, "jti"
    return hashlib.sha256(token.encode("utf-8")).hexdigest(), "sha256"


async def record_token_use(
    s: AsyncSession,
    *,
    workspace_id: UUID,
    identity_id: UUID,
    issuer: str,
    subject: str,
    ref: str,
    ref_kind: str,
    via: str,
    expires_at: datetime | None,
) -> str:
    """받은 토큰 하나를 남기고 판정한다: ``ok`` / ``revoked`` / ``jti_conflict``.

    호출자 공간 문맥(``workspace_session``) 안에서 부른다. 같은 토큰이면 마지막 사용 시각·횟수만 올린다.
    같은 ``token_ref``가 다른 연결의 것이면 고치지 않고 ``jti_conflict``.
    """
    stmt = insert(OAuthToken).values(
        id=uuid4(),
        workspace_id=workspace_id,
        identity_id=identity_id,
        issuer=issuer[:MAX_CHARS],
        subject=subject[:MAX_CHARS],
        token_ref=ref,
        ref_kind=ref_kind,
        via=via,
        first_seen_at=func.now(),
        last_seen_at=func.now(),
        expires_at=expires_at,
        uses=1,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_oauth_tokens_workspace_ref",
        set_={"last_seen_at": func.now(), "uses": OAuthToken.uses + 1},
        where=OAuthToken.identity_id == stmt.excluded.identity_id,
    ).returning(OAuthToken.revoked_at)
    row = (await s.execute(stmt)).first()
    if row is None:
        return "jti_conflict"
    return "revoked" if row.revoked_at is not None else "ok"


@dataclass(frozen=True, slots=True)
class OAuthTokenView:
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


def _token_view(r: OAuthToken) -> OAuthTokenView:
    return OAuthTokenView(
        r.id,
        r.identity_id,
        r.issuer,
        r.subject,
        r.token_ref,
        r.ref_kind,
        r.via,
        r.first_seen_at,
        r.last_seen_at,
        r.expires_at,
        r.uses,
        r.revoked_at,
    )


async def list_oauth_tokens(
    db: Database, actor: ActorContext, *, limit: int = 50
) -> list[OAuthTokenView]:
    """최근에 본 토큰 (폐기한 것 포함), 마지막 사용 순."""
    if not ScopePermissionPolicy().allows(actor, "oauth.link"):
        raise OAuthLinkError("forbidden")
    async with db.workspace_session(actor.workspace_id) as s:
        rows = (
            (
                await s.execute(
                    select(OAuthToken)
                    .order_by(OAuthToken.last_seen_at.desc(), OAuthToken.id)
                    .limit(max(1, min(limit, TOKEN_LIST_LIMIT)))
                )
            )
            .scalars()
            .all()
        )
    return [_token_view(r) for r in rows]


async def revoke_oauth_token(db: Database, actor: ActorContext, token_id: UUID) -> bool:
    """토큰 하나를 폐기 목록에 넣는다. 사람 소유자만. 이미 폐기했거나 없으면 False."""
    if not actor.is_human or not ScopePermissionPolicy().allows(actor, "oauth.link"):
        raise OAuthLinkError("forbidden")
    async with db.workspace_session(actor.workspace_id) as s:
        row = await s.get(OAuthToken, token_id, with_for_update=True)
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = utcnow()
        row.revoked_by = actor.subject_id
    return True


async def prune_oauth_tokens(db: Database, workspace_id: UUID) -> int:
    """만료가 지난 토큰 기록을 지운다 (주기 유지보수). 지운 수."""
    now = utcnow()
    async with db.workspace_session(workspace_id) as s:
        result = await s.execute(
            delete(OAuthToken).where(
                or_(
                    OAuthToken.expires_at < now - EXPIRED_KEEP,
                    (OAuthToken.expires_at.is_(None))
                    & (OAuthToken.last_seen_at < now - UNKNOWN_EXPIRY_KEEP),
                )
            )
        )
    return result.rowcount or 0


__all__ = [
    "OAuthIdentityView",
    "OAuthLinkError",
    "OAuthTokenView",
    "link_oauth_identity",
    "list_oauth_identities",
    "list_oauth_tokens",
    "prune_oauth_tokens",
    "record_token_use",
    "revoke_oauth_identity",
    "revoke_oauth_token",
    "token_ref",
]

"""외부 OAuth 신원 연결 (원격 MCP). 공간 소유자가 (issuer, subject)를 자기 actor에 연결한다.

토큰이 공간을 정하는 근거가 이 연결이다. 토큰 안의 값(공간 ID 등)은 쓰지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text

from career_core.db.base import utcnow
from career_core.db.models import OAuthIdentity
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


__all__ = [
    "OAuthIdentityView",
    "OAuthLinkError",
    "link_oauth_identity",
    "list_oauth_identities",
    "revoke_oauth_identity",
]

"""공간 역할 → scope, 행동 → 필요 scope."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from career_core.ports import ActorContext


class Role(StrEnum):
    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


ROLE_SCOPES: dict[Role, frozenset[str]] = {
    Role.OWNER: frozenset({"read", "draft-write", "approve", "egress", "workspace-admin"}),
    Role.EDITOR: frozenset({"read", "draft-write"}),
    Role.VIEWER: frozenset({"read"}),
}

# 새 행동을 추가할 때 여기에 등록하지 않으면 거부된다.
ACTION_SCOPES: dict[str, str] = {
    "workspace.read": "read",
    "members.read": "read",
    "members.manage": "workspace-admin",
    "sources.read": "read",
    "sources.write": "draft-write",
    "usage.read": "read",
    "brain.read": "read",
    "brain.write": "draft-write",
    "claims.confirm": "approve",
    # 자료를 외부 모델로 보내는 작업은 egress 권한이 있어야 한다.
    "brain.extract": "egress",
    "interview.ai": "egress",
    "search.read": "read",
    "search.reindex": "draft-write",
    "jobs.read": "read",
    "jobs.write": "draft-write",
    # O09 학습. 경험 후보로 올리기도 study.write (주장은 확인 대기로만 생긴다).
    "study.read": "read",
    "study.write": "draft-write",
}


def scopes_for_role(role: str) -> frozenset[str]:
    try:
        return ROLE_SCOPES[Role(role)]
    except ValueError:
        return frozenset()


class ScopePermissionPolicy:
    """등록된 행동만, actor가 필요한 scope를 가진 경우에만 허용."""

    def allows(self, actor: ActorContext, action: str, resource_id: UUID | None = None) -> bool:
        required = ACTION_SCOPES.get(action)
        return required is not None and actor.has_scope(required)

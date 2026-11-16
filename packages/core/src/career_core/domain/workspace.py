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
    # O07 지원·문서. 제출본 고정은 사람의 승인 행위다.
    "applications.read": "read",
    "applications.write": "draft-write",
    "applications.submit": "approve",
    "documents.ai": "egress",
    # O09 학습. 경험 후보로 올리기도 study.write (주장은 확인 대기로만 생긴다).
    "study.read": "read",
    "study.write": "draft-write",
    # O08 실행·승인. 승인 결정은 사람만 (agent actor는 approve 범위를 쓰지 못한다).
    "runs.read": "read",
    "runs.start": "draft-write",
    "runs.control": "draft-write",
    "approvals.read": "read",
    "approvals.request": "draft-write",
    "approvals.decide": "approve",
    # 승인 묶음을 가진 제출은 편집자·AI도 실행할 수 있다 (승인 자체는 사람이 이미 했다).
    "applications.submit_approved": "draft-write",
    # O11 내보내기·유지보수. 공간 전체 자료를 다루므로 소유자만.
    "workspace.export": "workspace-admin",
    "maintenance.read": "workspace-admin",
    "maintenance.purge": "workspace-admin",
    # c0014 감사 기록(MCP 도구 호출)·외부 OAuth 신원 연결. 소유자만.
    "audit.read": "workspace-admin",
    "oauth.link": "workspace-admin",
    # c0018 묶음을 기존 공간에 합치기 (웹 복원 포함). 공간 전체 자료를 쓰므로 소유자만.
    "workspace.restore": "workspace-admin",
    # API 토큰 목록·발급·폐기 (personal-api 토큰, 원문·해시는 보이지 않는다). 소유자만.
    "tokens.read": "workspace-admin",
    "tokens.manage": "workspace-admin",
    # c0021 공간 공용 설정 (표시 시간대·기술명 별칭). 학습 설정 PUT과 같은 범위.
    "settings.read": "read",
    "settings.write": "draft-write",
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

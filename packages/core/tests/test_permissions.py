from uuid import uuid4

import pytest
from career_core.domain.workspace import Role, ScopePermissionPolicy, scopes_for_role
from career_core.ports import ActorContext


def actor(role: str) -> ActorContext:
    return ActorContext(subject_id=uuid4(), workspace_id=uuid4(), scopes=scopes_for_role(role))


@pytest.mark.parametrize(
    ("role", "action", "allowed"),
    [
        (Role.OWNER, "members.manage", True),
        (Role.EDITOR, "members.manage", False),
        (Role.VIEWER, "workspace.read", True),
        (Role.OWNER, "unregistered.action", False),
        ("intruder", "workspace.read", False),
    ],
)
def test_scope_policy(role, action, allowed):
    assert ScopePermissionPolicy().allows(actor(role), action) is allowed

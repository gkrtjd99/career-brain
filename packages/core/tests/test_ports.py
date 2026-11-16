from uuid import uuid4

from career_core.ports import ActorContext, DenyAllEgressPolicy, DenyAllPermissionPolicy


def test_actor_scopes():
    actor = ActorContext(subject_id=uuid4(), workspace_id=uuid4(), scopes=frozenset({"read"}))
    assert actor.has_scope("read")
    assert not actor.has_scope("draft-write")


def test_default_policies_deny():
    actor = ActorContext(subject_id=uuid4(), workspace_id=uuid4())
    assert DenyAllPermissionPolicy().allows(actor, "artifact.read") is False
    assert DenyAllEgressPolicy().allows(actor, "openai", "private", "draft") is False

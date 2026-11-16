from __future__ import annotations

from uuid import UUID, uuid4

from career_core.db.models import Actor, Membership, Workspace
from career_core.db.session import Database
from career_core.domain.workspace import Role


async def create_workspace(
    db: Database, *, name: str, owner_display_name: str
) -> tuple[UUID, UUID]:
    """새 공간과 owner actor를 만든다. 반환: (workspace_id, actor_id)."""
    workspace_id, actor_id = uuid4(), uuid4()
    async with db.workspace_session(workspace_id) as s:
        s.add(Workspace(id=workspace_id, name=name))
        s.add(Actor(id=actor_id, display_name=owner_display_name))
        await s.flush()
        s.add(Membership(workspace_id=workspace_id, actor_id=actor_id, role=Role.OWNER))
    return workspace_id, actor_id

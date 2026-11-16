"""검색 색인 작업 예약. 다른 usecase가 순환 import 없이 부를 수 있게 따로 둔다."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from career_core.db import Database, Job

INDEX_JOB = "embed"


async def request_index(s: AsyncSession, workspace_id: UUID) -> None:
    """같은 트랜잭션 안에서 색인 작업을 예약한다. 이미 대기 중이면 아무것도 하지 않는다."""
    await s.execute(
        insert(Job)
        .values(id=uuid4(), workspace_id=workspace_id, kind=INDEX_JOB, target_id=workspace_id)
        .on_conflict_do_nothing(
            index_elements=["kind", "target_id"],
            index_where=text("status in ('queued', 'running')"),
        )
    )


async def enqueue_index(db: Database, workspace_id: UUID) -> None:
    async with db.workspace_session(workspace_id) as s:
        await request_index(s, workspace_id)

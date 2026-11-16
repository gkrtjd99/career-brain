"""검색 색인 작업 예약. 다른 usecase가 순환 import 없이 부를 수 있게 따로 둔다.

실행 중에 바뀐 자료를 놓치지 않는 규칙 (색인 작업은 공간당 대기·실행 1개):

- 예약하는 쪽은 같은 트랜잭션 안에서 공간별 advisory lock을 **공유**로 잡고 작업을 넣는다 (커밋 때 풀림).
- 색인 작업은 남은 대상을 고를 때마다 같은 lock을 **배타**로 잡는다. 남은 대상이 없으면 그 트랜잭션
  안에서 작업을 `done`으로 바꾼다.
- 그래서 (1) 마지막 확인 전에 커밋한 변경은 그 확인이 보고, (2) 확인 뒤에 예약하는 쪽은 실행 중인 작업이
  이미 끝난 것을 보고 새 작업을 넣는다. 확인과 종료 사이에 끼는 변경이 없다.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from career_core.db import Database, Job

INDEX_JOB = "embed"
_LOCK_KEY = "SELECT hashtextextended('career-brain:index:' || CAST(:ws AS text), 0)"


async def request_index(s: AsyncSession, workspace_id: UUID) -> None:
    """같은 트랜잭션 안에서 색인 작업을 예약한다. 이미 대기 중이면 아무것도 하지 않는다."""
    await s.execute(
        text(f"SELECT pg_advisory_xact_lock_shared(({_LOCK_KEY}))"), {"ws": str(workspace_id)}
    )
    await s.execute(
        insert(Job)
        .values(id=uuid4(), workspace_id=workspace_id, kind=INDEX_JOB, target_id=workspace_id)
        .on_conflict_do_nothing(
            index_elements=["kind", "target_id"],
            index_where=text("status in ('queued', 'running')"),
        )
    )


async def lock_for_check(s: AsyncSession, workspace_id: UUID) -> None:
    """색인 작업의 "남은 대상" 확인용 배타 lock (트랜잭션 끝까지). 예약 트랜잭션이 끝나기를 기다린다."""
    await s.execute(text(f"SELECT pg_advisory_xact_lock(({_LOCK_KEY}))"), {"ws": str(workspace_id)})


async def finish_running(s: AsyncSession, workspace_id: UUID) -> None:
    """실행 중인 색인 작업을 이 트랜잭션 안에서 끝낸다 (worker도 뒤이어 같은 값을 쓴다)."""
    await s.execute(
        text(
            "UPDATE core.jobs SET status = 'done', locked_until = NULL "
            "WHERE kind = :kind AND target_id = :ws AND status = 'running'"
        ),
        {"kind": INDEX_JOB, "ws": workspace_id},
    )


async def enqueue_index(db: Database, workspace_id: UUID) -> None:
    async with db.workspace_session(workspace_id) as s:
        await request_index(s, workspace_id)

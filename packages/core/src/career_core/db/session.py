from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import URL, make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from career_core.db.rls import WORKSPACE_SETTING


def normalize_url(url: str | URL) -> URL:
    u = make_url(url)
    if u.drivername in ("postgresql", "postgres"):
        u = u.set(drivername="postgresql+psycopg")
    return u


class Database:
    """런타임 DB 접근. 사용자 데이터는 반드시 ``workspace_session``으로 읽고 쓴다."""

    def __init__(self, url: str | URL, **engine_kw: Any) -> None:
        self.engine = create_async_engine(normalize_url(url), **engine_kw)
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def workspace_session(self, workspace_id: UUID) -> AsyncIterator[AsyncSession]:
        """트랜잭션 1개 + 트랜잭션 로컬 공간 문맥. 종료 시 commit(예외면 rollback)되고
        set_config(..., true)는 트랜잭션과 함께 사라지므로 풀에 반환된 연결에 남지 않는다."""
        async with self._sessions() as session, session.begin():
            await session.execute(
                text("SELECT set_config(:key, :ws, true)"),
                {"key": WORKSPACE_SETTING, "ws": str(workspace_id)},
            )
            yield session

    @asynccontextmanager
    async def system_session(self) -> AsyncIterator[AsyncSession]:
        """공간 문맥 없음. RLS 테이블은 0행으로 보인다 (신원 조회 함수 호출 등에만 사용)."""
        async with self._sessions() as session, session.begin():
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()

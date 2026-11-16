"""개인용 실행 앱. 코어를 라이브러리로 조립한다."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from career_core.db import Database
from career_core.interfaces.http import CoreDeps, build_router
from fastapi import FastAPI

from personal_api.identity import PersonalTokenResolver
from personal_api.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await db.dispose()

    app = FastAPI(title="career-brain (personal)", lifespan=lifespan)
    app.include_router(
        build_router(CoreDeps(identity=PersonalTokenResolver(db), db=db)), prefix="/api"
    )
    return app


app = create_app()

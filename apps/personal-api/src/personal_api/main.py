"""개인용 실행 앱. 코어를 라이브러리로 조립한다."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from career_core.adapters.blobstore import LocalBlobStore
from career_core.db import Database
from career_core.interfaces.http import CoreDeps, build_router
from career_core.interfaces.http.limits import MULTIPART_OVERHEAD, BodySizeLimit
from career_core.interfaces.http.restore import RESTORE_UPLOAD_PATH
from career_core.llm.gateway import ModelGateway
from fastapi import FastAPI

from personal_api.identity import PersonalTokenResolver, mcp_oauth_from_settings
from personal_api.models import load_config, load_prices, shared_client_factory
from personal_api.settings import Settings
from personal_api.web import mount_web, web_dir_from_env


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings.database_url)
    models = load_config(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await db.dispose()

    app = FastAPI(title="career-brain (personal)", lifespan=lifespan)
    # 업로드가 처리 함수에 닿기 전에 크기를 막는다 (Starlette는 본문을 먼저 다 받아 둔다).
    # 웹 복원 업로드 경로만 상한을 넓힌다 (본문 = 묶음 파일 그대로, 임시 폴더에 흘려 쓴다).
    app.add_middleware(
        BodySizeLimit,
        max_bytes=settings.max_upload_bytes + MULTIPART_OVERHEAD,
        overrides={("POST", f"/api{RESTORE_UPLOAD_PATH}"): settings.max_restore_bytes},
    )
    deps = CoreDeps(
        identity=PersonalTokenResolver(db),
        db=db,
        blobs=LocalBlobStore(settings.blob_dir),
        max_upload_bytes=settings.max_upload_bytes,
        max_restore_bytes=settings.max_restore_bytes,
        monthly_budget_usd=models.budget.monthly_usd,
        models=ModelGateway(db, models, load_prices(models), client_factory=shared_client_factory),
        mcp_oauth=mcp_oauth_from_settings(settings, db),
    )
    if deps.mcp_oauth is not None:
        from career_core.interfaces.mcp.oauth import add_metadata_routes

        add_metadata_routes(app, deps.mcp_oauth.config)  # /.well-known/… (접두어 밖)
    app.include_router(build_router(deps), prefix="/api")
    mount_web(app, web_dir_from_env())  # 마지막: /api 경로가 먼저 매칭된다
    return app


app = create_app()

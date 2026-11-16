"""개인용 실행 앱. 코어를 라이브러리로 조립한다."""

from __future__ import annotations

from career_core.interfaces.http import build_router
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="career-brain (personal)")
    app.include_router(build_router(), prefix="/api")
    return app


app = create_app()

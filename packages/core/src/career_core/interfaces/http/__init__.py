"""공통 FastAPI router. 각 실행 앱이 include_router로 조립한다."""

from __future__ import annotations

from fastapi import APIRouter

from career_core import __version__


def build_router() -> APIRouter:
    router = APIRouter(prefix="/core", tags=["core"])

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "core_version": __version__}

    return router


__all__ = ["build_router"]

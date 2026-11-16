"""빌드된 웹 앱(`apps/personal-web/dist`)을 API와 같은 주소에서 내준다.

- `CAREER_WEB_DIR`가 없으면 `apps/personal-web/dist`(현재 폴더 기준)가 있을 때만 쓴다.
  빈 값(`CAREER_WEB_DIR=`)이면 끈다. 폴더가 없으면 아무것도 붙이지 않는다 (API만).
- `/api/...`는 가리지 않는다. 없는 API 경로는 index.html이 아니라 404다.
- 클라이언트 라우팅: 파일이 없는 경로는 index.html (SPA fallback).
- 해시가 붙은 `assets/`는 오래 캐시하고, index.html은 매번 확인하게 한다.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

DEFAULT_WEB_DIR = "apps/personal-web/dist"
API_PREFIX = "api"
_IMMUTABLE = "public, max-age=31536000, immutable"


def web_dir_from_env() -> Path | None:
    raw = os.environ.get("CAREER_WEB_DIR")
    if raw is None:
        raw = DEFAULT_WEB_DIR
    if not raw.strip():
        return None
    path = Path(raw)
    return path if (path / "index.html").is_file() else None


class SpaStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        first = path.split("/", 1)[0]
        if first == API_PREFIX:
            raise HTTPException(status_code=404)
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            # 확장자가 있는 경로(없는 js·이미지 등)는 그대로 404. 나머지는 앱 화면으로.
            if exc.status_code != 404 or "." in path.rsplit("/", 1)[-1]:
                raise
            response = await super().get_response("index.html", scope)
        if first == "assets" and response.status_code in (200, 304):
            response.headers["Cache-Control"] = _IMMUTABLE
        elif response.media_type == "text/html":
            response.headers["Cache-Control"] = "no-cache"
        return response


def mount_web(app: FastAPI, web_dir: Path | None) -> bool:
    """API 라우터를 모두 붙인 뒤에 호출한다 (먼저 붙은 경로가 우선)."""
    if web_dir is None:
        return False
    app.mount("/", SpaStaticFiles(directory=web_dir, html=True), name="web")
    return True

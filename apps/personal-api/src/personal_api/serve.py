"""`personal-api serve`: 작은 서버용 all-in-one 실행.

한 프로세스가 API(uvicorn) + 작업 worker(별도 스레드) + 빌드된 웹 정적 파일을 맡는다.
migration은 하지 않는다 (`personal-api migrate`를 배포 때 따로 실행).

환경변수 (명령행 인자가 우선):
  CAREER_HOST              기본 127.0.0.1 (컨테이너 이미지는 0.0.0.0)
  CAREER_PORT              기본 8000
  CAREER_EMBEDDED_WORKER   기본 1. 0이면 API만 (worker는 `personal-api worker`로 따로)
  CAREER_WORKER_LISTEN     기본 1. 새 작업을 LISTEN/NOTIFY로 바로 알아챔 (0이면 짧은 간격 확인만)
  CAREER_WORKER_POLL_SECONDS  대기 작업이 없을 때 확인 간격, 기본 30 (LISTEN 끄면 1)
  CAREER_MAINTENANCE_INTERVAL_SECONDS  주기 유지보수(초안 정리) 간격, 기본 21600, 0이면 끔
  CAREER_WEB_DIR           빌드된 웹 앱 폴더 (personal_api.web 참고)
  CAREER_LOG_LEVEL         기본 info
  CAREER_ACCESS_LOG        기본 1
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from personal_api.runner import (
    BackgroundWorker,
    job_steps,
    maintenance_interval_seconds,
    worker_listen,
    worker_poll_seconds,
)
from personal_api.settings import Settings

log = logging.getLogger("uvicorn.error")

# 휴대폰 공유(Web Share Target)는 공유한 글·주소를 ``/capture?text=…`` 쿼리로 연다. 접속 기록에 그 글이
# 남지 않게 이 경로의 쿼리는 지운다 (웹 앱이 곧바로 주소창에서도 지운다).
_QUIET_QUERY_PATHS = ("/capture",)


class DropCaptureQuery(logging.Filter):
    """uvicorn 접속 기록 한 줄의 경로에서 ``/capture``의 쿼리를 뺀다."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path, _, query = args[2].partition("?")
            if query and path in _QUIET_QUERY_PATHS:
                record.args = (*args[:2], path, *args[3:])
        return True


def quiet_capture_queries() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, DropCaptureQuery) for f in access.filters):
        access.addFilter(DropCaptureQuery())


def env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def attach_worker(app: FastAPI, worker: BackgroundWorker) -> None:
    """앱 lifespan에 worker를 붙인다: 앱 시작 뒤 켜고, 종료 때 앱 정리보다 먼저 멈춘다."""
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a: Any) -> AsyncIterator[Any]:
        async with inner(a) as state:
            worker.start()
            log.info("job worker: running in-process")
            try:
                yield state
            finally:
                await asyncio.to_thread(worker.stop)

    app.router.lifespan_context = lifespan


def serve(
    settings: Settings,
    *,
    host: str | None = None,
    port: int | None = None,
    worker: bool | None = None,
) -> None:
    import uvicorn

    from personal_api.cli import build_handlers
    from personal_api.main import app

    if worker is None:
        worker = env_flag("CAREER_EMBEDDED_WORKER", True)
    if worker:
        listen = worker_listen()
        steps = job_steps(
            settings.database_url,
            lambda db: build_handlers(settings, db),
            listen=listen,
            maintenance_seconds=maintenance_interval_seconds(),
        )
        attach_worker(app, BackgroundWorker(steps, poll_seconds=worker_poll_seconds(listen)))
    quiet_capture_queries()
    uvicorn.run(
        app,
        host=host or os.environ.get("CAREER_HOST", "127.0.0.1"),
        port=port or int(os.environ.get("CAREER_PORT", "8000")),
        log_level=os.environ.get("CAREER_LOG_LEVEL", "info"),
        access_log=env_flag("CAREER_ACCESS_LOG", True),
        server_header=False,
    )

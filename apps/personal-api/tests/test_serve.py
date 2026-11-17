"""all-in-one 실행: 내장 worker 루프와 웹 정적 파일."""

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.db import ParseRun
from career_core.domain.workspace import Role, scopes_for_role
from career_core.ports import ActorContext
from career_core.usecases.sources import upload_source
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from personal_api.cli import build_handlers
from personal_api.main import create_app
from personal_api.runner import BackgroundWorker, job_steps, work_loop
from personal_api.serve import attach_worker, env_flag, quiet_capture_queries
from personal_api.settings import Settings


def _steps(step, opened: list):
    @asynccontextmanager
    async def source():
        opened.append("open")
        try:
            yield step
        finally:
            opened.append("closed")

    return source


def test_work_loop_processes_until_idle_then_waits_for_stop():
    async def main():
        stop = asyncio.Event()
        results = iter([True, True, False])

        async def step():
            r = next(results, False)
            if not r:
                stop.set()
            return r

        return await work_loop(step, stop, poll_seconds=0.01)

    assert asyncio.run(main()) == 2


def test_work_loop_survives_step_errors():
    async def main():
        stop = asyncio.Event()
        calls = []

        async def step():
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionError("db restarting")
            stop.set()
            return True

        return await work_loop(step, stop, poll_seconds=0.001), len(calls)

    assert asyncio.run(main()) == (1, 2)


def test_background_worker_runs_in_own_thread_and_stops():
    seen = []
    opened = []

    async def step():
        seen.append(threading.current_thread().name)
        return len(seen) <= 3

    w = BackgroundWorker(_steps(step, opened), poll_seconds=0.01)
    w.start()
    deadline = time.monotonic() + 5
    while len(seen) < 5 and time.monotonic() < deadline:
        time.sleep(0.01)
    w.stop(timeout=5)
    assert not w.alive
    assert w.processed == 3
    assert set(seen) == {"career-worker"}
    assert opened == ["open", "closed"]  # DB 등 자원은 worker 루프 안에서 열고 닫는다


def test_background_worker_cancels_a_stuck_job():
    opened = []
    started = threading.Event()

    async def step():
        started.set()
        await asyncio.sleep(3600)
        return True

    w = BackgroundWorker(_steps(step, opened), poll_seconds=0.01)
    w.start()
    assert started.wait(5)
    t0 = time.monotonic()
    w.stop(timeout=0.2)
    assert not w.alive
    assert time.monotonic() - t0 < 5
    assert opened == ["open", "closed"]


def test_worker_follows_app_lifespan():
    events = []

    async def step():
        events.append("step")
        return False

    app = FastAPI()
    w = BackgroundWorker(_steps(step, events), poll_seconds=0.01)
    attach_worker(app, w)
    assert not w.alive
    with TestClient(app):
        assert w.alive
    assert not w.alive
    assert events[0] == "open" and events[-1] == "closed"


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, True), ("", True), ("0", False), ("false", False), ("1", True)]
)
def test_env_flag(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("CAREER_X", raising=False)
    else:
        monkeypatch.setenv("CAREER_X", raw)
    assert env_flag("CAREER_X", True) is expected


def test_access_log_drops_shared_text_from_capture():
    """휴대폰 공유로 연 ``/capture?text=…``의 글이 접속 기록에 남지 않는다 (다른 경로는 그대로)."""
    import logging

    import uvicorn

    quiet_capture_queries()
    # uvicorn.run이 로깅을 다시 설정해도 거름이 남는다
    uvicorn.Config(FastAPI()).configure_logging()
    access = logging.getLogger("uvicorn.access")

    def line(path: str) -> str:
        record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:5000", "GET", path, "1.1", 200),
            None,
        )
        assert access.filter(record)
        return record.getMessage()

    assert "합성 비밀" not in line("/capture?text=합성 비밀&url=https%3A%2F%2Fa.test")
    assert '"GET /capture HTTP/1.1"' in line("/capture?text=x")
    assert "/api/core/job-postings?status=draft" in line("/api/core/job-postings?status=draft")
    quiet_capture_queries()
    assert sum(type(f).__name__ == "DropCaptureQuery" for f in access.filters) == 1


def test_background_worker_parses_an_upload(pg, tmp_path):
    """실제 DB: API 쪽에서 올린 자료를 내장 worker가 파싱한다."""
    settings = replace(Settings(), database_url=pg.app.render_as_string(hide_password=False))
    settings = replace(settings, blob_dir=str(tmp_path), models_config=str(tmp_path / "none.toml"))

    async def upload():
        from career_core.db import Database
        from sqlalchemy.pool import NullPool

        db = Database(settings.database_url, poolclass=NullPool)
        try:
            ws, subject = await create_workspace(db, name="w", owner_display_name="me")
            actor = ActorContext(subject, ws, scopes_for_role(Role.OWNER))
            up = await upload_source(
                db,
                LocalBlobStore(settings.blob_dir),
                actor,
                filename="notes.txt",
                data="합성 자료: 장애 대응 당번을 맡았다.".encode(),
                declared_mime="text/plain",
                max_bytes=1 << 20,
            )
            return db, ws, up.parse_run_id
        except BaseException:
            await db.dispose()
            raise

    async def status(db, ws, run_id):
        async with db.workspace_session(ws) as s:
            return (await s.get(ParseRun, run_id)).status

    async def main():
        db, ws, run_id = await upload()
        w = BackgroundWorker(
            job_steps(settings.database_url, lambda d: build_handlers(settings, d)),
            poll_seconds=0.05,
        )
        w.start()
        try:
            deadline = time.monotonic() + 20
            while (st := await status(db, ws, run_id)) != "complete":
                assert time.monotonic() < deadline, st
                await asyncio.sleep(0.05)
        finally:
            await asyncio.to_thread(w.stop)
            await db.dispose()
        assert w.error is None

    asyncio.run(main())


@pytest.fixture
def web_dir(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")
    (tmp_path / "assets" / "app-1234.js").write_text("console.log(1)")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    return tmp_path


def test_web_app_is_served_without_shadowing_api(monkeypatch, web_dir):
    monkeypatch.setenv("CAREER_WEB_DIR", str(web_dir))
    client = TestClient(create_app(Settings()))

    root = client.get("/")
    assert root.status_code == 200 and "root" in root.text
    assert root.headers["cache-control"] == "no-cache"

    deep = client.get("/experiences/123")  # 클라이언트 라우팅
    assert deep.status_code == 200 and "root" in deep.text

    asset = client.get("/assets/app-1234.js")
    assert asset.status_code == 200
    assert "immutable" in asset.headers["cache-control"]
    assert client.get("/favicon.svg").status_code == 200
    assert client.get("/assets/missing.js").status_code == 404

    assert client.get("/api/core/health").json()["status"] == "ok"
    missing_api = client.get("/api/core/nope")
    assert missing_api.status_code == 404
    assert "root" not in missing_api.text
    assert client.get("/api").status_code == 404
    # OAuth가 꺼져 있으면 메타데이터 경로도 앱 화면이 아니라 404
    assert client.get("/.well-known/oauth-protected-resource").status_code == 404


def test_web_is_off_when_dir_missing_or_disabled(monkeypatch, tmp_path, web_dir):
    monkeypatch.setenv("CAREER_WEB_DIR", str(tmp_path / "nope"))
    assert TestClient(create_app(Settings())).get("/").status_code == 404
    monkeypatch.setenv("CAREER_WEB_DIR", "")
    assert TestClient(create_app(Settings())).get("/").status_code == 404

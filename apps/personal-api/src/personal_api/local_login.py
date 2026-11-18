"""이 컴퓨터에서 바로 열기: bootstrap·토큰 붙여넣기 없이.

개인용 앱은 내 컴퓨터에서 ``personal-api serve``로 띄운다. 같은 컴퓨터의 브라우저가 처음 열면 웹 앱이
``POST /api/local/session``을 부르고, 서버는 "내 공간"의 owner 토큰을 새로 만들어 돌려준다. 공간이 아직
없으면 그때 만든다. 다른 기기(휴대폰 등)는 지금처럼 토큰을 넣는다.

받는 요청 (하나라도 아니면 404 — 이런 길이 있다는 것도 알리지 않는다):

- ``CAREER_LOCAL_LOGIN``이 꺼져 있지 않다 (기본 켜짐. 컨테이너 이미지는 0.0.0.0에 붙고 요청이 loopback이
  아니라 저절로 안 된다).
- 보낸 곳이 이 컴퓨터다 (``request.client``가 loopback).
- ``Host``가 이 컴퓨터 이름이다 (127.0.0.1·localhost·::1·*.localhost). 같은 컴퓨터의 reverse proxy가
  바깥 주소로 넘긴 요청, DNS rebinding을 막는다.
- 같은 화면에서 보낸 요청이다: ``X-Career-Local: 1`` 머리글(다른 사이트는 preflight 없이 못 붙인다),
  ``Origin``이 있으면 이 서버 주소, ``Sec-Fetch-Site``가 있으면 ``same-origin``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
from urllib.parse import urlsplit

from career_core.db import Database
from career_core.usecases.workspaces import create_workspace
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from personal_api.identity import issue_token

LOCAL_HEADER = "x-career-local"
TOKEN_LABEL = "이 컴퓨터"
WORKSPACE_NAME = "내 공간"
OWNER_NAME = "나"
_LOCAL_NAMES = {"127.0.0.1", "localhost", "::1"}


def local_login_enabled() -> bool:
    raw = os.environ.get("CAREER_LOCAL_LOGIN", "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _host_name(host_header: str) -> str:
    # "127.0.0.1:8000", "[::1]:8000", "localhost"
    return (urlsplit(f"//{host_header}").hostname or "").lower()


def is_local_request(request: Request) -> bool:
    client = request.client.host if request.client else None
    if not _is_loopback(client):
        return False
    host = request.headers.get("host", "")
    name = _host_name(host)
    if name not in _LOCAL_NAMES and not name.endswith(".localhost"):
        return False
    if request.headers.get(LOCAL_HEADER) != "1":
        return False
    origin = request.headers.get("origin")
    if origin is not None and origin.lower() != f"{request.url.scheme}://{host}".lower():
        return False
    site = request.headers.get("sec-fetch-site")
    return site is None or site == "same-origin"


async def local_session_token(db: Database, lock: asyncio.Lock) -> str:
    """내 공간의 owner 토큰을 새로 만든다. 공간이 없으면 만든다 (탭 두 개가 동시에 와도 하나만)."""
    async with lock:
        async with db.system_session() as s:
            row = (
                await s.execute(
                    text("SELECT workspace_id, actor_id FROM personal.local_login_owner()")
                )
            ).first()
        if row is None:
            ws, actor = await create_workspace(
                db, name=WORKSPACE_NAME, owner_display_name=OWNER_NAME
            )
        else:
            ws, actor = row.workspace_id, row.actor_id
        return await issue_token(db, workspace_id=ws, actor_id=actor, label=TOKEN_LABEL)


def local_login_router(db: Database) -> APIRouter:
    router = APIRouter()
    lock = asyncio.Lock()

    @router.post("/local/session", include_in_schema=False)
    async def local_session(request: Request) -> JSONResponse:
        if not local_login_enabled() or not is_local_request(request):
            raise HTTPException(status_code=404)
        token = await local_session_token(db, lock)
        return JSONResponse({"token": token}, headers={"Cache-Control": "no-store"})

    return router

"""MCP streamable HTTP 전송 (POST 하나에 JSON 응답 하나, 서버 → 클라이언트 스트림 없음).

- 경로: ``POST /core/mcp`` (개인 앱에서는 ``/api/core/mcp``).
- 인증: ``Authorization: Bearer <API 토큰>`` — HTTP API와 같은 ``IdentityResolver``. 요청마다 확인한다
  (토큰을 폐기하면 바로 막힌다). 공간은 토큰이 정하고 도구 인자로 받지 않는다.
  ``deps.mcp_oauth``가 있으면 외부 인가 서버의 JWT도 받는다 (``oauth.py``). 401에는 보호 자원
  메타데이터 주소(``resource_metadata``)를 알려 준다.
- ``Origin`` 헤더가 있으면 같은 호스트여야 한다 (브라우저를 이용한 DNS rebinding 차단).
- ``GET``(SSE 스트림)은 제공하지 않으므로 405.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

import json
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from career_core.interfaces.mcp.server import (
    INVALID_REQUEST,
    PARSE_ERROR,
    SUPPORTED_PROTOCOL_VERSIONS,
    McpServer,
)

MAX_BODY_BYTES = 1024 * 1024
MAX_BATCH = 20


def _rpc_error(code: int, message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}},
        status_code=status_code,
    )


def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True  # 브라우저가 아닌 클라이언트 (Claude Code·Desktop 등)
    return urlsplit(origin).netloc.lower() == (request.headers.get("host") or "").lower()


def add_mcp_routes(router: APIRouter, deps) -> None:
    server = McpServer(deps, channel="mcp-http")
    oauth = getattr(deps, "mcp_oauth", None)  # interfaces.mcp.oauth.McpOAuth | None

    @router.post("/mcp", include_in_schema=False)
    async def mcp(request: Request) -> Response:
        if not _origin_ok(request):
            return JSONResponse({"detail": "origin not allowed"}, status_code=403)
        auth = request.headers.get("authorization") or ""
        scheme, _, token = auth.partition(" ")
        token = token.strip() if scheme.lower() == "bearer" else ""
        actor = None
        if token:
            # 기본은 API 토큰. 설정했으면 외부 인가 서버의 JWT도 (oauth.py, 공간은 DB 연결이 정한다)
            actor = await deps.identity.resolve(token)
            if actor is None and oauth is not None:
                actor = await oauth.resolver.resolve(token)
        if actor is None:
            challenge = (
                oauth.config.www_authenticate(invalid_token=bool(token))
                if oauth is not None
                else "Bearer"
            )
            return JSONResponse(
                {"detail": "authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": challenge},
            )
        version = request.headers.get("mcp-protocol-version")
        if version and version not in SUPPORTED_PROTOCOL_VERSIONS:
            return _rpc_error(INVALID_REQUEST, "unsupported MCP-Protocol-Version")
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            return _rpc_error(INVALID_REQUEST, "request too large", 413)
        try:
            message = json.loads(body)
        except ValueError:
            return _rpc_error(PARSE_ERROR, "parse error")
        if isinstance(message, list):
            if not message or len(message) > MAX_BATCH:
                return _rpc_error(INVALID_REQUEST, "invalid batch")
            replies = [r for m in message if (r := await server.handle(m, actor)) is not None]
            return JSONResponse(replies) if replies else Response(status_code=202)
        reply = await server.handle(message, actor)
        if reply is None:
            return Response(status_code=202)
        return JSONResponse(reply)

    @router.get("/mcp", include_in_schema=False)
    async def mcp_stream() -> Response:
        return Response(status_code=405, headers={"Allow": "POST"})

    @router.delete("/mcp", include_in_schema=False)
    async def mcp_close() -> Response:
        return Response(status_code=405, headers={"Allow": "POST"})


__all__ = ["add_mcp_routes"]

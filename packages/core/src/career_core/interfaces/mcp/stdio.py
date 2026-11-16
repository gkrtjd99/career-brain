"""MCP stdio 전송: 한 줄에 JSON-RPC 메시지 하나 (stdin → stdout). 로그는 stderr로만.

토큰은 환경변수로 받고 메시지마다 ``IdentityResolver``로 다시 확인한다 (폐기하면 바로 막힌다).
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from typing import IO, Any

from career_core.interfaces.mcp.server import PARSE_ERROR, McpServer
from career_core.ports import ActorContext

UNAUTHORIZED = -32001


async def _readline(stream: IO[str]) -> str:
    return await asyncio.to_thread(stream.readline)


def _write(stream: IO[str], message: Any) -> None:
    stream.write(json.dumps(message, ensure_ascii=False) + "\n")
    stream.flush()


async def run_stdio(
    server: McpServer,
    resolve: Callable[[], Awaitable[ActorContext | None]],
    *,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    while True:
        line = await _readline(stdin)
        if not line:
            return  # 클라이언트가 닫음
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            _write(
                stdout,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": PARSE_ERROR, "message": "parse error"},
                },
            )
            continue
        actor = await resolve()
        if actor is None:
            if isinstance(message, dict) and "id" in message:
                _write(
                    stdout,
                    {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "error": {"code": UNAUTHORIZED, "message": "invalid or revoked token"},
                    },
                )
            continue
        messages = message if isinstance(message, list) else [message]
        replies = [r for m in messages if (r := await server.handle(m, actor)) is not None]
        if replies:
            _write(stdout, replies if isinstance(message, list) else replies[0])


__all__ = ["run_stdio"]

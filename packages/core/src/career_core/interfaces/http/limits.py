"""요청 본문 크기 상한 (ASGI 미들웨어).

FastAPI/Starlette는 업로드를 처리 함수 전에 디스크·tmpfs에 다 받아 둔다. 그래서 처리 함수 안의
크기 검사로는 늦다. 여기서 Content-Length를 먼저 보고, 길이가 없으면(chunked) 받는 동안 센다.
"""

from __future__ import annotations

from typing import Any

MULTIPART_OVERHEAD = 256 * 1024  # 폼 경계·필드 몫


class BodySizeLimit:
    """``overrides``: (메서드, 전체 경로) → 그 경로만의 상한. 정확히 같은 경로만 (예: 웹 복원 업로드
    ``("POST", "/api/core/maintenance/restore")``). 나머지는 모두 ``max_bytes``.
    """

    def __init__(
        self, app: Any, max_bytes: int, overrides: dict[tuple[str, str], int] | None = None
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.overrides = {(m.upper(), p): n for (m, p), n in (overrides or {}).items()}

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.overrides.get((scope.get("method", ""), scope.get("path", "")), self.max_bytes)
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    await _reply_413(send)
                    return
                if declared > limit:
                    await _reply_413(send)
                    return
        seen = 0
        started = False
        rejected = False

        async def counted_receive():
            nonlocal seen, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    # 길이 없이(chunked) 넘치면 413을 바로 보내고, 앱에는 연결이 끊긴 것으로 알린다.
                    rejected = True
                    if not started:
                        await _reply_413(send)
                    return {"type": "http.disconnect"}
            return message

        async def tracked_send(message):
            nonlocal started
            if rejected:
                return  # 이미 413을 보냈다: 앱의 응답은 버린다
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, tracked_send)
        except Exception:
            if not rejected:
                raise


async def _reply_413(send) -> None:
    body = b'{"detail":"too_large"}'
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})

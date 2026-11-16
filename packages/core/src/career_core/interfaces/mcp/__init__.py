"""MCP 인터페이스 (O08). 업무 단위 도구만, HTTP와 같은 usecase·같은 권한 정책.

- ``McpServer``: JSON-RPC 처리 (``server.py``), 도구 목록은 ``tools.py``.
- 전송: streamable HTTP ``POST /core/mcp`` (``http.py``), stdio (``stdio.py``).
- 연결 방법: ``docs/architecture/mcp.md``.
"""

from career_core.interfaces.mcp.server import McpServer, as_agent
from career_core.interfaces.mcp.tools import TOOLS

__all__ = ["TOOLS", "McpServer", "as_agent"]

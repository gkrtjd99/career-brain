"""``AuditSink`` 기본 구현: ``core.audit_events``(추가·조회만, FORCE RLS)에 한 줄씩 남긴다.

원문 인자·토큰·본문은 받지 않는다. 정해진 칸만 저장하고 나머지 속성은 버린다.
"""

from __future__ import annotations

import logging
from typing import Any

from career_core.db.models import AuditEvent
from career_core.db.session import Database
from career_core.ports import ActorContext

log = logging.getLogger(__name__)

# 칸으로 저장하는 속성. 그 밖의 키는 저장하지 않는다 (원문이 섞여 들어오지 않게).
_COLUMNS = ("channel", "tool", "status", "error_code", "duration_ms", "args_sha256")
# attributes JSON에 둘 수 있는 짧은 값 (예: MCP 규격 판)
_EXTRA = ("protocol_version",)
_MAX_TEXT = 100


def _short(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:_MAX_TEXT]


class DbAuditSink:
    """공간 문맥으로 한 트랜잭션에 한 줄. 실패해도 호출한 쪽 작업은 막지 않는다 (경고만)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def record(self, actor: ActorContext | None, event: str, attributes: dict) -> None:
        if actor is None:
            return  # 공간이 정해지지 않은 사건(인증 실패 등)은 공간 기록에 넣지 않는다
        attrs = {k: attributes.get(k) for k in _COLUMNS}
        status = "ok" if attrs["status"] == "ok" else "error"
        try:
            async with self.db.workspace_session(actor.workspace_id) as s:
                s.add(
                    AuditEvent(
                        workspace_id=actor.workspace_id,
                        actor_id=actor.subject_id,
                        actor_kind=actor.kind,
                        channel=_short(attrs["channel"]) or "unknown",
                        event=_short(event) or "unknown",
                        tool=_short(attrs["tool"]),
                        status=status,
                        error_code=_short(attrs["error_code"]),
                        duration_ms=max(0, int(attrs["duration_ms"] or 0)),
                        args_sha256=attrs["args_sha256"],
                        attributes={k: _short(attributes[k]) for k in _EXTRA if attributes.get(k)},
                    )
                )
        except Exception as exc:  # noqa: BLE001 - 감사 기록 실패가 도구 결과를 바꾸지 않게
            log.warning("audit record failed: %s", type(exc).__name__)


__all__ = ["DbAuditSink"]

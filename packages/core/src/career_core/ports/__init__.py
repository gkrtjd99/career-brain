"""코어가 바깥(개인용 앱 / 서비스)에 요구하는 최소 인터페이스.

구현은 각 composition root(apps/personal-api, career-service/apps/api)가 주입한다.
기본값이 '모두 허용'이 되지 않도록, 주입되지 않은 정책은 거부로 동작해야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ActorContext:
    """인증 결과. workspace는 요청 body가 아니라 IdentityResolver가 확정한다."""

    subject_id: UUID
    workspace_id: UUID
    scopes: frozenset[str] = field(default_factory=frozenset)

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


@runtime_checkable
class IdentityResolver(Protocol):
    """세션 또는 MCP 토큰을 ActorContext로 변환한다. 실패 시 None."""

    async def resolve(self, credential: str) -> ActorContext | None: ...


@runtime_checkable
class PermissionPolicy(Protocol):
    def allows(self, actor: ActorContext, action: str, resource_id: UUID | None = None) -> bool: ...


@runtime_checkable
class EgressPolicy(Protocol):
    """어떤 자료를 어떤 외부 제공자(LLM/OCR/검색)에 보낼 수 있는지 판단한다."""

    def allows(self, actor: ActorContext, provider: str, data_class: str, purpose: str) -> bool: ...


@runtime_checkable
class BudgetPolicy(Protocol):
    """작업 시작 전 예약, 완료 후 정산. 같은 idempotency_key로 이중 차감하지 않는다."""

    async def reserve(self, actor: ActorContext, kind: str, amount: int, idempotency_key: str) -> str | None: ...

    async def settle(self, reservation_id: str, actual: int) -> None: ...

    async def release(self, reservation_id: str) -> None: ...


@runtime_checkable
class BlobStore(Protocol):
    async def put(self, actor: ActorContext, key: str, data: bytes, content_type: str) -> str: ...

    async def get(self, actor: ActorContext, key: str) -> bytes: ...


@runtime_checkable
class NotificationSink(Protocol):
    async def emit(self, actor: ActorContext, event: str, payload: dict) -> None: ...


@runtime_checkable
class AuditSink(Protocol):
    """본문을 최소화한 감사 사건 기록. 원문·토큰을 넣지 않는다."""

    async def record(self, actor: ActorContext | None, event: str, attributes: dict) -> None: ...


class DenyAllPermissionPolicy:
    """주입이 빠졌을 때의 안전한 기본값."""

    def allows(self, actor: ActorContext, action: str, resource_id: UUID | None = None) -> bool:
        return False


class DenyAllEgressPolicy:
    def allows(self, actor: ActorContext, provider: str, data_class: str, purpose: str) -> bool:
        return False


__all__ = [
    "ActorContext",
    "AuditSink",
    "BlobStore",
    "BudgetPolicy",
    "DenyAllEgressPolicy",
    "DenyAllPermissionPolicy",
    "EgressPolicy",
    "IdentityResolver",
    "NotificationSink",
    "PermissionPolicy",
]

"""코어가 바깥(개인용 앱 / 서비스)에 요구하는 최소 인터페이스.

구현은 각 composition root(apps/personal-api, career-service/apps/api)가 주입한다.
기본값이 '모두 허용'이 되지 않도록, 주입되지 않은 정책은 거부로 동작해야 한다.
"""

from __future__ import annotations

from collections.abc import Sequence
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

    async def reserve(
        self, actor: ActorContext, kind: str, amount: int, idempotency_key: str
    ) -> str | None: ...

    async def settle(self, reservation_id: str, actual: int) -> None: ...

    async def release(self, reservation_id: str) -> None: ...


@runtime_checkable
class BlobStore(Protocol):
    """원본·결과 저장. key는 내용의 sha256 hex. 권한 검사는 호출하는 usecase가 먼저 한다."""

    async def put(self, workspace_id: UUID, key: str, data: bytes, content_type: str) -> str: ...

    async def get(self, workspace_id: UUID, key: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    page: int | None
    text: str


@dataclass(frozen=True, slots=True)
class LocalParseResult:
    """``pages_total``이 None이면 쪽 개념이 없는 자료(텍스트·HTML)."""

    blocks: tuple[ParsedBlock, ...]
    pages_total: int | None
    pages_with_text: frozenset[int] = frozenset()


class ParseError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@runtime_checkable
class LocalParser(Protocol):
    """외부 전송 없이 이 서버 안에서 실행하는 추출기."""

    def parse(self, data: bytes) -> LocalParseResult: ...


@dataclass(frozen=True, slots=True)
class ProviderParseStatus:
    state: str  # "pending" | "done" | "failed"
    blocks: tuple[ParsedBlock, ...] = ()
    returned_pages: frozenset[int] = frozenset()
    error: str | None = None


@runtime_checkable
class ParserProvider(Protocol):
    """외부 파서(OCR 등). 제출 후에는 provider_job_id로 상태만 조회한다.

    호출 전에 반드시 EgressPolicy를 검사한다. 다른 제공자로의 자동 전환도 새 전송이다.
    """

    name: str

    async def submit(
        self, data: bytes, mime_type: str, pages: Sequence[int], idempotency_key: str
    ) -> str: ...

    async def poll(self, provider_job_id: str) -> ProviderParseStatus: ...


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
    "LocalParseResult",
    "LocalParser",
    "NotificationSink",
    "ParseError",
    "ParsedBlock",
    "ParserProvider",
    "PermissionPolicy",
    "ProviderParseStatus",
]

"""코어가 바깥(개인용 앱 / 서비스)에 요구하는 최소 인터페이스.

구현은 각 composition root(apps/personal-api, career-service/apps/api)가 주입한다.
기본값이 '모두 허용'이 되지 않도록, 주입되지 않은 정책은 거부로 동작해야 한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from career_core.llm.types import (
    EmbeddingCompletion,
    JudgmentCompletion,
    ModelCompletion,
    ModelRequest,
    RerankCompletion,
)

ACTOR_HUMAN = "human"
ACTOR_AGENT = "agent"
APPROVE_SCOPE = "approve"


@dataclass(frozen=True, slots=True)
class ActorContext:
    """인증 결과. workspace는 요청 body가 아니라 IdentityResolver가 확정한다.

    ``kind``: 같은 토큰이라도 AI 클라이언트(MCP)나 자동 실행을 거치면 ``agent``다.
    agent는 ``approve`` 범위를 쓸 수 없다 (AI는 승인을 요청할 수 있지만 스스로 승인하지 않는다).
    """

    subject_id: UUID
    workspace_id: UUID
    scopes: frozenset[str] = field(default_factory=frozenset)
    kind: str = ACTOR_HUMAN
    # 인증에 쓴 자격 증명(API 토큰 등)의 ID. 원문이 아니다. "지금 쓰는 토큰은 폐기하지 않는다"에 쓴다.
    credential_id: UUID | None = None

    @property
    def is_human(self) -> bool:
        return self.kind == ACTOR_HUMAN

    def has_scope(self, scope: str) -> bool:
        if scope == APPROVE_SCOPE and not self.is_human:
            return False
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
class ModelClient(Protocol):
    """LLM 제공자 하나. 게이트웨이(`career_core.llm.gateway`)만 직접 부른다."""

    async def complete(self, model: str, request: ModelRequest) -> ModelCompletion: ...


@runtime_checkable
class EmbeddingClient(Protocol):
    """임베딩 제공자 하나 (API 또는 로컬 모델). 게이트웨이만 직접 부른다.

    input_type: "query"(검색어) 또는 "passage"(색인할 본문). 모델에 따라 접두어가 다르다.
    """

    async def embed(
        self, model: str, texts: Sequence[str], *, input_type: str, dimensions: int | None
    ) -> EmbeddingCompletion: ...


@runtime_checkable
class RerankClient(Protocol):
    """검색 결과 다시 정렬 (로컬 cross-encoder). 게이트웨이만 직접 부른다.

    점수는 보낸 글 순서 그대로 하나씩. 척도는 모델마다 다르다 (순서만 쓴다).
    """

    async def rerank(
        self, model: str, query: str, documents: Sequence[str]
    ) -> RerankCompletion: ...


@runtime_checkable
class JudgmentClient(Protocol):
    """판정 모델 하나 (TypeSafe System One). 글을 만들지 않고 질문마다 확률·점수를 돌려준다. 게이트웨이만 부른다.

    ``state``: 판단할 자료 (JSON 객체). ``questions``: 질문 이름 → 질문 (noul·score·choice).
    """

    async def judge(
        self, model: str, state: dict, questions: dict[str, dict]
    ) -> JudgmentCompletion: ...


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


@runtime_checkable
class HiddenTextParser(Protocol):
    """숨김 서식 글을 뺀 블록도 만들 수 있는 로컬 추출기 (c0029, 지금은 DOCX).

    ``parse_runs.hidden_text = 'exclude'``인 파싱은 ``parse`` 대신 이것을 부른다.
    """

    def parse_visible(self, data: bytes) -> LocalParseResult: ...


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


@dataclass(frozen=True, slots=True)
class ApiTokenRecord:
    """API 토큰 목록 한 줄. 토큰 원문·해시는 넣지 않는다."""

    id: UUID
    label: str
    actor_id: UUID
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


@runtime_checkable
class ApiTokenAdmin(Protocol):
    """actor 공간의 API 토큰 관리 (개인 앱의 로그인 토큰). 주입하지 않으면 토큰 경로가 없다.

    권한 검사(소유자만)는 호출하는 쪽(HTTP 경로)이 한다. 구현은 그 공간 문맥 안에서만 읽고 쓴다.
    """

    async def list(self, actor: ActorContext) -> list[ApiTokenRecord]: ...

    async def issue(
        self, actor: ActorContext, *, label: str, expires_days: int | None
    ) -> tuple[ApiTokenRecord, str]:
        """새 토큰. (목록 한 줄, 원문) — 원문은 이때 한 번만 돌려준다."""
        ...

    async def revoke(self, actor: ActorContext, token_id: UUID) -> bool: ...

    # 선택 (c0025): 이름 바꾸기·만료 연장. 없는 구현이면 HTTP 경로(PATCH /core/tokens/{id})가 없다.
    # async def update(self, actor, token_id, *, label: str | None, expires_days: int | None)
    #     -> ApiTokenRecord | None   # 없으면 None, 규칙 위반은 ApiTokenUpdateError


class ApiTokenUpdateError(ValueError):
    """토큰 이름·만료 변경 거부. ``code``: ``token_revoked``·``token_expired``·``not_an_extension``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DenyAllPermissionPolicy:
    """주입이 빠졌을 때의 안전한 기본값."""

    def allows(self, actor: ActorContext, action: str, resource_id: UUID | None = None) -> bool:
        return False


class DenyAllEgressPolicy:
    def allows(self, actor: ActorContext, provider: str, data_class: str, purpose: str) -> bool:
        return False


__all__ = [
    "ACTOR_AGENT",
    "ACTOR_HUMAN",
    "APPROVE_SCOPE",
    "ActorContext",
    "ApiTokenAdmin",
    "ApiTokenRecord",
    "ApiTokenUpdateError",
    "AuditSink",
    "BlobStore",
    "BudgetPolicy",
    "DenyAllEgressPolicy",
    "DenyAllPermissionPolicy",
    "EgressPolicy",
    "EmbeddingClient",
    "HiddenTextParser",
    "IdentityResolver",
    "JudgmentClient",
    "LocalParseResult",
    "LocalParser",
    "ModelClient",
    "NotificationSink",
    "ParseError",
    "ParsedBlock",
    "ParserProvider",
    "PermissionPolicy",
    "ProviderParseStatus",
    "RerankClient",
]

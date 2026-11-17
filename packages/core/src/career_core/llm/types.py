"""모델 호출의 공통 타입. 제공자 SDK 타입은 어댑터 밖으로 새지 않는다."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass(frozen=True, slots=True)
class ImageInput:
    """이미지 입력 (공고 포스터 등). 마지막 user 메시지에 붙는다."""

    media_type: str  # image/png | image/jpeg | image/webp | image/gif
    data: bytes


@dataclass(frozen=True, slots=True)
class ModelRequest:
    messages: tuple[ChatMessage, ...]
    system: str | None = None
    max_output_tokens: int = 4096
    # 요청에 담긴 자료의 등급. 게이트웨이가 egress 판단에 쓴다. 비어 있으면 거부한다.
    data_classes: frozenset[str] = frozenset()
    # 이미지 입력. 비전 모델 라우트에만 보낸다 (라우트 선택은 호출하는 쪽 책임).
    images: tuple[ImageInput, ...] = ()


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """정규화한 토큰 사용량.

    input_tokens는 캐시가 아닌 입력만 센다 (기본 단가로 과금되는 몫).
    output_tokens는 추론(reasoning) 토큰을 포함한다 (제공자가 따로 세어도 합쳐 둔다).
    reasoning_tokens는 참고용 내역이다.
    cache_write_tokens는 짧은 TTL 쓰기, cache_write_long_tokens는 긴 TTL 쓰기.
    """

    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_write_long_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def prompt_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cached_input_tokens
            + self.cache_write_tokens
            + self.cache_write_long_tokens
        )

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            self.input_tokens + other.input_tokens,
            self.cached_input_tokens + other.cached_input_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
            self.cache_write_long_tokens + other.cache_write_long_tokens,
            self.output_tokens + other.output_tokens,
            self.reasoning_tokens + other.reasoning_tokens,
        )


@dataclass(frozen=True, slots=True)
class UsagePart:
    """실제로 돈 낸 단위. 폴백이 돌면 모델별로 여러 개가 된다."""

    model: str
    usage: TokenUsage


@dataclass(frozen=True, slots=True)
class ModelCompletion:
    """어댑터가 돌려주는 원시 결과."""

    text: str
    model: str  # 응답을 만든 모델 (폴백이면 요청과 다를 수 있다)
    stop_reason: str | None
    parts: tuple[UsagePart, ...]
    request_id: str | None = None
    # 제공자가 응답에 실어 준 실제 청구액 (xAI `cost_in_usd_ticks`). 있으면 단가표보다 우선한다.
    reported_cost_usd: Decimal | None = None

    @property
    def usage(self) -> TokenUsage:
        total = TokenUsage()
        for part in self.parts:
            total = total + part.usage
        return total


@dataclass(frozen=True, slots=True)
class ModelResult:
    """게이트웨이가 호출자에게 돌려주는 결과 (비용 포함)."""

    text: str
    endpoint: str
    model: str
    stop_reason: str | None
    usage: TokenUsage
    cost_usd: str | None  # Decimal 문자열. 모르면 None (0이 아니다)
    cost_basis: str
    call_id: str
    notes: tuple[str, ...] = field(default=())


class ModelCallError(Exception):
    """어댑터가 던지는 실패. code는 기록·재시도 판단용 짧은 이름."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class ModelDenied(Exception):
    """호출 전에 막힘: egress, 예산, 가격 미상, 라우트 없음."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


EMBED_QUERY = "query"
EMBED_PASSAGE = "passage"


@dataclass(frozen=True, slots=True)
class EmbeddingCompletion:
    """임베딩 어댑터의 원시 결과. 장부 기록을 위해 ModelCompletion과 같은 이름의 필드를 둔다."""

    vectors: tuple[tuple[float, ...], ...]
    model: str
    parts: tuple[UsagePart, ...] = ()
    request_id: str | None = None
    stop_reason: str | None = None
    reported_cost_usd: Decimal | None = None

    @property
    def usage(self) -> TokenUsage:
        total = TokenUsage()
        for part in self.parts:
            total = total + part.usage
        return total


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vectors: tuple[tuple[float, ...], ...]
    endpoint: str
    model: str
    dimensions: int
    # 같은 검색 공간에 넣어도 되는지 판단하는 이름. 모델·차원이 다르면 공간이 다르다.
    space: str
    cost_usd: str | None
    cost_basis: str
    call_id: str


@dataclass(frozen=True, slots=True)
class RerankCompletion:
    """다시 정렬 어댑터의 원시 결과: 글마다 점수 하나(클수록 질문에 맞음, 보낸 순서 그대로).

    장부 기록을 위해 ModelCompletion과 같은 이름의 필드를 둔다.
    """

    scores: tuple[float, ...]
    model: str
    parts: tuple[UsagePart, ...] = ()
    request_id: str | None = None
    stop_reason: str | None = None
    reported_cost_usd: Decimal | None = None

    @property
    def usage(self) -> TokenUsage:
        total = TokenUsage()
        for part in self.parts:
            total = total + part.usage
        return total


@dataclass(frozen=True, slots=True)
class RerankResult:
    """게이트웨이가 돌려주는 다시 정렬 결과. 점수는 보낸 글 순서 그대로."""

    scores: tuple[float, ...]
    endpoint: str
    model: str
    cost_usd: str | None
    cost_basis: str
    call_id: str

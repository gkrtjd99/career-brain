"""실행(run)·승인(approval)의 순수 규칙 (O08).

- 실행은 여러 단계로 된 오래 걸리는 작업이다. 단계가 끝날 때마다 재개 위치(position)를 저장하고,
  worker가 죽으면 다음 worker가 그 위치부터 다시 한다. 단계의 부수효과는 멱등 키로 한 번만 일어난다.
- 승인은 행위·대상·대상 버전·내용 해시·만료 시각에 묶인다. 대상이나 내용이 바뀌면 그 승인은 쓸 수 없다.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

RUN_JOB = "run"  # core.jobs.kind. target_id = runs.id


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    FAILED = "failed"
    DONE = "done"


TERMINAL_RUN_STATUSES = frozenset({RunStatus.CANCELLED, RunStatus.FAILED, RunStatus.DONE})


class StopRequest(StrEnum):
    """실행 중인 단계는 끊지 않는다. worker가 단계 사이에서 본다."""

    CANCEL = "cancel"
    INTERRUPT = "interrupt"


class RunEventKind(StrEnum):
    STATUS = "status"
    STEP = "step"
    WAIT = "wait"
    RESUMED = "resumed"
    ERROR = "error"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    USED = "used"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


OPEN_APPROVAL_STATUSES = frozenset({ApprovalStatus.PENDING, ApprovalStatus.APPROVED})

# 외부 제공자 한도. 대기 후 같은 엔드포인트로만 다시 시도한다 (다른·유료 제공자로 바꾸지 않는다).
WAIT_ERROR_CODES = frozenset({"rate_limited", "timeout", "connection_error"})
# 기다려도 풀리지 않는 한도·거부. 사유를 남기고 실패한다.
FAIL_ERROR_CODES = frozenset({"quota_exceeded", "budget_exceeded", "price_unknown"})


class RunRuleError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ApprovalError(ValueError):
    """승인을 만들거나 결정하거나 쓸 수 없음. ``code``가 사유다."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# 승인을 쓰려 했는데 대상·내용이 바뀐 경우. 이 승인은 다시 쓸 수 없게 표시한다.
INVALIDATING_CODES = frozenset({"target_changed", "payload_changed"})


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(value: Any) -> str:
    """승인 내용·대상 버전 해시. 키 순서·공백과 무관하다."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def wait_seconds(waits: int, base: float) -> float:
    """한도 대기 간격: base·2^n, 최대 15분."""
    return min(base * 2**waits, 900.0)


__all__ = [
    "FAIL_ERROR_CODES",
    "INVALIDATING_CODES",
    "OPEN_APPROVAL_STATUSES",
    "RUN_JOB",
    "TERMINAL_RUN_STATUSES",
    "WAIT_ERROR_CODES",
    "ApprovalError",
    "ApprovalStatus",
    "RunEventKind",
    "RunRuleError",
    "RunStatus",
    "StopRequest",
    "canonical_json",
    "content_hash",
    "wait_seconds",
]

"""원본 자료·파싱 상태."""

from __future__ import annotations

from enum import StrEnum


class DataClass(StrEnum):
    """외부 전송 등급. restricted는 어떤 제공자 설정으로도 외부에 보내지 않는다."""

    PUBLIC = "public"
    PRIVATE = "private"
    RESTRICTED = "restricted"


class ParseStatus(StrEnum):
    PENDING = "pending"
    WAITING_PROVIDER = "waiting_provider"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


TERMINAL_PARSE_STATUSES = frozenset({ParseStatus.COMPLETE, ParseStatus.PARTIAL, ParseStatus.FAILED})

# 파싱 파이프라인이 바뀌어 재처리가 필요할 때 올린다. 같은 (버전, 파이프라인)은 다시 처리하지 않는다.
PIPELINE_VERSION = 1

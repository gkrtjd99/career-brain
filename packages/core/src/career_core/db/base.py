from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

CORE_SCHEMA = "core"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """코어 소유 테이블 전용. 서비스·개인 앱 테이블은 각자의 metadata/schema를 쓴다."""

    metadata = MetaData(schema=CORE_SCHEMA)

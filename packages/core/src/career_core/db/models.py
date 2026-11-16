"""공간·actor·membership.

기본값은 Python 쪽에서 채운다(server default 미사용). RLS 아래에서 INSERT ... RETURNING이
아직 보이지 않는 행을 다시 읽으려다 실패하지 않게 하기 위해서다.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from career_core.db.base import CORE_SCHEMA, Base, utcnow


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Actor(Base):
    """코어가 아는 작성자. 로그인 계정과의 매핑은 각 실행 앱(개인/서비스)이 소유한다."""

    __tablename__ = "actors"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    display_name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint("role in ('owner', 'editor', 'viewer')", name="ck_memberships_role"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{CORE_SCHEMA}.actors.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    title: Mapped[str] = mapped_column(Text)
    data_class: Mapped[str] = mapped_column(Text)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceVersion(Base):
    __tablename__ = "source_versions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    source_id: Mapped[UUID]
    sha256: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mime_type: Mapped[str] = mapped_column(Text)
    original_filename: Mapped[str] = mapped_column(Text)
    blob_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ParseRun(Base):
    __tablename__ = "parse_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    source_version_id: Mapped[UUID]
    pipeline_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    requested_by: Mapped[UUID]
    pages_total: Mapped[int | None] = mapped_column(Integer, default=None)
    provider: Mapped[str | None] = mapped_column(Text, default=None)
    provider_job_id: Mapped[str | None] = mapped_column(Text, default=None)
    requested_pages: Mapped[list[int] | None] = mapped_column(ARRAY(Integer), default=None)
    missing_pages: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    error_code: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DocumentBlock(Base):
    __tablename__ = "document_blocks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    parse_run_id: Mapped[UUID]
    page: Mapped[int | None] = mapped_column(Integer, default=None)
    ordinal: Mapped[int] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    """작업 큐 행. 원문을 넣지 않고 대상 ID만 둔다. 사용자 API·MCP에 노출하지 않는다."""

    __tablename__ = "jobs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(Text)
    target_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(Text, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelCall(Base):
    """모델 호출 장부. 프롬프트·응답 본문은 저장하지 않는다 (토큰 수와 비용만).

    앱 role은 INSERT/SELECT만 가진다. 기록을 고쳐 비용을 줄일 수 없게 한다.
    """

    __tablename__ = "model_calls"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    actor_id: Mapped[UUID | None] = mapped_column(default=None)
    purpose: Mapped[str] = mapped_column(Text)
    endpoint: Mapped[str] = mapped_column(Text)
    pricing_provider: Mapped[str | None] = mapped_column(Text, default=None)
    requested_model: Mapped[str] = mapped_column(Text)
    served_model: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text, default=None)
    stop_reason: Mapped[str | None] = mapped_column(Text, default=None)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    billing: Mapped[str] = mapped_column(Text)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), default=None)
    cost_basis: Mapped[str] = mapped_column(Text)
    price_refs: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    latency_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    provider_request_id: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

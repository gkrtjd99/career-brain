"""공간·actor·membership.

기본값은 Python 쪽에서 채운다(server default 미사용). RLS 아래에서 INSERT ... RETURNING이
아직 보이지 않는 행을 다시 읽으려다 실패하지 않게 하기 위해서다.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from career_core.db.base import CORE_SCHEMA, Base, utcnow
from career_core.db.types import Vector


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
    # c0032: experience(내 경험 자료) | proof(증빙) | reference(참고). domain.sources.SourcePurpose
    purpose: Mapped[str] = mapped_column(Text, default="experience")
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # c0039: 넣을 때 용도를 확신하지 못해 사람의 확인을 기다림 (그동안 등급은 restricted), 제안의 이유
    purpose_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    purpose_hint: Mapped[str | None] = mapped_column(Text, default=None)
    # c0043: 브라우저에서 보낸 글의 원래 주소 (파일로 올린 원본은 null)
    origin_url: Mapped[str | None] = mapped_column(Text, default=None)


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
    # c0029: 숨김 서식 글을 블록에 넣는지 (keep) 빼는지 (exclude, DOCX만)
    hidden_text: Mapped[str] = mapped_column(Text, default="keep")
    # c0039: 이 기기(브라우저)·연결된 모델이 읽은 글의 기록 (글은 document_blocks)
    text_layer: Mapped[dict | None] = mapped_column(JSONB, default=None)


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
    # c0039: OCR이 준 쪽 안 위치 [x0, y0, x1, y1] (px). 없으면 None
    bbox: Mapped[list[int] | None] = mapped_column(JSONB, default=None)


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
    # c0023: 집을 때마다 새로 정하는 lease 값 (``claim_job``). 결과 저장 때 이 worker의 lease인지 확인한다
    lease_token: Mapped[UUID | None] = mapped_column(default=None)


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
    # c0016: 실행 단계 안의 호출이면 그 실행·단계 (실행 비용을 장부에서 다시 맞춘다)
    run_id: Mapped[UUID | None] = mapped_column(default=None)
    run_step: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- O04: 개인 사실 (Brain)


class SourceSpan(Base):
    """원문 블록 안의 글자 범위. quote는 그 범위를 그대로 복사한 값 (근거 표시용, 불변)."""

    __tablename__ = "source_spans"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    document_block_id: Mapped[UUID]
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Experience(Base):
    __tablename__ = "experiences"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    title: Mapped[str] = mapped_column(Text)
    organization: Mapped[str | None] = mapped_column(Text, default=None)
    role: Mapped[str | None] = mapped_column(Text, default=None)
    # 원문에 적힌 기간을 그대로 둔다. 날짜로 추측해 바꾸지 않는다.
    period_text: Mapped[str | None] = mapped_column(Text, default=None)
    # c0030 (O19): 종류·상하(한 단계)·월 단위 기간(그 달 1일)·태그. 모호한 기간은 비운다.
    kind: Mapped[str] = mapped_column(Text, default="other")
    parent_id: Mapped[UUID | None] = mapped_column(default=None)
    started_on: Mapped[date | None] = mapped_column(Date, default=None)
    ended_on: Mapped[date | None] = mapped_column(Date, default=None)
    ongoing: Mapped[bool] = mapped_column(default=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    status: Mapped[str] = mapped_column(Text, default="active")
    origin: Mapped[str] = mapped_column(Text)
    extraction_run_id: Mapped[UUID | None] = mapped_column(default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    parse_run_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(Text, default="pending")
    requested_by: Mapped[UUID]
    model_call_id: Mapped[UUID | None] = mapped_column(default=None)
    error_code: Mapped[str | None] = mapped_column(Text, default=None)
    blocks_total: Mapped[int] = mapped_column(Integer, default=0)
    blocks_sent: Mapped[int] = mapped_column(Integer, default=0)
    experiences_created: Mapped[int] = mapped_column(Integer, default=0)
    claims_created: Mapped[int] = mapped_column(Integer, default=0)
    claims_dropped: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    experience_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(Text)
    statement: Mapped[str] = mapped_column(Text)
    contribution: Mapped[str] = mapped_column(Text, default="unknown")
    status: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text)
    extraction_run_id: Mapped[UUID | None] = mapped_column(default=None)
    created_by: Mapped[UUID]
    confirmed_by: Mapped[UUID | None] = mapped_column(default=None)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    claim_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(Text)
    source_span_id: Mapped[UUID | None] = mapped_column(default=None)
    interview_answer_id: Mapped[UUID | None] = mapped_column(default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    experience_id: Mapped[UUID | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(Text, default="open")
    idempotency_key: Mapped[str | None] = mapped_column(Text, default=None)  # c0016
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InterviewQuestion(Base):
    __tablename__ = "interview_questions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    session_id: Mapped[UUID]
    ordinal: Mapped[int] = mapped_column(Integer)
    focus: Mapped[str] = mapped_column(Text)
    prompt: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text)  # template | ai
    model_call_id: Mapped[UUID | None] = mapped_column(default=None)
    request_id: Mapped[UUID | None] = mapped_column(default=None)  # c0016 AI 질문 요청
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InterviewAnswer(Base):
    """사용자가 직접 쓴 답. 근거로 쓰이므로 고치지 않는다 (다시 답하면 새 행)."""

    __tablename__ = "interview_answers"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    question_id: Mapped[UUID]
    answer: Mapped[str] = mapped_column(Text)
    answered_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Embedding(Base):
    """검색용 벡터. 본문 원문은 두지 않고 대상 ID와 본문 해시만 둔다."""

    __tablename__ = "embeddings"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    target_kind: Mapped[str] = mapped_column(Text)
    target_id: Mapped[UUID]
    space: Mapped[str] = mapped_column(Text)
    dims: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector())
    model_call_id: Mapped[UUID | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class JobPosting(Base):
    __tablename__ = "job_postings"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    canonical_url: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="draft")
    fields: Mapped[dict] = mapped_column(JSONB, default=dict)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    confirmed_version_id: Mapped[UUID | None] = mapped_column(default=None)
    pending_changes: Mapped[list] = mapped_column(JSONB, default=list)
    # c0033 (O13): 회사 조사의 회사 (같은 공간). 회사를 지우면 비워진다
    company_id: Mapped[UUID | None] = mapped_column(default=None)
    saved_by: Mapped[UUID | None] = mapped_column(default=None)
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class JobPostingVersion(Base):
    """가져온 공고 스냅샷. 앱 role은 추가·조회만 한다."""

    __tablename__ = "job_posting_versions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    posting_id: Mapped[UUID]
    version_no: Mapped[int] = mapped_column(Integer)
    final_url: Mapped[str] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer, default=None)
    method: Mapped[str] = mapped_column(
        Text
    )  # http | browser | manual_text | browser_capture (c0043)
    adapter: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    extracted: Mapped[dict] = mapped_column(JSONB)
    missing: Mapped[list] = mapped_column(JSONB)
    changed_fields: Mapped[list] = mapped_column(JSONB)
    model_call_id: Mapped[UUID | None] = mapped_column(default=None)
    screenshot_key: Mapped[str | None] = mapped_column(Text, default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobFetchRun(Base):
    __tablename__ = "job_fetch_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    posting_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(Text, default="pending")
    error_code: Mapped[str | None] = mapped_column(Text, default=None)
    http_status: Mapped[int | None] = mapped_column(Integer, default=None)
    version_id: Mapped[UUID | None] = mapped_column(default=None)
    unchanged: Mapped[bool] = mapped_column(default=False)
    candidates: Mapped[list] = mapped_column(JSONB, default=list)
    notes: Mapped[list] = mapped_column(JSONB, default=list)
    requested_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------- O07: 지원·문서


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    posting_id: Mapped[UUID]
    posting_version_id: Mapped[UUID | None] = mapped_column(default=None)
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="draft")
    result_note: Mapped[str | None] = mapped_column(Text, default=None)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ApplicationDocument(Base):
    __tablename__ = "application_documents"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    # NULL이면 공간 공용 문서 (c0012)
    application_id: Mapped[UUID | None] = mapped_column(default=None)
    kind: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    question_index: Mapped[int | None] = mapped_column(Integer, default=None)
    question_prompt: Mapped[str | None] = mapped_column(Text, default=None)
    char_limit: Mapped[int | None] = mapped_column(Integer, default=None)
    byte_limit: Mapped[int | None] = mapped_column(Integer, default=None)
    count_rule: Mapped[dict] = mapped_column(JSONB, default=dict)
    current_revision: Mapped[int] = mapped_column(Integer, default=0)
    # 복사해 온 원본 문서와 그때의 revision (c0012)
    source_document_id: Mapped[UUID | None] = mapped_column(default=None)
    source_revision_id: Mapped[UUID | None] = mapped_column(default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DocumentRevision(Base):
    """저장할 때마다 새로 생기는 문서 본문. 앱 role은 추가·조회만 한다."""

    __tablename__ = "document_revisions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    document_id: Mapped[UUID]
    revision_no: Mapped[int] = mapped_column(Integer)
    blocks: Mapped[list] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(Text)
    counts: Mapped[dict] = mapped_column(JSONB, default=dict)
    origin: Mapped[str] = mapped_column(Text, default="user")
    suggestion_id: Mapped[UUID | None] = mapped_column(default=None)
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    idempotency_key: Mapped[str | None] = mapped_column(Text, default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DocumentClaim(Base):
    """revision의 문단 ↔ 브레인 주장. 추가·조회만."""

    __tablename__ = "document_claims"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    revision_id: Mapped[UUID]
    block_id: Mapped[str] = mapped_column(Text)
    claim_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DocumentSuggestion(Base):
    """AI 수정안. 초안일 뿐 브레인 근거가 아니다."""

    __tablename__ = "document_suggestions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    document_id: Mapped[UUID]
    base_revision_id: Mapped[UUID]
    block_id: Mapped[str] = mapped_column(Text)
    original_text: Mapped[str] = mapped_column(Text)
    suggested_text: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(Text)
    reject_reasons: Mapped[list] = mapped_column(JSONB, default=list)
    model_call_id: Mapped[UUID | None] = mapped_column(default=None)
    applied_revision_id: Mapped[UUID | None] = mapped_column(default=None)
    idempotency_key: Mapped[str | None] = mapped_column(Text, default=None)  # c0012
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ApplicationSubmission(Base):
    """실제 제출본 스냅샷. 추가·조회만 (이후 경력·공고 수정이 소급되지 않는다)."""

    __tablename__ = "application_submissions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    application_id: Mapped[UUID]
    posting_id: Mapped[UUID]
    posting_version_id: Mapped[UUID | None] = mapped_column(default=None)
    posting: Mapped[dict] = mapped_column(JSONB)
    documents: Mapped[list] = mapped_column(JSONB)
    checks: Mapped[dict] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    submitted_by: Mapped[UUID]
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- O09: 학습 (Learning)
# 학습 기록은 계획이다. 이 테이블들은 claims/experiences에 쓰지 않는다
# (예외: 사용자가 직접 올린 경험 후보 → StudyClaimCandidate).


class StudyRequirement(Base):
    """저장 공고의 요건 한 줄 (확정 당시 문장 그대로). 앱 role은 추가·조회만 한다."""

    __tablename__ = "study_requirements"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    posting_id: Mapped[UUID]
    field: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StudyRequirementClaim(Base):
    __tablename__ = "study_requirement_claims"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    requirement_id: Mapped[UUID]
    claim_id: Mapped[UUID]
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StudyAssessment(Base):
    __tablename__ = "study_assessments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    title: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="planned")
    requirement_id: Mapped[UUID | None] = mapped_column(default=None)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class StudyItem(Base):
    __tablename__ = "study_items"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    title: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="planned")
    requirement_id: Mapped[UUID | None] = mapped_column(default=None)
    assessment_id: Mapped[UUID | None] = mapped_column(default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class StudySession(Base):
    __tablename__ = "study_sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    study_item_id: Mapped[UUID]
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    minutes: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    resources: Mapped[list] = mapped_column(JSONB, default=list)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StudyAttempt(Base):
    """시험·연습 응시 1회. 앱 role은 추가·조회만 한다."""

    __tablename__ = "study_attempts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    assessment_id: Mapped[UUID | None] = mapped_column(default=None)
    study_item_id: Mapped[UUID | None] = mapped_column(default=None)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[str] = mapped_column(Text)
    score: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=None)
    max_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=None)
    minutes: Mapped[int | None] = mapped_column(Integer, default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StudyMistake(Base):
    """오답과 그 복습 상태 (SM-2 축약: repetitions·interval_days·ease 천분율·due_at)."""

    __tablename__ = "study_mistakes"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    attempt_id: Mapped[UUID | None] = mapped_column(default=None)
    study_item_id: Mapped[UUID | None] = mapped_column(default=None)
    question: Mapped[str] = mapped_column(Text)
    wrong_answer: Mapped[str | None] = mapped_column(Text, default=None)
    correct_answer: Mapped[str | None] = mapped_column(Text, default=None)
    reason: Mapped[str] = mapped_column(Text)
    reason_note: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(Text, default="open")
    repetitions: Mapped[int] = mapped_column(Integer, default=0)
    interval_days: Mapped[int] = mapped_column(Integer, default=0)
    ease: Mapped[int] = mapped_column(Integer, default=2500)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class StudyReview(Base):
    """복습 1회 기록. 앱 role은 추가·조회만 한다."""

    __tablename__ = "study_reviews"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    mistake_id: Mapped[UUID]
    grade: Mapped[str] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    interval_before: Mapped[int] = mapped_column(Integer)
    interval_after: Mapped[int] = mapped_column(Integer)
    ease_after: Mapped[int] = mapped_column(Integer)
    due_at_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StudyClaimCandidate(Base):
    """사용자가 학습 결과를 경험 후보로 올린 기록. 주장은 항상 extracted로 시작한다."""

    __tablename__ = "study_claim_candidates"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    study_item_id: Mapped[UUID]
    attempt_id: Mapped[UUID | None] = mapped_column(default=None)
    claim_id: Mapped[UUID]
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- O08: 실행·승인 (Automation)
# 실행 기록에는 원문을 넣지 않는다 (대상 ID와 짧은 결과만).


class Run(Base):
    """오래 걸리는 여러 단계 작업 하나. 단계가 끝날 때마다 ``position``(재개 위치)을 저장한다."""

    __tablename__ = "runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    workflow: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="queued")
    input: Mapped[dict] = mapped_column(JSONB, default=dict)
    position: Mapped[dict] = mapped_column(JSONB, default=dict)
    step: Mapped[int] = mapped_column(Integer, default=0)
    steps_total: Mapped[int | None] = mapped_column(Integer, default=None)
    result: Mapped[dict | None] = mapped_column(JSONB, default=None)
    stop_requested: Mapped[str | None] = mapped_column(Text, default=None)
    wait_reason: Mapped[str | None] = mapped_column(Text, default=None)
    waits: Mapped[int] = mapped_column(Integer, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    approval_id: Mapped[UUID | None] = mapped_column(default=None)
    error_code: Mapped[str | None] = mapped_column(Text, default=None)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 10), default=Decimal(0))
    cost_unknown: Mapped[bool] = mapped_column(default=False)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    # c0016: 참이면 비용을 장부(model_calls.run_id)에서 계산한다. 이전에 만든 실행은 거짓
    cost_from_ledger: Mapped[bool] = mapped_column(default=True)
    resumes: Mapped[int] = mapped_column(Integer, default=0)
    actor_kind: Mapped[str] = mapped_column(Text, default="human")
    actor_scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    idempotency_key: Mapped[str | None] = mapped_column(Text, default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RunEvent(Base):
    """실행 사건. 추가·조회만."""

    __tablename__ = "run_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    run_id: Mapped[UUID]
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text, default=None)
    step: Mapped[int | None] = mapped_column(Integer, default=None)
    code: Mapped[str | None] = mapped_column(Text, default=None)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Approval(Base):
    """사람의 승인 1건. 행위·대상·대상 버전·내용 해시는 만든 뒤 바꿀 수 없다 (DB trigger)."""

    __tablename__ = "approvals"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    action: Mapped[str] = mapped_column(Text)
    target_kind: Mapped[str] = mapped_column(Text)
    target_id: Mapped[UUID]
    target_version: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(Text, default="pending")
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    requested_by: Mapped[UUID]
    requested_via: Mapped[str] = mapped_column(Text, default="human")
    run_id: Mapped[UUID | None] = mapped_column(default=None)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[UUID | None] = mapped_column(default=None)
    decided_via: Mapped[str | None] = mapped_column(Text, default=None)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    decision_note: Mapped[str | None] = mapped_column(Text, default=None)
    used_by: Mapped[UUID | None] = mapped_column(default=None)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    used_result_id: Mapped[UUID | None] = mapped_column(default=None)
    invalid_reason: Mapped[str | None] = mapped_column(Text, default=None)
    idempotency_key: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------- O11: 내보내기·복원


class ExportRun(Base):
    """내보내기·복원 1회 기록 (마지막 백업 시각 표시용). 앱 role은 추가·조회만 한다."""

    __tablename__ = "export_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    # export | restore | merge (c0018) | markdown (c0046, 읽기 전용 Markdown 묶음 — 백업으로 세지 않음)
    kind: Mapped[str] = mapped_column(Text)
    via: Mapped[str] = mapped_column(Text)  # cli | http
    created_by: Mapped[UUID | None] = mapped_column(default=None)
    format_version: Mapped[int] = mapped_column(Integer)
    archive_sha256: Mapped[str] = mapped_column(Text)
    archive_bytes: Mapped[int] = mapped_column(BigInteger)
    rows_total: Mapped[int] = mapped_column(BigInteger)
    blobs_total: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- c0012: 문서 설정·전형 단계


class DocumentSettings(Base):
    """공간의 문서 설정 1행. cliches가 NULL이면 기본 상투어 목록을 쓴다."""

    __tablename__ = "document_settings"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    cliches: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), default=None)
    own_contacts: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ApplicationStage(Base):
    """제출본에 묶인 전형 단계(서류·시험·면접)와 결과. 결과만 기록한다."""

    __tablename__ = "application_stages"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    application_id: Mapped[UUID]
    submission_id: Mapped[UUID]
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    outcome: Mapped[str] = mapped_column(Text, default="pending")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------- c0013: 학습 항목 병합


# 학습 설정(study_settings)은 c0021에서 공간 공용 설정(WorkspaceSettings)으로 옮겼다.


class StudyItemMerge(Base):
    """학습 항목 병합 기록: 합쳐진 항목(status = merged) → 합친 항목. 원래 항목마다 한 행."""

    __tablename__ = "study_item_merges"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    source_item_id: Mapped[UUID]
    target_item_id: Mapped[UUID]
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- c0014: 감사·삭제 기록·외부 신원


class AuditEvent(Base):
    """감사 사건 (MCP 도구 호출 등). 인자 원문 없이 sha256만. 앱 role은 추가·조회만 한다."""

    __tablename__ = "audit_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    actor_id: Mapped[UUID | None] = mapped_column(default=None)
    actor_kind: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text)
    event: Mapped[str] = mapped_column(Text)
    tool: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text, default=None)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    args_sha256: Mapped[str | None] = mapped_column(Text, default=None)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Deletion(Base):
    """사용자 삭제 기록 (DELETE trigger가 남긴다). 복원 뒤 다시 적용한다. 추가·조회만."""

    __tablename__ = "deletions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    target_table: Mapped[str] = mapped_column(Text)
    target_id: Mapped[UUID]
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OAuthIdentity(Base):
    """외부 인가 서버의 (issuer, subject) → 이 공간의 actor. 조회는 ``core.lookup_oauth_identity``."""

    __tablename__ = "oauth_identities"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    actor_id: Mapped[UUID]
    issuer: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


# ---------------------------------------------------------------- c0016: AI 요청 기록


class DocumentSuggestionRequest(Base):
    """AI 수정안 요청 1회. 모델이 되물은 질문 글과 개수를 남긴다. 추가·조회만."""

    __tablename__ = "document_suggestion_requests"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    document_id: Mapped[UUID]
    base_revision_id: Mapped[UUID]
    block_ids: Mapped[list] = mapped_column(JSONB, default=list)
    questions: Mapped[list] = mapped_column(JSONB, default=list)
    proposed: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    dropped: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(Text, default=None)
    run_id: Mapped[UUID | None] = mapped_column(default=None)
    model_call_id: Mapped[UUID | None] = mapped_column(default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InterviewQuestionRequest(Base):
    """AI 인터뷰 질문 요청 1회. 만든 질문은 ``interview_questions.request_id``로 묶인다. 추가·조회만."""

    __tablename__ = "interview_question_requests"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    session_id: Mapped[UUID]
    requested: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(Text, default=None)
    run_id: Mapped[UUID | None] = mapped_column(default=None)
    model_call_id: Mapped[UUID | None] = mapped_column(default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- c0019: 원격 MCP 토큰 기록·폐기


class OAuthToken(Base):
    """원격 MCP에서 본 외부 액세스 토큰 1개 (``jti`` 또는 토큰 sha256). 폐기하면 다음 요청부터 401."""

    __tablename__ = "oauth_tokens"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    identity_id: Mapped[UUID]
    issuer: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    token_ref: Mapped[str] = mapped_column(Text)
    ref_kind: Mapped[str] = mapped_column(Text)  # jti | sha256
    via: Mapped[str] = mapped_column(Text)  # jwt | introspection
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    uses: Mapped[int] = mapped_column(Integer, default=1)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_by: Mapped[UUID | None] = mapped_column(default=None)


# ---------------------------------------------------------------- c0021: 공간 공용 설정


class WorkspaceSettings(Base):
    """공간 공용 설정 (한 공간에 한 행).

    - timezone: 표시 시간대 (날짜 표시·복습 마감·D-day). NULL이면 기본값(Asia/Seoul).
    - tech_aliases: 사용자 기술명 별칭 ``[{term, aliases}]``. 기본 표(domain.korean) 위에 합쳐진다.
    """

    __tablename__ = "workspace_settings"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    timezone: Mapped[str | None] = mapped_column(Text, default=None)
    tech_aliases: Mapped[list] = mapped_column(JSONB, default=list)
    updated_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------- c0024: 웹 복원 작업


class RestoreJob(Base):
    """웹 복원 작업 하나 (공간마다 하나). 올린 파일은 공유 폴더에 있고 경로는 ID에서 정해진다.

    실행 중이면 돌리는 프로세스가 ``heartbeat_at``을 고친다. 오래되면 읽는 쪽이 ``interrupted``로 닫는다.
    """

    __tablename__ = "restore_jobs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    actor_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(Text)
    phase: Mapped[str | None] = mapped_column(Text, default=None)
    done: Mapped[int] = mapped_column(BigInteger, default=0)
    total: Mapped[int] = mapped_column(BigInteger, default=0)
    bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(Text)
    encrypted: Mapped[bool] = mapped_column(default=False)
    filename: Mapped[str | None] = mapped_column(Text, default=None)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    on_duplicate: Mapped[str | None] = mapped_column(Text, default=None)
    report: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    error_detail: Mapped[str | None] = mapped_column(Text, default=None)
    runner: Mapped[str | None] = mapped_column(Text, default=None)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # c0027: 확인·합치기를 시작한 사람 (worker가 이 사람으로 합친다), 고른 비슷한 행 쌍
    started_by: Mapped[UUID | None] = mapped_column(default=None)
    near_choices: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), default=None)


# ---------------------------------------------------------------- c0025: 공고 가져오기 비용 확인


class JobFetchConfirmation(Base):
    """스캔 PDF 비전 호출 전 예상 비용 확인 (c0025).

    예상 비용이 상한을 넘으면 가져오기 실행(``fetch_run_id``)을 ``needs_confirmation``으로 멈추고 이 행을
    ``pending``으로 남긴다. 사람이 확인하면 ``confirmed`` → 다음 가져오기가 같은 파일(``content_digest``)·
    같은 쪽·그 상한 이하일 때만 한 번 쓰고 ``used``(``used_run_id``). 거절 ``declined``, 새 확인 요청이
    생기면 옛 것은 ``superseded``. 금액은 USD, 토큰 수는 추정값이다.
    """

    __tablename__ = "job_fetch_confirmations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    posting_id: Mapped[UUID]
    fetch_run_id: Mapped[UUID]
    used_run_id: Mapped[UUID | None] = mapped_column(default=None)
    kind: Mapped[str] = mapped_column(Text, default="vision_cost")
    status: Mapped[str] = mapped_column(Text, default="pending")
    content_digest: Mapped[str] = mapped_column(Text)
    pages: Mapped[list] = mapped_column(JSONB, default=list)  # 읽을 쪽 (1부터)
    pages_total: Mapped[int] = mapped_column(Integer)
    images: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    estimated_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), default=None)
    max_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), default=None)
    threshold_usd: Mapped[Decimal] = mapped_column(Numeric(18, 10))
    endpoint: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[UUID | None] = mapped_column(default=None)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------- c0022: 판정 모델 결과 캐시


class JudgeCache(Base):
    """판정 모델 응답 캐시: (용도, 라우트 모델, 프롬프트 버전, 입력 해시)마다 한 행. 만료 뒤에는 쓰지 않는다.

    응답에 문서 인용이 들어 있어 공간 RLS 아래 둔다. 내보내기에서는 뺀다 (export.EXCLUDED_TABLES).
    """

    __tablename__ = "judge_cache"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    purpose: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str] = mapped_column(Text)
    response_text: Mapped[str] = mapped_column(Text)
    endpoint: Mapped[str] = mapped_column(Text)
    result_model: Mapped[str] = mapped_column(Text)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), default=None)
    cost_basis: Mapped[str] = mapped_column(Text)
    call_id: Mapped[UUID | None] = mapped_column(default=None)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # c0026: 요청에 들어간 주장·지원 문서. 그 행을 지우면 trigger `judge_cache_forget`이 이 응답도 지운다
    claim_ids: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid), default=list)
    document_ids: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid), default=list)


# ---------------------------------------------------------------- c0030: 화면에서 만든 모델 연결 (O22)


class ModelConnection(Base):
    """화면에서 만든 모델 연결 하나. 키는 여기 두지 않는다 (``ModelConnectionSecret``)."""

    __tablename__ = "model_connections"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    name: Mapped[str] = mapped_column(Text)
    preset: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str | None] = mapped_column(Text, default=None)
    billing: Mapped[str] = mapped_column(Text)
    price_as: Mapped[str | None] = mapped_column(Text, default=None)
    token_param: Mapped[str] = mapped_column(Text)
    data_classes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    api_key_last4: Mapped[str | None] = mapped_column(Text, default=None)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, default=None)
    updated_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ModelConnectionSecret(Base):
    """연결 키의 암호문 (Fernet). 내보내기에서 뺀다 (export.EXCLUDED_TABLES)."""

    __tablename__ = "model_connection_secrets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    connection_id: Mapped[UUID]
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:  # 암호문도 로그에 남기지 않는다
        return f"ModelConnectionSecret(connection_id={self.connection_id})"


class ModelRouteOverride(Base):
    """용도(purpose)마다 파일 라우트를 덮는 값."""

    __tablename__ = "model_route_overrides"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    purpose: Mapped[str] = mapped_column(Text)
    endpoint: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ModelSettings(Base):
    """공간당 1행: 월 예산 덮기(NULL = 파일 값)와 바뀔 때마다 오르는 ``version``."""

    __tablename__ = "model_settings"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    monthly_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), default=None)
    version: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------- c0033: 회사 조사 (O13, 참고 지식)


class Company(Base):
    """회사 하나. 참고 지식 칸이다: 이 표와 항목은 내 경험(주장)이 아니고 문서 근거가 되지 않는다."""

    __tablename__ = "companies"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    name: Mapped[str] = mapped_column(Text)
    # 정규화한 이름 (domain.companies.name_key). 공간 안에서 유일
    name_key: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    homepage: Mapped[str | None] = mapped_column(Text, default=None)
    corp_code: Mapped[str | None] = mapped_column(Text, default=None)
    business_no: Mapped[str | None] = mapped_column(Text, default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CompanyPage(Base):
    """회사 항목의 원문: 가져온 페이지 한 건 또는 붙여넣은 글. 인용은 이 글 안에서 찾는다."""

    __tablename__ = "company_pages"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    company_id: Mapped[UUID]
    origin: Mapped[str] = mapped_column(Text)  # url | paste
    url: Mapped[str | None] = mapped_column(Text, default=None)
    final_url: Mapped[str | None] = mapped_column(Text, default=None)
    title: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(Text, default="pending")  # pending | done | failed
    error_code: Mapped[str | None] = mapped_column(Text, default=None)
    http_status: Mapped[int | None] = mapped_column(Integer, default=None)
    method: Mapped[str | None] = mapped_column(Text, default=None)
    text: Mapped[str | None] = mapped_column(Text, default=None)
    content_hash: Mapped[str | None] = mapped_column(Text, default=None)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    model_call_id: Mapped[UUID | None] = mapped_column(default=None)
    candidates: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[list] = mapped_column(JSONB, default=list)
    requested_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CompanyFact(Base):
    """회사 항목 하나: 인용(원문 위치 + 그대로 복사한 글)과 기준일(모르면 비움)이 붙는다."""

    __tablename__ = "company_facts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    company_id: Mapped[UUID]
    page_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text)
    quote_start: Mapped[int] = mapped_column(Integer)
    quote_end: Mapped[int] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(Text, default=None)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    as_of: Mapped[date | None] = mapped_column(Date, default=None)
    as_of_precision: Mapped[str | None] = mapped_column(Text, default=None)  # year | month | day
    status: Mapped[str] = mapped_column(Text, default="candidate")  # candidate | confirmed
    origin: Mapped[str] = mapped_column(Text)  # model | user
    model_call_id: Mapped[UUID | None] = mapped_column(default=None)
    confirmed_by: Mapped[UUID | None] = mapped_column(default=None)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Credential(Base):
    """자격·이수 기록 하나 (c0034, plan §20): 자격증·어학·수료·수상·학력·그 밖의 증명. 경험(주장)이 아니다.

    ``field_spans``: 증빙에서 뽑은 칸의 원문 위치 ``{칸: {"block": 순번, "start", "end", "page"}}``
    (``parse_run_id``의 블록 안). 원문 글은 복사하지 않는다. 사람이 고친 칸은 위치가 없다.
    """

    __tablename__ = "credentials"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(Text)  # license | language | course | award | degree | other
    name: Mapped[str] = mapped_column(Text)
    issuer: Mapped[str | None] = mapped_column(Text, default=None)
    number: Mapped[str | None] = mapped_column(Text, default=None)
    acquired_on: Mapped[date | None] = mapped_column(Date, default=None)
    expires_on: Mapped[date | None] = mapped_column(Date, default=None)
    started_on: Mapped[date | None] = mapped_column(Date, default=None)
    ended_on: Mapped[date | None] = mapped_column(Date, default=None)
    score: Mapped[str | None] = mapped_column(Text, default=None)
    hours: Mapped[int | None] = mapped_column(Integer, default=None)
    detail: Mapped[str | None] = mapped_column(Text, default=None)
    # extracted | self_confirmed | evidence_supported
    status: Mapped[str] = mapped_column(Text)
    source_id: Mapped[UUID | None] = mapped_column(default=None)
    experience_id: Mapped[UUID | None] = mapped_column(default=None)
    parse_run_id: Mapped[UUID | None] = mapped_column(default=None)
    field_spans: Mapped[dict] = mapped_column(JSONB, default=dict)
    confirmed_by: Mapped[UUID | None] = mapped_column(default=None)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------- c0035: 참고 자료 보관함 (O14, 참고 지식)


class ReferenceItem(Base):
    """참고 글 하나 (다른 사람의 자기소개서·이력서·면접 후기·직무 글). 내 경험이 아니고 근거가 되지 않는다.

    ``text``는 저장한 글이다 (개인정보를 가리기로 했으면 가린 글). 원본(``source_id``)은 용도 ``reference``."""

    __tablename__ = "reference_items"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    company_id: Mapped[UUID | None] = mapped_column(default=None)
    role: Mapped[str | None] = mapped_column(Text, default=None)
    year: Mapped[int | None] = mapped_column(Integer, default=None)
    source_note: Mapped[str | None] = mapped_column(Text, default=None)
    source_id: Mapped[UUID | None] = mapped_column(default=None)
    data_class: Mapped[str] = mapped_column(Text, default="private")
    text: Mapped[str] = mapped_column(Text)
    pii_masked: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ReferenceNote(Base):
    """참고 글의 구조 메모: 문항·문단 구성·소재 종류·강조 역량. 원문 문장은 넣지 않는다."""

    __tablename__ = "reference_notes"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    reference_id: Mapped[UUID]
    question: Mapped[str | None] = mapped_column(Text, default=None)
    structure: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    materials: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    competencies: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    status: Mapped[str] = mapped_column(Text, default="candidate")  # candidate | confirmed
    origin: Mapped[str] = mapped_column(Text)  # model | user
    model_call_id: Mapped[UUID | None] = mapped_column(default=None)
    confirmed_by: Mapped[UUID | None] = mapped_column(default=None)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------- c0036: 프로필·증명사진 (O21)


class ProfilePhoto(Base):
    """사진 변환 도구가 만든 변환본 한 장 (원본은 두지 않는다). 메타데이터를 지운 바이트가 BlobStore에."""

    __tablename__ = "profile_photos"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    spec: Mapped[str] = mapped_column(Text)  # domain.profiles.PhotoSpec
    width_px: Mapped[int] = mapped_column(Integer)
    height_px: Mapped[int] = mapped_column(Integer)
    bytes: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(Text)  # image/jpeg | image/png
    blob_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Profile(Base):
    """공간마다 하나. 인적 사항·연락처·링크·한 줄 소개·기본 사진. 자료 등급 restricted로 다룬다."""

    __tablename__ = "profiles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    name: Mapped[str | None] = mapped_column(Text, default=None)
    name_en: Mapped[str | None] = mapped_column(Text, default=None)
    birth_date: Mapped[date | None] = mapped_column(Date, default=None)
    email: Mapped[str | None] = mapped_column(Text, default=None)
    phone: Mapped[str | None] = mapped_column(Text, default=None)
    address: Mapped[str | None] = mapped_column(Text, default=None)
    links: Mapped[list] = mapped_column(JSONB, default=list)  # [{label, url}]
    headline: Mapped[str | None] = mapped_column(Text, default=None)
    default_photo_id: Mapped[UUID | None] = mapped_column(default=None)
    # c0041: 병역·보훈·장애·희망 조건 (모두 선택, restricted: MCP·모델로 보내지 않는다)
    military_status: Mapped[str | None] = mapped_column(Text, default=None)
    military_branch: Mapped[str | None] = mapped_column(Text, default=None)
    military_rank: Mapped[str | None] = mapped_column(Text, default=None)
    military_started_on: Mapped[date | None] = mapped_column(Date, default=None)
    military_ended_on: Mapped[date | None] = mapped_column(Date, default=None)
    military_exemption_reason: Mapped[str | None] = mapped_column(Text, default=None)
    veteran: Mapped[bool | None] = mapped_column(Boolean, default=None)
    disability: Mapped[bool | None] = mapped_column(Boolean, default=None)
    desired_roles: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    desired_locations: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    desired_salary: Mapped[str | None] = mapped_column(Text, default=None)
    available_from: Mapped[str | None] = mapped_column(Text, default=None)
    employment_types: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    updated_by: Mapped[UUID | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------- c0037: 공고별 지원 준비 (O15)


class ApplicationPrep(Base):
    """지원 하나의 준비 문서: 문항마다 쓸 경험 후보·요건 충족표. 공고 버전·쓴 경험의 해시(basis)에 묶인다.

    글을 쓰지 않는다. 사람이 고른 경험(choices)은 다시 만들어도 남는다."""

    __tablename__ = "application_preps"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    application_id: Mapped[UUID]
    posting_id: Mapped[UUID]
    posting_version_id: Mapped[UUID | None] = mapped_column(default=None)
    mode: Mapped[str] = mapped_column(Text)  # model | search
    basis: Mapped[dict] = mapped_column(JSONB, default=dict)
    questions: Mapped[list] = mapped_column(JSONB, default=list)
    requirements: Mapped[list] = mapped_column(JSONB, default=list)
    company: Mapped[dict | None] = mapped_column(JSONB, default=None)
    choices: Mapped[dict] = mapped_column(JSONB, default=dict)
    notes: Mapped[list] = mapped_column(JSONB, default=list)
    model_call_id: Mapped[UUID | None] = mapped_column(default=None)
    generated_by: Mapped[UUID]
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------- c0040: 학력


class Education(Base):
    """학교 하나 (plan §20 "나 › 학력"). 입학·졸업은 월 단위(그 달 1일), 학점은 만점과 함께."""

    __tablename__ = "education"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    school: Mapped[str] = mapped_column(Text)
    campus: Mapped[str | None] = mapped_column(Text, default=None)
    major: Mapped[str | None] = mapped_column(Text, default=None)
    extra_major: Mapped[str | None] = mapped_column(Text, default=None)
    extra_major_kind: Mapped[str | None] = mapped_column(Text, default=None)  # double | minor
    degree: Mapped[str] = mapped_column(Text)  # domain.education.Degree
    status: Mapped[str] = mapped_column(Text)  # domain.education.EducationStatus
    transferred: Mapped[bool] = mapped_column(Boolean, default=False)
    entered_on: Mapped[date | None] = mapped_column(Date, default=None)
    graduated_on: Mapped[date | None] = mapped_column(Date, default=None)
    gpa: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    gpa_scale: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    source_id: Mapped[UUID | None] = mapped_column(default=None)
    experience_id: Mapped[UUID | None] = mapped_column(default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------- c0042: 공통 답변


class AnswerAsset(Base):
    """지원을 넘어 다시 쓰는 내 글 (plan §19.4). 근거는 확인한 사실(주장)만 잇는다."""

    __tablename__ = "answer_assets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(Text)  # domain.answers.AnswerKind
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, default="")
    source_submission_id: Mapped[UUID | None] = mapped_column(default=None)
    source_document_id: Mapped[UUID | None] = mapped_column(default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AnswerAssetClaim(Base):
    __tablename__ = "answer_asset_claims"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    answer_asset_id: Mapped[UUID]
    claim_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnswerAssetUse(Base):
    """공통 답변을 지원 문서에 넣은 기록."""

    __tablename__ = "answer_asset_uses"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    answer_asset_id: Mapped[UUID]
    application_id: Mapped[UUID]
    document_id: Mapped[UUID]
    revision_id: Mapped[UUID | None] = mapped_column(default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- c0047: 이력서 판본 (O17)


class ResumeVersion(Base):
    """이력서 판본 하나: 마스터 → 직무별 → 공고별. 위 판본을 복사해 시작하고 출처(판본·revision)를 남긴다."""

    __tablename__ = "resume_versions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(Text)  # master | role | posting
    title: Mapped[str] = mapped_column(Text)
    target_role: Mapped[str | None] = mapped_column(Text, default=None)
    parent_id: Mapped[UUID | None] = mapped_column(default=None)
    source_revision_id: Mapped[UUID | None] = mapped_column(default=None)
    application_id: Mapped[UUID | None] = mapped_column(default=None)
    template: Mapped[str] = mapped_column(Text, default="one_column")
    blind: Mapped[bool] = mapped_column(Boolean, default=False)
    max_pages: Mapped[int] = mapped_column(Integer, default=2)
    current_revision: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ResumeRevision(Base):
    """판본 본문 (``domain.resume`` 구역 JSON). 저장할 때마다 새 행, 앱 role은 추가·조회만."""

    __tablename__ = "resume_revisions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    version_id: Mapped[UUID]
    revision_no: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text)  # user | agent | copy | ai
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    idempotency_key: Mapped[str | None] = mapped_column(Text, default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResumeLineClaim(Base):
    """revision의 줄 ↔ 확인한 사실. 추가·조회만."""

    __tablename__ = "resume_line_claims"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    revision_id: Mapped[UUID]
    line_id: Mapped[str] = mapped_column(Text)
    claim_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- c0048: 면접 준비 (O16)


class InterviewPrep(Base):
    """지원 하나의 면접 준비: 예상 질문 묶음. 공고·제출본·회사·참고·경험의 해시(basis)에 묶인다.

    O04 경험 인터뷰(``interview_sessions``)와 다른 것이다: 그것은 경험을 채우려는 질문, 이것은 면접 대비 질문."""

    __tablename__ = "interview_preps"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    application_id: Mapped[UUID]
    posting_id: Mapped[UUID]
    submission_id: Mapped[UUID | None] = mapped_column(default=None)
    mode: Mapped[str] = mapped_column(Text)  # model | search
    basis: Mapped[dict] = mapped_column(JSONB, default=dict)
    notes: Mapped[list] = mapped_column(JSONB, default=list)
    model_call_id: Mapped[UUID | None] = mapped_column(default=None)
    generated_by: Mapped[UUID]
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class InterviewPrepQuestion(Base):
    """예상 질문 하나와 그 출처. 답변 메모(``answer``)는 사람이 쓴다 — 앱이 만들지 않는다."""

    __tablename__ = "interview_prep_questions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    prep_id: Mapped[UUID]
    position: Mapped[int] = mapped_column(Integer)
    # posting | submission | company | reference | common | user
    source_kind: Mapped[str] = mapped_column(Text)
    source_key: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text)  # rule | model | user (질문 문구를 누가 썼나)
    question: Mapped[str] = mapped_column(Text)
    source: Mapped[dict] = mapped_column(JSONB)
    submission_id: Mapped[UUID | None] = mapped_column(default=None)
    company_fact_id: Mapped[UUID | None] = mapped_column(default=None)
    reference_note_id: Mapped[UUID | None] = mapped_column(default=None)
    common_kind: Mapped[str | None] = mapped_column(Text, default=None)
    suggestions: Mapped[list] = mapped_column(JSONB, default=list)
    answer: Mapped[str] = mapped_column(Text, default="")
    answer_asset_id: Mapped[UUID | None] = mapped_column(default=None)
    answer_updated_by: Mapped[UUID | None] = mapped_column(default=None)
    answer_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------- c0049: 포트폴리오 (O18)


class PortfolioItem(Base):
    """포트폴리오 항목 하나. 경험 하나에 붙는다. 본문은 ``portfolio_item_revisions``, 설정(제목·공개 범위·태그·링크)은 여기."""

    __tablename__ = "portfolio_items"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    experience_id: Mapped[UUID]
    title: Mapped[str] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(
        Text, default="public"
    )  # public | application | private
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    links: Mapped[list] = mapped_column(JSONB, default=list)
    current_revision: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PortfolioItemRevision(Base):
    """항목 본문 (``domain.portfolio``: 문제 · 내 역할 · 한 일 · 결과). 추가·조회만."""

    __tablename__ = "portfolio_item_revisions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    item_id: Mapped[UUID]
    revision_no: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text)  # user | agent
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    idempotency_key: Mapped[str | None] = mapped_column(Text, default=None)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PortfolioLineClaim(Base):
    """항목 revision의 줄 ↔ 확인한 사실. 추가·조회만."""

    __tablename__ = "portfolio_line_claims"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    revision_id: Mapped[UUID]
    line_id: Mapped[str] = mapped_column(Text)
    claim_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PortfolioImage(Base):
    """스크린샷·구성도 한 장. 브라우저에서 줄여 다시 인코딩하고 서버가 메타데이터를 한 번 더 지운 바이트."""

    __tablename__ = "portfolio_images"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    item_id: Mapped[UUID]
    position: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(Text, default="screenshot")  # screenshot | diagram
    caption: Mapped[str | None] = mapped_column(Text, default=None)
    visibility: Mapped[str] = mapped_column(
        Text, default="private"
    )  # public | application | private
    mime: Mapped[str] = mapped_column(Text)
    width_px: Mapped[int] = mapped_column(Integer)
    height_px: Mapped[int] = mapped_column(Integer)
    bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(Text)
    blob_key: Mapped[str] = mapped_column(Text)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PortfolioBundle(Base):
    """포트폴리오 묶음: 전체 → 직무별 → 공고별. 위 묶음의 항목 목록을 복사해 시작하고 출처를 남긴다."""

    __tablename__ = "portfolio_bundles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(Text)  # all | role | posting
    title: Mapped[str] = mapped_column(Text)
    target_role: Mapped[str | None] = mapped_column(Text, default=None)
    intro: Mapped[str | None] = mapped_column(Text, default=None)
    parent_id: Mapped[UUID | None] = mapped_column(default=None)
    source_revision: Mapped[int | None] = mapped_column(Integer, default=None)
    application_id: Mapped[UUID | None] = mapped_column(default=None)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PortfolioBundleItem(Base):
    __tablename__ = "portfolio_bundle_items"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    bundle_id: Mapped[UUID]
    item_id: Mapped[UUID]
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GitHubToken(Base):
    """비공개 저장소를 읽는 사용자 토큰의 암호문 (SecretBox). 내보내기에서 뺀다 (export.EXCLUDED_TABLES)."""

    __tablename__ = "github_tokens"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    last4: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:  # 암호문도 로그에 남기지 않는다
        return f"GitHubToken(workspace_id={self.workspace_id})"

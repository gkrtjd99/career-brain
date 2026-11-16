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
    method: Mapped[str] = mapped_column(Text)
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
    kind: Mapped[str] = mapped_column(Text)  # export | restore | merge (c0018)
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

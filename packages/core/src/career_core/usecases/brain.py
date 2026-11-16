"""경험·주장·근거 관리.

확인(confirm)은 `approve` 권한을 가진 사람만 한다. 개인용에서는 공간 주인 본인이다.
편집자(editor)가 쓴 주장은 주인이 확인하기 전까지 후보(`extracted`)로 남는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from career_core.db import (
    Claim,
    ClaimEvidence,
    Database,
    DocumentBlock,
    Experience,
    InterviewAnswer,
    InterviewQuestion,
    ParseRun,
    Source,
    SourceSpan,
    SourceVersion,
)
from career_core.db.base import utcnow
from career_core.domain.brain import (
    CONFIRMED_STATUSES,
    ClaimKind,
    ClaimOrigin,
    ClaimRuleError,
    ClaimStatus,
    Contribution,
    EvidenceKind,
    confirmed_status,
)
from career_core.ports import ActorContext
from career_core.usecases.index_queue import request_index

APPROVE_SCOPE = "approve"


class NotFound(LookupError):
    pass


def can_confirm(actor: ActorContext) -> bool:
    return actor.has_scope(APPROVE_SCOPE)


def _kind(value: str) -> str:
    try:
        return ClaimKind(value).value
    except ValueError as exc:
        raise ClaimRuleError("invalid_kind") from exc


def _contribution(value: str) -> str:
    try:
        return Contribution(value).value
    except ValueError as exc:
        raise ClaimRuleError("invalid_contribution") from exc


def _statement(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ClaimRuleError("statement_required")
    if len(value) > 4000:
        raise ClaimRuleError("statement_too_long")
    return value


# ---------------------------------------------------------------- 원문 위치


async def ensure_span(
    s: AsyncSession, actor: ActorContext, block: DocumentBlock, start: int, end: int
) -> SourceSpan:
    """블록 안의 [start, end) 범위를 근거 위치로 만든다. 같은 범위가 있으면 재사용."""
    if not (0 <= start < end <= len(block.text)):
        raise ClaimRuleError("span_out_of_range")
    existing = await s.scalar(
        select(SourceSpan).where(
            SourceSpan.document_block_id == block.id,
            SourceSpan.start_char == start,
            SourceSpan.end_char == end,
        )
    )
    if existing:
        return existing
    span = SourceSpan(
        id=uuid4(),
        workspace_id=actor.workspace_id,
        document_block_id=block.id,
        start_char=start,
        end_char=end,
        quote=block.text[start:end],
        created_by=actor.subject_id,
    )
    s.add(span)
    await s.flush()
    return span


async def create_span(
    db: Database, actor: ActorContext, block_id: UUID, start: int, end: int
) -> UUID:
    async with db.workspace_session(actor.workspace_id) as s:
        block = await s.get(DocumentBlock, block_id)
        if block is None:
            raise NotFound("block")
        return (await ensure_span(s, actor, block, start, end)).id


@dataclass(frozen=True, slots=True)
class SpanView:
    id: UUID
    quote: str
    start_char: int
    end_char: int
    block_id: UUID
    block_text: str
    page: int | None
    ordinal: int
    parse_run_id: UUID
    source_id: UUID
    source_title: str
    original_filename: str


async def get_span(db: Database, actor: ActorContext, span_id: UUID) -> SpanView:
    async with db.workspace_session(actor.workspace_id) as s:
        row = (
            await s.execute(
                select(SourceSpan, DocumentBlock, ParseRun.id, Source, SourceVersion)
                .join(DocumentBlock, DocumentBlock.id == SourceSpan.document_block_id)
                .join(ParseRun, ParseRun.id == DocumentBlock.parse_run_id)
                .join(SourceVersion, SourceVersion.id == ParseRun.source_version_id)
                .join(Source, Source.id == SourceVersion.source_id)
                .where(SourceSpan.id == span_id)
            )
        ).first()
    if row is None:
        raise NotFound("span")
    span, block, run_id, source, version = row
    return SpanView(
        id=span.id,
        quote=span.quote,
        start_char=span.start_char,
        end_char=span.end_char,
        block_id=block.id,
        block_text=block.text,
        page=block.page,
        ordinal=block.ordinal,
        parse_run_id=run_id,
        source_id=source.id,
        source_title=source.title,
        original_filename=version.original_filename,
    )


# ---------------------------------------------------------------- 경험


async def create_experience(
    db: Database,
    actor: ActorContext,
    *,
    title: str,
    organization: str | None = None,
    role: str | None = None,
    period_text: str | None = None,
) -> UUID:
    title = (title or "").strip()
    if not title:
        raise ClaimRuleError("title_required")
    async with db.workspace_session(actor.workspace_id) as s:
        exp = Experience(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            title=title,
            organization=organization,
            role=role,
            period_text=period_text,
            origin=ClaimOrigin.USER.value,
            created_by=actor.subject_id,
        )
        s.add(exp)
    return exp.id


@dataclass(frozen=True, slots=True)
class ExperienceSummary:
    id: UUID
    title: str
    organization: str | None
    role: str | None
    period_text: str | None
    origin: str
    claim_counts: dict[str, int]

    @property
    def needs_review(self) -> int:
        return self.claim_counts.get(ClaimStatus.EXTRACTED, 0)


async def list_experiences(db: Database, actor: ActorContext) -> list[ExperienceSummary]:
    async with db.workspace_session(actor.workspace_id) as s:
        exps = (
            (
                await s.execute(
                    select(Experience)
                    .where(Experience.status == "active")
                    .order_by(Experience.created_at)
                )
            )
            .scalars()
            .all()
        )
        counts = (
            await s.execute(
                select(Claim.experience_id, Claim.status, func.count()).group_by(
                    Claim.experience_id, Claim.status
                )
            )
        ).all()
    by_exp: dict[UUID, dict[str, int]] = {}
    for exp_id, status, n in counts:
        by_exp.setdefault(exp_id, {})[status] = n
    return [
        ExperienceSummary(
            e.id, e.title, e.organization, e.role, e.period_text, e.origin, by_exp.get(e.id, {})
        )
        for e in exps
    ]


@dataclass(frozen=True, slots=True)
class EvidenceView:
    id: UUID
    kind: str
    span_id: UUID | None = None
    quote: str | None = None
    page: int | None = None
    source_title: str | None = None
    answer_id: UUID | None = None
    question: str | None = None
    answer: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimView:
    id: UUID
    kind: str
    statement: str
    contribution: str
    status: str
    origin: str
    confirmed_at: datetime | None
    evidence: list[EvidenceView] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ExperienceView:
    id: UUID
    title: str
    organization: str | None
    role: str | None
    period_text: str | None
    origin: str
    status: str
    claims: list[ClaimView]


async def get_experience(db: Database, actor: ActorContext, experience_id: UUID) -> ExperienceView:
    async with db.workspace_session(actor.workspace_id) as s:
        exp = await s.get(Experience, experience_id)
        if exp is None:
            raise NotFound("experience")
        claims = (
            (
                await s.execute(
                    select(Claim)
                    .where(Claim.experience_id == experience_id)
                    .order_by(Claim.created_at)
                )
            )
            .scalars()
            .all()
        )
        claim_ids = [c.id for c in claims]
        span_rows = (
            await s.execute(
                select(ClaimEvidence, SourceSpan, DocumentBlock.page, Source.title)
                .join(SourceSpan, SourceSpan.id == ClaimEvidence.source_span_id)
                .join(DocumentBlock, DocumentBlock.id == SourceSpan.document_block_id)
                .join(ParseRun, ParseRun.id == DocumentBlock.parse_run_id)
                .join(SourceVersion, SourceVersion.id == ParseRun.source_version_id)
                .join(Source, Source.id == SourceVersion.source_id)
                .where(ClaimEvidence.claim_id.in_(claim_ids))
            )
        ).all()
        answer_rows = (
            await s.execute(
                select(ClaimEvidence, InterviewAnswer, InterviewQuestion.prompt)
                .join(InterviewAnswer, InterviewAnswer.id == ClaimEvidence.interview_answer_id)
                .join(InterviewQuestion, InterviewQuestion.id == InterviewAnswer.question_id)
                .where(ClaimEvidence.claim_id.in_(claim_ids))
            )
        ).all()
    evidence: dict[UUID, list[EvidenceView]] = {}
    for ev, span, page, title in span_rows:
        evidence.setdefault(ev.claim_id, []).append(
            EvidenceView(
                ev.id, ev.kind, span_id=span.id, quote=span.quote, page=page, source_title=title
            )
        )
    for ev, ans, prompt in answer_rows:
        evidence.setdefault(ev.claim_id, []).append(
            EvidenceView(ev.id, ev.kind, answer_id=ans.id, question=prompt, answer=ans.answer)
        )
    return ExperienceView(
        id=exp.id,
        title=exp.title,
        organization=exp.organization,
        role=exp.role,
        period_text=exp.period_text,
        origin=exp.origin,
        status=exp.status,
        claims=[
            ClaimView(
                c.id,
                c.kind,
                c.statement,
                c.contribution,
                c.status,
                c.origin,
                c.confirmed_at,
                evidence.get(c.id, []),
            )
            for c in claims
        ],
    )


# ---------------------------------------------------------------- 주장


async def _has_span(s: AsyncSession, claim_id: UUID) -> bool:
    n = await s.scalar(
        select(func.count()).where(
            ClaimEvidence.claim_id == claim_id,
            ClaimEvidence.kind == EvidenceKind.SOURCE_SPAN,
        )
    )
    return bool(n)


def _mark_confirmed(claim: Claim, actor: ActorContext, *, has_span: bool) -> None:
    claim.status = confirmed_status(has_source_span=has_span, contribution=claim.contribution)
    claim.confirmed_by = actor.subject_id
    claim.confirmed_at = utcnow()


async def add_claim(
    db: Database,
    actor: ActorContext,
    experience_id: UUID,
    *,
    statement: str,
    kind: str,
    contribution: str = Contribution.UNKNOWN,
    span_ids: list[UUID] | None = None,
    origin: ClaimOrigin = ClaimOrigin.USER,
) -> UUID:
    """사람이 쓴 주장. 확인 권한이 있고 팀/본인 구분이 있으면 바로 확인된 상태가 된다."""
    async with db.workspace_session(actor.workspace_id) as s:
        if await s.get(Experience, experience_id) is None:
            raise NotFound("experience")
        claim = Claim(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            experience_id=experience_id,
            kind=_kind(kind),
            statement=_statement(statement),
            contribution=_contribution(contribution),
            status=ClaimStatus.EXTRACTED.value,
            origin=origin.value,
            created_by=actor.subject_id,
        )
        s.add(claim)
        await s.flush()
        for span_id in span_ids or []:
            await _attach_span(s, actor, claim, span_id)
        if can_confirm(actor) and claim.contribution != Contribution.UNKNOWN:
            _mark_confirmed(claim, actor, has_span=bool(span_ids))
        await request_index(s, actor.workspace_id)
    return claim.id


async def _attach_span(s: AsyncSession, actor: ActorContext, claim: Claim, span_id: UUID) -> None:
    if await s.get(SourceSpan, span_id) is None:
        raise NotFound("span")
    exists = await s.scalar(
        select(ClaimEvidence.id).where(
            ClaimEvidence.claim_id == claim.id, ClaimEvidence.source_span_id == span_id
        )
    )
    if exists:
        return
    s.add(
        ClaimEvidence(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            claim_id=claim.id,
            kind=EvidenceKind.SOURCE_SPAN.value,
            source_span_id=span_id,
            created_by=actor.subject_id,
        )
    )
    await s.flush()


async def attach_span(db: Database, actor: ActorContext, claim_id: UUID, span_id: UUID) -> str:
    async with db.workspace_session(actor.workspace_id) as s:
        claim = await s.get(Claim, claim_id)
        if claim is None:
            raise NotFound("claim")
        await _attach_span(s, actor, claim, span_id)
        # 본인 진술로 확인된 주장에 원문 근거가 붙으면 자료로 확인된 상태로 올라간다.
        if claim.status == ClaimStatus.SELF_CONFIRMED:
            claim.status = ClaimStatus.EVIDENCE_SUPPORTED.value
        return claim.status


async def confirm_claim(
    db: Database,
    actor: ActorContext,
    claim_id: UUID,
    *,
    contribution: str | None = None,
    statement: str | None = None,
) -> str:
    if not can_confirm(actor):
        raise ClaimRuleError("confirm_requires_approve")
    async with db.workspace_session(actor.workspace_id) as s:
        claim = await s.get(Claim, claim_id)
        if claim is None:
            raise NotFound("claim")
        if claim.status == ClaimStatus.RETIRED:
            raise ClaimRuleError("claim_retired")
        if contribution is not None:
            claim.contribution = _contribution(contribution)
        if statement is not None:
            claim.statement = _statement(statement)
            await request_index(s, actor.workspace_id)
        _mark_confirmed(claim, actor, has_span=await _has_span(s, claim.id))
        return claim.status


async def edit_claim(
    db: Database,
    actor: ActorContext,
    claim_id: UUID,
    *,
    statement: str | None = None,
    kind: str | None = None,
    contribution: str | None = None,
) -> str:
    async with db.workspace_session(actor.workspace_id) as s:
        claim = await s.get(Claim, claim_id)
        if claim is None:
            raise NotFound("claim")
        if claim.status == ClaimStatus.RETIRED:
            raise ClaimRuleError("claim_retired")
        if statement is not None:
            claim.statement = _statement(statement)
            await request_index(s, actor.workspace_id)
        if kind is not None:
            claim.kind = _kind(kind)
        if contribution is not None:
            claim.contribution = _contribution(contribution)
        if claim.status in CONFIRMED_STATUSES:
            if can_confirm(actor):
                _mark_confirmed(claim, actor, has_span=await _has_span(s, claim.id))
            else:
                # 확인한 사람이 아닌 사람이 고쳤으면 다시 확인받아야 한다.
                claim.status = ClaimStatus.EXTRACTED.value
                claim.confirmed_by = None
                claim.confirmed_at = None
        return claim.status


async def set_claim_status(
    db: Database, actor: ActorContext, claim_id: UUID, status: ClaimStatus
) -> str:
    if status not in (ClaimStatus.DISPUTED, ClaimStatus.RETIRED):
        raise ClaimRuleError("use_confirm")
    async with db.workspace_session(actor.workspace_id) as s:
        claim = await s.get(Claim, claim_id)
        if claim is None:
            raise NotFound("claim")
        claim.status = status.value
        return claim.status

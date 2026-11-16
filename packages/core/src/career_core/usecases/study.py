"""학습·시험·오답·복습 (O09).

연결 사슬: 저장 공고의 요건 한 줄(`study_requirements`) → 학습 항목·시험 준비(`study_items`,
`study_assessments`) → 학습 기록·응시·오답(`study_sessions`, `study_attempts`, `study_mistakes`)
→ (사용자가 직접 올릴 때만) 확인 대기 주장. 이 모듈의 어떤 함수도 주장을 확정하지 않는다.

요건이 경험으로 채워졌는지는 사용자가 연결한 주장(`study_requirement_claims`)으로만 본다.
공고 문장과 주장 문장을 자동으로 맞춰 "충족"으로 판정하지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from career_core.db import (
    Claim,
    Database,
    Embedding,
    Experience,
    JobPosting,
    StudyAssessment,
    StudyAttempt,
    StudyClaimCandidate,
    StudyItem,
    StudyItemMerge,
    StudyMistake,
    StudyRequirement,
    StudyRequirementClaim,
    StudyReview,
    StudySession,
)
from career_core.db.base import utcnow
from career_core.domain.brain import (
    CONFIRMED_STATUSES,
    ClaimKind,
    ClaimOrigin,
    ClaimStatus,
    Contribution,
)
from career_core.domain.postings import PostingStatus
from career_core.domain.study import (
    DEFAULT_TIMEZONE,
    TEST_KINDS,
    AssessmentKind,
    AssessmentStatus,
    AttemptResult,
    Coverage,
    ItemKind,
    ItemStatus,
    MistakeReason,
    MistakeStatus,
    RequirementField,
    ReviewGrade,
    ReviewState,
    StudyRuleError,
    classify_step,
    d_day,
    day_end,
    day_start,
    due_after,
    first_due,
    local_date,
    next_review,
    normalize_line,
    requirement_query,
    requirement_terms,
    split_process,
    zone,
)
from career_core.llm.gateway import ModelGateway
from career_core.ports import ActorContext
from career_core.usecases import judges
from career_core.usecases import workspace_settings as ws_settings
from career_core.usecases.brain import NotFound
from career_core.usecases.data_classes import claim_data_classes, restricted_claims
from career_core.usecases.index_queue import request_index
from career_core.usecases.judges import JudgeRun, Verdict
from career_core.usecases.search import STUDY_KINDS
from career_core.usecases.search import search as search_records
from career_core.usecases.workspace_settings import (
    workspace_aliases,
    workspace_timezone_name,
    workspace_zone,
)

MAX_TITLE = 300
MAX_TEXT = 4000
MAX_RESOURCES = 20
DUE_LIMIT = 100


# ---------------------------------------------------------------- 입력 검사


def _enum(enum: type, value: Any, code: str) -> str:
    try:
        return enum(value).value
    except ValueError as exc:
        raise StudyRuleError(code) from exc


def _title(value: str | None) -> str:
    value = " ".join((value or "").split())
    if not value:
        raise StudyRuleError("title_required")
    return value[:MAX_TITLE]


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if len(value) > MAX_TEXT:
        raise StudyRuleError("text_too_long")
    return value or None


def _resources(value: list[dict] | None) -> list[dict]:
    out = []
    for r in value or []:
        if not isinstance(r, dict) or not str(r.get("title") or "").strip():
            raise StudyRuleError("invalid_resource")
        url = r.get("url")
        if url is not None and (not isinstance(url, str) or len(url) > 2000):
            raise StudyRuleError("invalid_resource")
        out.append({"title": " ".join(str(r["title"]).split())[:MAX_TITLE], "url": url or None})
    if len(out) > MAX_RESOURCES:
        raise StudyRuleError("too_many_resources")
    return out


def _minutes(value: int | None, *, required: bool) -> int | None:
    if value is None:
        if required:
            raise StudyRuleError("minutes_required")
        return None
    if not isinstance(value, int) or not 0 < value <= 1440:
        raise StudyRuleError("invalid_minutes")
    return value


async def _get(s: AsyncSession, model: type, ident: UUID | None, what: str):
    row = await s.get(model, ident) if ident is not None else None
    if row is None:
        raise NotFound(what)
    return row


async def _live_item(s: AsyncSession, ident: UUID | None) -> StudyItem:
    """기록을 더할 수 있는 학습 항목. 다른 항목으로 합쳐진 항목이면 409 `item_merged`."""
    item = await _get(s, StudyItem, ident, "study_item")
    if item.status == ItemStatus.MERGED:
        raise StudyRuleError("item_merged")
    return item


async def _merged_sources(s: AsyncSession, item_ids: Iterable[UUID]) -> dict[UUID, list[UUID]]:
    """합친 항목 → 그 항목으로 합쳐진 항목들 (병합은 항상 한 단계: 합쳐진 항목을 다시 가리키지 않는다)."""
    ids = list(item_ids)
    if not ids:
        return {}
    out: dict[UUID, list[UUID]] = {}
    for src, dst in (
        await s.execute(
            select(StudyItemMerge.source_item_id, StudyItemMerge.target_item_id)
            .where(StudyItemMerge.target_item_id.in_(ids))
            .order_by(StudyItemMerge.created_at)
        )
    ).all():
        out.setdefault(dst, []).append(src)
    return out


async def _merge_targets(s: AsyncSession, item_ids: Iterable[UUID]) -> dict[UUID, UUID]:
    """합쳐진 항목 → 합친 항목."""
    ids = list(item_ids)
    if not ids:
        return {}
    rows = await s.execute(
        select(StudyItemMerge.source_item_id, StudyItemMerge.target_item_id).where(
            StudyItemMerge.source_item_id.in_(ids)
        )
    )
    return dict(rows.all())


# ---------------------------------------------------------------- 공고 요건


async def _saved_posting(s: AsyncSession, posting_id: UUID) -> JobPosting:
    posting = await _get(s, JobPosting, posting_id, "posting")
    if posting.status == PostingStatus.DRAFT:
        # 확정하지 않은 추출값은 학습 계획의 근거로 쓰지 않는다.
        raise StudyRuleError("posting_not_saved")
    return posting


def _posting_list(posting: JobPosting, name: str) -> list[str]:
    value = (posting.fields or {}).get(name)
    return [v for v in value if isinstance(v, str) and v.strip()] if isinstance(value, list) else []


async def _ensure_requirement(
    s: AsyncSession, actor: ActorContext, posting: JobPosting, name: str, text: str
) -> StudyRequirement:
    name = _enum(RequirementField, name, "invalid_field")
    text = " ".join((text or "").split())
    if text not in _posting_list(posting, name):
        raise StudyRuleError("requirement_not_in_posting")
    req = await s.scalar(
        select(StudyRequirement).where(
            StudyRequirement.posting_id == posting.id,
            StudyRequirement.field == name,
            StudyRequirement.text == text,
        )
    )
    if req is None:
        req = StudyRequirement(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            posting_id=posting.id,
            field=name,
            text=text,
            created_by=actor.subject_id,
        )
        s.add(req)
        await s.flush()
    return req


# ---------------------------------------------------------------- 학습 항목


def _new_item(
    actor: ActorContext,
    *,
    title: str,
    kind: str,
    requirement_id: UUID | None = None,
    assessment_id: UUID | None = None,
    notes: str | None = None,
) -> StudyItem:
    return StudyItem(
        id=uuid4(),
        workspace_id=actor.workspace_id,
        title=_title(title),
        kind=_enum(ItemKind, kind, "invalid_kind"),
        status=ItemStatus.PLANNED.value,
        requirement_id=requirement_id,
        assessment_id=assessment_id,
        notes=_text(notes),
        created_by=actor.subject_id,
    )


async def create_item(
    db: Database,
    actor: ActorContext,
    *,
    title: str | None = None,
    kind: str = ItemKind.SKILL,
    notes: str | None = None,
    posting_id: UUID | None = None,
    field: str | None = None,
    text: str | None = None,
    assessment_id: UUID | None = None,
) -> UUID:
    """직접 만들거나, 저장 공고의 요건 한 줄에서 만든다 (그 줄을 그대로 연결해 둔다)."""
    async with db.workspace_session(actor.workspace_id) as s:
        req = None
        if posting_id is not None:
            posting = await _saved_posting(s, posting_id)
            req = await _ensure_requirement(
                s, actor, posting, field or RequirementField.REQUIREMENTS, text or ""
            )
        elif field is not None or text is not None:
            raise StudyRuleError("posting_required")
        if assessment_id is not None:
            await _get(s, StudyAssessment, assessment_id, "assessment")
        item = _new_item(
            actor,
            title=title or (req.text if req else ""),
            kind=kind,
            requirement_id=req.id if req else None,
            assessment_id=assessment_id,
            notes=notes,
        )
        s.add(item)
    return item.id


async def update_item(
    db: Database,
    actor: ActorContext,
    item_id: UUID,
    *,
    title: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    notes: str | None = None,
    now: datetime | None = None,
) -> str:
    """학습 상태를 바꾼다. 완료해도 주장·경험은 생기지 않는다 (학습함 ≠ 입증함)."""
    async with db.workspace_session(actor.workspace_id) as s:
        item = await _live_item(s, item_id)
        if title is not None:
            item.title = _title(title)
        if kind is not None:
            item.kind = _enum(ItemKind, kind, "invalid_kind")
        if notes is not None:
            item.notes = _text(notes)
        if status is not None:
            item.status = _enum(ItemStatus, status, "invalid_status")
            if item.status == ItemStatus.MERGED:
                raise StudyRuleError("invalid_status")  # 병합은 merge_items로만
            if item.status == ItemStatus.DONE:
                item.completed_at = item.completed_at or now or utcnow()
            else:
                item.completed_at = None
        return item.status


async def log_session(
    db: Database,
    actor: ActorContext,
    item_id: UUID,
    *,
    minutes: int,
    started_at: datetime | None = None,
    notes: str | None = None,
    resources: list[dict] | None = None,
) -> UUID:
    async with db.workspace_session(actor.workspace_id) as s:
        item = await _live_item(s, item_id)
        session = StudySession(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            study_item_id=item.id,
            started_at=started_at or utcnow(),
            minutes=_minutes(minutes, required=True),
            notes=_text(notes),
            resources=_resources(resources),
            created_by=actor.subject_id,
        )
        s.add(session)
        if item.status == ItemStatus.PLANNED:
            item.status = ItemStatus.IN_PROGRESS.value
        if session.notes:
            await request_index(s, actor.workspace_id)  # 학습 메모도 검색 대상 (학습 기록으로 표시)
    return session.id


# ---------------------------------------------------------------- 시험·응시


async def create_assessment(
    db: Database,
    actor: ActorContext,
    *,
    title: str,
    kind: str,
    scheduled_at: datetime | None = None,
    notes: str | None = None,
    posting_id: UUID | None = None,
    process_text: str | None = None,
) -> UUID:
    """시험 준비 항목. 공고에서 만들면 그 공고의 전형 절차 한 줄을 연결한다."""
    async with db.workspace_session(actor.workspace_id) as s:
        req = None
        if posting_id is not None:
            posting = await _saved_posting(s, posting_id)
            req = await _ensure_requirement(
                s, actor, posting, RequirementField.PROCESS, process_text or ""
            )
        elif process_text is not None:
            raise StudyRuleError("posting_required")
        a = StudyAssessment(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            title=_title(title),
            kind=_enum(AssessmentKind, kind, "invalid_kind"),
            status=AssessmentStatus.PLANNED.value,
            requirement_id=req.id if req else None,
            scheduled_at=scheduled_at,
            notes=_text(notes),
            created_by=actor.subject_id,
        )
        s.add(a)
    return a.id


async def update_assessment(
    db: Database,
    actor: ActorContext,
    assessment_id: UUID,
    *,
    title: str | None = None,
    status: str | None = None,
    scheduled_at: datetime | None = None,
    notes: str | None = None,
) -> str:
    async with db.workspace_session(actor.workspace_id) as s:
        a = await _get(s, StudyAssessment, assessment_id, "assessment")
        if title is not None:
            a.title = _title(title)
        if status is not None:
            a.status = _enum(AssessmentStatus, status, "invalid_status")
        if scheduled_at is not None:
            a.scheduled_at = scheduled_at
        if notes is not None:
            a.notes = _text(notes)
        return a.status


def _score(value: Decimal | float | None) -> Decimal | None:
    if value is None:
        return None
    try:
        d = Decimal(str(value)).quantize(Decimal("0.01"))
    except ArithmeticError as exc:
        raise StudyRuleError("invalid_score") from exc
    if not d.is_finite() or d < 0 or d >= Decimal("1e8"):
        raise StudyRuleError("invalid_score")
    return d


async def record_attempt(
    db: Database,
    actor: ActorContext,
    *,
    result: str,
    assessment_id: UUID | None = None,
    study_item_id: UUID | None = None,
    score: Decimal | float | None = None,
    max_score: Decimal | float | None = None,
    taken_at: datetime | None = None,
    minutes: int | None = None,
    notes: str | None = None,
) -> UUID:
    """응시·풀이 결과 기록. 학습 기록일 뿐 실무 경험으로 바꾸지 않는다."""
    if assessment_id is None and study_item_id is None:
        raise StudyRuleError("attempt_target_required")
    score_d, max_d = _score(score), _score(max_score)
    if max_d is not None and max_d == 0:
        raise StudyRuleError("invalid_score")
    if score_d is not None and max_d is not None and score_d > max_d:
        raise StudyRuleError("score_above_max")
    async with db.workspace_session(actor.workspace_id) as s:
        if assessment_id is not None:
            await _get(s, StudyAssessment, assessment_id, "assessment")
        if study_item_id is not None:
            await _live_item(s, study_item_id)
        attempt = StudyAttempt(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            assessment_id=assessment_id,
            study_item_id=study_item_id,
            taken_at=taken_at or utcnow(),
            result=_enum(AttemptResult, result, "invalid_result"),
            score=score_d,
            max_score=max_d,
            minutes=_minutes(minutes, required=False),
            notes=_text(notes),
            created_by=actor.subject_id,
        )
        s.add(attempt)
    return attempt.id


# ---------------------------------------------------------------- 오답·복습


async def add_mistake(
    db: Database,
    actor: ActorContext,
    *,
    question: str,
    reason: str = MistakeReason.UNKNOWN,
    attempt_id: UUID | None = None,
    study_item_id: UUID | None = None,
    wrong_answer: str | None = None,
    correct_answer: str | None = None,
    reason_note: str | None = None,
    now: datetime | None = None,
) -> UUID:
    """오답 하나. 다음 날 첫 복습이 잡힌다."""
    if attempt_id is None and study_item_id is None:
        raise StudyRuleError("mistake_target_required")
    question = (question or "").strip()
    if not question:
        raise StudyRuleError("question_required")
    if len(question) > MAX_TEXT:
        raise StudyRuleError("text_too_long")
    now = now or utcnow()
    async with db.workspace_session(actor.workspace_id) as s:
        if attempt_id is not None:
            attempt = await _get(s, StudyAttempt, attempt_id, "attempt")
            study_item_id = study_item_id or attempt.study_item_id
        if study_item_id is not None:
            item = await _get(s, StudyItem, study_item_id, "study_item")
            if item.status == ItemStatus.MERGED:
                target = (await _merge_targets(s, [item.id])).get(item.id)
                if target is None or attempt_id is None:
                    raise StudyRuleError("item_merged")
                study_item_id = target  # 합쳐진 항목의 응시에서 적는 오답은 합친 항목으로
        m = StudyMistake(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            attempt_id=attempt_id,
            study_item_id=study_item_id,
            question=question,
            wrong_answer=_text(wrong_answer),
            correct_answer=_text(correct_answer),
            reason=_enum(MistakeReason, reason, "invalid_reason"),
            reason_note=_text(reason_note),
            status=MistakeStatus.OPEN.value,
            due_at=first_due(now),
            created_by=actor.subject_id,
            created_at=now,
        )
        s.add(m)
        await request_index(s, actor.workspace_id)  # 오답도 검색 대상 (학습 기록으로 표시)
    return m.id


async def set_mistake_status(
    db: Database, actor: ActorContext, mistake_id: UUID, status: str
) -> str:
    async with db.workspace_session(actor.workspace_id) as s:
        m = await _get(s, StudyMistake, mistake_id, "mistake")
        m.status = _enum(MistakeStatus, status, "invalid_status")
        return m.status


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    mistake_id: UUID
    grade: str
    repetitions: int
    interval_days: int
    ease: int
    due_at: datetime


async def review_mistake(
    db: Database,
    actor: ActorContext,
    mistake_id: UUID,
    grade: str,
    *,
    now: datetime | None = None,
) -> ReviewOutcome:
    g = ReviewGrade(_enum(ReviewGrade, grade, "invalid_grade"))
    now = now or utcnow()
    async with db.workspace_session(actor.workspace_id) as s:
        m = await _get(s, StudyMistake, mistake_id, "mistake")
        if m.status != MistakeStatus.OPEN:
            raise StudyRuleError("mistake_resolved")
        before = m.interval_days
        state = next_review(ReviewState(m.repetitions, m.interval_days, m.ease), g)
        due = due_after(now, state)
        m.repetitions, m.interval_days, m.ease = state.repetitions, state.interval_days, state.ease
        m.due_at, m.last_reviewed_at = due, now
        s.add(
            StudyReview(
                id=uuid4(),
                workspace_id=actor.workspace_id,
                mistake_id=m.id,
                grade=g.value,
                reviewed_at=now,
                interval_before=before,
                interval_after=state.interval_days,
                ease_after=state.ease,
                due_at_after=due,
                created_by=actor.subject_id,
            )
        )
    return ReviewOutcome(m.id, g.value, state.repetitions, state.interval_days, state.ease, due)


@dataclass(frozen=True, slots=True)
class DueMistake:
    id: UUID
    question: str
    wrong_answer: str | None
    correct_answer: str | None
    reason: str
    reason_note: str | None
    due_at: datetime
    repetitions: int
    interval_days: int
    study_item_id: UUID | None
    study_item_title: str | None
    attempt_id: UUID | None


async def due_reviews(
    db: Database,
    actor: ActorContext,
    *,
    until: datetime | None = None,
    now: datetime | None = None,
    tz: str | None = None,
    limit: int = DUE_LIMIT,
) -> list[DueMistake]:
    """오늘 복습할 오답 (가장 오래 밀린 것부터).

    마감은 표시 시간대(`tz` → 공간 설정 → Asia/Seoul)에서 `now`가 속한 하루의 끝이다.
    `until`을 주면 그 시각까지(포함)로 바꾼다.
    """
    async with db.workspace_session(actor.workspace_id) as s:
        if until is not None:
            due = StudyMistake.due_at <= until
        else:
            due = StudyMistake.due_at < day_end(now or utcnow(), await _zone(s, tz))
        rows = (
            await s.execute(
                select(StudyMistake, StudyItem.title)
                .outerjoin(StudyItem, StudyItem.id == StudyMistake.study_item_id)
                .where(StudyMistake.status == MistakeStatus.OPEN, due)
                .order_by(StudyMistake.due_at, StudyMistake.created_at)
                .limit(max(1, min(limit, DUE_LIMIT)))
            )
        ).all()
    return [
        DueMistake(
            m.id,
            m.question,
            m.wrong_answer,
            m.correct_answer,
            m.reason,
            m.reason_note,
            m.due_at,
            m.repetitions,
            m.interval_days,
            m.study_item_id,
            title,
            m.attempt_id,
        )
        for m, title in rows
    ]


@dataclass(frozen=True, slots=True)
class MistakeListItem:
    id: UUID
    question: str
    wrong_answer: str | None
    correct_answer: str | None
    reason: str
    reason_note: str | None
    status: str
    due_at: datetime
    repetitions: int
    interval_days: int
    last_reviewed_at: datetime | None
    study_item_id: UUID | None
    study_item_title: str | None
    attempt_id: UUID | None
    created_at: datetime


async def list_mistakes(
    db: Database, actor: ActorContext, *, status: str | None = None, limit: int = DUE_LIMIT
) -> list[MistakeListItem]:
    """오답 목록 (다음 복습이 이른 순). `status`가 없으면 전부."""
    async with db.workspace_session(actor.workspace_id) as s:
        stmt = (
            select(StudyMistake, StudyItem.title)
            .outerjoin(StudyItem, StudyItem.id == StudyMistake.study_item_id)
            .order_by(StudyMistake.due_at, StudyMistake.created_at)
            .limit(max(1, min(limit, DUE_LIMIT)))
        )
        if status is not None:
            stmt = stmt.where(StudyMistake.status == _enum(MistakeStatus, status, "invalid_status"))
        rows = (await s.execute(stmt)).all()
    return [
        MistakeListItem(
            m.id,
            m.question,
            m.wrong_answer,
            m.correct_answer,
            m.reason,
            m.reason_note,
            m.status,
            m.due_at,
            m.repetitions,
            m.interval_days,
            m.last_reviewed_at,
            m.study_item_id,
            title,
            m.attempt_id,
            m.created_at,
        )
        for m, title in rows
    ]


# ---------------------------------------------------------------- 요건 ↔ 주장, 경험 후보


async def link_claim(
    db: Database, actor: ActorContext, posting_id: UUID, *, field: str, text: str, claim_id: UUID
) -> UUID:
    """사용자가 "이 요건은 이 주장으로 보인다"고 직접 연결한다. 주장 상태는 바꾸지 않는다."""
    async with db.workspace_session(actor.workspace_id) as s:
        posting = await _saved_posting(s, posting_id)
        await _get(s, Claim, claim_id, "claim")
        req = await _ensure_requirement(s, actor, posting, field, text)
        await _link(s, actor, req.id, claim_id)
        return req.id


async def _link(s: AsyncSession, actor: ActorContext, requirement_id: UUID, claim_id: UUID) -> None:
    exists = await s.scalar(
        select(StudyRequirementClaim.id).where(
            StudyRequirementClaim.requirement_id == requirement_id,
            StudyRequirementClaim.claim_id == claim_id,
        )
    )
    if exists is None:
        s.add(
            StudyRequirementClaim(
                id=uuid4(),
                workspace_id=actor.workspace_id,
                requirement_id=requirement_id,
                claim_id=claim_id,
                created_by=actor.subject_id,
            )
        )
        await s.flush()


async def unlink_claim(
    db: Database, actor: ActorContext, requirement_id: UUID, claim_id: UUID
) -> None:
    async with db.workspace_session(actor.workspace_id) as s:
        link = await s.scalar(
            select(StudyRequirementClaim).where(
                StudyRequirementClaim.requirement_id == requirement_id,
                StudyRequirementClaim.claim_id == claim_id,
            )
        )
        if link is None:
            raise NotFound("link")
        await s.delete(link)


async def propose_claim(
    db: Database,
    actor: ActorContext,
    item_id: UUID,
    *,
    experience_id: UUID,
    statement: str,
    kind: str = ClaimKind.SKILL,
    attempt_id: UUID | None = None,
) -> UUID:
    """학습 결과를 경험 후보로 올린다 (사용자의 명시적 행동만).

    - 문장은 사용자가 쓴다. 자동으로 만들지 않는다.
    - 주장은 항상 `extracted`(확인 대기)로 시작한다. 확인 권한이 있어도 여기서 확정하지 않는다.
    - 학습 기록은 주장의 근거가 아니다 (근거 칸에 연결하지 않는다).
    """
    statement = (statement or "").strip()
    if not statement:
        raise StudyRuleError("statement_required")
    if len(statement) > MAX_TEXT:
        raise StudyRuleError("text_too_long")
    claim_kind = _enum(ClaimKind, kind, "invalid_claim_kind")
    async with db.workspace_session(actor.workspace_id) as s:
        item = await _live_item(s, item_id)
        await _get(s, Experience, experience_id, "experience")
        if attempt_id is not None:
            attempt = await _get(s, StudyAttempt, attempt_id, "attempt")
            merged = (await _merged_sources(s, [item.id])).get(item.id, [])
            if attempt.study_item_id not in (item.id, *merged):
                raise StudyRuleError("attempt_not_for_item")
        elif item.status != ItemStatus.DONE:
            raise StudyRuleError("no_study_result")
        claim = Claim(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            experience_id=experience_id,
            kind=claim_kind,
            statement=statement,
            contribution=Contribution.UNKNOWN.value,
            status=ClaimStatus.EXTRACTED.value,
            origin=ClaimOrigin.USER.value,
            created_by=actor.subject_id,
        )
        s.add(claim)
        await s.flush()
        s.add(
            StudyClaimCandidate(
                id=uuid4(),
                workspace_id=actor.workspace_id,
                study_item_id=item.id,
                attempt_id=attempt_id,
                claim_id=claim.id,
                created_by=actor.subject_id,
            )
        )
        if item.requirement_id is not None:
            await _link(s, actor, item.requirement_id, claim.id)
        await request_index(s, actor.workspace_id)
    return claim.id


# ---------------------------------------------------------------- 공고에서 준비 항목 만들기


@dataclass(frozen=True, slots=True)
class PlanResult:
    items_created: list[UUID]
    assessments_created: list[UUID]
    skipped: int


async def plan_from_posting(
    db: Database,
    actor: ActorContext,
    posting_id: UUID,
    *,
    selections: Iterable[tuple[str, str]] | None = None,
    include_process: bool | None = None,
) -> PlanResult:
    """확정한 공고에서 준비 항목을 만든다. 여러 번 눌러도 같은 항목을 다시 만들지 않는다.

    - `selections` 없음: 자격·우대 요건 중 확정 주장도, 진행 중인 학습 항목도 없는 줄 → 학습 항목.
      전형 절차에서 시험으로 읽히는 단계(코딩테스트·필기·과제) → 시험 준비 항목.
    - `selections` 있음: 고른 줄만. 전형 절차 줄을 고르면 그 줄의 시험 단계만.
    """
    async with db.workspace_session(actor.workspace_id) as s:
        posting = await _saved_posting(s, posting_id)
        if selections is None:
            wanted = [
                (f.value, t)
                for f in (RequirementField.REQUIREMENTS, RequirementField.PREFERRED)
                for t in _posting_list(posting, f)
            ]
            process = _posting_list(posting, RequirementField.PROCESS)
            if include_process is False:
                process = []
            auto = True
        else:
            wanted, process = [], []
            for name, text in selections:
                name = _enum(RequirementField, name, "invalid_field")
                if name == RequirementField.PROCESS:
                    process.append(text)
                else:
                    wanted.append((name, text))
            auto = False
        items: list[UUID] = []
        assessments: list[UUID] = []
        skipped = 0
        for name, text in wanted:
            req = await _ensure_requirement(s, actor, posting, name, text)
            if auto and await _requirement_handled(s, req.id):
                skipped += 1
                continue
            existing = await s.scalar(
                select(func.count())
                .select_from(StudyItem)
                .where(
                    StudyItem.requirement_id == req.id,
                    StudyItem.status != ItemStatus.DROPPED,
                )
            )
            if existing:
                skipped += 1
                continue
            item = _new_item(actor, title=req.text, kind=ItemKind.SKILL, requirement_id=req.id)
            s.add(item)
            items.append(item.id)
        for text in process:
            req = await _ensure_requirement(s, actor, posting, RequirementField.PROCESS, text)
            have = set(
                (
                    await s.execute(
                        select(StudyAssessment.title).where(
                            StudyAssessment.requirement_id == req.id
                        )
                    )
                ).scalars()
            )
            for step in split_process(req.text):
                kind = classify_step(step)
                if kind not in TEST_KINDS:
                    continue
                if _title(step) in have:
                    skipped += 1
                    continue
                a = StudyAssessment(
                    id=uuid4(),
                    workspace_id=actor.workspace_id,
                    title=_title(step),
                    kind=kind.value,
                    status=AssessmentStatus.PLANNED.value,
                    requirement_id=req.id,
                    created_by=actor.subject_id,
                )
                s.add(a)
                have.add(a.title)
                assessments.append(a.id)
        await s.flush()
    return PlanResult(items, assessments, skipped)


async def _requirement_handled(s: AsyncSession, requirement_id: UUID) -> bool:
    """확정 주장이 이미 연결된 요건은 자동 계획에서 뺀다."""
    n = await s.scalar(
        select(func.count())
        .select_from(StudyRequirementClaim)
        .join(Claim, Claim.id == StudyRequirementClaim.claim_id)
        .where(
            StudyRequirementClaim.requirement_id == requirement_id,
            Claim.status.in_([c.value for c in CONFIRMED_STATUSES]),
        )
    )
    return bool(n)


# ---------------------------------------------------------------- 조회


@dataclass(frozen=True, slots=True)
class AttemptView:
    id: UUID
    assessment_id: UUID | None
    study_item_id: UUID | None
    taken_at: datetime
    result: str
    score: Decimal | None
    max_score: Decimal | None
    minutes: int | None
    notes: str | None


def _attempt_view(a: StudyAttempt) -> AttemptView:
    return AttemptView(
        a.id,
        a.assessment_id,
        a.study_item_id,
        a.taken_at,
        a.result,
        a.score,
        a.max_score,
        a.minutes,
        a.notes,
    )


@dataclass(frozen=True, slots=True)
class RequirementRef:
    id: UUID
    posting_id: UUID
    field: str
    text: str
    posting_title: str | None


@dataclass(frozen=True, slots=True)
class ItemSummary:
    id: UUID
    title: str
    kind: str
    status: str
    requirement: RequirementRef | None
    assessment_id: UUID | None
    total_minutes: int
    open_mistakes: int
    completed_at: datetime | None
    updated_at: datetime


async def _requirement_refs(s: AsyncSession, ids: set[UUID]) -> dict[UUID, RequirementRef]:
    if not ids:
        return {}
    rows = (
        await s.execute(
            select(StudyRequirement, JobPosting.fields)
            .join(JobPosting, JobPosting.id == StudyRequirement.posting_id)
            .where(StudyRequirement.id.in_(ids))
        )
    ).all()
    return {
        r.id: RequirementRef(r.id, r.posting_id, r.field, r.text, (fields or {}).get("title"))
        for r, fields in rows
    }


async def list_items(
    db: Database, actor: ActorContext, *, status: str | None = None
) -> list[ItemSummary]:
    async with db.workspace_session(actor.workspace_id) as s:
        q = select(StudyItem).order_by(StudyItem.created_at)
        if status is not None:
            q = q.where(StudyItem.status == _enum(ItemStatus, status, "invalid_status"))
        else:
            q = q.where(
                StudyItem.status != ItemStatus.MERGED
            )  # 합쳐진 항목은 합친 항목 안에서 보인다
        items = (await s.execute(q)).scalars().all()
        ids = [i.id for i in items]
        minutes = dict(
            (
                await s.execute(
                    select(StudySession.study_item_id, func.sum(StudySession.minutes))
                    .where(StudySession.study_item_id.in_(ids))
                    .group_by(StudySession.study_item_id)
                )
            ).all()
        )
        mistakes = dict(
            (
                await s.execute(
                    select(StudyMistake.study_item_id, func.count())
                    .where(
                        StudyMistake.study_item_id.in_(ids),
                        StudyMistake.status == MistakeStatus.OPEN,
                    )
                    .group_by(StudyMistake.study_item_id)
                )
            ).all()
        )
        refs = await _requirement_refs(s, {i.requirement_id for i in items if i.requirement_id})
    return [
        ItemSummary(
            i.id,
            i.title,
            i.kind,
            i.status,
            refs.get(i.requirement_id) if i.requirement_id else None,
            i.assessment_id,
            int(minutes.get(i.id) or 0),
            int(mistakes.get(i.id) or 0),
            i.completed_at,
            i.updated_at,
        )
        for i in items
    ]


@dataclass(frozen=True, slots=True)
class SessionView:
    id: UUID
    started_at: datetime
    minutes: int
    notes: str | None
    resources: list[dict]


@dataclass(frozen=True, slots=True)
class MistakeView:
    id: UUID
    attempt_id: UUID | None
    study_item_id: UUID | None
    question: str
    wrong_answer: str | None
    correct_answer: str | None
    reason: str
    reason_note: str | None
    status: str
    repetitions: int
    interval_days: int
    due_at: datetime
    last_reviewed_at: datetime | None


def _mistake_view(m: StudyMistake) -> MistakeView:
    return MistakeView(
        m.id,
        m.attempt_id,
        m.study_item_id,
        m.question,
        m.wrong_answer,
        m.correct_answer,
        m.reason,
        m.reason_note,
        m.status,
        m.repetitions,
        m.interval_days,
        m.due_at,
        m.last_reviewed_at,
    )


@dataclass(frozen=True, slots=True)
class CandidateView:
    claim_id: UUID
    attempt_id: UUID | None
    statement: str
    status: str


@dataclass(frozen=True, slots=True)
class ItemView:
    id: UUID
    title: str
    kind: str
    status: str
    notes: str | None
    requirement: RequirementRef | None
    assessment_id: UUID | None
    completed_at: datetime | None
    total_minutes: int
    sessions: list[SessionView]
    attempts: list[AttemptView]
    mistakes: list[MistakeView]
    claim_candidates: list[CandidateView]
    merged_into_id: UUID | None = None
    # 이 항목으로 합쳐진 항목 (응시·후보 기록은 원래 항목에 남아 있고 여기서 함께 보인다)
    merged_from: list[MergedFrom] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class MergedFrom:
    id: UUID
    title: str


async def get_item(db: Database, actor: ActorContext, item_id: UUID) -> ItemView:
    async with db.workspace_session(actor.workspace_id) as s:
        item = await _get(s, StudyItem, item_id, "study_item")
        sources = (await _merged_sources(s, [item.id])).get(item.id, [])
        merged_into = (await _merge_targets(s, [item.id])).get(item.id)
        owners = [item.id, *sources]
        merged_from = (
            await s.execute(
                select(StudyItem.id, StudyItem.title)
                .where(StudyItem.id.in_(sources))
                .order_by(StudyItem.created_at)
            )
        ).all()
        sessions = (
            (
                await s.execute(
                    select(StudySession)
                    .where(StudySession.study_item_id == item_id)
                    .order_by(StudySession.started_at)
                )
            )
            .scalars()
            .all()
        )
        attempts = (
            (
                await s.execute(
                    select(StudyAttempt)
                    .where(StudyAttempt.study_item_id.in_(owners))
                    .order_by(StudyAttempt.taken_at)
                )
            )
            .scalars()
            .all()
        )
        mistakes = (
            (
                await s.execute(
                    select(StudyMistake)
                    .where(StudyMistake.study_item_id == item_id)
                    .order_by(StudyMistake.created_at)
                )
            )
            .scalars()
            .all()
        )
        candidates = (
            await s.execute(
                select(StudyClaimCandidate, Claim.statement, Claim.status)
                .join(Claim, Claim.id == StudyClaimCandidate.claim_id)
                .where(StudyClaimCandidate.study_item_id.in_(owners))
                .order_by(StudyClaimCandidate.created_at)
            )
        ).all()
        refs = await _requirement_refs(s, {item.requirement_id} if item.requirement_id else set())
    return ItemView(
        id=item.id,
        title=item.title,
        kind=item.kind,
        status=item.status,
        notes=item.notes,
        requirement=refs.get(item.requirement_id) if item.requirement_id else None,
        assessment_id=item.assessment_id,
        completed_at=item.completed_at,
        total_minutes=sum(x.minutes for x in sessions),
        sessions=[
            SessionView(x.id, x.started_at, x.minutes, x.notes, list(x.resources or []))
            for x in sessions
        ],
        attempts=[_attempt_view(a) for a in attempts],
        mistakes=[_mistake_view(m) for m in mistakes],
        claim_candidates=[
            CandidateView(c.claim_id, c.attempt_id, statement, status)
            for c, statement, status in candidates
        ],
        merged_into_id=merged_into,
        merged_from=[MergedFrom(i, t) for i, t in merged_from],
    )


@dataclass(frozen=True, slots=True)
class AssessmentView:
    id: UUID
    title: str
    kind: str
    status: str
    scheduled_at: datetime | None
    notes: str | None
    requirement: RequirementRef | None
    attempts: list[AttemptView] = field(default_factory=list)
    items: list[UUID] = field(default_factory=list)
    mistakes: list[MistakeView] = field(default_factory=list)

    @property
    def last_attempt(self) -> AttemptView | None:
        return self.attempts[-1] if self.attempts else None


async def _assessment_views(
    s: AsyncSession, rows: list[StudyAssessment], *, detail: bool
) -> list[AssessmentView]:
    ids = [a.id for a in rows]
    attempts = (
        (
            await s.execute(
                select(StudyAttempt)
                .where(StudyAttempt.assessment_id.in_(ids))
                .order_by(StudyAttempt.taken_at)
            )
        )
        .scalars()
        .all()
    )
    by_a: dict[UUID, list[AttemptView]] = {}
    for a in attempts:
        by_a.setdefault(a.assessment_id, []).append(_attempt_view(a))
    items: dict[UUID, list[UUID]] = {}
    mistakes: dict[UUID, list[MistakeView]] = {}
    if detail:
        for item_id, a_id in (
            await s.execute(
                select(StudyItem.id, StudyItem.assessment_id)
                .where(StudyItem.assessment_id.in_(ids))
                .order_by(StudyItem.created_at)
            )
        ).all():
            items.setdefault(a_id, []).append(item_id)
        attempt_owner = {a.id: a.assessment_id for a in attempts}
        for m in (
            (
                await s.execute(
                    select(StudyMistake)
                    .where(StudyMistake.attempt_id.in_(list(attempt_owner)))
                    .order_by(StudyMistake.created_at)
                )
            )
            .scalars()
            .all()
        ):
            mistakes.setdefault(attempt_owner[m.attempt_id], []).append(_mistake_view(m))
    refs = await _requirement_refs(s, {a.requirement_id for a in rows if a.requirement_id})
    return [
        AssessmentView(
            a.id,
            a.title,
            a.kind,
            a.status,
            a.scheduled_at,
            a.notes,
            refs.get(a.requirement_id) if a.requirement_id else None,
            by_a.get(a.id, []),
            items.get(a.id, []),
            mistakes.get(a.id, []),
        )
        for a in rows
    ]


async def list_assessments(db: Database, actor: ActorContext) -> list[AssessmentView]:
    async with db.workspace_session(actor.workspace_id) as s:
        rows = (
            (await s.execute(select(StudyAssessment).order_by(StudyAssessment.created_at)))
            .scalars()
            .all()
        )
        return await _assessment_views(s, list(rows), detail=False)


async def get_assessment(db: Database, actor: ActorContext, assessment_id: UUID) -> AssessmentView:
    async with db.workspace_session(actor.workspace_id) as s:
        a = await _get(s, StudyAssessment, assessment_id, "assessment")
        [view] = await _assessment_views(s, [a], detail=True)
        return view


# ---------------------------------------------------------------- 공고 요건 충족 보기


@dataclass(frozen=True, slots=True)
class LinkedClaim:
    id: UUID
    statement: str
    status: str


@dataclass(frozen=True, slots=True)
class LinkedItem:
    id: UUID
    title: str
    status: str
    last_attempt: AttemptView | None


@dataclass(frozen=True, slots=True)
class LinkedAssessment:
    id: UUID
    title: str
    kind: str
    status: str
    scheduled_at: datetime | None
    last_attempt: AttemptView | None


@dataclass(frozen=True, slots=True)
class ProcessStep:
    step: str
    kind: str | None


@dataclass(frozen=True, slots=True)
class RequirementCoverage:
    field: str
    text: str
    requirement_id: UUID | None
    current: bool  # False: 다시 확정하면서 공고에서 빠진 줄 (기록은 남긴다)
    coverage: str
    claims: list[LinkedClaim]
    items: list[LinkedItem]
    assessments: list[LinkedAssessment]
    steps: list[ProcessStep]


@dataclass(frozen=True, slots=True)
class PostingCoverage:
    posting_id: UUID
    title: str | None
    company: str | None
    status: str
    requirements: list[RequirementCoverage]
    counts: dict[str, int]
    # 판정 기준: 사용자가 연결한 주장과 학습 기록만 본다. 자동 문장 비교·합격 가능성 아님.
    basis: str = "user_links"


_LIVE_CLAIM = frozenset({ClaimStatus.EXTRACTED, *CONFIRMED_STATUSES})


def _coverage(
    claims: list[LinkedClaim], items: list[LinkedItem], assessments: list[LinkedAssessment]
) -> Coverage:
    if any(c.status in CONFIRMED_STATUSES for c in claims):
        return Coverage.EVIDENCED
    if any(c.status in _LIVE_CLAIM for c in claims):
        return Coverage.CLAIM_PENDING
    if any(i.status == ItemStatus.DONE for i in items) or any(
        a.status == AssessmentStatus.DONE for a in assessments
    ):
        return Coverage.STUDIED
    if any(i.status in (ItemStatus.PLANNED, ItemStatus.IN_PROGRESS) for i in items) or any(
        a.status == AssessmentStatus.PLANNED for a in assessments
    ):
        return Coverage.STUDYING
    return Coverage.UNCOVERED


async def posting_coverage(db: Database, actor: ActorContext, posting_id: UUID) -> PostingCoverage:
    """저장 공고의 요건마다: 연결된 주장 → 학습 항목·시험 → 최근 결과. 읽기만 한다."""
    async with db.workspace_session(actor.workspace_id) as s:
        posting = await _saved_posting(s, posting_id)
        reqs = (
            (
                await s.execute(
                    select(StudyRequirement)
                    .where(StudyRequirement.posting_id == posting_id)
                    .order_by(StudyRequirement.created_at)
                )
            )
            .scalars()
            .all()
        )
        req_ids = [r.id for r in reqs]
        claim_rows = (
            await s.execute(
                select(StudyRequirementClaim.requirement_id, Claim)
                .join(Claim, Claim.id == StudyRequirementClaim.claim_id)
                .where(StudyRequirementClaim.requirement_id.in_(req_ids))
                .order_by(StudyRequirementClaim.created_at)
            )
        ).all()
        item_rows = (
            (
                await s.execute(
                    select(StudyItem)
                    .where(StudyItem.requirement_id.in_(req_ids))
                    .order_by(StudyItem.created_at)
                )
            )
            .scalars()
            .all()
        )
        assessment_rows = (
            (
                await s.execute(
                    select(StudyAssessment)
                    .where(StudyAssessment.requirement_id.in_(req_ids))
                    .order_by(StudyAssessment.created_at)
                )
            )
            .scalars()
            .all()
        )
        # 합쳐진 항목은 합친 항목으로 보여 준다 (그 항목의 응시도 합친 항목의 최근 응시로 센다).
        merged_to = await _merge_targets(
            s, [i.id for i in item_rows if i.status == ItemStatus.MERGED]
        )
        target_rows = {
            t.id: t
            for t in (
                (
                    await s.execute(
                        select(StudyItem).where(StudyItem.id.in_(set(merged_to.values())))
                    )
                )
                .scalars()
                .all()
            )
        }
        shown_ids = {i.id for i in item_rows if i.status != ItemStatus.MERGED} | set(target_rows)
        owner_of = {i: i for i in shown_ids} | {
            src: dst for dst, srcs in (await _merged_sources(s, shown_ids)).items() for src in srcs
        }
        item_ids = list(owner_of)
        a_ids = [a.id for a in assessment_rows]
        attempts = (
            (
                await s.execute(
                    select(StudyAttempt)
                    .where(
                        StudyAttempt.study_item_id.in_(item_ids)
                        | StudyAttempt.assessment_id.in_(a_ids)
                    )
                    .order_by(StudyAttempt.taken_at)
                )
            )
            .scalars()
            .all()
        )
    last: dict[UUID, AttemptView] = {}
    for a in attempts:  # taken_at 순이므로 마지막 값이 최근
        if a.study_item_id:
            last[owner_of.get(a.study_item_id, a.study_item_id)] = _attempt_view(a)
        if a.assessment_id:
            last[a.assessment_id] = _attempt_view(a)
    claims: dict[UUID, list[LinkedClaim]] = {}
    for req_id, c in claim_rows:
        claims.setdefault(req_id, []).append(LinkedClaim(c.id, c.statement, c.status))
    items: dict[UUID, list[LinkedItem]] = {}
    for i in item_rows:
        shown = target_rows.get(merged_to.get(i.id), i) if i.status == ItemStatus.MERGED else i
        row_items = items.setdefault(i.requirement_id, [])
        if all(x.id != shown.id for x in row_items):
            row_items.append(LinkedItem(shown.id, shown.title, shown.status, last.get(shown.id)))
    assessments: dict[UUID, list[LinkedAssessment]] = {}
    for a in assessment_rows:
        assessments.setdefault(a.requirement_id, []).append(
            LinkedAssessment(a.id, a.title, a.kind, a.status, a.scheduled_at, last.get(a.id))
        )
    by_key = {(r.field, r.text): r for r in reqs}

    def row(name: str, text: str, req: StudyRequirement | None, current: bool):
        rid = req.id if req else None
        c, i, a = claims.get(rid, []), items.get(rid, []), assessments.get(rid, [])
        steps = []
        if name == RequirementField.PROCESS:
            for st in split_process(text):
                kind = classify_step(st)
                steps.append(ProcessStep(st, kind.value if kind else None))
        return RequirementCoverage(
            name, text, rid, current, _coverage(c, i, a).value, c, i, a, steps
        )

    out: list[RequirementCoverage] = []
    seen: set[tuple[str, str]] = set()
    for f in RequirementField:
        for text in _posting_list(posting, f):
            key = (f.value, " ".join(text.split()))
            if key in seen:
                continue
            seen.add(key)
            out.append(row(f.value, key[1], by_key.get(key), True))
    for key, req in by_key.items():
        if key not in seen:
            out.append(row(req.field, req.text, req, False))
    counts: dict[str, int] = {c.value: 0 for c in Coverage}
    for r in out:
        if r.current and r.field != RequirementField.PROCESS:
            counts[r.coverage] += 1
    return PostingCoverage(
        posting_id=posting.id,
        title=(posting.fields or {}).get("title"),
        company=(posting.fields or {}).get("company"),
        status=posting.status,
        requirements=out,
        counts=counts,
    )


# ---------------------------------------------------------------- 표시 시간대 설정


async def _zone(s: AsyncSession, override: str | None = None) -> ZoneInfo:
    """요청 값 → 공간 설정(c0017 공간 공용 표시 시간대) → 기본값(Asia/Seoul) 순서."""
    return await workspace_zone(s, override)


@dataclass(frozen=True, slots=True)
class SettingsView:
    timezone: str
    is_default: bool  # 저장한 설정이 없어 기본값을 쓰는 중
    today: date  # 그 시간대의 오늘
    day_ends_at: datetime  # 오늘 복습 마감 (다음 날 0시, UTC)


def _settings_view(name: str, is_default: bool, now: datetime) -> SettingsView:
    tz = zone(name)
    return SettingsView(name, is_default, local_date(now, tz), day_end(now, tz))


async def get_settings(
    db: Database, actor: ActorContext, *, now: datetime | None = None
) -> SettingsView:
    """학습 설정 = 공간 공용 표시 시간대 (예전 API 모양 그대로)."""
    async with db.workspace_session(actor.workspace_id) as s:
        name = await workspace_timezone_name(s)
    return _settings_view(name or DEFAULT_TIMEZONE, name is None, now or utcnow())


async def set_settings(
    db: Database, actor: ActorContext, *, timezone: str, now: datetime | None = None
) -> SettingsView:
    """공간 공용 표시 시간대를 바꾼다 (`workspace_settings.update_settings`와 같은 값)."""
    name = timezone.strip() if isinstance(timezone, str) else ""
    zone(name)  # 모르는 시간대는 저장하지 않는다
    await ws_settings.update_settings(db, actor, timezone=name, now=now)
    return _settings_view(name, False, now or utcnow())


# ---------------------------------------------------------------- 다가오는 시험 (오늘 화면 D-day)


@dataclass(frozen=True, slots=True)
class UpcomingAssessment:
    id: UUID
    title: str
    kind: str
    scheduled_at: datetime
    d_day: int  # 0 = 오늘, 1 = 내일 (표시 시간대 날짜 기준)
    requirement: RequirementRef | None


async def upcoming_assessments(
    db: Database,
    actor: ActorContext,
    *,
    now: datetime | None = None,
    tz: str | None = None,
    days: int = 30,
    limit: int = 20,
) -> list[UpcomingAssessment]:
    """일정이 있는 예정 시험 중 오늘부터 `days`일 안의 것 (가까운 순)."""
    now = now or utcnow()
    days = max(0, min(days, 365))
    async with db.workspace_session(actor.workspace_id) as s:
        z = await _zone(s, tz)
        start = day_start(now, z)
        end = day_end(now + timedelta(days=days), z)
        rows = (
            (
                await s.execute(
                    select(StudyAssessment)
                    .where(
                        StudyAssessment.status == AssessmentStatus.PLANNED,
                        StudyAssessment.scheduled_at >= start,
                        StudyAssessment.scheduled_at < end,
                    )
                    .order_by(StudyAssessment.scheduled_at, StudyAssessment.created_at)
                    .limit(max(1, min(limit, DUE_LIMIT)))
                )
            )
            .scalars()
            .all()
        )
        refs = await _requirement_refs(s, {a.requirement_id for a in rows if a.requirement_id})
    return [
        UpcomingAssessment(
            a.id,
            a.title,
            a.kind,
            a.scheduled_at,
            d_day(a.scheduled_at, now, z),
            refs.get(a.requirement_id) if a.requirement_id else None,
        )
        for a in rows
    ]


# ---------------------------------------------------------------- 학습 항목 병합·삭제


@dataclass(frozen=True, slots=True)
class MergeResult:
    target_id: UUID
    sessions_moved: int
    mistakes_moved: int
    # 불변 기록: 옮기지 않고 원래 항목에 남긴다 (합친 항목에서 함께 보인다)
    attempts_kept: int
    candidates_kept: int


async def merge_items(
    db: Database, actor: ActorContext, source_id: UUID, *, into: UUID
) -> MergeResult:
    """`source_id` 항목을 `into` 항목으로 합친다.

    - 학습 기록(sessions)·오답(mistakes)은 합친 항목으로 옮긴다 (고칠 수 있는 행).
    - 응시(attempts)·경험 후보(claim candidates)는 고치지 않는 기록이라 원래 항목에 그대로 두고,
      원래 항목을 `merged`로 표시해 합친 항목의 화면·충족 보기에서 함께 보이게 한다.
    - 요건·시험 연결은 합친 항목에 없을 때만 넘겨받는다. 원래 항목의 연결도 기록으로 남는다
      (충족 보기에서는 합친 항목으로 보인다).
    """
    if source_id == into:
        raise StudyRuleError("merge_same_item")
    async with db.workspace_session(actor.workspace_id) as s:
        src = await _live_item(s, source_id)
        dst = await _live_item(s, into)
        sessions = await s.execute(
            update(StudySession)
            .where(StudySession.study_item_id == src.id)
            .values(study_item_id=dst.id)
            .execution_options(synchronize_session=False)
        )
        mistakes = await s.execute(
            update(StudyMistake)
            .where(StudyMistake.study_item_id == src.id)
            .values(study_item_id=dst.id)
            .execution_options(synchronize_session=False)
        )
        # 이미 src로 합쳐져 있던 항목도 dst를 바로 가리키게 한다 (병합은 늘 한 단계).
        await s.execute(
            update(StudyItemMerge)
            .where(StudyItemMerge.target_item_id == src.id)
            .values(target_item_id=dst.id)
            .execution_options(synchronize_session=False)
        )
        owners = [src.id, *(await _merged_sources(s, [dst.id])).get(dst.id, [])]
        attempts = await s.scalar(
            select(func.count())
            .select_from(StudyAttempt)
            .where(StudyAttempt.study_item_id.in_(owners))
        )
        candidates = await s.scalar(
            select(func.count())
            .select_from(StudyClaimCandidate)
            .where(StudyClaimCandidate.study_item_id.in_(owners))
        )
        dst.requirement_id = dst.requirement_id or src.requirement_id
        dst.assessment_id = dst.assessment_id or src.assessment_id
        if src.notes:
            joined = f"{dst.notes}\n\n{src.notes}" if dst.notes else src.notes
            dst.notes = joined if len(joined) <= MAX_TEXT else dst.notes
        if dst.status == ItemStatus.PLANNED and sessions.rowcount:
            dst.status = ItemStatus.IN_PROGRESS.value
        src.status = ItemStatus.MERGED.value
        src.completed_at = None
        s.add(
            StudyItemMerge(
                id=uuid4(),
                workspace_id=actor.workspace_id,
                source_item_id=src.id,
                target_item_id=dst.id,
                created_by=actor.subject_id,
            )
        )
    return MergeResult(dst.id, sessions.rowcount, mistakes.rowcount, attempts or 0, candidates or 0)


@dataclass(frozen=True, slots=True)
class DeleteResult:
    item_id: UUID
    sessions_deleted: int
    mistakes_deleted: int
    merged_items_deleted: int


async def delete_item(db: Database, actor: ActorContext, item_id: UUID) -> DeleteResult:
    """학습 항목을 지운다 (학습 기록·오답, 이 항목으로 합쳐진 항목 포함).

    응시·경험 후보·복습 기록이 있으면 409 `item_has_records`: 고치지 않는 기록을 지우지 않는다.
    그런 항목은 `dropped`로 두거나 다른 항목에 합친다.
    """
    async with db.workspace_session(actor.workspace_id) as s:
        item = await _get(s, StudyItem, item_id, "study_item")
        sources = (await _merged_sources(s, [item.id])).get(item.id, [])
        owners = [item.id, *sources]
        mistake_ids = list(
            (
                await s.execute(
                    select(StudyMistake.id).where(StudyMistake.study_item_id.in_(owners))
                )
            ).scalars()
        )
        records = (
            await s.scalar(
                select(func.count())
                .select_from(StudyAttempt)
                .where(StudyAttempt.study_item_id.in_(owners))
            )
            or await s.scalar(
                select(func.count())
                .select_from(StudyClaimCandidate)
                .where(StudyClaimCandidate.study_item_id.in_(owners))
            )
            or await s.scalar(
                select(func.count())
                .select_from(StudyReview)
                .where(StudyReview.mistake_id.in_(mistake_ids))
            )
        )
        if records:
            raise StudyRuleError("item_has_records")
        session_ids = list(
            (
                await s.execute(
                    select(StudySession.id).where(StudySession.study_item_id.in_(owners))
                )
            ).scalars()
        )
        # 지운 학습 메모·오답의 검색 벡터도 지운다 (검색은 원본이 없으면 어차피 빠진다).
        await s.execute(
            delete(Embedding).where(
                Embedding.target_kind.in_(STUDY_KINDS),
                Embedding.target_id.in_(session_ids + mistake_ids),
            )
        )
        await s.execute(delete(StudyItem).where(StudyItem.id.in_(sources)))
        await s.execute(delete(StudyItem).where(StudyItem.id == item.id))
    return DeleteResult(item.id, len(session_ids), len(mistake_ids), len(sources))


# ---------------------------------------------------------------- 공고에서 빠진 요건 정리


@dataclass(frozen=True, slots=True)
class CleanupResult:
    removed: list[UUID]
    items_unlinked: int
    assessments_unlinked: int
    items_dropped: int
    assessments_cancelled: int
    claim_links_removed: int


async def cleanup_requirements(
    db: Database,
    actor: ActorContext,
    posting_id: UUID,
    *,
    requirement_ids: Iterable[UUID] | None = None,
    drop_items: bool = False,
) -> CleanupResult:
    """다시 확정하면서 공고에서 빠진 요건(충족 보기의 `current: false`)을 지운다.

    - 지금 공고에 있는 요건은 지우지 않는다 (409 `requirement_current`).
    - 요건 ↔ 주장 연결은 함께 지워지고, 학습 항목·시험은 남긴 채 요건 연결만 끊긴다.
      `drop_items`면 끊기는 학습 항목(계획·진행 중)은 `dropped`, 예정 시험은 `cancelled`로 바꾼다.
    """
    async with db.workspace_session(actor.workspace_id) as s:
        posting = await _saved_posting(s, posting_id)
        current = {
            (f.value, " ".join(t.split()))
            for f in RequirementField
            for t in _posting_list(posting, f)
        }
        reqs = (
            (
                await s.execute(
                    select(StudyRequirement).where(StudyRequirement.posting_id == posting.id)
                )
            )
            .scalars()
            .all()
        )
        by_id = {r.id: r for r in reqs}
        if requirement_ids is None:
            chosen = [r for r in reqs if (r.field, r.text) not in current]
        else:
            chosen = []
            for rid in dict.fromkeys(requirement_ids):
                if rid not in by_id:
                    raise NotFound("requirement")
                if (by_id[rid].field, by_id[rid].text) in current:
                    raise StudyRuleError("requirement_current")
                chosen.append(by_id[rid])
        ids = [r.id for r in chosen]
        items = (
            (await s.execute(select(StudyItem).where(StudyItem.requirement_id.in_(ids))))
            .scalars()
            .all()
        )
        assessments = (
            (
                await s.execute(
                    select(StudyAssessment).where(StudyAssessment.requirement_id.in_(ids))
                )
            )
            .scalars()
            .all()
        )
        links = await s.scalar(
            select(func.count())
            .select_from(StudyRequirementClaim)
            .where(StudyRequirementClaim.requirement_id.in_(ids))
        )
        dropped = cancelled = 0
        if drop_items:
            for i in items:
                if i.status in (ItemStatus.PLANNED, ItemStatus.IN_PROGRESS):
                    i.status, dropped = ItemStatus.DROPPED.value, dropped + 1
            for a in assessments:
                if a.status == AssessmentStatus.PLANNED:
                    a.status, cancelled = AssessmentStatus.CANCELLED.value, cancelled + 1
            await s.flush()
        # 학습 항목·시험의 요건 칸은 DB가 비우고(SET NULL), 주장 연결은 함께 지운다(CASCADE).
        await s.execute(delete(StudyRequirement).where(StudyRequirement.id.in_(ids)))
    return CleanupResult(ids, len(items), len(assessments), dropped, cancelled, links or 0)


# ---------------------------------------------------------------- 요건 → 주장 후보 (검색 제안)

SUGGEST_LINES = 40  # 한 번에 제안할 요건 줄 수 상한 (의미 검색이면 줄마다 검색어 임베딩 1회)


@dataclass(frozen=True, slots=True)
class ClaimSuggestion:
    claim_id: UUID
    statement: str
    status: str
    experience_id: UUID | None
    experience_title: str | None
    score: float
    keyword_rank: int | None
    semantic_rank: int | None
    # 판정 모델(c0017, judge=true일 때만)의 참고 의견. 연결·충족 판정이 아니다.
    judge: Verdict | None = None
    judge_note: str | None = None  # restricted_not_sent 등 판정하지 않은 이유


@dataclass(frozen=True, slots=True)
class LineSuggestions:
    field: str
    text: str
    requirement_id: UUID | None
    query: str
    candidates: list[ClaimSuggestion]


@dataclass(frozen=True, slots=True)
class SuggestionsResult:
    posting_id: UUID
    lines: list[LineSuggestions]
    mode: str  # hybrid | keyword (마지막 검색 기준)
    semantic_note: str | None
    # 검색 제안일 뿐: 사용자가 하나씩 연결해야 반영되고, 충족 판정에 쓰지 않는다.
    basis: str = "search_suggestion"
    # judge=true로 물었을 때 판정 모델 상태 (라우트가 없으면 unavailable/no_route)
    judge: JudgeRun | None = None


async def suggest_claims(
    db: Database,
    actor: ActorContext,
    posting_id: UUID,
    *,
    gateway: ModelGateway | None = None,
    field: str | None = None,
    text: str | None = None,
    limit: int = 5,
    judge: bool = False,
    call_model: judges.ModelCaller | None = None,
) -> SuggestionsResult:
    """저장 공고의 요건마다 연결할 만한 주장 후보를 검색으로 찾는다 (키워드 + 설정 시 의미 검색).

    아무것도 연결하지 않고 충족 여부도 바꾸지 않는다. 연결은 `link_claim`(사용자 확인)으로만.
    이미 연결된 주장과 이의 제기(disputed)·폐기된 주장은 빼고, 확인 대기 주장은 상태를 붙여 보여 준다.

    `judge=True`: 라우트 `judge_claim`이 있으면 후보마다 "이 주장이 요건을 보여 주는가"와 이유를 붙인다
    (참고용, 요건 줄마다 호출 1회). restricted 근거의 주장은 보내지 않는다. 라우트가 없으면 검색 결과만.
    """
    limit = max(1, min(limit, 20))
    async with db.workspace_session(actor.workspace_id) as s:
        posting = await _saved_posting(s, posting_id)
        if text is not None:
            name = _enum(RequirementField, field or RequirementField.REQUIREMENTS, "invalid_field")
            line = " ".join(text.split())
            if line not in _posting_list(posting, name):
                raise StudyRuleError("requirement_not_in_posting")
            wanted = [(name, line)]
        else:
            wanted = [
                (f.value, " ".join(t.split()))
                for f in (RequirementField.REQUIREMENTS, RequirementField.PREFERRED)
                for t in _posting_list(posting, f)
            ]
            wanted = list(dict.fromkeys(wanted))[:SUGGEST_LINES]
        reqs = {
            (r.field, r.text): r.id
            for r in (
                await s.execute(
                    select(StudyRequirement).where(StudyRequirement.posting_id == posting.id)
                )
            ).scalars()
        }
        linked: dict[UUID, set[UUID]] = {}
        for rid, cid in (
            await s.execute(
                select(StudyRequirementClaim.requirement_id, StudyRequirementClaim.claim_id).where(
                    StudyRequirementClaim.requirement_id.in_(list(reqs.values()))
                )
            )
        ).all():
            linked.setdefault(rid, set()).add(cid)

    lines: list[LineSuggestions] = []
    mode, note = "keyword", None
    for name, line in wanted:
        rid = reqs.get((name, line))
        skip = linked.get(rid, set()) if rid else set()
        query = requirement_query(line)
        result = await search_records(
            db, actor, query, gateway=gateway, kinds=("claim",), limit=limit + len(skip)
        )
        mode, note = result.mode, result.semantic_note
        found = [
            ClaimSuggestion(
                h.id,
                h.snippet,
                h.claim_status or "",
                h.experience_id,
                h.experience_title,
                h.score,
                h.keyword_rank,
                h.semantic_rank,
            )
            for h in result.hits
            if h.id not in skip and h.claim_status != ClaimStatus.DISPUTED
        ][:limit]
        lines.append(LineSuggestions(name, line, rid, query, found))
    run = None
    if judge:
        lines, run = await _judge_lines(db, gateway, actor, lines, call_model)
    return SuggestionsResult(posting_id, lines, mode, note, judge=run)


async def _judge_lines(
    db: Database,
    gateway: ModelGateway | None,
    actor: ActorContext,
    lines: list[LineSuggestions],
    call_model: judges.ModelCaller | None,
) -> tuple[list[LineSuggestions], judges.JudgeRun]:
    """후보 주장에 판정 모델 의견을 붙인다. 아무것도 저장·연결하지 않는다."""
    if call_model is None:
        if (why := judges.unavailable(gateway, actor, judges.JUDGE_CLAIM_PURPOSE)) is not None:
            return lines, why
        assert gateway is not None
        call_model = judges.gateway_caller(gateway, actor)
    ids = {c.claim_id for ln in lines for c in ln.candidates}
    async with db.workspace_session(actor.workspace_id) as s:
        blocked = await restricted_claims(s, ids)
        texts = {
            c.id: f"{c.statement}" + (f" ({e.title})" if e and e.title else "")
            for c, e in (
                await s.execute(
                    select(Claim, Experience)
                    .outerjoin(Experience, Experience.id == Claim.experience_id)
                    .where(Claim.id.in_(list(ids) or [uuid4()]))
                )
            ).all()
        }
        classes = await claim_data_classes(s, ids - blocked)
    tally = judges._Tally()
    out: list[LineSuggestions] = []
    for ln in lines:
        send = [
            (str(c.claim_id), texts[c.claim_id])
            for c in ln.candidates
            if c.claim_id not in blocked and c.claim_id in texts
        ]
        verdicts, _ = await judges.judge_claims(
            call_model, ln.text, send, data_classes=classes, tally=tally
        )
        out.append(
            replace(
                ln,
                candidates=[
                    replace(
                        c,
                        judge=verdicts.get(str(c.claim_id)),
                        judge_note="restricted_not_sent" if c.claim_id in blocked else None,
                    )
                    for c in ln.candidates
                ],
            )
        )
    return out, tally.run()


# ---------------------------------------------------------------- 저장 공고 요건 빈도


@dataclass(frozen=True, slots=True)
class FrequencyGroup:
    key: str
    label: str
    postings: int  # 이 낱말이 나온 저장 공고 수 (공고당 한 번)
    required: int  # 그중 자격 요건에 나온 공고 수
    preferred: int  # 그중 우대 사항에 나온 공고 수
    examples: list[str]  # 원문 예시 (최대 3줄)
    posting_ids: list[UUID]


@dataclass(frozen=True, slots=True)
class FrequencyResult:
    total_postings: int
    groups: list[FrequencyGroup]
    statuses: list[str]
    # 사용자가 저장한 공고만 센다. 채용 시장 전체 통계가 아니다.
    basis: str = "saved_postings_only"


@dataclass
class _Group:
    label: str
    postings: dict[UUID, None] = field(default_factory=dict)  # 순서 있는 집합
    required: set[UUID] = field(default_factory=set)
    preferred: set[UUID] = field(default_factory=set)
    examples: list[str] = field(default_factory=list)


async def requirement_frequency(
    db: Database,
    actor: ActorContext,
    *,
    include_archived: bool = False,
    min_postings: int = 1,
    limit: int = 50,
) -> FrequencyResult:
    """저장 공고들 사이의 요건 빈도: "저장 공고 N개 중 M개가 X 요구".

    요건·우대 줄에서 기술 낱말을 규칙으로 뽑아 묶는다 (`domain.study.requirement_terms`).
    낱말이 없는 줄은 정규화한 문장 전체로 묶는다. 전형 절차는 세지 않는다.
    """
    statuses = [PostingStatus.SAVED.value]
    if include_archived:
        statuses.append(PostingStatus.ARCHIVED.value)
    async with db.workspace_session(actor.workspace_id) as s:
        aliases = await workspace_aliases(s)
        postings = (
            (
                await s.execute(
                    select(JobPosting)
                    .where(JobPosting.status.in_(statuses))
                    .order_by(JobPosting.saved_at, JobPosting.created_at)
                )
            )
            .scalars()
            .all()
        )
    groups: dict[str, _Group] = {}
    for p in postings:
        for f in (RequirementField.REQUIREMENTS, RequirementField.PREFERRED):
            for raw in _posting_list(p, f):
                line = " ".join(raw.split())
                terms = requirement_terms(line, aliases)
                keys = [(t.key, t.label) for t in terms] or [("line:" + normalize_line(line), line)]
                for key, label in keys:
                    g = groups.setdefault(key, _Group(label))
                    g.postings.setdefault(p.id, None)
                    (g.required if f == RequirementField.REQUIREMENTS else g.preferred).add(p.id)
                    if line not in g.examples and len(g.examples) < 3:
                        g.examples.append(line)
    out = [
        FrequencyGroup(
            key,
            g.label,
            len(g.postings),
            len(g.required),
            len(g.preferred),
            g.examples,
            list(g.postings),
        )
        for key, g in groups.items()
        if len(g.postings) >= max(1, min_postings)
    ]
    out.sort(key=lambda g: (-g.postings, -g.required, g.label.lower()))
    return FrequencyResult(len(postings), out[: max(1, min(limit, 200))], statuses)

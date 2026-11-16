"""학습·시험·오답·복습 (O09).

연결 사슬: 저장 공고의 요건 한 줄(`study_requirements`) → 학습 항목·시험 준비(`study_items`,
`study_assessments`) → 학습 기록·응시·오답(`study_sessions`, `study_attempts`, `study_mistakes`)
→ (사용자가 직접 올릴 때만) 확인 대기 주장. 이 모듈의 어떤 함수도 주장을 확정하지 않는다.

요건이 경험으로 채워졌는지는 사용자가 연결한 주장(`study_requirement_claims`)으로만 본다.
공고 문장과 주장 문장을 자동으로 맞춰 "충족"으로 판정하지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from career_core.db import (
    Claim,
    Database,
    Experience,
    JobPosting,
    StudyAssessment,
    StudyAttempt,
    StudyClaimCandidate,
    StudyItem,
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
    due_after,
    first_due,
    next_review,
    split_process,
)
from career_core.ports import ActorContext
from career_core.usecases.brain import NotFound
from career_core.usecases.index_queue import request_index

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
        item = await _get(s, StudyItem, item_id, "study_item")
        if title is not None:
            item.title = _title(title)
        if kind is not None:
            item.kind = _enum(ItemKind, kind, "invalid_kind")
        if notes is not None:
            item.notes = _text(notes)
        if status is not None:
            item.status = _enum(ItemStatus, status, "invalid_status")
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
        item = await _get(s, StudyItem, item_id, "study_item")
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
            await _get(s, StudyItem, study_item_id, "study_item")
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
            await _get(s, StudyItem, study_item_id, "study_item")
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
    db: Database, actor: ActorContext, *, now: datetime | None = None, limit: int = DUE_LIMIT
) -> list[DueMistake]:
    """`now`까지 복습할 오답 (가장 오래 밀린 것부터)."""
    now = now or utcnow()
    async with db.workspace_session(actor.workspace_id) as s:
        rows = (
            await s.execute(
                select(StudyMistake, StudyItem.title)
                .outerjoin(StudyItem, StudyItem.id == StudyMistake.study_item_id)
                .where(StudyMistake.status == MistakeStatus.OPEN, StudyMistake.due_at <= now)
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
        item = await _get(s, StudyItem, item_id, "study_item")
        await _get(s, Experience, experience_id, "experience")
        if attempt_id is not None:
            attempt = await _get(s, StudyAttempt, attempt_id, "attempt")
            if attempt.study_item_id != item.id:
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


async def get_item(db: Database, actor: ActorContext, item_id: UUID) -> ItemView:
    async with db.workspace_session(actor.workspace_id) as s:
        item = await _get(s, StudyItem, item_id, "study_item")
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
                    .where(StudyAttempt.study_item_id == item_id)
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
                .where(StudyClaimCandidate.study_item_id == item_id)
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
        item_ids = [i.id for i in item_rows]
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
            last[a.study_item_id] = _attempt_view(a)
        if a.assessment_id:
            last[a.assessment_id] = _attempt_view(a)
    claims: dict[UUID, list[LinkedClaim]] = {}
    for req_id, c in claim_rows:
        claims.setdefault(req_id, []).append(LinkedClaim(c.id, c.statement, c.status))
    items: dict[UUID, list[LinkedItem]] = {}
    for i in item_rows:
        items.setdefault(i.requirement_id, []).append(
            LinkedItem(i.id, i.title, i.status, last.get(i.id))
        )
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

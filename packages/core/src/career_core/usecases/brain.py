"""경험·주장·근거 관리.

확인(confirm)은 `approve` 권한을 가진 사람만 한다. 개인용에서는 공간 주인 본인이다.
편집자(editor)가 쓴 주장은 주인이 확인하기 전까지 후보(`extracted`)로 남는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Text, bindparam, case, func, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

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
from career_core.domain.experience_match import QUOTE_WEAK, quote_support
from career_core.domain.experiences import (
    EXPERIENCE_KINDS,
    ExperienceKind,
    check_period,
    experience_kind,
    month_text,
    normalize_tags,
    parse_month,
)
from career_core.domain.sources import SourcePurpose
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


async def block_purpose(s: AsyncSession, block_id: UUID) -> str | None:
    """블록이 속한 원본의 용도 (c0032). 블록이 없으면 None."""
    return await s.scalar(
        select(Source.purpose)
        .join(SourceVersion, SourceVersion.source_id == Source.id)
        .join(ParseRun, ParseRun.source_version_id == SourceVersion.id)
        .join(DocumentBlock, DocumentBlock.parse_run_id == ParseRun.id)
        .where(DocumentBlock.id == block_id)
    )


def require_experience_source(purpose: str | None) -> None:
    """근거(원문 위치)는 내 경험 자료에서만 (plan §19 원칙 1). 증빙·참고는 근거가 되지 않는다."""
    if purpose is not None and purpose != SourcePurpose.EXPERIENCE:
        raise ClaimRuleError("source_not_experience")


async def create_span(
    db: Database, actor: ActorContext, block_id: UUID, start: int, end: int
) -> UUID:
    async with db.workspace_session(actor.workspace_id) as s:
        block = await s.get(DocumentBlock, block_id)
        if block is None:
            raise NotFound("block")
        require_experience_source(await block_purpose(s, block.id))
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
    # c0032: 원본 용도. MCP는 증빙 원본의 글을 내보내지 않는다.
    source_purpose: str = SourcePurpose.EXPERIENCE.value


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
        source_purpose=source.purpose,
    )


# ---------------------------------------------------------------- 경험

ACTIVE = "active"
MAX_LIST_LIMIT = 1000
SORTS = ("start", "updated", "title")
REVIEW_FILTERS = ("needs_review", "confirmed", "no_claims")


class _Unset:
    def __repr__(self) -> str:  # pragma: no cover - 표시용
        return "UNSET"


UNSET: Any = _Unset()


def _text(value: str | None, limit: int = 300) -> str | None:
    value = (value or "").strip()
    return value[:limit] or None


def _month(value: str | date | None) -> date | None:
    if isinstance(value, date):
        if value.day != 1:
            raise ClaimRuleError("invalid_month")
        return value
    return parse_month(value)


async def _check_parent(s: AsyncSession, exp_id: UUID | None, parent_id: UUID | None) -> None:
    """한 단계만: 부모는 같은 공간의 활성 맨 위 경험, 자식이 있는 경험은 자식이 될 수 없다.

    다른 공간의 ID는 RLS로 보이지 않아 ``parent_not_found``다.
    """
    if parent_id is None:
        return
    if exp_id is not None and parent_id == exp_id:
        raise ClaimRuleError("parent_self")
    parent = await s.get(Experience, parent_id, with_for_update=True)
    if parent is None or parent.status != ACTIVE:
        raise ClaimRuleError("parent_not_found")
    if parent.parent_id is not None:
        raise ClaimRuleError("parent_is_child")
    if exp_id is not None:
        child = await s.scalar(select(Experience.id).where(Experience.parent_id == exp_id).limit(1))
        if child is not None:
            raise ClaimRuleError("experience_has_children")


_TRIGGER_CODES = {
    "experience_parent_self": "parent_self",
    "experience_parent_is_child": "parent_is_child",
    "experience_has_children": "experience_has_children",
}


async def _flush_hierarchy(s: AsyncSession) -> None:
    """DB trigger(한 단계)가 막은 경우를 같은 오류 코드로 바꾼다 (동시에 고친 경우의 안전장치)."""
    try:
        await s.flush()
    except IntegrityError as exc:
        message = str(exc.orig)
        for marker, code in _TRIGGER_CODES.items():
            if marker in message:
                raise ClaimRuleError(code) from exc
        raise


async def create_experience(
    db: Database,
    actor: ActorContext,
    *,
    title: str,
    organization: str | None = None,
    role: str | None = None,
    period_text: str | None = None,
    kind: str = ExperienceKind.OTHER.value,
    parent_id: UUID | None = None,
    started_on: str | date | None = None,
    ended_on: str | date | None = None,
    ongoing: bool = False,
    tags: list[str] | None = None,
) -> UUID:
    title = (title or "").strip()
    if not title:
        raise ClaimRuleError("title_required")
    start, end = _month(started_on), _month(ended_on)
    check_period(start, end, ongoing)
    kind = experience_kind(kind)
    tags = normalize_tags(tags)
    async with db.workspace_session(actor.workspace_id) as s:
        await _check_parent(s, None, parent_id)
        exp = Experience(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            title=title,
            organization=_text(organization),
            role=_text(role),
            period_text=_text(period_text, 100),
            kind=kind,
            parent_id=parent_id,
            started_on=start,
            ended_on=end,
            ongoing=ongoing,
            tags=tags,
            origin=ClaimOrigin.USER.value,
            created_by=actor.subject_id,
        )
        s.add(exp)
        await _flush_hierarchy(s)
    return exp.id


async def update_experience(
    db: Database,
    actor: ActorContext,
    experience_id: UUID,
    *,
    title: str | None = UNSET,
    organization: str | None = UNSET,
    role: str | None = UNSET,
    period_text: str | None = UNSET,
    kind: str = UNSET,
    parent_id: UUID | None = UNSET,
    started_on: str | date | None = UNSET,
    ended_on: str | date | None = UNSET,
    ongoing: bool = UNSET,
    tags: list[str] | None = UNSET,
) -> None:
    """준 칸만 고친다 (``None``은 비우기). 종류·상하·기간·태그를 고쳐도 주장 확인 상태는 그대로다."""
    async with db.workspace_session(actor.workspace_id) as s:
        exp = await s.get(Experience, experience_id, with_for_update=True)
        if exp is None:
            raise NotFound("experience")
        if title is not UNSET:
            value = (title or "").strip()
            if not value:
                raise ClaimRuleError("title_required")
            exp.title = value
        if organization is not UNSET:
            exp.organization = _text(organization)
        if role is not UNSET:
            exp.role = _text(role)
        if period_text is not UNSET:
            exp.period_text = _text(period_text, 100)
        if kind is not UNSET:
            exp.kind = experience_kind(kind)
        start = exp.started_on if started_on is UNSET else _month(started_on)
        end = exp.ended_on if ended_on is UNSET else _month(ended_on)
        now_ongoing = exp.ongoing if ongoing is UNSET else bool(ongoing)
        if now_ongoing and ongoing is not UNSET and ended_on is UNSET:
            end = None  # 진행 중으로 바꾸면 끝 달을 비운다
        check_period(start, end, now_ongoing)
        exp.started_on, exp.ended_on, exp.ongoing = start, end, now_ongoing
        if tags is not UNSET:
            exp.tags = normalize_tags(tags)
        if parent_id is not UNSET and parent_id != exp.parent_id:
            await _check_parent(s, exp.id, parent_id)
            exp.parent_id = parent_id
        exp.updated_at = utcnow()
        await _flush_hierarchy(s)


@dataclass(frozen=True, slots=True)
class ExperienceFilter:
    """경험 목록 거름. 빈 값은 거르지 않는다."""

    kinds: tuple[str, ...] = ()
    parent_id: UUID | None = None
    top_level: bool = False
    tags: tuple[str, ...] = ()  # 하나라도 있으면 (대소문자 무시)
    year: int | None = None  # 그해와 기간이 겹친다
    organization: str | None = None  # 같은 조직 (대소문자 무시)
    review: str | None = None  # needs_review | confirmed | no_claims
    q: str | None = None  # 제목·조직·역할·태그에 든 글자
    sort: str = "start"  # start(시작 달 최근 먼저) | updated | title
    duplicates: bool = False  # c0050: "같은 경험일 수 있음" 표시가 있는 것만
    limit: int = MAX_LIST_LIMIT
    offset: int = 0


@dataclass(frozen=True, slots=True)
class ExperienceSummary:
    id: UUID
    title: str
    organization: str | None
    role: str | None
    period_text: str | None
    origin: str
    claim_counts: dict[str, int]
    kind: str = ExperienceKind.OTHER.value
    parent_id: UUID | None = None
    parent_title: str | None = None
    started_on: str | None = None  # YYYY-MM
    ended_on: str | None = None
    ongoing: bool = False
    tags: list[str] = field(default_factory=list)
    children_count: int = 0
    updated_at: datetime | None = None
    # c0050: 추출이 "같은 경험일 수 있음"으로 본 기존 경험 (활성일 때만)
    possible_duplicate_of: UUID | None = None
    possible_duplicate_title: str | None = None

    @property
    def needs_review(self) -> int:
        return self.claim_counts.get(ClaimStatus.EXTRACTED, 0)


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _claim_exists(*statuses: str):
    return (
        select(Claim.id)
        .where(Claim.experience_id == Experience.id, Claim.status.in_(statuses))
        .correlate(Experience)
        .exists()
    )


def _filtered(f: ExperienceFilter) -> list:
    conds: list = [Experience.status == ACTIVE]
    if f.kinds:
        conds.append(Experience.kind.in_([experience_kind(k) for k in f.kinds]))
    if f.parent_id is not None:
        conds.append(Experience.parent_id == f.parent_id)
    elif f.top_level:
        conds.append(Experience.parent_id.is_(None))
    if f.tags:
        wanted = [t.casefold() for t in normalize_tags(list(f.tags), strict=False)]
        if wanted:
            conds.append(
                text(
                    "EXISTS (SELECT 1 FROM unnest(core.experiences.tags) AS t "
                    "WHERE lower(t) = ANY(:tags_wanted))"
                ).bindparams(bindparam("tags_wanted", wanted, type_=ARRAY(Text)))
            )
    if f.year is not None:
        first, last = date(f.year, 1, 1), date(f.year, 12, 1)
        end = func.coalesce(
            Experience.ended_on,
            case((Experience.ongoing, func.current_date()), else_=Experience.started_on),
        )
        conds += [Experience.started_on.is_not(None), Experience.started_on <= last, end >= first]
    if f.organization and f.organization.strip():
        conds.append(func.lower(Experience.organization) == f.organization.strip().lower())
    if f.review == "needs_review":
        conds.append(_claim_exists(ClaimStatus.EXTRACTED))
    elif f.review == "confirmed":
        conds += [_claim_exists(*CONFIRMED_STATUSES), ~_claim_exists(ClaimStatus.EXTRACTED)]
    elif f.review == "no_claims":
        conds.append(~_claim_exists(ClaimStatus.EXTRACTED, *CONFIRMED_STATUSES))
    elif f.review is not None:
        raise ClaimRuleError("invalid_review_filter")
    if f.duplicates:
        conds.append(Experience.possible_duplicate_of.is_not(None))
    if f.q and f.q.strip():
        pattern = _like(f.q.strip())
        conds.append(
            or_(
                Experience.title.ilike(pattern, escape="\\"),
                Experience.organization.ilike(pattern, escape="\\"),
                Experience.role.ilike(pattern, escape="\\"),
                func.array_to_string(Experience.tags, " ").ilike(pattern, escape="\\"),
            )
        )
    return conds


def _order(sort: str) -> tuple:
    if sort == "start":
        return (Experience.started_on.desc().nulls_last(), Experience.created_at, Experience.id)
    if sort == "updated":
        return (Experience.updated_at.desc(), Experience.id)
    if sort == "title":
        # 코드 포인트 순서 (설치의 collation과 무관하게 한글은 가나다, 영문은 abc)
        return (func.lower(Experience.title).collate("C"), Experience.created_at, Experience.id)
    raise ClaimRuleError("invalid_sort")


async def page_experiences(
    db: Database, actor: ActorContext, f: ExperienceFilter | None = None
) -> tuple[list[ExperienceSummary], int]:
    """거른 경험 (쪽, 거른 전체 수). 활성 경험만."""
    f = f or ExperienceFilter()
    limit = max(1, min(f.limit, MAX_LIST_LIMIT))
    offset = max(0, f.offset)
    conds = _filtered(f)
    order = _order(f.sort)
    parent = aliased(Experience)
    dup = aliased(Experience)
    child = aliased(Experience)
    children = (
        select(func.count(child.id))
        .where(child.parent_id == Experience.id, child.status == ACTIVE)
        .correlate(Experience)
        .scalar_subquery()
    )
    async with db.workspace_session(actor.workspace_id) as s:
        total = await s.scalar(select(func.count()).select_from(Experience).where(*conds))
        rows = (
            await s.execute(
                select(Experience, parent.title, children, dup.id, dup.title)
                .outerjoin(parent, parent.id == Experience.parent_id)
                .outerjoin(
                    dup, (dup.id == Experience.possible_duplicate_of) & (dup.status == ACTIVE)
                )
                .where(*conds)
                .order_by(*order)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        ids = [e.id for e, *_ in rows]
        counts = (
            (
                await s.execute(
                    select(Claim.experience_id, Claim.status, func.count())
                    .where(Claim.experience_id.in_(ids))
                    .group_by(Claim.experience_id, Claim.status)
                )
            ).all()
            if ids
            else []
        )
    by_exp: dict[UUID, dict[str, int]] = {}
    for exp_id, status, n in counts:
        by_exp.setdefault(exp_id, {})[status] = n
    out = [
        ExperienceSummary(
            id=e.id,
            title=e.title,
            organization=e.organization,
            role=e.role,
            period_text=e.period_text,
            origin=e.origin,
            claim_counts=by_exp.get(e.id, {}),
            kind=e.kind,
            parent_id=e.parent_id,
            parent_title=parent_title,
            started_on=month_text(e.started_on),
            ended_on=month_text(e.ended_on),
            ongoing=e.ongoing,
            tags=list(e.tags or []),
            children_count=n_children or 0,
            updated_at=e.updated_at,
            possible_duplicate_of=dup_id,
            possible_duplicate_title=dup_title,
        )
        for e, parent_title, n_children, dup_id, dup_title in rows
    ]
    return out, total or 0


async def list_experiences(
    db: Database, actor: ActorContext, f: ExperienceFilter | None = None
) -> list[ExperienceSummary]:
    return (await page_experiences(db, actor, f))[0]


# ---------------------------------------------------------------- 브레인 색인


@dataclass(frozen=True, slots=True)
class RecentExperience:
    id: UUID
    title: str
    kind: str
    organization: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TagCount:
    tag: str
    count: int


@dataclass(frozen=True, slots=True)
class BrainOverview:
    experiences: int
    kinds: dict[str, int]  # 모든 종류 (0 포함)
    needs_review: int  # 확인할 주장이 있는 경험 수
    pending_claims: int  # 확인할 주장 수
    undated: int  # 시작 달이 없는 경험
    year_min: int | None
    year_max: int | None
    recent: list[RecentExperience]
    tags: list[TagCount]


MAX_OVERVIEW_TAGS = 30
RECENT_LIMIT = 5

_TAGS_SQL = text(
    """
SELECT mode() WITHIN GROUP (ORDER BY t COLLATE "C") AS tag, count(*) AS n
FROM core.experiences e, unnest(e.tags) AS t
WHERE e.status = 'active'
GROUP BY lower(t)
ORDER BY n DESC, lower(t) COLLATE "C"
LIMIT :limit
"""
)
_YEARS_SQL = text(
    """
SELECT min(extract(year FROM started_on))::int AS lo,
       max(extract(year FROM coalesce(ended_on,
           CASE WHEN ongoing THEN current_date ELSE started_on END)))::int AS hi,
       count(*) FILTER (WHERE started_on IS NULL) AS undated
FROM core.experiences WHERE status = 'active'
"""
)


async def brain_overview(db: Database, actor: ActorContext) -> BrainOverview:
    """브레인 첫 화면(색인)의 수: 종류별 수, 확인할 것, 최근 고친 5개, 자주 쓴 태그, 연도 범위."""
    async with db.workspace_session(actor.workspace_id) as s:
        kinds = dict(
            (
                await s.execute(
                    select(Experience.kind, func.count())
                    .where(Experience.status == ACTIVE)
                    .group_by(Experience.kind)
                )
            ).all()
        )
        review = (
            await s.execute(
                select(func.count(func.distinct(Claim.experience_id)), func.count())
                .join(Experience, Experience.id == Claim.experience_id)
                .where(Claim.status == ClaimStatus.EXTRACTED, Experience.status == ACTIVE)
            )
        ).one()
        recent = (
            (
                await s.execute(
                    select(Experience)
                    .where(Experience.status == ACTIVE)
                    .order_by(Experience.updated_at.desc(), Experience.id)
                    .limit(RECENT_LIMIT)
                )
            )
            .scalars()
            .all()
        )
        tags = (await s.execute(_TAGS_SQL, {"limit": MAX_OVERVIEW_TAGS})).all()
        years = (await s.execute(_YEARS_SQL)).one()
    return BrainOverview(
        experiences=sum(kinds.values()),
        kinds={k: kinds.get(k, 0) for k in EXPERIENCE_KINDS},
        needs_review=review[0] or 0,
        pending_claims=review[1] or 0,
        undated=years.undated or 0,
        year_min=years.lo,
        year_max=years.hi,
        recent=[
            RecentExperience(e.id, e.title, e.kind, e.organization, e.updated_at) for e in recent
        ],
        tags=[TagCount(t.tag, t.n) for t in tags],
    )


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
    # 원문 위치 근거: 인용이 주장을 받치는가 (숫자·핵심 낱말 비교, ``domain.experience_match.quote_support``).
    # True면 화면에 "인용이 약함". 모델을 부르지 않고 읽을 때 계산한다 (문장을 고치면 다시 본다).
    weak_quote: bool = False


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
    # c0030 (O19)
    kind: str = ExperienceKind.OTHER.value
    parent_id: UUID | None = None
    parent_title: str | None = None
    started_on: str | None = None  # YYYY-MM
    ended_on: str | None = None
    ongoing: bool = False
    tags: list[str] = field(default_factory=list)
    children: list[ChildExperience] = field(default_factory=list)
    updated_at: datetime | None = None
    # c0050: 합친 경험이면 대상 경험(보관 상태), 추출이 "같은 경험일 수 있음"으로 본 경험
    merged_into_id: UUID | None = None
    merged_at: datetime | None = None
    possible_duplicate_of: UUID | None = None
    possible_duplicate_title: str | None = None


@dataclass(frozen=True, slots=True)
class ChildExperience:
    id: UUID
    title: str
    kind: str
    role: str | None
    started_on: str | None
    ended_on: str | None
    ongoing: bool


async def get_experience(db: Database, actor: ActorContext, experience_id: UUID) -> ExperienceView:
    async with db.workspace_session(actor.workspace_id) as s:
        exp = await s.get(Experience, experience_id)
        if exp is None:
            raise NotFound("experience")
        parent_title = (
            await s.scalar(select(Experience.title).where(Experience.id == exp.parent_id))
            if exp.parent_id
            else None
        )
        dup_title = (
            await s.scalar(
                select(Experience.title).where(
                    Experience.id == exp.possible_duplicate_of, Experience.status == ACTIVE
                )
            )
            if exp.possible_duplicate_of
            else None
        )
        children = (
            (
                await s.execute(
                    select(Experience)
                    .where(Experience.parent_id == exp.id, Experience.status == ACTIVE)
                    .order_by(*_order("start"))
                )
            )
            .scalars()
            .all()
        )
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
    statements = {c.id: c.statement for c in claims}
    evidence: dict[UUID, list[EvidenceView]] = {}
    for ev, span, page, title in span_rows:
        evidence.setdefault(ev.claim_id, []).append(
            EvidenceView(
                ev.id,
                ev.kind,
                span_id=span.id,
                quote=span.quote,
                page=page,
                source_title=title,
                weak_quote=quote_support(statements.get(ev.claim_id, ""), span.quote) == QUOTE_WEAK,
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
        kind=exp.kind,
        parent_id=exp.parent_id,
        parent_title=parent_title,
        started_on=month_text(exp.started_on),
        ended_on=month_text(exp.ended_on),
        ongoing=exp.ongoing,
        tags=list(exp.tags or []),
        updated_at=exp.updated_at,
        merged_into_id=exp.merged_into_id,
        merged_at=exp.merged_at,
        possible_duplicate_of=exp.possible_duplicate_of if dup_title else None,
        possible_duplicate_title=dup_title,
        children=[
            ChildExperience(
                c.id,
                c.title,
                c.kind,
                c.role,
                month_text(c.started_on),
                month_text(c.ended_on),
                c.ongoing,
            )
            for c in children
        ],
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
    span = await s.get(SourceSpan, span_id)
    if span is None:
        raise NotFound("span")
    require_experience_source(await block_purpose(s, span.document_block_id))
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
        # 상태를 올리는 것도 확인 행위라 approve 권한이 있을 때만.
        if claim.status == ClaimStatus.SELF_CONFIRMED and can_confirm(actor):
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
        # 확인된 주장을 뒤집거나 폐기하는 것은 확인과 같은 무게다. 폐기는 되돌릴 수 없다.
        if claim.status in CONFIRMED_STATUSES and not can_confirm(actor):
            raise ClaimRuleError("confirm_requires_approve")
        claim.status = status.value
        return claim.status


@dataclass(frozen=True, slots=True)
class ClaimBrief:
    id: UUID
    statement: str
    status: str
    kind: str
    contribution: str
    experience_id: UUID
    experience_title: str


async def claims_by_ids(db: Database, actor: ActorContext, ids: list[UUID]) -> list[ClaimBrief]:
    """주장 여러 개의 짧은 보기 (문서의 근거 표시용). 없는 ID는 빠진다."""
    if not ids:
        return []
    async with db.workspace_session(actor.workspace_id) as s:
        rows = (
            await s.execute(
                select(Claim, Experience.title)
                .join(Experience, Experience.id == Claim.experience_id)
                .where(Claim.id.in_(ids[:MAX_BRIEF_IDS]))
            )
        ).all()
    order = {cid: i for i, cid in enumerate(ids)}
    return sorted(
        (
            ClaimBrief(c.id, c.statement, c.status, c.kind, c.contribution, c.experience_id, t)
            for c, t in rows
        ),
        key=lambda b: order.get(b.id, 0),
    )


MAX_BRIEF_IDS = 200


# ---------------------------------------------------------------- 확인 대기 주장 (한 번의 조회)


@dataclass(frozen=True, slots=True)
class PendingClaim:
    claim_id: UUID
    experience_id: UUID
    experience_title: str
    kind: str
    statement: str
    contribution: str
    evidence: int  # 근거 수 (원문 위치 + 인터뷰 답변)
    created_at: datetime


MAX_PENDING_CLAIMS = 200


async def list_pending_claims(
    db: Database, actor: ActorContext, *, limit: int = 50
) -> tuple[list[PendingClaim], bool]:
    """확인 대기(extracted) 주장을 활성 경험 순서대로. (목록, 잘림 여부). 경험마다 조회하지 않는다."""
    limit = max(1, min(limit, MAX_PENDING_CLAIMS))
    evidence = (
        select(func.count(ClaimEvidence.id))
        .where(ClaimEvidence.claim_id == Claim.id)
        .correlate(Claim)
        .scalar_subquery()
    )
    async with db.workspace_session(actor.workspace_id) as s:
        rows = (
            await s.execute(
                select(Claim, Experience.title, evidence)
                .join(Experience, Experience.id == Claim.experience_id)
                .where(Claim.status == ClaimStatus.EXTRACTED, Experience.status == "active")
                .order_by(Experience.created_at, Experience.id, Claim.created_at, Claim.id)
                .limit(limit + 1)
            )
        ).all()
    out = [
        PendingClaim(
            c.id, c.experience_id, title, c.kind, c.statement, c.contribution, n or 0, c.created_at
        )
        for c, title, n in rows[:limit]
    ]
    return out, len(rows) > limit


# ---------------------------------------------------------------- 여러 사실 한 번에 확인

MAX_BATCH_CONFIRM = 200
BATCH_CONTRIBUTIONS = ("personal", "team", "as_is")


@dataclass(frozen=True, slots=True)
class BatchConfirmResult:
    confirmed: list[tuple[UUID, str]]  # (사실, 새 상태)
    skipped: list[tuple[UUID, str]]  # (사실, 까닭: contribution_required)
    truncated: bool  # 고른 범위의 후보가 한도보다 많아 나머지는 그대로


async def confirm_claims(
    db: Database,
    actor: ActorContext,
    *,
    contribution: str,
    experience_id: UUID | None = None,
    source_id: UUID | None = None,
    claim_ids: list[UUID] | None = None,
) -> BatchConfirmResult:
    """확인 대기(``extracted``) 사실을 한 번에 확인한다 — 한 경험, 한 원본에서 나온 것, 또는 고른 ID.

    한 건 확인(``confirm_claim``)과 같은 규칙: 확인 권한(approve)이 있어야 하고, 팀/본인 구분이 있어야 확인된다.
    ``contribution``은 모두에 같은 선택을 준다: ``personal``(본인) · ``team``(팀) · ``as_is``(각 사실에 적힌 대로).
    ``as_is``에서 구분이 "모름"인 사실은 확인하지 않고 ``skipped``(``contribution_required``)로 돌려준다.
    활성 경험의 사실만. 한 번에 200개까지 (넘으면 ``truncated``, 나머지는 그대로).
    """
    if not can_confirm(actor):
        raise ClaimRuleError("confirm_requires_approve")
    if contribution not in BATCH_CONTRIBUTIONS:
        raise ClaimRuleError("invalid_contribution")
    if sum(x is not None for x in (experience_id, source_id, claim_ids)) != 1:
        raise ClaimRuleError("choose_one_scope")
    q = (
        select(Claim)
        .join(Experience, Experience.id == Claim.experience_id)
        .where(Claim.status == ClaimStatus.EXTRACTED, Experience.status == ACTIVE)
    )
    if experience_id is not None:
        q = q.where(Claim.experience_id == experience_id)
    elif source_id is not None:
        from_source = (
            select(ClaimEvidence.claim_id)
            .join(SourceSpan, SourceSpan.id == ClaimEvidence.source_span_id)
            .join(DocumentBlock, DocumentBlock.id == SourceSpan.document_block_id)
            .join(ParseRun, ParseRun.id == DocumentBlock.parse_run_id)
            .join(SourceVersion, SourceVersion.id == ParseRun.source_version_id)
            .where(SourceVersion.source_id == source_id)
        )
        q = q.where(Claim.id.in_(from_source))
    else:
        ids = list(dict.fromkeys(claim_ids or []))
        if not ids:
            raise ClaimRuleError("choose_one_scope")
        if len(ids) > MAX_BATCH_CONFIRM:
            raise ClaimRuleError("too_many_ids")
        q = q.where(Claim.id.in_(ids))
    confirmed: list[tuple[UUID, str]] = []
    skipped: list[tuple[UUID, str]] = []
    async with db.workspace_session(actor.workspace_id) as s:
        if experience_id is not None and await s.get(Experience, experience_id) is None:
            raise NotFound("experience")
        if source_id is not None and await s.get(Source, source_id) is None:
            raise NotFound("source")
        rows = (
            await s.scalars(
                q.order_by(Claim.created_at, Claim.id)
                .limit(MAX_BATCH_CONFIRM + 1)
                .with_for_update(of=Claim)
            )
        ).all()
        truncated = len(rows) > MAX_BATCH_CONFIRM
        rows = rows[:MAX_BATCH_CONFIRM]
        spans = set(
            (
                await s.scalars(
                    select(ClaimEvidence.claim_id).where(
                        ClaimEvidence.claim_id.in_([c.id for c in rows]),
                        ClaimEvidence.kind == EvidenceKind.SOURCE_SPAN,
                    )
                )
            ).all()
        )
        for claim in rows:
            if contribution != "as_is":
                claim.contribution = contribution
            if claim.contribution == Contribution.UNKNOWN:
                skipped.append((claim.id, "contribution_required"))
                continue
            _mark_confirmed(claim, actor, has_span=claim.id in spans)
            confirmed.append((claim.id, claim.status))
    return BatchConfirmResult(confirmed, skipped, truncated)

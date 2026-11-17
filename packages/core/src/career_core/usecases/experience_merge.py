"""같은 경험 합치기·경험 지우기·"같은 경험일 수 있음" 표시 지우기 (c0050, 2026-11-18 사용 점검).

합치기(``merge_experiences``): 여러 자료에서 따로 만들어진 같은 경험을 하나(대상)로 모은다.

- 옮기는 것: 주장(확인 상태 그대로)과 근거, 아래 경험, 태그(합집합), 자격·학력·포트폴리오 항목·경험 인터뷰의
  연결, 지원 준비(고른 경험·후보)와 면접 준비(질문의 경험 후보) JSON 안의 경험 ID.
- 대상 경험의 칸(제목·조직·역할·기간·종류)은 그대로 둔다. 비어 있는 칸만 합친 경험에서 채운다.
- 확인 전(``extracted``) 주장이 대상의 다른 주장과 같은 문장이면 근거를 그 주장으로 옮기고 후보는 폐기한다.
  확인한 사실은 지우거나 합치지 않는다 (둘 다 남는다).
- 합친 경험은 지우지 않는다: ``status = retired``, ``merged_into_id`` = 대상, ``merged_at``. 이력서 revision은
  추가·조회만이라 고치지 않는다 — 이력서가 옛 경험을 읽을 때 ``merged_into_id``를 따라 대상 경험을 쓴다.
- 확인한 사실을 옮기는 일이라 확인 권한(approve)이 있어야 한다.

지우기(``delete_experience``): **쓰이는 경험은 지우지 않는다** (``experience_in_use``, 무엇이 쓰는지 함께 준다).
쓰는 곳: 아래 경험, 포트폴리오 항목, 경험 인터뷰(답변이 근거일 수 있다), 지원 문서·이력서·포트폴리오·공통 답변·
학습이 근거로 쓴 사실, 이력서 판본의 현재 revision, 지원 준비에서 고른 경험. 확인한 사실이 있으면
``include_confirmed=True``로 다시 불러야 지운다. 같은 경험이 둘이면 지우기 대신 합치기를 쓴다.
지우면 주장·근거 연결이 같이 지워지고(삭제 기록이 남는다) 자격·학력 연결은 비운다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import String, cast, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from career_core.db import Claim, ClaimEvidence, Database, Experience, InterviewSession
from career_core.db.base import utcnow
from career_core.db.models import (
    AnswerAssetClaim,
    ApplicationPrep,
    Credential,
    DocumentClaim,
    Education,
    InterviewPrepQuestion,
    PortfolioItem,
    PortfolioLineClaim,
    ResumeLineClaim,
    ResumeRevision,
    ResumeVersion,
    StudyClaimCandidate,
    StudyRequirementClaim,
)
from career_core.domain.brain import CONFIRMED_STATUSES, ClaimRuleError, ClaimStatus
from career_core.domain.experience_match import same_statement
from career_core.domain.experiences import normalize_tags
from career_core.ports import ActorContext
from career_core.usecases.brain import ACTIVE, NotFound, can_confirm
from career_core.usecases.index_queue import request_index

MAX_MERGE = 20
RETIRED = "retired"


class ExperienceInUse(ClaimRuleError):
    def __init__(self, references: dict[str, int]) -> None:
        super().__init__("experience_in_use")
        self.references = references


@dataclass(frozen=True, slots=True)
class MergeResult:
    target_id: UUID
    merged: list[UUID]
    claims_moved: int
    claims_folded: int  # 같은 문장이라 근거를 옮기고 폐기한 확인 전 후보
    children_moved: int
    links_moved: dict[str, int] = field(default_factory=dict)


def _swap_ids(value: Any, mapping: dict[str, str]) -> tuple[Any, bool]:
    """JSON 안의 문자열 ID를 바꾼다 (지원 준비·면접 준비의 경험 후보)."""
    if isinstance(value, str):
        new = mapping.get(value)
        return (new, True) if new is not None else (value, False)
    if isinstance(value, list):
        changed = False
        out = []
        for v in value:
            nv, c = _swap_ids(v, mapping)
            changed |= c
            out.append(nv)
        return out, changed
    if isinstance(value, dict):
        changed = False
        out_d = {}
        for k, v in value.items():
            nv, c = _swap_ids(v, mapping)
            changed |= c
            out_d[k] = nv
        return out_d, changed
    return value, False


def _dedupe_choices(choices: dict) -> dict:
    """지원 준비에서 고른 경험: 문서마다 같은 경험이 둘이 되면 앞의 것만 (고른 사실은 합친다)."""
    out: dict = {}
    for doc_id, items in (choices or {}).items():
        kept: list[dict] = []
        by_exp: dict[str, dict] = {}
        for it in items or []:
            if not isinstance(it, dict) or "experience_id" not in it:
                kept.append(it)
                continue
            prev = by_exp.get(it["experience_id"])
            if prev is None:
                by_exp[it["experience_id"]] = it
                kept.append(it)
            elif isinstance(prev.get("fact_ids"), list) and isinstance(it.get("fact_ids"), list):
                prev["fact_ids"] = list(dict.fromkeys([*prev["fact_ids"], *it["fact_ids"]]))
        out[doc_id] = kept
    return out


async def _rewrite_json(s: AsyncSession, mapping: dict[str, str]) -> dict[str, int]:
    """지원 준비·면접 준비 JSON의 경험 ID. 글로 한 번 걸러 해당 행만 읽는다."""
    counts = {"application_preps": 0, "interview_prep_questions": 0}
    if not mapping:
        return counts
    ids = list(mapping)

    def mentions(*cols):
        return func.concat(*(cast(c, String) for c in cols)).op("~")("|".join(ids))

    for prep in (
        await s.scalars(
            select(ApplicationPrep)
            .where(
                mentions(
                    ApplicationPrep.choices, ApplicationPrep.questions, ApplicationPrep.requirements
                )
            )
            .with_for_update()
        )
    ).all():
        changed = False
        for name in ("choices", "questions", "requirements"):
            value, c = _swap_ids(getattr(prep, name), mapping)
            if c:
                if name == "choices":
                    value = _dedupe_choices(value)
                setattr(prep, name, value)
                changed = True
        if changed:
            prep.updated_at = utcnow()
            counts["application_preps"] += 1
    for q in (
        await s.scalars(
            select(InterviewPrepQuestion)
            .where(mentions(InterviewPrepQuestion.suggestions))
            .with_for_update()
        )
    ).all():
        value, c = _swap_ids(q.suggestions, mapping)
        if c:
            q.suggestions = value
            q.updated_at = utcnow()
            counts["interview_prep_questions"] += 1
    return counts


async def merge_experiences(
    db: Database, actor: ActorContext, target_id: UUID, source_ids: list[UUID]
) -> MergeResult:
    if not can_confirm(actor):
        raise ClaimRuleError("merge_requires_approve")
    sources = list(dict.fromkeys(source_ids))
    if not sources:
        raise ClaimRuleError("nothing_to_merge")
    if len(sources) > MAX_MERGE:
        raise ClaimRuleError("too_many_to_merge")
    if target_id in sources:
        raise ClaimRuleError("merge_self")
    now = utcnow()
    async with db.workspace_session(actor.workspace_id) as s:
        target = await s.get(Experience, target_id, with_for_update=True)
        if target is None:
            raise NotFound("experience")
        rows = (
            await s.scalars(select(Experience).where(Experience.id.in_(sources)).with_for_update())
        ).all()
        if len(rows) != len(sources):
            raise NotFound("experience")
        if target.status != ACTIVE or any(r.status != ACTIVE for r in rows):
            raise ClaimRuleError("experience_not_active")
        if target.parent_id in sources:
            raise ClaimRuleError("merge_parent_into_child")
        children = (
            await s.scalars(
                select(Experience)
                .where(Experience.parent_id.in_(sources), Experience.id != target_id)
                .with_for_update()
            )
        ).all()
        if children and target.parent_id is not None:
            raise ClaimRuleError("merge_children_into_child")

        # 대상 칸: 비어 있는 칸만 채운다
        order = {sid: i for i, sid in enumerate(sources)}
        rows = sorted(rows, key=lambda r: order[r.id])
        for r in rows:
            target.organization = target.organization or r.organization
            target.role = target.role or r.role
            target.period_text = target.period_text or r.period_text
            if target.started_on is None and r.started_on is not None:
                target.started_on, target.ended_on, target.ongoing = (
                    r.started_on,
                    r.ended_on,
                    r.ongoing,
                )
            if target.kind == "other" and r.kind != "other":
                target.kind = r.kind
        target.tags = normalize_tags(
            [*(target.tags or []), *(t for r in rows for t in (r.tags or []))], strict=False
        )
        if target.possible_duplicate_of in sources:
            target.possible_duplicate_of = None
        target.updated_at = now

        moved = (
            await s.execute(
                update(Claim)
                .where(Claim.experience_id.in_(sources))
                .values(experience_id=target_id, updated_at=now)
            )
        ).rowcount
        folded = await _fold_duplicates(s, target_id, sources)
        for child in children:
            child.parent_id = target_id
            child.updated_at = now
        links: dict[str, int] = {}
        for name, model in (
            ("credentials", Credential),
            ("education", Education),
            ("portfolio_items", PortfolioItem),
            ("interview_sessions", InterviewSession),
        ):
            values: dict[str, Any] = {"experience_id": target_id}
            if hasattr(model, "updated_at"):
                values["updated_at"] = now
            n = (
                await s.execute(
                    update(model).where(model.experience_id.in_(sources)).values(**values)
                )
            ).rowcount
            if n:
                links[name] = n
        # 다른 경험의 표시·합친 기록도 대상으로 (사슬을 남기지 않는다)
        await s.execute(
            update(Experience)
            .where(Experience.possible_duplicate_of.in_(sources), Experience.id != target_id)
            .values(possible_duplicate_of=target_id)
        )
        await s.execute(
            update(Experience)
            .where(Experience.merged_into_id.in_(sources))
            .values(merged_into_id=target_id)
        )
        mapping = {str(r.id): str(target_id) for r in rows}
        for name, n in (await _rewrite_json(s, mapping)).items():
            if n:
                links[name] = n
        for r in rows:
            r.status = RETIRED
            r.merged_into_id = target_id
            r.merged_at = now
            r.possible_duplicate_of = None
            r.parent_id = None if r.parent_id == target_id else r.parent_id
            r.updated_at = now
        await s.flush()
        await request_index(s, actor.workspace_id)
    return MergeResult(target_id, [r.id for r in rows], moved or 0, folded, len(children), links)


async def _fold_duplicates(s: AsyncSession, target_id: UUID, sources: list[UUID]) -> int:
    """옮긴 확인 전 후보 중 대상에 같은 문장이 이미 있으면 근거를 옮기고 후보를 폐기한다."""
    claims = (
        await s.scalars(
            select(Claim)
            .where(Claim.experience_id == target_id, Claim.status != ClaimStatus.RETIRED)
            .order_by(Claim.created_at, Claim.id)
        )
    ).all()
    # 확인한 사실을 먼저 남긴다 (같은 문장이면 확인한 쪽으로)
    keep: list[Claim] = sorted(
        claims, key=lambda c: (c.status not in CONFIRMED_STATUSES, c.created_at)
    )
    kept: list[Claim] = []
    folded = 0
    for c in keep:
        twin = next((k for k in kept if same_statement(k.statement, c.statement)), None)
        if twin is None or c.status != ClaimStatus.EXTRACTED:
            kept.append(c)
            continue
        have = set(
            (
                await s.scalars(
                    select(ClaimEvidence.source_span_id).where(
                        ClaimEvidence.claim_id == twin.id,
                        ClaimEvidence.source_span_id.is_not(None),
                    )
                )
            ).all()
        )
        for ev in (
            await s.scalars(select(ClaimEvidence).where(ClaimEvidence.claim_id == c.id))
        ).all():
            if ev.source_span_id is not None and ev.source_span_id in have:
                continue
            ev.claim_id = twin.id
            have.add(ev.source_span_id)
        c.status = ClaimStatus.RETIRED.value
        c.updated_at = utcnow()
        folded += 1
    await s.flush()
    return folded


# ---------------------------------------------------------------- 지우기


async def experience_references(s: AsyncSession, experience_id: UUID) -> dict[str, int]:
    """이 경험을 쓰는 곳 (지우기 전에 본다). 0인 칸은 뺀다."""
    claim_ids = select(Claim.id).where(Claim.experience_id == experience_id)
    refs: dict[str, int] = {}

    async def count(name: str, q) -> None:
        n = await s.scalar(select(func.count()).select_from(q.subquery()))
        if n:
            refs[name] = n

    await count(
        "children",
        select(Experience.id).where(
            Experience.parent_id == experience_id, Experience.status == ACTIVE
        ),
    )
    await count(
        "portfolio_items",
        select(PortfolioItem.id).where(PortfolioItem.experience_id == experience_id),
    )
    await count(
        "interview_sessions",
        select(InterviewSession.id).where(InterviewSession.experience_id == experience_id),
    )
    used = 0
    for model in (
        DocumentClaim,
        ResumeLineClaim,
        PortfolioLineClaim,
        AnswerAssetClaim,
        StudyRequirementClaim,
        StudyClaimCandidate,
    ):
        used += (
            await s.scalar(
                select(func.count()).select_from(model).where(model.claim_id.in_(claim_ids))
            )
            or 0
        )
    if used:
        refs["claims_in_use"] = used
    needle = str(experience_id)
    await count(
        "resumes",
        select(ResumeVersion.id)
        .join(
            ResumeRevision,
            (ResumeRevision.version_id == ResumeVersion.id)
            & (ResumeRevision.revision_no == ResumeVersion.current_revision),
        )
        .where(cast(ResumeRevision.content, String).contains(needle)),
    )
    await count(
        "application_preps",
        select(ApplicationPrep.id).where(cast(ApplicationPrep.choices, String).contains(needle)),
    )
    return refs


@dataclass(frozen=True, slots=True)
class DeleteResult:
    id: UUID
    claims_deleted: int
    unlinked: dict[str, int]


async def delete_experience(
    db: Database, actor: ActorContext, experience_id: UUID, *, include_confirmed: bool = False
) -> DeleteResult:
    async with db.workspace_session(actor.workspace_id) as s:
        exp = await s.get(Experience, experience_id, with_for_update=True)
        if exp is None:
            raise NotFound("experience")
        refs = await experience_references(s, experience_id)
        confirmed = await s.scalar(
            select(func.count()).where(
                Claim.experience_id == experience_id,
                Claim.status.in_([str(c) for c in CONFIRMED_STATUSES]),
            )
        )
        if confirmed and not include_confirmed:
            refs["confirmed_claims"] = confirmed
        if confirmed and not can_confirm(actor):
            raise ClaimRuleError("confirm_requires_approve")
        if refs:
            raise ExperienceInUse(refs)
        claims = await s.scalar(select(func.count()).where(Claim.experience_id == experience_id))
        unlinked: dict[str, int] = {}
        for name, model in (("credentials", Credential), ("education", Education)):
            n = (
                await s.execute(
                    update(model)
                    .where(model.experience_id == experience_id)
                    .values(experience_id=None, updated_at=utcnow())
                )
            ).rowcount
            if n:
                unlinked[name] = n
        # 보관한(합친) 아래 경험은 위 경험 연결만 푼다 (활성 아래 경험이 있으면 위에서 거절했다)
        await s.execute(
            update(Experience)
            .where(Experience.parent_id == experience_id)
            .values(parent_id=None, updated_at=utcnow())
        )
        await s.delete(exp)
        await s.flush()
        await request_index(s, actor.workspace_id)
    return DeleteResult(experience_id, claims or 0, unlinked)


async def dismiss_duplicate(db: Database, actor: ActorContext, experience_id: UUID) -> None:
    """ "같은 경험일 수 있음" 표시를 지운다 (사람이 다른 경험이라고 봤다)."""
    async with db.workspace_session(actor.workspace_id) as s:
        exp = await s.get(Experience, experience_id, with_for_update=True)
        if exp is None:
            raise NotFound("experience")
        exp.possible_duplicate_of = None
        exp.updated_at = utcnow()

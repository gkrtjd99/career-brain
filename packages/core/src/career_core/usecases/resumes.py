"""이력서 판본 (O17, plan §19.5): 브레인에서 나오는 이력서.

- 판본은 마스터 하나 → 직무별 → 공고별. 아래 판본은 위 판본의 한 revision을 복사해 시작하고 출처(판본·revision)를
  남긴다 (공용 문서 복사 O07과 같은 규칙). 위 판본이 그 뒤에 바뀌어도 아래 판본은 그대로다.
- 본문은 revision으로만 바뀐다. 저장 요청은 ``base_revision``을 가져와야 하고 최신과 다르면 ``ResumeConflict``(409).
  같은 ``idempotency_key``면 처음 만든 revision을 돌려준다. 내용이 그대로면 새 revision을 만들지 않는다.
- 항목은 브레인 기록을 고르면 채워진다(경험 → 기간·조직·역할, 학력·자격·기술·프로필). 성과 줄은 그 경험의
  **확인한 사실**에서 고른다. 직접 쓴 줄은 "근거 없음"이고 문서 검사(범용 문장·근거 없는 문장)를 그대로 받는다.
- 모델이 글을 먼저 쓰지 않는다. 줄 다듬기(``suggest_lines``)는 문서 수정안과 같은 지시문·같은 사실 대조로,
  사람이 요청할 때만이다. 수정안은 저장하지 않고 돌려주며, 사람이 적용하면 그 줄은 ``origin = ai``.
- 블라인드 판본은 미리보기와 모든 내보내기에서 사진·생년월일·주소·학교 이름(``domain.blind``)을 뺀다.
- 프로필 값은 restricted다: MCP용 보기(``agent_view``)는 블라인드와 상관없이 연락처·생년월일·주소·사진을 주지 않는다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from career_core.db import Claim, Database, Experience, JobPosting, JobPostingVersion
from career_core.db.models import (
    Application,
    ApplicationDocument,
    ApplicationPrep,
    Credential,
    Education,
    Profile,
    ProfilePhoto,
    ResumeLineClaim,
    ResumeRevision,
    ResumeVersion,
)
from career_core.domain.brain import CONFIRMED_STATUSES
from career_core.domain.document_export import ExportError, export_summary, inspect_export
from career_core.domain.documents import BlockInput, Finding, check_document
from career_core.domain.experience_match import SAME, ExperienceKey, best_match, same_statement
from career_core.domain.resume import (
    CONTACT_FIELDS,
    CREDENTIAL_SECTION,
    EXPERIENCE_SECTION,
    MAX_TARGET_ROLE,
    PARENT_KINDS,
    REQUIRED_SECTIONS,
    SECTION_TITLE,
    TEMPLATES,
    VERSION_KINDS,
    ClaimInfo,
    Lookup,
    RenderDoc,
    ResumeConflict,
    ResumeRuleError,
    apply_operations,
    birth_strings,
    blind_words,
    build_render,
    check_content,
    clean_optional,
    clean_title,
    content_hash,
    credential_fields,
    education_fields,
    empty_content,
    estimate_pages,
    experience_fields,
    item_state,
    iter_items,
    iter_lines,
    line_state,
    mentions_blind,
    new_id,
    operation_refs,
    profile_fields,
    skill_fields,
)
from career_core.domain.resume_export import (
    RESUME_FORMATS,
    RESUME_MEDIA_TYPES,
    render_resume,
    resume_filename,
)
from career_core.domain.sensitive import sensitive_findings
from career_core.ports import ActorContext, BlobStore
from career_core.usecases import applications as app_uc
from career_core.usecases.brain import NotFound
from career_core.usecases.documents import ExportResult

ACTIVE = "active"
MAX_SKILLS_FILL = 15
MAX_CLAIM_LINES_FILL = 5
# 한 번에 채우기에서 성과 줄로 넣는 사실 종류 (사용한 기술·어려웠던 점·기타는 넣지 않는다)
FILL_CLAIM_KINDS = ("result", "action", "responsibility", "decision")
MAX_SUGGEST_LINES = 20
MAX_INSTRUCTION_CHARS = 500


# ---------------------------------------------------------------- 보기


@dataclass(frozen=True, slots=True)
class VersionSummary:
    id: UUID
    kind: str
    title: str
    target_role: str | None
    parent_id: UUID | None
    parent_title: str | None
    # 복사해 온 위 판본 revision 번호와 위 판본의 지금 revision (다르면 위 판본이 그 뒤에 바뀌었다)
    source_revision_no: int | None
    parent_revision: int | None
    application_id: UUID | None
    application_title: str | None
    template: str
    blind: bool
    max_pages: int
    current_revision: int
    items: int
    lines: int
    unsupported_lines: int
    children: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class VersionView:
    summary: VersionSummary
    revision_no: int
    revision_id: UUID | None
    # 구역 목록 (항목에 ``state``, 줄에 ``state``를 붙인 복사본)
    content: dict[str, Any]
    # 마스터까지의 계보 [{id, kind, title}] (위에서부터)
    lineage: list[dict[str, Any]]
    blind_suggested: bool
    # 공고별 판본: 지원 준비에서 고른 경험이 있다
    prep_choices: int
    revision_origin: str | None = None


@dataclass(frozen=True, slots=True)
class RevisionResult:
    id: UUID
    revision_no: int
    created: bool


@dataclass(frozen=True, slots=True)
class RevisionSummary:
    id: UUID
    revision_no: int
    origin: str
    reason: str | None
    content_hash: str
    created_by: UUID
    created_at: datetime


@dataclass(slots=True)
class _Brain:
    lookup: Lookup = field(default_factory=Lookup)
    # 프로필 원래 값 (연락처 비교, 블라인드 가리기)
    profile: dict[str, Any] | None = None


# ---------------------------------------------------------------- 브레인 값 읽기


def _row(obj: Any, names: Sequence[str]) -> dict[str, Any]:
    return {n: getattr(obj, n) for n in names}


_EXP_COLS = (
    "title",
    "organization",
    "role",
    "kind",
    "started_on",
    "ended_on",
    "ongoing",
    "period_text",
    "tags",
)
_EDU_COLS = (
    "school",
    "campus",
    "major",
    "extra_major",
    "extra_major_kind",
    "degree",
    "status",
    "transferred",
    "entered_on",
    "graduated_on",
    "gpa",
    "gpa_scale",
)
_CRED_COLS = (
    "kind",
    "name",
    "issuer",
    "acquired_on",
    "expires_on",
    "started_on",
    "ended_on",
    "score",
    "hours",
)
_PROFILE_COLS = (
    "name",
    "name_en",
    "headline",
    "email",
    "phone",
    "address",
    "links",
    "birth_date",
    "military_status",
    "military_branch",
    "military_rank",
    "military_started_on",
    "military_ended_on",
)


def _uuids(values: Sequence[str]) -> list[UUID]:
    out = []
    for v in values:
        try:
            out.append(UUID(str(v)))
        except ValueError:
            continue
    return out


async def _profile(s: AsyncSession) -> tuple[Profile | None, str | None]:
    profile = await s.scalar(select(Profile))
    photos = (
        await s.scalars(
            select(ProfilePhoto).order_by(ProfilePhoto.created_at.desc(), ProfilePhoto.id.desc())
        )
    ).all()
    ids = {p.id for p in photos}
    default = None
    if profile is not None and profile.default_photo_id in ids:
        default = profile.default_photo_id
    elif photos:
        default = photos[0].id
    return profile, str(default) if default else None


async def _skills(db: Database, actor: ActorContext) -> dict[str, str]:
    from career_core.usecases import skills as skills_uc

    return {v.key: v.name for v in await skills_uc.skills_overview(db, actor)}


async def _load(
    s: AsyncSession,
    records: set[tuple[str, str]],
    claims: set[str],
    *,
    skills: dict[str, str] | None = None,
) -> _Brain:
    """기록·주장의 지금 값. RLS 때문에 다른 공간의 것은 보이지 않는다 (= 없는 기록)."""
    brain = _Brain()
    lk = brain.lookup
    by_type: dict[str, list[str]] = {}
    for t, i in records:
        by_type.setdefault(t, []).append(i)
    # c0050: 합친 경험(보관, merged_into_id)은 대상 경험으로 읽는다 (revision은 고치지 않는 표다)
    merged: dict[str, str] = {}
    if ids := _uuids(by_type.get("experience", [])):
        rows = (await s.scalars(select(Experience).where(Experience.id.in_(ids)))).all()
        for e in rows:
            if e.status != ACTIVE and e.merged_into_id is not None:
                merged[str(e.id)] = str(e.merged_into_id)
        targets = {
            e.id: e
            for e in (
                await s.scalars(
                    select(Experience).where(
                        Experience.id.in_(
                            [e.id for e in rows] + _uuids(sorted(set(merged.values())))
                        ),
                        Experience.status == ACTIVE,
                    )
                )
            ).all()
        }
        for ref_id in ids:
            e = targets.get(UUID(merged.get(str(ref_id), str(ref_id))))
            if e is not None:
                lk.records[("experience", str(ref_id))] = experience_fields(_row(e, _EXP_COLS))
    if ids := _uuids(by_type.get("education", [])):
        for e in (await s.scalars(select(Education).where(Education.id.in_(ids)))).all():
            lk.records[("education", str(e.id))] = education_fields(_row(e, _EDU_COLS))
    if ids := _uuids(by_type.get("credential", [])):
        for c in (
            await s.scalars(
                select(Credential).where(Credential.id.in_(ids), Credential.status != "extracted")
            )
        ).all():
            lk.records[("credential", str(c.id))] = credential_fields(_row(c, _CRED_COLS))
    for key in by_type.get("skill", []):
        if skills is not None and key in skills:
            lk.records[("skill", key)] = skill_fields(skills[key])
    profile, photo_id = await _profile(s)
    if profile is not None:
        raw = _row(profile, _PROFILE_COLS)
        brain.profile = raw
        lk.records[("profile", str(profile.id))] = profile_fields(raw, photo_id)
    # 경험 → 그 경험과 바로 아래 경험 (성과 줄은 이 안의 사실에서)
    exp_ids = _uuids(by_type.get("experience", []))
    if exp_ids:
        owner: dict[str, list[str]] = {}
        for e in exp_ids:
            lk.family[str(e)] = {str(e)}
            real = merged.get(str(e), str(e))
            lk.family[str(e)].add(real)
            owner.setdefault(real, []).append(str(e))
        for child, parent in (
            await s.execute(
                select(Experience.id, Experience.parent_id).where(
                    Experience.parent_id.in_(_uuids(sorted(owner)))
                )
            )
        ).all():
            for ref_id in owner.get(str(parent), []):
                lk.family[ref_id].add(str(child))
    if cids := _uuids(sorted(claims)):
        for c, e in (
            await s.execute(
                select(Claim, Experience)
                .join(Experience, Experience.id == Claim.experience_id)
                .where(Claim.id.in_(cids))
            )
        ).all():
            lk.claims[str(c.id)] = ClaimInfo(
                id=str(c.id),
                statement=c.statement,
                confirmed=c.status in CONFIRMED_STATUSES and e.status == ACTIVE,
                experience_id=str(c.experience_id),
            )
    return brain


def _needs_skills(records: set[tuple[str, str]]) -> bool:
    return any(t == "skill" for t, _ in records)


async def _brain_for(
    db: Database,
    actor: ActorContext,
    records: set[tuple[str, str]],
    claims: set[str],
) -> _Brain:
    skills = await _skills(db, actor) if _needs_skills(records) else None
    async with db.workspace_session(actor.workspace_id) as s:
        return await _load(s, records, claims, skills=skills)


# ---------------------------------------------------------------- 공통


async def _version(s: AsyncSession, version_id: UUID, *, lock: bool = False) -> ResumeVersion:
    q = select(ResumeVersion).where(ResumeVersion.id == version_id)
    if lock:
        q = q.with_for_update()
    v = await s.scalar(q)
    if v is None:
        raise NotFound("resume")
    return v


async def _revision(s: AsyncSession, version_id: UUID, no: int) -> ResumeRevision | None:
    if no <= 0:
        return None
    return await s.scalar(
        select(ResumeRevision).where(
            ResumeRevision.version_id == version_id, ResumeRevision.revision_no == no
        )
    )


async def _content(
    s: AsyncSession, v: ResumeVersion, revision: int | None = None
) -> tuple[ResumeRevision | None, dict[str, Any]]:
    no = v.current_revision if revision is None else revision
    rev = await _revision(s, v.id, no)
    if rev is None and no != 0:
        raise NotFound("revision")
    return rev, (rev.content if rev else empty_content())


async def _insert_revision(
    s: AsyncSession,
    actor: ActorContext,
    v: ResumeVersion,
    content: dict[str, Any],
    *,
    origin: str,
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> ResumeRevision:
    rev = ResumeRevision(
        id=uuid4(),
        workspace_id=actor.workspace_id,
        version_id=v.id,
        revision_no=v.current_revision + 1,
        content=content,
        content_hash=content_hash(content),
        origin=origin,
        reason=(reason or "").strip()[:1000] or None,
        idempotency_key=idempotency_key,
        created_by=actor.subject_id,
    )
    s.add(rev)
    await s.flush()
    for _, _, ln in iter_lines(content):
        if ln.get("claim_id"):
            s.add(
                ResumeLineClaim(
                    id=uuid4(),
                    workspace_id=actor.workspace_id,
                    revision_id=rev.id,
                    line_id=ln["id"],
                    claim_id=UUID(ln["claim_id"]),
                )
            )
    v.current_revision = rev.revision_no
    await s.flush()
    return rev


def _counts(content: dict[str, Any]) -> tuple[int, int, int]:
    items = sum(1 for _ in iter_items(content))
    lines = [ln for _, _, ln in iter_lines(content)]
    return items, len(lines), sum(1 for ln in lines if not ln.get("claim_id"))


async def _summaries(s: AsyncSession, rows: Sequence[ResumeVersion]) -> list[VersionSummary]:
    ids = [v.id for v in rows]
    by_id = {v.id: v for v in rows}
    parents = {v.parent_id for v in rows if v.parent_id and v.parent_id not in by_id}
    if parents:
        for p in (
            await s.scalars(select(ResumeVersion).where(ResumeVersion.id.in_(parents)))
        ).all():
            by_id[p.id] = p
    children: dict[UUID, int] = {}
    if ids:
        for pid, n in (
            await s.execute(
                select(ResumeVersion.parent_id, func.count())
                .where(ResumeVersion.parent_id.in_(ids))
                .group_by(ResumeVersion.parent_id)
            )
        ).all():
            children[pid] = n
    src_ids = [v.source_revision_id for v in rows if v.source_revision_id]
    src_no: dict[UUID, int] = {}
    if src_ids:
        for rid, no in (
            await s.execute(
                select(ResumeRevision.id, ResumeRevision.revision_no).where(
                    ResumeRevision.id.in_(src_ids)
                )
            )
        ).all():
            src_no[rid] = no
    app_ids = [v.application_id for v in rows if v.application_id]
    apps: dict[UUID, str] = {}
    if app_ids:
        for aid, title in (
            await s.execute(
                select(Application.id, Application.title).where(Application.id.in_(app_ids))
            )
        ).all():
            apps[aid] = title
    current: dict[tuple[UUID, int], dict] = {}
    pairs = [(v.id, v.current_revision) for v in rows if v.current_revision]
    if pairs:
        for r in (
            await s.scalars(
                select(ResumeRevision).where(ResumeRevision.version_id.in_([p[0] for p in pairs]))
            )
        ).all():
            if (r.version_id, r.revision_no) in set(pairs):
                current[(r.version_id, r.revision_no)] = r.content
    out = []
    for v in rows:
        content = current.get((v.id, v.current_revision)) or empty_content()
        items, lines, unsupported = _counts(content)
        parent = by_id.get(v.parent_id) if v.parent_id else None
        out.append(
            VersionSummary(
                id=v.id,
                kind=v.kind,
                title=v.title,
                target_role=v.target_role,
                parent_id=v.parent_id,
                parent_title=parent.title if parent else None,
                source_revision_no=src_no.get(v.source_revision_id)
                if v.source_revision_id
                else None,
                parent_revision=parent.current_revision if parent else None,
                application_id=v.application_id,
                application_title=apps.get(v.application_id) if v.application_id else None,
                template=v.template,
                blind=v.blind,
                max_pages=v.max_pages,
                current_revision=v.current_revision,
                items=items,
                lines=lines,
                unsupported_lines=unsupported,
                children=children.get(v.id, 0),
                created_at=v.created_at,
                updated_at=v.updated_at,
            )
        )
    return out


_KIND_ORDER = {k: n for n, k in enumerate(VERSION_KINDS)}


async def list_versions(
    db: Database, actor: ActorContext, *, application_id: UUID | None = None
) -> list[VersionSummary]:
    """판본 목록: 마스터 → 직무별 → 공고별, 같은 종류 안에서는 최근에 고친 순."""
    async with db.workspace_session(actor.workspace_id) as s:
        q = select(ResumeVersion)
        if application_id is not None:
            q = q.where(ResumeVersion.application_id == application_id)
        rows = (await s.scalars(q.order_by(ResumeVersion.updated_at.desc()))).all()
        out = await _summaries(s, rows)
    return sorted(out, key=lambda v: _KIND_ORDER.get(v.kind, 9))


async def _lineage(s: AsyncSession, v: ResumeVersion) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    seen = {v.id}
    cur = v
    while cur.parent_id and cur.parent_id not in seen and len(chain) < 5:
        p = await s.get(ResumeVersion, cur.parent_id)
        if p is None:
            break
        seen.add(p.id)
        chain.insert(0, {"id": str(p.id), "kind": p.kind, "title": p.title})
        cur = p
    return chain


async def _posting_mentions_blind(s: AsyncSession, application_id: UUID | None) -> bool:
    """이은 공고의 항목·문항·본문에 "블라인드"가 보이면 켜자고 제안한다."""
    if application_id is None:
        return False
    app = await s.get(Application, application_id)
    if app is None:
        return False
    posting = await s.get(JobPosting, app.posting_id)
    if posting is not None and mentions_blind(json.dumps(posting.fields or {}, ensure_ascii=False)):
        return True
    text = await s.scalar(
        select(JobPostingVersion.text)
        .where(JobPostingVersion.posting_id == app.posting_id)
        .order_by(JobPostingVersion.version_no.desc())
        .limit(1)
    )
    if mentions_blind(text):
        return True
    prompts = (
        await s.scalars(
            select(ApplicationDocument.question_prompt).where(
                ApplicationDocument.application_id == application_id
            )
        )
    ).all()
    return any(mentions_blind(p) for p in prompts)


def _annotate(content: dict[str, Any], brain: _Brain) -> dict[str, Any]:
    out = json.loads(json.dumps(content))
    lk = brain.lookup
    for _, it in iter_items(out):
        ref = it.get("ref")
        it["state"] = item_state(it, lk.records.get((ref["type"], ref["id"])) if ref else None)
        for ln in it.get("lines") or []:
            ln["state"] = line_state(ln, lk.claims.get(ln.get("claim_id") or ""))
    return out


def _refs_of(content: dict[str, Any]) -> tuple[set[tuple[str, str]], set[str]]:
    return operation_refs(content, [])


async def _prep_choice_order(
    s: AsyncSession, application_id: UUID | None
) -> list[tuple[str, list[str]]]:
    """지원 준비에서 사람이 고른 경험: 문항 순서 → 고른 순서. [(경험 ID, 고른 사실 ID들)]"""
    if application_id is None:
        return []
    row = await s.scalar(
        select(ApplicationPrep).where(ApplicationPrep.application_id == application_id)
    )
    if row is None or not row.choices:
        return []
    docs = (
        await s.scalars(
            select(ApplicationDocument)
            .where(ApplicationDocument.application_id == application_id)
            .order_by(
                ApplicationDocument.question_index.asc().nulls_last(),
                ApplicationDocument.created_at,
            )
        )
    ).all()
    order: dict[str, list[str]] = {}
    for d in docs:
        items = sorted(row.choices.get(str(d.id)) or [], key=lambda c: c.get("chosen_at") or "")
        for c in items:
            claims = order.setdefault(c["experience_id"], [])
            claims.extend(x for x in c.get("claim_ids") or [] if x not in claims)
    return list(order.items())


async def get_version(
    db: Database, actor: ActorContext, version_id: UUID, *, revision: int | None = None
) -> VersionView:
    async with db.workspace_session(actor.workspace_id) as s:
        v = await _version(s, version_id)
        rev, content = await _content(s, v, revision)
        summary = (await _summaries(s, [v]))[0]
        lineage = await _lineage(s, v)
        blind_hint = (not v.blind) and await _posting_mentions_blind(s, v.application_id)
        choices = len(await _prep_choice_order(s, v.application_id))
    records, claims = _refs_of(content)
    brain = await _brain_for(db, actor, records, claims)
    return VersionView(
        summary=summary,
        revision_no=rev.revision_no if rev else 0,
        revision_id=rev.id if rev else None,
        content=_annotate(content, brain),
        lineage=lineage,
        blind_suggested=blind_hint,
        prep_choices=choices,
        revision_origin=rev.origin if rev else None,
    )


async def list_revisions(
    db: Database, actor: ActorContext, version_id: UUID
) -> list[RevisionSummary]:
    async with db.workspace_session(actor.workspace_id) as s:
        await _version(s, version_id)
        rows = (
            await s.scalars(
                select(ResumeRevision)
                .where(ResumeRevision.version_id == version_id)
                .order_by(ResumeRevision.revision_no.desc())
            )
        ).all()
    return [
        RevisionSummary(
            r.id, r.revision_no, r.origin, r.reason, r.content_hash, r.created_by, r.created_at
        )
        for r in rows
    ]


# ---------------------------------------------------------------- 만들기·설정·지우기


def _template(value: Any) -> str:
    if value not in TEMPLATES:
        raise ResumeRuleError("invalid_template")
    return value


def _max_pages(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 10:
        raise ResumeRuleError("invalid_max_pages")
    return value


def _origin(actor: ActorContext) -> tuple[str, str]:
    """(revision origin, 줄 origin): 사람은 user, 에이전트(MCP)는 agent·ai."""
    return ("user", "user") if actor.is_human else ("agent", "ai")


async def _check_application(s: AsyncSession, application_id: UUID) -> Application:
    app = await s.get(Application, application_id)
    if app is None:
        raise NotFound("application")
    return app


async def create_version(
    db: Database,
    actor: ActorContext,
    *,
    kind: str,
    title: str | None = None,
    parent_id: UUID | None = None,
    parent_revision: int | None = None,
    target_role: str | None = None,
    application_id: UUID | None = None,
    template: str | None = None,
    fill: bool = False,
    fill_claims: bool = False,
) -> UUID:
    """판본을 만든다. 마스터는 공간에 하나(``master_exists``), 빈 구역 또는 브레인에서 한 번에 채운 것으로 시작.

    직무별·공고별은 위 판본(``parent_id``, 직무별의 위는 마스터, 공고별의 위는 마스터나 직무별)의 revision(기본 최신)을
    revision 1로 복사한다 — 항목·줄·근거 연결·채울 때의 값 복사본·템플릿·블라인드가 그대로 온다.
    """
    if kind not in VERSION_KINDS:
        raise ResumeRuleError("invalid_kind")
    role = clean_optional(target_role, MAX_TARGET_ROLE, "invalid_target_role")
    if application_id is not None and kind != "posting":
        raise ResumeRuleError("application_needs_posting")
    async with db.workspace_session(actor.workspace_id) as s:
        if application_id is not None:
            app = await _check_application(s, application_id)
            title = title or f"{app.title} 이력서"
        if kind == "master":
            if parent_id is not None:
                raise ResumeRuleError("master_has_no_parent")
            # 마스터는 공간에 하나. 동시에 만들기는 공간별 트랜잭션 잠금으로 줄 세운다 (DB 유일 색인을 두지 않는 것은
            # 묶음 합치기(restore)가 마스터 두 개를 들여올 수 있게 하기 위해서다 — 화면은 둘 다 보인다)
            await s.execute(
                sql_text("select pg_advisory_xact_lock(hashtext(:k))"),
                {"k": f"resume-master:{actor.workspace_id}"},
            )
            if await s.scalar(
                select(func.count())
                .select_from(ResumeVersion)
                .where(ResumeVersion.kind == "master")
            ):
                raise ResumeRuleError("master_exists")
            v = ResumeVersion(
                id=uuid4(),
                workspace_id=actor.workspace_id,
                kind="master",
                title=clean_title(title or "마스터 이력서"),
                target_role=role,
                template=_template(template or "one_column"),
                created_by=actor.subject_id,
            )
            s.add(v)
            await s.flush()
            version_id = v.id
        else:
            if parent_id is None:
                raise ResumeRuleError("parent_required")
            parent = await _version(s, parent_id)
            if parent.kind not in PARENT_KINDS[kind]:
                raise ResumeRuleError("invalid_parent")
            no = parent.current_revision if parent_revision is None else parent_revision
            src = await _revision(s, parent.id, no)
            if src is None and no != 0:
                raise NotFound("revision")
            v = ResumeVersion(
                id=uuid4(),
                workspace_id=actor.workspace_id,
                kind=kind,
                title=clean_title(title or f"{parent.title} 사본"),
                target_role=role if role is not None else parent.target_role,
                parent_id=parent.id,
                source_revision_id=src.id if src else None,
                application_id=application_id,
                template=_template(template or parent.template),
                blind=parent.blind,
                max_pages=parent.max_pages,
                created_by=actor.subject_id,
            )
            s.add(v)
            await s.flush()
            content = check_content(json.loads(json.dumps(src.content if src else empty_content())))
            await _insert_revision(
                s,
                actor,
                v,
                content,
                origin="copy",
                reason=f"'{parent.title}' r{no}에서 복사"
                if src
                else f"'{parent.title}'(빈 판본)에서 시작",
            )
            version_id = v.id
    if kind == "master" and fill:
        await save(
            db,
            actor,
            version_id,
            base_revision=0,
            operations=[{"op": "fill_all", "claims": fill_claims}],
            reason="브레인에서 한 번에 채움",
        )
    return version_id


async def update_settings(
    db: Database, actor: ActorContext, version_id: UUID, changes: dict[str, Any]
) -> None:
    """판본 설정(제목·직무·템플릿·블라인드·쪽 수 한도·이은 지원)만 바꾼다. 본문은 revision으로만 바뀐다."""
    unknown = set(changes) - {
        "title",
        "target_role",
        "template",
        "blind",
        "max_pages",
        "application_id",
    }
    if unknown:
        raise ResumeRuleError("unknown_field")
    async with db.workspace_session(actor.workspace_id) as s:
        v = await _version(s, version_id, lock=True)
        if "title" in changes:
            v.title = clean_title(changes["title"])
        if "target_role" in changes:
            v.target_role = clean_optional(
                changes["target_role"], MAX_TARGET_ROLE, "invalid_target_role"
            )
        if "template" in changes:
            v.template = _template(changes["template"])
        if "blind" in changes:
            if not isinstance(changes["blind"], bool):
                raise ResumeRuleError("invalid_blind")
            v.blind = changes["blind"]
        if "max_pages" in changes:
            v.max_pages = _max_pages(changes["max_pages"])
        if "application_id" in changes:
            aid = changes["application_id"]
            if aid is not None:
                if v.kind != "posting":
                    raise ResumeRuleError("application_needs_posting")
                await _check_application(s, UUID(str(aid)))
                v.application_id = UUID(str(aid))
            else:
                v.application_id = None


async def delete_version(db: Database, actor: ActorContext, version_id: UUID) -> None:
    """판본을 지운다. 아래 판본이 있으면 지우지 않는다(계보 유지, ``version_has_children``)."""
    async with db.workspace_session(actor.workspace_id) as s:
        v = await _version(s, version_id, lock=True)
        n = await s.scalar(
            select(func.count()).select_from(ResumeVersion).where(ResumeVersion.parent_id == v.id)
        )
        if n:
            raise ResumeRuleError("version_has_children")
        await s.delete(v)


# ---------------------------------------------------------------- 저장 (수정 연산)


def _exp_key(e: Experience) -> ExperienceKey:
    return ExperienceKey(
        e.id, e.title, e.organization, e.role, e.started_on, e.ended_on, bool(e.ongoing)
    )


async def _fill_all_ops(
    db: Database, actor: ActorContext, content: dict[str, Any], *, with_claims: bool
) -> list[dict[str, Any]]:
    """브레인에서 한 번에 채우기: 프로필·활성 경험·학력·확인한 자격·기술(많이 쓴 순 15개) 중 아직 없는 것.
    ``with_claims``면 경험마다 확인한 사실(결과·한 일·맡은 일·결정) 5개까지 성과 줄로 넣는다."""
    have = {(it["ref"]["type"], it["ref"]["id"]) for _, it in iter_items(content) if it.get("ref")}
    has_profile = any(s["kind"] == "profile" and s["items"] for s in content["sections"])
    skills = await _skills(db, actor)
    ops: list[dict[str, Any]] = []
    async with db.workspace_session(actor.workspace_id) as s:
        profile = await s.scalar(select(Profile))
        if profile is not None and not has_profile:
            ops.append(
                {
                    "op": "add_item",
                    "section": "profile",
                    "ref": {"type": "profile", "id": str(profile.id)},
                }
            )
        exps = (
            await s.scalars(
                select(Experience)
                .where(Experience.status == ACTIVE)
                .order_by(
                    Experience.ongoing.desc(),
                    func.coalesce(Experience.ended_on, Experience.started_on).desc().nulls_last(),
                    Experience.created_at.desc(),
                )
            )
        ).all()
        # 합친 경험을 가리키는 항목은 합쳐 받은 경험이 이미 있는 것으로 본다.
        old_refs = [UUID(i) for t, i in have if t == "experience"]
        if old_refs:
            for old, target in (
                await s.execute(
                    select(Experience.id, Experience.merged_into_id).where(
                        Experience.id.in_(old_refs), Experience.merged_into_id.is_not(None)
                    )
                )
            ).all():
                have.add(("experience", str(target)))
        claims_by_exp: dict[UUID, list[str]] = {}
        if with_claims and exps:
            seen: list[str] = []  # 같은 문장이 두 경험에 있으면 한 번만 넣는다
            for c in (
                await s.scalars(
                    select(Claim)
                    .where(
                        Claim.experience_id.in_([e.id for e in exps]),
                        Claim.status.in_([str(x) for x in CONFIRMED_STATUSES]),
                        Claim.kind.in_(FILL_CLAIM_KINDS),
                    )
                    .order_by(Claim.created_at)
                )
            ).all():
                if any(same_statement(c.statement, x) for x in seen):
                    continue
                seen.append(c.statement)
                claims_by_exp.setdefault(c.experience_id, []).append(str(c.id))
        placed = [_exp_key(e) for e in exps if ("experience", str(e.id)) in have]
        placed_ids = {k.id for k in placed}
        for e in exps:
            if ("experience", str(e.id)) in have:
                continue
            key = _exp_key(e)
            # 같은 경험일 수 있다고 표시된 짝이 이미 들어갔거나, 이름·곳·기간이 거의 같으면 건너뛴다.
            if e.possible_duplicate_of in placed_ids:
                continue
            if best_match(key, placed)[0] == SAME:
                continue
            placed.append(key)
            placed_ids.add(e.id)
            op: dict[str, Any] = {
                "op": "add_item",
                "section": EXPERIENCE_SECTION.get(e.kind, "activities"),
                "ref": {"type": "experience", "id": str(e.id)},
            }
            if claims_by_exp.get(e.id):
                op["claim_ids"] = claims_by_exp[e.id][:MAX_CLAIM_LINES_FILL]
            ops.append(op)
        for ed in (
            await s.scalars(
                select(Education).order_by(
                    Education.graduated_on.desc().nulls_first(),
                    Education.entered_on.desc().nulls_last(),
                )
            )
        ).all():
            if ("education", str(ed.id)) not in have:
                ops.append(
                    {
                        "op": "add_item",
                        "section": "education",
                        "ref": {"type": "education", "id": str(ed.id)},
                    }
                )
        for c in (
            await s.scalars(
                select(Credential)
                .where(Credential.status != "extracted", Credential.kind != "degree")
                .order_by(Credential.acquired_on.desc().nulls_last(), Credential.created_at.desc())
            )
        ).all():
            if ("credential", str(c.id)) not in have:
                ops.append(
                    {
                        "op": "add_item",
                        "section": CREDENTIAL_SECTION.get(c.kind, "credentials"),
                        "ref": {"type": "credential", "id": str(c.id)},
                    }
                )
    for key in list(skills)[:MAX_SKILLS_FILL]:
        if ("skill", key) not in have:
            ops.append({"op": "add_item", "section": "skills", "ref": {"type": "skill", "id": key}})
    return ops


async def _prep_ops(
    db: Database, actor: ActorContext, v: ResumeVersion, content: dict[str, Any]
) -> list[dict[str, Any]]:
    """지원 준비에서 고른 경험 순서: 없는 경험은 고른 사실을 줄로 해서 더하고, 구역마다 고른 순서로 앞에 둔다."""
    if v.application_id is None:
        raise ResumeRuleError("no_application")
    async with db.workspace_session(actor.workspace_id) as s:
        order = await _prep_choice_order(s, v.application_id)
        if not order:
            raise ResumeRuleError("prep_not_chosen")
        kinds = dict(
            (
                await s.execute(
                    select(Experience.id, Experience.kind).where(
                        Experience.id.in_(_uuids([e for e, _ in order])),
                        Experience.status == ACTIVE,
                    )
                )
            ).all()
        )
        confirmed = {
            str(i)
            for i in (
                await s.scalars(
                    select(Claim.id).where(
                        Claim.id.in_(_uuids([c for _, cs in order for c in cs])),
                        Claim.status.in_([str(x) for x in CONFIRMED_STATUSES]),
                    )
                )
            ).all()
        }
    present: dict[str, tuple[str, str]] = {}
    for sec, it in iter_items(content):
        if it.get("ref") and it["ref"]["type"] == "experience":
            present[it["ref"]["id"]] = (sec["kind"], it["id"])
    ops: list[dict[str, Any]] = []
    per_section: dict[str, list[str]] = {}
    for exp_id, claim_ids in order:
        kind = kinds.get(UUID(exp_id))
        if kind is None:
            continue
        if exp_id in present:
            sec_kind, item_id = present[exp_id]
        else:
            sec_kind, item_id = EXPERIENCE_SECTION.get(kind, "activities"), new_id("i")
            ops.append(
                {
                    "op": "add_item",
                    "section": sec_kind,
                    "item_id": item_id,
                    "ref": {"type": "experience", "id": exp_id},
                    "claim_ids": [c for c in claim_ids if c in confirmed][:MAX_CLAIM_LINES_FILL],
                }
            )
        per_section.setdefault(sec_kind, []).append(item_id)
    for sec_kind, ids in per_section.items():
        ops.append({"op": "order_items", "section": sec_kind, "item_ids": ids})
    return ops


async def save(
    db: Database,
    actor: ActorContext,
    version_id: UUID,
    *,
    base_revision: int,
    operations: list[dict[str, Any]],
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> RevisionResult:
    """수정 연산(``domain.resume.apply_operations``)을 적용해 새 revision을 만든다.

    더해 쓰는 연산: ``fill_all {claims?}``(브레인에서 한 번에 채우기), ``apply_prep_order``(공고별 판본: 지원 준비에서
    고른 경험 순서). 둘은 기본 연산들로 풀어 같은 revision에 넣는다.
    """
    key = (idempotency_key or "").strip()[:200] or None
    rev_origin, line_origin = _origin(actor)
    if not isinstance(operations, list) or not operations:
        raise ResumeRuleError("no_operations")
    async with db.workspace_session(actor.workspace_id) as s:
        v = await _version(s, version_id)
        if key:
            done = await s.scalar(
                select(ResumeRevision).where(
                    ResumeRevision.version_id == v.id, ResumeRevision.idempotency_key == key
                )
            )
            if done is not None:
                return RevisionResult(done.id, done.revision_no, False)
        if base_revision != v.current_revision:
            raise ResumeConflict(v.current_revision)
        _, content = await _content(s, v)
        snapshot = (v.current_revision, v.application_id)
    expanded: list[dict[str, Any]] = []
    for op in operations:
        if isinstance(op, dict) and op.get("op") == "fill_all":
            expanded.extend(
                await _fill_all_ops(db, actor, content, with_claims=bool(op.get("claims")))
            )
        elif isinstance(op, dict) and op.get("op") == "apply_prep_order":
            expanded.extend(await _prep_ops(db, actor, v, content))
        else:
            expanded.append(op)
    if not expanded:
        # 한 번에 채우기에서 더할 것이 없다
        async with db.workspace_session(actor.workspace_id) as s:
            rev = await _revision(s, v.id, v.current_revision)
        if rev is None:
            raise ResumeRuleError("nothing_to_fill")
        return RevisionResult(rev.id, rev.revision_no, False)
    records, claims = operation_refs(content, expanded)
    brain = await _brain_for(db, actor, records, claims)
    new = apply_operations(content, expanded, brain.lookup, line_origin=line_origin)
    async with db.workspace_session(actor.workspace_id) as s:
        v = await _version(s, version_id, lock=True)
        if (v.current_revision, v.application_id) != snapshot:
            raise ResumeConflict(v.current_revision)
        current = await _revision(s, v.id, v.current_revision)
        if current is not None and content_hash(new) == current.content_hash:
            return RevisionResult(current.id, current.revision_no, False)
        rev = await _insert_revision(
            s, actor, v, new, origin=rev_origin, reason=reason, idempotency_key=key
        )
        return RevisionResult(rev.id, rev.revision_no, True)


# ---------------------------------------------------------------- 출력 모양·검사


async def _blind_names(s: AsyncSession, content: dict[str, Any], brain: _Brain) -> list[str]:
    """블라인드 판본에서 줄 글을 가릴 이름: 학교·캠퍼스(학력 기록과 판본의 복사본), 학력 자격 이름·기관, 생년월일."""
    names: set[str] = set()
    for school, campus in (await s.execute(select(Education.school, Education.campus))).all():
        names.update(x for x in (school, campus) if x)
    for name, issuer in (
        await s.execute(
            select(Credential.name, Credential.issuer).where(Credential.kind == "degree")
        )
    ).all():
        names.update(x for x in (name, issuer) if x)
    for _, it in iter_items(content):
        f = it.get("fields") or {}
        if it.get("ref") and it["ref"]["type"] == "education":
            names.update(x for x in (f.get("school"), f.get("campus")) if x)
        if it.get("ref") and it["ref"]["type"] == "profile":
            names.update(birth_strings(f.get("birth_date")))
    if brain.profile and brain.profile.get("birth_date"):
        names.update(birth_strings(brain.profile["birth_date"].isoformat()))
    return sorted(names)


@dataclass(slots=True)
class _Prepared:
    version: ResumeVersion
    revision_no: int
    content: dict[str, Any]
    brain: _Brain
    doc: RenderDoc
    masked: int
    blind_names: list[str]
    app: Application | None
    photos: dict[str, tuple[str, bytes]]


async def _prepare(
    db: Database,
    actor: ActorContext,
    version_id: UUID,
    *,
    revision: int | None,
    blobs: BlobStore | None = None,
    blind: bool | None = None,
) -> _Prepared:
    async with db.workspace_session(actor.workspace_id) as s:
        v = await _version(s, version_id)
        rev, content = await _content(s, v, revision)
        app = await s.get(Application, v.application_id) if v.application_id else None
        records, claims = _refs_of(content)
    brain = await _brain_for(db, actor, records, claims)
    use_blind = v.blind if blind is None else blind
    photos: dict[str, tuple[str, bytes]] = {}
    async with db.workspace_session(actor.workspace_id) as s:
        names = await _blind_names(s, content, brain) if use_blind else []
        wanted = {
            (it.get("fields") or {}).get("photo_id")
            for sec, it in iter_items(content)
            if sec["kind"] == "profile"
        } - {None}
        rows = []
        if blobs is not None and wanted and not use_blind:
            rows = (
                await s.scalars(
                    select(ProfilePhoto).where(ProfilePhoto.id.in_(_uuids(sorted(wanted))))
                )
            ).all()
    for p in rows:
        try:
            photos[str(p.id)] = (p.mime, await blobs.get(actor.workspace_id, p.blob_key))  # type: ignore[union-attr]
        except (FileNotFoundError, KeyError, OSError):
            continue
    doc, masked = build_render(
        title=v.title,
        template=v.template,
        blind=use_blind,
        content=content,
        photo_bytes=photos.get,
        redact_names=names,
        target_role=v.target_role,
    )
    return _Prepared(
        v, rev.revision_no if rev else 0, content, brain, doc, masked, names, app, photos
    )


def _line_blocks(content: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for sec, it, ln in iter_lines(content):
        if not sec.get("visible", True) or it.get("hidden"):
            continue
        out.append(
            {
                "id": ln["id"],
                "text": ln["text"],
                "origin": "ai" if ln.get("origin") == "ai" else "user",
                "claim_ids": [ln["claim_id"]] if ln.get("claim_id") else [],
            }
        )
    return out


async def check_version(
    db: Database, actor: ActorContext, version_id: UUID, *, revision: int | None = None
) -> dict[str, Any]:
    """판본 체크리스트(화면 위)와 줄 검사. 줄 검사는 문서 검사(``check_document``)를 그대로 쓴다 — 줄 하나가 문단 하나,
    근거는 그 줄의 확인한 사실. 이은 지원이 있으면 그 공고로 회사 바꿔치기·다른 회사 이름·요건을 본다."""
    prep = await _prepare(db, actor, version_id, revision=revision)
    v, content, brain = prep.version, prep.content, prep.brain
    lk = brain.lookup
    checklist: list[dict[str, Any]] = []

    def add(
        code: str, message: str, *, count: int = 1, severity: str = "warning", targets=()
    ) -> None:
        checklist.append(
            {
                "code": code,
                "message": message,
                "count": count,
                "severity": severity,
                "targets": list(targets),
            }
        )

    sections = {s["kind"]: s for s in content["sections"]}

    def visible_items(kind: str) -> list[dict]:
        sec = sections[kind]
        return (
            [it for it in sec["items"] if not it.get("hidden")] if sec.get("visible", True) else []
        )

    for kind in REQUIRED_SECTIONS:
        if kind == "work" and visible_items("project"):
            continue
        if not sections[kind].get("visible", True) and sections[kind]["items"]:
            add(
                "required_section_hidden",
                f"필수 구역 '{SECTION_TITLE[kind]}'을 숨겼습니다.",
                targets=[kind],
            )
        elif not visible_items(kind):
            msg = {
                "profile": "프로필이 비어 있습니다. 이름과 연락처를 넣습니다.",
                "work": "경력이나 프로젝트가 없습니다.",
                "education": "학력이 없습니다.",
            }[kind]
            add("empty_required_section", msg, targets=[kind])
    profile_items = visible_items("profile")
    if profile_items:
        f = profile_items[0].get("fields") or {}
        if not f.get("name"):
            add(
                "profile_no_name",
                "이름이 없습니다. 프로필에 이름을 적고 다시 채웁니다.",
                targets=[profile_items[0]["id"]],
            )
        if not (f.get("email") or f.get("phone")):
            add(
                "profile_no_contact",
                "연락처가 없습니다. 프로필에 이메일이나 전화를 적습니다.",
                targets=[profile_items[0]["id"]],
            )
        current = (
            lk.records.get(("profile", profile_items[0]["ref"]["id"]))
            if profile_items[0].get("ref")
            else None
        )
        if current is not None:
            old = [
                k
                for k in CONTACT_FIELDS
                if f.get(k) != current.get(k) and (f.get(k) or current.get(k))
            ]
            if old:
                add(
                    "old_contact",
                    "연락처가 지금 프로필과 다릅니다. 다시 채우면 지금 연락처로 바뀝니다.",
                    targets=[profile_items[0]["id"]],
                )
    unsupported, stale_lines, broken_lines, stale_items, missing_items, empty_items = (
        [],
        [],
        [],
        [],
        [],
        [],
    )
    for sec, it in iter_items(content):
        if not sec.get("visible", True) or it.get("hidden"):
            continue
        ref = it.get("ref")
        state = item_state(it, lk.records.get((ref["type"], ref["id"])) if ref else None)
        if state == "stale":
            stale_items.append(it["id"])
        elif state == "missing":
            missing_items.append(it["id"])
        if ref and ref["type"] == "experience" and not it.get("lines"):
            empty_items.append(it["id"])
        for ln in it.get("lines") or []:
            st = line_state(ln, lk.claims.get(ln.get("claim_id") or ""))
            if st == "none":
                unsupported.append(ln["id"])
            elif st == "stale":
                stale_lines.append(ln["id"])
            elif st == "broken":
                broken_lines.append(ln["id"])
    if unsupported:
        add(
            "lines_without_evidence",
            f"근거 없는 줄이 {len(unsupported)}개 있습니다. 확인한 사실에서 고르거나 브레인에 사실을 더합니다.",
            count=len(unsupported),
            targets=unsupported,
        )
    if broken_lines:
        add(
            "lines_evidence_broken",
            f"근거가 끊긴 줄이 {len(broken_lines)}개 있습니다. 연결한 사실의 확인이 풀렸습니다.",
            count=len(broken_lines),
            targets=broken_lines,
        )
    if stale_lines:
        add(
            "lines_stale",
            f"확인한 사실이 그 뒤에 바뀐 줄이 {len(stale_lines)}개 있습니다.",
            count=len(stale_lines),
            targets=stale_lines,
        )
    if stale_items:
        add(
            "items_stale",
            f"브레인 기록이 그 뒤에 바뀐 항목이 {len(stale_items)}개 있습니다. 다시 채우면 지금 값으로 바뀝니다.",
            count=len(stale_items),
            targets=stale_items,
        )
    if missing_items:
        add(
            "items_missing",
            f"브레인에서 지웠거나 확인이 풀린 기록의 항목이 {len(missing_items)}개 있습니다.",
            count=len(missing_items),
            targets=missing_items,
        )
    if empty_items:
        add(
            "items_without_lines",
            f"성과 줄이 없는 경험이 {len(empty_items)}개 있습니다.",
            count=len(empty_items),
            severity="info",
            targets=empty_items,
        )
    pages = estimate_pages(prep.doc)
    if pages > v.max_pages:
        add(
            "page_overflow",
            f"쪽 수가 한도 {v.max_pages}쪽을 넘을 것 같습니다 (어림 {pages}쪽).",
            count=pages,
        )
    async with db.workspace_session(actor.workspace_id) as s:
        blind_hint = (not v.blind) and await _posting_mentions_blind(s, v.application_id)
        ctx, _ = await app_uc._check_context(s, prep.app)
        own = await app_uc.own_contacts(s)
        blocks = _line_blocks(content)
        inputs: list[BlockInput] = await app_uc._block_inputs(s, blocks)
    if blind_hint:
        add(
            "blind_suggested",
            "공고에 블라인드 안내가 있습니다. 블라인드를 켜면 사진·생년월일·학교를 빼고 냅니다.",
            severity="info",
        )
    if v.blind:
        if prep.masked:
            add(
                "blind_masked",
                "줄에 학교 이름이나 생년월일이 있어 출력에서 ○○로 가렸습니다. 줄을 고쳐 쓰는 편이 좋습니다.",
                count=prep.masked,
            )
        words = sorted({w for b in blocks for w in blind_words(b["text"])})
        if words:
            add(
                "blind_personal_words",
                "블라인드 판본에 성별·가족·나이·출신을 드러내는 말이 있습니다: " + ", ".join(words),
                count=len(words),
            )
    result = check_document(ctx, inputs)
    findings = result["findings"]
    for b in blocks:
        findings.extend(
            f.as_dict() for f in sensitive_findings(b["text"], own=own, block_id=b["id"])
        )
    generic = [
        f
        for f in findings
        if f["code"] in ("generic_company_swap", "cliche", "paragraph_not_specific")
    ]
    if generic:
        add(
            "generic_lines",
            f"범용 문장·상투어로 보이는 줄이 {len({f['block_id'] for f in generic})}개 있습니다.",
            count=len({f["block_id"] for f in generic}),
            severity="info",
            targets=sorted({f["block_id"] for f in generic}),
        )
    summary: dict[str, int] = {}
    for f in findings:
        summary[f["code"]] = summary.get(f["code"], 0) + 1
    return {
        "version_id": str(v.id),
        "revision": prep.revision_no,
        "estimated_pages": pages,
        # 서버 값은 어림이다 (domain.resume.estimate_pages). 미리보기는 실제로 잰 쪽 수를 쓴다.
        "pages_estimated": True,
        "max_pages": v.max_pages,
        "blind": v.blind,
        "blind_suggested": blind_hint,
        "checklist": checklist,
        "findings": findings,
        "summary": dict(sorted(summary.items())),
    }


async def export_version(
    db: Database,
    blobs: BlobStore | None,
    actor: ActorContext,
    version_id: UUID,
    *,
    fmt: str,
    revision: int | None = None,
) -> ExportResult:
    """판본 한 revision을 파일로 (html·docx·md·json). 블라인드 판본은 사진·생년월일·주소·학교를 뺀다.

    만든 파일을 다시 읽어 숨김 글·조작 문구를 검사하고, 블라인드면 뺀 값이 파일에 남지 않았는지 한 번 더 본다
    (``blind_leak``, 있어서는 안 된다)."""
    if fmt not in RESUME_FORMATS:
        raise ResumeRuleError("invalid_format")
    prep = await _prepare(db, actor, version_id, revision=revision, blobs=blobs)
    if prep.revision_no == 0:
        raise ResumeRuleError("nothing_to_export")
    data = (
        await asyncio.to_thread(
            render_resume, fmt, prep.doc, version_id=str(prep.version.id), revision=prep.revision_no
        )
        if fmt == "json"
        else await asyncio.to_thread(render_resume, fmt, prep.doc)
    )
    # JSON은 키 이름("name" 28번)이 아니라 값 글만 본다 — 형식마다 같은 글을 같은 규칙으로 (검사 수가 같게)
    inspected = _json_values_text(data) if fmt == "json" else data
    try:
        found: list[Finding] = await asyncio.to_thread(
            inspect_export,
            "docx" if fmt == "docx" else ("html" if fmt == "html" else "md"),
            inspected,
        )
    except ExportError as exc:
        raise ResumeRuleError(exc.code) from exc
    async with db.workspace_session(actor.workspace_id) as s:
        own = await app_uc.own_contacts(s)
    for b in _line_blocks(prep.content):
        found.extend(sensitive_findings(b["text"], own=own, block_id=b["id"]))
    if prep.version.blind:
        leaks = blind_leaks(fmt, data, prep.blind_names, prep.photos)
        if leaks:
            found.append(
                Finding(
                    "blind_leak",
                    "블라인드 판본 파일에 뺀 값이 남았습니다.",
                    detail={"count": leaks},
                    severity="error",
                )
            )
    return ExportResult(
        filename=resume_filename(prep.version.title, fmt),
        media_type=RESUME_MEDIA_TYPES[fmt],
        data=data,
        format=fmt,
        revision=prep.revision_no,
        findings=[f.as_dict() for f in found],
        summary=export_summary(found),
    )


async def preview_html(
    db: Database,
    blobs: BlobStore | None,
    actor: ActorContext,
    version_id: UUID,
    *,
    revision: int | None = None,
) -> tuple[bytes, int]:
    """미리보기 = 내보낸 HTML 파일과 같은 바이트 (빈 판본도 빈 쪽 하나로). (HTML, 가린 수)"""
    prep = await _prepare(db, actor, version_id, revision=revision, blobs=blobs)
    data = await asyncio.to_thread(render_resume, "html", prep.doc)
    return data, prep.masked


async def picker(db: Database, actor: ActorContext, version_id: UUID) -> dict[str, Any]:
    """항목 더하기 시트: 브레인 기록 (경험 + 그 경험의 확인한 사실, 학력, 확인한 자격, 기술, 프로필).
    이미 넣은 기록은 ``added``. 확인 대기 사실·후보 자격은 고를 수 없어 보이지 않는다."""
    async with db.workspace_session(actor.workspace_id) as s:
        v = await _version(s, version_id)
        _, content = await _content(s, v)
    have = {(it["ref"]["type"], it["ref"]["id"]) for _, it in iter_items(content) if it.get("ref")}
    skills = await _skills(db, actor)
    async with db.workspace_session(actor.workspace_id) as s:
        exps = (
            await s.scalars(
                select(Experience)
                .where(Experience.status == ACTIVE)
                .order_by(
                    Experience.ongoing.desc(),
                    func.coalesce(Experience.ended_on, Experience.started_on).desc().nulls_last(),
                    Experience.created_at.desc(),
                )
            )
        ).all()
        claims: dict[UUID, list[dict[str, Any]]] = {}
        if exps:
            for c in (
                await s.scalars(
                    select(Claim)
                    .where(
                        Claim.experience_id.in_([e.id for e in exps]),
                        Claim.status.in_([str(x) for x in CONFIRMED_STATUSES]),
                    )
                    .order_by(Claim.created_at)
                )
            ).all():
                claims.setdefault(c.experience_id, []).append(
                    {"id": str(c.id), "statement": c.statement, "kind": c.kind}
                )
        children: dict[UUID, list[UUID]] = {}
        for e in exps:
            if e.parent_id:
                children.setdefault(e.parent_id, []).append(e.id)
        experiences = []
        for e in exps:
            f = experience_fields(_row(e, _EXP_COLS))
            own = list(claims.get(e.id, []))
            for child in children.get(e.id, []):
                own.extend(claims.get(child, []))
            experiences.append(
                {
                    "id": str(e.id),
                    "kind": e.kind,
                    "section": EXPERIENCE_SECTION.get(e.kind, "activities"),
                    "title": e.title,
                    "organization": e.organization,
                    "role": e.role,
                    "period": f.get("period_text") or _period(f),
                    "added": ("experience", str(e.id)) in have,
                    "claims": own,
                }
            )
        education = [
            {
                "id": str(ed.id),
                "school": ed.school,
                "major": ed.major,
                "degree": ed.degree,
                "period": _period(
                    {"started_on": _iso(ed.entered_on), "ended_on": _iso(ed.graduated_on)}
                ),
                "added": ("education", str(ed.id)) in have,
            }
            for ed in (
                await s.scalars(
                    select(Education).order_by(Education.graduated_on.desc().nulls_first())
                )
            ).all()
        ]
        credentials = [
            {
                "id": str(c.id),
                "kind": c.kind,
                "section": CREDENTIAL_SECTION.get(c.kind, "credentials"),
                "name": c.name,
                "issuer": c.issuer,
                "acquired_on": _iso(c.acquired_on),
                "added": ("credential", str(c.id)) in have,
            }
            for c in (
                await s.scalars(
                    select(Credential)
                    .where(Credential.status != "extracted")
                    .order_by(
                        Credential.acquired_on.desc().nulls_last(), Credential.created_at.desc()
                    )
                )
            ).all()
        ]
        profile = await s.scalar(select(Profile))
    return {
        "profile": {
            "id": str(profile.id),
            "name": profile.name,
            "added": ("profile", str(profile.id)) in have,
        }
        if profile is not None
        else None,
        "experiences": experiences,
        "education": education,
        "credentials": credentials,
        "skills": [{"key": k, "name": n, "added": ("skill", k) in have} for k, n in skills.items()],
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _period(f: dict[str, Any]) -> str | None:
    from career_core.domain.resume import period_text

    return period_text(f)


def _json_values_text(data: bytes) -> bytes:
    """JSON Resume의 글 값만 줄마다 (스키마 주소·meta 빼고). 키 이름은 보이는 글이 아니다."""
    import json

    def walk(v: Any):
        if isinstance(v, str):
            yield v
        elif isinstance(v, list):
            for x in v:
                yield from walk(x)
        elif isinstance(v, dict):
            for k, x in v.items():
                if k not in ("$schema", "meta"):
                    yield from walk(x)

    return "\n".join(walk(json.loads(data))).encode("utf-8")


def export_text(fmt: str, data: bytes) -> str:
    """파일에서 보이는 글 (블라인드 확인·시험). DOCX는 본문 XML의 글, 나머지는 바이트 그대로."""
    if fmt == "docx":
        import io
        import zipfile

        from career_core.domain.document_export import _docx_text

        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return "\n".join(
                _docx_text(z.read(n).decode("utf-8", "replace"))
                for n in z.namelist()
                if n.startswith("word/") and n.endswith(".xml")
            )
    return data.decode("utf-8", "replace")


def blind_leaks(
    fmt: str, data: bytes, names: Sequence[str], photos: dict[str, tuple[str, bytes]] | None = None
) -> int:
    text = export_text(fmt, data)
    n = sum(1 for name in names if name and len(name) >= 2 and name in text)
    if "data:image" in text:
        n += 1
    if fmt == "docx":
        import io
        import zipfile

        with zipfile.ZipFile(io.BytesIO(data)) as z:
            n += sum(1 for x in z.namelist() if x.startswith("word/media/"))
    return n


# ---------------------------------------------------------------- MCP용 보기


AGENT_PROFILE_FIELDS = ("name", "headline", "links")


def agent_view(view: VersionView, *, blind: bool) -> dict[str, Any]:
    """MCP 결과: 프로필은 이름·한 줄 소개·링크만 (연락처·생년월일·주소·사진·병역은 restricted라 주지 않는다).
    블라인드 판본이면 학교·캠퍼스, 학력 자격의 이름·발급 기관도 뺀다."""
    content = json.loads(json.dumps(view.content))
    for sec, it in iter_items(content):
        f = it.get("fields") or {}
        if sec["kind"] == "profile":
            it["fields"] = {k: f.get(k) for k in AGENT_PROFILE_FIELDS}
            it.pop("show", None)
        elif blind and it.get("ref") and it["ref"]["type"] == "education":
            f.pop("school", None)
            f.pop("campus", None)
        elif (
            blind
            and it.get("ref")
            and it["ref"]["type"] == "credential"
            and f.get("kind") == "degree"
        ):
            f.pop("name", None)
            f.pop("issuer", None)
    s = view.summary
    return {
        "id": str(s.id),
        "kind": s.kind,
        "title": s.title,
        "target_role": s.target_role,
        "parent_id": str(s.parent_id) if s.parent_id else None,
        "lineage": view.lineage,
        "application_id": str(s.application_id) if s.application_id else None,
        "template": s.template,
        "blind": s.blind,
        "max_pages": s.max_pages,
        "revision": view.revision_no,
        "current_revision": s.current_revision,
        "blind_suggested": view.blind_suggested,
        "sections": content["sections"],
    }


__all__ = [
    "RevisionResult",
    "VersionSummary",
    "VersionView",
    "agent_view",
    "blind_leaks",
    "check_version",
    "create_version",
    "delete_version",
    "export_text",
    "export_version",
    "get_version",
    "list_revisions",
    "list_versions",
    "save",
    "update_settings",
]

"""포트폴리오 (O18, plan §19.6): 경험 하나에 붙는 항목, 이미지, 골라 순서를 정한 묶음, 출력.

- 항목은 경험 하나에 붙는다(``experience_id`` 필수, 이 공간의 활성 경험만). 본문("문제 → 내 역할 → 한 일 → 결과")은
  revision으로만 바뀐다 — 이력서 판본과 같은 409(``base_revision``)·같은 요청 키·같은 내용이면 새 revision 없음.
  결과·한 일 줄은 그 경험(과 바로 아래 경험)의 **확인한 사실**에서 고르고(``portfolio_line_claims``), 직접 쓴 줄과
  문제·내 역할 글은 "근거 없음"이다. 사실이 바뀌면 "오래됨", 확인이 풀리면 "근거 끊김"으로 보이고 저절로 바뀌지 않는다.
- 이미지는 브라우저가 줄여 다시 인코딩한 파일만 받는다. 서버는 형식(JPEG·PNG)·용량·크기를 다시 보고 메타데이터를
  한 번 더 지운다(``domain.profiles.strip_metadata``, 프로필 사진과 같은 코드). 넘으면 거부한다(다시 줄이지 않는다).
  이미지 바이트는 모델·MCP·검색 색인으로 가지 않는다.
- 공개 범위는 ``domain.portfolio.visible_in`` 하나로 정한다: 나만(private)은 어떤 출력에도 없고, 지원서에만은 공고별
  묶음의 출력에만 있다. 묶음을 고르지 않은 출력(Markdown 내보내기·MCP 항목 보기)은 공개 규칙.
- 묶음은 전체 → 직무별 → 공고별. 아래 묶음은 위 묶음의 항목 목록을 복사해 시작하고 출처(묶음·revision)를 남긴다.
  공고별 묶음은 지원 준비(O15)에서 고른 경험의 항목을 먼저 놓자고 제안한다(``prep_order``).
- GitHub 가져오기: 공개 저장소의 README를 원본(내 경험 자료, ``origin_url`` = 저장소 주소)으로 넣어 보통 추출을 탄다.
  주 언어·만든 날·마지막 push는 링크의 참고 정보로 둔다. 별·포크 같은 숫자는 근거가 아니다.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from career_core.db import Claim, Database, Experience
from career_core.db.models import (
    Application,
    GitHubToken,
    PortfolioBundle,
    PortfolioBundleItem,
    PortfolioImage,
    PortfolioItem,
    PortfolioItemRevision,
    PortfolioLineClaim,
    Profile,
)
from career_core.domain.brain import CONFIRMED_STATUSES
from career_core.domain.document_export import ExportError, Finding, export_summary, inspect_export
from career_core.domain.portfolio import (
    ACTION_CLAIM_KINDS,
    BUNDLE_KINDS,
    MAX_BUNDLE_ITEMS,
    MAX_IMAGE_BYTES,
    MAX_IMAGES_PER_ITEM,
    MAX_INTRO,
    MAX_TITLE,
    PARENT_KINDS,
    RESULT_CLAIM_KINDS,
    Lookup,
    PortfolioConflict,
    PortfolioRuleError,
    RenderImage,
    RenderLink,
    RenderPortfolio,
    RenderPortfolioItem,
    annotate,
    apply_operations,
    bundle_audience,
    check_content,
    check_image_kind,
    check_image_size,
    check_visibility,
    clean_caption,
    clean_links,
    clean_repo_info,
    clean_tags,
    content_hash,
    empty_content,
    estimate_item_pages,
    image_ext,
    image_shown,
    iter_lines,
    line_counts,
    new_id,
    operation_claims,
    visible_in,
)
from career_core.domain.portfolio_export import (
    CSP_BUNDLE,
    PORTFOLIO_FORMATS,
    PORTFOLIO_MEDIA_TYPES,
    file_ref,
    portfolio_filename,
    render_portfolio_html,
    render_portfolio_zip,
)
from career_core.domain.profiles import ProfileRuleError, strip_metadata
from career_core.domain.resume import (
    ClaimInfo,
    ResumeRuleError,
    clean_optional,
    clean_title,
    experience_fields,
    period_text,
)
from career_core.domain.sensitive import sensitive_findings
from career_core.ports import ActorContext, BlobStore
from career_core.usecases import applications as app_uc
from career_core.usecases.brain import NotFound
from career_core.usecases.documents import ExportResult

ACTIVE = "active"
MAX_FILL_LINES = 5
MAX_README_SOURCE_BYTES = 1024 * 1024


# ---------------------------------------------------------------- 보기 모양


@dataclass(frozen=True, slots=True)
class ImageView:
    id: UUID
    item_id: UUID
    position: int
    kind: str
    caption: str | None
    visibility: str
    mime: str
    width_px: int
    height_px: int
    bytes: int
    sha256: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ItemSummary:
    id: UUID
    experience_id: UUID
    experience_title: str
    experience_active: bool
    title: str
    visibility: str
    tags: list[str]
    links: list[dict[str, Any]]
    current_revision: int
    # 줄 상태 수 {none, ok, stale, broken}
    lines: dict[str, int]
    results: int
    images: int
    images_private: int
    bundles: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ItemView:
    summary: ItemSummary
    revision_no: int
    revision_id: UUID | None
    content: dict[str, Any]
    experience: dict[str, Any]
    images: list[ImageView]


@dataclass(frozen=True, slots=True)
class RevisionResult:
    id: UUID
    revision_no: int
    created: bool


@dataclass(frozen=True, slots=True)
class BundleSummary:
    id: UUID
    kind: str
    title: str
    target_role: str | None
    intro: str | None
    parent_id: UUID | None
    parent_title: str | None
    # 복사할 때의 위 묶음 revision과 위 묶음의 지금 revision (다르면 위 묶음이 그 뒤에 바뀌었다)
    source_revision: int | None
    parent_revision: int | None
    application_id: UUID | None
    application_title: str | None
    audience: str
    revision: int
    items: int
    shown: int
    children: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class BundleView:
    summary: BundleSummary
    lineage: list[dict[str, Any]]
    # 넣은 순서 그대로. 항목마다 이 묶음의 출력에 보이는지(``shown``)·안 보이는 까닭·이미지 수
    items: list[dict[str, Any]]
    # 공고별 묶음: 지원 준비에서 고른 경험의 항목 (고른 순서). 묶음에 없는 것도 들어 있다
    prep_order: list[UUID]


# ---------------------------------------------------------------- 공통


def _origin(actor: ActorContext) -> tuple[str, str]:
    """(revision origin, 줄 origin): 사람은 user, 에이전트(MCP)는 agent·ai."""
    return ("user", "user") if actor.is_human else ("agent", "ai")


def _uuids(values: Sequence[Any]) -> list[UUID]:
    out = []
    for v in values:
        try:
            out.append(UUID(str(v)))
        except ValueError:
            continue
    return out


async def _item(s: AsyncSession, item_id: UUID, *, lock: bool = False) -> PortfolioItem:
    q = select(PortfolioItem).where(PortfolioItem.id == item_id)
    if lock:
        q = q.with_for_update()
    it = await s.scalar(q)
    if it is None:
        raise NotFound("portfolio_item")
    return it


async def _bundle(s: AsyncSession, bundle_id: UUID, *, lock: bool = False) -> PortfolioBundle:
    q = select(PortfolioBundle).where(PortfolioBundle.id == bundle_id)
    if lock:
        q = q.with_for_update()
    b = await s.scalar(q)
    if b is None:
        raise NotFound("portfolio")
    return b


async def _revision(s: AsyncSession, item_id: UUID, no: int) -> PortfolioItemRevision | None:
    if no <= 0:
        return None
    return await s.scalar(
        select(PortfolioItemRevision).where(
            PortfolioItemRevision.item_id == item_id, PortfolioItemRevision.revision_no == no
        )
    )


async def _content(
    s: AsyncSession, it: PortfolioItem, revision: int | None = None
) -> tuple[PortfolioItemRevision | None, dict[str, Any]]:
    no = it.current_revision if revision is None else revision
    rev = await _revision(s, it.id, no)
    if rev is None and no != 0:
        raise NotFound("revision")
    return rev, (rev.content if rev else empty_content())


async def _contents(s: AsyncSession, items: Sequence[PortfolioItem]) -> dict[UUID, dict[str, Any]]:
    """항목마다 지금 본문."""
    out: dict[UUID, dict[str, Any]] = {it.id: empty_content() for it in items}
    want = {(it.id, it.current_revision) for it in items if it.current_revision}
    if want:
        for r in (
            await s.scalars(
                select(PortfolioItemRevision).where(
                    PortfolioItemRevision.item_id.in_([i for i, _ in want])
                )
            )
        ).all():
            if (r.item_id, r.revision_no) in want:
                out[r.item_id] = r.content
    return out


async def _claims(s: AsyncSession, ids: set[str]) -> dict[str, ClaimInfo]:
    out: dict[str, ClaimInfo] = {}
    if cids := _uuids(sorted(ids)):
        for c, e in (
            await s.execute(
                select(Claim, Experience)
                .join(Experience, Experience.id == Claim.experience_id)
                .where(Claim.id.in_(cids))
            )
        ).all():
            out[str(c.id)] = ClaimInfo(
                id=str(c.id),
                statement=c.statement,
                confirmed=c.status in CONFIRMED_STATUSES and e.status == ACTIVE,
                experience_id=str(c.experience_id),
            )
    return out


async def _family(s: AsyncSession, experience_id: UUID) -> set[str]:
    kids = (
        await s.scalars(select(Experience.id).where(Experience.parent_id == experience_id))
    ).all()
    return {str(experience_id), *(str(k) for k in kids)}


async def _active_experience(s: AsyncSession, experience_id: UUID) -> Experience:
    e = await s.get(Experience, experience_id)
    if e is None:
        raise NotFound("experience")
    if e.status != ACTIVE:
        raise PortfolioRuleError("experience_not_active")
    return e


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


def _exp_view(e: Experience | None) -> dict[str, Any]:
    if e is None:
        return {"id": None, "title": "", "active": False}
    f = experience_fields({n: getattr(e, n) for n in _EXP_COLS})
    return {
        "id": str(e.id),
        "title": e.title,
        "organization": e.organization,
        "role": e.role,
        "kind": e.kind,
        "period": period_text(f),
        "tags": list(e.tags or []),
        "active": e.status == ACTIVE,
    }


def _exp_meta(e: Experience | None) -> str | None:
    if e is None:
        return None
    v = _exp_view(e)
    return " · ".join(x for x in (v["organization"], v["role"], v["period"]) if x) or None


def _image_view(i: PortfolioImage) -> ImageView:
    return ImageView(
        i.id,
        i.item_id,
        i.position,
        i.kind,
        i.caption,
        i.visibility,
        i.mime,
        i.width_px,
        i.height_px,
        i.bytes,
        i.sha256,
        i.created_at,
    )


async def _summaries(s: AsyncSession, rows: Sequence[PortfolioItem]) -> list[ItemSummary]:
    if not rows:
        return []
    ids = [r.id for r in rows]
    exps = {
        e.id: e
        for e in (
            await s.scalars(
                select(Experience).where(Experience.id.in_({r.experience_id for r in rows}))
            )
        ).all()
    }
    contents = await _contents(s, rows)
    claim_ids = {
        ln["claim_id"] for c in contents.values() for _, ln in iter_lines(c) if ln.get("claim_id")
    }
    claims = await _claims(s, claim_ids)
    imgs: dict[UUID, list[str]] = {}
    for item_id, vis in (
        await s.execute(
            select(PortfolioImage.item_id, PortfolioImage.visibility).where(
                PortfolioImage.item_id.in_(ids)
            )
        )
    ).all():
        imgs.setdefault(item_id, []).append(vis)
    bundles: dict[UUID, int] = {}
    for item_id, n in (
        await s.execute(
            select(PortfolioBundleItem.item_id, func.count())
            .where(PortfolioBundleItem.item_id.in_(ids))
            .group_by(PortfolioBundleItem.item_id)
        )
    ).all():
        bundles[item_id] = n
    out = []
    for r in rows:
        e = exps.get(r.experience_id)
        c = contents[r.id]
        out.append(
            ItemSummary(
                id=r.id,
                experience_id=r.experience_id,
                experience_title=e.title if e else "",
                experience_active=bool(e and e.status == ACTIVE),
                title=r.title,
                visibility=r.visibility,
                tags=list(r.tags or []),
                links=list(r.links or []),
                current_revision=r.current_revision,
                lines=line_counts(c, claims),
                results=len(c.get("results") or []),
                images=len(imgs.get(r.id, [])),
                images_private=sum(1 for v in imgs.get(r.id, []) if v == "private"),
                bundles=bundles.get(r.id, 0),
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
        )
    return out


# ---------------------------------------------------------------- 항목


async def list_items(
    db: Database, actor: ActorContext, *, experience_id: UUID | None = None
) -> list[ItemSummary]:
    async with db.workspace_session(actor.workspace_id) as s:
        q = select(PortfolioItem)
        if experience_id is not None:
            q = q.where(PortfolioItem.experience_id == experience_id)
        rows = (
            await s.scalars(q.order_by(PortfolioItem.updated_at.desc(), PortfolioItem.id))
        ).all()
        return await _summaries(s, rows)


async def get_item(
    db: Database, actor: ActorContext, item_id: UUID, *, revision: int | None = None
) -> ItemView:
    async with db.workspace_session(actor.workspace_id) as s:
        it = await _item(s, item_id)
        rev, content = await _content(s, it, revision)
        [summary] = await _summaries(s, [it])
        claims = await _claims(s, operation_claims(content, []))
        e = await s.get(Experience, it.experience_id)
        images = (
            await s.scalars(
                select(PortfolioImage)
                .where(PortfolioImage.item_id == it.id)
                .order_by(PortfolioImage.position, PortfolioImage.created_at)
            )
        ).all()
    return ItemView(
        summary=summary,
        revision_no=rev.revision_no if rev else 0,
        revision_id=rev.id if rev else None,
        content=annotate(content, claims),
        experience=_exp_view(e),
        images=[_image_view(i) for i in images],
    )


async def _insert_revision(
    s: AsyncSession,
    actor: ActorContext,
    it: PortfolioItem,
    content: dict[str, Any],
    *,
    origin: str,
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> PortfolioItemRevision:
    rev = PortfolioItemRevision(
        id=uuid4(),
        workspace_id=actor.workspace_id,
        item_id=it.id,
        revision_no=it.current_revision + 1,
        content=content,
        content_hash=content_hash(content),
        origin=origin,
        reason=(reason or "").strip()[:1000] or None,
        idempotency_key=idempotency_key,
        created_by=actor.subject_id,
    )
    s.add(rev)
    await s.flush()
    for _, ln in iter_lines(content):
        if ln.get("claim_id"):
            s.add(
                PortfolioLineClaim(
                    id=uuid4(),
                    workspace_id=actor.workspace_id,
                    revision_id=rev.id,
                    line_id=ln["id"],
                    claim_id=UUID(ln["claim_id"]),
                )
            )
    it.current_revision = rev.revision_no
    it.updated_at = rev.created_at or it.updated_at
    await s.flush()
    return rev


async def create_item(
    db: Database,
    actor: ActorContext,
    *,
    experience_id: UUID,
    title: str | None = None,
    visibility: str = "public",
    fill: bool = False,
) -> UUID:
    """경험 하나에 항목을 만든다. ``fill``이면 그 경험의 확인한 사실(결과·결정 → 결과, 한 일·맡은 일 → 한 일)을
    5개씩까지 줄로 넣는다 (근거가 이어진 줄. 모델이 글을 쓰지 않는다)."""
    vis = check_visibility(visibility)
    async with db.workspace_session(actor.workspace_id) as s:
        e = await _active_experience(s, experience_id)
        it = PortfolioItem(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            experience_id=e.id,
            title=clean_title(title or e.title, limit=MAX_TITLE),
            visibility=vis,
            tags=clean_tags(list(e.tags or [])[:20]),
            links=[],
            created_by=actor.subject_id,
        )
        s.add(it)
        await s.flush()
        item_id = it.id
        ops: list[dict[str, Any]] = []
        if fill:
            family = await _family(s, e.id)
            rows = (
                await s.scalars(
                    select(Claim)
                    .where(
                        Claim.experience_id.in_(_uuids(sorted(family))),
                        Claim.status.in_([str(x) for x in CONFIRMED_STATUSES]),
                        Claim.kind.in_((*RESULT_CLAIM_KINDS, *ACTION_CLAIM_KINDS)),
                    )
                    .order_by(Claim.created_at, Claim.id)
                )
            ).all()
            per = {"results": 0, "actions": 0}
            for c in rows:
                sec = "results" if c.kind in RESULT_CLAIM_KINDS else "actions"
                if per[sec] >= MAX_FILL_LINES:
                    continue
                per[sec] += 1
                ops.append({"op": "add_line", "section": sec, "claim_id": str(c.id)})
    if ops:
        await save_item(
            db, actor, item_id, base_revision=0, operations=ops, reason="확인한 사실에서 채움"
        )
    return item_id


async def update_item(
    db: Database, actor: ActorContext, item_id: UUID, changes: dict[str, Any]
) -> None:
    """설정(제목·공개 범위·기술 태그·링크)만. 본문은 revision으로만 바뀐다."""
    if set(changes) - {"title", "visibility", "tags", "links"}:
        raise PortfolioRuleError("unknown_field")
    async with db.workspace_session(actor.workspace_id) as s:
        it = await _item(s, item_id, lock=True)
        if "title" in changes:
            it.title = clean_title(changes["title"], limit=MAX_TITLE)
        if "visibility" in changes:
            it.visibility = check_visibility(changes["visibility"])
        if "tags" in changes:
            it.tags = clean_tags(changes["tags"])
        if "links" in changes:
            old = {lk.get("id"): lk for lk in it.links or []}
            new = clean_links(changes["links"])
            # 저장소 정보(info)는 서버가 GitHub에서 읽은 값만 남긴다: 받은 info는 버리고 같은 링크의 옛 값을 잇는다
            for lk in new:
                lk.pop("info", None)
                prev = old.get(lk["id"])
                if prev and prev.get("url") == lk["url"] and prev.get("info"):
                    lk["info"] = prev["info"]
            it.links = new


async def _blob_cleanup(db: Database, blobs: BlobStore | None, ws: UUID, keys: set[str]) -> None:
    from career_core.usecases.profiles import _blob_referenced

    remove = getattr(blobs, "delete", None)
    if remove is None:
        return
    for key in keys:
        if not await _blob_referenced(db, ws, key):
            await remove(ws, key)


async def delete_item(
    db: Database, blobs: BlobStore | None, actor: ActorContext, item_id: UUID
) -> None:
    """항목·본문·이미지를 지운다. 넣어 둔 묶음에서도 빠진다. 다른 행이 쓰지 않는 이미지 파일도 지운다."""
    async with db.workspace_session(actor.workspace_id) as s:
        it = await _item(s, item_id, lock=True)
        keys = set(
            (
                await s.scalars(
                    select(PortfolioImage.blob_key).where(PortfolioImage.item_id == it.id)
                )
            ).all()
        )
        bundle_ids = (
            await s.scalars(
                select(PortfolioBundleItem.bundle_id).where(PortfolioBundleItem.item_id == it.id)
            )
        ).all()
        await s.delete(it)
        await s.flush()
        if bundle_ids:
            for b in (
                await s.scalars(select(PortfolioBundle).where(PortfolioBundle.id.in_(bundle_ids)))
            ).all():
                b.revision += 1
    await _blob_cleanup(db, blobs, actor.workspace_id, keys)


async def save_item(
    db: Database,
    actor: ActorContext,
    item_id: UUID,
    *,
    base_revision: int,
    operations: list[dict[str, Any]],
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> RevisionResult:
    """수정 연산(``domain.portfolio.apply_operations``)을 적용해 새 revision을 만든다."""
    key = (idempotency_key or "").strip()[:200] or None
    rev_origin, line_origin = _origin(actor)
    if not isinstance(operations, list) or not operations:
        raise PortfolioRuleError("no_operations")
    async with db.workspace_session(actor.workspace_id) as s:
        it = await _item(s, item_id, lock=True)
        if key:
            done = await s.scalar(
                select(PortfolioItemRevision).where(
                    PortfolioItemRevision.item_id == it.id,
                    PortfolioItemRevision.idempotency_key == key,
                )
            )
            if done is not None:
                return RevisionResult(done.id, done.revision_no, False)
        if base_revision != it.current_revision:
            raise PortfolioConflict(it.current_revision)
        current, content = await _content(s, it)
        lookup = Lookup(
            claims=await _claims(s, operation_claims(content, operations)),
            family=await _family(s, it.experience_id),
        )
        new = apply_operations(content, operations, lookup, line_origin=line_origin)
        if current is not None and content_hash(new) == current.content_hash:
            return RevisionResult(current.id, current.revision_no, False)
        if current is None and content_hash(new) == content_hash(empty_content()):
            return RevisionResult(uuid4(), 0, False)
        rev = await _insert_revision(
            s, actor, it, new, origin=rev_origin, reason=reason, idempotency_key=key
        )
        return RevisionResult(rev.id, rev.revision_no, True)


async def item_picker(db: Database, actor: ActorContext, item_id: UUID) -> dict[str, Any]:
    """줄 더하기 시트: 이 경험(과 바로 아래 경험)의 확인한 사실. 이미 줄로 넣은 사실은 ``used``."""
    async with db.workspace_session(actor.workspace_id) as s:
        it = await _item(s, item_id)
        _, content = await _content(s, it)
        family = await _family(s, it.experience_id)
        used = {ln["claim_id"] for _, ln in iter_lines(content) if ln.get("claim_id")}
        rows = (
            await s.execute(
                select(Claim, Experience.title)
                .join(Experience, Experience.id == Claim.experience_id)
                .where(
                    Claim.experience_id.in_(_uuids(sorted(family))),
                    Claim.status.in_([str(x) for x in CONFIRMED_STATUSES]),
                    Experience.status == ACTIVE,
                )
                .order_by(Claim.created_at, Claim.id)
            )
        ).all()
    return {
        "claims": [
            {
                "id": str(c.id),
                "statement": c.statement,
                "kind": c.kind,
                "experience_title": title,
                "suggested": "results" if c.kind in RESULT_CLAIM_KINDS else "actions",
                "used": str(c.id) in used,
            }
            for c, title in rows
        ]
    }


# ---------------------------------------------------------------- 이미지


async def upload_image(
    db: Database,
    blobs: BlobStore,
    actor: ActorContext,
    item_id: UUID,
    *,
    data: bytes,
    kind: str = "screenshot",
    caption: str | None = None,
    visibility: str = "private",
) -> tuple[ImageView, bool, tuple[str, ...]]:
    """브라우저에서 줄인 이미지 한 장. (보기, 같은 이미지가 이미 있었나, 서버가 지운 메타데이터 조각).

    서버 확인: 빈 파일·1MiB 초과·JPEG·PNG 밖·깨진 파일·긴 변 1600px 초과는 거부. 메타데이터(EXIF·XMP·ICC·글 덩어리)는
    지운 바이트만 저장한다. 공개 범위 기본은 나만 — 회사 내부 화면이 섞이기 쉬워 사람이 골라야 나간다."""
    if not data:
        raise PortfolioRuleError("empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise PortfolioRuleError("image_too_large")
    k = check_image_kind(kind)
    cap = clean_caption(caption)
    vis = check_visibility(visibility)
    try:
        clean = strip_metadata(data)
    except ProfileRuleError as exc:
        raise PortfolioRuleError(exc.code) from exc
    check_image_size(clean.width, clean.height, len(clean.data))
    sha = hashlib.sha256(clean.data).hexdigest()
    async with db.workspace_session(actor.workspace_id) as s:
        it = await _item(s, item_id)
        found = await s.scalar(
            select(PortfolioImage).where(
                PortfolioImage.item_id == it.id, PortfolioImage.sha256 == sha
            )
        )
        if found is not None:
            return _image_view(found), True, clean.removed
        n = await s.scalar(
            select(func.count()).select_from(PortfolioImage).where(PortfolioImage.item_id == it.id)
        )
        if (n or 0) >= MAX_IMAGES_PER_ITEM:
            raise PortfolioRuleError("too_many_images")
    # 파일 쓰기는 DB 트랜잭션 밖에서 (내용 주소라 겹쳐 써도 무해하다)
    key = await blobs.put(actor.workspace_id, sha, clean.data, clean.mime)
    async with db.workspace_session(actor.workspace_id) as s:
        it = await _item(s, item_id, lock=True)
        pos = await s.scalar(
            select(func.coalesce(func.max(PortfolioImage.position) + 1, 0)).where(
                PortfolioImage.item_id == it.id
            )
        )
        img = PortfolioImage(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            item_id=it.id,
            position=int(pos or 0),
            kind=k,
            caption=cap,
            visibility=vis,
            mime=clean.mime,
            width_px=clean.width,
            height_px=clean.height,
            bytes=len(clean.data),
            sha256=sha,
            blob_key=key,
            created_by=actor.subject_id,
        )
        s.add(img)
        await s.flush()
        return _image_view(img), False, clean.removed


async def update_image(
    db: Database, actor: ActorContext, image_id: UUID, changes: dict[str, Any]
) -> ImageView:
    """설명·종류·공개 범위·순서(``position``: 그 자리로 옮기고 나머지를 민다)."""
    if set(changes) - {"caption", "kind", "visibility", "position"}:
        raise PortfolioRuleError("unknown_field")
    async with db.workspace_session(actor.workspace_id) as s:
        img = await s.get(PortfolioImage, image_id, with_for_update=True)
        if img is None:
            raise NotFound("image")
        if "caption" in changes:
            img.caption = clean_caption(changes["caption"])
        if "kind" in changes:
            img.kind = check_image_kind(changes["kind"])
        if "visibility" in changes:
            img.visibility = check_visibility(changes["visibility"])
        if "position" in changes:
            pos = changes["position"]
            if not isinstance(pos, int) or isinstance(pos, bool) or pos < 0:
                raise PortfolioRuleError("invalid_index")
            rows = list(
                (
                    await s.scalars(
                        select(PortfolioImage)
                        .where(PortfolioImage.item_id == img.item_id)
                        .order_by(PortfolioImage.position, PortfolioImage.created_at)
                    )
                ).all()
            )
            rows.remove(img)
            rows.insert(min(pos, len(rows)), img)
            for n, r in enumerate(rows):
                r.position = n
        await s.flush()
        return _image_view(img)


async def delete_image(
    db: Database, blobs: BlobStore | None, actor: ActorContext, image_id: UUID
) -> None:
    async with db.workspace_session(actor.workspace_id) as s:
        img = await s.get(PortfolioImage, image_id)
        if img is None:
            raise NotFound("image")
        key = img.blob_key
        await s.delete(img)
    await _blob_cleanup(db, blobs, actor.workspace_id, {key})


async def image_file(
    db: Database, blobs: BlobStore, actor: ActorContext, image_id: UUID
) -> tuple[ImageView, bytes]:
    """편집 화면의 미리 보기 그림 (소유 공간 안에서만)."""
    async with db.workspace_session(actor.workspace_id) as s:
        img = await s.get(PortfolioImage, image_id)
    if img is None:
        raise NotFound("image")
    return _image_view(img), await blobs.get(actor.workspace_id, img.blob_key)


# ---------------------------------------------------------------- 묶음


async def _bundle_rows(s: AsyncSession, bundle_id: UUID) -> list[PortfolioBundleItem]:
    return list(
        (
            await s.scalars(
                select(PortfolioBundleItem)
                .where(PortfolioBundleItem.bundle_id == bundle_id)
                .order_by(PortfolioBundleItem.position, PortfolioBundleItem.id)
            )
        ).all()
    )


async def _bundle_summaries(
    s: AsyncSession, rows: Sequence[PortfolioBundle]
) -> list[BundleSummary]:
    if not rows:
        return []
    ids = [b.id for b in rows]
    by_id = {b.id: b for b in rows}
    parents = {b.parent_id for b in rows if b.parent_id and b.parent_id not in by_id}
    if parents:
        for p in (
            await s.scalars(select(PortfolioBundle).where(PortfolioBundle.id.in_(parents)))
        ).all():
            by_id[p.id] = p
    children: dict[UUID, int] = {}
    for pid, n in (
        await s.execute(
            select(PortfolioBundle.parent_id, func.count())
            .where(PortfolioBundle.parent_id.in_(ids))
            .group_by(PortfolioBundle.parent_id)
        )
    ).all():
        children[pid] = n
    members: dict[UUID, list[str]] = {}
    for bid, vis in (
        await s.execute(
            select(PortfolioBundleItem.bundle_id, PortfolioItem.visibility)
            .join(PortfolioItem, PortfolioItem.id == PortfolioBundleItem.item_id)
            .where(PortfolioBundleItem.bundle_id.in_(ids))
        )
    ).all():
        members.setdefault(bid, []).append(vis)
    apps: dict[UUID, str] = {}
    app_ids = [b.application_id for b in rows if b.application_id]
    if app_ids:
        for aid, title in (
            await s.execute(
                select(Application.id, Application.title).where(Application.id.in_(app_ids))
            )
        ).all():
            apps[aid] = title
    out = []
    for b in rows:
        aud = bundle_audience(b.kind)
        vis = members.get(b.id, [])
        parent = by_id.get(b.parent_id) if b.parent_id else None
        out.append(
            BundleSummary(
                id=b.id,
                kind=b.kind,
                title=b.title,
                target_role=b.target_role,
                intro=b.intro,
                parent_id=b.parent_id,
                parent_title=parent.title if parent else None,
                source_revision=b.source_revision,
                parent_revision=parent.revision if parent else None,
                application_id=b.application_id,
                application_title=apps.get(b.application_id) if b.application_id else None,
                audience=aud,
                revision=b.revision,
                items=len(vis),
                shown=sum(1 for v in vis if visible_in(v, aud)),
                children=children.get(b.id, 0),
                created_at=b.created_at,
                updated_at=b.updated_at,
            )
        )
    return out


_KIND_ORDER = {k: n for n, k in enumerate(BUNDLE_KINDS)}


async def list_bundles(
    db: Database, actor: ActorContext, *, application_id: UUID | None = None
) -> list[BundleSummary]:
    async with db.workspace_session(actor.workspace_id) as s:
        q = select(PortfolioBundle)
        if application_id is not None:
            q = q.where(PortfolioBundle.application_id == application_id)
        rows = (await s.scalars(q.order_by(PortfolioBundle.updated_at.desc()))).all()
        out = await _bundle_summaries(s, rows)
    return sorted(out, key=lambda b: _KIND_ORDER.get(b.kind, 9))


async def _lineage(s: AsyncSession, b: PortfolioBundle) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    seen = {b.id}
    cur = b
    while cur.parent_id and cur.parent_id not in seen and len(chain) < 5:
        p = await s.get(PortfolioBundle, cur.parent_id)
        if p is None:
            break
        seen.add(p.id)
        chain.insert(0, {"id": str(p.id), "kind": p.kind, "title": p.title})
        cur = p
    return chain


async def _prep_items(s: AsyncSession, application_id: UUID | None) -> list[UUID]:
    """지원 준비에서 고른 경험 순서 → 그 경험의 항목 (나만 항목은 빼고, 경험마다 최근에 고친 순)."""
    from career_core.usecases.resumes import _prep_choice_order

    order = await _prep_choice_order(s, application_id)
    if not order:
        return []
    exp_ids = _uuids([e for e, _ in order])
    by_exp: dict[UUID, list[PortfolioItem]] = {}
    for it in (
        await s.scalars(
            select(PortfolioItem)
            .where(PortfolioItem.experience_id.in_(exp_ids), PortfolioItem.visibility != "private")
            .order_by(PortfolioItem.updated_at.desc())
        )
    ).all():
        by_exp.setdefault(it.experience_id, []).append(it)
    out: list[UUID] = []
    for e in exp_ids:
        out.extend(it.id for it in by_exp.get(e, []))
    return out


async def get_bundle(db: Database, actor: ActorContext, bundle_id: UUID) -> BundleView:
    async with db.workspace_session(actor.workspace_id) as s:
        b = await _bundle(s, bundle_id)
        [summary] = await _bundle_summaries(s, [b])
        lineage = await _lineage(s, b)
        rows = await _bundle_rows(s, b.id)
        items = {
            it.id: it
            for it in (
                await s.scalars(
                    select(PortfolioItem).where(PortfolioItem.id.in_([r.item_id for r in rows]))
                )
            ).all()
        }
        sums = {x.id: x for x in await _summaries(s, list(items.values()))}
        imgs: dict[UUID, list[str]] = {}
        if items:
            for item_id, vis in (
                await s.execute(
                    select(PortfolioImage.item_id, PortfolioImage.visibility).where(
                        PortfolioImage.item_id.in_(list(items))
                    )
                )
            ).all():
                imgs.setdefault(item_id, []).append(vis)
        prep = await _prep_items(s, b.application_id) if b.kind == "posting" else []
    aud = bundle_audience(b.kind)
    out_items = []
    for r in rows:
        it = items.get(r.item_id)
        if it is None:
            continue
        shown = visible_in(it.visibility, aud)
        vis = imgs.get(it.id, [])
        out_items.append(
            {
                "item": sums[it.id],
                "shown": shown,
                "hidden_reason": None if shown else it.visibility,
                "images_shown": sum(1 for v in vis if shown and visible_in(v, aud)),
                "images_hidden": sum(1 for v in vis if not (shown and visible_in(v, aud))),
            }
        )
    return BundleView(summary=summary, lineage=lineage, items=out_items, prep_order=prep)


def _kind(value: Any) -> str:
    if value not in BUNDLE_KINDS:
        raise PortfolioRuleError("invalid_kind")
    return value


async def _check_application(s: AsyncSession, application_id: UUID) -> Application:
    app = await s.get(Application, application_id)
    if app is None:
        raise NotFound("application")
    return app


async def _write_items(
    s: AsyncSession, actor: ActorContext, b: PortfolioBundle, item_ids: Sequence[UUID]
) -> None:
    ids = list(dict.fromkeys(item_ids))
    if len(ids) > MAX_BUNDLE_ITEMS:
        raise PortfolioRuleError("too_many_items")
    if ids:
        found = set(
            (await s.scalars(select(PortfolioItem.id).where(PortfolioItem.id.in_(ids)))).all()
        )
        if found != set(ids):
            raise PortfolioRuleError("unknown_item")
    await s.execute(delete(PortfolioBundleItem).where(PortfolioBundleItem.bundle_id == b.id))
    await s.flush()
    for n, iid in enumerate(ids):
        s.add(
            PortfolioBundleItem(
                id=uuid4(),
                workspace_id=actor.workspace_id,
                bundle_id=b.id,
                item_id=iid,
                position=n,
            )
        )
    b.revision += 1
    await s.flush()


async def create_bundle(
    db: Database,
    actor: ActorContext,
    *,
    kind: str,
    title: str | None = None,
    parent_id: UUID | None = None,
    target_role: str | None = None,
    application_id: UUID | None = None,
    intro: str | None = None,
    fill: bool = False,
) -> UUID:
    """묶음을 만든다. 직무별·공고별은 위 묶음(``parent_id``)의 항목 목록을 복사해 시작한다(출처 revision을 남긴다).

    ``fill``: 위 묶음이 없을 때 나만이 아닌 항목을 모두 넣는다 (전체 묶음의 처음). 공고별 묶음을 위 묶음 없이 지원에
    이어 만들면 지원 준비에서 고른 경험의 항목부터 넣는다."""
    k = _kind(kind)
    role = clean_optional(target_role, 200, "invalid_target_role")
    if application_id is not None and k != "posting":
        raise PortfolioRuleError("application_needs_posting")
    async with db.workspace_session(actor.workspace_id) as s:
        app = await _check_application(s, application_id) if application_id else None
        parent = None
        if parent_id is not None:
            parent = await _bundle(s, parent_id)
            if parent.kind not in PARENT_KINDS[k]:
                raise PortfolioRuleError("invalid_parent")
        default_title = {
            "all": "포트폴리오",
            "role": f"{role} 포트폴리오" if role else "직무별 포트폴리오",
            "posting": f"{app.title} 포트폴리오" if app else "공고별 포트폴리오",
        }[k]
        b = PortfolioBundle(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            kind=k,
            title=clean_title(title or default_title, limit=MAX_TITLE),
            target_role=role if role is not None else (parent.target_role if parent else None),
            intro=clean_optional(intro, MAX_INTRO, "invalid_intro")
            if intro is not None
            else (parent.intro if parent else None),
            parent_id=parent.id if parent else None,
            source_revision=parent.revision if parent else None,
            application_id=app.id if app else None,
            created_by=actor.subject_id,
        )
        s.add(b)
        await s.flush()
        ids: list[UUID] = []
        if parent is not None:
            ids = [r.item_id for r in await _bundle_rows(s, parent.id)]
        else:
            if k == "posting" and app is not None:
                ids = await _prep_items(s, app.id)
            if fill:
                rest = (
                    await s.scalars(
                        select(PortfolioItem)
                        .where(PortfolioItem.visibility != "private")
                        .order_by(PortfolioItem.updated_at.desc(), PortfolioItem.id)
                    )
                ).all()
                ids += [it.id for it in rest if it.id not in ids]
        await _write_items(s, actor, b, ids[:MAX_BUNDLE_ITEMS])
        return b.id


async def update_bundle(
    db: Database, actor: ActorContext, bundle_id: UUID, changes: dict[str, Any]
) -> None:
    if set(changes) - {"title", "target_role", "intro", "application_id"}:
        raise PortfolioRuleError("unknown_field")
    async with db.workspace_session(actor.workspace_id) as s:
        b = await _bundle(s, bundle_id, lock=True)
        if "title" in changes:
            b.title = clean_title(changes["title"], limit=MAX_TITLE)
        if "target_role" in changes:
            b.target_role = clean_optional(changes["target_role"], 200, "invalid_target_role")
        if "intro" in changes:
            b.intro = clean_optional(changes["intro"], MAX_INTRO, "invalid_intro")
        if "application_id" in changes:
            aid = changes["application_id"]
            if aid is not None:
                if b.kind != "posting":
                    raise PortfolioRuleError("application_needs_posting")
                b.application_id = (await _check_application(s, UUID(str(aid)))).id
            else:
                b.application_id = None


async def set_bundle_items(
    db: Database,
    actor: ActorContext,
    bundle_id: UUID,
    *,
    base_revision: int,
    item_ids: list[UUID],
) -> int:
    """묶음의 항목 목록(순서 그대로)을 바꾼다. ``base_revision``이 최신이 아니면 409. 새 revision을 돌려준다."""
    async with db.workspace_session(actor.workspace_id) as s:
        b = await _bundle(s, bundle_id, lock=True)
        if base_revision != b.revision:
            raise PortfolioConflict(b.revision)
        current = [r.item_id for r in await _bundle_rows(s, b.id)]
        if current == list(dict.fromkeys(item_ids)):
            return b.revision
        await _write_items(s, actor, b, item_ids)
        return b.revision


async def apply_prep_order(
    db: Database, actor: ActorContext, bundle_id: UUID, *, base_revision: int
) -> int:
    """공고별 묶음: 지원 준비에서 고른 경험의 항목을 고른 순서로 맨 앞에 (없으면 더한다). 나머지는 그대로 뒤에."""
    async with db.workspace_session(actor.workspace_id) as s:
        b = await _bundle(s, bundle_id, lock=True)
        if b.kind != "posting" or b.application_id is None:
            raise PortfolioRuleError("no_application")
        if base_revision != b.revision:
            raise PortfolioConflict(b.revision)
        prep = await _prep_items(s, b.application_id)
        if not prep:
            raise PortfolioRuleError("prep_not_chosen")
        current = [r.item_id for r in await _bundle_rows(s, b.id)]
        new = prep + [i for i in current if i not in prep]
        if new != current:
            await _write_items(s, actor, b, new)
        return b.revision


async def delete_bundle(db: Database, actor: ActorContext, bundle_id: UUID) -> None:
    """묶음을 지운다 (항목은 그대로). 아래 묶음이 있으면 지우지 않는다(계보 유지)."""
    async with db.workspace_session(actor.workspace_id) as s:
        b = await _bundle(s, bundle_id, lock=True)
        n = await s.scalar(
            select(func.count())
            .select_from(PortfolioBundle)
            .where(PortfolioBundle.parent_id == b.id)
        )
        if n:
            raise PortfolioRuleError("bundle_has_children")
        await s.delete(b)


# ---------------------------------------------------------------- 출력


@dataclass(slots=True)
class _Prepared:
    bundle: PortfolioBundle
    doc: RenderPortfolio
    texts: list[tuple[str, str]] = field(default_factory=list)  # (블록 ID, 글) 민감 정보 검사용
    claims: dict[str, ClaimInfo] = field(default_factory=dict)
    contents: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    items: list[PortfolioItem] = field(default_factory=list)  # 이 출력에 보이는 항목 (순서대로)
    hidden_items: list[PortfolioItem] = field(default_factory=list)
    hidden_images: int = 0
    experiences: dict[UUID, Experience] = field(default_factory=dict)


async def _render_items(
    db: Database,
    blobs: BlobStore | None,
    actor: ActorContext,
    items: Sequence[PortfolioItem],
    audience: str,
) -> tuple[
    list[RenderPortfolioItem],
    list[PortfolioItem],
    list[PortfolioItem],
    int,
    dict[UUID, dict[str, Any]],
    dict[UUID, Experience],
    list[tuple[str, str]],
]:
    """항목들 → 출력 모양. 공개 범위로 거른다 (항목이 안 보이면 그 이미지도 안 보인다)."""
    async with db.workspace_session(actor.workspace_id) as s:
        contents = await _contents(s, items)
        exps = {
            e.id: e
            for e in (
                await s.scalars(
                    select(Experience).where(Experience.id.in_({i.experience_id for i in items}))
                )
            ).all()
        }
        images: dict[UUID, list[PortfolioImage]] = {}
        if items:
            for img in (
                await s.scalars(
                    select(PortfolioImage)
                    .where(PortfolioImage.item_id.in_([i.id for i in items]))
                    .order_by(PortfolioImage.position, PortfolioImage.created_at)
                )
            ).all():
                images.setdefault(img.item_id, []).append(img)
    shown: list[PortfolioItem] = []
    hidden: list[PortfolioItem] = []
    hidden_images = 0
    out: list[RenderPortfolioItem] = []
    texts: list[tuple[str, str]] = []
    for it in items:
        if not visible_in(it.visibility, audience):
            hidden.append(it)
            hidden_images += len(images.get(it.id, []))
            continue
        shown.append(it)
        n = len(shown)
        c = contents[it.id]
        rimgs: list[RenderImage] = []
        for m, img in enumerate(images.get(it.id, []), 1):
            if not image_shown(it.visibility, img.visibility, audience):
                hidden_images += 1
                continue
            data = b""
            if blobs is not None:
                try:
                    data = await blobs.get(actor.workspace_id, img.blob_key)
                except (FileNotFoundError, KeyError, OSError):
                    continue
            rimgs.append(
                RenderImage(
                    name=f"item-{n}-{m}.{image_ext(img.mime)}",
                    mime=img.mime,
                    data=data,
                    width=img.width_px,
                    height=img.height_px,
                    kind=img.kind,
                    caption=img.caption,
                )
            )
        links = tuple(RenderLink(lk["kind"], lk["url"], lk.get("label")) for lk in (it.links or []))
        for f in ("problem", "role"):
            if c.get(f):
                texts.append((f"{it.id}:{f}", c[f]))
        for _, ln in iter_lines(c):
            texts.append((ln["id"], ln["text"]))
        out.append(
            RenderPortfolioItem(
                anchor=f"item-{n}",
                title=it.title,
                experience_id=str(it.experience_id),
                meta=_exp_meta(exps.get(it.experience_id)),
                tags=tuple(it.tags or ()),
                links=links,
                problem=c.get("problem"),
                role=c.get("role"),
                actions=tuple(ln["text"] for ln in c.get("actions") or []),
                results=tuple(ln["text"] for ln in c.get("results") or []),
                images=tuple(rimgs),
            )
        )
    return out, shown, hidden, hidden_images, contents, exps, texts


async def _prepare(
    db: Database, blobs: BlobStore | None, actor: ActorContext, bundle_id: UUID
) -> _Prepared:
    async with db.workspace_session(actor.workspace_id) as s:
        b = await _bundle(s, bundle_id)
        rows = await _bundle_rows(s, b.id)
        by_id = {
            it.id: it
            for it in (
                await s.scalars(
                    select(PortfolioItem).where(PortfolioItem.id.in_([r.item_id for r in rows]))
                )
            ).all()
        }
        profile = await s.scalar(select(Profile))
        app = await s.get(Application, b.application_id) if b.application_id else None
    items = [by_id[r.item_id] for r in rows if r.item_id in by_id]
    audience = bundle_audience(b.kind)
    rendered, shown, hidden, hidden_images, contents, exps, texts = await _render_items(
        db, blobs, actor, items, audience
    )
    subtitle = " · ".join(x for x in (b.target_role, app.title if app else None) if x) or None
    doc = RenderPortfolio(
        title=b.title,
        audience=audience,
        owner_name=profile.name if profile else None,
        headline=profile.headline if profile else None,
        owner_links=tuple(
            (lk.get("label") or None, lk["url"])
            for lk in ((profile.links or []) if profile else [])
            if isinstance(lk, dict) and lk.get("url")
        ),
        subtitle=subtitle,
        intro=b.intro,
        items=tuple(rendered),
    )
    async with db.workspace_session(actor.workspace_id) as s:
        claim_ids = {
            ln["claim_id"]
            for c in contents.values()
            for _, ln in iter_lines(c)
            if ln.get("claim_id")
        }
        claims = await _claims(s, claim_ids)
    return _Prepared(
        bundle=b,
        doc=doc,
        texts=texts,
        claims=claims,
        contents=contents,
        items=shown,
        hidden_items=hidden,
        hidden_images=hidden_images,
        experiences=exps,
    )


async def preview_html(
    db: Database, blobs: BlobStore | None, actor: ActorContext, bundle_id: UUID
) -> bytes:
    """미리보기 = 내보낸 인쇄용 HTML 파일과 같은 바이트."""
    prep = await _prepare(db, blobs, actor, bundle_id)
    return (await asyncio.to_thread(render_portfolio_html, prep.doc)).encode("utf-8")


async def _resume_with_links(
    db: Database,
    blobs: BlobStore | None,
    actor: ActorContext,
    version_id: UUID,
    doc: RenderPortfolio,
) -> bytes:
    """함께 내보내는 이력서: 프로젝트 구역의 경험 항목이 이 묶음에 보이는 항목과 같은 경험이면
    ``index.html#item-n`` 상대 링크를 단다. 블라인드 등 이력서 규칙은 그대로 (``resumes._prepare``)."""
    from career_core.domain.resume_export import render_resume_html
    from career_core.usecases import resumes as resume_uc

    rprep = await resume_uc._prepare(db, actor, version_id, revision=None, blobs=blobs)
    if rprep.revision_no == 0:
        raise PortfolioRuleError("resume_empty")
    anchors: dict[str, str] = {}
    for it in doc.items:
        anchors.setdefault(it.experience_id, it.anchor)
    sections = []
    for sec in rprep.doc.sections:
        if sec.kind == "project":
            sec = dataclasses.replace(
                sec,
                items=tuple(
                    dataclasses.replace(x, href=f"index.html#{anchors[x.ref_id]}")
                    if x.ref_id in anchors
                    else x
                    for x in sec.items
                ),
            )
        sections.append(sec)
    rdoc = dataclasses.replace(rprep.doc, sections=tuple(sections))
    return render_resume_html(rdoc).encode("utf-8")


async def export_bundle(
    db: Database,
    blobs: BlobStore | None,
    actor: ActorContext,
    bundle_id: UUID,
    *,
    fmt: str,
    resume_version_id: UUID | None = None,
) -> ExportResult:
    """묶음을 파일로: ``html``(인쇄용 HTML 하나, 미리보기와 같은 바이트) · ``zip``(정적 HTML 묶음).
    ``zip``에 이력서 판본을 함께 넣으면(``resume_version_id``) 프로젝트 항목에서 포트폴리오 항목으로 링크가 이어진다.

    만든 HTML을 다시 읽어 숨김 서식·조작 문구를 검사하고, 줄 글의 민감 정보(연락처·주민등록번호 모양)를 경고한다."""
    if fmt not in PORTFOLIO_FORMATS:
        raise PortfolioRuleError("invalid_format")
    if resume_version_id is not None and fmt != "zip":
        raise PortfolioRuleError("resume_needs_zip")
    prep = await _prepare(db, blobs, actor, bundle_id)
    if not prep.doc.items:
        raise PortfolioRuleError("nothing_to_export")
    if fmt == "html":
        data = (await asyncio.to_thread(render_portfolio_html, prep.doc)).encode("utf-8")
        page = data
    else:
        resume = None
        doc = prep.doc
        if resume_version_id is not None:
            resume = await _resume_with_links(db, blobs, actor, resume_version_id, doc)
            doc = dataclasses.replace(doc, resume_href="resume.html")
        data = await asyncio.to_thread(render_portfolio_zip, doc, resume_html=resume)
        page = render_portfolio_html(doc, image_src=file_ref, csp=CSP_BUNDLE).encode("utf-8")
    try:
        found: list[Finding] = await asyncio.to_thread(inspect_export, "html", page)
    except ExportError as exc:
        raise PortfolioRuleError(exc.code) from exc
    async with db.workspace_session(actor.workspace_id) as s:
        own = await app_uc.own_contacts(s)
    for block_id, text in prep.texts:
        found.extend(sensitive_findings(text, own=own, block_id=block_id))
    return ExportResult(
        filename=portfolio_filename(prep.bundle.title, fmt),
        media_type=PORTFOLIO_MEDIA_TYPES[fmt],
        data=data,
        format=fmt,
        revision=prep.bundle.revision,
        findings=[f.as_dict() for f in found],
        summary=export_summary(found),
    )


async def check_bundle(db: Database, actor: ActorContext, bundle_id: UUID) -> dict[str, Any]:
    """묶음 체크리스트: 이 출력에서 빠지는 항목·이미지, 결과 없는 항목, 근거 없는·끊긴·바뀐 줄, 직접 쓴 글,
    보관한 경험, 두 쪽을 넘을 것 같은 항목, 지원 준비 순서 제안."""
    prep = await _prepare(db, None, actor, bundle_id)
    b = prep.bundle
    checklist: list[dict[str, Any]] = []

    def add(code: str, message: str, *, count: int = 1, severity: str = "warning", targets=()):
        checklist.append(
            {
                "code": code,
                "message": message,
                "count": count,
                "severity": severity,
                "targets": [str(t) for t in targets],
            }
        )

    if not prep.items and not prep.hidden_items:
        add("empty_bundle", "아직 넣은 항목이 없습니다.", severity="info")
    private = [i for i in prep.hidden_items if i.visibility == "private"]
    app_only = [i for i in prep.hidden_items if i.visibility == "application"]
    if private:
        add(
            "items_private",
            f"'나만' 항목 {len(private)}개는 이 묶음의 어떤 출력에도 나가지 않습니다.",
            count=len(private),
            severity="info",
            targets=[i.id for i in private],
        )
    if app_only:
        add(
            "items_application_only",
            f"'지원서에만' 항목 {len(app_only)}개는 공고별 묶음에서만 나갑니다.",
            count=len(app_only),
            severity="info",
            targets=[i.id for i in app_only],
        )
    if prep.hidden_images:
        add(
            "images_hidden",
            f"이 묶음에서 나가지 않는 이미지가 {prep.hidden_images}장 있습니다 (공개 범위).",
            count=prep.hidden_images,
            severity="info",
        )
    none, stale, broken, no_results, written, gone, long = [], [], [], [], [], [], []
    for it, r in zip(prep.items, prep.doc.items, strict=True):
        c = prep.contents[it.id]
        if not c.get("results"):
            no_results.append(it.id)
        if c.get("problem") or c.get("role"):
            written.append(it.id)
        e = prep.experiences.get(it.experience_id)
        if e is None or e.status != ACTIVE:
            gone.append(it.id)
        if estimate_item_pages(r) > 2:
            long.append(it.id)
        for _, ln in iter_lines(c):
            from career_core.domain.resume import line_state

            st = line_state(ln, prep.claims.get(ln.get("claim_id") or ""))
            {"none": none, "stale": stale, "broken": broken}.get(st, []).append(ln["id"])
    if no_results:
        add(
            "items_without_results",
            f"결과 줄이 없는 항목이 {len(no_results)}개 있습니다. 확인한 사실에서 고릅니다.",
            count=len(no_results),
            targets=no_results,
        )
    if none:
        add(
            "lines_without_evidence",
            f"근거 없는 줄이 {len(none)}개 있습니다. 확인한 사실에서 고르거나 브레인에 사실을 더합니다.",
            count=len(none),
            targets=none,
        )
    if broken:
        add(
            "lines_evidence_broken",
            f"근거가 끊긴 줄이 {len(broken)}개 있습니다. 연결한 사실의 확인이 풀렸습니다.",
            count=len(broken),
            targets=broken,
        )
    if stale:
        add(
            "lines_stale",
            f"확인한 사실이 그 뒤에 바뀐 줄이 {len(stale)}개 있습니다.",
            count=len(stale),
            targets=stale,
        )
    if written:
        add(
            "text_without_evidence",
            f"'문제'·'내 역할'은 직접 쓴 글이라 근거가 없습니다 (항목 {len(written)}개).",
            count=len(written),
            severity="info",
            targets=written,
        )
    if gone:
        add(
            "experience_not_active",
            f"보관했거나 없어진 경험의 항목이 {len(gone)}개 있습니다.",
            count=len(gone),
            targets=gone,
        )
    if long:
        add(
            "item_too_long",
            f"두 쪽을 넘을 것 같은 항목이 {len(long)}개 있습니다. 줄이나 이미지를 줄입니다.",
            count=len(long),
            targets=long,
        )
    if b.kind == "posting" and b.application_id:
        async with db.workspace_session(actor.workspace_id) as s:
            prep_ids = await _prep_items(s, b.application_id)
        if prep_ids:
            add(
                "prep_order",
                f"지원 준비에서 고른 경험의 항목이 {len(prep_ids)}개 있습니다. 먼저 놓을 수 있습니다.",
                count=len(prep_ids),
                severity="info",
                targets=prep_ids,
            )
    return {
        "bundle_id": str(b.id),
        "revision": b.revision,
        "audience": prep.doc.audience,
        "items_shown": len(prep.items),
        "checklist": checklist,
    }


# ---------------------------------------------------------------- 이력서·Markdown·MCP가 쓰는 보기


async def items_for_experiences(s: AsyncSession, exp_ids: Sequence[UUID]) -> dict[str, str]:
    """경험 ID → 그 경험의 항목 하나 (나만 항목은 빼고 최근에 고친 것). 이력서 편집기의 "포트폴리오" 링크용."""
    if not exp_ids:
        return {}
    out: dict[str, str] = {}
    for it in (
        await s.scalars(
            select(PortfolioItem)
            .where(
                PortfolioItem.experience_id.in_(list(exp_ids)),
                PortfolioItem.visibility != "private",
            )
            .order_by(PortfolioItem.updated_at.desc())
        )
    ).all():
        out.setdefault(str(it.experience_id), str(it.id))
    return out


def _agent_item(
    it: PortfolioItem,
    content: dict[str, Any],
    claims: dict[str, ClaimInfo],
    exp: Experience | None,
    images: Sequence[PortfolioImage],
    audience: str,
) -> dict[str, Any]:
    c = annotate(content, claims)

    def lines(sec: str) -> list[dict[str, Any]]:
        return [
            {"text": ln["text"], "claim_id": ln.get("claim_id"), "state": ln["state"]}
            for ln in c.get(sec) or []
        ]

    return {
        "id": str(it.id),
        "title": it.title,
        "visibility": it.visibility,
        "experience": _exp_view(exp),
        "tags": list(it.tags or []),
        "links": [
            {k: lk.get(k) for k in ("kind", "url", "label", "info") if lk.get(k) is not None}
            for lk in it.links or []
        ],
        "problem": c.get("problem"),
        "role": c.get("role"),
        "actions": lines("actions"),
        "results": lines("results"),
        # 이미지는 설명·크기만 (바이트·파일 주소는 주지 않는다)
        "images": [
            {
                "kind": img.kind,
                "caption": img.caption,
                "width_px": img.width_px,
                "height_px": img.height_px,
                "visibility": img.visibility,
            }
            for img in images
            if image_shown(it.visibility, img.visibility, audience)
        ],
    }


async def _agent_items(
    db: Database, actor: ActorContext, items: Sequence[PortfolioItem], audience: str
) -> list[dict[str, Any]]:
    shown = [it for it in items if visible_in(it.visibility, audience)]
    async with db.workspace_session(actor.workspace_id) as s:
        contents = await _contents(s, shown)
        claims = await _claims(
            s,
            {
                ln["claim_id"]
                for c in contents.values()
                for _, ln in iter_lines(c)
                if ln.get("claim_id")
            },
        )
        exps = {
            e.id: e
            for e in (
                await s.scalars(
                    select(Experience).where(Experience.id.in_({i.experience_id for i in shown}))
                )
            ).all()
        }
        imgs: dict[UUID, list[PortfolioImage]] = {}
        if shown:
            for img in (
                await s.scalars(
                    select(PortfolioImage)
                    .where(PortfolioImage.item_id.in_([i.id for i in shown]))
                    .order_by(PortfolioImage.position, PortfolioImage.created_at)
                )
            ).all():
                imgs.setdefault(img.item_id, []).append(img)
    return [
        _agent_item(
            it, contents[it.id], claims, exps.get(it.experience_id), imgs.get(it.id, []), audience
        )
        for it in shown
    ]


def _bundle_brief(b: BundleSummary) -> dict[str, Any]:
    return {
        "id": str(b.id),
        "kind": b.kind,
        "title": b.title,
        "target_role": b.target_role,
        "application_id": str(b.application_id) if b.application_id else None,
        "parent_id": str(b.parent_id) if b.parent_id else None,
        "audience": b.audience,
        "items_shown": b.shown,
    }


async def agent_list(db: Database, actor: ActorContext) -> dict[str, Any]:
    """MCP 목록: 묶음과 항목(공개 규칙: 나만·지원서에만 항목은 빠진다). 이미지 바이트 없음."""
    bundles = await list_bundles(db, actor)
    async with db.workspace_session(actor.workspace_id) as s:
        rows = (
            await s.scalars(
                select(PortfolioItem)
                .where(PortfolioItem.visibility == "public")
                .order_by(PortfolioItem.updated_at.desc())
            )
        ).all()
        sums = await _summaries(s, rows)
    return {
        "bundles": [_bundle_brief(b) for b in bundles],
        "items": [
            {
                "id": str(x.id),
                "title": x.title,
                "experience_id": str(x.experience_id),
                "experience_title": x.experience_title,
                "tags": x.tags,
                "lines": x.lines,
            }
            for x in sums
        ],
    }


async def agent_get(
    db: Database, actor: ActorContext, *, bundle_id: UUID | None, item_id: UUID | None
) -> dict[str, Any]:
    """MCP 보기. 묶음을 고르면 그 묶음의 출력 규칙(공고별이면 지원서에만도 보인다), 항목만 고르면 공개 규칙.
    나만 항목·이미지는 어떤 경우에도 없다. 규칙상 안 보이는 항목을 고르면 없는 것과 같다(404)."""
    if (bundle_id is None) == (item_id is None):
        raise PortfolioRuleError("pick_one")
    if bundle_id is not None:
        async with db.workspace_session(actor.workspace_id) as s:
            b = await _bundle(s, bundle_id)
            [summary] = await _bundle_summaries(s, [b])
            rows = await _bundle_rows(s, b.id)
            by_id = {
                it.id: it
                for it in (
                    await s.scalars(
                        select(PortfolioItem).where(PortfolioItem.id.in_([r.item_id for r in rows]))
                    )
                ).all()
            }
        items = [by_id[r.item_id] for r in rows if r.item_id in by_id]
        return {
            "bundle": {**_bundle_brief(summary), "intro": summary.intro},
            "items": await _agent_items(db, actor, items, summary.audience),
        }
    async with db.workspace_session(actor.workspace_id) as s:
        it = await _item(s, item_id)  # type: ignore[arg-type]
    if not visible_in(it.visibility, "public"):
        raise NotFound("portfolio_item")
    [out] = await _agent_items(db, actor, [it], "public")
    return {"item": out}


async def markdown_items(s: AsyncSession) -> list[dict[str, Any]]:
    """Markdown 내보내기용 (공개 규칙): 공개 가능 항목과 그 공개 가능 이미지의 설명만. 바이트·파일은 없다."""
    rows = (
        await s.scalars(
            select(PortfolioItem)
            .where(PortfolioItem.visibility == "public")
            .order_by(PortfolioItem.created_at, PortfolioItem.id)
        )
    ).all()
    contents = await _contents(s, rows)
    imgs: dict[UUID, list[PortfolioImage]] = {}
    if rows:
        for img in (
            await s.scalars(
                select(PortfolioImage)
                .where(PortfolioImage.item_id.in_([i.id for i in rows]))
                .order_by(PortfolioImage.position, PortfolioImage.created_at)
            )
        ).all():
            imgs.setdefault(img.item_id, []).append(img)
    return [
        {
            "row": it,
            "content": contents[it.id],
            "images": [
                img
                for img in imgs.get(it.id, [])
                if image_shown(it.visibility, img.visibility, "public")
            ],
        }
        for it in rows
    ]


# ---------------------------------------------------------------- GitHub 가져오기·토큰


@dataclass(frozen=True, slots=True)
class GitHubDeps:
    """``CoreDeps.github``. ``client``가 바깥 요청을 하고, ``secrets``가 사용자 토큰을 암호화한다 (없으면 토큰 저장 불가)."""

    client: Any  # adapters.github.GitHubClient
    secrets: Any = None  # adapters.secret_box.SecretBox

    def __repr__(self) -> str:
        return "GitHubDeps(<hidden>)"


@dataclass(frozen=True, slots=True)
class ImportResult:
    repo: dict[str, Any]
    readme: bool
    source_id: UUID | None
    parse_run_id: UUID | None
    deduplicated: bool
    item_id: UUID | None


async def github_token_status(db: Database, actor: ActorContext) -> dict[str, Any]:
    async with db.workspace_session(actor.workspace_id) as s:
        row = await s.scalar(select(GitHubToken))
    return {"set": row is not None, "last4": row.last4 if row else None}


async def set_github_token(db: Database, gh: GitHubDeps, actor: ActorContext, token: str) -> dict:
    from career_core.adapters.github import GitHubError, check_token
    from career_core.adapters.secret_box import last4

    if gh.secrets is None:
        raise PortfolioRuleError("token_storage_unavailable")
    try:
        value = check_token(token)
    except GitHubError as exc:
        raise PortfolioRuleError(exc.code) from exc
    from career_core.adapters.secret_box import SecretBoxError

    try:
        cipher = gh.secrets.encrypt(value)
    except SecretBoxError as exc:
        raise PortfolioRuleError("token_storage_unavailable") from exc
    async with db.workspace_session(actor.workspace_id) as s:
        row = await s.scalar(select(GitHubToken).with_for_update())
        if row is None:
            s.add(
                GitHubToken(
                    id=uuid4(),
                    workspace_id=actor.workspace_id,
                    ciphertext=cipher,
                    last4=last4(value),
                    created_by=actor.subject_id,
                )
            )
        else:
            row.ciphertext = cipher
            row.last4 = last4(value)
    return await github_token_status(db, actor)


async def delete_github_token(db: Database, actor: ActorContext) -> None:
    async with db.workspace_session(actor.workspace_id) as s:
        await s.execute(delete(GitHubToken))


async def _token(db: Database, gh: GitHubDeps, actor: ActorContext) -> str:
    async with db.workspace_session(actor.workspace_id) as s:
        row = await s.scalar(select(GitHubToken))
    if row is None:
        raise PortfolioRuleError("token_missing")
    value = gh.secrets.decrypt(row.ciphertext) if gh.secrets is not None else None
    if not value:
        raise PortfolioRuleError("token_needs_reentry")
    return value


def _readme_source(info: Any) -> str:
    head = [f"# {info.full_name} README", "", f"출처: <{info.html_url}>", ""]
    meta = [
        f"- 주 언어: {info.language}" if info.language else None,
        f"- 만든 날: {info.created_at[:10]}" if info.created_at else None,
        f"- 마지막 push: {info.pushed_at[:10]}" if info.pushed_at else None,
    ]
    meta = [m for m in meta if m]
    if meta:
        head += ["저장소 정보 (참고, 본인 기여의 근거가 아님):", "", *meta, ""]
    return "\n".join(head) + "\n" + (info.readme or "") + "\n"


async def github_import(
    db: Database,
    blobs: BlobStore,
    gh: GitHubDeps,
    actor: ActorContext,
    *,
    url: str,
    private: bool = False,
    item_id: UUID | None = None,
) -> ImportResult:
    """GitHub 저장소 → README 원본(내 경험 자료, ``origin_url`` = 저장소 주소) + 저장소 정보.

    ``private``면 저장해 둔 사용자 토큰으로 읽는다(없으면 ``token_missing``). 아니면 토큰 없이 공개 API로만.
    ``item_id``를 주면 그 항목의 저장소 링크(같은 주소면 바꾸고, 없으면 더한다)에 정보를 남긴다."""
    from career_core.adapters.github import GitHubError, parse_repo_url
    from career_core.domain.sources import DataClass, SourcePurpose
    from career_core.uploads import UploadRejected
    from career_core.usecases.sources import upload_source

    try:
        ref = parse_repo_url(url)
    except GitHubError as exc:
        raise PortfolioRuleError(exc.code) from exc
    if item_id is not None:
        async with db.workspace_session(actor.workspace_id) as s:
            await _item(s, item_id)
    token = await _token(db, gh, actor) if private else None
    try:
        info = await gh.client.repo(ref, token=token)
    except GitHubError as exc:
        raise PortfolioRuleError(exc.code, reset_at=exc.reset_at) from None
    repo = {
        "full_name": info.full_name,
        "html_url": info.html_url,
        "private": info.private,
        "language": info.language,
        "created_at": info.created_at,
        "pushed_at": info.pushed_at,
        "default_branch": info.default_branch,
        "description": info.description,
        "stars": info.stars,
        "forks": info.forks,
    }
    source_id = run_id = None
    dedup = False
    if info.readme:
        name = info.full_name.replace("/", "-")
        try:
            up = await upload_source(
                db,
                blobs,
                actor,
                filename=f"{name}-README.md",
                data=_readme_source(info).encode("utf-8"),
                declared_mime="text/markdown",
                max_bytes=MAX_README_SOURCE_BYTES,
                title=f"GitHub README · {info.full_name}",
                data_class=DataClass.PRIVATE,
                purpose=SourcePurpose.EXPERIENCE,
                origin_url=info.html_url,
            )
        except UploadRejected as exc:
            raise PortfolioRuleError(exc.code) from exc
        source_id, run_id, dedup = up.source_id, up.parse_run_id, up.deduplicated
    if item_id is not None:
        async with db.workspace_session(actor.workspace_id) as s:
            it = await _item(s, item_id, lock=True)
            links = [dict(lk) for lk in it.links or []]
            same = [
                lk
                for lk in links
                if lk.get("kind") == "repo"
                and lk.get("url", "").rstrip("/").lower() == info.html_url.lower()
            ]
            if same:
                same[0]["info"] = clean_repo_info(repo)
                same[0]["url"] = info.html_url
            else:
                links.append(
                    {
                        "id": new_id("k"),
                        "kind": "repo",
                        "url": info.html_url,
                        "label": None,
                        "info": clean_repo_info(repo),
                    }
                )
            cleaned = clean_links([{k: v for k, v in lk.items()} for lk in links])
            it.links = cleaned
    return ImportResult(repo, bool(info.readme), source_id, run_id, dedup, item_id)


def portfolio_error_code(exc: Exception) -> str:
    return exc.code if isinstance(exc, ResumeRuleError) else "invalid"


__all__ = [
    "BundleSummary",
    "BundleView",
    "GitHubDeps",
    "ImageView",
    "ImportResult",
    "ItemSummary",
    "ItemView",
    "PortfolioConflict",
    "PortfolioRuleError",
    "RevisionResult",
    "agent_get",
    "agent_list",
    "apply_prep_order",
    "check_bundle",
    "check_content",
    "create_bundle",
    "create_item",
    "delete_bundle",
    "delete_image",
    "delete_item",
    "export_bundle",
    "get_bundle",
    "get_item",
    "github_import",
    "image_file",
    "item_picker",
    "list_bundles",
    "list_items",
    "preview_html",
    "save_item",
    "set_bundle_items",
    "update_bundle",
    "update_image",
    "update_item",
    "upload_image",
]

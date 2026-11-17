"""공통 답변·지난 제출본 (c0042, plan §19.4 ``answer_assets``, §20 "나 › 내 글").

공통 답변
- 지원마다 다시 쓰지 않는 글: 1분 자기소개·지원 동기 틀·성격 장단점·갈등 경험·실패 경험·입사 후 포부·기타.
  본문은 사용자가 쓴 글이다. 근거는 확인한 사실(``claims``, 확인된 상태)만 잇는다 — 확인 전 후보·참고 글·회사
  항목은 잇지 않는다 (``claim_not_confirmed`` / ``unknown_claim``).
- "지원서에 넣기"(``use_answer``): 본문을 줄마다 문단으로 나눠 지원 문서 끝에 더하거나(``document_id`` +
  ``base_revision``, 다르면 409) 새 문서를 만든다. 넣는 순간에도 확인된 사실만 문단 근거로 옮긴다 (답변 근거를
  문단마다 붙인다 — 사람이 작업대에서 문단별로 줄인다). 넣은 기록(``answer_asset_uses``)이 "쓴 곳"이 된다.

지난 제출본
- 제출본 고정(``application_submissions``)에 들어 있는 문서만 보인다 (읽기 전용, 제출 당시 복사본).
- "이 글로 시작": 새 공통 답변으로(``start_answer_from_submission``) 또는 다른 지원 문서로
  (``use_submission_document``). 지원으로 옮길 때 블록의 origin(user/ai)은 그대로다 — AI 비율이 사라지지 않는다.

다른 회사 이름 경고
- 글에 이 공간의 회사 이름(회사 조사의 이름·별칭, 공고의 회사 이름)이 보이면 알린다 (``company_mentions``).
  지원으로 넣을 때는 그 지원의 회사는 빼고 본다. 막지는 않는다. ``dry_run``이면 쓰지 않고 경고만 돌려준다.
- 모델을 부르지 않는다. 공통 답변은 사용자가 쓴 글이라 MCP ``answer_asset_get``이 본문을 준다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from career_core.db.base import utcnow
from career_core.db.models import (
    AnswerAsset,
    AnswerAssetClaim,
    AnswerAssetUse,
    Application,
    ApplicationDocument,
    ApplicationSubmission,
    Claim,
    Company,
    Experience,
    JobPosting,
)
from career_core.db.session import Database
from career_core.domain.brain import CONFIRMED_STATUSES
from career_core.domain.companies import name_key
from career_core.domain.documents import (
    MAX_BLOCK_CHARS,
    MAX_BLOCKS,
    BlockOrigin,
    CountRule,
    DocumentKind,
    DocumentRuleError,
    RevisionConflict,
    count_text,
    new_block_id,
    normalize_text,
    rule_from_prompt,
)
from career_core.domain.postings import FieldName
from career_core.ports import ActorContext
from career_core.usecases import applications as app_uc
from career_core.usecases.brain import NotFound

MAX_TITLE = 200
MAX_BODY = 20_000
MAX_CLAIMS = 20
EXCERPT = 120


class AnswerKind(StrEnum):
    INTRO = "intro"  # 1분 자기소개
    MOTIVATION = "motivation"  # 지원 동기 틀
    PERSONALITY = "personality"  # 성격 장단점
    CONFLICT = "conflict"  # 갈등 경험
    FAILURE = "failure"  # 실패 경험
    ASPIRATION = "aspiration"  # 입사 후 포부
    OTHER = "other"


class AnswerRuleError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- 보기


@dataclass(frozen=True, slots=True)
class ClaimRef:
    id: UUID
    statement: str
    kind: str
    status: str
    confirmed: bool  # 지금도 확인된 상태인가 (아니면 넣을 때 빠진다)
    experience_id: UUID
    experience_title: str


@dataclass(frozen=True, slots=True)
class UseRef:
    application_id: UUID
    application_title: str
    document_id: UUID
    document_title: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AnswerSummary:
    id: UUID
    kind: str
    title: str
    excerpt: str
    chars: int  # 공백 포함
    chars_without_spaces: int
    claims: int
    uses: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AnswerView:
    id: UUID
    kind: str
    title: str
    body: str
    chars: int
    chars_without_spaces: int
    claims: list[ClaimRef]
    uses: list[UseRef]
    source_submission_id: UUID | None
    source_document_id: UUID | None
    created_at: datetime
    updated_at: datetime


def _counts(body: str) -> tuple[int, int]:
    c = count_text(body or "", CountRule())
    return c.chars_with_spaces, c.chars_without_spaces


def _excerpt(body: str) -> str:
    s = " ".join((body or "").split())
    return s if len(s) <= EXCERPT else s[: EXCERPT - 1] + "…"


async def list_answers(db: Database, actor: ActorContext) -> list[AnswerSummary]:
    async with db.workspace_session(actor.workspace_id) as s:
        rows = list(
            (await s.scalars(select(AnswerAsset).order_by(AnswerAsset.updated_at.desc()))).all()
        )
        claims = dict(
            (
                await s.execute(
                    select(AnswerAssetClaim.answer_asset_id, func.count()).group_by(
                        AnswerAssetClaim.answer_asset_id
                    )
                )
            ).all()
        )
        uses = dict(
            (
                await s.execute(
                    select(AnswerAssetUse.answer_asset_id, func.count()).group_by(
                        AnswerAssetUse.answer_asset_id
                    )
                )
            ).all()
        )
    out = []
    for r in rows:
        chars, no_space = _counts(r.body)
        out.append(
            AnswerSummary(
                r.id,
                r.kind,
                r.title,
                _excerpt(r.body),
                chars,
                no_space,
                claims.get(r.id, 0),
                uses.get(r.id, 0),
                r.updated_at,
            )
        )
    return out


async def _claim_refs(s: AsyncSession, answer_id: UUID) -> list[ClaimRef]:
    rows = (
        await s.execute(
            select(Claim, Experience.title)
            .join(AnswerAssetClaim, AnswerAssetClaim.claim_id == Claim.id)
            .join(Experience, Experience.id == Claim.experience_id)
            .where(AnswerAssetClaim.answer_asset_id == answer_id)
            .order_by(AnswerAssetClaim.created_at, Claim.id)
        )
    ).all()
    return [
        ClaimRef(
            c.id,
            c.statement,
            c.kind,
            c.status,
            c.status in CONFIRMED_STATUSES,
            c.experience_id,
            title,
        )
        for c, title in rows
    ]


async def _use_refs(s: AsyncSession, answer_id: UUID) -> list[UseRef]:
    rows = (
        await s.execute(
            select(AnswerAssetUse, Application.title, ApplicationDocument.title)
            .join(Application, Application.id == AnswerAssetUse.application_id)
            .join(ApplicationDocument, ApplicationDocument.id == AnswerAssetUse.document_id)
            .where(AnswerAssetUse.answer_asset_id == answer_id)
            .order_by(AnswerAssetUse.created_at.desc())
        )
    ).all()
    return [UseRef(u.application_id, at, u.document_id, dt, u.created_at) for u, at, dt in rows]


async def get_answer(db: Database, actor: ActorContext, answer_id: UUID) -> AnswerView:
    async with db.workspace_session(actor.workspace_id) as s:
        r = await s.get(AnswerAsset, answer_id)
        if r is None:
            raise NotFound(answer_id)
        claims = await _claim_refs(s, r.id)
        uses = await _use_refs(s, r.id)
    chars, no_space = _counts(r.body)
    return AnswerView(
        r.id,
        r.kind,
        r.title,
        r.body,
        chars,
        no_space,
        claims,
        uses,
        r.source_submission_id,
        r.source_document_id,
        r.created_at,
        r.updated_at,
    )


# ---------------------------------------------------------------- 쓰기


def _kind(value: Any) -> str:
    try:
        return AnswerKind(value).value
    except ValueError as exc:
        raise AnswerRuleError("invalid_kind") from exc


def _title(value: Any) -> str:
    s = " ".join(str(value or "").split())
    if not s:
        raise AnswerRuleError("title_required")
    if len(s) > MAX_TITLE:
        raise AnswerRuleError("title_too_long")
    return s


def _body(value: Any) -> str:
    s = normalize_text(str(value or "")).strip()
    if len(s) > MAX_BODY:
        raise AnswerRuleError("body_too_long")
    return s


async def _confirmed_claims(s: AsyncSession, claim_ids: list[UUID]) -> list[UUID]:
    """잇는 주장은 모두 이 공간에 있고 확인된 상태여야 한다. 순서는 준 대로, 겹침은 하나로."""
    ids = list(dict.fromkeys(claim_ids))
    if len(ids) > MAX_CLAIMS:
        raise AnswerRuleError("too_many_claims")
    if not ids:
        return []
    found = dict((await s.execute(select(Claim.id, Claim.status).where(Claim.id.in_(ids)))).all())
    if set(ids) - set(found):
        raise AnswerRuleError("unknown_claim")
    if any(found[i] not in CONFIRMED_STATUSES for i in ids):
        raise AnswerRuleError("claim_not_confirmed")
    return ids


async def _set_claims(
    s: AsyncSession, actor: ActorContext, answer_id: UUID, ids: list[UUID]
) -> None:
    current = list(
        (
            await s.scalars(
                select(AnswerAssetClaim).where(AnswerAssetClaim.answer_asset_id == answer_id)
            )
        ).all()
    )
    keep = set(ids)
    for link in current:
        if link.claim_id not in keep:
            await s.delete(link)
    have = {link.claim_id for link in current}
    for cid in ids:
        if cid not in have:
            s.add(
                AnswerAssetClaim(
                    id=uuid4(),
                    workspace_id=actor.workspace_id,
                    answer_asset_id=answer_id,
                    claim_id=cid,
                )
            )


async def create_answer(
    db: Database,
    actor: ActorContext,
    *,
    kind: str,
    title: str,
    body: str = "",
    claim_ids: list[UUID] | None = None,
    source_submission_id: UUID | None = None,
    source_document_id: UUID | None = None,
) -> UUID:
    row = AnswerAsset(
        id=uuid4(),
        workspace_id=actor.workspace_id,
        kind=_kind(kind),
        title=_title(title),
        body=_body(body),
        source_submission_id=source_submission_id,
        source_document_id=source_document_id,
        created_by=actor.subject_id,
    )
    async with db.workspace_session(actor.workspace_id) as s:
        ids = await _confirmed_claims(s, claim_ids or [])
        s.add(row)
        await s.flush()
        await _set_claims(s, actor, row.id, ids)
    return row.id


async def update_answer(
    db: Database, actor: ActorContext, answer_id: UUID, changes: dict[str, Any]
) -> None:
    """준 칸만 바꾼다. ``claim_ids``는 근거 목록 전체를 이것으로."""
    if set(changes) - {"kind", "title", "body", "claim_ids"}:
        raise AnswerRuleError("unknown_field")
    async with db.workspace_session(actor.workspace_id) as s:
        row = await s.get(AnswerAsset, answer_id, with_for_update=True)
        if row is None:
            raise NotFound(answer_id)
        if "kind" in changes:
            row.kind = _kind(changes["kind"])
        if "title" in changes:
            row.title = _title(changes["title"])
        if "body" in changes:
            row.body = _body(changes["body"])
        if "claim_ids" in changes:
            ids = await _confirmed_claims(s, list(changes["claim_ids"] or []))
            await _set_claims(s, actor, row.id, ids)
        row.updated_at = utcnow()


async def delete_answer(db: Database, actor: ActorContext, answer_id: UUID) -> None:
    """공통 답변 지우기. 이미 넣은 지원 문서의 글은 그대로다 (쓴 곳 기록만 함께 지워진다)."""
    async with db.workspace_session(actor.workspace_id) as s:
        row = await s.get(AnswerAsset, answer_id)
        if row is None:
            raise NotFound(answer_id)
        await s.delete(row)


# ---------------------------------------------------------------- 다른 회사 이름

_LEGAL = re.compile(
    r"\(주\)|㈜|\(유\)|주식회사|유한회사|\b(?:co\.?,?\s*ltd\.?|inc\.?|corp\.?|ltd\.?|llc)(?=\W|$)",
    re.IGNORECASE,
)
MIN_NAME_CHARS = 2


def _plain(name: str) -> str:
    return " ".join(_LEGAL.sub(" ", unicodedata.normalize("NFKC", name or "")).split())


async def _company_names(s: AsyncSession) -> dict[str, tuple[str, set[str]]]:
    """회사 키 → (화면 이름, 찾을 표기들). 회사 조사의 이름·별칭과 공고의 회사 이름."""
    out: dict[str, tuple[str, set[str]]] = {}

    def add(display: str, spellings: list[str]) -> None:
        key = name_key(display)
        if not key:
            return
        _, have = out.setdefault(key, (display, set()))
        for sp in spellings:
            plain = _plain(sp)
            if len(plain) >= MIN_NAME_CHARS:
                have.add(plain)

    for name, aliases in (await s.execute(select(Company.name, Company.aliases))).all():
        add(name, [name, *(aliases or [])])
    for (value,) in (
        await s.execute(
            select(JobPosting.fields[FieldName.COMPANY.value].astext).where(
                JobPosting.fields[FieldName.COMPANY.value].astext.isnot(None)
            )
        )
    ).all():
        if value:
            add(value, [value])
    return out


def _mentioned(text: str, spellings: set[str]) -> bool:
    low = unicodedata.normalize("NFKC", text or "").casefold()
    for sp in spellings:
        needle = sp.casefold()
        if needle.isascii():
            if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", low):
                return True
        elif needle in low:
            return True
    return False


async def company_mentions(
    s: AsyncSession, text: str, *, exclude: set[str] | None = None
) -> list[str]:
    """글에 보이는 이 공간의 회사 이름 (화면 이름, 가나다순). ``exclude``는 뺄 회사 키(``name_key``)."""
    names = await _company_names(s)
    skip = exclude or set()
    found = [
        display
        for key, (display, spellings) in names.items()
        if key not in skip and _mentioned(text, spellings)
    ]
    return sorted(found, key=str.casefold)


async def _application_company_keys(s: AsyncSession, app: Application) -> set[str]:
    """지원의 회사: 공고의 회사 이름과, 이어진 회사 조사의 이름·별칭."""
    posting = await s.get(JobPosting, app.posting_id)
    keys: set[str] = set()
    if posting is None:
        return keys
    name = (posting.fields or {}).get(FieldName.COMPANY.value)
    if isinstance(name, str) and name_key(name):
        keys.add(name_key(name))
    if posting.company_id is not None:
        company = await s.get(Company, posting.company_id)
        if company is not None:
            keys |= {name_key(n) for n in (company.name, *(company.aliases or [])) if name_key(n)}
    # 이 회사의 별칭이 다른 표기로 저장된 공고 이름이면 그것도 같은 회사
    for display, spellings in (await _company_names(s)).values():
        if {name_key(sp) for sp in spellings} & keys:
            keys.add(name_key(display))
    return keys


# ---------------------------------------------------------------- 지원 문서에 넣기


@dataclass(frozen=True, slots=True)
class PlaceResult:
    application_id: UUID
    document_id: UUID | None  # dry_run이면 None
    revision_no: int | None
    created_document: bool
    company_mentions: list[str]
    claims_dropped: int = 0  # 지금은 확인된 상태가 아니라 근거로 옮기지 않은 사실 수


def _paragraphs(text: str) -> list[str]:
    out = []
    for line in normalize_text(text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        while len(line) > MAX_BLOCK_CHARS:  # 아주 긴 줄은 나눠 넣는다 (문단 상한)
            out.append(line[:MAX_BLOCK_CHARS])
            line = line[MAX_BLOCK_CHARS:].strip()
        if line:
            out.append(line)
    return out


async def _still_confirmed(s: AsyncSession, ids: list[str]) -> list[str]:
    if not ids:
        return []
    rows = (
        await s.execute(select(Claim.id, Claim.status).where(Claim.id.in_([UUID(i) for i in ids])))
    ).all()
    ok = {str(i) for i, st in rows if st in CONFIRMED_STATUSES}
    return [i for i in ids if i in ok]


async def _place(
    s: AsyncSession,
    actor: ActorContext,
    application_id: UUID,
    blocks: list[dict],
    *,
    document_id: UUID | None,
    base_revision: int | None,
    title: str,
    question_prompt: str | None,
    char_limit: int | None,
    reason: str,
    dry_run: bool,
) -> tuple[PlaceResult, ApplicationDocument | None, Any]:
    app = await app_uc._get_app(s, application_id, lock=not dry_run)
    app_uc._ensure_editable(app)
    text = "\n".join(b["text"] for b in blocks)
    mentions = await company_mentions(s, text, exclude=await _application_company_keys(s, app))
    if not blocks:
        raise AnswerRuleError("nothing_to_copy")
    if document_id is not None:
        _, doc = await app_uc._get_doc(s, application_id, document_id, lock=not dry_run)
        if base_revision is None or base_revision != doc.current_revision:
            raise RevisionConflict(doc.current_revision)
        if dry_run:
            return PlaceResult(app.id, None, None, False, mentions), None, None
        current = await app_uc._revision(s, doc.id, doc.current_revision)
        old = [dict(b) for b in current.blocks] if current else []
        created = False
    else:
        if dry_run:
            return PlaceResult(app.id, None, None, True, mentions), None, None
        prompt = " ".join((question_prompt or "").split()) or None
        if prompt:
            rule, limit, byte_limit = rule_from_prompt(prompt, char_limit)
        else:
            rule, limit, byte_limit = CountRule(), char_limit, None
        doc = ApplicationDocument(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            application_id=app.id,
            kind=(DocumentKind.QUESTION if prompt else DocumentKind.OTHER).value,
            title=app_uc._title(title),
            question_prompt=prompt,
            char_limit=app_uc._limit(limit, "invalid_char_limit"),
            byte_limit=app_uc._limit(byte_limit, "invalid_byte_limit"),
            count_rule=rule.as_dict(),
            created_by=actor.subject_id,
        )
        s.add(doc)
        await s.flush()
        old = []
        created = True
    if len(old) + len(blocks) > MAX_BLOCKS:
        raise DocumentRuleError("too_many_blocks")
    await app_uc._ensure_claims(s, blocks)
    rev = await app_uc._insert_revision(s, actor, doc, old + blocks, reason=reason)
    return PlaceResult(app.id, doc.id, rev.revision_no, created, mentions), doc, rev


async def use_answer(
    db: Database,
    actor: ActorContext,
    answer_id: UUID,
    application_id: UUID,
    *,
    document_id: UUID | None = None,
    base_revision: int | None = None,
    question_prompt: str | None = None,
    char_limit: int | None = None,
    dry_run: bool = False,
) -> PlaceResult:
    """공통 답변을 지원 문서에 넣는다 (``document_id`` 끝에 더하기, 없으면 새 문서). 넣은 기록을 남긴다."""
    async with db.workspace_session(actor.workspace_id) as s:
        row = await s.get(AnswerAsset, answer_id)
        if row is None:
            raise NotFound(answer_id)
        linked = [
            str(c)
            for c in (
                await s.scalars(
                    select(AnswerAssetClaim.claim_id)
                    .where(AnswerAssetClaim.answer_asset_id == row.id)
                    .order_by(AnswerAssetClaim.created_at)
                )
            ).all()
        ]
        claims = await _still_confirmed(s, linked)
        blocks = [
            {
                "id": new_block_id(),
                "text": p,
                "origin": BlockOrigin.USER.value,
                "claim_ids": list(claims),
                "spans": [],
            }
            for p in _paragraphs(row.body)
        ]
        result, doc, rev = await _place(
            s,
            actor,
            application_id,
            blocks,
            document_id=document_id,
            base_revision=base_revision,
            title=row.title,
            question_prompt=question_prompt,
            char_limit=char_limit,
            reason=f"공통 답변 '{row.title}'에서 넣음",
            dry_run=dry_run,
        )
        if doc is not None:
            s.add(
                AnswerAssetUse(
                    id=uuid4(),
                    workspace_id=actor.workspace_id,
                    answer_asset_id=row.id,
                    application_id=application_id,
                    document_id=doc.id,
                    revision_id=rev.id,
                    created_by=actor.subject_id,
                )
            )
    return PlaceResult(
        result.application_id,
        result.document_id,
        result.revision_no,
        result.created_document,
        result.company_mentions,
        len(linked) - len(claims),
    )


# ---------------------------------------------------------------- 지난 제출본


@dataclass(frozen=True, slots=True)
class SubmittedDocument:
    submission_id: UUID
    application_id: UUID
    application_title: str
    company: str | None
    role: str | None  # 공고 제목 (직무)
    document_id: UUID
    kind: str
    title: str
    question: str | None
    char_limit: int | None
    chars: int | None
    submitted_at: datetime


@dataclass(frozen=True, slots=True)
class SubmittedDocumentView(SubmittedDocument):
    text: str
    blocks: list[dict]  # [{id, text, origin, claim_ids}]
    evidence: list[dict]  # 제출 당시 근거 문장 복사본
    company_mentions: list[str]  # 글에 보이는 회사 이름 (이 제출본의 회사 포함)
    answers: list[dict]  # 이 글로 시작한 공통 답변 [{id, title}]


def _doc_entry(sub: ApplicationSubmission, app_title: str, d: dict) -> SubmittedDocument:
    fields = (sub.posting or {}).get("fields") or {}
    counts = d.get("counts") or {}
    return SubmittedDocument(
        submission_id=sub.id,
        application_id=sub.application_id,
        application_title=app_title,
        company=fields.get(FieldName.COMPANY.value) or None,
        role=fields.get(FieldName.TITLE.value) or None,
        document_id=UUID(d["document_id"]),
        kind=d.get("kind") or "other",
        title=d.get("title") or "",
        question=d.get("question_prompt"),
        char_limit=d.get("char_limit"),
        chars=counts.get("chars"),
        submitted_at=sub.submitted_at,
    )


async def list_submitted_documents(db: Database, actor: ActorContext) -> list[SubmittedDocument]:
    """모든 지원의 제출본 문서 (최근 제출부터, 한 제출본 안은 문항 순서)."""
    async with db.workspace_session(actor.workspace_id) as s:
        rows = (
            await s.execute(
                select(ApplicationSubmission, Application.title)
                .join(Application, Application.id == ApplicationSubmission.application_id)
                .order_by(ApplicationSubmission.submitted_at.desc())
            )
        ).all()
    return [_doc_entry(sub, title, d) for sub, title in rows for d in sub.documents or []]


async def _submitted(
    s: AsyncSession, submission_id: UUID, document_id: UUID
) -> tuple[ApplicationSubmission, str, dict]:
    row = (
        await s.execute(
            select(ApplicationSubmission, Application.title)
            .join(Application, Application.id == ApplicationSubmission.application_id)
            .where(ApplicationSubmission.id == submission_id)
        )
    ).first()
    if row is None:
        raise NotFound(submission_id)
    sub, title = row
    for d in sub.documents or []:
        if d.get("document_id") == str(document_id):
            return sub, title, d
    raise NotFound(document_id)


async def get_submitted_document(
    db: Database, actor: ActorContext, submission_id: UUID, document_id: UUID
) -> SubmittedDocumentView:
    async with db.workspace_session(actor.workspace_id) as s:
        sub, title, d = await _submitted(s, submission_id, document_id)
        text = d.get("text") or "\n".join(b.get("text", "") for b in d.get("blocks") or [])
        mentions = await company_mentions(s, text)
        answers = (
            await s.execute(
                select(AnswerAsset.id, AnswerAsset.title).where(
                    AnswerAsset.source_submission_id == sub.id,
                    AnswerAsset.source_document_id == document_id,
                )
            )
        ).all()
    entry = _doc_entry(sub, title, d)
    blocks = [
        {
            "id": b.get("id"),
            "text": b.get("text", ""),
            "origin": b.get("origin", "user"),
            "claim_ids": list(b.get("claim_ids") or []),
        }
        for b in d.get("blocks") or []
    ]
    return SubmittedDocumentView(
        **{f: getattr(entry, f) for f in SubmittedDocument.__dataclass_fields__},
        text=text,
        blocks=blocks,
        evidence=list(d.get("evidence") or []),
        company_mentions=mentions,
        answers=[{"id": str(i), "title": t} for i, t in answers],
    )


@dataclass(frozen=True, slots=True)
class StartedAnswer:
    answer_id: UUID
    company_mentions: list[str]
    claims_dropped: int


async def start_answer_from_submission(
    db: Database,
    actor: ActorContext,
    submission_id: UUID,
    document_id: UUID,
    *,
    kind: str = AnswerKind.OTHER.value,
    title: str | None = None,
) -> StartedAnswer:
    """제출본 문서 하나로 새 공통 답변을 만든다. 근거는 지금도 확인된 사실만 옮긴다."""
    async with db.workspace_session(actor.workspace_id) as s:
        sub, _, d = await _submitted(s, submission_id, document_id)
        text = d.get("text") or "\n".join(b.get("text", "") for b in d.get("blocks") or [])
        ids = list(
            dict.fromkeys(c for b in d.get("blocks") or [] for c in b.get("claim_ids") or [])
        )
        keep = await _still_confirmed(s, ids)
        mentions = await company_mentions(s, text)
        row = AnswerAsset(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            kind=_kind(kind),
            title=_title(title or d.get("question_prompt") or d.get("title")),
            body=_body(text),
            source_submission_id=sub.id,
            source_document_id=document_id,
            created_by=actor.subject_id,
        )
        s.add(row)
        await s.flush()
        await _set_claims(s, actor, row.id, [UUID(c) for c in keep[:MAX_CLAIMS]])
    return StartedAnswer(row.id, mentions, len(ids) - len(keep[:MAX_CLAIMS]))


async def use_submission_document(
    db: Database,
    actor: ActorContext,
    submission_id: UUID,
    document_id: UUID,
    application_id: UUID,
    *,
    target_document_id: UUID | None = None,
    base_revision: int | None = None,
    question_prompt: str | None = None,
    char_limit: int | None = None,
    dry_run: bool = False,
) -> PlaceResult:
    """제출본 문서의 문단을 다른 지원 문서로 옮긴다 (근거는 지금도 확인된 사실만, origin 그대로)."""
    async with db.workspace_session(actor.workspace_id) as s:
        _, _, d = await _submitted(s, submission_id, document_id)
        dropped = 0
        blocks = []
        for b in d.get("blocks") or []:
            text = (b.get("text") or "").strip()
            if not text:
                continue
            ids = list(b.get("claim_ids") or [])
            keep = await _still_confirmed(s, ids)
            dropped += len(ids) - len(keep)
            blocks.append(
                {
                    "id": new_block_id(),
                    "text": text[:MAX_BLOCK_CHARS],
                    "origin": b.get("origin", BlockOrigin.USER.value),
                    "claim_ids": keep,
                    "spans": [],
                }
            )
        result, _, _ = await _place(
            s,
            actor,
            application_id,
            blocks,
            document_id=target_document_id,
            base_revision=base_revision,
            title=d.get("title") or "지난 제출본",
            question_prompt=question_prompt,
            char_limit=char_limit,
            reason=f"지난 제출본 '{d.get('title') or ''}'에서 옮김",
            dry_run=dry_run,
        )
    return PlaceResult(
        result.application_id,
        result.document_id,
        result.revision_no,
        result.created_document,
        result.company_mentions,
        dropped,
    )


__all__ = [
    "AnswerKind",
    "AnswerRuleError",
    "AnswerSummary",
    "AnswerView",
    "PlaceResult",
    "StartedAnswer",
    "SubmittedDocument",
    "SubmittedDocumentView",
    "company_mentions",
    "create_answer",
    "delete_answer",
    "get_answer",
    "get_submitted_document",
    "list_answers",
    "list_submitted_documents",
    "start_answer_from_submission",
    "update_answer",
    "use_answer",
    "use_submission_document",
]

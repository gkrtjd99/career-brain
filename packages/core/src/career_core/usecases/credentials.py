"""자격·이수 기록과 증빙 서류함 (c0034, O20, plan §20).

- 기록은 사람이 직접 넣거나(바로 ``self_confirmed``, 증빙을 이으면 ``evidence_supported``), 증빙 원본의 글에서
  로컬 규칙으로 뽑은 후보(``extracted``)를 사람이 확인한다. 확인은 사람만 (``human_confirmation_required``).
- 뽑기는 모델·외부 OCR을 부르지 않는다 (``domain.credentials.extract_credential``). 증빙 원본이 읽기를 마치면
  파싱 worker가 같은 트랜잭션에서 한 번 부르고(``extract_in_session``), ``POST /sources/{id}/extract-credentials``
  로 다시 뽑을 수 있다. 다시 뽑으면 그 원본의 확인 전 후보를 새 후보로 바꾼다 (확인한 기록은 그대로).
- 뽑은 칸에는 원문 위치(``field_spans``)가 있다. 사람이 칸 값을 바꾸면 그 칸의 위치를 지운다.
- 번호는 보기마다 끝 4자리만 (``reveal``이면 원래 값, 권한 검사는 HTTP가 한다).
- 만료: 원문이나 사람이 적은 ``expires_on``만 쓴다. 계산하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from career_core.db.base import utcnow
from career_core.db.models import (
    Credential,
    DocumentBlock,
    Education,
    Experience,
    ParseRun,
    Source,
    SourceVersion,
)
from career_core.db.session import Database
from career_core.domain.credentials import (
    DATE_FIELDS,
    MAX_DETAIL,
    MAX_HOURS,
    MAX_ISSUER,
    MAX_NAME,
    MAX_NUMBER,
    MAX_SCORE,
    CredentialDraft,
    CredentialKind,
    CredentialRuleError,
    CredentialStatus,
    TextBlock,
    credential_kind,
    expiry_state,
    extract_credential,
    has_resident_number,
    mask_number,
)
from career_core.domain.sources import ParseStatus, SourcePurpose
from career_core.domain.study import local_date
from career_core.ports import ActorContext
from career_core.usecases.brain import NotFound
from career_core.usecases.workspace_settings import workspace_zone

LIST_LIMIT = 500
SOON_DAYS = 90  # "곧 만료" 표시
MAX_EXPIRING_WITHIN = 3650

_EDITABLE = (
    "kind",
    "name",
    "issuer",
    "number",
    "acquired_on",
    "expires_on",
    "started_on",
    "ended_on",
    "score",
    "hours",
    "detail",
    "source_id",
    "experience_id",
)
_LIMITS = {
    "name": MAX_NAME,
    "issuer": MAX_ISSUER,
    "number": MAX_NUMBER,
    "score": MAX_SCORE,
    "detail": MAX_DETAIL,
}


# ---------------------------------------------------------------- 보기


@dataclass(frozen=True, slots=True)
class CredentialView:
    id: UUID
    kind: str
    name: str
    issuer: str | None
    number: str | None  # 가린 값 (reveal이면 원래 값)
    number_masked: bool
    acquired_on: date | None
    expires_on: date | None
    started_on: date | None
    ended_on: date | None
    score: str | None
    hours: int | None
    detail: str | None
    status: str
    source_id: UUID | None
    source_title: str | None
    experience_id: UUID | None
    experience_title: str | None
    parse_run_id: UUID | None
    # 칸 → {"block": 순번, "start", "end", "page"?}: 증빙에서 뽑은 칸의 원문 위치 (글은 없다)
    field_spans: dict[str, dict]
    expiry: str | None  # expired | soon | None
    days_left: int | None  # 만료까지 남은 날 (지났으면 음수)
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _view(
    c: Credential,
    *,
    today: date,
    reveal: bool,
    sources: dict[UUID, str],
    experiences: dict[UUID, str],
) -> CredentialView:
    return CredentialView(
        id=c.id,
        kind=c.kind,
        name=c.name,
        issuer=c.issuer,
        number=c.number if reveal else mask_number(c.number),
        number_masked=bool(c.number) and not reveal,
        acquired_on=c.acquired_on,
        expires_on=c.expires_on,
        started_on=c.started_on,
        ended_on=c.ended_on,
        score=c.score,
        hours=c.hours,
        detail=c.detail,
        status=c.status,
        source_id=c.source_id,
        source_title=sources.get(c.source_id) if c.source_id else None,
        experience_id=c.experience_id,
        experience_title=experiences.get(c.experience_id) if c.experience_id else None,
        parse_run_id=c.parse_run_id,
        field_spans=dict(c.field_spans or {}),
        expiry=expiry_state(c.expires_on, today, SOON_DAYS),
        days_left=(c.expires_on - today).days if c.expires_on else None,
        confirmed_at=c.confirmed_at,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


async def _today(s: AsyncSession, tz: str | None, now: datetime | None) -> date:
    return local_date(now or utcnow(), await workspace_zone(s, tz))


async def _titles(s: AsyncSession, rows: list[Credential]) -> tuple[dict, dict]:
    src_ids = {c.source_id for c in rows if c.source_id}
    exp_ids = {c.experience_id for c in rows if c.experience_id}
    sources = (
        dict((await s.execute(select(Source.id, Source.title).where(Source.id.in_(src_ids)))).all())
        if src_ids
        else {}
    )
    exps = (
        dict(
            (
                await s.execute(
                    select(Experience.id, Experience.title).where(Experience.id.in_(exp_ids))
                )
            ).all()
        )
        if exp_ids
        else {}
    )
    return sources, exps


_KIND_ORDER = {k.value: i for i, k in enumerate(CredentialKind)}


async def list_credentials(
    db: Database,
    actor: ActorContext,
    *,
    kind: str | None = None,
    status: str | None = None,
    expiring_within: int | None = None,
    source_id: UUID | None = None,
    reveal: bool = False,
    tz: str | None = None,
    now: datetime | None = None,
) -> list[CredentialView]:
    """기록 목록. 확인 전 후보가 맨 위, 그다음 종류 순·최근 취득 순.

    ``expiring_within``: 만료일이 오늘부터 그 날 수 안인 것만 (오늘 화면 일정, 지난 것은 빼고 가까운 순).
    """
    if kind is not None:
        kind = credential_kind(kind).value
    if status is not None and status not in {s.value for s in CredentialStatus}:
        raise CredentialRuleError("invalid_status")
    async with db.workspace_session(actor.workspace_id) as s:
        today = await _today(s, tz, now)
        q = select(Credential)
        if kind is not None:
            q = q.where(Credential.kind == kind)
        if status is not None:
            q = q.where(Credential.status == status)
        if source_id is not None:
            q = q.where(Credential.source_id == source_id)
        if expiring_within is not None:
            days = max(0, min(expiring_within, MAX_EXPIRING_WITHIN))
            q = q.where(
                Credential.expires_on >= today,
                Credential.expires_on <= today + timedelta(days=days),
            ).order_by(Credential.expires_on)
        rows = list((await s.execute(q.limit(LIST_LIMIT))).scalars())
        sources, exps = await _titles(s, rows)
    if expiring_within is None:
        rows.sort(
            key=lambda c: (
                c.status != CredentialStatus.EXTRACTED,
                _KIND_ORDER.get(c.kind, 99),
                -(c.acquired_on or c.ended_on or date.min).toordinal(),
                c.name,
            )
        )
    return [_view(c, today=today, reveal=reveal, sources=sources, experiences=exps) for c in rows]


async def get_credential(
    db: Database,
    actor: ActorContext,
    credential_id: UUID,
    *,
    reveal: bool = False,
    tz: str | None = None,
    now: datetime | None = None,
) -> CredentialView:
    async with db.workspace_session(actor.workspace_id) as s:
        c = await s.get(Credential, credential_id)
        if c is None:
            raise NotFound(credential_id)
        today = await _today(s, tz, now)
        sources, exps = await _titles(s, [c])
    return _view(c, today=today, reveal=reveal, sources=sources, experiences=exps)


# ---------------------------------------------------------------- 쓰기


def _clean_text(name: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CredentialRuleError(f"invalid_credential_{name}")
    v = " ".join(value.split()) if name != "detail" else value.strip()
    if not v:
        return None
    if len(v) > _LIMITS[name]:
        raise CredentialRuleError(f"credential_{name}_too_long")
    return v


def _clean_values(values: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in values.items():
        if k not in _EDITABLE:
            raise CredentialRuleError("unknown_field")
        if k == "kind":
            out[k] = credential_kind(v).value
        elif k in _LIMITS:
            out[k] = _clean_text(k, v)
        elif k in DATE_FIELDS:
            if v is not None and not isinstance(v, date):
                raise CredentialRuleError(f"invalid_credential_{k}")
            out[k] = v
        elif k == "hours":
            if v is not None and (
                not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= MAX_HOURS
            ):
                raise CredentialRuleError("invalid_hours")
            out[k] = v
        else:
            out[k] = v  # source_id, experience_id (아래에서 확인)
    return out


def _check_dates(c: Credential) -> None:
    if c.expires_on and c.acquired_on and c.expires_on < c.acquired_on:
        raise CredentialRuleError("expires_before_acquired")
    if c.ended_on and c.started_on and c.ended_on < c.started_on:
        raise CredentialRuleError("ended_before_started")


async def _check_links(s: AsyncSession, values: dict[str, Any]) -> None:
    if values.get("source_id") is not None:
        purpose = await s.scalar(select(Source.purpose).where(Source.id == values["source_id"]))
        if purpose is None:
            raise CredentialRuleError("source_not_found")
        if purpose != SourcePurpose.PROOF:
            raise CredentialRuleError("source_not_proof")
    exp_id = values.get("experience_id")
    if exp_id is not None and await s.get(Experience, exp_id) is None:
        raise CredentialRuleError("experience_not_found")


def _confirmed_status(c: Credential) -> str:
    return (
        CredentialStatus.EVIDENCE_SUPPORTED.value
        if c.source_id
        else CredentialStatus.SELF_CONFIRMED.value
    )


async def create_credential(db: Database, actor: ActorContext, values: dict[str, Any]) -> UUID:
    """사람이 넣은 기록은 바로 확인된 것 (증빙을 이으면 evidence_supported). AI(agent)가 넣으면 후보."""
    values = _clean_values(values)
    if not values.get("name"):
        raise CredentialRuleError("credential_name_required")
    if "kind" not in values:
        raise CredentialRuleError("invalid_kind")
    now = utcnow()
    c = Credential(
        id=uuid4(),
        workspace_id=actor.workspace_id,
        created_by=actor.subject_id,
        field_spans={},
        **values,
    )
    if actor.is_human:
        c.status = _confirmed_status(c)
        c.confirmed_by, c.confirmed_at = actor.subject_id, now
    else:
        c.status = CredentialStatus.EXTRACTED.value
    _check_dates(c)
    async with db.workspace_session(actor.workspace_id) as s:
        await _check_links(s, values)
        s.add(c)
    return c.id


async def update_credential(
    db: Database, actor: ActorContext, credential_id: UUID, values: dict[str, Any]
) -> UUID:
    """준 칸만 바꾼다 (None은 비우기, 이름은 비울 수 없다). 값이 바뀐 칸은 원문 위치를 지운다.

    증빙을 떼면 ``evidence_supported`` → ``self_confirmed``, 확인된 기록에 증빙을 이으면 → ``evidence_supported``.
    """
    values = _clean_values(values)
    if "name" in values and not values["name"]:
        raise CredentialRuleError("credential_name_required")
    async with db.workspace_session(actor.workspace_id) as s:
        c = await s.get(Credential, credential_id, with_for_update=True)
        if c is None:
            raise NotFound(credential_id)
        await _check_links(s, {k: v for k, v in values.items() if v != getattr(c, k)})
        spans = dict(c.field_spans or {})
        for k, v in values.items():
            if v != getattr(c, k):
                setattr(c, k, v)
                spans.pop(k, None)
        c.field_spans = spans
        if c.status != CredentialStatus.EXTRACTED:
            c.status = _confirmed_status(c)
        _check_dates(c)
        c.updated_at = utcnow()
    return credential_id


async def confirm_credential(db: Database, actor: ActorContext, credential_id: UUID) -> str:
    """후보를 확인한다 (사람만). 증빙이 이어져 있으면 evidence_supported."""
    if not actor.is_human:
        raise CredentialRuleError("human_confirmation_required")
    async with db.workspace_session(actor.workspace_id) as s:
        c = await s.get(Credential, credential_id, with_for_update=True)
        if c is None:
            raise NotFound(credential_id)
        if c.status == CredentialStatus.EXTRACTED:
            c.status = _confirmed_status(c)
            c.confirmed_by, c.confirmed_at = actor.subject_id, utcnow()
            c.updated_at = utcnow()
        return c.status


async def delete_credential(db: Database, actor: ActorContext, credential_id: UUID) -> None:
    async with db.workspace_session(actor.workspace_id) as s:
        c = await s.get(Credential, credential_id)
        if c is None:
            raise NotFound(credential_id)
        await s.delete(c)


# ---------------------------------------------------------------- 증빙에서 뽑기


@dataclass(frozen=True, slots=True)
class ExtractResult:
    source_id: UUID
    # 만든 후보 (없으면 None)
    credential_id: UUID | None
    # 왜 없나: not_parsed (읽기 전·실패) | no_text (읽은 글 없음, 사진 등) | nothing_found | already_recorded
    reason: str | None
    replaced: int = 0  # 바꾼 옛 후보 수
    fields: list[str] = field(default_factory=list)  # 채운 칸


async def _latest_run(s: AsyncSession, source_id: UUID) -> ParseRun | None:
    return await s.scalar(
        select(ParseRun)
        .join(SourceVersion, SourceVersion.id == ParseRun.source_version_id)
        .where(
            SourceVersion.source_id == source_id,
            ParseRun.status.in_([ParseStatus.COMPLETE, ParseStatus.PARTIAL]),
        )
        .order_by(
            SourceVersion.created_at.desc(),
            ParseRun.pipeline_version.desc(),
            ParseRun.created_at.desc(),
        )
        .limit(1)
    )


async def _blocks(s: AsyncSession, run_id: UUID) -> list[TextBlock]:
    rows = (
        await s.execute(
            select(DocumentBlock.ordinal, DocumentBlock.text, DocumentBlock.page)
            .where(DocumentBlock.parse_run_id == run_id)
            .order_by(DocumentBlock.ordinal)
        )
    ).all()
    return [TextBlock(r.ordinal, r.text, r.page) for r in rows]


def _draft_values(draft: CredentialDraft) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": draft.kind.value}
    for name, fv in draft.fields.items():
        v = fv.value
        if isinstance(v, str) and name in _LIMITS:
            v = v[: _LIMITS[name]]
        out[name] = v
    return out


async def extract_in_session(
    s: AsyncSession, workspace_id: UUID, source_id: UUID, requested_by: UUID
) -> ExtractResult:
    """같은 트랜잭션 안에서 증빙 원본 하나를 뽑는다 (파싱 worker·HTTP가 같이 쓴다). 모델 호출 없음."""
    src = await s.get(Source, source_id)
    if src is None:
        raise NotFound(source_id)
    if src.purpose != SourcePurpose.PROOF:
        raise CredentialRuleError("source_not_proof")
    run = await _latest_run(s, source_id)
    if run is None:
        return ExtractResult(source_id, None, "not_parsed")
    blocks = await _blocks(s, run.id)
    if not blocks:
        return ExtractResult(source_id, None, "no_text")
    # 이 원본의 옛 후보는 새로 뽑은 것으로 바꾼다 (확인한 기록은 건드리지 않는다)
    replaced = (
        await s.execute(
            delete(Credential).where(
                Credential.source_id == source_id,
                Credential.status == CredentialStatus.EXTRACTED,
            )
        )
    ).rowcount
    draft = extract_credential(blocks)
    if draft is None:
        return ExtractResult(source_id, None, "nothing_found", replaced)
    values = _draft_values(draft)
    same = await s.scalar(
        select(func.count())
        .select_from(Credential)
        .where(
            Credential.source_id == source_id,
            Credential.kind == values["kind"],
            Credential.name == values["name"],
        )
    )
    if not same and values["kind"] == CredentialKind.DEGREE:
        # 학력으로 옮긴 증빙(c0040)은 다시 뽑아도 학력 후보를 또 만들지 않는다
        same = await s.scalar(
            select(func.count()).select_from(Education).where(Education.source_id == source_id)
        )
    if same:
        return ExtractResult(source_id, None, "already_recorded", replaced)
    c = Credential(
        id=uuid4(),
        workspace_id=workspace_id,
        status=CredentialStatus.EXTRACTED.value,
        source_id=source_id,
        parse_run_id=run.id,
        field_spans={name: fv.span.as_dict() for name, fv in draft.fields.items()},
        created_by=requested_by,
        **values,
    )
    try:
        _check_dates(c)
    except CredentialRuleError:  # 뽑기가 이미 거르지만, 남으면 날짜 칸을 비운다
        for k in DATE_FIELDS:
            setattr(c, k, None)
            c.field_spans.pop(k, None)
    s.add(c)
    await s.flush()
    return ExtractResult(source_id, c.id, None, replaced, sorted(draft.fields))


async def extract_from_source(db: Database, actor: ActorContext, source_id: UUID) -> ExtractResult:
    async with db.workspace_session(actor.workspace_id) as s:
        return await extract_in_session(s, actor.workspace_id, source_id, actor.subject_id)


# ---------------------------------------------------------------- 증빙 서류함


@dataclass(frozen=True, slots=True)
class CredentialBrief:
    id: UUID
    kind: str
    name: str
    status: str
    expires_on: date | None


@dataclass(frozen=True, slots=True)
class ProofItem:
    id: UUID
    title: str
    created_at: datetime
    original_filename: str | None
    mime_type: str | None
    size_bytes: int | None
    sha256: str | None
    parse_status: str | None
    parse_error: str | None
    # 주민등록번호로 보이는 값이 읽은 글에 있다 (값은 돌려주지 않는다)
    resident_number: bool
    credentials: list[CredentialBrief]
    candidates: int  # 그중 확인 전 후보 수


async def proof_flags(s: AsyncSession, source_ids: list[UUID]) -> dict[UUID, bool]:
    """원본마다 최신 읽기 결과에 주민등록번호로 보이는 값이 있나."""
    out: dict[UUID, bool] = {}
    for sid in source_ids:
        run = await _latest_run(s, sid)
        # c0039: 이 기기·모델이 읽은 글은 주민등록번호를 가린 뒤 들어온다 — 가린 수로 표시한다
        masked = bool(run and run.text_layer and run.text_layer.get("masked_resident_numbers"))
        out[sid] = bool(run) and (masked or has_resident_number(await _blocks(s, run.id)))
    return out


async def list_proofs(
    db: Database, actor: ActorContext, *, source_id: UUID | None = None
) -> list[ProofItem]:
    """증빙 원본 목록 (최근 것부터): 파일 정보, 읽기 상태, 민감 정보 표시, 이어진 기록."""
    async with db.workspace_session(actor.workspace_id) as s:
        q = select(Source).where(Source.purpose == SourcePurpose.PROOF)
        if source_id is not None:
            q = q.where(Source.id == source_id)
        sources = list(
            (
                await s.execute(q.order_by(Source.created_at.desc(), Source.id).limit(LIST_LIMIT))
            ).scalars()
        )
        ids = [x.id for x in sources]
        latest: dict[UUID, SourceVersion] = {}
        runs: dict[UUID, ParseRun] = {}
        creds: dict[UUID, list[Credential]] = {}
        if ids:
            for v in (
                await s.execute(
                    select(SourceVersion)
                    .where(SourceVersion.source_id.in_(ids))
                    .order_by(SourceVersion.created_at, SourceVersion.id)
                )
            ).scalars():
                latest[v.source_id] = v
            for r in (
                await s.execute(
                    select(ParseRun)
                    .where(ParseRun.source_version_id.in_([v.id for v in latest.values()]))
                    .order_by(ParseRun.pipeline_version, ParseRun.created_at)
                )
            ).scalars():
                runs[r.source_version_id] = r
            for c in (
                await s.execute(
                    select(Credential)
                    .where(Credential.source_id.in_(ids))
                    .order_by(Credential.created_at)
                )
            ).scalars():
                creds.setdefault(c.source_id, []).append(c)
        flags = await proof_flags(s, ids)
    out = []
    for src in sources:
        v = latest.get(src.id)
        r = runs.get(v.id) if v else None
        cs = creds.get(src.id, [])
        out.append(
            ProofItem(
                id=src.id,
                title=src.title,
                created_at=src.created_at,
                original_filename=v.original_filename if v else None,
                mime_type=v.mime_type if v else None,
                size_bytes=v.size_bytes if v else None,
                sha256=v.sha256 if v else None,
                parse_status=r.status if r else None,
                parse_error=r.error_code if r else None,
                resident_number=flags.get(src.id, False),
                credentials=[
                    CredentialBrief(c.id, c.kind, c.name, c.status, c.expires_on) for c in cs
                ],
                candidates=sum(c.status == CredentialStatus.EXTRACTED for c in cs),
            )
        )
    return out


# ---------------------------------------------------------------- 원본 내려받기


@dataclass(frozen=True, slots=True)
class OriginalFile:
    source_id: UUID
    version_id: UUID
    data: bytes
    mime_type: str
    filename: str
    sha256: str
    data_class: str
    purpose: str


class OriginalChanged(RuntimeError):
    """저장소의 바이트가 기록한 해시와 다르다 (보내지 않는다)."""


async def read_original(
    db: Database, blobs, actor: ActorContext, source_id: UUID, *, version_id: UUID | None = None
) -> OriginalFile:
    """원본 바이트 (기본은 최신 버전). 기록한 sha256과 같을 때만 돌려준다."""
    import hashlib

    async with db.workspace_session(actor.workspace_id) as s:
        src = await s.get(Source, source_id)
        if src is None:
            raise NotFound(source_id)
        q = select(SourceVersion).where(SourceVersion.source_id == source_id)
        if version_id is not None:
            q = q.where(SourceVersion.id == version_id)
        v = await s.scalar(q.order_by(SourceVersion.created_at.desc()).limit(1))
        if v is None:
            raise NotFound(version_id or source_id)
    data = await blobs.get(actor.workspace_id, v.blob_key)
    if hashlib.sha256(data).hexdigest() != v.sha256:
        raise OriginalChanged(str(v.id))
    return OriginalFile(
        src.id, v.id, data, v.mime_type, v.original_filename, v.sha256, src.data_class, src.purpose
    )


__all__ = [
    "CredentialView",
    "ExtractResult",
    "OriginalChanged",
    "OriginalFile",
    "ProofItem",
    "confirm_credential",
    "create_credential",
    "delete_credential",
    "extract_from_source",
    "extract_in_session",
    "get_credential",
    "list_credentials",
    "list_proofs",
    "proof_flags",
    "read_original",
    "update_credential",
]

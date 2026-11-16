"""공용 문서·지원 문서로 복사·문서 설정(상투어·본인 연락처)·내보내기 (c0012).

- 공용 문서는 ``application_documents.application_id IS NULL``인 문서다. revision·근거 연결·수정안
  테이블과 규칙(base_revision/409, 확인된 주장만 근거, AI 글은 근거 아님)을 지원 문서와 그대로 같이 쓴다.
  편집·조회 usecase는 ``usecases.applications``의 같은 함수에 ``application_id=None``을 넘긴다.
- 복사는 원본 문서의 한 revision을 지원 문서의 revision 1로 옮긴다. 복사본은 출처(원본 문서 ID·revision ID)를
  기억하고, 이후 원본이 바뀌어도 복사본은 바뀌지 않는다 (반대도 같다).
- 상투어 목록은 공간 설정(``document_settings.cliches``)에 저장한다. 비어 있으면(NULL) 기본 목록.
  검사 요청의 ``extra_cliches``는 계속 받는다(설정 목록에 더한다).
- 내보내기(md·docx·인쇄용 html)는 만든 파일을 다시 읽어 숨김 글·조작 문구를 검사하고 경고를 같이 준다.
- 제출본 내보내기(c0016)는 불변 스냅샷(``application_submissions``)의 문서들을 같은 writer로 한 파일에 쓴다.
  지금 문서·주장이 아니라 제출 당시 복사본만 쓴다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from career_core.db import Database
from career_core.db.models import Application, ApplicationDocument, DocumentSettings
from career_core.domain.document_export import (
    FORMATS,
    MEDIA_TYPES,
    ExportError,
    ExportSection,
    export_filename,
    export_summary,
    inspect_export,
    render,
)
from career_core.domain.documents import (
    DEFAULT_CLICHES,
    CountRule,
    DocumentRuleError,
    Finding,
)
from career_core.domain.sensitive import sensitive_findings
from career_core.ports import ActorContext
from career_core.usecases import applications as app_uc
from career_core.usecases.brain import NotFound

MAX_CLICHES = 300
MAX_CLICHE_CHARS = 40
MAX_CONTACTS = 10


# ---------------------------------------------------------------- 공용 문서


@dataclass(frozen=True, slots=True)
class SharedDocumentSummary:
    id: UUID
    kind: str
    title: str
    char_limit: int | None
    byte_limit: int | None
    current_revision: int
    counts: dict | None
    # 이 문서를 복사해 간 지원 문서 수
    copies: int
    created_at: datetime
    updated_at: datetime


async def create_shared_document(
    db: Database,
    actor: ActorContext,
    *,
    kind: str,
    title: str,
    char_limit: int | None = None,
    byte_limit: int | None = None,
    count_rule: dict | None = None,
) -> UUID:
    """공간 공용 문서 (포트폴리오·기본 이력서 등). 공고 문항(question)은 만들 수 없다."""
    return await app_uc.add_document(
        db,
        actor,
        None,
        kind=kind,
        title=title,
        char_limit=char_limit,
        byte_limit=byte_limit,
        count_rule=count_rule,
    )


async def list_shared_documents(db: Database, actor: ActorContext) -> list[SharedDocumentSummary]:
    async with db.workspace_session(actor.workspace_id) as s:
        docs = (
            (
                await s.execute(
                    select(ApplicationDocument)
                    .where(ApplicationDocument.application_id.is_(None))
                    .order_by(ApplicationDocument.updated_at.desc())
                )
            )
            .scalars()
            .all()
        )
        copies: dict[UUID, int] = {}
        for (src,) in (
            await s.execute(
                select(ApplicationDocument.source_document_id).where(
                    ApplicationDocument.source_document_id.in_([d.id for d in docs])
                )
            )
        ).all():
            copies[src] = copies.get(src, 0) + 1
        out = []
        for d in docs:
            rev = await app_uc._revision(s, d.id, d.current_revision)
            out.append(
                SharedDocumentSummary(
                    d.id,
                    d.kind,
                    d.title,
                    d.char_limit,
                    d.byte_limit,
                    d.current_revision,
                    app_uc._counts(d, rev.blocks).as_dict() if rev else None,
                    copies.get(d.id, 0),
                    d.created_at,
                    d.updated_at,
                )
            )
    return out


async def copy_to_application(
    db: Database,
    actor: ActorContext,
    application_id: UUID,
    source_document_id: UUID,
    *,
    revision: int | None = None,
    title: str | None = None,
) -> UUID:
    """공용 문서의 revision(기본 최신)을 지원 문서로 복사한다. 복사본 revision 1 = 원본 블록·근거 연결 그대로.

    출처(원본 문서·revision)는 복사본에 남는다. 근거 연결은 지금도 공간에 있는 주장만 옮긴다(규칙 동일).
    블록 origin(user/ai)도 그대로 둔다 — AI가 고친 문단을 복사해도 AI 비율이 사라지지 않는다.
    """
    async with db.workspace_session(actor.workspace_id) as s:
        app = await app_uc._get_app(s, application_id, lock=True)
        app_uc._ensure_editable(app)
        _, src = await app_uc._get_doc(s, None, source_document_id)
        no = src.current_revision if revision is None else revision
        rev = await app_uc._revision(s, src.id, no)
        if rev is None:
            raise DocumentRuleError("nothing_to_copy") if no == 0 else NotFound("revision")
        blocks = [dict(b) for b in rev.blocks]
        await app_uc._ensure_claims(s, blocks)
        doc = ApplicationDocument(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            application_id=app.id,
            kind=src.kind,
            title=app_uc._title(title or src.title),
            char_limit=src.char_limit,
            byte_limit=src.byte_limit,
            count_rule=CountRule.parse(src.count_rule).as_dict(),
            source_document_id=src.id,
            source_revision_id=rev.id,
            created_by=actor.subject_id,
        )
        s.add(doc)
        await s.flush()
        await app_uc._insert_revision(
            s,
            actor,
            doc,
            blocks,
            reason=f"공용 문서 '{src.title}' r{rev.revision_no}에서 복사",
        )
    return doc.id


# ---------------------------------------------------------------- 문서 설정


@dataclass(frozen=True, slots=True)
class DocumentSettingsView:
    cliches: list[str]
    customized: bool  # False면 기본 목록
    default_cliches: list[str]
    own_contacts: dict[str, list[str]]
    updated_at: datetime | None


def _clean_cliches(values: Sequence[Any]) -> list[str]:
    out = []
    for v in values:
        if not isinstance(v, str):
            raise DocumentRuleError("invalid_cliches")
        v = " ".join(v.split())
        if not v:
            continue
        if len(v) > MAX_CLICHE_CHARS:
            raise DocumentRuleError("invalid_cliches")
        out.append(v)
    out = list(dict.fromkeys(out))
    if len(out) > MAX_CLICHES:
        raise DocumentRuleError("too_many_cliches")
    return out


def _clean_contacts(data: Any) -> dict[str, list[str]]:
    if not isinstance(data, dict) or set(data) - {"emails", "phones"}:
        raise DocumentRuleError("invalid_contacts")
    out: dict[str, list[str]] = {}
    for key in ("emails", "phones"):
        items = data.get(key) or []
        if not isinstance(items, list) or len(items) > MAX_CONTACTS:
            raise DocumentRuleError("invalid_contacts")
        if not all(isinstance(i, str) and 0 < len(i.strip()) <= 200 for i in items):
            raise DocumentRuleError("invalid_contacts")
        out[key] = list(dict.fromkeys(i.strip() for i in items))
    return out


def _settings_view(row: DocumentSettings | None) -> DocumentSettingsView:
    custom = row is not None and row.cliches is not None
    raw = (row.own_contacts if row else None) or {}
    return DocumentSettingsView(
        cliches=list(row.cliches) if custom else list(DEFAULT_CLICHES),
        customized=custom,
        default_cliches=list(DEFAULT_CLICHES),
        # 사용자가 적은 그대로 보여 준다 (비교는 숫자만·소문자로 정규화해서 한다)
        own_contacts={
            "emails": list(raw.get("emails") or []),
            "phones": list(raw.get("phones") or []),
        },
        updated_at=row.updated_at if row else None,
    )


async def get_settings(db: Database, actor: ActorContext) -> DocumentSettingsView:
    async with db.workspace_session(actor.workspace_id) as s:
        row = await s.scalar(select(DocumentSettings))
    return _settings_view(row)


_UNSET: Any = object()


async def update_settings(
    db: Database,
    actor: ActorContext,
    *,
    cliches: Sequence[str] | None = _UNSET,
    own_contacts: dict | None = _UNSET,
) -> DocumentSettingsView:
    """상투어 목록(전체 교체, None이면 기본 목록으로)과 본인 연락처를 바꾼다. 준 칸만 바꾼다."""
    async with db.workspace_session(actor.workspace_id) as s:
        row = await s.scalar(select(DocumentSettings).with_for_update())
        if row is None:
            row = DocumentSettings(
                id=uuid4(),
                workspace_id=actor.workspace_id,
                cliches=None,
                own_contacts={},
                updated_by=actor.subject_id,
            )
            s.add(row)
        if cliches is not _UNSET:
            row.cliches = None if cliches is None else _clean_cliches(cliches)
        if own_contacts is not _UNSET:
            row.own_contacts = {} if own_contacts is None else _clean_contacts(own_contacts)
        row.updated_by = actor.subject_id
        await s.flush()
        await s.refresh(row)
        view = _settings_view(row)
    return view


# ---------------------------------------------------------------- 내보내기


@dataclass(frozen=True, slots=True)
class ExportResult:
    filename: str
    media_type: str
    data: bytes
    format: str
    revision: int
    # 만든 파일 검사(숨김 글·조작 문구) + 민감 정보 경고. 경고만 한다.
    findings: list[dict]
    summary: dict[str, int]


async def export_document(
    db: Database,
    actor: ActorContext,
    application_id: UUID | None,
    document_id: UUID,
    *,
    fmt: str,
    revision: int | None = None,
) -> ExportResult:
    """문서 한 revision을 파일로 만든다 (md·docx·html). 만든 파일을 다시 읽어 검사한 결과를 같이 준다."""
    if fmt not in FORMATS:
        raise DocumentRuleError("invalid_format")
    view = await app_uc.get_document(db, actor, application_id, document_id, revision=revision)
    if view.revision_no == 0:
        raise DocumentRuleError("nothing_to_export")
    async with db.workspace_session(actor.workspace_id) as s:
        own = await app_uc.own_contacts(s)
    paragraphs = [b.text for b in view.blocks]
    data = render(fmt, view.title, paragraphs)
    try:
        found: list[Finding] = inspect_export(fmt, data)
    except ExportError as exc:
        raise DocumentRuleError(exc.code) from exc
    for b in view.blocks:
        found.extend(sensitive_findings(b.text, own=own, block_id=b.id))
    return ExportResult(
        filename=export_filename(view.title, fmt),
        media_type=MEDIA_TYPES[fmt],
        data=data,
        format=fmt,
        revision=view.revision_no,
        findings=[f.as_dict() for f in found],
        summary=export_summary(found),
    )


def _submission_sections(documents: Sequence[dict]) -> list[ExportSection]:
    out = []
    for d in documents:
        paragraphs = tuple(b.get("text", "") for b in d.get("blocks") or [] if b.get("text"))
        note = d.get("question_prompt")
        out.append(
            ExportSection(
                paragraphs,
                heading=d.get("title") or None,
                note=f"문항: {note}" if note else None,
            )
        )
    return out


async def export_submission(
    db: Database, actor: ActorContext, application_id: UUID, *, fmt: str
) -> ExportResult:
    """제출본(불변 스냅샷)을 파일로 (md·docx·html). 문서 내보내기와 같은 writer·같은 검사."""
    if fmt not in FORMATS:
        raise DocumentRuleError("invalid_format")
    sub = await app_uc.get_submission(db, actor, application_id)
    async with db.workspace_session(actor.workspace_id) as s:
        app = await s.get(Application, application_id)
        own = await app_uc.own_contacts(s)
    title = (app.title if app is not None else None) or "제출본"
    stamp = sub.submitted_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    meta = ExportSection((f"제출본 · {stamp} 고정 · 내용 해시 {sub.content_hash[:12]}",))
    sections = [meta, *_submission_sections(sub.documents)]
    data = render(fmt, title, sections=sections)
    try:
        found: list[Finding] = inspect_export(fmt, data)
    except ExportError as exc:
        raise DocumentRuleError(exc.code) from exc
    for d in sub.documents:
        for b in d.get("blocks") or []:
            found.extend(sensitive_findings(b.get("text", ""), own=own, block_id=b.get("id")))
    return ExportResult(
        filename=export_filename(f"{title} 제출본", fmt),
        media_type=MEDIA_TYPES[fmt],
        data=data,
        format=fmt,
        revision=0,
        findings=[f.as_dict() for f in found],
        summary=export_summary(found),
    )


__all__ = [
    "DocumentSettingsView",
    "ExportResult",
    "SharedDocumentSummary",
    "copy_to_application",
    "create_shared_document",
    "export_document",
    "export_submission",
    "get_settings",
    "list_shared_documents",
    "update_settings",
]

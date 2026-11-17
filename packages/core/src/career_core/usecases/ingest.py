"""브레인에 넣기 (plan §9, §20): 넣은 것을 고르고, 이 기기에서 읽은 글을 원본에 붙인다.

- ``classify_items``: 파일·글·주소마다 무엇인지 (``domain.ingest``, 모델 없음). 회사 주소면 이 공간의 회사 중
  홈페이지 호스트나 이름이 같은 것을 찾아 준다.
- ``attach_text_layer``: 브라우저(tesseract.js)가 읽은 글을 원본의 읽기 결과에 ``device_ocr`` 블록으로 붙인다.
  주민등록번호 모양은 브라우저가 가린 뒤 보내고 여기서도 한 번 더 가린다. 원본 바이트는 그대로다.
  읽기가 이미 끝났으면 상태를 다시 정하고(``parsing.settle``) 증빙이면 자격·이수 후보를 다시 뽑는다.
  아직 읽는 중이면 worker가 끝낼 때 이 글을 본다 — 그 쪽을 외부 OCR·모델로 보내지 않는다.
- ``capabilities``: 사진을 연결된 모델로 읽을 수 있는지 (자료 등급별). 증빙은 늘 이 기기에서 읽는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, func, select

from career_core.db.base import utcnow
from career_core.db.models import Company, DocumentBlock, ParseRun, Source, SourceVersion
from career_core.db.session import Database
from career_core.domain.companies import name_key
from career_core.domain.ingest import (
    TextLayer,
    TextLayerError,
    check_text_layer,
    classify_file,
    classify_url,
    normalize_host,
)
from career_core.domain.sources import (
    PIPELINE_VERSION,
    TERMINAL_PARSE_STATUSES,
    DataClass,
    ParseStatus,
)
from career_core.domain.text_clean import clean_control
from career_core.ports import ActorContext
from career_core.usecases import parsing

MAX_CLASSIFY_ITEMS = 100


class IngestNotFound(LookupError):
    pass


# ---------------------------------------------------------------- 고르기


@dataclass(frozen=True, slots=True)
class ClassifyIn:
    key: str
    filename: str | None = None
    mime: str | None = None
    text_head: str | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class ClassifyOut:
    key: str
    # experience | proof | reference | posting | company
    kind: str
    confidence: str
    reason: str
    resident_number: bool = False
    host: str | None = None
    company_hint: str | None = None
    company_id: UUID | None = None
    company_name: str | None = None


async def _companies(db: Database, actor: ActorContext) -> list[tuple[UUID, str, str, str]]:
    async with db.workspace_session(actor.workspace_id) as s:
        rows = (
            await s.execute(select(Company.id, Company.name, Company.name_key, Company.homepage))
        ).all()
    return [
        (r.id, r.name, r.name_key, normalize_host(r.homepage) if r.homepage else "") for r in rows
    ]


async def classify_items(
    db: Database, actor: ActorContext, items: list[ClassifyIn]
) -> list[ClassifyOut]:
    if len(items) > MAX_CLASSIFY_ITEMS:
        raise TextLayerError("too_many_items")
    companies: list[tuple[UUID, str, str, str]] | None = None
    out: list[ClassifyOut] = []
    for it in items:
        if it.url:
            g = classify_url(it.url)
            cid = cname = None
            if g.kind == "company" or g.company_hint:
                if companies is None:
                    companies = await _companies(db, actor)
                hint_key = name_key(g.company_hint) if g.company_hint else ""
                for c_id, c_name, c_key, c_host in companies:
                    same_host = c_host and (g.host == c_host or g.host.endswith("." + c_host))
                    if same_host or (hint_key and c_key == hint_key):
                        cid, cname = c_id, c_name
                        break
            out.append(
                ClassifyOut(
                    it.key,
                    g.kind,
                    g.confidence,
                    g.reason,
                    host=g.host,
                    company_hint=g.company_hint,
                    company_id=cid,
                    company_name=cname,
                )
            )
        else:
            f = classify_file(it.filename, it.mime, it.text_head)
            out.append(
                ClassifyOut(
                    it.key,
                    f.purpose.value,
                    f.confidence,
                    f.reason,
                    resident_number=f.resident_number,
                )
            )
    return out


# ---------------------------------------------------------------- 읽을 수 있는 길


@dataclass(frozen=True, slots=True)
class Capabilities:
    # 사진을 연결된 비전 모델로 읽을 수 있는 자료 등급 (증빙·restricted는 넣지 않는다)
    model_ocr_classes: tuple[str, ...]
    model_ocr_route: str | None  # "연결/모델" (보여 주기용)


async def capabilities(gateway, actor: ActorContext) -> Capabilities:
    from career_core.llm.gateway import scoped_gateway

    gw = await scoped_gateway(gateway, actor.workspace_id)
    picked = parsing.model_ocr_route(gw)
    if picked is None:
        return Capabilities((), None)
    purpose, route = picked
    endpoint = gw.config.endpoints.get(route.endpoint)
    if endpoint is None:
        return Capabilities((), None)
    classes = []
    for dc in (DataClass.PUBLIC, DataClass.PRIVATE):
        if not endpoint.leaves_host or gw.allows_data_class(
            actor, endpoint.name, dc.value, purpose
        ):
            classes.append(dc.value)
    return Capabilities(tuple(classes), f"{route.endpoint}/{route.model}")


# ---------------------------------------------------------------- 이 기기에서 읽은 글


@dataclass(frozen=True, slots=True)
class TextLayerResult:
    source_id: UUID
    parse_run_id: UUID
    blocks: int
    masked: int
    parse_status: str
    missing_pages: list[int]


async def _store(s, ws: UUID, run: ParseRun, layer: TextLayer, *, settle: bool) -> int:
    """이 기기의 블록을 바꿔 넣는다. 돌려주는 값 = 넣은 블록 수."""
    await s.execute(
        delete(DocumentBlock).where(
            DocumentBlock.parse_run_id == run.id, DocumentBlock.origin == parsing.DEVICE_ORIGIN
        )
    )
    local_pages = set(
        await s.scalars(
            select(DocumentBlock.page)
            .where(
                DocumentBlock.parse_run_id == run.id,
                DocumentBlock.origin == parsing.LOCAL_ORIGIN,
                DocumentBlock.page.is_not(None),
            )
            .distinct()
        )
    )
    # 이 서버가 이미 글자를 읽은 쪽(텍스트 PDF)은 OCR 글로 덮지 않는다
    blocks = [b for b in layer.blocks if b.page not in local_pages]
    if not blocks:
        raise TextLayerError("already_has_text")
    start = await s.scalar(
        select(func.coalesce(func.max(DocumentBlock.ordinal), -1) + 1).where(
            DocumentBlock.parse_run_id == run.id
        )
    )
    for i, b in enumerate(blocks):
        s.add(
            DocumentBlock(
                workspace_id=ws,
                parse_run_id=run.id,
                page=b.page,
                ordinal=start + i,
                origin=parsing.DEVICE_ORIGIN,
                text=clean_control(b.text),  # 길이는 그대로 (좌표가 어긋나지 않게)
                bbox=list(b.bbox) if b.bbox else None,
            )
        )
    run.text_layer = {
        "origin": "device",
        "engine": layer.engine,
        "pages": layer.pages_total,
        "blocks": len(blocks),
        "chars": sum(len(b.text) for b in blocks),
        "masked_resident_numbers": layer.masked,
        "at": utcnow().isoformat(),
    }
    await s.flush()
    if settle:
        await parsing.settle(s, ws, run, layer.pages_total)
    return len(blocks)


async def store_with_new_run(s, ws: UUID, run: ParseRun, layer: TextLayer, mime: str) -> int:
    """올리기와 같은 트랜잭션에서 (아직 읽기 전). worker가 끝낼 때 이 글을 본다."""
    layer = check_text_layer(layer, mime=mime)
    return await _store(s, ws, run, layer, settle=False)


async def attach_text_layer(
    db: Database,
    actor: ActorContext,
    source_id: UUID,
    layer: TextLayer,
    *,
    version_id: UUID | None = None,
) -> TextLayerResult:
    """원본(최신 버전 또는 ``version_id``)의 읽기 결과에 이 기기에서 읽은 글을 붙인다.

    오류: 원본·버전 없음(다른 공간 포함) → ``IngestNotFound``. 형식이 글자 있는 것(텍스트·DOCX·HTML) →
    ``text_layer_not_needed``. 텍스트 PDF가 아직 읽는 중이면 ``source_not_ready``(로컬 블록과 순번이 겹치지
    않게 — 사진은 로컬 블록이 없어 읽기 전에도 받는다). 모든 쪽이 이미 글자로 읽혔으면 ``already_has_text``.
    """
    ws = actor.workspace_id
    async with db.workspace_session(ws) as s:
        src = await s.get(Source, source_id)
        if src is None:
            raise IngestNotFound(source_id)
        q = select(SourceVersion).where(SourceVersion.source_id == source_id)
        if version_id is not None:
            q = q.where(SourceVersion.id == version_id)
        version = await s.scalar(
            q.order_by(SourceVersion.created_at.desc(), SourceVersion.id).limit(1)
        )
        if version is None:
            raise IngestNotFound(version_id or source_id)
        checked = check_text_layer(layer, mime=version.mime_type)
        run = await s.scalar(
            select(ParseRun)
            .where(
                ParseRun.source_version_id == version.id,
                ParseRun.pipeline_version == PIPELINE_VERSION,
            )
            .with_for_update()
        )
        if run is None:
            raise IngestNotFound(version.id)
        terminal = run.status in TERMINAL_PARSE_STATUSES
        if not terminal and not version.mime_type.startswith("image/"):
            raise TextLayerError("source_not_ready")
        n = await _store(s, ws, run, checked, settle=terminal)
        return TextLayerResult(
            source_id,
            run.id,
            n,
            checked.masked,
            run.status if terminal else ParseStatus.PENDING.value,
            list(run.missing_pages or []),
        )


__all__ = [
    "MAX_CLASSIFY_ITEMS",
    "Capabilities",
    "ClassifyIn",
    "ClassifyOut",
    "IngestNotFound",
    "TextLayerResult",
    "attach_text_layer",
    "capabilities",
    "classify_items",
    "store_with_new_run",
]

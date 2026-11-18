"""브레인에 넣기 (c0039, plan §9·§20).

- ``POST /ingest/classify``: 파일·글·주소마다 무엇인지 (모델 없음, 넣은 것을 밖으로 보내지 않는다).
- ``GET /ingest/capabilities``: 사진을 연결된 모델로 읽을 수 있는 자료 등급. c0052: 넣으면 바로 정리하는지
  (``auto_extract``: on · off · no_permission · no_model · egress_denied · api_key_missing).
- ``PUT /sources/{id}/text-layer``: 이 기기(브라우저)에서 읽은 글을 원본에 붙인다.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from career_core.domain.ingest import (
    MAX_BBOX,
    MAX_BLOCK_CHARS,
    MAX_LAYER_BLOCKS,
    MAX_LAYER_PAGES,
    LayerBlock,
    TextLayer,
    TextLayerError,
)
from career_core.domain.sources import SUGGEST_TEXT_HEAD
from career_core.ports import ActorContext
from career_core.usecases import ingest as ingest_uc

_STATUS = {
    "source_not_ready": status.HTTP_409_CONFLICT,
    "already_has_text": status.HTTP_409_CONFLICT,
}


class ClassifyItemIn(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    filename: str | None = Field(default=None, max_length=500)
    mime: str | None = Field(default=None, max_length=200)
    text_head: str | None = Field(default=None, max_length=SUGGEST_TEXT_HEAD * 2)
    url: str | None = Field(default=None, max_length=2000, pattern=r"^https?://")


class ClassifyIn(BaseModel):
    items: list[ClassifyItemIn] = Field(max_length=ingest_uc.MAX_CLASSIFY_ITEMS)


class ClassifyItemOut(BaseModel):
    key: str
    kind: str
    confidence: str
    reason: str
    resident_number: bool
    host: str | None
    company_hint: str | None
    company_id: UUID | None
    company_name: str | None


class CapabilitiesOut(BaseModel):
    # 사진을 연결된 모델로 읽을 수 있는 자료 등급 (증빙은 늘 이 기기에서)
    model_ocr_classes: list[str]
    model_ocr_route: str | None
    auto_extract: str = "off"


class LayerBlockIn(BaseModel):
    page: int = Field(default=1, ge=1, le=MAX_LAYER_PAGES)
    text: str = Field(max_length=MAX_BLOCK_CHARS)
    bbox: list[Annotated[int, Field(ge=0, le=MAX_BBOX)]] | None = Field(
        default=None, min_length=4, max_length=4
    )


class TextLayerIn(BaseModel):
    """이 기기에서 읽은 글. 주민등록번호 모양은 보내기 전에 가린다 (``masked`` = 가린 수)."""

    engine: str = Field(min_length=1, max_length=100)
    pages_total: int = Field(default=1, ge=1, le=MAX_LAYER_PAGES)
    blocks: list[LayerBlockIn] = Field(max_length=MAX_LAYER_BLOCKS)
    masked: int = Field(default=0, ge=0, le=MAX_LAYER_BLOCKS)
    version_id: UUID | None = None


def to_layer(body: TextLayerIn) -> TextLayer:
    return TextLayer(
        engine=body.engine,
        pages_total=body.pages_total,
        masked=body.masked,
        blocks=tuple(
            LayerBlock(b.page, b.text, tuple(b.bbox) if b.bbox else None) for b in body.blocks
        ),
    )


class TextLayerOut(BaseModel):
    source_id: UUID
    parse_run_id: UUID
    blocks: int
    masked: int
    parse_status: str
    missing_pages: list[int]


def add_ingest_routes(router: APIRouter, deps, authorize) -> None:
    reader = Annotated[ActorContext, Depends(authorize("sources.read"))]
    writer = Annotated[ActorContext, Depends(authorize("sources.write"))]

    @router.post("/ingest/classify", response_model=list[ClassifyItemOut])
    async def classify(body: ClassifyIn, actor: reader) -> list[ClassifyItemOut]:
        """넣기 전에 무엇인지 고른다. 모델 없이 이 서버의 규칙으로 (증빙일 수 있어 밖으로 보내지 않는다)."""
        items = [
            ingest_uc.ClassifyIn(
                key=i.key, filename=i.filename, mime=i.mime, text_head=i.text_head, url=i.url
            )
            for i in body.items
        ]
        got = await ingest_uc.classify_items(deps.db, actor, items)
        return [ClassifyItemOut.model_validate(g, from_attributes=True) for g in got]

    @router.get("/ingest/capabilities", response_model=CapabilitiesOut)
    async def caps(actor: reader) -> CapabilitiesOut:
        from career_core.usecases.extraction import auto_extract_state

        got = await ingest_uc.capabilities(deps.models, actor)
        state = await auto_extract_state(
            deps.db,
            deps.models,
            actor,
            can_extract=deps.permissions.allows(actor, "brain.extract"),
        )
        return CapabilitiesOut(
            model_ocr_classes=list(got.model_ocr_classes),
            model_ocr_route=got.model_ocr_route,
            auto_extract=state,
        )

    @router.put("/sources/{source_id}/text-layer", response_model=TextLayerOut)
    async def text_layer(source_id: UUID, body: TextLayerIn, actor: writer) -> TextLayerOut:
        try:
            got = await ingest_uc.attach_text_layer(
                deps.db, actor, source_id, to_layer(body), version_id=body.version_id
            )
        except ingest_uc.IngestNotFound as exc:
            raise HTTPException(status_code=404, detail="not found") from exc
        except TextLayerError as exc:
            raise HTTPException(
                status_code=_STATUS.get(exc.code, status.HTTP_422_UNPROCESSABLE_CONTENT),
                detail=exc.code,
            ) from exc
        if got.parse_status in ("complete", "partial"):
            # 끝난 읽기에 글을 붙였다 → 내 경험 자료면 바로 정리 (c0052)
            from career_core.usecases.parsing import after_read

            can = deps.permissions.allows(actor, "brain.extract")
            await after_read(
                deps.db, deps.models, actor.workspace_id, got.parse_run_id, allowed=can or None
            )
        return TextLayerOut.model_validate(got, from_attributes=True)


__all__ = ["TextLayerIn", "add_ingest_routes", "to_layer"]

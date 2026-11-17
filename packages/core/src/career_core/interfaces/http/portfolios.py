"""포트폴리오 경로 (c0049, O18, plan §19.6).

읽기 ``portfolio.read``, 쓰기 ``portfolio.write``, GitHub 가져오기 ``portfolio.import``,
GitHub 토큰 저장·삭제 ``portfolio.token`` (공간 관리자).

- 항목: ``GET|POST /portfolio/items`` (``?experience_id=``), ``GET|PATCH|DELETE /portfolio/items/{id}``,
  ``POST /portfolio/items/{id}/revisions`` (수정 연산. ``base_revision``이 최신이 아니면 409
  ``{"code": "revision_conflict", "current_revision": n}``), ``GET /portfolio/items/{id}/picker``
- 이미지: ``POST /portfolio/items/{id}/images`` (multipart ``file`` + ``kind``·``caption``·``visibility``),
  ``PATCH|DELETE /portfolio/images/{id}``, ``GET /portfolio/images/{id}/file`` (편집 화면 미리 보기)
- 묶음: ``GET|POST /portfolio/bundles`` (``?application_id=``), ``GET|PATCH|DELETE /portfolio/bundles/{id}``,
  ``PUT /portfolio/bundles/{id}/items`` (``base_revision`` + 순서대로 항목 ID),
  ``POST /portfolio/bundles/{id}/prep-order``, ``GET /portfolio/bundles/{id}/check``,
  ``GET /portfolio/bundles/{id}/preview`` (내보낸 HTML과 같은 바이트),
  ``GET /portfolio/bundles/{id}/export?format=html|zip[&resume_version_id=]`` (+ ``/export/check``)
- GitHub: ``POST /portfolio/github/import``, ``GET|PUT|DELETE /portfolio/github/token``

오류는 ``{"detail": code}``. GitHub 한도는 429 ``{"code": "rate_limited", "reset_at": …}``.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from career_core.domain.portfolio import MAX_IMAGE_BYTES, PortfolioConflict
from career_core.domain.resume import MAX_OPERATIONS, ResumeConflict, ResumeRuleError
from career_core.ports import ActorContext
from career_core.usecases import portfolios as uc
from career_core.usecases.brain import NotFound

_RULE_STATUS = {
    "bundle_has_children": 409,
    "too_many_images": 409,
    "token_missing": 409,
    "token_needs_reentry": 409,
    "image_too_large": 413,
    "unsupported_type": 415,
    "empty": 400,
    "repo_not_found": 404,
    "rate_limited": 429,
    "github_unavailable": 502,
    "token_storage_unavailable": 503,
}


def _error(exc: ResumeRuleError) -> HTTPException:
    if isinstance(exc, (PortfolioConflict, ResumeConflict)):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "current_revision": exc.current},
        )
    if exc.code == "rate_limited":
        reset = getattr(exc, "reset_at", None)
        return HTTPException(
            status_code=429,
            detail={"code": exc.code, "reset_at": reset.isoformat() if reset else None},
        )
    return HTTPException(status_code=_RULE_STATUS.get(exc.code, 422), detail=exc.code)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


Visibility = Literal["public", "application", "private"]
BundleKind = Literal["all", "role", "posting"]
ImageKind = Literal["screenshot", "diagram"]


class ItemCreateIn(BaseModel):
    experience_id: UUID
    title: str | None = Field(default=None, max_length=400)
    visibility: Visibility = "public"
    # 그 경험의 확인한 사실로 결과·한 일 줄을 채운다 (5개씩까지)
    fill: bool = False


class LinkIn(BaseModel):
    id: str | None = Field(default=None, max_length=40)
    kind: str = Field(max_length=20)
    url: str = Field(max_length=2100)
    label: str | None = Field(default=None, max_length=200)


class ItemPatch(BaseModel):
    title: str | None = Field(default=None, max_length=400)
    visibility: Visibility | None = None
    tags: list[str] | None = Field(default=None, max_length=40)
    links: list[LinkIn] | None = Field(default=None, max_length=20)


class SaveIn(BaseModel):
    base_revision: int = Field(ge=0)
    operations: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_OPERATIONS)
    reason: str | None = Field(default=None, max_length=1000)
    idempotency_key: str | None = Field(default=None, max_length=200)


class ImagePatch(BaseModel):
    caption: str | None = Field(default=None, max_length=400)
    kind: ImageKind | None = None
    visibility: Visibility | None = None
    position: int | None = Field(default=None, ge=0, le=100)


class BundleCreateIn(BaseModel):
    kind: BundleKind
    title: str | None = Field(default=None, max_length=400)
    parent_id: UUID | None = None
    target_role: str | None = Field(default=None, max_length=400)
    application_id: UUID | None = None
    intro: str | None = Field(default=None, max_length=2000)
    # 위 묶음이 없을 때 나만이 아닌 항목을 모두 넣는다
    fill: bool = False


class BundlePatch(BaseModel):
    title: str | None = Field(default=None, max_length=400)
    target_role: str | None = Field(default=None, max_length=400)
    intro: str | None = Field(default=None, max_length=2000)
    application_id: UUID | None = None


class BundleItemsIn(BaseModel):
    base_revision: int = Field(ge=0)
    item_ids: list[UUID] = Field(max_length=200)


class BaseRevisionIn(BaseModel):
    base_revision: int = Field(ge=0)


class ImportIn(BaseModel):
    url: str = Field(max_length=500)
    # 저장해 둔 토큰으로 비공개 저장소를 읽는다 (공간 관리자만)
    private: bool = False
    item_id: UUID | None = None


class TokenIn(BaseModel):
    token: str = Field(min_length=1, max_length=400)

    def __repr__(self) -> str:
        return "TokenIn(<hidden>)"


class CreatedOut(BaseModel):
    id: UUID


class ItemSummaryOut(BaseModel):
    id: UUID
    experience_id: UUID
    experience_title: str
    experience_active: bool
    title: str
    visibility: str
    tags: list[str]
    links: list[dict[str, Any]]
    current_revision: int
    lines: dict[str, int]
    results: int
    images: int
    images_private: int
    bundles: int
    created_at: datetime
    updated_at: datetime


class ImageOut(BaseModel):
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


class ImageUploadOut(ImageOut):
    deduplicated: bool
    removed_metadata: list[str]


class ItemOut(BaseModel):
    summary: ItemSummaryOut
    revision_no: int
    revision_id: UUID | None
    content: dict[str, Any]
    experience: dict[str, Any]
    images: list[ImageOut]


class RevisionOut(BaseModel):
    id: UUID
    revision_no: int
    created: bool


class BundleSummaryOut(BaseModel):
    id: UUID
    kind: str
    title: str
    target_role: str | None
    intro: str | None
    parent_id: UUID | None
    parent_title: str | None
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


class BundleItemOut(BaseModel):
    item: ItemSummaryOut
    shown: bool
    hidden_reason: str | None
    images_shown: int
    images_hidden: int


class BundleOut(BaseModel):
    summary: BundleSummaryOut
    lineage: list[dict[str, Any]]
    items: list[BundleItemOut]
    prep_order: list[UUID]


class RevisionNoOut(BaseModel):
    revision: int


class ExportCheckOut(BaseModel):
    filename: str
    media_type: str
    format: str
    revision: int
    bytes: int
    findings: list[dict[str, Any]]
    summary: dict[str, int]


class ImportOut(BaseModel):
    repo: dict[str, Any]
    readme: bool
    source_id: UUID | None
    parse_run_id: UUID | None
    deduplicated: bool
    item_id: UUID | None


class TokenStatusOut(BaseModel):
    set: bool
    last4: str | None
    # 이 서버에 토큰을 암호화해 둘 열쇠가 있는가 (없으면 저장할 수 없다)
    available: bool


Fmt = Annotated[Literal["html", "zip"], Query(alias="format")]
_EXT = {"image/jpeg": "jpg", "image/png": "png"}


def add_portfolio_routes(router: APIRouter, deps, authorize) -> None:
    db = deps.db
    reader = Annotated[ActorContext, Depends(authorize("portfolio.read"))]
    writer = Annotated[ActorContext, Depends(authorize("portfolio.write"))]
    importer = Annotated[ActorContext, Depends(authorize("portfolio.import"))]
    admin = Annotated[ActorContext, Depends(authorize("portfolio.token"))]
    item_base = "/portfolio/items/{item_id}"
    bundle_base = "/portfolio/bundles/{bundle_id}"

    async def _item(actor: ActorContext, item_id: UUID, revision: int | None = None) -> ItemOut:
        try:
            view = await uc.get_item(db, actor, item_id, revision=revision)
        except NotFound as exc:
            raise _not_found() from exc
        return ItemOut.model_validate(view, from_attributes=True)

    async def _bundle(actor: ActorContext, bundle_id: UUID) -> BundleOut:
        try:
            view = await uc.get_bundle(db, actor, bundle_id)
        except NotFound as exc:
            raise _not_found() from exc
        return BundleOut.model_validate(view, from_attributes=True)

    # ------------------------------------------------------------ 항목

    @router.get("/portfolio/items", response_model=list[ItemSummaryOut])
    async def list_items(actor: reader, experience_id: UUID | None = None) -> list[ItemSummaryOut]:
        rows = await uc.list_items(db, actor, experience_id=experience_id)
        return [ItemSummaryOut.model_validate(r, from_attributes=True) for r in rows]

    @router.post("/portfolio/items", response_model=CreatedOut, status_code=201)
    async def create_item(actor: writer, body: ItemCreateIn) -> CreatedOut:
        try:
            new_id = await uc.create_item(db, actor, **body.model_dump())
        except NotFound as exc:
            raise _not_found() from exc
        except ResumeRuleError as exc:
            raise _error(exc) from exc
        return CreatedOut(id=new_id)

    @router.get(item_base, response_model=ItemOut)
    async def get_item(actor: reader, item_id: UUID, revision: int | None = None) -> ItemOut:
        return await _item(actor, item_id, revision)

    @router.patch(item_base, response_model=ItemOut)
    async def patch_item(actor: writer, item_id: UUID, body: ItemPatch) -> ItemOut:
        try:
            await uc.update_item(db, actor, item_id, body.model_dump(exclude_unset=True))
        except NotFound as exc:
            raise _not_found() from exc
        except ResumeRuleError as exc:
            raise _error(exc) from exc
        return await _item(actor, item_id)

    @router.delete(item_base, status_code=204)
    async def delete_item(actor: writer, item_id: UUID) -> Response:
        try:
            await uc.delete_item(db, deps.blobs, actor, item_id)
        except NotFound as exc:
            raise _not_found() from exc
        return Response(status_code=204)

    @router.post(item_base + "/revisions", response_model=RevisionOut)
    async def save_item(actor: writer, item_id: UUID, body: SaveIn) -> RevisionOut:
        try:
            res = await uc.save_item(db, actor, item_id, **body.model_dump())
        except NotFound as exc:
            raise _not_found() from exc
        except ResumeRuleError as exc:
            raise _error(exc) from exc
        return RevisionOut.model_validate(res, from_attributes=True)

    @router.get(item_base + "/picker")
    async def picker(actor: reader, item_id: UUID) -> dict[str, Any]:
        try:
            return await uc.item_picker(db, actor, item_id)
        except NotFound as exc:
            raise _not_found() from exc

    # ------------------------------------------------------------ 이미지

    @router.patch("/portfolio/images/{image_id}", response_model=ImageOut)
    async def patch_image(actor: writer, image_id: UUID, body: ImagePatch) -> ImageOut:
        try:
            got = await uc.update_image(db, actor, image_id, body.model_dump(exclude_unset=True))
        except NotFound as exc:
            raise _not_found() from exc
        except ResumeRuleError as exc:
            raise _error(exc) from exc
        return ImageOut.model_validate(got, from_attributes=True)

    @router.delete("/portfolio/images/{image_id}", status_code=204)
    async def delete_image(actor: writer, image_id: UUID) -> Response:
        try:
            await uc.delete_image(db, deps.blobs, actor, image_id)
        except NotFound as exc:
            raise _not_found() from exc
        return Response(status_code=204)

    # ------------------------------------------------------------ 묶음

    @router.get("/portfolio/bundles", response_model=list[BundleSummaryOut])
    async def list_bundles(
        actor: reader, application_id: UUID | None = None
    ) -> list[BundleSummaryOut]:
        rows = await uc.list_bundles(db, actor, application_id=application_id)
        return [BundleSummaryOut.model_validate(r, from_attributes=True) for r in rows]

    @router.post("/portfolio/bundles", response_model=CreatedOut, status_code=201)
    async def create_bundle(actor: writer, body: BundleCreateIn) -> CreatedOut:
        try:
            new_id = await uc.create_bundle(db, actor, **body.model_dump())
        except NotFound as exc:
            raise _not_found() from exc
        except ResumeRuleError as exc:
            raise _error(exc) from exc
        return CreatedOut(id=new_id)

    @router.get(bundle_base, response_model=BundleOut)
    async def get_bundle(actor: reader, bundle_id: UUID) -> BundleOut:
        return await _bundle(actor, bundle_id)

    @router.patch(bundle_base, response_model=BundleOut)
    async def patch_bundle(actor: writer, bundle_id: UUID, body: BundlePatch) -> BundleOut:
        try:
            await uc.update_bundle(db, actor, bundle_id, body.model_dump(exclude_unset=True))
        except NotFound as exc:
            raise _not_found() from exc
        except ResumeRuleError as exc:
            raise _error(exc) from exc
        return await _bundle(actor, bundle_id)

    @router.delete(bundle_base, status_code=204)
    async def delete_bundle(actor: writer, bundle_id: UUID) -> Response:
        try:
            await uc.delete_bundle(db, actor, bundle_id)
        except NotFound as exc:
            raise _not_found() from exc
        except ResumeRuleError as exc:
            raise _error(exc) from exc
        return Response(status_code=204)

    @router.put(bundle_base + "/items", response_model=RevisionNoOut)
    async def set_items(actor: writer, bundle_id: UUID, body: BundleItemsIn) -> RevisionNoOut:
        try:
            rev = await uc.set_bundle_items(
                db, actor, bundle_id, base_revision=body.base_revision, item_ids=body.item_ids
            )
        except NotFound as exc:
            raise _not_found() from exc
        except ResumeRuleError as exc:
            raise _error(exc) from exc
        return RevisionNoOut(revision=rev)

    @router.post(bundle_base + "/prep-order", response_model=RevisionNoOut)
    async def prep_order(actor: writer, bundle_id: UUID, body: BaseRevisionIn) -> RevisionNoOut:
        try:
            rev = await uc.apply_prep_order(db, actor, bundle_id, base_revision=body.base_revision)
        except NotFound as exc:
            raise _not_found() from exc
        except ResumeRuleError as exc:
            raise _error(exc) from exc
        return RevisionNoOut(revision=rev)

    @router.get(bundle_base + "/check")
    async def check(actor: reader, bundle_id: UUID) -> dict[str, Any]:
        try:
            return await uc.check_bundle(db, actor, bundle_id)
        except NotFound as exc:
            raise _not_found() from exc

    @router.get(bundle_base + "/preview")
    async def preview(actor: reader, bundle_id: UUID) -> Response:
        """미리보기 HTML. 화면은 이 글을 sandbox iframe의 srcdoc으로 띄운다 (인쇄도 그 안에서)."""
        try:
            data = await uc.preview_html(db, deps.blobs, actor, bundle_id)
        except NotFound as exc:
            raise _not_found() from exc
        return Response(
            content=data,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    async def _export(actor, bundle_id, fmt, resume_version_id):
        try:
            return await uc.export_bundle(
                db, deps.blobs, actor, bundle_id, fmt=fmt, resume_version_id=resume_version_id
            )
        except NotFound as exc:
            raise _not_found() from exc
        except ResumeRuleError as exc:
            raise _error(exc) from exc

    @router.get(bundle_base + "/export/check", response_model=ExportCheckOut)
    async def export_check(
        actor: reader, bundle_id: UUID, fmt: Fmt, resume_version_id: UUID | None = None
    ) -> ExportCheckOut:
        res = await _export(actor, bundle_id, fmt, resume_version_id)
        return ExportCheckOut(
            filename=res.filename,
            media_type=res.media_type,
            format=res.format,
            revision=res.revision,
            bytes=len(res.data),
            findings=res.findings,
            summary=res.summary,
        )

    @router.get(bundle_base + "/export")
    async def export(
        actor: reader, bundle_id: UUID, fmt: Fmt, resume_version_id: UUID | None = None
    ) -> Response:
        res = await _export(actor, bundle_id, fmt, resume_version_id)
        return Response(
            content=res.data,
            media_type=res.media_type,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(res.filename)}",
                "X-Export-Findings": str(len(res.findings)),
                "X-Export-Codes": ",".join(res.summary),
                "Cache-Control": "no-store",
            },
        )

    # ------------------------------------------------------------ GitHub

    gh = getattr(deps, "github", None)

    @router.get("/portfolio/github/token", response_model=TokenStatusOut)
    async def token_status(actor: reader) -> TokenStatusOut:
        got = await uc.github_token_status(db, actor)
        return TokenStatusOut(**got, available=bool(gh and gh.secrets is not None))

    @router.put("/portfolio/github/token", response_model=TokenStatusOut)
    async def token_set(actor: admin, body: TokenIn) -> TokenStatusOut:
        if gh is None:
            raise HTTPException(status_code=503, detail="github_not_configured")
        try:
            got = await uc.set_github_token(db, gh, actor, body.token)
        except ResumeRuleError as exc:
            raise _error(exc) from exc
        return TokenStatusOut(**got, available=gh.secrets is not None)

    @router.delete("/portfolio/github/token", status_code=204)
    async def token_delete(actor: admin) -> Response:
        await uc.delete_github_token(db, actor)
        return Response(status_code=204)

    if deps.blobs is None:
        return
    blobs = deps.blobs

    @router.post(item_base + "/images", response_model=ImageUploadOut)
    async def upload_image(
        actor: writer,
        item_id: UUID,
        file: Annotated[UploadFile, File()],
        kind: Annotated[ImageKind, Form()] = "screenshot",
        caption: Annotated[str | None, Form(max_length=400)] = None,
        visibility: Annotated[Visibility, Form()] = "private",
    ) -> ImageUploadOut:
        data = await file.read(MAX_IMAGE_BYTES + 1)
        try:
            view, dedup, removed = await uc.upload_image(
                db,
                blobs,
                actor,
                item_id,
                data=data,
                kind=kind,
                caption=caption,
                visibility=visibility,
            )
        except NotFound as exc:
            raise _not_found() from exc
        except ResumeRuleError as exc:
            raise _error(exc) from exc
        return ImageUploadOut(
            **ImageOut.model_validate(view, from_attributes=True).model_dump(),
            deduplicated=dedup,
            removed_metadata=list(removed),
        )

    @router.get("/portfolio/images/{image_id}/file")
    async def image_file(actor: reader, image_id: UUID) -> Response:
        try:
            img, data = await uc.image_file(db, blobs, actor, image_id)
        except NotFound as exc:
            raise _not_found() from exc
        name = f"portfolio_{img.width_px}x{img.height_px}.{_EXT.get(img.mime, 'bin')}"
        return Response(
            content=data,
            media_type=img.mime,
            headers={
                "Content-Disposition": f'inline; filename="{name}"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "ETag": f'"{img.sha256}"',
            },
        )

    @router.post("/portfolio/github/import", response_model=ImportOut)
    async def github_import(actor: importer, body: ImportIn) -> ImportOut:
        if gh is None:
            raise HTTPException(status_code=503, detail="github_not_configured")
        if body.private and not deps.permissions.allows(actor, "portfolio.token"):
            raise HTTPException(status_code=403, detail="forbidden")
        try:
            res = await uc.github_import(
                db, blobs, gh, actor, url=body.url, private=body.private, item_id=body.item_id
            )
        except NotFound as exc:
            raise _not_found() from exc
        except ResumeRuleError as exc:
            raise _error(exc) from exc
        return ImportOut.model_validate(res, from_attributes=True)

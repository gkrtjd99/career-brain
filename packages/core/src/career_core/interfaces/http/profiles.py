"""프로필·증명사진 경로 (O21, plan §20 "나 › 프로필").

읽기 ``profile.read``, 쓰기 ``profile.write``. 공간은 토큰이 정한다 (다른 공간의 사진은 404).
오류는 ``{"detail": code}``: 없는 사진 404 ``not found``, 너무 큰 파일 413 ``too_large``,
JPEG·PNG가 아님 415 ``unsupported_type``, 사진이 너무 많음 409 ``too_many_photos``,
그 밖의 규칙 422 (``invalid_image``, ``invalid_spec``, ``spec_mismatch``, ``invalid_dimensions``,
``invalid_email``, ``invalid_phone``, ``invalid_link``, ``invalid_birth_date``, ``default_required`` …).

사진 올리기·파일 받기는 BlobStore가 있을 때만 등록한다 (원본 업로드와 같다).

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from career_core.domain.profiles import (
    MAX_ADDRESS,
    MAX_AVAILABLE_FROM,
    MAX_EMAIL,
    MAX_EXEMPTION_REASON,
    MAX_HEADLINE,
    MAX_LINK_LABEL,
    MAX_LINK_URL,
    MAX_LINKS,
    MAX_MILITARY_TEXT,
    MAX_NAME,
    MAX_PHONE,
    MAX_PHOTO_BYTES,
    MAX_PREFERENCE_ITEMS,
    MAX_SALARY,
    PhotoSpec,
    ProfileRuleError,
)
from career_core.ports import ActorContext
from career_core.usecases import profiles as uc
from career_core.usecases.brain import NotFound

_STATUS = {"too_large": 413, "unsupported_type": 415, "too_many_photos": 409, "empty": 400}
_EXT = {"image/jpeg": "jpg", "image/png": "png"}


def _rule(exc: ProfileRuleError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(exc.code, 422), detail=exc.code)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not found")


class LinkIO(BaseModel):
    label: str = Field(default="", max_length=MAX_LINK_LABEL)
    url: str = Field(max_length=MAX_LINK_URL)


class ProfileIn(BaseModel):
    """프로필 전체. 빠진 칸·빈 칸은 비운다."""

    name: str | None = Field(default=None, max_length=MAX_NAME * 2)
    name_en: str | None = Field(default=None, max_length=MAX_NAME * 2)
    birth_date: date | None = None
    email: str | None = Field(default=None, max_length=MAX_EMAIL * 2)
    phone: str | None = Field(default=None, max_length=MAX_PHONE * 2)
    address: str | None = Field(default=None, max_length=MAX_ADDRESS * 2)
    links: list[LinkIO] = Field(default_factory=list, max_length=MAX_LINKS * 2)
    headline: str | None = Field(default=None, max_length=MAX_HEADLINE * 2)


class ProfileOut(BaseModel):
    name: str | None
    name_en: str | None
    birth_date: date | None
    email: str | None
    phone: str | None
    address: str | None
    links: list[LinkIO]
    headline: str | None
    default_photo_id: UUID | None
    updated_at: datetime | None


class PhotoOut(BaseModel):
    id: UUID
    spec: str
    width_px: int
    height_px: int
    bytes: int
    mime: str
    sha256: str
    is_default: bool
    created_at: datetime


class PhotoUploadOut(PhotoOut):
    deduplicated: bool
    # 서버에서 지운 메타데이터 조각 (APP1 = EXIF·XMP, APP2 = ICC …). 사진 변환 도구의 JPEG는 보통
    # 브라우저가 넣은 sRGB 색 프로필(APP2)뿐이다 (색 프로필이 없으면 sRGB로 보므로 보이는 색은 같다)
    removed_metadata: list[str]


Month = Annotated[str, Field(pattern=r"^\d{4}-\d{2}(-\d{2})?$")]
MilitaryStatus = Literal["served", "not_served", "exempt", "not_applicable", "serving"]
EmploymentType = Literal["regular", "contract", "intern"]


class ProfileDetailsPatch(BaseModel):
    """병역·보훈·장애·희망 조건 (c0041). 준 칸만 바꾼다 (null·빈 목록은 비우기). 모두 선택."""

    military_status: MilitaryStatus | None = None
    military_branch: str | None = Field(default=None, max_length=MAX_MILITARY_TEXT * 2)
    military_rank: str | None = Field(default=None, max_length=MAX_MILITARY_TEXT * 2)
    military_started_on: Month | None = None  # 달까지 ("YYYY-MM")
    military_ended_on: Month | None = None
    military_exemption_reason: str | None = Field(default=None, max_length=MAX_EXEMPTION_REASON * 2)
    veteran: bool | None = None
    disability: bool | None = None
    desired_roles: list[str] | None = Field(default=None, max_length=MAX_PREFERENCE_ITEMS * 2)
    desired_locations: list[str] | None = Field(default=None, max_length=MAX_PREFERENCE_ITEMS * 2)
    desired_salary: str | None = Field(default=None, max_length=MAX_SALARY * 2)
    available_from: str | None = Field(default=None, max_length=MAX_AVAILABLE_FROM * 2)
    employment_types: list[EmploymentType] | None = None


class ProfileDetailsOut(BaseModel):
    military_status: str | None
    military_branch: str | None
    military_rank: str | None
    military_started_on: date | None  # 그 달 1일
    military_ended_on: date | None
    military_exemption_reason: str | None
    veteran: bool | None
    disability: bool | None
    desired_roles: list[str]
    desired_locations: list[str]
    desired_salary: str | None
    available_from: str | None
    employment_types: list[str]


class ProfileSuggestionOut(BaseModel):
    field: Literal["name", "email", "phone", "link"]
    value: str
    # 링크만: GitHub | LinkedIn | 블로그 | 포트폴리오 | 링크
    label: str | None
    source_id: UUID
    source_title: str
    quote: str


class ProfileSuggestionsOut(BaseModel):
    """내 경험 자료(이력서 등)에서 규칙으로 찾은 프로필 칸 후보. 이미 프로필에 있는 값은 없다."""

    suggestions: list[ProfileSuggestionOut]


class PhotoPatch(BaseModel):
    is_default: Literal[True] | None = None
    spec: PhotoSpec | None = None


class DeleteOut(BaseModel):
    default_photo_id: UUID | None


def _profile_out(v: uc.ProfileView) -> ProfileOut:
    return ProfileOut(
        name=v.name,
        name_en=v.name_en,
        birth_date=v.birth_date,
        email=v.email,
        phone=v.phone,
        address=v.address,
        links=[LinkIO(**lk) for lk in v.links],
        headline=v.headline,
        default_photo_id=v.default_photo_id,
        updated_at=v.updated_at,
    )


def _photo_out(p: uc.PhotoView) -> PhotoOut:
    return PhotoOut.model_validate(p, from_attributes=True)


def add_profile_routes(router: APIRouter, deps, authorize) -> None:
    reader = Annotated[ActorContext, Depends(authorize("profile.read"))]
    writer = Annotated[ActorContext, Depends(authorize("profile.write"))]

    @router.get("/profile", response_model=ProfileOut)
    async def get_profile(actor: reader) -> ProfileOut:
        return _profile_out(await uc.get_profile(deps.db, actor))

    @router.put("/profile", response_model=ProfileOut)
    async def put_profile(body: ProfileIn, actor: writer) -> ProfileOut:
        try:
            view = await uc.save_profile(
                deps.db,
                actor,
                name=body.name,
                name_en=body.name_en,
                birth_date=body.birth_date,
                email=body.email,
                phone=body.phone,
                address=body.address,
                links=[(lk.label, lk.url) for lk in body.links],
                headline=body.headline,
            )
        except ProfileRuleError as exc:
            raise _rule(exc) from exc
        return _profile_out(view)

    @router.get("/profile/suggestions", response_model=ProfileSuggestionsOut)
    async def profile_suggestions(actor: reader) -> ProfileSuggestionsOut:
        """채울 값 제안 (저장하지 않는다). 증빙·참고 원본은 보지 않는다."""
        found = await uc.profile_suggestions(deps.db, actor)
        return ProfileSuggestionsOut(
            suggestions=[
                ProfileSuggestionOut.model_validate(x, from_attributes=True) for x in found
            ]
        )

    @router.get("/profile/details", response_model=ProfileDetailsOut)
    async def get_details(actor: reader) -> ProfileDetailsOut:
        view = await uc.get_profile_details(deps.db, actor)
        return ProfileDetailsOut.model_validate(view, from_attributes=True)

    @router.patch("/profile/details", response_model=ProfileDetailsOut)
    async def patch_details(body: ProfileDetailsPatch, actor: writer) -> ProfileDetailsOut:
        changes = body.model_dump(exclude_unset=True)
        try:
            for key in ("military_started_on", "military_ended_on"):
                if changes.get(key):
                    v = changes[key]
                    changes[key] = date(int(v[:4]), int(v[5:7]), 1)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid_military_period") from exc
        try:
            view = await uc.update_profile_details(deps.db, actor, changes)
        except ProfileRuleError as exc:
            raise _rule(exc) from exc
        return ProfileDetailsOut.model_validate(view, from_attributes=True)

    @router.get("/profile/photos", response_model=list[PhotoOut])
    async def list_photos(actor: reader) -> list[PhotoOut]:
        return [_photo_out(p) for p in await uc.list_photos(deps.db, actor)]

    @router.patch("/profile/photos/{photo_id}", response_model=PhotoOut)
    async def patch_photo(photo_id: UUID, body: PhotoPatch, actor: writer) -> PhotoOut:
        try:
            got = await uc.update_photo(
                deps.db,
                actor,
                photo_id,
                is_default=body.is_default,
                spec=body.spec.value if body.spec else None,
            )
        except NotFound as exc:
            raise _not_found() from exc
        except ProfileRuleError as exc:
            raise _rule(exc) from exc
        return _photo_out(got)

    @router.delete("/profile/photos/{photo_id}", response_model=DeleteOut)
    async def delete_photo(photo_id: UUID, actor: writer) -> DeleteOut:
        try:
            default_id = await uc.delete_photo(deps.db, deps.blobs, actor, photo_id)
        except NotFound as exc:
            raise _not_found() from exc
        return DeleteOut(default_photo_id=default_id)

    if deps.blobs is None:
        return
    blobs = deps.blobs

    @router.post("/profile/photos", response_model=PhotoUploadOut)
    async def upload_photo(
        actor: writer,
        file: Annotated[UploadFile, File()],
        spec: Annotated[str, Form()],
    ) -> PhotoUploadOut:
        data = await file.read(MAX_PHOTO_BYTES + 1)
        try:
            got = await uc.upload_photo(deps.db, blobs, actor, data=data, spec=spec)
        except ProfileRuleError as exc:
            raise _rule(exc) from exc
        return PhotoUploadOut(
            **_photo_out(got.photo).model_dump(),
            deduplicated=got.deduplicated,
            removed_metadata=list(got.removed_metadata),
        )

    @router.get("/profile/photos/{photo_id}/file")
    async def photo_file(photo_id: UUID, actor: reader) -> Response:
        try:
            p, data = await uc.photo_file(deps.db, blobs, actor, photo_id)
        except NotFound as exc:
            raise _not_found() from exc
        name = f"photo_{p.spec}_{p.width_px}x{p.height_px}.{_EXT.get(p.mime, 'bin')}"
        return Response(
            content=data,
            media_type=p.mime,
            headers={
                "Content-Disposition": f'inline; filename="{name}"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "ETag": f'"{p.sha256}"',
            },
        )

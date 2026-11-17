"""프로필·증명사진 (O21, plan §20 "나 › 프로필").

- 프로필은 공간마다 하나다. 없으면 빈 프로필로 보인다 (저장하거나 사진을 처음 넣을 때 행을 만든다).
- 사진은 변환본만: 받은 바이트에서 메타데이터를 지우고(``domain.profiles.strip_metadata``) 그 결과만
  BlobStore에 둔다. 같은 공간에 같은 바이트는 한 장 (다시 올리면 있던 사진을 돌려준다).
- 기본 사진은 공간에 하나 (``profiles.default_photo_id``). 사진이 있으면 늘 하나가 기본이다: 첫 사진이
  기본이 되고, 기본 사진을 지우면 남은 사진 중 가장 최근 것이 기본이 된다. 묶음 합치기 등으로 칸이 비어
  있으면 가장 최근 사진을 기본으로 보여 준다.
- 자료 등급 restricted: 이 모듈의 값은 모델로 보내지 않는다. MCP에는 ``profile_summary``(이름·한 줄
  소개·링크)만 준다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from career_core.db.base import utcnow
from career_core.db.models import Profile, ProfilePhoto
from career_core.db.session import Database
from career_core.domain.profiles import (
    MAX_ADDRESS,
    MAX_HEADLINE,
    MAX_NAME,
    MAX_PHOTO_BYTES,
    MAX_PHOTOS,
    PROFILE_SUMMARY_FIELDS,
    ProfileRuleError,
    check_spec_size,
    clean_email,
    clean_links,
    clean_phone,
    clean_text,
    photo_spec,
    strip_metadata,
)
from career_core.ports import ActorContext, BlobStore
from career_core.usecases.brain import NotFound


@dataclass(frozen=True, slots=True)
class ProfileView:
    name: str | None = None
    name_en: str | None = None
    birth_date: date | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    links: list[dict] = field(default_factory=list)
    headline: str | None = None
    default_photo_id: UUID | None = None
    updated_at: datetime | None = None  # 저장한 적이 없으면 None


@dataclass(frozen=True, slots=True)
class PhotoView:
    id: UUID
    spec: str
    width_px: int
    height_px: int
    bytes: int
    mime: str
    sha256: str
    is_default: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class UploadedPhoto:
    photo: PhotoView
    deduplicated: bool
    removed_metadata: tuple[str, ...]


def _effective_default(profile: Profile | None, photos: list[ProfilePhoto]) -> UUID | None:
    """저장된 기본 사진. 칸이 비었거나 없는 사진이면 가장 최근 사진 (사진이 있으면 늘 하나)."""
    ids = {p.id for p in photos}
    if profile is not None and profile.default_photo_id in ids:
        return profile.default_photo_id
    return photos[0].id if photos else None


def _photo_view(p: ProfilePhoto, default_id: UUID | None) -> PhotoView:
    return PhotoView(
        id=p.id,
        spec=p.spec,
        width_px=p.width_px,
        height_px=p.height_px,
        bytes=p.bytes,
        mime=p.mime,
        sha256=p.sha256,
        is_default=p.id == default_id,
        created_at=p.created_at,
    )


def _newest_first():
    return (ProfilePhoto.created_at.desc(), ProfilePhoto.id.desc())


async def _photos(s) -> list[ProfilePhoto]:
    return list((await s.scalars(select(ProfilePhoto).order_by(*_newest_first()))).all())


async def get_profile(db: Database, actor: ActorContext) -> ProfileView:
    async with db.workspace_session(actor.workspace_id) as s:
        profile = await s.scalar(select(Profile))
        photos = await _photos(s)
    default_id = _effective_default(profile, photos)
    if profile is None:
        return ProfileView(default_photo_id=default_id)
    return ProfileView(
        name=profile.name,
        name_en=profile.name_en,
        birth_date=profile.birth_date,
        email=profile.email,
        phone=profile.phone,
        address=profile.address,
        links=list(profile.links or []),
        headline=profile.headline,
        default_photo_id=default_id,
        updated_at=profile.updated_at,
    )


async def _ensure_profile(s, actor: ActorContext) -> None:
    await s.execute(
        pg_insert(Profile)
        .values(id=uuid4(), workspace_id=actor.workspace_id, links=[], updated_by=actor.subject_id)
        .on_conflict_do_nothing(index_elements=[Profile.workspace_id])
    )


async def save_profile(
    db: Database,
    actor: ActorContext,
    *,
    name: str | None = None,
    name_en: str | None = None,
    birth_date: date | None = None,
    email: str | None = None,
    phone: str | None = None,
    address: str | None = None,
    links: list[tuple[str | None, str | None]] | None = None,
    headline: str | None = None,
    today: date | None = None,
) -> ProfileView:
    """프로필 전체를 이 값으로 (준 값이 없으면 비운다). 기본 사진은 바꾸지 않는다."""
    if birth_date is not None and (birth_date > (today or date.today()) or birth_date.year < 1900):
        raise ProfileRuleError("invalid_birth_date")
    values = {
        "name": clean_text(name, MAX_NAME, "invalid_name"),
        "name_en": clean_text(name_en, MAX_NAME, "invalid_name"),
        "birth_date": birth_date,
        "email": clean_email(email),
        "phone": clean_phone(phone),
        "address": clean_text(address, MAX_ADDRESS, "invalid_address"),
        "links": [{"label": lk.label, "url": lk.url} for lk in clean_links(links or [])],
        "headline": clean_text(headline, MAX_HEADLINE, "invalid_headline"),
        "updated_by": actor.subject_id,
        "updated_at": utcnow(),
    }
    async with db.workspace_session(actor.workspace_id) as s:
        await _ensure_profile(s, actor)
        await s.execute(
            update(Profile).where(Profile.workspace_id == actor.workspace_id).values(**values)
        )
    return await get_profile(db, actor)


async def profile_summary(db: Database, actor: ActorContext) -> dict:
    """MCP용: 이름·한 줄 소개·링크만. 생년월일·연락처·주소·사진은 넣지 않는다."""
    view = await get_profile(db, actor)
    return {f: getattr(view, f) for f in PROFILE_SUMMARY_FIELDS}


# ---------------------------------------------------------------- 사진


async def list_photos(db: Database, actor: ActorContext) -> list[PhotoView]:
    async with db.workspace_session(actor.workspace_id) as s:
        profile = await s.scalar(select(Profile))
        photos = await _photos(s)
    default_id = _effective_default(profile, photos)
    return [_photo_view(p, default_id) for p in photos]


async def _find(db: Database, actor: ActorContext, sha256: str) -> PhotoView | None:
    for p in await list_photos(db, actor):
        if p.sha256 == sha256:
            return p
    return None


async def upload_photo(
    db: Database,
    blobs: BlobStore,
    actor: ActorContext,
    *,
    data: bytes,
    spec: str,
) -> UploadedPhoto:
    """변환한 사진 한 장. 메타데이터를 지운 바이트만 저장한다. 첫 사진이면 기본 사진이 된다."""
    if not data:
        raise ProfileRuleError("empty")
    if len(data) > MAX_PHOTO_BYTES:
        raise ProfileRuleError("too_large")
    sp = photo_spec(spec)
    clean = strip_metadata(data)
    check_spec_size(sp, clean.width, clean.height)
    sha = hashlib.sha256(clean.data).hexdigest()
    if found := await _find(db, actor, sha):
        return UploadedPhoto(found, True, clean.removed)
    async with db.workspace_session(actor.workspace_id) as s:
        count = await s.scalar(select(func.count()).select_from(ProfilePhoto))
    if (count or 0) >= MAX_PHOTOS:
        raise ProfileRuleError("too_many_photos")
    # 파일 쓰기는 DB 트랜잭션 밖에서. 내용 주소라 중복 쓰기는 무해하다.
    key = await blobs.put(actor.workspace_id, sha, clean.data, clean.mime)
    photo = ProfilePhoto(
        id=uuid4(),
        workspace_id=actor.workspace_id,
        spec=sp.value,
        width_px=clean.width,
        height_px=clean.height,
        bytes=len(clean.data),
        mime=clean.mime,
        blob_key=key,
        sha256=sha,
        created_by=actor.subject_id,
    )
    try:
        async with db.workspace_session(actor.workspace_id) as s:
            s.add(photo)
            await s.flush()
            await _ensure_profile(s, actor)
            await s.execute(
                update(Profile)
                .where(Profile.workspace_id == actor.workspace_id)
                .where(Profile.default_photo_id.is_(None))
                .values(default_photo_id=photo.id)
            )
    except IntegrityError:
        # 같은 사진이 동시에 올라온 경우: 먼저 커밋된 쪽을 돌려준다
        if found := await _find(db, actor, sha):
            return UploadedPhoto(found, True, clean.removed)
        raise
    got = next(p for p in await list_photos(db, actor) if p.id == photo.id)
    return UploadedPhoto(got, False, clean.removed)


async def photo_file(
    db: Database, blobs: BlobStore, actor: ActorContext, photo_id: UUID
) -> tuple[PhotoView, bytes]:
    async with db.workspace_session(actor.workspace_id) as s:
        p = await s.get(ProfilePhoto, photo_id)
    if p is None:
        raise NotFound("photo")
    data = await blobs.get(actor.workspace_id, p.blob_key)
    return _photo_view(p, None), data


async def update_photo(
    db: Database,
    actor: ActorContext,
    photo_id: UUID,
    *,
    is_default: bool | None = None,
    spec: str | None = None,
) -> PhotoView:
    """기본으로 지정(다른 사진은 기본이 아니게 된다)·규격 바꾸기. 기본을 끄는 것은 받지 않는다."""
    if is_default is False:
        raise ProfileRuleError("default_required")
    async with db.workspace_session(actor.workspace_id) as s:
        p = await s.get(ProfilePhoto, photo_id, with_for_update=True)
        if p is None:
            raise NotFound("photo")
        if spec is not None:
            sp = photo_spec(spec)
            check_spec_size(sp, p.width_px, p.height_px)
            p.spec = sp.value
        if is_default:
            await _ensure_profile(s, actor)
            await s.execute(
                update(Profile)
                .where(Profile.workspace_id == actor.workspace_id)
                .values(default_photo_id=p.id)
            )
    return next(x for x in await list_photos(db, actor) if x.id == photo_id)


async def delete_photo(
    db: Database, blobs: BlobStore | None, actor: ActorContext, photo_id: UUID
) -> UUID | None:
    """사진 지우기. 기본 사진이었으면 남은 사진 중 가장 최근 것이 기본이 된다 (없으면 기본 없음).

    다른 행이 같은 파일을 쓰지 않으면 BlobStore의 파일도 지운다 (저장소가 ``delete``를 줄 때만).
    돌려주는 값은 지운 뒤의 기본 사진."""
    async with db.workspace_session(actor.workspace_id) as s:
        p = await s.get(ProfilePhoto, photo_id)
        if p is None:
            raise NotFound("photo")
        key = p.blob_key
        await s.delete(p)
        await s.flush()
        # FK가 기본 사진 칸을 비웠으면(SET NULL) 남은 사진 중 가장 최근 것으로 잇는다
        nxt = await s.scalar(select(ProfilePhoto.id).order_by(*_newest_first()).limit(1))
        if nxt is not None:
            await s.execute(
                update(Profile)
                .where(Profile.workspace_id == actor.workspace_id)
                .where(Profile.default_photo_id.is_(None))
                .values(default_photo_id=nxt)
            )
        default_id = await s.scalar(select(Profile.default_photo_id))
    remove = getattr(blobs, "delete", None)
    if remove is not None and not await _blob_referenced(db, actor.workspace_id, key):
        await remove(actor.workspace_id, key)
    return default_id


async def _blob_referenced(db: Database, workspace_id: UUID, key: str) -> bool:
    """이 공간에서 같은 BlobStore key를 가리키는 행이 남았나 (내보내기와 같은 칸 규칙)."""
    from career_core.usecases.export import build_plan, read_catalog, scope_sql

    async with db.workspace_session(workspace_id) as s:
        plan = build_plan(await read_catalog(await s.connection()))
        for t in plan.tables:
            for col in t.blob_columns:
                hit = await s.scalar(
                    text(
                        f'SELECT 1 FROM {t.sql} AS t WHERE {scope_sql(t)} AND t."{col}" = :key '
                        "LIMIT 1"
                    ),
                    {"ws": str(workspace_id), "key": key},
                )
                if hit:
                    return True
    return False

"""O21 프로필·증명사진: 공간당 프로필 하나, 사진은 변환본만(EXIF 0), 규격별 여러 장·기본 하나,
기본 사진을 지우면 다른 사진이 기본, 다른 공간 404, 크기 상한, 내보내기·복원 후 같은 해시, 합치기에서
기본 사진이 부딪히지 않음, MCP는 이름·한 줄 소개·링크만.

사진은 모두 코드로 만든 합성 바이트다 (얼굴·실제 사진 없음). JPEG는 구조만 맞춘 작은 파일이고
(서버는 화소를 디코딩하지 않는다), PNG는 zlib로 만든 실제 2색 그림이다.
"""

import hashlib
import json
import struct
import tarfile
import zlib
from datetime import date

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.db.models import Profile, ProfilePhoto
from career_core.domain.profiles import (
    MAX_PHOTO_BYTES,
    ProfileRuleError,
    has_metadata,
    strip_metadata,
)
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.ports import ActorContext
from career_core.usecases import profiles as uc
from career_core.usecases.export import export_workspace, merge_workspace, restore_workspace
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.anyio

# ---------------------------------------------------------------- 합성 그림

SCAN = b"\x12\x34\xff\x00\x56\xff\xd0\x78\x9a"  # 채움(FF00)·재시작(FFD0)이 섞인 부호화 데이터


def _seg(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def jpeg(w=354, h=472, *, exif=True, comment=True, trailer=False, thumb=False, seed=0) -> bytes:
    """구조만 맞춘 baseline JPEG. EXIF(APP1)에 가짜 GPS 글, 주석(COM), JFIF 썸네일, EOI 뒤 바이트 선택."""
    app0 = b"JFIF\x00\x01\x01\x01\x01\x2c\x01\x2c"
    app0 += b"\x01\x01\xff\xff\xff" if thumb else b"\x00\x00"
    out = b"\xff\xd8" + _seg(0xE0, app0)
    if exif:
        tiff = b"MM\x00*\x00\x00\x00\x08" + b"GPS 37.5665N 126.9780E synthetic camera"
        out += _seg(0xE1, b"Exif\x00\x00" + tiff)
        out += _seg(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta>synthetic</x:xmpmeta>")
        out += _seg(0xE2, b"ICC_PROFILE\x00\x01\x01synthetic-icc")
    if comment:
        out += _seg(0xFE, b"synthetic comment: taken on device X")
    out += _seg(0xDB, b"\x00" + bytes((i + seed) % 255 + 1 for i in range(64)))
    out += _seg(
        0xC0, b"\x08" + struct.pack(">HH", h, w) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    )
    out += _seg(0xC4, b"\x00" + bytes([1] + [0] * 15) + b"\x00")
    out += _seg(0xDA, b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00")
    out += SCAN + bytes([seed]) + b"\xff\xd9"
    if trailer:
        out += b"hidden trailer after EOI"
    return out


def _chunk(ctype: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body))


def png(w=30, h=40, *, text=True) -> bytes:
    """실제로 열리는 2색 PNG (위 절반 흰색, 아래 절반 회색). tEXt·eXIf·tIME 선택."""
    rows = b"".join(
        b"\x00" + (b"\xff\xff\xff" if y < h // 2 else b"\x80\x80\x80") * w for y in range(h)
    )
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    out = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
    out += _chunk(b"pHYs", struct.pack(">IIB", 11811, 11811, 1))
    if text:
        out += _chunk(b"tEXt", b"Comment\x00synthetic author")
        out += _chunk(b"eXIf", b"MM\x00*\x00\x00\x00\x08GPS synthetic")
        out += _chunk(b"tIME", b"\x07\xea\x0b\x11\x0c\x00\x00")
    out += _chunk(b"IDAT", zlib.compress(rows)) + _chunk(b"IEND", b"")
    return out


# ---------------------------------------------------------------- 메타데이터 지우기 (DB 없음)


def test_strip_jpeg_removes_exif_xmp_icc_comment_thumbnail_and_trailer():
    raw = jpeg(trailer=True, thumb=True)
    assert has_metadata(raw)
    clean = strip_metadata(raw)
    assert (clean.mime, clean.width, clean.height) == ("image/jpeg", 354, 472)
    assert set(clean.removed) == {"APP1", "APP2", "COM", "APP0 thumbnail", "trailer"}
    for needle in (b"Exif", b"GPS", b"xmpmeta", b"ICC_PROFILE", b"synthetic comment", b"hidden"):
        assert needle not in clean.data
    assert b"\xff\xe1" not in clean.data and b"\xff\xfe" not in clean.data
    # 화소 데이터(부호화된 scan)는 바이트 그대로, JFIF 밀도는 남는다
    assert SCAN in clean.data and clean.data.endswith(b"\xff\xd9")
    assert clean.data[2:20] == b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x01\x2c\x01\x2c\x00\x00"
    # 두 번 지워도 같다 (이미 깨끗한 파일은 그대로)
    again = strip_metadata(clean.data)
    assert again.data == clean.data and again.removed == ()
    assert not has_metadata(clean.data)


def test_strip_png_keeps_pixels_and_drops_text_chunks():
    raw = png()
    clean = strip_metadata(raw)
    assert (clean.mime, clean.width, clean.height) == ("image/png", 30, 40)
    assert set(clean.removed) == {"tEXt", "eXIf", "tIME"}
    assert b"synthetic" not in clean.data and b"eXIf" not in clean.data
    assert b"pHYs" in clean.data  # 인쇄 밀도는 남긴다
    assert clean.data == png(text=False)


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"GIF89a....", "unsupported_type"),
        (b"%PDF-1.7 synthetic", "unsupported_type"),
        (jpeg()[:-40], "invalid_image"),  # 잘린 파일
        (jpeg().replace(b"\xff\xc0", b"\xff\xc4", 1)[:120], "invalid_image"),
        (png()[:-6], "invalid_image"),
        (png().replace(b"IDAT", b"IDAX"), "invalid_image"),  # CRC가 맞지 않는다
    ],
)
def test_strip_rejects_other_or_broken_files(data, code):
    with pytest.raises(ProfileRuleError) as e:
        strip_metadata(data)
    assert e.value.code == code


# ---------------------------------------------------------------- HTTP


class FakeResolver:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


@pytest.fixture
async def env(db, tmp_path):
    ws_a, a = await create_workspace(db, name="A (합성)", owner_display_name="amy")
    ws_b, b = await create_workspace(db, name="B (합성)", owner_display_name="ben")
    owner = ActorContext(a, ws_a, scopes_for_role(Role.OWNER))
    other = ActorContext(b, ws_b, scopes_for_role(Role.OWNER))
    viewer = ActorContext(a, ws_a, scopes_for_role(Role.VIEWER))
    blobs = LocalBlobStore(tmp_path / "blobs")
    tokens = {"owner": owner, "other": other, "viewer": viewer}
    app = FastAPI()
    app.include_router(build_router(CoreDeps(identity=FakeResolver(tokens), db=db, blobs=blobs)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        yield {"c": c, "owner": owner, "other": other, "blobs": blobs, "root": tmp_path / "blobs"}


def auth(token):
    return {"Authorization": f"Bearer {token}"}


async def upload(c, data, spec="cm-3x4", token="owner", name="photo.jpg", mime="image/jpeg"):
    return await c.post(
        "/core/profile/photos",
        files={"file": (name, data, mime)},
        data={"spec": spec},
        headers=auth(token),
    )


async def test_one_profile_per_workspace(db, env):
    c = env["c"]
    empty = (await c.get("/core/profile", headers=auth("owner"))).json()
    assert empty["name"] is None and empty["links"] == [] and empty["updated_at"] is None
    body = {
        "name": "  김합성 ",
        "name_en": "Hapseong Kim",
        "birth_date": "1995-03-04",
        "email": "synthetic@example.test",
        "phone": "010-0000-0000",
        "address": "서울시 합성구",
        "headline": "데이터로 문제를 푸는 백엔드 개발자",
        "links": [
            {"label": "GitHub", "url": "github.com/synthetic"},
            {"label": "", "url": ""},  # 빈 줄은 무시
        ],
    }
    first = await c.put("/core/profile", json=body, headers=auth("owner"))
    assert first.status_code == 200, first.text
    got = first.json()
    assert got["name"] == "김합성" and got["birth_date"] == "1995-03-04"
    assert got["links"] == [{"label": "GitHub", "url": "https://github.com/synthetic"}]
    # 다시 저장하면 같은 행을 바꾼다 (빠진 칸은 비운다)
    second = await c.put("/core/profile", json={"name": "김합성"}, headers=auth("owner"))
    assert second.json()["phone"] is None and second.json()["links"] == []
    async with db.workspace_session(env["owner"].workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(Profile)) == 1
    # DB도 공간당 하나만 받는다
    with pytest.raises(IntegrityError):
        async with db.workspace_session(env["owner"].workspace_id) as s:
            s.add(Profile(workspace_id=env["owner"].workspace_id, links=[]))
    # 다른 공간은 비어 있고, 저장해도 따로다
    assert (await c.get("/core/profile", headers=auth("other"))).json()["name"] is None
    # 보기 권한은 읽기만
    assert (await c.get("/core/profile", headers=auth("viewer"))).json()["name"] == "김합성"
    assert (await c.put("/core/profile", json={}, headers=auth("viewer"))).status_code == 403


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ({"email": "not-an-email"}, "invalid_email"),
        ({"phone": "call me"}, "invalid_phone"),
        ({"links": [{"label": "x", "url": "javascript:alert(1)"}]}, "invalid_link"),
        (
            {"links": [{"label": "x", "url": f"https://e.test/{i}"} for i in range(11)]},
            "too_many_links",
        ),
        ({"birth_date": "2999-01-01"}, "invalid_birth_date"),
    ],
)
async def test_profile_rules(env, patch, code):
    res = await env["c"].put("/core/profile", json=patch, headers=auth("owner"))
    assert (res.status_code, res.json()["detail"]) == (422, code)


async def test_upload_stores_only_stripped_bytes(env):
    c, root = env["c"], env["root"]
    raw = jpeg(trailer=True)
    res = await upload(c, raw)
    assert res.status_code == 200, res.text
    photo = res.json()
    assert photo["is_default"] is True and photo["deduplicated"] is False
    assert {"APP1", "COM", "trailer"} <= set(photo["removed_metadata"])
    assert (photo["width_px"], photo["height_px"], photo["mime"]) == (354, 472, "image/jpeg")
    # 저장된 파일 = 지운 바이트. 원본 바이트(원본 해시)는 어디에도 없다
    stored = [p for p in root.rglob("*") if p.is_file()]
    assert len(stored) == 1
    data = stored[0].read_bytes()
    assert b"Exif" not in data and b"\xff\xe1" not in data and b"GPS" not in data
    assert hashlib.sha256(data).hexdigest() == photo["sha256"] == stored[0].name
    assert photo["sha256"] != hashlib.sha256(raw).hexdigest() and photo["bytes"] == len(data)
    got = await c.get(f"/core/profile/photos/{photo['id']}/file", headers=auth("owner"))
    assert got.status_code == 200 and got.content == data
    assert got.headers["content-type"] == "image/jpeg"
    assert got.headers["cache-control"] == "private, no-store"
    # 같은 바이트(메타데이터만 다른 파일 포함)는 한 장
    again = (await upload(c, jpeg(exif=False, comment=False))).json()
    assert again["deduplicated"] is True and again["id"] == photo["id"]
    # PNG도 받는다 (글 덩어리는 지운다)
    p = (await upload(c, png(), spec="custom", name="p.png", mime="image/png")).json()
    assert p["mime"] == "image/png" and p["is_default"] is False and "tEXt" in p["removed_metadata"]
    assert (await c.get(f"/core/profile/photos/{p['id']}/file", headers=auth("owner"))).content == (
        png(text=False)
    )


async def test_upload_limits(env):
    c = env["c"]
    big = jpeg()[:-2] + b"\x00" * (MAX_PHOTO_BYTES + 10) + b"\xff\xd9"
    res = await upload(c, big)
    assert (res.status_code, res.json()["detail"]) == (413, "too_large")
    res = await upload(c, b"GIF89a synthetic", name="a.gif", mime="image/gif")
    assert (res.status_code, res.json()["detail"]) == (415, "unsupported_type")
    res = await upload(c, jpeg(w=400, h=400), spec="cm-3x4")  # 3:4가 아니다
    assert (res.status_code, res.json()["detail"]) == (422, "spec_mismatch")
    res = await upload(c, jpeg(w=8000, h=6000), spec="custom")
    assert (res.status_code, res.json()["detail"]) == (422, "invalid_dimensions")
    res = await upload(c, jpeg(), spec="wallet")
    assert (res.status_code, res.json()["detail"]) == (422, "invalid_spec")
    res = await upload(c, jpeg()[:200])
    assert (res.status_code, res.json()["detail"]) == (422, "invalid_image")
    # 여권 3.5×4.5 (413×531 px, 300 dpi)와 600 dpi 3×4 (709×945)는 비율이 맞는다
    assert (await upload(c, jpeg(w=413, h=531), spec="cm-3.5x4.5")).status_code == 200
    assert (await upload(c, jpeg(w=709, h=945, seed=1), spec="cm-3x4")).status_code == 200
    assert (await c.post("/core/profile/photos", headers=auth("viewer"))).status_code == 403
    assert len((await c.get("/core/profile/photos", headers=auth("owner"))).json()) == 2


async def test_one_default_and_delete_moves_default(db, env):
    c, root = env["c"], env["root"]
    a = (await upload(c, jpeg(seed=1))).json()
    b = (await upload(c, jpeg(seed=2))).json()
    d = (await upload(c, jpeg(w=413, h=531, seed=3), spec="cm-3.5x4.5")).json()

    async def defaults():
        rows = (await c.get("/core/profile/photos", headers=auth("owner"))).json()
        return [r["id"] for r in rows if r["is_default"]]

    assert await defaults() == [a["id"]]  # 첫 사진이 기본
    res = await c.patch(
        f"/core/profile/photos/{b['id']}", json={"is_default": True}, headers=auth("owner")
    )
    assert res.status_code == 200 and res.json()["is_default"] is True
    assert await defaults() == [b["id"]]  # 다른 사진을 기본으로 지정하면 바뀐다
    assert (await c.get("/core/profile", headers=auth("owner"))).json()["default_photo_id"] == b[
        "id"
    ]
    res = await c.patch(
        f"/core/profile/photos/{b['id']}", json={"is_default": False}, headers=auth("owner")
    )
    assert res.status_code == 422
    # 규격 바꾸기도 비율을 본다
    res = await c.patch(
        f"/core/profile/photos/{a['id']}", json={"spec": "cm-3.5x4.5"}, headers=auth("owner")
    )
    assert (res.status_code, res.json()["detail"]) == (422, "spec_mismatch")

    # 기본 사진을 지우면 남은 사진 중 가장 최근 것이 기본
    res = await c.delete(f"/core/profile/photos/{b['id']}", headers=auth("owner"))
    assert res.json() == {"default_photo_id": d["id"]}
    assert await defaults() == [d["id"]]
    assert not any(p.name == b["sha256"] for p in root.rglob("*"))  # 파일도 지웠다
    # 기본이 아닌 사진을 지우면 기본은 그대로
    await c.delete(f"/core/profile/photos/{a['id']}", headers=auth("owner"))
    assert await defaults() == [d["id"]]
    res = await c.delete(f"/core/profile/photos/{d['id']}", headers=auth("owner"))
    assert res.json() == {"default_photo_id": None}
    assert await defaults() == []
    assert (await c.get("/core/profile", headers=auth("owner"))).json()["default_photo_id"] is None
    assert not [p for p in root.rglob("*") if p.is_file()]
    async with db.workspace_session(env["owner"].workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(ProfilePhoto)) == 0


async def test_other_workspace_cannot_see_photos(env):
    c = env["c"]
    mine = (await upload(c, jpeg())).json()
    pid = mine["id"]
    assert (await c.get("/core/profile/photos", headers=auth("other"))).json() == []
    assert (
        await c.get(f"/core/profile/photos/{pid}/file", headers=auth("other"))
    ).status_code == 404
    res = await c.patch(
        f"/core/profile/photos/{pid}", json={"is_default": True}, headers=auth("other")
    )
    assert res.status_code == 404
    assert (await c.delete(f"/core/profile/photos/{pid}", headers=auth("other"))).status_code == 404
    # 다른 공간이 같은 바이트를 올려도 따로 저장된다 (내 사진은 그대로 기본)
    theirs = (await upload(c, jpeg(), token="other")).json()
    assert theirs["id"] != pid and theirs["deduplicated"] is False
    assert (
        await c.get(f"/core/profile/photos/{pid}/file", headers=auth("owner"))
    ).status_code == 200
    assert (await c.get("/core/profile", headers=auth("owner"))).json()["default_photo_id"] == pid
    assert (await c.get("/core/profile/photos/x/file", headers=auth("owner"))).status_code == 422


# ---------------------------------------------------------------- 내보내기·복원·합치기


async def test_export_restore_keeps_photo_bytes_and_default(db, env, tmp_path):
    owner, blobs = env["owner"], env["blobs"]
    await uc.save_profile(
        db, owner, name="김합성", birth_date=date(1995, 3, 4), headline="합성 소개"
    )
    first = await uc.upload_photo(db, blobs, owner, data=jpeg(seed=1), spec="cm-3x4")
    second = await uc.upload_photo(db, blobs, owner, data=png(), spec="custom")
    await uc.update_photo(db, owner, second.photo.id, is_default=True)
    before = sorted((p.spec, p.sha256, p.is_default) for p in await uc.list_photos(db, owner))

    out = tmp_path / "a.tar.gz"
    report = await export_workspace(db, blobs, owner, out)
    with tarfile.open(out, "r:gz") as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())
        names = tar.getnames()
    rows = {t["name"]: t["rows"] for t in manifest["tables"]}
    assert (rows["core.profiles"], rows["core.profile_photos"]) == (1, 2)
    assert {f"blobs/{first.photo.sha256}", f"blobs/{second.photo.sha256}"} <= set(names)

    fresh = LocalBlobStore(tmp_path / "restored-blobs")
    copy = await restore_workspace(db, fresh, out, new_ids=True)
    assert copy.rows_total == report.rows_total
    [new_owner] = copy.owners
    actor = ActorContext(new_owner, copy.workspace_id, scopes_for_role(Role.OWNER))
    restored = await uc.list_photos(db, actor)
    assert sorted((p.spec, p.sha256, p.is_default) for p in restored) == before
    assert {p.id for p in restored}.isdisjoint({first.photo.id, second.photo.id})
    for p in restored:
        _, data = await uc.photo_file(db, fresh, actor, p.id)
        assert hashlib.sha256(data).hexdigest() == p.sha256
    profile = await uc.get_profile(db, actor)
    assert (profile.name, profile.birth_date) == ("김합성", date(1995, 3, 4))
    assert profile.default_photo_id == next(p.id for p in restored if p.is_default)


async def test_merge_into_workspace_with_its_own_default(db, env, tmp_path):
    """두 공간 모두 기본 사진이 있어도 합치기가 부딪히지 않는다: 받는 공간의 프로필·기본 사진이 남는다."""
    owner, other, blobs = env["owner"], env["other"], env["blobs"]
    await uc.save_profile(db, owner, name="A 이름")
    mine = await uc.upload_photo(db, blobs, owner, data=jpeg(seed=1), spec="cm-3x4")
    await uc.upload_photo(db, blobs, owner, data=jpeg(seed=9), spec="cm-3x4")  # 둘 다 들어온다
    await uc.save_profile(db, other, name="B 이름")
    theirs = await uc.upload_photo(db, blobs, other, data=jpeg(seed=2), spec="cm-3x4")
    shared = await uc.upload_photo(db, blobs, other, data=jpeg(seed=9), spec="cm-3x4")

    out = tmp_path / "a.tar.gz"
    await export_workspace(db, blobs, owner, out)
    merged = await merge_workspace(db, blobs, other, out)
    by = {t.name: t for t in merged.tables}
    assert by["core.profiles"].added == 0  # 공간당 하나: 받는 공간 것이 남는다
    assert by["core.profile_photos"].added == 1  # 같은 바이트(seed 9)는 겹쳐서 건너뛴다
    photos = await uc.list_photos(db, other)
    assert len(photos) == 3
    assert [p.id for p in photos if p.is_default] == [theirs.photo.id]
    assert shared.photo.id in {p.id for p in photos}
    assert mine.photo.sha256 in {p.sha256 for p in photos}
    assert (await uc.get_profile(db, other)).name == "B 이름"


# ---------------------------------------------------------------- MCP


async def test_mcp_profile_summary_shares_only_name_headline_links(db, env):
    from career_core.interfaces.mcp.tools import TOOLS_BY_NAME

    c, owner, blobs = env["c"], env["owner"], env["blobs"]
    await uc.save_profile(
        db,
        owner,
        name="김합성",
        birth_date=date(1995, 3, 4),
        email="synthetic@example.test",
        phone="010-1234-5678",
        address="서울시 합성구 가상로 1",
        headline="합성 데이터 엔지니어",
        links=[("GitHub", "https://github.com/synthetic")],
    )
    await uc.upload_photo(db, blobs, owner, data=jpeg(), spec="cm-3x4")
    tool = TOOLS_BY_NAME["profile_summary"]
    assert tool.read_only and tool.action == "profile.read"

    async def call(token):
        res = await c.post(
            "/core/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "profile_summary", "arguments": {}},
            },
            headers=auth(token),
        )
        return res.json()["result"]

    result = await call("owner")
    got = result["structuredContent"]
    assert got["record"] == "profile"
    assert (got["name"], got["headline"]) == ("김합성", "합성 데이터 엔지니어")
    assert got["links"] == [{"label": "GitHub", "url": "https://github.com/synthetic"}]
    assert not {"birth_date", "email", "phone", "address", "photos", "default_photo_id"} & set(got)
    raw = json.dumps(result, ensure_ascii=False)
    for secret in ("1995", "010-1234-5678", "synthetic@example.test", "가상로", "image/jpeg"):
        assert secret not in raw
    other = (await call("other"))["structuredContent"]
    assert (other["name"], other["headline"], other["links"]) == (None, None, [])

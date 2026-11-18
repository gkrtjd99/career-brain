"""옛 한글(.hwp, 한글 5.x) 글 읽기 (2026-11-19).

시험 파일은 이 시험이 만든다: 작은 OLE 복합 문서(CFB v3) 작성기 + "한글 문서 파일 형식 5.0"의 FileHeader·
BodyText 레코드. 글은 모두 합성이다.
"""

from __future__ import annotations

import zlib

import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.hwp import HWPTAG_PARA_TEXT, HwpTextParser, para_text, records
from career_core.adapters.parsers import default_local_parsers
from career_core.db import DocumentBlock, ParseRun
from career_core.domain.workspace import Role, scopes_for_role
from career_core.jobs.worker import parsing_handlers, run_until_idle
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext, ParseError
from career_core.uploads import HWP_MIME, resolve_mime
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.sources import upload_source
from career_core.usecases.workspaces import create_workspace
from sqlalchemy import select

# ---------------------------------------------------------------- CFB 작성기 (시험용)

SECTOR = 512
MINI = 64
CUTOFF = 4096
FREE, END, FATSECT, NOSTREAM = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF


def _u32(n: int) -> bytes:
    return n.to_bytes(4, "little")


def _chain(start: int, count: int, table: list[int]) -> None:
    for k in range(count):
        table[start + k] = start + k + 1 if k < count - 1 else END


def make_cfb(streams: dict[str, bytes]) -> bytes:
    """``{"BodyText/Section0": b"..."}`` → OLE 복합 문서. 4096바이트 아래는 mini stream에 넣는다."""
    # 디렉터리 트리
    nodes: list[dict] = [{"name": "Root Entry", "type": 5, "children": [], "data": b""}]
    index: dict[str, int] = {"": 0}
    for path, data in streams.items():
        parts = path.split("/")
        parent = ""
        for depth, part in enumerate(parts):
            here = "/".join(parts[: depth + 1])
            if here not in index:
                leaf = depth == len(parts) - 1
                index[here] = len(nodes)
                nodes.append(
                    {
                        "name": part,
                        "type": 2 if leaf else 1,
                        "children": [],
                        "data": data if leaf else b"",
                    }
                )
                nodes[index[parent]]["children"].append(index[here])
            parent = here
    for n in nodes:
        n.update(left=NOSTREAM, right=NOSTREAM, child=NOSTREAM, start=END, size=0)

    def tree(ids: list[int]) -> int:
        if not ids:
            return NOSTREAM
        mid = len(ids) // 2
        nodes[ids[mid]]["left"] = tree(ids[:mid])
        nodes[ids[mid]]["right"] = tree(ids[mid + 1 :])
        return ids[mid]

    for n in nodes:
        kids = sorted(
            n["children"], key=lambda i: (len(nodes[i]["name"]), nodes[i]["name"].upper())
        )
        n["child"] = tree(kids)

    # mini stream
    mini = bytearray()
    minifat: list[int] = []
    for n in nodes:
        if n["type"] == 2 and len(n["data"]) < CUTOFF:
            count = max(1, -(-len(n["data"]) // MINI))
            n["start"], n["size"] = len(minifat), len(n["data"])
            minifat.extend([FREE] * count)
            _chain(n["start"], count, minifat)
            mini += n["data"].ljust(count * MINI, b"\0")
    big = [n for n in nodes if n["type"] == 2 and len(n["data"]) >= CUTOFF]
    dir_sectors = -(-len(nodes) // 4)
    minifat_sectors = -(-len(minifat) * 4 // SECTOR) if minifat else 0
    mini_sectors = -(-len(mini) // SECTOR)
    big_sectors = [-(-len(n["data"]) // SECTOR) for n in big]
    body = dir_sectors + minifat_sectors + mini_sectors + sum(big_sectors)
    nfat = 1
    while nfat * (SECTOR // 4) < nfat + body:
        nfat += 1
    assert nfat <= 109
    fat = [FREE] * (nfat * SECTOR // 4)
    pos = 0
    for k in range(nfat):
        fat[k] = FATSECT
    pos = nfat
    first_dir = pos
    _chain(pos, dir_sectors, fat)
    pos += dir_sectors
    first_minifat = pos if minifat_sectors else END
    _chain(pos, minifat_sectors, fat)
    pos += minifat_sectors
    nodes[0]["start"] = pos if mini_sectors else END
    nodes[0]["size"] = len(mini)
    _chain(pos, mini_sectors, fat)
    pos += mini_sectors
    for n, count in zip(big, big_sectors, strict=True):
        n["start"], n["size"] = pos, len(n["data"])
        _chain(pos, count, fat)
        pos += count

    header = (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        + b"\0" * 16
        + (0x3E).to_bytes(2, "little")
        + (3).to_bytes(2, "little")
        + b"\xfe\xff"
        + (9).to_bytes(2, "little")
        + (6).to_bytes(2, "little")
        + b"\0" * 6
        + _u32(0)
        + _u32(nfat)
        + _u32(first_dir)
        + _u32(0)
        + _u32(CUTOFF)
        + _u32(first_minifat)
        + _u32(minifat_sectors)
        + _u32(END)
        + _u32(0)
        + b"".join(_u32(k if k < nfat else FREE) for k in range(109))
    )
    assert len(header) == SECTOR
    entries = bytearray()
    for n in nodes:
        name = n["name"].encode("utf-16-le") + b"\0\0"
        entries += (
            name.ljust(64, b"\0")
            + len(name).to_bytes(2, "little")
            + bytes([n["type"], 1])
            + _u32(n["left"])
            + _u32(n["right"])
            + _u32(n["child"])
            + b"\0" * 36
            + _u32(n["start"])
            + n["size"].to_bytes(8, "little")
        )
    while len(entries) % SECTOR:
        entries += (
            b"\0" * 64 + b"\0\0" + b"\0\0" + _u32(NOSTREAM) * 3 + b"\0" * 36 + _u32(0) + b"\0" * 8
        )
    out = bytearray(header)
    out += b"".join(_u32(x) for x in fat)
    out += entries
    if minifat_sectors:
        out += b"".join(_u32(x) for x in minifat).ljust(minifat_sectors * SECTOR, b"\xff")
    out += bytes(mini).ljust(mini_sectors * SECTOR, b"\0")
    for n, count in zip(big, big_sectors, strict=True):
        out += n["data"].ljust(count * SECTOR, b"\0")
    return bytes(out)


# ---------------------------------------------------------------- 한글 5.x 시험 파일


def record(tag: int, payload: bytes, level: int = 0) -> bytes:
    size = len(payload)
    if size >= 0xFFF:
        return _u32(tag | level << 10 | 0xFFF << 20) + _u32(size) + payload
    return _u32(tag | level << 10 | size << 20) + payload


def para(text: str) -> bytes:
    return text.encode("utf-16-le") + b"\x0d\x00"


def ctrl(code: int) -> bytes:
    """인라인·확장 컨트롤 (8글자: 코드 + 6글자 정보 + 같은 코드)."""
    return code.to_bytes(2, "little") + b"\x00" * 12 + code.to_bytes(2, "little")


PARA_HEADER = 0x10 + 50  # 읽지 않는 레코드


def section(*paragraphs: bytes) -> bytes:
    out = b""
    for p in paragraphs:
        out += record(PARA_HEADER, b"\x00" * 22) + record(HWPTAG_PARA_TEXT, p, level=1)
    return out


def file_header(flags: int) -> bytes:
    sig = b"HWP Document File".ljust(32, b"\0")
    return (sig + bytes([0, 0, 1, 5]) + _u32(flags)).ljust(256, b"\0")


def make_hwp(*sections: bytes, compressed: bool = True, flags: int = 0) -> bytes:
    def pack(b: bytes) -> bytes:
        if not compressed:
            return b
        c = zlib.compressobj(9, zlib.DEFLATED, -15)
        return c.compress(b) + c.flush()

    streams = {"FileHeader": file_header(flags | (1 if compressed else 0))}
    for k, body in enumerate(sections):
        streams[f"BodyText/Section{k}"] = pack(body)
    streams["PrvText"] = "미리보기".encode("utf-16-le")
    return make_cfb(streams)


ESSAY = section(
    para("[세 번 실패한 배포를 끝까지 살렸습니다]"),
    "결제 서버 배포가 세 번 실패했을 때".encode("utf-16-le")
    + b"\x0a\x00"
    + para("로그를 하나씩 대조했습니다."),
    ctrl(11) + para("표 앞 컨트롤은 건너뜁니다."),
    para("기간"),
    ctrl(9) + para("탭 뒤 글"),
    para("   "),
)


# ---------------------------------------------------------------- 시험


def test_para_text_controls():
    assert para_text(para("가나다")) == "가나다"
    assert para_text("줄".encode("utf-16-le") + b"\x0a\x00" + para("바꿈")) == "줄\n바꿈"
    assert para_text(ctrl(11) + para("표")) == "표"  # 확장 컨트롤 16바이트 건너뜀
    assert para_text(ctrl(9) + para("탭")) == "\t탭"
    assert para_text("😀".encode("utf-16-le") + b"\x0d\x00") == "😀"  # 서로게이트 짝


def test_records_long_size_and_truncation():
    long = "가" * 3000
    [(tag, level, payload)] = list(records(record(HWPTAG_PARA_TEXT, para(long), level=2)))
    assert (tag, level, para_text(payload)) == (HWPTAG_PARA_TEXT, 2, long)
    with pytest.raises(ParseError, match="malformed"):
        list(records(record(HWPTAG_PARA_TEXT, para("끊김"))[:-3]))


@pytest.mark.parametrize("compressed", [True, False])
def test_hwp5_paragraphs_in_order(compressed):
    data = make_hwp(ESSAY, section(para("둘째 구역")), compressed=compressed)
    assert resolve_mime(data, None, "자소서.hwp") == HWP_MIME
    got = HwpTextParser().parse(data)
    assert [b.text for b in got.blocks] == [
        "[세 번 실패한 배포를 끝까지 살렸습니다]",
        "결제 서버 배포가 세 번 실패했을 때\n로그를 하나씩 대조했습니다.",
        "표 앞 컨트롤은 건너뜁니다.",
        "기간",
        "탭 뒤 글",
        "둘째 구역",
    ]
    assert all(b.page is None for b in got.blocks) and got.pages_total is None


def test_hwp5_large_section_uses_regular_sectors():
    paras = [para(f"{k}번째 문단입니다. 합성 글을 길게 늘립니다.") for k in range(400)]
    got = HwpTextParser().parse(make_hwp(section(*paras), compressed=False))
    assert len(got.blocks) == 400 and got.blocks[-1].text.startswith("399번째")


def test_hwp5_refusals():
    with pytest.raises(ParseError, match="encrypted"):
        HwpTextParser().parse(make_hwp(ESSAY, flags=0x2))
    with pytest.raises(ParseError, match="hwp_distribution"):
        HwpTextParser().parse(make_hwp(ESSAY, flags=0x4))
    # 한글 3.0 이하는 복합 문서가 아니다
    with pytest.raises(ParseError, match="hwp_unsupported"):
        HwpTextParser().parse(b"HWP Document File V3.00 \x1a\x01\x02\x03\x04\x05" + b"\0" * 2000)
    # 다른 OLE 문서처럼 FileHeader가 없으면
    with pytest.raises(ParseError, match="hwp_unsupported"):
        HwpTextParser().parse(make_cfb({"WordDocument": b"\0" * 100}))
    # 망가진 압축
    bad = make_cfb({"FileHeader": file_header(1), "BodyText/Section0": b"\xff" * 300})
    with pytest.raises(ParseError, match="malformed"):
        HwpTextParser().parse(bad)
    # 압축 폭탄: 정한 크기를 넘게 풀리면 끊는다
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    bomb = c.compress(b"\0" * (40 * 1024 * 1024)) + c.flush()
    with pytest.raises(ParseError, match="file_too_complex"):
        HwpTextParser().parse(make_cfb({"FileHeader": file_header(1), "BodyText/Section0": bomb}))


@pytest.mark.anyio
async def test_hwp_upload_is_read_into_blocks(db, tmp_path):
    ws, subject = await create_workspace(db, name="A", owner_display_name="alice")
    owner = ActorContext(subject, ws, scopes_for_role(Role.OWNER))
    blobs = LocalBlobStore(tmp_path / "blobs")
    deps = ParsingDeps(
        db=db, blobs=blobs, egress=ProviderEgressPolicy({}), local_parsers=default_local_parsers()
    )
    res = await upload_source(
        db,
        blobs,
        owner,
        filename="지원서.hwp",
        data=make_hwp(ESSAY),
        declared_mime="application/haansofthwp",
        max_bytes=1024 * 1024,
        data_class="private",
    )
    await run_until_idle(db, parsing_handlers(deps))
    async with db.workspace_session(ws) as s:
        run = await s.get(ParseRun, res.parse_run_id)
        texts = (
            await s.scalars(
                select(DocumentBlock.text)
                .where(DocumentBlock.parse_run_id == run.id)
                .order_by(DocumentBlock.ordinal)
            )
        ).all()
    assert (run.status, run.error_code) == ("complete", None)
    assert texts[0] == "[세 번 실패한 배포를 끝까지 살렸습니다]" and len(texts) == 5

"""옛 한글 문서(.hwp, 한글 5.x) 글 읽기 (2026-11-19).

모아 둔 자기소개서·이력서는 .hwp가 많다. 한글 5.x 파일은 OLE 복합 문서(CFB)이고, 본문은
``BodyText/Section{n}`` 스트림의 레코드(대개 raw deflate 압축)에 들어 있다. 한컴이 공개한 "한글 문서 파일 형식
5.0" 문서를 따라 문단 글(``HWPTAG_PARA_TEXT``)만 읽는다.

- 복합 문서는 ``olefile``(BSD-2, 순수 파이썬, 널리 쓰임)로 연다. 레코드 해석만 여기서 한다.
- 상한: 원본 크기는 업로드 상한, 구역 ``MAX_SECTIONS``개, 구역 하나를 푼 크기 ``MAX_SECTION_BYTES``, 블록
  ``MAX_BLOCKS``개. 넘으면 ``file_too_complex``·``too_many_blocks``. 압축 폭탄은 풀 크기를 정해 두고 끊는다.
- 문단 하나 = 블록 하나. 표 칸의 문단도 같은 순서로 나온다 (칸마다 블록). 쪽 정보는 없다.
- 컨트롤 문자: 글자 하나짜리(줄 바꿈 10, 문단 끝 13 등)는 줄 바꿈이나 지우기, 인라인·확장 컨트롤(표·그림·
  각주 자리 등)은 8글자(16바이트)를 건너뛴다. 각주·글상자 안 글도 레코드로 따로 나오므로 그대로 읽힌다.
- 암호를 건 문서는 ``encrypted``, 배포용 문서(본문이 ``ViewText``에 암호화)는 ``hwp_distribution``.
  한글 3.0 이하(복합 문서가 아님)는 여전히 ``hwp_unsupported``.
- 원본 바이트를 실행하거나 바깥으로 보내지 않는다. 글만 꺼낸다.
"""

from __future__ import annotations

import io
import re
import zlib

from career_core.ports import LocalParseResult, ParsedBlock, ParseError

SIGNATURE = b"HWP Document File"
MAX_SECTIONS = 200
MAX_SECTION_BYTES = 32 * 1024 * 1024
MAX_BLOCKS = 5000
MAX_RECORDS = 2_000_000

HWPTAG_BEGIN = 0x10
HWPTAG_PARA_TEXT = HWPTAG_BEGIN + 51  # 67

FLAG_COMPRESSED = 0x1
FLAG_ENCRYPTED = 0x2
FLAG_DISTRIBUTION = 0x4

# 글자 하나 차지 (2바이트): 0 쓰지 않음, 10 줄 바꿈, 13 문단 끝, 24~31 하이픈·빈칸 등
_CHAR_CONTROLS = frozenset({0, 10, 13, *range(24, 32)})
_SECTION = re.compile(r"^Section(\d+)$")


def _clean(text: str) -> str:
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r" ?\n ?", "\n", text).strip()


def para_text(data: bytes) -> str:
    """``HWPTAG_PARA_TEXT`` 내용(UTF-16LE + 컨트롤) → 글."""
    out: list[str] = []
    i, n = 0, len(data) - 1
    while i < n:
        code = data[i] | (data[i + 1] << 8)
        if code >= 32:
            out.append(chr(code))
            i += 2
        elif code in _CHAR_CONTROLS:
            if code == 10:
                out.append("\n")
            elif code in (30, 31):  # 묶음 빈칸·고정폭 빈칸
                out.append(" ")
            i += 2
        else:
            if code == 9:
                out.append("\t")  # 탭도 인라인 컨트롤 (8글자)
            i += 16
    # 서로게이트 짝(이모지 등)을 붙인다
    return "".join(out).encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def records(data: bytes):
    """레코드 ``(tag, level, payload)``. 머리 32비트 = tag 10 · level 10 · size 12 (0xFFF면 다음 4바이트)."""
    i, n, count = 0, len(data), 0
    while i + 4 <= n:
        head = int.from_bytes(data[i : i + 4], "little")
        tag, level, size = head & 0x3FF, (head >> 10) & 0x3FF, head >> 20
        i += 4
        if size == 0xFFF:
            if i + 4 > n:
                raise ParseError("malformed")
            size = int.from_bytes(data[i : i + 4], "little")
            i += 4
        if i + size > n:
            raise ParseError("malformed")
        count += 1
        if count > MAX_RECORDS:
            raise ParseError("file_too_complex")
        yield tag, level, data[i : i + size]
        i += size


def _inflate(raw: bytes) -> bytes:
    d = zlib.decompressobj(-15)
    try:
        out = d.decompress(raw, MAX_SECTION_BYTES)
    except zlib.error as exc:
        raise ParseError("malformed") from exc
    if d.unconsumed_tail:
        raise ParseError("file_too_complex")
    return out


class HwpTextParser:
    def parse(self, data: bytes) -> LocalParseResult:
        try:
            import olefile
        except ImportError as exc:  # 설치에서 뺐으면 예전처럼 안내
            raise ParseError("hwp_unsupported") from exc
        if not olefile.isOleFile(io.BytesIO(data)):
            raise ParseError("hwp_unsupported")  # 한글 3.0 이하 등
        try:
            ole = olefile.OleFileIO(io.BytesIO(data))
        except Exception as exc:  # olefile은 손상 종류마다 다른 예외를 낸다
            raise ParseError("malformed") from exc
        try:
            return self._read(ole)
        finally:
            ole.close()

    def _read(self, ole) -> LocalParseResult:
        if not ole.exists("FileHeader"):
            raise ParseError("hwp_unsupported")
        header = ole.openstream("FileHeader").read(256)
        if not header.startswith(SIGNATURE) or len(header) < 40:
            raise ParseError("hwp_unsupported")
        flags = int.from_bytes(header[36:40], "little")
        if flags & FLAG_ENCRYPTED:
            raise ParseError("encrypted")
        if flags & FLAG_DISTRIBUTION:
            raise ParseError("hwp_distribution")
        sections = sorted(
            (int(m.group(1)), path)
            for path in ole.listdir(streams=True, storages=False)
            if len(path) == 2 and path[0] == "BodyText" and (m := _SECTION.match(path[1]))
        )
        if not sections:
            raise ParseError("malformed")
        if len(sections) > MAX_SECTIONS:
            raise ParseError("file_too_complex")
        blocks: list[ParsedBlock] = []
        for _, path in sections:
            if ole.get_size("/".join(path)) > MAX_SECTION_BYTES:
                raise ParseError("file_too_complex")
            raw = ole.openstream(list(path)).read()
            body = _inflate(raw) if flags & FLAG_COMPRESSED else raw
            for tag, _level, payload in records(body):
                if tag != HWPTAG_PARA_TEXT:
                    continue
                text = _clean(para_text(payload))
                if not text:
                    continue
                if len(blocks) >= MAX_BLOCKS:
                    raise ParseError("too_many_blocks")
                blocks.append(ParsedBlock(page=None, text=text))
        return LocalParseResult(blocks=tuple(blocks), pages_total=None)


__all__ = ["HwpTextParser", "para_text", "records"]

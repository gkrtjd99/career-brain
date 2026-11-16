"""DOCX(zip + WordprocessingML) 안전하게 열기. 숨김 글 검사와 원본 파서가 같이 쓴다 (c0023).

믿을 수 없는 파일이므로 (plan §9 "과도한 압축 해제를 막는다"):

- 항목 수 상한 ``MAX_ENTRIES``. 넘으면 열지 않는다.
- 필요한 부분만 읽는다. 각 부분과 합계의 **풀린 크기** 상한을 중앙 디렉터리 값으로 먼저 보고,
  읽을 때도 상한 + 1바이트까지만 풀어 실제 크기로 다시 확인한다 (머리말 크기를 속인 압축 폭탄).
- DTD·개체 선언(``<!DOCTYPE``·``<!ENTITY``)이 있으면 거부한다. WordprocessingML에는 없고, 개체 확장
  폭탄·외부 개체를 막는다. 파서는 표준 라이브러리 ``xml.etree``다.
- 경로 이름으로 파일을 쓰지 않는다 (메모리에서만 읽는다).
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from xml.etree import ElementTree as ET

MAX_ENTRIES = 2000
MAX_PART_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 60 * 1024 * 1024
MAIN_PART = "word/document.xml"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocxError(ValueError):
    """``invalid_docx`` · ``file_too_complex`` · ``file_too_large``"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def is_docx(data: bytes) -> bool:
    """zip이고 본문 부분(``word/document.xml``)이 있는가. 항목이 너무 많으면 아니라고 본다."""
    if not data.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            return len(infos) <= MAX_ENTRIES and any(i.filename == MAIN_PART for i in infos)
    except (zipfile.BadZipFile, ValueError, OSError):
        return False


def read_parts(
    data: bytes,
    wanted: Callable[[str], bool],
    *,
    max_part: int = MAX_PART_BYTES,
    max_total: int = MAX_TOTAL_BYTES,
) -> dict[str, bytes]:
    """``wanted(name)``인 부분을 읽는다. 본문 부분은 언제나 읽는다."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        raise DocxError("invalid_docx") from exc
    with z:
        infos = z.infolist()
        if len(infos) > MAX_ENTRIES:
            raise DocxError("file_too_complex")
        picked = [i for i in infos if i.filename == MAIN_PART or wanted(i.filename)]
        if MAIN_PART not in {i.filename for i in picked}:
            raise DocxError("invalid_docx")
        if (
            any(i.file_size > max_part for i in picked)
            or sum(i.file_size for i in picked) > max_total
        ):
            raise DocxError("file_too_large")
        out: dict[str, bytes] = {}
        total = 0
        for info in picked:
            if info.filename in out:  # 같은 이름이 두 번: 정상 파일에는 없다
                raise DocxError("invalid_docx")
            try:
                with z.open(info) as f:
                    body = f.read(max_part + 1)
            except (zipfile.BadZipFile, ValueError, OSError, EOFError) as exc:
                raise DocxError("invalid_docx") from exc
            total += len(body)
            if len(body) > max_part or total > max_total:
                raise DocxError("file_too_large")
            out[info.filename] = body
        return out


def parse_xml(xml: bytes) -> ET.Element:
    if b"<!DOCTYPE" in xml[:4096] or b"<!ENTITY" in xml:
        raise DocxError("invalid_docx")
    try:
        return ET.fromstring(xml)
    except ET.ParseError as exc:
        raise DocxError("invalid_docx") from exc


__all__ = [
    "MAIN_PART",
    "MAX_ENTRIES",
    "MAX_PART_BYTES",
    "MAX_TOTAL_BYTES",
    "DocxError",
    "W",
    "is_docx",
    "parse_xml",
    "read_parts",
]

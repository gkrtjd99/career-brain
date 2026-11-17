"""DOCX(zip + WordprocessingML) 안전하게 열기. 숨김 글 검사와 원본 파서가 같이 쓴다 (c0023).
PPTX·XLSX·HWPX(OWPML)도 같은 zip 상한과 XML 검사로 연다 (``PartReader``·``office_kind``, 2026-11-17).

믿을 수 없는 파일이므로 (plan §9 "과도한 압축 해제를 막는다"):

- 항목 수 상한 ``MAX_ENTRIES``. 넘으면 열지 않는다.
- 필요한 부분만 읽는다. 각 부분과 합계의 **풀린 크기** 상한을 중앙 디렉터리 값으로 먼저 보고,
  읽을 때도 상한 + 1바이트까지만 풀어 실제 크기로 다시 확인한다 (머리말 크기를 속인 압축 폭탄).
- DTD·개체 선언(``<!DOCTYPE``·``<!ENTITY``)이 있으면 거부한다. WordprocessingML에는 없고, 개체 확장
  폭탄·외부 개체를 막는다. 파서는 표준 라이브러리 ``xml.etree``다. UTF-8이 아닌 부분(UTF-16 BOM·
  앞쪽 NUL)은 이 바이트 검사를 비켜 갈 수 있어 열지 않는다 (Office·한글은 UTF-8로 쓴다).
- 경로 이름으로 파일을 쓰지 않는다 (메모리에서만 읽는다).
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from typing import Self
from xml.etree import ElementTree as ET

MAX_ENTRIES = 2000
MAX_PART_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 60 * 1024 * 1024
MAIN_PART = "word/document.xml"
# zip 안 본문 부분 → 형식. 앞의 것이 이긴다 (DOCX 판별은 c0023 그대로)
OFFICE_MAIN_PARTS = (
    ("docx", MAIN_PART),
    ("pptx", "ppt/presentation.xml"),
    ("xlsx", "xl/workbook.xml"),
    ("hwpx", "Contents/section0.xml"),
)
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocxError(ValueError):
    """``invalid_docx`` · ``file_too_complex`` · ``file_too_large``"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def is_docx(data: bytes) -> bool:
    """zip이고 본문 부분(``word/document.xml``)이 있는가. 항목이 너무 많으면 아니라고 본다."""
    return office_kind(data) == "docx"


def office_kind(data: bytes) -> str | None:
    """``docx``·``pptx``·``xlsx``·``hwpx`` 또는 None. zip 이름 목록만 본다 (풀지 않는다).
    항목이 너무 많으면 None (판별하지 않는다)."""
    if not data.startswith(b"PK\x03\x04"):
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            if len(infos) > MAX_ENTRIES:
                return None
            names = {i.filename for i in infos}
    except (zipfile.BadZipFile, ValueError, OSError):
        return None
    return next((kind for kind, main in OFFICE_MAIN_PARTS if main in names), None)


class PartReader:
    """필요한 부분을 하나씩 읽는 zip (``read_parts``와 같은 상한). 앞 부분을 읽고 나서야 다음에 읽을
    부분 이름을 아는 형식(PPTX 관계 파일 → 슬라이드)에 쓴다. ``with``로 연다."""

    def __init__(
        self,
        data: bytes,
        main: str,
        *,
        max_part: int = MAX_PART_BYTES,
        max_total: int = MAX_TOTAL_BYTES,
    ) -> None:
        try:
            self._zip = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, ValueError, OSError) as exc:
            raise DocxError("invalid_docx") from exc
        infos = self._zip.infolist()
        if len(infos) > MAX_ENTRIES:
            self._zip.close()
            raise DocxError("file_too_complex")
        self._infos = {i.filename: i for i in infos}
        if len(self._infos) != len(infos) or main not in self._infos:
            self._zip.close()
            raise DocxError("invalid_docx")  # 같은 이름이 두 번이거나 본문이 없다
        self._max_part = max_part
        self._max_total = max_total
        self._total = 0
        self._seen: set[str] = set()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self._zip.close()

    def names(self) -> list[str]:
        return list(self._infos)

    def read(self, name: str) -> bytes | None:
        """없으면 None. 같은 부분을 두 번 읽어도 합계에는 한 번만 센다."""
        info = self._infos.get(name)
        if info is None:
            return None
        if info.file_size > self._max_part:
            raise DocxError("file_too_large")
        try:
            with self._zip.open(info) as f:
                body = f.read(self._max_part + 1)
        except (zipfile.BadZipFile, ValueError, OSError, EOFError) as exc:
            raise DocxError("invalid_docx") from exc
        if len(body) > self._max_part:
            raise DocxError("file_too_large")
        if name not in self._seen:
            self._seen.add(name)
            self._total += len(body)
            if self._total > self._max_total:
                raise DocxError("file_too_large")
        return body

    def xml(self, name: str) -> ET.Element | None:
        body = self.read(name)
        return None if body is None else parse_xml(body)


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
    if xml.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in xml[:512]:
        raise DocxError("invalid_docx")  # UTF-16·32: 아래 바이트 검사를 비켜 간다
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
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
    "OFFICE_MAIN_PARTS",
    "DocxError",
    "PartReader",
    "W",
    "is_docx",
    "office_kind",
    "parse_xml",
    "read_parts",
]

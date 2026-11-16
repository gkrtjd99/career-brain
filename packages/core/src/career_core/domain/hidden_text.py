"""사용자가 올린 DOCX·PDF의 숨김 글 검사 (c0016). 경고만 하고 파일을 고치지 않는다.

plan §11 "요건 가시성 검사": 숨김 텍스트·심사 모델 대상 지시문은 만들지도 제안하지도 않고, 문서에 있으면
경고한다. 우리가 만든 파일은 ``document_export.inspect_export``가 보고, 여기서는 **다른 도구로 만든** 파일
(직접 꾸민 이력서·포트폴리오 PDF 등)을 제출 전에 본다.

찾는 것 (``code``):

- ``hidden_text_vanish``: DOCX 숨김 글(``w:vanish``·``w:specVanish``·``w:webHidden``, 그런 서식의 스타일 포함)
- ``hidden_text_invisible``: PDF 글자 그리기 방식 3·7(보이지 않게 그림)
- ``hidden_text_white``: 흰(또는 거의 흰) 글씨. DOCX는 문단·글자 배경색이 진하면 뺀다.
  PDF는 글 아래에 먼저 칠한 도형의 색과 비교한다 (c0023): 흰 바탕 위 흰 글씨만, 어두운 상자 위 흰 글씨는 뺀다
- ``hidden_text_same_color``: PDF 글자 색이 아래 배경(흰색이 아닌 칠한 도형)과 같은 색 (c0023)
- ``hidden_text_tiny``: 1pt 미만 글자
- ``hidden_text_outside_page``: 쪽 밖에 놓인 글 (PDF 글 위치, DOCX 글상자·틀 위치)
- ``hidden_text_annotation``: PDF 주석·양식 칸 글 중 숨김 표시·크기 0·쪽 밖, 쪽에 없는 양식 칸 값 (c0023)
- ``annotation_text`` (참고): PDF 메모·링크 설명처럼 인쇄되지 않는 주석 글 (c0023)
- 이미지 안 글(OCR)은 보지 않는다. 이미지·음영 위 글은 배경 색을 몰라 색 비교를 하지 않는다.
- 그 위에 전체 글의 보이지 않는 문자·심사 지시문 (``documents.ats_findings``, 반복 검사는 끔)

의존성은 이미 쓰는 ``pypdf``와 표준 라이브러리(``zipfile``, ``xml.etree``)뿐이다. 압축 폭탄을 막으려고
DOCX는 항목 수·풀린 크기를 먼저 보고, PDF는 앞쪽 ``MAX_PDF_PAGES``쪽만 본다.
"""

from __future__ import annotations

import io
import math
import zipfile
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import Any
from xml.etree import ElementTree as ET

from career_core.domain import docx_zip
from career_core.domain.documents import Finding, ats_findings

FORMATS = ("docx", "pdf")
MAX_DOCX_ENTRIES = docx_zip.MAX_ENTRIES
MAX_DOCX_PART_BYTES = docx_zip.MAX_PART_BYTES
MAX_DOCX_TOTAL_BYTES = docx_zip.MAX_TOTAL_BYTES
MAX_PDF_PAGES = 100
MAX_SAMPLES = 3
SAMPLE_CHARS = 80
# 이보다 작은 글자는 사람이 읽을 수 없다 (pt)
TINY_POINTS = 1.0
# 거의 흰색: 채널마다 0.94(= F0/FF) 이상
WHITE_LEVEL = 0.94
# 쪽 경계에서 이만큼(pt) 넘게 벗어나면 쪽 밖
PAGE_MARGIN_PT = 2.0

MESSAGES = {
    "hidden_text_vanish": "숨김 서식(보이지 않게 한 글)이 있습니다. 화면·인쇄에는 안 보이고 기계만 읽습니다.",
    "hidden_text_invisible": "보이지 않게 그린 글이 있습니다. 화면·인쇄에는 안 보이고 기계만 읽습니다.",
    "hidden_text_white": "흰 바탕 위 흰 글씨가 있습니다. 사람에게는 안 보이고 기계만 읽습니다.",
    "hidden_text_same_color": "배경과 같은 색의 글이 있습니다. 사람에게는 안 보이고 기계만 읽습니다.",
    "hidden_text_tiny": "1pt보다 작은 글자가 있습니다. 사람이 읽을 수 없는 크기입니다.",
    "hidden_text_outside_page": "쪽 밖에 놓인 글이 있습니다. 인쇄·화면에는 안 보입니다.",
    "hidden_text_annotation": "숨긴 주석·양식 칸 글이 있습니다 (숨김 표시·크기 0·쪽 밖·쪽에 없는 칸). 사람에게는 안 보이고 기계는 읽을 수 있습니다.",
    "annotation_text": "주석(메모·링크 설명) 글이 있습니다. 인쇄에는 안 나오고 화면에서도 잘 안 보이지만 기계는 읽을 수 있습니다.",
}
# 참고로만 보이는 종류 (나머지는 경고)
SEVERITY = {"annotation_text": "info"}


class InspectError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(slots=True)
class _Hits:
    """종류별 개수와 앞쪽 예시."""

    counts: dict[str, int] = field(default_factory=dict)
    samples: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    hidden_text: list[str] = field(default_factory=list)

    def add(self, kind: str, text: str | None, **where: Any) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1
        bucket = self.samples.setdefault(kind, [])
        if len(bucket) < MAX_SAMPLES:
            sample = " ".join((text or "").split())
            bucket.append({**where, "text": sample[:SAMPLE_CHARS] or None})
        if text:
            self.hidden_text.append(text)

    def findings(self) -> list[Finding]:
        return [
            Finding(
                kind,
                MESSAGES[kind],
                detail={"count": n, "samples": self.samples.get(kind, [])},
                severity=SEVERITY.get(kind, "warning"),
            )
            for kind, n in sorted(self.counts.items())
        ]


@dataclass(frozen=True, slots=True)
class FileInspection:
    format: str
    findings: list[Finding]
    pages: int | None
    pages_checked: int | None
    chars: int

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.code] = out.get(f.code, 0) + int(f.detail.get("count", 1))
        return dict(sorted(out.items()))


def detect_format(data: bytes, filename: str | None = None) -> str | None:
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        if docx_zip.is_docx(data):
            return "docx"
        # 항목이 너무 많은 zip도 DOCX로 보고 검사에서 거절한다 (file_too_complex)
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" in z.namelist():
                    return "docx"
        except (zipfile.BadZipFile, ValueError, OSError):
            return None
    return None


def inspect_file(data: bytes, filename: str | None = None) -> FileInspection:
    fmt = detect_format(data, filename)
    if fmt == "docx":
        return inspect_docx(data)
    if fmt == "pdf":
        return inspect_pdf(data)
    raise InspectError("unsupported_type")


def _text_findings(text: str, hidden: Sequence[str]) -> list[Finding]:
    """전체 글의 보이지 않는 문자·심사 지시문. 숨긴 글 안의 지시문은 따로 표시한다."""
    # 숨긴 글도 전체 글에 들어 있다. 지시문이 숨긴 글과 겹치면 표시만 더한다.
    out = [f for f in ats_findings(text, repetition=False) if f.code != "hidden_text_markup"]
    pieces = [" ".join(h.split()) for h in hidden if h.strip()]
    for f in out:
        sentence = " ".join((f.sentence or "").split())
        if (
            f.code == "screening_instruction"
            and sentence
            and any(p in sentence or sentence in p for p in pieces)
        ):
            f.detail["in_hidden_text"] = True
    return out


# ---------------------------------------------------------------- DOCX

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_EMU_PER_TWIP = 635
_A4 = (11906, 16838)  # twip
_CONTENT_PARTS = ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml")


def _on(el: ET.Element | None) -> bool:
    if el is None:
        return False
    val = (el.get(f"{_W}val") or "true").lower()
    return val not in ("0", "false", "off", "none")


def _whiteish(hex_value: str | None) -> bool:
    v = (hex_value or "").strip().lower()
    if v == "white":
        return True
    if len(v) != 6:
        return False
    try:
        rgb = [int(v[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    except ValueError:
        return False
    return min(rgb) >= WHITE_LEVEL


def _dark_fill(rpr: ET.Element | None, ppr: ET.Element | None) -> bool:
    """글자·문단 배경(shd fill, highlight)이 흰색이 아니면 흰 글씨가 보인다."""
    for parent in (rpr, ppr):
        if parent is None:
            continue
        shd = parent.find(f"{_W}shd")
        if shd is not None:
            fill = (shd.get(f"{_W}fill") or "auto").lower()
            if fill not in ("auto", "") and not _whiteish(fill):
                return True
        hl = parent.find(f"{_W}highlight")
        if hl is not None and (hl.get(f"{_W}val") or "none").lower() not in ("none", "white"):
            return True
    return False


def _rpr_kinds(rpr: ET.Element | None) -> set[str]:
    kinds: set[str] = set()
    if rpr is None:
        return kinds
    if any(_on(rpr.find(f"{_W}{tag}")) for tag in ("vanish", "specVanish", "webHidden")):
        kinds.add("hidden_text_vanish")
    color = rpr.find(f"{_W}color")
    if color is not None and _whiteish(color.get(f"{_W}val")):
        kinds.add("hidden_text_white")
    sz = rpr.find(f"{_W}sz")
    if sz is not None:
        try:
            if int(sz.get(f"{_W}val") or "24") / 2 < TINY_POINTS:
                kinds.add("hidden_text_tiny")
        except ValueError:
            pass
    return kinds


def _wanted_part(name: str) -> bool:
    return (
        name in _CONTENT_PARTS
        or name == "word/styles.xml"
        or (name.startswith("word/header") and name.endswith(".xml"))
        or (name.startswith("word/footer") and name.endswith(".xml"))
    )


def _safe_parts(data: bytes) -> dict[str, bytes]:
    """항목 수·풀린 크기 상한을 지켜 필요한 부분만 읽는다 (``docx_zip``, 원본 파서와 같은 규칙)."""
    try:
        return docx_zip.read_parts(data, _wanted_part)
    except docx_zip.DocxError as exc:
        raise InspectError(exc.code) from exc


def _parse(xml: bytes) -> ET.Element:
    # DTD·외부 개체는 쓰지 않는다 (WordprocessingML에는 없다). 있으면 거부한다.
    try:
        return docx_zip.parse_xml(xml)
    except docx_zip.DocxError as exc:
        raise InspectError(exc.code) from exc


def _hidden_styles(styles: ET.Element | None) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    if styles is None:
        return out
    for st in styles.iter(f"{_W}style"):
        sid = st.get(f"{_W}styleId")
        kinds = _rpr_kinds(st.find(f"{_W}rPr"))
        if sid and kinds:
            out[sid] = kinds
    return out


def _run_text(run: ET.Element) -> str:
    parts = []
    for child in run:
        if child.tag == f"{_W}t":
            parts.append(child.text or "")
        elif child.tag == f"{_W}tab":
            parts.append("\t")
        elif child.tag in (f"{_W}br", f"{_W}cr"):
            parts.append("\n")
    return "".join(parts)


def _runs(para: ET.Element) -> Iterable[ET.Element]:
    """문단의 run (링크·변경 추적·내용 컨트롤 안 포함). 글상자 안의 문단은 따로 센다."""
    for child in para:
        if child.tag == f"{_W}r":
            yield child
        elif child.tag not in (f"{_W}p", f"{_W}pPr", f"{_W}del", f"{_W}moveFrom"):
            yield from _runs(child)


def _page_size(doc: ET.Element) -> tuple[int, int]:
    pg = None
    for pg in doc.iter(f"{_W}pgSz"):
        break
    if pg is None:
        return _A4
    try:
        return int(pg.get(f"{_W}w") or _A4[0]), int(pg.get(f"{_W}h") or _A4[1])
    except ValueError:
        return _A4


def _outside(offset: int, extent: int, page: int) -> bool:
    """글상자가 통째로 쪽 밖 (EMU). 기준점(쪽·여백·문단)과 무관하게 쪽 크기 너머면 밖으로 본다."""
    return offset + extent <= 0 or offset >= page


def _anchor_outside(anchor: ET.Element, page_emu: tuple[int, int]) -> bool:
    ext = anchor.find(f"{_WP}extent")
    try:
        cx = int(ext.get("cx") or 0) if ext is not None else 0
        cy = int(ext.get("cy") or 0) if ext is not None else 0
    except ValueError:
        cx = cy = 0
    for axis, extent, page in (("positionH", cx, page_emu[0]), ("positionV", cy, page_emu[1])):
        pos = anchor.find(f"{_WP}{axis}")
        off = pos.find(f"{_WP}posOffset") if pos is not None else None
        if off is None or off.text is None:
            continue
        try:
            if _outside(int(off.text.strip()), extent, page):
                return True
        except ValueError:
            continue
    return False


def inspect_docx(data: bytes) -> FileInspection:
    parts = _safe_parts(data)
    styles = _parse(parts["word/styles.xml"]) if "word/styles.xml" in parts else None
    style_kinds = _hidden_styles(styles)
    hits = _Hits()
    all_text: list[str] = []
    for name in sorted(parts):
        if name == "word/styles.xml":
            continue
        root = _parse(parts[name])
        pw, ph = _page_size(root) if name == "word/document.xml" else _A4
        page_emu = (pw * _EMU_PER_TWIP, ph * _EMU_PER_TWIP)
        # 쪽 밖 글상자·틀 안의 run
        outside_runs: set[int] = set()
        for anchor in root.iter(f"{_WP}anchor"):
            if _anchor_outside(anchor, page_emu):
                outside_runs.update(id(r) for r in anchor.iter(f"{_W}r"))
        for para in root.iter(f"{_W}p"):
            ppr = para.find(f"{_W}pPr")
            frame = ppr.find(f"{_W}framePr") if ppr is not None else None
            if frame is not None:
                try:
                    x = int(frame.get(f"{_W}x") or 0)
                    y = int(frame.get(f"{_W}y") or 0)
                except ValueError:
                    x = y = 0
                if x < -pw or x >= pw or y < -ph or y >= ph:
                    outside_runs.update(id(r) for r in para.iter(f"{_W}r"))
            pstyle = ppr.find(f"{_W}pStyle") if ppr is not None else None
            para_kinds = (
                style_kinds.get(pstyle.get(f"{_W}val") or "", set())
                if pstyle is not None
                else set()
            )
            for run in _runs(para):
                text = _run_text(run)
                all_text.append(text)
                if not text.strip():
                    continue
                rpr = run.find(f"{_W}rPr")
                kinds = set(para_kinds) | _rpr_kinds(rpr)
                rstyle = rpr.find(f"{_W}rStyle") if rpr is not None else None
                if rstyle is not None:
                    kinds |= style_kinds.get(rstyle.get(f"{_W}val") or "", set())
                if "hidden_text_white" in kinds and _dark_fill(rpr, ppr):
                    kinds.discard("hidden_text_white")
                if id(run) in outside_runs:
                    kinds.add("hidden_text_outside_page")
                for kind in sorted(kinds):
                    hits.add(kind, text, part=name)
            all_text.append("\n")
    text = "".join(all_text)
    return FileInspection(
        "docx",
        hits.findings() + _text_findings(text, hits.hidden_text),
        None,
        None,
        len(text.strip()),
    )


# ---------------------------------------------------------------- PDF
#
# 글자 그리기마다 글자 색과 **그 아래에 먼저 칠해진 배경**을 비교한다 (c0023).
#
# - 칠한 도형(``re``·``m``/``l``/``c`` 경로 + ``f``·``F``·``f*``·``B``·``b`` 등)은 장치 좌표의 경계 상자와
#   채움 색으로 기억한다. 오목한 도형·곡선도 경계 상자로 본다 (근사). 선만 긋기(``S``)는 배경이 아니다.
# - 이미지(``Do`` 이미지·인라인 이미지)·음영(``sh``)·반투명 채움은 배경 색을 모른다: 그 위 글은 색 비교를
#   하지 않는다 (이미지 속 글·이미지 위 글 판단은 OCR이 필요해 범위 밖).
# - 글자 위치는 글 시작점에서 글자 높이 0.3쯤 위 한 점이다. 그 점을 덮는 가장 나중에 칠한 도형이 배경,
#   없으면 쪽 바탕(흰색).
# - 글자 색과 배경 색의 채널 차이가 모두 ``SAME_COLOR_DELTA`` 이하면 사람에게 안 보인다: 배경이 흰색이면
#   ``hidden_text_white``, 아니면 ``hidden_text_same_color``. 어두운 상자 위 흰 글씨는 경고하지 않는다.
# - 폼 XObject 안 그리기는 ``/Matrix``·자기 자원으로 따라간다 (pypdf가 안쪽 연산을 같은 visitor로 부른다).
# - 글 뒤에 칠한 불투명 도형이 글을 덮는 경우는 아직 보지 않는다 (plan §18 후속).
#
# 주석·양식 칸: 쪽의 ``/Annots``(``/Contents``, 양식 칸 값 ``/V``)와 쪽에 놓이지 않은 양식 칸 값을 본다.
# 숨김 표시(``/F`` Invisible·Hidden·NoView)·크기 0·쪽 밖이면 ``hidden_text_annotation``, 보이는
# 메모·링크 설명처럼 인쇄되지 않는 주석 글은 참고(``annotation_text``, info). 모두 전체 글 검사에 들어간다.

_IDENTITY = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
WHITE_RGB = (1.0, 1.0, 1.0)
# 글자 색과 배경 색이 이만큼(채널마다, 0~1) 가까우면 같은 색으로 본다 (= 흰색 판단 1 - WHITE_LEVEL)
SAME_COLOR_DELTA = 1 - WHITE_LEVEL
# 이보다 투명한 채움(``/ca``)은 칠하지 않은 것과 같다 / 그런 글은 안 보인다
ALPHA_INVISIBLE = 0.05
MAX_REGIONS = 2000
MAX_ANNOTS_PER_PAGE = 500
MAX_FIELDS = 2000
# 주석 플래그 (PDF 32000-1 12.5.3): Invisible 1, Hidden 2, Print 4, NoView 32
_ANNOT_HIDDEN_FLAGS = 1 | 2 | 32
_FILL_OPS = frozenset({b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"})
_PAINT_OPS = _FILL_OPS | {b"S", b"s", b"n"}
_DEVICE_SPACES = {
    "/DeviceGray": 1,
    "/DeviceRGB": 3,
    "/DeviceCMYK": 4,
    "/G": 1,
    "/RGB": 3,
    "/CMYK": 4,
}

RGB = tuple[float, float, float]
Box = tuple[float, float, float, float]


def _mul(a: Sequence[float], b: Sequence[float]) -> list[float]:
    """PDF 행렬 곱 a × b (6개 값)."""
    return [
        a[0] * b[0] + a[1] * b[2],
        a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2],
        a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4],
        a[4] * b[1] + a[5] * b[3] + b[5],
    ]


def _apply(m: Sequence[float], x: float, y: float) -> tuple[float, float]:
    return x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5]


def _nums(operands: Iterable[Any]) -> list[float] | None:
    out = []
    for o in operands:
        try:
            out.append(float(o))
        except (TypeError, ValueError):
            return None
    return out


def _to_rgb(values: list[float] | None, components: int | None = None) -> RGB | None:
    """회색·RGB·CMYK 값 → RGB (0~1). 값 개수로 색 공간을 본다 (``components``가 있으면 맞아야 한다)."""
    if not values or (components is not None and len(values) != components):
        return None
    v = [min(1.0, max(0.0, x)) for x in values]
    if len(v) == 1:
        return (v[0], v[0], v[0])
    if len(v) == 3:
        return (v[0], v[1], v[2])
    if len(v) == 4:
        c, m, y, k = v
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return None


def _same_color(a: RGB, b: RGB) -> bool:
    return max(abs(x - y) for x, y in zip(a, b, strict=True)) <= SAME_COLOR_DELTA


def _raw_has_text(operands: Sequence[Any]) -> bool:
    """보일 글이 있는가 (공백만 그리는 것은 뺀다). 문자 코드를 해석하지 않고 바이트로 본다."""
    for op in operands:
        items = op if isinstance(op, list) else [op]
        for item in items:
            raw: bytes | None = None
            if isinstance(item, bytes):
                raw = item
            elif isinstance(item, str) and not item.startswith("/"):
                raw = item.encode("utf-8", "replace")
            if hasattr(item, "original_bytes"):
                raw = item.original_bytes
            if raw and raw.strip(b" \x00"):
                return True
    return False


def _bbox(points: Sequence[tuple[float, float]]) -> Box | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _intersect(a: Box | None, b: Box | None) -> Box | None:
    if a is None:
        return b
    if b is None:
        return a
    box = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return box if box[0] <= box[2] and box[1] <= box[3] else (0.0, 0.0, -1.0, -1.0)


def _contains(box: Box, x: float, y: float) -> bool:
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _unit_square(m: Sequence[float]) -> Box | None:
    return _bbox([_apply(m, 0, 0), _apply(m, 1, 0), _apply(m, 0, 1), _apply(m, 1, 1)])


def _resolve(obj: Any) -> Any:
    try:
        return obj.get_object() if obj is not None else None
    except Exception:  # noqa: BLE001 - 깨진 참조는 없는 것으로
        return None


@dataclass(slots=True)
class _PdfState:
    fill: RGB | None = (0.0, 0.0, 0.0)  # None: 모르는 색(무늬·별색 등)
    stroke: RGB | None = (0.0, 0.0, 0.0)
    fill_components: int | None = None  # ``cs``로 고른 색 공간의 값 개수 (None: 값 개수로 본다)
    stroke_components: int | None = None
    fill_alpha: float = 1.0
    render_mode: int = 0
    font_size: float = 0.0
    clip: Box | None = None


@dataclass(slots=True)
class _Frame:
    """폼 XObject 하나: 바깥 상태·좌표 기준·자원."""

    state: _PdfState
    stack: list[_PdfState]
    base: list[float]
    resources: Any


def _space_components(name: Any, resources: Any) -> int | None:
    """``cs`` 이름 → 값 개수. 모르는 공간(무늬·별색·색인)은 0 (= 색을 모른다)."""
    key = str(name)
    if key in _DEVICE_SPACES:
        return _DEVICE_SPACES[key]
    spaces = _resolve(resources.get("/ColorSpace")) if resources is not None else None
    cs = _resolve(spaces.get(key)) if spaces is not None else None
    if isinstance(cs, str) and str(cs) in _DEVICE_SPACES:
        return _DEVICE_SPACES[str(cs)]
    if isinstance(cs, list) and cs:
        kind = str(cs[0])
        if kind in _DEVICE_SPACES:
            return _DEVICE_SPACES[kind]
        if kind == "/ICCBased" and len(cs) > 1:
            n = _resolve(cs[1])
            try:
                return int(n.get("/N")) if n is not None else None
            except (TypeError, ValueError):
                return None
        if kind == "/CalGray":
            return 1
        if kind == "/CalRGB":
            return 3
    return 0


def _page_hits(page: Any, page_no: int, hits: _Hits) -> str:
    box = page.cropbox if page.cropbox is not None else page.mediabox
    x0, y0, x1, y1 = (float(v) for v in (box.left, box.bottom, box.right, box.top))
    page_box: Box = (x0, y0, x1, y1)
    state = _PdfState()
    stack: list[_PdfState] = []
    frames: list[_Frame | None] = []  # ``Do``마다 하나 (폼이 아니면 None)
    base: list[float] = list(_IDENTITY)
    resources = _resolve(page.get("/Resources"))
    regions: list[tuple[Box, RGB | None]] = []  # 칠한 순서대로 (경계 상자, 색 — None이면 모름)
    path: list[tuple[float, float]] = []
    clip_pending = False
    pending: list[set[str]] = []
    texts: list[str] = []

    def paint(area: Box | None, color: RGB | None) -> None:
        area = _intersect(area, state.clip)
        if area is None or area[0] > area[2] or area[1] > area[3]:
            return
        if area[0] <= x0 and area[1] <= y0 and area[2] >= x1 and area[3] >= y1:
            regions.clear()  # 쪽 전체를 덮었다: 앞의 것은 모두 가려졌다
        regions.append((area, color))
        if len(regions) > MAX_REGIONS:
            del regions[: len(regions) - MAX_REGIONS]

    def background(x: float, y: float) -> RGB | None:
        for area, color in reversed(regions):
            if _contains(area, x, y):
                return color
        return WHITE_RGB

    def set_color(operands: list[Any], stroke: bool) -> None:
        comps = state.stroke_components if stroke else state.fill_components
        rgb = None if comps == 0 else _to_rgb(_nums(operands), comps)
        if stroke:
            state.stroke = rgb
        else:
            state.fill = rgb

    def before(op: bytes, operands: list[Any], cm: list[float], tm: list[float]) -> None:
        nonlocal state, stack, base, resources, path, clip_pending
        ctm = _mul(cm, base)
        if op == b"q":
            stack.append(replace(state))
        elif op == b"Q":
            state = stack.pop() if stack else _PdfState()
        elif op in (b"rg", b"g", b"k"):
            state.fill_components = None
            state.fill = _to_rgb(_nums(operands))
        elif op in (b"RG", b"G", b"K"):
            state.stroke_components = None
            state.stroke = _to_rgb(_nums(operands))
        elif op in (b"sc", b"scn"):
            set_color(operands, stroke=False)
        elif op in (b"SC", b"SCN"):
            set_color(operands, stroke=True)
        elif op == b"cs":
            state.fill_components = _space_components(operands[0], resources) if operands else 0
            state.fill = (0.0, 0.0, 0.0) if state.fill_components else None
        elif op == b"CS":
            state.stroke_components = _space_components(operands[0], resources) if operands else 0
            state.stroke = (0.0, 0.0, 0.0) if state.stroke_components else None
        elif op == b"gs" and operands:
            states = _resolve(resources.get("/ExtGState")) if resources is not None else None
            gs = _resolve(states.get(str(operands[0]))) if states is not None else None
            if gs is not None and "/ca" in gs:
                vals = _nums([gs["/ca"]])
                state.fill_alpha = vals[0] if vals else 1.0
        elif op == b"re" and len(operands) >= 4:
            vals = _nums(operands[:4])
            if vals:
                x, y, w, h = vals
                path.extend(
                    _apply(ctm, px, py)
                    for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h))
                )
        elif op in (b"m", b"l", b"c", b"v", b"y"):
            vals = _nums(operands) or []
            path.extend(_apply(ctm, vals[i], vals[i + 1]) for i in range(0, len(vals) - 1, 2))
        elif op in (b"W", b"W*"):
            clip_pending = True
        elif op in _PAINT_OPS:
            area = _bbox(path)
            if op in _FILL_OPS and area is not None:
                alpha = state.fill_alpha
                if alpha > ALPHA_INVISIBLE:
                    paint(area, state.fill if alpha >= 1 - ALPHA_INVISIBLE else None)
            if clip_pending:
                state.clip = _intersect(state.clip, area)
                clip_pending = False
            path = []
        elif op == b"sh":
            paint(page_box, None)
        elif op == b"INLINE IMAGE":
            paint(_unit_square(ctm), None)
        elif op == b"Do" and operands:
            xobjects = _resolve(resources.get("/XObject")) if resources is not None else None
            xobj = _resolve(xobjects.get(str(operands[0]))) if xobjects is not None else None
            subtype = str(xobj.get("/Subtype")) if xobj is not None else ""
            if subtype == "/Form":
                frames.append(_Frame(replace(state), stack, base, resources))
                matrix = _nums(xobj.get("/Matrix") or _IDENTITY) or list(_IDENTITY)
                base = _mul(matrix if len(matrix) == 6 else _IDENTITY, ctm)
                stack = []
                own = _resolve(xobj.get("/Resources"))
                resources = own if own is not None else resources
            else:
                if subtype == "/Image":
                    paint(_unit_square(ctm), None)
                frames.append(None)
        elif op == b"Tr":
            vals = _nums(operands)
            state.render_mode = int(vals[0]) if vals else 0
        elif op == b"Tf" and len(operands) >= 2:
            vals = _nums(operands[1:2])
            state.font_size = vals[0] if vals else state.font_size
        elif op in (b"Tj", b"TJ", b"'", b'"'):
            shown = operands[2:] if op == b'"' else operands
            if not _raw_has_text(shown):
                return
            m = _mul(tm, ctm)
            kinds: set[str] = set()
            mode = state.render_mode
            scale = math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))
            if mode in (3, 7) or state.fill_alpha <= ALPHA_INVISIBLE and mode in (0, 2, 4, 6):
                kinds.add("hidden_text_invisible")
            else:
                # 글자 높이의 0.3쯤 위 한 점에서 배경을 본다
                px, py = _apply(m, 0, 0.3 * state.font_size)
                bg = background(px, py)
                colors = [state.fill] if mode in (0, 2, 4, 6) else []
                if mode in (1, 2, 5, 6):
                    colors.append(state.stroke)
                known = [c for c in colors if c is not None]
                # 글자 색·배경 색을 모두 알 때만 비교한다 (이미지·무늬 위 글은 판단하지 않음)
                if (
                    bg is not None
                    and known
                    and len(known) == len(colors)
                    and all(_same_color(c, bg) for c in known)
                ):
                    kinds.add(
                        "hidden_text_white" if min(bg) >= WHITE_LEVEL else "hidden_text_same_color"
                    )
            if 0 < state.font_size * scale < TINY_POINTS or (state.font_size and scale == 0):
                kinds.add("hidden_text_tiny")
            x, y = m[4], m[5]
            if (
                x < x0 - PAGE_MARGIN_PT
                or x > x1 + PAGE_MARGIN_PT
                or y < y0 - PAGE_MARGIN_PT
                or y > y1 + PAGE_MARGIN_PT
            ):
                kinds.add("hidden_text_outside_page")
            pending.append(kinds)

    def after(op: bytes, operands: list[Any], cm: list[float], tm: list[float]) -> None:
        nonlocal state, stack, base, resources
        if op == b"Do" and operands and frames:
            frame = frames.pop()
            if frame is not None:  # 폼 끝: 바깥 상태로 (폼은 암묵적인 q … Q)
                state, stack, base, resources = (
                    frame.state,
                    frame.stack,
                    frame.base,
                    frame.resources,
                )

    def on_text(text: str, cm: Any, tm: Any, font: Any, size: Any) -> None:
        if not text or not text.strip():
            return
        texts.append(text)
        kinds = set().union(*pending) if pending else set()
        pending.clear()
        for kind in sorted(kinds):
            hits.add(kind, text, page=page_no)

    page.extract_text(
        visitor_operand_before=before, visitor_operand_after=after, visitor_text=on_text
    )
    for kinds in pending:  # 해석한 글 없이 끝난 그리기
        for kind in sorted(kinds):
            hits.add(kind, None, page=page_no)
    return "".join(texts)


def _pdf_text(value: Any) -> str | None:
    """주석·양식 칸의 글 값 (문자열·이름·문자열 목록). 그 밖은 None."""
    value = _resolve(value)
    if value is None:
        return None
    if isinstance(value, list):
        parts = [p for p in (_pdf_text(v) for v in value[:50]) if p]
        return "\n".join(parts) or None
    if isinstance(value, (str, bytes)):
        text = value.decode("latin-1", "replace") if isinstance(value, bytes) else str(value)
        if text.startswith("/"):  # 이름 (선택 칸의 값 /Off 등)
            text = text[1:]
            if text in ("Off", ""):
                return None
        return text.strip() or None
    return None


def _field_value(annot: Any) -> str | None:
    """위젯(양식 칸)의 값: 자기 ``/V`` 또는 부모 칸의 ``/V`` (5단계까지)."""
    node = annot
    for _ in range(5):
        if node is None:
            return None
        value = _pdf_text(node.get("/V"))
        if value:
            return value
        node = _resolve(node.get("/Parent"))
    return None


def _annotation_hits(page: Any, page_no: int, box: Box, hits: _Hits, seen: set[int]) -> list[str]:
    """쪽 주석·양식 칸의 글. 숨김·크기 0·쪽 밖이면 숨긴 글, 인쇄되지 않는 메모 글이면 참고."""
    texts: list[str] = []
    annots = _resolve(page.get("/Annots"))
    if not isinstance(annots, list):
        return texts
    for ref in annots[:MAX_ANNOTS_PER_PAGE]:
        idnum = getattr(ref, "idnum", None)
        if idnum is not None:
            seen.add(idnum)
        annot = _resolve(ref)
        if annot is None or not hasattr(annot, "get"):
            continue
        subtype = str(annot.get("/Subtype") or "")
        if subtype == "/Popup":  # 부모 주석의 글을 보여 주는 창: 부모에서 센다
            continue
        parent_id = getattr(annot.get("/Parent"), "idnum", None)
        if parent_id is not None:
            seen.add(parent_id)
        pieces = [_pdf_text(annot.get("/Contents"))]
        if subtype == "/Widget":
            pieces.append(_field_value(annot))
        text = "\n".join(p for p in pieces if p)
        if not text:
            continue
        texts.append(text)
        try:
            flags = int(annot.get("/F") or 0)
        except (TypeError, ValueError):
            flags = 0
        rect = _nums(_resolve(annot.get("/Rect")) or []) or []
        area = (
            (
                min(rect[0], rect[2]),
                min(rect[1], rect[3]),
                max(rect[0], rect[2]),
                max(rect[1], rect[3]),
            )
            if len(rect) == 4
            else None
        )
        zero = area is not None and (
            area[2] - area[0] < TINY_POINTS or area[3] - area[1] < TINY_POINTS
        )
        outside = area is not None and (
            area[2] < box[0] - PAGE_MARGIN_PT
            or area[0] > box[2] + PAGE_MARGIN_PT
            or area[3] < box[1] - PAGE_MARGIN_PT
            or area[1] > box[3] + PAGE_MARGIN_PT
        )
        kind_name = subtype.lstrip("/") or None
        if flags & _ANNOT_HIDDEN_FLAGS or zero or outside:
            hits.add("hidden_text_annotation", text, page=page_no, annotation=kind_name)
        elif subtype not in ("/FreeText", "/Widget"):
            # 메모(Text)·링크 설명 등: 화면에선 아이콘·마우스를 올릴 때만, 인쇄에는 없다. 기계는 읽는다
            hits.add("annotation_text", text, page=page_no, annotation=kind_name)
    return texts


def _orphan_field_hits(reader: Any, seen: set[int], hits: _Hits) -> list[str]:
    """쪽 어디에도 놓이지 않은 양식 칸의 값 (사람에게는 안 보이고 기계는 읽을 수 있다)."""
    texts: list[str] = []
    root = _resolve(reader.trailer.get("/Root"))
    form = _resolve(root.get("/AcroForm")) if root is not None else None
    fields = _resolve(form.get("/Fields")) if form is not None else None
    if not isinstance(fields, list):
        return texts
    budget = [MAX_FIELDS]

    def kids_of(node: Any, depth: int) -> list[Any]:
        kids = _resolve(node.get("/Kids"))
        return list(kids[:200]) if isinstance(kids, list) and depth < 10 else []

    def placed(ref: Any, node: Any, depth: int) -> bool:
        budget[0] -= 1
        if budget[0] < 0 or getattr(ref, "idnum", None) in seen:
            return True  # 다 못 보면 놓인 것으로 (경고를 지어내지 않는다)
        return any(
            placed(k, n, depth + 1)
            for k in kids_of(node, depth)
            if (n := _resolve(k)) is not None and hasattr(n, "get")
        )

    def walk(ref: Any, depth: int) -> None:
        budget[0] -= 1
        node = _resolve(ref)
        if budget[0] < 0 or node is None or not hasattr(node, "get"):
            return
        value = _pdf_text(node.get("/V"))
        if value and not placed(ref, node, depth):
            texts.append(value)
            hits.add("hidden_text_annotation", value, page=None, annotation="field")
            return
        for kid in kids_of(node, depth):
            walk(kid, depth + 1)

    for f in fields[:MAX_FIELDS]:
        walk(f, 0)
    return texts


def inspect_pdf(data: bytes) -> FileInspection:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise InspectError("pdf_encrypted")
        total = len(reader.pages)
    except InspectError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise InspectError("invalid_pdf") from exc
    hits = _Hits()
    texts: list[str] = []
    unreadable: list[int] = []
    seen_annots: set[int] = set()
    checked = min(total, MAX_PDF_PAGES)
    for i in range(checked):
        try:
            page = reader.pages[i]
            texts.append(_page_hits(page, i + 1, hits))
            box = page.cropbox if page.cropbox is not None else page.mediabox
            texts.extend(
                _annotation_hits(
                    page,
                    i + 1,
                    (float(box.left), float(box.bottom), float(box.right), float(box.top)),
                    hits,
                    seen_annots,
                )
            )
        except Exception:  # noqa: BLE001 - 한 쪽이 깨져도 나머지는 본다
            unreadable.append(i + 1)
    if total <= checked:  # 안 본 쪽의 칸을 "쪽에 없음"으로 잘못 세지 않게, 모든 쪽을 봤을 때만
        # 양식 사전이 깨졌으면 그 부분만 건너뛴다
        with suppress(Exception):
            texts.extend(_orphan_field_hits(reader, seen_annots, hits))
    text = "\n".join(texts)
    findings = hits.findings() + _text_findings(text, hits.hidden_text)
    if unreadable:
        findings.append(
            Finding(
                "pages_unreadable",
                "읽지 못한 쪽이 있어 그 쪽은 검사하지 못했습니다.",
                detail={"pages": unreadable[:20]},
                severity="info",
            )
        )
    if total > checked:
        findings.append(
            Finding(
                "pages_not_checked",
                f"앞 {checked}쪽만 검사했습니다.",
                detail={"pages_total": total},
                severity="info",
            )
        )
    return FileInspection("pdf", findings, total, checked, len(text.strip()))


__all__ = [
    "FORMATS",
    "FileInspection",
    "InspectError",
    "detect_format",
    "inspect_docx",
    "inspect_file",
    "inspect_pdf",
]

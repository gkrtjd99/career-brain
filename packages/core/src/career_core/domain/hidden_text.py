"""사용자가 올린 DOCX·PDF의 숨김 글 검사 (c0016). 경고만 하고 파일을 고치지 않는다.

plan §11 "요건 가시성 검사": 숨김 텍스트·심사 모델 대상 지시문은 만들지도 제안하지도 않고, 문서에 있으면
경고한다. 우리가 만든 파일은 ``document_export.inspect_export``가 보고, 여기서는 **다른 도구로 만든** 파일
(직접 꾸민 이력서·포트폴리오 PDF 등)을 제출 전에 본다.

찾는 것 (``code``):

- ``hidden_text_vanish``: DOCX 숨김 글(``w:vanish``·``w:specVanish``·``w:webHidden``, 그런 서식의 스타일 포함)
- ``hidden_text_invisible``: PDF 글자 그리기 방식 3·7(보이지 않게 그림)
- ``hidden_text_white``: 흰(또는 거의 흰) 글씨. DOCX는 문단·글자 배경색이 진하면 뺀다.
  PDF는 배경을 보지 않으므로 어두운 배경 위의 흰 글씨도 잡힌다 (경고 문구에 적는다)
- ``hidden_text_tiny``: 1pt 미만 글자
- ``hidden_text_outside_page``: 쪽 밖에 놓인 글 (PDF 글 위치, DOCX 글상자·틀 위치)
- 그 위에 전체 글의 보이지 않는 문자·심사 지시문 (``documents.ats_findings``, 반복 검사는 끔)

의존성은 이미 쓰는 ``pypdf``와 표준 라이브러리(``zipfile``, ``xml.etree``)뿐이다. 압축 폭탄을 막으려고
DOCX는 항목 수·풀린 크기를 먼저 보고, PDF는 앞쪽 ``MAX_PDF_PAGES``쪽만 본다.
"""

from __future__ import annotations

import io
import math
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any
from xml.etree import ElementTree as ET

from career_core.domain.documents import Finding, ats_findings

FORMATS = ("docx", "pdf")
MAX_DOCX_ENTRIES = 2000
MAX_DOCX_PART_BYTES = 20 * 1024 * 1024
MAX_DOCX_TOTAL_BYTES = 60 * 1024 * 1024
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
    "hidden_text_white": "흰 글씨가 있습니다. 흰 바탕 위라면 사람에게 안 보입니다 (어두운 배경 위의 글이면 괜찮습니다).",
    "hidden_text_tiny": "1pt보다 작은 글자가 있습니다. 사람이 읽을 수 없는 크기입니다.",
    "hidden_text_outside_page": "쪽 밖에 놓인 글이 있습니다. 인쇄·화면에는 안 보입니다.",
}


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
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" in z.namelist():
                    return "docx"
        except zipfile.BadZipFile:
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


def _safe_parts(data: bytes) -> dict[str, bytes]:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise InspectError("invalid_docx") from exc
    with z:
        infos = z.infolist()
        if len(infos) > MAX_DOCX_ENTRIES:
            raise InspectError("file_too_complex")
        wanted = [
            i
            for i in infos
            if i.filename in _CONTENT_PARTS
            or i.filename == "word/styles.xml"
            or (i.filename.startswith("word/header") and i.filename.endswith(".xml"))
            or (i.filename.startswith("word/footer") and i.filename.endswith(".xml"))
        ]
        if any(i.file_size > MAX_DOCX_PART_BYTES for i in wanted) or (
            sum(i.file_size for i in wanted) > MAX_DOCX_TOTAL_BYTES
        ):
            raise InspectError("file_too_large")
        if "word/document.xml" not in {i.filename for i in wanted}:
            raise InspectError("invalid_docx")
        return {i.filename: z.read(i) for i in wanted}


def _parse(xml: bytes) -> ET.Element:
    # DTD·외부 개체는 쓰지 않는다 (WordprocessingML에는 없다). 있으면 거부한다.
    if b"<!DOCTYPE" in xml[:4096] or b"<!ENTITY" in xml:
        raise InspectError("invalid_docx")
    try:
        return ET.fromstring(xml)
    except ET.ParseError as exc:
        raise InspectError("invalid_docx") from exc


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


def _nums(operands: Iterable[Any]) -> list[float] | None:
    out = []
    for o in operands:
        try:
            out.append(float(o))
        except (TypeError, ValueError):
            return None
    return out


def _white_color(values: list[float] | None) -> bool:
    if not values:
        return False
    if len(values) == 1:
        return values[0] >= WHITE_LEVEL
    if len(values) == 3:
        return min(values) >= WHITE_LEVEL
    if len(values) == 4:
        return max(values) <= 1 - WHITE_LEVEL
    return False


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


@dataclass(slots=True)
class _PdfState:
    fill_white: bool = False
    stroke_white: bool = False
    render_mode: int = 0
    font_size: float = 0.0


def _page_hits(page: Any, page_no: int, hits: _Hits) -> str:
    box = page.cropbox if page.cropbox is not None else page.mediabox
    x0, y0, x1, y1 = (float(v) for v in (box.left, box.bottom, box.right, box.top))
    state = _PdfState()
    stack: list[_PdfState] = []
    pending: list[set[str]] = []
    texts: list[str] = []

    def before(op: bytes, operands: list[Any], cm: list[float], tm: list[float]) -> None:
        nonlocal state
        if op == b"q":
            stack.append(replace(state))
        elif op == b"Q":
            state = stack.pop() if stack else _PdfState()
        elif op in (b"rg", b"g", b"k", b"sc", b"scn"):
            state.fill_white = _white_color(_nums(operands))
        elif op in (b"RG", b"G", b"K", b"SC", b"SCN"):
            state.stroke_white = _white_color(_nums(operands))
        elif op == b"cs":
            state.fill_white = False
        elif op == b"CS":
            state.stroke_white = False
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
            m = _mul(tm, cm)
            kinds: set[str] = set()
            mode = state.render_mode
            if mode in (3, 7):
                kinds.add("hidden_text_invisible")
            elif (mode in (0, 2, 4, 6) and state.fill_white) or (
                mode in (1, 5) and state.stroke_white
            ):
                kinds.add("hidden_text_white")
            scale = math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))
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

    def on_text(text: str, cm: Any, tm: Any, font: Any, size: Any) -> None:
        if not text or not text.strip():
            return
        texts.append(text)
        kinds = set().union(*pending) if pending else set()
        pending.clear()
        for kind in sorted(kinds):
            hits.add(kind, text, page=page_no)

    page.extract_text(visitor_operand_before=before, visitor_text=on_text)
    for kinds in pending:  # 해석한 글 없이 끝난 그리기
        for kind in sorted(kinds):
            hits.add(kind, None, page=page_no)
    return "".join(texts)


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
    checked = min(total, MAX_PDF_PAGES)
    for i in range(checked):
        try:
            texts.append(_page_hits(reader.pages[i], i + 1, hits))
        except Exception:  # noqa: BLE001 - 한 쪽이 깨져도 나머지는 본다
            unreadable.append(i + 1)
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

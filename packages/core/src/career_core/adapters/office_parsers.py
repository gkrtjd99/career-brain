"""PPTX·XLSX·HWPX 로컬 추출기 (2026-11-17).

DOCX 파서(``adapters.parsers.DocxTextParser``)와 같은 길: 표준 라이브러리 ``zipfile`` + ``xml.etree``,
``domain.docx_zip``의 항목 수·풀린 크기 상한과 DTD·개체 선언 거부. 외부 전송·외부 의존성이 없다.
읽는 순서와 블록 나누기는 microsoft/markitdown(MIT)의 방식을 참고만 했다 (코드는 가져오지 않았다).

- PPTX: 슬라이드 하나 = 한 쪽 (``page`` = 슬라이드 번호, 숨긴 슬라이드도 번호를 센다). 제목 먼저, 나머지
  글상자·표는 위→아래, 왼쪽→오른쪽. 발표자 메모는 그 쪽의 마지막 블록으로 ``발표자 메모:``를 붙여 둔다.
  그림·차트·SmartArt 안 글은 읽지 않는다 (DOCX와 같다). 그래서 모든 쪽을 "글을 읽은 쪽"으로 본다 —
  PPTX는 외부 OCR로 보내지 않는다.
- XLSX: 시트마다 시트 이름 블록 하나 + 행 하나 = 블록 하나 (칸은 `` | ``). 빈 행은 건너뛴다. 공유 문자열·
  날짜 서식(일련번호 → ``YYYY-MM-DD``)을 푼다. 시트당 ``MAX_XLSX_ROWS``행·행당 ``MAX_XLSX_COLS``칸까지만
  읽고, 잘랐으면 시트 이름 블록에 적는다. 쪽은 없다.
- HWPX(OWPML): ``Contents/section*.xml`` 순서대로 문단·표(행 하나 = 블록 하나)·글상자, 각주·미주는 뒤에.
  머리말·꼬리말·숨은 설명(``hiddenComment``)은 읽지 않는다. 암호를 건 문서는 ``encrypted``. 쪽은 없다.
- HWP(한글 5.x OLE)는 ``adapters.hwp`` (2026-11-19). 한글 3.0 이하는 ``hwp_unsupported``.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

from career_core.domain import docx_zip
from career_core.ports import LocalParseResult, ParsedBlock, ParseError

MAX_BLOCKS = 5000
MAX_SLIDES = 300  # PDF 쪽 상한과 같다
MAX_SHEETS = 100
MAX_XLSX_ROWS = 2000
MAX_XLSX_COLS = 64
MAX_HWPX_SECTIONS = 200
NOTES_LABEL = "발표자 메모: "

_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
_REL_TYPE_STRICT = "http://purl.oclc.org/ooxml/officeDocument/relationships/"


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _rid(el: ET.Element) -> str | None:
    """``r:id`` (전이·strict 이름공간 모두)."""
    for key, value in el.attrib.items():
        if key.endswith("}id") and "relationships" in key:
            return value
    return None


def _clean(text: str) -> str:
    clean = re.sub(r"[ \t ]+", " ", text.replace("\r", "")).strip()
    return re.sub(r" ?\n ?", "\n", clean)


class _Blocks:
    def __init__(self) -> None:
        self.items: list[ParsedBlock] = []

    def emit(self, text: str, page: int | None = None) -> None:
        clean = _clean(text)
        if clean:
            if len(self.items) >= MAX_BLOCKS:
                raise ParseError("too_many_blocks")
            self.items.append(ParsedBlock(page=page, text=clean))


def _rels(z: docx_zip.PartReader, part: str) -> dict[str, tuple[str, str]]:
    """부분의 관계 파일 → ``{rId: (종류 끝 이름, zip 안 경로)}``. 바깥 주소(TargetMode=External)는 뺀다."""
    folder, name = posixpath.split(part)
    root = z.xml(posixpath.join(folder, "_rels", name + ".rels"))
    out: dict[str, tuple[str, str]] = {}
    if root is None:
        return out
    for rel in root:
        if _local(rel.tag) != "Relationship" or rel.get("TargetMode") == "External":
            continue
        rid, target, kind = rel.get("Id"), rel.get("Target") or "", rel.get("Type") or ""
        if not rid or not target:
            continue
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            path = posixpath.normpath(posixpath.join(folder, target))
        if path.startswith("../"):
            continue  # zip 밖을 가리키는 관계
        out[rid] = (kind.removeprefix(_REL_TYPE).removeprefix(_REL_TYPE_STRICT), path)
    return out


def _zip_errors(exc: docx_zip.DocxError) -> ParseError:
    return ParseError("malformed" if exc.code == "invalid_docx" else exc.code)


# ---------------------------------------------------------------- PPTX

_PPTX_MAIN = "ppt/presentation.xml"
_TITLE_PH = frozenset({"title", "ctrTitle"})
# 쪽마다 되풀이되는 자리 (DOCX 머리글·바닥글처럼 뺀다)
_REPEATED_PH = frozenset({"dt", "ftr", "sldNum", "hdr", "sldImg"})


def _drawing_paras(body: ET.Element) -> str:
    """``a:txBody`` → 문단마다 한 줄."""
    lines: list[str] = []
    for p in body:
        if _local(p.tag) != "p":
            continue
        parts: list[str] = []
        for child in p:
            name = _local(child.tag)
            if name in ("r", "fld"):
                parts.extend(t.text or "" for t in child if _local(t.tag) == "t")
            elif name == "br":
                parts.append("\n")
        line = "".join(parts)
        if line.strip():
            lines.append(line)
    return "\n".join(lines)


def _offset(el: ET.Element) -> tuple[float, float]:
    """도형 위치 (위, 왼쪽). 레이아웃에서 물려받아 파일에 없으면 맨 앞으로 (markitdown과 같은 규칙)."""
    for child in el:
        if _local(child.tag) in ("spPr", "grpSpPr", "xfrm"):
            xfrm = (
                child
                if _local(child.tag) == "xfrm"
                else next((x for x in child if _local(x.tag) == "xfrm"), None)
            )
            off = None if xfrm is None else next((o for o in xfrm if _local(o.tag) == "off"), None)
            if off is not None:
                try:
                    return float(off.get("y", "")), float(off.get("x", ""))
                except ValueError:
                    break
    return float("-inf"), float("-inf")


def _placeholder(sp: ET.Element) -> str | None:
    for nv in sp:
        if _local(nv.tag) in ("nvSpPr", "nvGraphicFramePr"):
            for x in nv:
                if _local(x.tag) == "nvPr":
                    for ph in x:
                        if _local(ph.tag) == "ph":
                            return ph.get("type", "obj")  # 표준의 기본값
    return None


def _shapes(tree: ET.Element) -> Iterator[ET.Element]:
    """도형 목록 → 위치 순서. 묶음(grpSp)은 묶음 자리에서 안쪽을 다시 위치 순서로."""
    items: list[ET.Element] = []
    for child in tree:
        name = _local(child.tag)
        if name == "AlternateContent":
            choice = next((c for c in child if _local(c.tag) == "Choice"), None)
            items.extend(
                c
                for c in (choice if choice is not None else ())
                if _local(c.tag) in ("sp", "graphicFrame", "grpSp")
            )
        elif name in ("sp", "graphicFrame", "grpSp"):
            items.append(child)
    for shape in sorted(items, key=_offset):
        if _local(shape.tag) == "grpSp":
            yield from _shapes(shape)
        else:
            yield shape


def _shape_texts(shape: ET.Element) -> list[str]:
    """글상자 하나 → 블록 하나, 표 → 행마다 블록 하나."""
    if _local(shape.tag) == "sp":
        body = next((c for c in shape if _local(c.tag) == "txBody"), None)
        return [] if body is None else [_drawing_paras(body)]
    rows: list[str] = []
    for tbl in shape.iter():
        if _local(tbl.tag) != "tbl":
            continue
        for tr in tbl:
            if _local(tr.tag) != "tr":
                continue
            cells = []
            for tc in tr:
                if _local(tc.tag) != "tc" or tc.get("hMerge") or tc.get("vMerge"):
                    continue  # 합친 칸의 나머지 자리
                body = next((c for c in tc if _local(c.tag) == "txBody"), None)
                text = "" if body is None else _drawing_paras(body).replace("\n", " ")
                if text.strip():
                    cells.append(text)
            rows.append(" | ".join(cells))
        break  # 한 graphicFrame에 표 하나
    return rows


class PptxTextParser:
    def parse(self, data: bytes) -> LocalParseResult:
        out = _Blocks()
        try:
            with docx_zip.PartReader(data, _PPTX_MAIN) as z:
                pres = z.xml(_PPTX_MAIN)
                rels = _rels(z, _PPTX_MAIN)
                slide_ids = [_rid(s) for el in pres if _local(el.tag) == "sldIdLst" for s in el]
                slides = [rels[r][1] for r in slide_ids if r in rels and rels[r][0] == "slide"]
                if len(slides) > MAX_SLIDES:
                    raise ParseError("too_many_pages")
                for page, part in enumerate(slides, start=1):
                    self._slide(z, part, page, out)
        except docx_zip.DocxError as exc:
            raise _zip_errors(exc) from exc
        return LocalParseResult(
            blocks=tuple(out.items),
            pages_total=len(slides),
            pages_with_text=frozenset(range(1, len(slides) + 1)),
        )

    @staticmethod
    def _slide(z: docx_zip.PartReader, part: str, page: int, out: _Blocks) -> None:
        root = z.xml(part)
        if root is None:
            return
        tree = next(
            (t for c in root if _local(c.tag) == "cSld" for t in c if _local(t.tag) == "spTree"),
            None,
        )
        if tree is not None:
            shapes = list(_shapes(tree))
            titles = [s for s in shapes if _placeholder(s) in _TITLE_PH]
            rest = [s for s in shapes if s not in titles and _placeholder(s) not in _REPEATED_PH]
            for shape in (*titles, *rest):
                for text in _shape_texts(shape):
                    out.emit(text, page)
        notes = next((p for kind, p in _rels(z, part).values() if kind == "notesSlide"), None)
        root = z.xml(notes) if notes else None
        if root is None:
            return
        texts = [
            t
            for s in root.iter()
            if _local(s.tag) == "sp" and _placeholder(s) == "body"
            for t in _shape_texts(s)
        ]
        note = "\n".join(t for t in texts if t.strip())
        if note.strip():
            out.emit(NOTES_LABEL + note, page)


# ---------------------------------------------------------------- XLSX

_XLSX_MAIN = "xl/workbook.xml"
# 한국어·일본어·중국어 로캘 기본 서식 27–36·50–58도 날짜다
_DATE_FORMAT_IDS = frozenset({*range(14, 23), *range(27, 37), *range(45, 48), *range(50, 59)})
_CELL_REF = re.compile(r"([A-Z]{1,3})(\d+)$")


def _col_index(ref: str | None) -> int | None:
    m = _CELL_REF.match(ref or "")
    if not m:
        return None
    n = 0
    for ch in m.group(1):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _is_date_format(code: str) -> bool:
    code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.|_.|\*.', "", code).lower()
    return bool(re.search(r"[ymdh]", code)) and "general" not in code


def _xlsx_text(el: ET.Element) -> str:
    """``si``·``is``: 글(t) 또는 서식 run(r/t). 읽는 법 표시(rPh)는 뺀다."""
    parts: list[str] = []
    for child in el:
        name = _local(child.tag)
        if name == "t":
            parts.append(child.text or "")
        elif name == "r":
            parts.extend(t.text or "" for t in child if _local(t.tag) == "t")
    return "".join(parts)


def _number(raw: str) -> str:
    try:
        value = float(raw)
    except ValueError:
        return raw
    return format(value, ".15g")


def _serial_date(raw: str, date1904: bool) -> str:
    try:
        value = float(raw)
    except ValueError:
        return raw
    if not 0 <= value < 2958466:  # 9999-12-31까지
        return _number(raw)
    base = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
    moment = base + timedelta(days=value)
    moment = moment.replace(microsecond=0) + timedelta(seconds=round(moment.microsecond / 1e6))
    if value < 1:
        return moment.strftime("%H:%M")
    if value == int(value):
        return moment.strftime("%Y-%m-%d")
    return moment.strftime("%Y-%m-%d %H:%M")


class XlsxTextParser:
    def parse(self, data: bytes) -> LocalParseResult:
        out = _Blocks()
        try:
            with docx_zip.PartReader(data, _XLSX_MAIN) as z:
                book = z.xml(_XLSX_MAIN)
                rels = _rels(z, _XLSX_MAIN)
                strings = self._shared_strings(z, rels)
                dates = self._date_styles(z, rels)
                date1904 = any(
                    _local(el.tag) == "workbookPr" and el.get("date1904") in ("1", "true")
                    for el in book
                )
                sheets = [
                    (s.get("name") or "", rels.get(_rid(s) or ""))
                    for el in book
                    if _local(el.tag) == "sheets"
                    for s in el
                    if _local(s.tag) == "sheet"
                ]
                for name, rel in sheets[:MAX_SHEETS]:
                    if rel is None or rel[0] != "worksheet":
                        continue  # 차트 시트 등
                    if len(out.items) >= MAX_BLOCKS - 1:
                        break
                    self._sheet(z, rel[1], name, strings, dates, date1904, out)
        except docx_zip.DocxError as exc:
            raise _zip_errors(exc) from exc
        return LocalParseResult(blocks=tuple(out.items), pages_total=None)

    @staticmethod
    def _shared_strings(z: docx_zip.PartReader, rels) -> list[str]:
        part = next((p for kind, p in rels.values() if kind == "sharedStrings"), None)
        root = z.xml(part or "xl/sharedStrings.xml")
        if root is None:
            return []
        return [_xlsx_text(si) for si in root if _local(si.tag) == "si"]

    @staticmethod
    def _date_styles(z: docx_zip.PartReader, rels) -> frozenset[int]:
        """날짜 서식을 쓰는 칸 서식(``cellXfs``) 번호."""
        part = next((p for kind, p in rels.values() if kind == "styles"), None)
        root = z.xml(part or "xl/styles.xml")
        if root is None:
            return frozenset()
        custom: set[int] = set()
        xfs: list[ET.Element] = []
        for el in root:
            if _local(el.tag) == "numFmts":
                for f in el:
                    if _is_date_format(f.get("formatCode") or ""):
                        try:
                            custom.add(int(f.get("numFmtId", "")))
                        except ValueError:
                            pass
            elif _local(el.tag) == "cellXfs":
                xfs = [x for x in el if _local(x.tag) == "xf"]
        out = set()
        for i, xf in enumerate(xfs):
            try:
                fmt = int(xf.get("numFmtId", "0"))
            except ValueError:
                continue
            if fmt in _DATE_FORMAT_IDS or fmt in custom:
                out.add(i)
        return frozenset(out)

    @staticmethod
    def _cell(c: ET.Element, strings: list[str], dates: frozenset[int], date1904: bool) -> str:
        kind = c.get("t", "n")
        v = next((x.text or "" for x in c if _local(x.tag) == "v"), None)
        if kind == "inlineStr":
            box = next((x for x in c if _local(x.tag) == "is"), None)
            return "" if box is None else _xlsx_text(box)
        if v is None:
            return ""
        if kind == "s":
            try:
                return strings[int(v)]
            except (ValueError, IndexError):
                return ""
        if kind == "b":
            return "TRUE" if v.strip() in ("1", "true") else "FALSE"
        if kind in ("str", "e", "d"):
            return v
        try:
            style = int(c.get("s", "0"))
        except ValueError:
            style = 0
        return _serial_date(v, date1904) if style in dates else _number(v)

    def _sheet(self, z, part, name, strings, dates, date1904, out: _Blocks) -> None:
        root = z.xml(part)
        if root is None:
            return
        data = next((el for el in root if _local(el.tag) == "sheetData"), None)
        lines: list[str] = []
        cut = False
        budget = min(MAX_XLSX_ROWS, MAX_BLOCKS - len(out.items) - 1)
        for row in data if data is not None else ():
            if _local(row.tag) != "row":
                continue
            cells: dict[int, str] = {}
            pos = 0
            for c in row:
                if _local(c.tag) != "c":
                    continue
                col = _col_index(c.get("r"))
                pos = pos if col is None else col
                if pos < MAX_XLSX_COLS:
                    text = _clean(self._cell(c, strings, dates, date1904)).replace("\n", " ")
                    if text:
                        cells[pos] = text
                pos += 1
            if not cells:
                continue  # 빈 행
            if len(lines) >= budget:
                cut = True
                break
            lines.append(" | ".join(cells.get(i, "") for i in range(max(cells) + 1)))
        if not lines:
            return
        out.emit(f"{name} (앞 {len(lines)}행만 읽음)" if cut else name)
        for line in lines:
            out.emit(line)


# ---------------------------------------------------------------- HWPX

_HWPX_MAIN = "Contents/section0.xml"
_HWPX_SECTION = re.compile(r"Contents/section(\d+)\.xml$")
_HWPX_MANIFEST = "META-INF/manifest.xml"
# 문단 글에 넣지 않는 것: 머리말·꼬리말(쪽마다 되풀이), 숨은 설명·메모 필드, 그림·수식·OLE, 구역·단 설정
_HWPX_SKIP = frozenset(
    {
        "header",
        "footer",
        "hiddenComment",
        "pic",
        "equation",
        "ole",
        "chart",
        "secPr",
        "colPr",
        "linesegarray",
        "parameters",
        "fieldBegin",
    }
)
_HWPX_NOTES = frozenset({"footNote", "endNote"})
_HWPX_CHAR = {"tab": "\t", "lineBreak": "\n", "nbSpace": " ", "fwSpace": " ", "hyphen": "-"}


def _hwpx_t(t: ET.Element) -> str:
    parts = [t.text or ""]
    for child in t:
        parts.append(_HWPX_CHAR.get(_local(child.tag), ""))
        parts.append(child.tail or "")
    return "".join(parts)


def _hwpx_branch(switch: ET.Element) -> ET.Element | None:
    """``hp:switch``: 호환 표현(default)이 있으면 그것, 없으면 첫 case. 같은 글을 두 번 읽지 않게."""
    default = next((c for c in switch if _local(c.tag) == "default"), None)
    return default if default is not None else next(iter(switch), None)


def _hwpx_para_text(p: ET.Element) -> str:
    """문단 바로 아래 run의 글만 (표·글상자 안 문단은 따로)."""
    out: list[str] = []

    def walk(el: ET.Element) -> None:
        for child in el:
            name = _local(child.tag)
            if name == "run":
                walk(child)
            elif name == "t":
                out.append(_hwpx_t(child))
            elif name == "switch":
                branch = _hwpx_branch(child)
                if branch is not None:
                    walk(branch)

    walk(p)
    return "".join(out)


def _hwpx_embedded(el: ET.Element) -> Iterator[tuple[str, ET.Element]]:
    """문단 안에 박힌 표·글상자·각주를 문서 순서로. 그 안으로는 들어가지 않는다."""
    for child in el:
        name = _local(child.tag)
        if name in _HWPX_SKIP or name in ("t", "p"):
            continue
        if name == "tbl":
            yield "table", child
        elif name == "drawText":
            yield "box", child
        elif name in _HWPX_NOTES:
            yield "note", child
        elif name == "switch":
            branch = _hwpx_branch(child)
            if branch is not None:
                yield from _hwpx_embedded(branch)
        else:
            yield from _hwpx_embedded(child)  # run·ctrl·도형(rect·container …) 껍데기


def _hwpx_paras(el: ET.Element) -> Iterator[ET.Element]:
    """글 묶음(구역·subList·칸·글상자)의 문단. 문단 안으로는 들어가지 않는다."""
    for child in el:
        name = _local(child.tag)
        if name == "p":
            yield child
        elif name not in _HWPX_SKIP and name not in ("tbl", "drawText"):
            yield from _hwpx_paras(child)


def _hwpx_cell_text(tc: ET.Element, notes: list[ET.Element]) -> str:
    """칸 하나 → 한 줄. 칸 안의 표·글상자 글도 이어 붙인다."""
    texts: list[str] = []

    def walk(container: ET.Element) -> None:
        for p in _hwpx_paras(container):
            texts.append(_hwpx_para_text(p))
            for kind, el in _hwpx_embedded(p):
                if kind == "note":
                    notes.append(el)
                elif kind == "table":
                    for tr in el:
                        if _local(tr.tag) == "tr":
                            for cell in tr:
                                if _local(cell.tag) == "tc":
                                    walk(cell)
                else:
                    walk(el)

    walk(tc)
    return " ".join(_clean(t).replace("\n", " ") for t in texts if t.strip())


def _hwpx_blocks(container: ET.Element, out: _Blocks, notes: list[ET.Element]) -> None:
    for p in _hwpx_paras(container):
        out.emit(_hwpx_para_text(p))
        for kind, el in _hwpx_embedded(p):
            if kind == "note":
                notes.append(el)
            elif kind == "box":
                _hwpx_blocks(el, out, notes)
            else:
                for tr in el:
                    if _local(tr.tag) != "tr":
                        continue
                    cells = [_hwpx_cell_text(tc, notes) for tc in tr if _local(tc.tag) == "tc"]
                    out.emit(" | ".join(c for c in cells if c))


class HwpxTextParser:
    def parse(self, data: bytes) -> LocalParseResult:
        out = _Blocks()
        try:
            with docx_zip.PartReader(data, _HWPX_MAIN) as z:
                manifest = z.read(_HWPX_MANIFEST)
                if manifest and b"encryption-data" in manifest:
                    raise ParseError("encrypted")
                sections = sorted(
                    (int(m.group(1)), name)
                    for name in z.names()
                    if (m := _HWPX_SECTION.match(name))
                )
                if len(sections) > MAX_HWPX_SECTIONS:
                    raise ParseError("file_too_complex")
                notes: list[ET.Element] = []
                for _, name in sections:
                    root = z.xml(name)
                    if root is not None:
                        _hwpx_blocks(root, out, notes)
                for note in notes:  # 각주·미주는 본문 뒤에 (DOCX와 같다)
                    _hwpx_blocks(note, out, [])
        except docx_zip.DocxError as exc:
            raise _zip_errors(exc) from exc
        return LocalParseResult(blocks=tuple(out.items), pages_total=None)


__all__ = [
    "MAX_SLIDES",
    "MAX_XLSX_COLS",
    "MAX_XLSX_ROWS",
    "NOTES_LABEL",
    "HwpxTextParser",
    "PptxTextParser",
    "XlsxTextParser",
]

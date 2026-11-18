"""로컬 추출기. 외부 전송 없이 텍스트·HTML·텍스트 PDF·DOCX를 블록으로 나눈다.
PPTX·XLSX·HWPX는 ``adapters.office_parsers`` (2026-11-17), 옛 한글(.hwp)은 ``adapters.hwp`` (2026-11-19).

스캔 PDF·이미지처럼 텍스트가 없는 쪽은 추출하지 않고 ``pages_with_text``에서 빠진다
(부분 파싱으로 표시되고, 허용된 경우에만 외부 OCR로 넘어간다).

DOCX(c0023)는 표준 라이브러리(``zipfile`` + ``xml.etree``)로 읽는다. 쪽 번호는 만들지 않는다
(DOCX에는 조판된 쪽이 없다 — plan §9 "없는 좌표를 만들어 넣지 않는다"). 블록 위치는 순서(``ordinal``)다.
"""

from __future__ import annotations

import io
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from html.parser import HTMLParser
from itertools import pairwise
from typing import ClassVar
from xml.etree import ElementTree as ET

from career_core.adapters.hwp import HwpTextParser
from career_core.adapters.office_parsers import (
    HwpxTextParser,
    PptxTextParser,
    XlsxTextParser,
)
from career_core.domain import docx_zip
from career_core.domain.text_clean import clean_control
from career_core.ports import LocalParser, LocalParseResult, ParsedBlock, ParseError
from career_core.uploads import DOCX_MIME, HWP_MIME, HWPX_MIME, PPTX_MIME, XLSX_MIME

MAX_PDF_PAGES = 300
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def _paragraphs(text: str) -> tuple[ParsedBlock, ...]:
    parts = (re.sub(r"[ \t]+", " ", p).strip() for p in _PARAGRAPH_SPLIT.split(text))
    return tuple(ParsedBlock(page=None, text=p) for p in parts if p)


class PlainTextParser:
    def parse(self, data: bytes) -> LocalParseResult:
        text = data.decode("utf-8", errors="replace").lstrip("﻿").replace("\r\n", "\n")
        return LocalParseResult(blocks=_paragraphs(text), pages_total=None)


class _TextCollector(HTMLParser):
    _SKIP: ClassVar[frozenset[str]] = frozenset(
        {"script", "style", "noscript", "template", "svg", "head"}
    )
    _BREAK: ClassVar[frozenset[str]] = frozenset(
        {
            "p",
            "div",
            "br",
            "li",
            "ul",
            "ol",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "tr",
            "section",
            "article",
            "header",
            "footer",
            "table",
            "dd",
            "dt",
            "pre",
            "blockquote",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAK:
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BREAK:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


class HtmlTextParser:
    """HTML을 실행·렌더링하지 않고 텍스트만 꺼낸다."""

    def parse(self, data: bytes) -> LocalParseResult:
        collector = _TextCollector()
        collector.feed(data.decode("utf-8", errors="replace"))
        collector.close()
        return LocalParseResult(blocks=_paragraphs("".join(collector.parts)), pages_total=None)


class PdfTextParser:
    """텍스트 PDF → 문단 블록 (2026-11-18).

    pypdf는 쪽 하나를 줄 단위 글로 준다. Chrome으로 인쇄한 한국어 PDF는 띄어쓰기를 ``\\x01``(글꼴에 따라
    ``\\t``)로 읽고, 화면 폭에서 꺾인 줄이 낱말 중간에서 끊긴다("40분으\\n로"). 그래서:

    1. 제어 문자·탭을 공백으로 (``domain.text_clean``).
    2. 쪽 글이 깨져 보이면(띄어쓰기가 거의 없는 긴 줄, ``�``) 선택 설치 ``pdf-render``(pypdfium2)가 있을 때
       PDFium 글로 바꿔 본다 — 더 나을 때만 (``_looks_broken``).
    3. 꺾인 줄을 다시 잇는다 (``rejoin_lines``): 쪽에서 가장 긴 줄 폭의 85% 이상인 줄은 다음 줄로 이어진
       글로 본다 (문장이 끝나지 않았고 다음 줄이 글머리표·번호로 시작하지 않을 때). 한글↔한글 경계는 붙여
       잇는다 (Chrome은 한글 낱말 중간에서 꺾는다), 그 밖에는 공백 하나로.
    4. 이어 붙인 줄 하나 = 블록 하나 (다른 파서의 문단 블록과 같은 크기). 쪽 번호는 그대로.

    붙여 이을 때 띄어쓰기를 잘못 맞힐 수 있다. 추출의 인용 확인(``find_quote``)은 공백 차이를 보지 않는다.
    """

    def parse(self, data: bytes) -> LocalParseResult:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise ParseError("encrypted")
            total = len(reader.pages)
            if total > MAX_PDF_PAGES:
                raise ParseError("too_many_pages")
            texts = [_pdf_clean(page.extract_text() or "") for page in reader.pages]
        except PdfReadError as exc:
            raise ParseError("malformed") from exc
        broken = [i for i, t in enumerate(texts) if _looks_broken(t)]
        if broken:
            texts = _with_pdfium(data, texts, broken)
        blocks: list[ParsedBlock] = []
        with_text: set[int] = set()
        for index, text in enumerate(texts, start=1):
            paragraphs = rejoin_lines(text)
            if paragraphs:
                with_text.add(index)
                blocks.extend(ParsedBlock(page=index, text=p) for p in paragraphs)
        return LocalParseResult(
            blocks=tuple(blocks), pages_total=total, pages_with_text=frozenset(with_text)
        )


_BULLET = re.compile(r"^(?:[•·▪◦○●■□▶►◆◇※✓✔\-–—*]|\(?\d{1,2}[.)]\s|[가-하][.)]\s)")
_SENTENCE_END = re.compile(r"(?:[.!?。:;]|[다요음함됨임]\.?)$")
_HANGUL = re.compile(r"[가-힣]")


def _pdf_clean(text: str) -> str:
    """제어 문자·탭 → 공백, 줄마다 겹친 공백을 하나로. 빈 줄(문단 경계)은 남긴다."""
    lines = clean_control(text.replace("\r\n", "\n")).replace("\t", " ").split("\n")
    return "\n".join(" ".join(line.split()) for line in lines).strip()


def _width(line: str) -> float:
    """대략의 글 폭 (한글·한자·전각 = 1, 그 밖 글자 = 0.55, 공백 = 0.3)."""
    w = 0.0
    for ch in line:
        if ch == " ":
            w += 0.3
        elif ord(ch) >= 0x1100 and unicodedata.east_asian_width(ch) in ("W", "F"):
            w += 1.0
        else:
            w += 0.55
    return w


def _looks_broken(text: str) -> bool:
    """띄어쓰기가 거의 없는 긴 줄(낱말이 붙어 읽혔다)이나 대체 문자(�)가 많으면 깨져 보인다."""
    if not text:
        return False
    if text.count("�") >= 3:
        return True
    long_lines = [ln for ln in text.split("\n") if len(ln) >= 40]
    if not long_lines:
        return False
    spaces = sum(ln.count(" ") for ln in long_lines)
    return spaces / sum(len(ln) for ln in long_lines) < 0.02


def _quality(text: str) -> float:
    if not text:
        return 0.0
    return text.count(" ") / max(1, len(text)) - text.count("�") * 0.05


def _with_pdfium(data: bytes, texts: list[str], pages: list[int]) -> list[str]:
    """깨져 보이는 쪽만 PDFium 글로 바꿔 본다 (설치가 없거나 실패하면 그대로)."""
    from career_core.adapters.pdf_render import PdfRenderError, PdfRenderUnavailable, page_texts

    try:
        other = page_texts(data, pages)
    except (PdfRenderUnavailable, PdfRenderError):
        return texts
    out = list(texts)
    for index, text in other.items():
        cleaned = _pdf_clean(text)
        if cleaned and _quality(cleaned) > _quality(out[index]):
            out[index] = cleaned
    return out


def _joiner(prev: str, nxt: str) -> str:
    if _HANGUL.match(prev[-1:]) and _HANGUL.match(nxt[:1]):
        return ""
    return " "


def rejoin_lines(text: str) -> list[str]:
    """쪽 글 → 문단 목록. 화면 폭에서 꺾인 줄을 다시 잇고, 빈 줄·짧은 줄·문장 끝·글머리표에서 끊는다."""
    out: list[str] = []
    for chunk in _PARAGRAPH_SPLIT.split(text):
        lines = [ln for ln in chunk.split("\n") if ln.strip()]
        if not lines:
            continue
        full = max(_width(ln) for ln in lines) * 0.85 if len(lines) >= 3 else float("inf")
        current = lines[0]
        for prev, nxt in pairwise(lines):
            # 다음 줄이 짧고 문장으로 끝나지 않으면 제목("자격", "기술")이다 — 꺾인 끝("로 줄였다.")과 구별
            heading = _width(nxt) < full * 0.3 and not _SENTENCE_END.search(nxt)
            wrapped = (
                _width(prev) >= full
                and not _SENTENCE_END.search(prev)
                and not _BULLET.match(nxt)
                and not heading
            )
            if wrapped:
                current += _joiner(current, nxt) + nxt
            else:
                out.append(current)
                current = nxt
        out.append(current)
    return [p.strip() for p in out if p.strip()]


_W = docx_zip.W
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
# 문단 글에 들어가지 않는 하위 요소: 지운 글(변경 추적), 옮기기 전 자리, 문단 속성
_DOCX_SKIP = frozenset({f"{_W}pPr", f"{_W}rPr", f"{_W}del", f"{_W}moveFrom", _MC_FALLBACK})
# 본문 다음에 붙이는 부분 (머리글·바닥글은 쪽마다 되풀이되는 글이라 뺀다)
_DOCX_NOTES = ("word/footnotes.xml", "word/endnotes.xml")
_DOCX_STYLES = "word/styles.xml"
MAX_DOCX_BLOCKS = 5000


def _docx_run_text(run: ET.Element) -> str:
    parts: list[str] = []
    for child in run:
        if child.tag == f"{_W}t":
            parts.append(child.text or "")
        elif child.tag == f"{_W}tab":
            parts.append("\t")
        elif child.tag in (f"{_W}br", f"{_W}cr"):
            parts.append("\n")
    return "".join(parts)


# 숨김 run 판정 (run, 그 run의 문단) → True면 글을 뺀다. None이면 모두 읽는다
HiddenRun = Callable[[ET.Element, ET.Element], bool] | None


def _docx_para_text(para: ET.Element, boxes: list[ET.Element], hidden: HiddenRun = None) -> str:
    """문단 글. 글상자(``w:txbxContent``)는 글을 섞지 않고 ``boxes``에 모아 문단 뒤에 따로 블록으로."""
    out: list[str] = []

    def walk(el: ET.Element) -> None:
        for child in el:
            tag = child.tag
            if tag in _DOCX_SKIP:
                continue
            if tag == f"{_W}r":
                if hidden is None or not hidden(child, para):
                    out.append(_docx_run_text(child))
                walk(child)  # run 안의 그림·글상자 (글상자 문단은 제 서식으로 따로 본다)
            elif tag == f"{_W}txbxContent" or tag == f"{_W}p":
                boxes.append(child)
            else:
                walk(child)  # 링크·내용 컨트롤·삽입(ins)·그림 껍데기

    walk(para)
    return "".join(out)


def _docx_paras(el: ET.Element):
    """하위 문단 (대체 표현 ``mc:Fallback``·지운 글 안은 빼고, 같은 글을 두 번 읽지 않게)."""
    for child in el:
        if child.tag in _DOCX_SKIP:
            continue
        if child.tag == f"{_W}p":
            yield child
        yield from _docx_paras(child)


def _docx_cell_text(cell: ET.Element, hidden: HiddenRun = None) -> str:
    texts = []
    for p in _docx_paras(cell):
        t = _docx_para_text(p, [], hidden)
        if t.strip():
            texts.append(t)
    return " ".join(texts)


def _docx_blocks(container: Iterable[ET.Element], emit, hidden: HiddenRun = None) -> None:
    """본문 순서대로: 문단 하나 = 블록 하나, 표는 행 하나 = 블록 하나 (칸은 `` | ``로)."""
    for child in container:
        tag = child.tag
        if tag in _DOCX_SKIP or tag == f"{_W}sectPr":
            continue
        if tag == f"{_W}p":
            boxes: list[ET.Element] = []
            emit(_docx_para_text(child, boxes, hidden))
            for box in boxes:
                _docx_blocks([box] if box.tag == f"{_W}p" else box, emit, hidden)
        elif tag == f"{_W}tbl":
            for row in child.findall(f"{_W}tr"):
                cells = [_docx_cell_text(c, hidden) for c in row.findall(f"{_W}tc")]
                emit(" | ".join(c for c in cells if c.strip()))
        else:
            _docx_blocks(child, emit, hidden)  # 내용 컨트롤(sdt)·customXml 등 껍데기


class DocxTextParser:
    """DOCX 본문·각주·미주 → 블록. 기본(``parse``)은 숨김 서식 글도 글로 읽는다 (PDF의 흰 글씨처럼);
    숨김 여부는 ``GET /core/sources/{id}/hidden-text``가 따로 경고한다. ``parse_visible``(c0029, 업로드
    선택 ``hidden_text=exclude``)은 숨김 글 검사가 ``hidden_text_vanish``로 세는 run(``w:vanish``·
    ``specVanish``·``webHidden``, 글자·문단 스타일 상속 포함)을 뺀다. 이미지 안의 글은 읽지 않는다."""

    def parse(self, data: bytes) -> LocalParseResult:
        return self._parse(data, exclude_hidden=False)

    def parse_visible(self, data: bytes) -> LocalParseResult:
        return self._parse(data, exclude_hidden=True)

    def _parse(self, data: bytes, *, exclude_hidden: bool) -> LocalParseResult:
        from career_core.domain.hidden_text import InspectError, docx_run_kinds, docx_style_kinds

        wanted = (*_DOCX_NOTES, _DOCX_STYLES) if exclude_hidden else _DOCX_NOTES
        try:
            parts = docx_zip.read_parts(data, lambda name: name in wanted)
            hidden: HiddenRun = None
            if exclude_hidden:
                try:
                    styles = docx_style_kinds(parts.get(_DOCX_STYLES))
                except InspectError as exc:
                    raise docx_zip.DocxError(exc.code) from exc

                def hidden(run: ET.Element, para: ET.Element) -> bool:
                    return "hidden_text_vanish" in docx_run_kinds(run, para, styles)

            blocks: list[ParsedBlock] = []

            def emit(text: str) -> None:
                clean = re.sub(r"[ \t]+", " ", text.replace("\r", "")).strip()
                clean = re.sub(r" ?\n ?", "\n", clean)
                if clean:
                    if len(blocks) >= MAX_DOCX_BLOCKS:
                        raise ParseError("too_many_blocks")
                    blocks.append(ParsedBlock(page=None, text=clean))

            for name in (docx_zip.MAIN_PART, *_DOCX_NOTES):
                if name not in parts:
                    continue
                root = docx_zip.parse_xml(parts[name])
                if name == docx_zip.MAIN_PART:
                    body = root.find(f"{_W}body")
                    _docx_blocks(body if body is not None else root, emit, hidden)
                else:
                    for note in root:
                        # 구분선·이어짐 표시(w:type 있음)는 글이 아니다
                        if note.get(f"{_W}type") in (None, "normal"):
                            _docx_blocks(note, emit, hidden)
        except docx_zip.DocxError as exc:
            raise ParseError("malformed" if exc.code == "invalid_docx" else exc.code) from exc
        return LocalParseResult(blocks=tuple(blocks), pages_total=None)


class ImageParser:
    """이미지는 로컬에서 글자를 읽지 않는다: 1쪽짜리, 텍스트 없음."""

    def parse(self, data: bytes) -> LocalParseResult:
        return LocalParseResult(blocks=(), pages_total=1)


def default_local_parsers() -> Mapping[str, LocalParser]:
    text = PlainTextParser()
    image = ImageParser()
    return {
        "text/plain": text,
        "text/markdown": text,
        "text/html": HtmlTextParser(),
        "application/pdf": PdfTextParser(),
        DOCX_MIME: DocxTextParser(),
        PPTX_MIME: PptxTextParser(),
        XLSX_MIME: XlsxTextParser(),
        HWPX_MIME: HwpxTextParser(),
        HWP_MIME: HwpTextParser(),
        "image/png": image,
        "image/jpeg": image,
    }

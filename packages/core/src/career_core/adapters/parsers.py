"""로컬 추출기. 외부 전송 없이 텍스트·HTML·텍스트 PDF·DOCX를 블록으로 나눈다.

스캔 PDF·이미지처럼 텍스트가 없는 쪽은 추출하지 않고 ``pages_with_text``에서 빠진다
(부분 파싱으로 표시되고, 허용된 경우에만 외부 OCR로 넘어간다).

DOCX(c0023)는 표준 라이브러리(``zipfile`` + ``xml.etree``)로 읽는다. 쪽 번호는 만들지 않는다
(DOCX에는 조판된 쪽이 없다 — plan §9 "없는 좌표를 만들어 넣지 않는다"). 블록 위치는 순서(``ordinal``)다.
"""

from __future__ import annotations

import io
import re
from collections.abc import Callable, Iterable, Mapping
from html.parser import HTMLParser
from typing import ClassVar
from xml.etree import ElementTree as ET

from career_core.domain import docx_zip
from career_core.ports import LocalParser, LocalParseResult, ParsedBlock, ParseError
from career_core.uploads import DOCX_MIME

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
            blocks: list[ParsedBlock] = []
            with_text: set[int] = set()
            for index, page in enumerate(reader.pages, start=1):
                text = (page.extract_text() or "").strip()
                if text:
                    with_text.add(index)
                    blocks.append(ParsedBlock(page=index, text=text))
        except PdfReadError as exc:
            raise ParseError("malformed") from exc
        return LocalParseResult(
            blocks=tuple(blocks), pages_total=total, pages_with_text=frozenset(with_text)
        )


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
        "image/png": image,
        "image/jpeg": image,
    }

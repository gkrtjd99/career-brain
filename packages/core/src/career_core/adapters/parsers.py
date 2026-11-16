"""로컬 추출기. 외부 전송 없이 텍스트·HTML·텍스트 PDF를 블록으로 나눈다.

스캔 PDF·이미지처럼 텍스트가 없는 쪽은 추출하지 않고 ``pages_with_text``에서 빠진다
(부분 파싱으로 표시되고, 허용된 경우에만 외부 OCR로 넘어간다).
"""

from __future__ import annotations

import io
import re
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import ClassVar

from career_core.ports import LocalParser, LocalParseResult, ParsedBlock, ParseError

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
        "image/png": image,
        "image/jpeg": image,
    }

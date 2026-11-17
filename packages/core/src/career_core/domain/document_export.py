"""문서 내보내기: Markdown · DOCX · 인쇄용 HTML(브라우저에서 PDF로 저장). 순수 코드, 의존성 없음.

- DOCX는 표준 라이브러리(zipfile)로 WordprocessingML 최소 묶음을 직접 쓴다. 문단·줄바꿈·제목만 있으면
  되므로 python-docx(+lxml 수 MB)를 들이지 않는다. 같은 입력이면 같은 바이트(고정 시각)다.
- PDF는 서버에서 만들지 않는다. 한글 PDF는 글꼴을 넣어야 해서(수 MB, 라이선스 확인 필요) 가볍지 않다.
  대신 인쇄용 HTML을 주고 브라우저의 "PDF로 저장"을 쓴다.
- 내보내기 전에 만든 파일 자체를 다시 읽어 검사한다(``inspect_export``): 보이지 않는 문자, 숨김 서식
  (DOCX의 vanish·흰 글씨·크기 0, HTML/Markdown의 숨김 CSS), 심사 모델 대상 지시문, 키워드 반복.
  경고만 하고 내용을 바꾸지 않는다.
"""

from __future__ import annotations

import html
import io
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from career_core.domain.documents import HIDDEN_MARKUP, Finding, ats_findings

FORMATS = ("md", "docx", "html")
MEDIA_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "html": "text/html; charset=utf-8",
}
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
# XML 1.0에 넣을 수 없는 제어 문자 (탭·줄바꿈 제외)
_XML_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_MD_LINE_START = re.compile(r"^(\s*)([#>*+\-=|`]|\d+[.)](?=\s|$))", re.MULTILINE)


class ExportError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ExportSection:
    """파일 안의 한 묶음 (c0016 제출본 내보내기: 문서마다 하나). 제목이 없으면 문단만 쓴다.

    ``note``는 제목 아래 한 줄(문항 문구 등). 모든 칸은 글자 그대로 escape해 쓴다.
    """

    paragraphs: tuple[str, ...]
    heading: str | None = None
    note: str | None = None


def _sections(
    paragraphs: Sequence[str], sections: Sequence[ExportSection] | None
) -> tuple[ExportSection, ...]:
    if sections is None:
        return (ExportSection(tuple(paragraphs)),)
    return tuple(sections)


def export_filename(title: str, fmt: str) -> str:
    base = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", title or "").strip()
    base = re.sub(r"\s+", " ", base)[:80].strip() or "document"
    ext = "pdf.html" if fmt == "html" else fmt
    return f"{base}.{ext}"


# ---------------------------------------------------------------- Markdown


def md_inline(text: str) -> str:
    """줄 가운데 글: 역슬래시와 '<'만 escape (줄 앞 기호 escape는 하지 않는다)."""
    return (text or "").replace("\\", "\\\\").replace("<", "\\<").replace("\n", " ")


def md_escape(text: str) -> str:
    """줄 앞의 Markdown 기호와 '<'를 글자 그대로 보이게 escape한다. 문단 안 줄바꿈은 Markdown 줄바꿈으로."""
    text = text.replace("\\", "\\\\").replace("<", "\\<")
    text = _MD_LINE_START.sub(lambda m: m.group(1) + "\\" + m.group(2), text)
    return text.replace("\n", "  \n")


def render_markdown(
    title: str, paragraphs: Sequence[str] = (), *, sections: Sequence[ExportSection] | None = None
) -> str:
    """제목 + 문단(빈 줄로 구분). 줄 앞의 Markdown 기호와 '<'는 글자 그대로 보이게 escape한다.

    ``sections``를 주면 묶음마다 ``## 제목``·안내 줄·문단을 차례로 쓴다 (제출본 내보내기).
    """
    esc = md_escape
    parts = [f"# {esc(title)}"]
    for sec in _sections(paragraphs, sections):
        if sec.heading:
            parts.append(f"## {esc(sec.heading)}")
        if sec.note:
            parts.append(esc(sec.note))
        parts.extend(esc(p) for p in sec.paragraphs)
    return "\n\n".join(parts) + "\n"


# ---------------------------------------------------------------- HTML (인쇄용)

_PRINT_CSS = """
@page { size: A4; margin: 20mm 18mm; }
body { font-family: "Pretendard", "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR",
  sans-serif; font-size: 11pt; line-height: 1.7; color: #111; background: #fff;
  max-width: 720px; margin: 24px auto; padding: 0 16px; word-break: keep-all; }
h1 { font-size: 18pt; margin: 0 0 16px; }
h2 { font-size: 13pt; margin: 18pt 0 6pt; }
p { margin: 0 0 10pt; white-space: pre-wrap; }
p.note { color: #444; font-size: 10pt; }
"""


def render_html(
    title: str, paragraphs: Sequence[str] = (), *, sections: Sequence[ExportSection] | None = None
) -> str:
    """브라우저 인쇄(PDF로 저장)용 단독 HTML. 스크립트·외부 자원 없음, 모든 글은 escape."""
    pieces: list[str] = []
    for sec in _sections(paragraphs, sections):
        if sec.heading:
            pieces.append(f"<h2>{html.escape(sec.heading)}</h2>")
        if sec.note:
            pieces.append(f'<p class="note">{html.escape(sec.note)}</p>')
        pieces.extend(f"<p>{html.escape(p)}</p>" for p in sec.paragraphs)
    body = "\n".join(pieces)
    return (
        "<!doctype html>\n"
        '<html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_PRINT_CSS}</style></head>"
        f"<body><h1>{html.escape(title)}</h1>\n{body}\n</body></html>\n"
    )


# ---------------------------------------------------------------- DOCX

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/docProps/core.xml" '
    'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/'
    'relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    "</Relationships>"
)
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _xml_text(text: str) -> str:
    return html.escape(_XML_INVALID.sub("�", text), quote=False)


def _run(text: str, *, bold: bool = False, size_half_points: int | None = None) -> str:
    props = '<w:lang w:val="ko-KR" w:eastAsia="ko-KR"/>'
    if bold:
        props = "<w:b/>" + props
    if size_half_points:
        props += f'<w:sz w:val="{size_half_points}"/><w:szCs w:val="{size_half_points}"/>'
    lines = text.split("\n")
    pieces = []
    for i, line in enumerate(lines):
        if i:
            pieces.append("<w:br/>")
        pieces.append(f'<w:t xml:space="preserve">{_xml_text(line)}</w:t>')
    return f"<w:r><w:rPr>{props}</w:rPr>{''.join(pieces)}</w:r>"


def render_docx(
    title: str, paragraphs: Sequence[str] = (), *, sections: Sequence[ExportSection] | None = None
) -> bytes:
    paras = [
        f'<w:p><w:pPr><w:spacing w:after="240"/></w:pPr>{_run(title, bold=True, size_half_points=32)}</w:p>'
    ]
    for sec in _sections(paragraphs, sections):
        if sec.heading:
            paras.append(
                '<w:p><w:pPr><w:spacing w:before="240" w:after="120"/></w:pPr>'
                f"{_run(sec.heading, bold=True, size_half_points=26)}</w:p>"
            )
        if sec.note:
            paras.append(
                f'<w:p><w:pPr><w:spacing w:after="120"/></w:pPr>{_run(sec.note, size_half_points=20)}</w:p>'
            )
        for p in sec.paragraphs:
            paras.append(f'<w:p><w:pPr><w:spacing w:after="160"/></w:pPr>{_run(p)}</w:p>')
    return docx_package(title, "".join(paras))


@dataclass(frozen=True, slots=True)
class DocxMedia:
    """DOCX에 넣는 그림 (이력서 증명사진, O17). ``rel_id``로 본문 ``<a:blip r:embed>``가 가리킨다."""

    rel_id: str
    name: str  # word/media/ 아래 파일 이름 (예: photo.jpeg)
    mime: str  # image/jpeg | image/png
    data: bytes


_DOCX_NS = (
    f'xmlns:w="{_W}" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
)


def docx_package(title: str, body_xml: str, media: Sequence[DocxMedia] = ()) -> bytes:
    """본문 XML(``<w:p>``·``<w:tbl>`` …)을 A4 DOCX 묶음으로. 같은 입력이면 같은 바이트(고정 시각).

    문서 내보내기(``render_docx``)와 이력서 내보내기(``domain.resume_export``)가 같이 쓴다.
    """
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<w:document {_DOCX_NS}><w:body>{body_xml}"
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1020" w:bottom="1134" w:left="1020" '
        'w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )
    # 작성자·시각 같은 메타데이터는 넣지 않는다 (제목만)
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
        'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{_xml_text(title)}</dc:title></cp:coreProperties>"
    )
    content_types = _CONTENT_TYPES
    parts: list[tuple[str, bytes]] = []
    if media:
        exts = {m.name.rsplit(".", 1)[-1]: m.mime for m in media}
        defaults = "".join(
            f'<Default Extension="{ext}" ContentType="{mime}"/>'
            for ext, mime in sorted(exts.items())
        )
        content_types = content_types.replace(
            '<Default Extension="xml"', defaults + '<Default Extension="xml"', 1
        )
        rels = "".join(
            f'<Relationship Id="{m.rel_id}" Type="http://schemas.openxmlformats.org/'
            f'officeDocument/2006/relationships/image" Target="media/{m.name}"/>'
            for m in media
        )
        parts.append(
            (
                "word/_rels/document.xml.rels",
                (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                    f'relationships">{rels}</Relationships>'
                ).encode(),
            )
        )
        parts.extend((f"word/media/{m.name}", m.data) for m in media)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in (
            ("[Content_Types].xml", content_types.encode("utf-8")),
            ("_rels/.rels", _RELS.encode("utf-8")),
            ("word/document.xml", document.encode("utf-8")),
            ("docProps/core.xml", core.encode("utf-8")),
            *parts,
        ):
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return buf.getvalue()


def render(
    fmt: str,
    title: str,
    paragraphs: Sequence[str] = (),
    *,
    sections: Sequence[ExportSection] | None = None,
) -> bytes:
    """문서 revision과 제출본(묶음 여러 개)이 같은 writer를 쓴다."""
    if fmt == "md":
        return render_markdown(title, paragraphs, sections=sections).encode("utf-8")
    if fmt == "html":
        return render_html(title, paragraphs, sections=sections).encode("utf-8")
    if fmt == "docx":
        return render_docx(title, paragraphs, sections=sections)
    raise ExportError("invalid_format")


# ---------------------------------------------------------------- 만든 파일 검사

# DOCX 숨김 서식: 숨김(vanish), 흰 글씨, 크기 0~1(반 포인트), 흰 형광펜 위 흰 글씨 등
_DOCX_HIDDEN = re.compile(
    r"(<w:vanish\b(?![^>]*w:val=\"(?:0|false)\")[^>]*/?>|<w:specVanish\b[^>]*/?>|"
    r"<w:color\b[^>]*w:val=\"(?:FFFFFF|ffffff|white)\"|"
    r"<w:sz\b[^>]*w:val=\"[01]\"|<w:webHidden\b[^>]*/?>)"
)
_DOCX_TEXT = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>|<w:(?:br|cr|tab)\b[^>]*/>|</w:p>", re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


def _docx_text(xml: str) -> str:
    out = []
    for m in _DOCX_TEXT.finditer(xml):
        if m.group(1) is not None:
            out.append(html.unescape(m.group(1)))
        elif m.group(0).startswith("</w:p"):
            out.append("\n")
        elif "tab" in m.group(0):
            out.append("\t")
        else:
            out.append("\n")
    return "".join(out)


def _html_text(raw: str) -> str:
    raw = re.sub(r"<(style|script)\b.*?</\1>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r"<(?:br|/p|/h\d|/div|/li)\b[^>]*>", "\n", raw, flags=re.IGNORECASE)
    return html.unescape(_TAG.sub("", raw))


def inspect_export(fmt: str, data: bytes) -> list[Finding]:
    """만든 파일 내용을 다시 읽어 숨김 글·조작 문구를 찾는다. 파일 형식별로 보이는 글과 서식을 따로 본다."""
    findings: list[Finding] = []
    if fmt == "docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                parts = {
                    n: z.read(n).decode("utf-8", "replace")
                    for n in z.namelist()
                    if n.startswith("word/") and n.endswith(".xml")
                }
        except zipfile.BadZipFile as exc:
            raise ExportError("invalid_docx") from exc
        text = "\n".join(_docx_text(x) for x in parts.values())
        for name, xml in sorted(parts.items()):
            if m := _DOCX_HIDDEN.search(xml):
                findings.append(
                    Finding(
                        "hidden_text_markup",
                        "파일에 숨김·흰 글씨·크기 0 같은 서식이 있습니다. 숨긴 글은 넣지 않습니다.",
                        detail={"part": name, "match": m.group(0)[:80]},
                    )
                )
        findings.extend(f for f in ats_findings(text) if f.code != "hidden_text_markup")
    else:
        raw = data.decode("utf-8", "replace")
        visible = _html_text(raw) if fmt == "html" else raw
        if fmt == "html" and (m := HIDDEN_MARKUP.search(raw)):
            findings.append(
                Finding(
                    "hidden_text_markup",
                    "파일에 숨김 서식이 있습니다. 숨긴 글은 넣지 않습니다.",
                    detail={"match": m.group(0)[:80]},
                )
            )
        findings.extend(ats_findings(visible))
    return findings


def export_summary(findings: Sequence[Finding]) -> dict[str, Any]:
    codes: dict[str, int] = {}
    for f in findings:
        codes[f.code] = codes.get(f.code, 0) + 1
    return dict(sorted(codes.items()))


__all__ = [
    "FORMATS",
    "MEDIA_TYPES",
    "DocxMedia",
    "ExportError",
    "ExportSection",
    "docx_package",
    "export_filename",
    "export_summary",
    "inspect_export",
    "md_escape",
    "render",
    "render_docx",
    "render_html",
    "render_markdown",
]

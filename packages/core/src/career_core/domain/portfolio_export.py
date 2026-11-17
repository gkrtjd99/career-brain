"""포트폴리오 출력 (O18, plan §19.6): 인쇄용 HTML(A4 쪽 나눔) · 정적 HTML 묶음(zip). 순수 코드.

- **이력서와 같은 규칙: 미리보기 = 내보낸 HTML.** 웹은 이 파일을 sandbox iframe(``srcdoc``)에 그대로 띄우고 인쇄도
  그 안에서 한다. 쪽 나눔은 이력서와 **같은 스크립트**(``resume_export.PAGINATE_JS``, 같은 CSP 해시)다. 표지·목차가
  첫 쪽, 항목마다 새 쪽(``data-break``)에서 시작하고, 목차의 쪽 번호는 스크립트가 나눈 뒤 채운다(``data-toc``).
  항목 하나는 한두 쪽 안에 들게 이미지 높이를 줄인다.
- 인쇄용 HTML(``html``)은 파일 하나로 열린다: 이미지는 ``data:`` URI, CSP는 이력서와 같다
  (``default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'sha256-…'``).
- 정적 HTML 묶음(``zip``)은 ``index.html`` + ``images/…``. 서버 없이 ``file://``로 열린다: 이미지·이력서는 상대 경로로만
  가리키고, 글꼴·스크립트·스타일을 밖에서 가져오지 않는다. CSP는 ``img-src 'self' file:``만 다르다(자기 폴더의 그림).
  함께 넣은 이력서(``resume.html``)의 프로젝트 항목은 ``index.html#item-n``으로 이어진다.
- 링크(저장소·데모 등)는 글로만 적는다(이력서 연락처와 같다). 파일 안에 바깥 주소를 가리키는 ``src``·``href``가 없다.
- 모든 글은 escape한다. 숨김 서식(``display:none``·흰 글씨·인라인 style)을 쓰지 않는다 — 내보내기 검사가 경고한다.
- 받는 것은 ``domain.portfolio``가 공개 범위로 이미 거른 모양이다. 여기서는 받은 것만 쓴다.
"""

from __future__ import annotations

import base64
import html
import io
import zipfile
from collections.abc import Callable
from datetime import datetime

from career_core.domain.document_export import export_filename
from career_core.domain.portfolio import (
    IMAGE_KIND_LABEL,
    LINK_LABEL,
    RenderImage,
    RenderPortfolio,
    RenderPortfolioItem,
)
from career_core.domain.resume_export import PAGINATE_JS, PAGINATE_SHA256

PORTFOLIO_FORMATS = ("html", "zip")
PORTFOLIO_MEDIA_TYPES = {
    "html": "text/html; charset=utf-8",
    "zip": "application/zip",
}
CSP_SINGLE = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
    f"script-src 'sha256-{PAGINATE_SHA256}'"
)
CSP_BUNDLE = (
    "default-src 'none'; img-src 'self' file:; style-src 'unsafe-inline'; "
    f"script-src 'sha256-{PAGINATE_SHA256}'"
)

_CSS = """
@page { size: A4; margin: 0; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { font-family: "Pretendard", "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR",
  sans-serif; color: #111; font-size: 10pt; line-height: 1.6; word-break: keep-all;
  overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.page, .flow { width: 210mm; padding: 18mm 18mm 18mm; background: #fff; position: relative; }
.page { height: 297mm; overflow: hidden; break-after: page; }
.page:last-of-type { break-after: auto; }
.flow { min-height: 297mm; margin: 0 auto; }
.pc { height: 100%; }
.pn { position: absolute; right: 18mm; bottom: 8mm; font-size: 8pt; color: #777; }
@media screen {
  body { background: #e6e6e6; padding: 16px 0; }
  .page { margin: 0 auto 16px; box-shadow: 0 1px 4px rgba(0, 0, 0, 0.18); }
}
@media print {
  body { background: none; }
  .page { margin: 0; box-shadow: none; }
}
h1, h2, h3, p, figure { margin: 0; }
a { color: inherit; }
.b { padding-bottom: 1.4mm; }
.muted { color: #555; }
.cover { padding-top: 40mm; padding-bottom: 12mm; border-bottom: 0.3mm solid #111; margin-bottom: 8mm; }
.cover h1 { font-size: 24pt; font-weight: 700; line-height: 1.25; letter-spacing: -0.01em; }
.cover .who { font-size: 12pt; margin-top: 6mm; }
.cover .sub { margin-top: 1.5mm; color: #444; }
.cover .intro { margin-top: 5mm; white-space: pre-line; }
.cover .resume { margin-top: 4mm; }
.toc-h { font-size: 11.5pt; font-weight: 700; padding: 2mm 0 2mm; }
.toc { display: flex; gap: 4mm; align-items: baseline; padding: 1.2mm 0;
  border-bottom: 0.2mm solid #ddd; }
.toc a { flex: 1; min-width: 0; text-decoration: none; }
.toc .tn { color: #555; min-width: 8mm; text-align: right; }
.it { font-size: 16pt; font-weight: 700; line-height: 1.3; padding-bottom: 1mm; }
.meta { color: #444; }
.tags { color: #555; font-size: 9pt; margin-top: 1mm; }
.links { color: #333; font-size: 9pt; }
.links span + span::before { content: " · "; color: #999; }
.sec { font-size: 11pt; font-weight: 700; padding: 4mm 0 1.2mm; border-bottom: 0.3mm solid #111;
  margin-bottom: 1.5mm; }
.para { white-space: pre-line; }
.ln { padding-left: 4mm; text-indent: -3mm; }
.ln::before { content: "•"; display: inline-block; width: 3mm; text-indent: 0; }
.fig { padding-top: 2mm; text-align: center; }
.fig img { max-width: 100%; max-height: 105mm; width: auto; height: auto;
  border: 0.2mm solid #ccc; }
.fig figcaption { color: #555; font-size: 9pt; margin-top: 1mm; }
.empty { color: #555; padding-top: 20mm; text-align: center; }
"""


def _e(text: str | None) -> str:
    return html.escape(text or "")


def data_uri(img: RenderImage) -> str:
    return f"data:{img.mime};base64,{base64.b64encode(img.data).decode('ascii')}"


def file_ref(img: RenderImage) -> str:
    return f"images/{img.name}"


def _cover(doc: RenderPortfolio) -> list[str]:
    who = " · ".join(v for v in (doc.owner_name, doc.headline) if v)
    parts = [f"<h1>{_e(doc.title)}</h1>"]
    if who:
        parts.append(f'<p class="who">{_e(who)}</p>')
    if doc.subtitle:
        parts.append(f'<p class="sub">{_e(doc.subtitle)}</p>')
    links = [f"{lab} {url}" if lab else url for lab, url in doc.owner_links]
    if links:
        parts.append(f'<p class="sub">{_e(" · ".join(links))}</p>')
    if doc.intro:
        parts.append(f'<p class="intro">{_e(doc.intro)}</p>')
    if doc.resume_href:
        parts.append(f'<p class="resume"><a href="{_e(doc.resume_href)}">이력서 보기</a></p>')
    out = [f'<section class="b cover">{"".join(parts)}</section>']
    if doc.items:
        out.append('<h2 class="b keep toc-h">목차</h2>')
        for n, it in enumerate(doc.items, 1):
            out.append(
                f'<div class="b toc"><span class="tn">{n}</span>'
                f'<a href="#{_e(it.anchor)}">{_e(it.title)}</a>'
                f'<span class="tn" data-toc="{_e(it.anchor)}"></span></div>'
            )
    else:
        out.append('<p class="b empty">아직 넣은 항목이 없습니다.</p>')
    return out


def _item_blocks(it: RenderPortfolioItem, src: Callable[[RenderImage], str]) -> list[str]:
    head = [f'<h2 class="it" id="{_e(it.anchor)}">{_e(it.title)}</h2>']
    if it.meta:
        head.append(f'<p class="meta">{_e(it.meta)}</p>')
    if it.tags:
        head.append(f'<p class="tags">{_e(", ".join(it.tags))}</p>')
    if it.links:
        spans = "".join(
            f"<span>{_e((ln.label or LINK_LABEL.get(ln.kind, ln.kind)) + ' ' + ln.url)}</span>"
            for ln in it.links
        )
        head.append(f'<p class="links">{spans}</p>')
    out = [f'<header class="b keep" data-break>{"".join(head)}</header>']

    def section(title: str) -> None:
        out.append(f'<h3 class="b keep sec">{_e(title)}</h3>')

    if it.problem:
        section("문제")
        out.append(f'<p class="b para">{_e(it.problem)}</p>')
    if it.role:
        section("내 역할")
        out.append(f'<p class="b para">{_e(it.role)}</p>')
    if it.actions:
        section("한 일")
        out.extend(f'<div class="b ln">{_e(x)}</div>' for x in it.actions)
    if it.results:
        section("결과")
        out.extend(f'<div class="b ln">{_e(x)}</div>' for x in it.results)
    for img in it.images:
        label = img.caption or IMAGE_KIND_LABEL.get(img.kind, "")
        out.append(
            f'<figure class="b fig"><img src="{_e(src(img))}" alt="{_e(label)}" '
            f'width="{img.width}" height="{img.height}">'
            f"<figcaption>{_e(label)}</figcaption></figure>"
        )
    return out


def render_portfolio_html(
    doc: RenderPortfolio,
    *,
    image_src: Callable[[RenderImage], str] = data_uri,
    csp: str = CSP_SINGLE,
) -> str:
    blocks = _cover(doc)
    for it in doc.items:
        blocks.extend(_item_blocks(it, image_src))
    body = "\n".join(blocks)
    return (
        "<!doctype html>\n"
        '<html lang="ko"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="referrer" content="no-referrer">'
        f"<title>{_e(doc.title)}</title><style>{_CSS}</style></head>"
        f'<body class="portfolio">\n<main id="flow" class="flow">\n{body}\n</main>\n'
        f"<script>{PAGINATE_JS}</script>\n</body></html>\n"
    )


def render_portfolio_zip(
    doc: RenderPortfolio,
    *,
    resume_html: bytes | None = None,
    when: datetime | None = None,
) -> bytes:
    """정적 HTML 묶음: ``index.html`` + ``images/…`` (+ ``resume.html``). 경로는 모두 상대 경로."""
    stamp = when or datetime(2026, 1, 1)
    date_time = (stamp.year, stamp.month, stamp.day, stamp.hour, stamp.minute, stamp.second)
    index = render_portfolio_html(doc, image_src=file_ref, csp=CSP_BUNDLE).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:

        def put(name: str, data: bytes, *, compress: bool = True) -> None:
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)

        put("index.html", index)
        if resume_html is not None:
            put("resume.html", resume_html)
        seen: set[str] = set()
        for it in doc.items:
            for img in it.images:
                if img.name in seen:
                    continue
                seen.add(img.name)
                put(file_ref(img), img.data, compress=False)
    return buf.getvalue()


def portfolio_filename(title: str, fmt: str) -> str:
    if fmt == "zip":
        return export_filename(title, "md")[: -len(".md")] + ".zip"
    return export_filename(title, "html")


__all__ = [
    "CSP_BUNDLE",
    "CSP_SINGLE",
    "PORTFOLIO_FORMATS",
    "PORTFOLIO_MEDIA_TYPES",
    "data_uri",
    "file_ref",
    "portfolio_filename",
    "render_portfolio_html",
    "render_portfolio_zip",
]

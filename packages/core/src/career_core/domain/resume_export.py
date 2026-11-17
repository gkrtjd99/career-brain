"""이력서 출력 (O17, plan §19.5): 인쇄용 HTML(A4 쪽 나눔) · DOCX · Markdown · JSON Resume. 순수 코드.

- **HTML 하나가 미리보기이자 인쇄본이자 내보낸 파일이다.** 웹 미리보기는 이 파일을 그대로 띄우고(iframe
  ``srcdoc``, ``sandbox="allow-scripts allow-modals"``) 인쇄도 그 안에서 한다. 파일 안의 작은 스크립트
  (``PAGINATE_JS``)가 블록(구역 제목·항목 머리·줄·표 행)을 A4 쪽(210×297mm, 여백 포함)에 차례로 넣어 쪽을 나눈다.
  인쇄는 ``@page { size: A4; margin: 0 }``로 그 쪽을 한 장씩 찍으므로 화면의 쪽 나눔과 종이가 같다.
  제목·항목 머리는 다음 블록과 같은 쪽에 두고, 표가 쪽을 넘으면 새 쪽에 머리 행을 다시 놓는다.
  스크립트가 돌지 않으면(스크립트 차단) 한 장으로 이어 보이고 브라우저가 쪽을 나눈다.
- 파일은 서버 없이 열린다: 글꼴·그림·스크립트를 밖에서 가져오지 않는다(사진은 ``data:`` URI). CSP meta가
  이 스크립트(해시)만 허용한다. 모든 글은 escape하고, 숨김 서식(``display:none``·흰 글씨·인라인 style)을 쓰지
  않는다 — 내보내기 검사(``inspect_export``)가 그런 서식을 경고하기 때문이다.
- DOCX는 문서 writer(``docx_package``)를 넓혀 쓴다: 한 단·두 단은 문단, 한국식 표 양식은 Word 표. 사진은
  블라인드가 아니고 표시를 켰을 때만 ``word/media``에 들어간다. 쪽 나눔은 Word가 한다.
- JSON Resume(https://jsonresume.org/schema, v1.0.0)은 다른 도구와 주고받는 형식이다. 사진은 넣지 않는다.
- 블라인드 판본은 ``domain.resume.build_render``가 이미 칸을 뺀 모양을 받는다. 여기서는 받은 것만 쓴다.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
from collections.abc import Sequence
from typing import Any

from career_core.domain.document_export import (
    DocxMedia,
    _run,
    docx_package,
    md_escape,
)
from career_core.domain.resume import RenderDoc, RenderItem, RenderProfile, RenderSection

RESUME_FORMATS = ("html", "docx", "md", "json")
RESUME_MEDIA_TYPES = {
    "html": "text/html; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "md": "text/markdown; charset=utf-8",
    "json": "application/json; charset=utf-8",
}
JSON_RESUME_SCHEMA = "https://raw.githubusercontent.com/jsonresume/resume-schema/v1.0.0/schema.json"

# 표 양식의 칸 이름 (구역 종류 → 기간 | 주 칸 | 둘째 칸 | 넷째 칸)
TABLE_COLUMNS: dict[str, tuple[str, str, str, str]] = {
    "work": ("기간", "회사·기관", "직위·역할", "담당 업무"),
    "project": ("기간", "이름", "역할·소속", "내용"),
    "activities": ("기간", "이름", "역할·소속", "내용"),
    "publications": ("날짜", "제목", "발표처", "내용"),
    "education": ("기간", "학교", "전공", "구분"),
    "credentials": ("취득일", "이름", "발급 기관", "점수·등급"),
    "awards": ("수상일", "이름", "수여 기관", "내용"),
}
BLIND_EDUCATION_COLUMNS = ("기간", "학위 과정", "전공", "학점")

# ---------------------------------------------------------------- HTML

PAGINATE_JS = """(function () {
  var flow = document.getElementById("flow");
  if (!flow) return;
  function post() {
    if (window.parent === window) return;
    window.parent.postMessage({
      type: "career-resume",
      pages: document.querySelectorAll(".page").length,
      height: document.documentElement.scrollHeight
    }, "*");
  }
  function paginate() {
    var blocks = Array.prototype.slice.call(flow.children);
    var heads = {};
    blocks.forEach(function (b) {
      if (b.hasAttribute("data-th")) heads[b.getAttribute("data-th")] = b;
    });
    var pages = [], pc = null;
    function newPage() {
      var page = document.createElement("div");
      page.className = "page";
      pc = document.createElement("div");
      pc.className = "pc";
      page.appendChild(pc);
      document.body.insertBefore(page, flow);
      pages.push(page);
    }
    function over() { return pc.scrollHeight > pc.clientHeight + 1; }
    newPage();
    for (var i = 0; i < blocks.length; i++) {
      var b = blocks[i];
      pc.appendChild(b);
      if (!over()) continue;
      var carry = [b];
      while (pc.children.length > carry.length) {
        var prev = pc.children[pc.children.length - carry.length - 1];
        if (prev.classList.contains("keep")) carry.unshift(prev); else break;
      }
      if (pc.children.length === carry.length) continue;
      newPage();
      var s = carry[0].getAttribute("data-s");
      if (s !== null && !carry[0].hasAttribute("data-th") && heads[s]) {
        var h = heads[s].cloneNode(true);
        h.removeAttribute("data-th");
        pc.appendChild(h);
      }
      carry.forEach(function (x) { pc.appendChild(x); });
    }
    flow.parentNode.removeChild(flow);
    pages.forEach(function (p, n) {
      var f = document.createElement("div");
      f.className = "pn";
      f.textContent = (n + 1) + " / " + pages.length;
      p.appendChild(f);
    });
    document.documentElement.setAttribute("data-pages", String(pages.length));
    post();
  }
  window.addEventListener("message", function (e) {
    if (e.data && e.data.type === "career-resume:print") window.print();
  });
  var imgs = Array.prototype.slice.call(document.images);
  var waits = imgs.map(function (img) {
    return img.complete ? null : new Promise(function (r) { img.onload = img.onerror = r; });
  }).filter(Boolean);
  var fonts = document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve();
  Promise.all([fonts].concat(waits)).then(paginate, paginate);
})();"""

# CSP에 넣는 스크립트 해시. 웹 앱을 CSP와 함께 띄우면 미리보기 iframe도 이 해시를 허용해야 한다(web.md).
PAGINATE_SHA256 = base64.b64encode(hashlib.sha256(PAGINATE_JS.encode("utf-8")).digest()).decode()
CSP = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
    f"script-src 'sha256-{PAGINATE_SHA256}'"
)

_CSS = """
@page { size: A4; margin: 0; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { font-family: "Pretendard", "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR",
  sans-serif; color: #111; font-size: 10pt; line-height: 1.55; word-break: keep-all;
  overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.page, .flow { width: 210mm; padding: 16mm 17mm 18mm; background: #fff; position: relative; }
.page { height: 297mm; overflow: hidden; break-after: page; }
.page:last-of-type { break-after: auto; }
.flow { min-height: 297mm; margin: 0 auto; }
.pc { height: 100%; }
.pn { position: absolute; right: 17mm; bottom: 8mm; font-size: 8pt; color: #777; }
@media screen {
  body { background: #e6e6e6; padding: 16px 0; }
  .page { margin: 0 auto 16px; box-shadow: 0 1px 4px rgba(0, 0, 0, 0.18); }
}
@media print {
  body { background: none; }
  .page { margin: 0; box-shadow: none; }
}
h1, h2, h3, p { margin: 0; }
a { color: inherit; text-decoration: none; }
.b { padding-bottom: 1.2mm; }
.muted { color: #555; }
.head { display: flex; gap: 6mm; align-items: flex-start; padding-bottom: 5mm; }
.head .who { flex: 1; min-width: 0; }
.name { font-size: 20pt; font-weight: 700; line-height: 1.25; letter-spacing: -0.01em; }
.name small { font-size: 11pt; font-weight: 500; color: #555; margin-left: 2mm; }
.headline { font-size: 11pt; margin-top: 1.5mm; }
.contact { margin-top: 2mm; color: #333; }
.contact span + span::before { content: " · "; color: #999; }
.photo { width: 30mm; height: 40mm; object-fit: cover; border: 0.2mm solid #999; }
.sec { font-size: 11.5pt; font-weight: 700; padding: 4mm 0 1.5mm; border-bottom: 0.3mm solid #111;
  margin-bottom: 2mm; }
.ih { padding-top: 1.5mm; }
.ih1 { display: flex; gap: 4mm; justify-content: space-between; align-items: baseline; }
.ih1 strong { font-weight: 600; font-size: 10.5pt; }
.per { color: #444; white-space: nowrap; font-size: 9.5pt; }
.ih2 { color: #444; }
.ln { padding-left: 4mm; text-indent: -3mm; }
.ln::before { content: "•"; display: inline-block; width: 3mm; text-indent: 0; }
.tags { color: #555; font-size: 9pt; }
.sk { line-height: 1.7; }
/* 두 단: 왼쪽 구역 이름, 오른쪽 내용 */
.t-two_column .row { display: grid; grid-template-columns: 30mm 1fr; gap: 5mm; padding: 1.5mm 0 2mm; }
.t-two_column .row + .row { border-top: 0.2mm solid #ddd; }
.t-two_column .lab { font-weight: 700; font-size: 10.5pt; }
.t-two_column .row.first { border-top: 0.3mm solid #111; padding-top: 3mm; margin-top: 2mm; }
.t-two_column .cell .ln { margin-top: 0.4mm; }
/* 한국식 표 양식 */
.t-korean_table .doc-title { text-align: center; font-size: 20pt; font-weight: 700;
  letter-spacing: 0.6em; padding: 2mm 0 6mm; }
.pt { display: grid; grid-template-columns: 34mm 26mm 1fr; border: 0.3mm solid #333;
  margin-bottom: 5mm; }
.pt.nophoto { grid-template-columns: 26mm 1fr; }
.pt .ph.r2 { grid-row: 1 / span 2; }
.pt .ph.r3 { grid-row: 1 / span 3; }
.pt .ph.r4 { grid-row: 1 / span 4; }
.pt .ph.r5 { grid-row: 1 / span 5; }
.pt .ph.r6 { grid-row: 1 / span 6; }
.pt .ph.r7 { grid-row: 1 / span 7; }
.pt .ph.r8 { grid-row: 1 / span 8; }
.pt .ph.r9 { grid-row: 1 / span 9; }
.pt .ph { grid-row: 1 / span 4; border-right: 0.3mm solid #333; display: flex;
  align-items: center; justify-content: center; padding: 2mm; }
.pt .k, .pt .v { padding: 1.6mm 2.5mm; border-bottom: 0.2mm solid #999; }
.pt .k { background: #f0f0f0; font-weight: 600; border-right: 0.2mm solid #999; }
.cap { font-weight: 700; font-size: 11pt; padding: 3mm 0 1.5mm; }
.tr { display: grid; grid-template-columns: 27mm 44mm 38mm 1fr; border: 0.3mm solid #333;
  border-top: 0; }
.tr > div { padding: 1.4mm 2mm; border-right: 0.2mm solid #999; }
.tr > div:last-child { border-right: 0; }
.tr.hd { border-top: 0.3mm solid #333; background: #f0f0f0; font-weight: 600; }
.tr.one { grid-template-columns: 1fr; }
.tr .lns div + div { margin-top: 0.6mm; }
"""


def _e(text: str | None) -> str:
    return html.escape(text or "")


def _contact_spans(p: RenderProfile) -> list[str]:
    out = []
    for v in (p.email, p.phone, p.address, p.birth_date, p.military):
        if v:
            out.append(f"<span>{_e(v)}</span>")
    for label, url in p.links:
        out.append(f"<span>{_e(f'{label} {url}' if label else url)}</span>")
    return out


def _photo_img(p: RenderProfile) -> str:
    if not p.photo:
        return ""
    mime, data = p.photo
    b64 = base64.b64encode(data).decode("ascii")
    return f'<img class="photo" alt="증명사진" src="data:{_e(mime)};base64,{b64}">'


def _head_block(doc: RenderDoc) -> str:
    p = doc.profile
    if p is None:
        return ""
    name = _e(p.name or "")
    if p.name_en:
        name += f"<small>{_e(p.name_en)}</small>"
    headline = f'<p class="headline">{_e(p.headline)}</p>' if p.headline else ""
    contact = _contact_spans(p)
    contact_html = f'<p class="contact">{"".join(contact)}</p>' if contact else ""
    return (
        f'<header class="b head"><div class="who"><h1 class="name">{name}</h1>{headline}'
        f"{contact_html}</div>{_photo_img(p)}</header>"
    )


def _item_head(sec: RenderSection, it: RenderItem) -> str:
    per = f'<span class="per">{_e(it.period)}</span>' if it.period else ""
    sub = " · ".join(v for v in (it.secondary, it.tertiary) if v)
    sub_html = f'<div class="ih2">{_e(sub)}</div>' if sub else ""
    tags = (
        f'<div class="tags">{_e(", ".join(it.tags))}</div>'
        if it.tags and sec.kind == "project"
        else ""
    )
    return f'<div class="ih1"><strong>{_e(it.primary)}</strong>{per}</div>{sub_html}{tags}'


def _skills_text(sec: RenderSection) -> str:
    return " · ".join(it.primary for it in sec.items if it.primary)


def _blocks_one_column(doc: RenderDoc) -> list[str]:
    out = [_head_block(doc)] if doc.profile else []
    for sec in doc.sections:
        out.append(f'<h2 class="b keep sec">{_e(sec.title)}</h2>')
        if sec.kind == "skills":
            out.append(f'<p class="b sk">{_e(_skills_text(sec))}</p>')
            continue
        for it in sec.items:
            keep = " keep" if it.lines else ""
            out.append(f'<div class="b ih{keep}">{_item_head(sec, it)}</div>')
            out.extend(f'<div class="b ln">{_e(ln)}</div>' for ln in it.lines)
    return out


def _blocks_two_column(doc: RenderDoc) -> list[str]:
    out = [_head_block(doc)] if doc.profile else []
    for sec in doc.sections:
        if sec.kind == "skills":
            out.append(
                f'<div class="b row first"><div class="lab">{_e(sec.title)}</div>'
                f'<div class="cell sk">{_e(_skills_text(sec))}</div></div>'
            )
            continue
        for n, it in enumerate(sec.items):
            lines = "".join(f'<div class="ln">{_e(ln)}</div>' for ln in it.lines)
            lab = _e(sec.title) if n == 0 else ""
            first = " first" if n == 0 else ""
            out.append(
                f'<div class="b row{first}"><div class="lab">{lab}</div>'
                f'<div class="cell">{_item_head(sec, it)}{lines}</div></div>'
            )
    return out


def _table_head(sec: RenderSection, blind: bool) -> tuple[str, str, str, str]:
    if sec.kind == "education" and blind:
        return BLIND_EDUCATION_COLUMNS
    return TABLE_COLUMNS.get(sec.kind, ("기간", "이름", "내용", "비고"))


def _blocks_korean_table(doc: RenderDoc) -> list[str]:
    out = ['<h1 class="b doc-title">이력서</h1>']
    p = doc.profile
    if p is not None:
        rows = [("성명", p.name + (f" ({p.name_en})" if p.name_en else "") if p.name else "")]
        if p.birth_date:
            rows.append(("생년월일", p.birth_date))
        rows += [("연락처", p.phone or ""), ("이메일", p.email or "")]
        if p.address:
            rows.append(("주소", p.address))
        if p.military:
            rows.append(("병역", p.military))
        if p.links:
            rows.append(("링크", ", ".join(f"{lab} {url}" if lab else url for lab, url in p.links)))
        cells = "".join(
            f'<div class="k">{_e(k)}</div><div class="v">{_e(v)}</div>' for k, v in rows
        )
        photo = _photo_img(p)
        if photo:
            out.append(f'<div class="b pt"><div class="ph r{len(rows)}">{photo}</div>{cells}</div>')
        else:
            out.append(f'<div class="b pt nophoto">{cells}</div>')
        if p.headline:
            out.append(f'<p class="b headline">{_e(p.headline)}</p>')
    for n, sec in enumerate(doc.sections):
        if sec.kind == "skills":
            out.append(
                f'<div class="b keep" data-s="{n}" data-th="{n}"><p class="cap">{_e(sec.title)}</p></div>'
            )
            out.append(
                f'<div class="b tr one" data-s="{n}"><div>{_e(_skills_text(sec))}</div></div>'
            )
            continue
        cols = "".join(f"<div>{_e(c)}</div>" for c in _table_head(sec, doc.blind))
        out.append(
            f'<div class="b keep" data-s="{n}" data-th="{n}"><p class="cap">{_e(sec.title)}</p>'
            f'<div class="tr hd">{cols}</div></div>'
        )
        for it in sec.items:
            last = [it.tertiary] if it.tertiary else []
            last += list(it.lines)
            lns = "".join(f"<div>{_e(x)}</div>" for x in last)
            out.append(
                f'<div class="b tr" data-s="{n}"><div>{_e(it.period)}</div><div>{_e(it.primary)}</div>'
                f'<div>{_e(it.secondary)}</div><div class="lns">{lns}</div></div>'
            )
    return out


def render_resume_html(doc: RenderDoc) -> str:
    if doc.template == "two_column":
        blocks = _blocks_two_column(doc)
    elif doc.template == "korean_table":
        blocks = _blocks_korean_table(doc)
    else:
        blocks = _blocks_one_column(doc)
    body = "\n".join(b for b in blocks if b)
    return (
        "<!doctype html>\n"
        '<html lang="ko"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_e(doc.title)}</title><style>{_CSS}</style></head>"
        f'<body class="t-{_e(doc.template)}">\n<main id="flow" class="flow">\n{body}\n</main>\n'
        f"<script>{PAGINATE_JS}</script>\n</body></html>\n"
    )


# ---------------------------------------------------------------- Markdown


def render_resume_markdown(doc: RenderDoc) -> str:
    esc = md_escape
    parts: list[str] = []
    p = doc.profile
    if p is not None:
        parts.append(f"# {esc(p.name or doc.title)}")
        if p.headline:
            parts.append(esc(p.headline))
        contact = [v for v in (p.email, p.phone, p.address, p.birth_date, p.military) if v]
        contact += [f"{lab} {url}" if lab else url for lab, url in p.links]
        if contact:
            parts.append(esc(" · ".join(contact)))
    else:
        parts.append(f"# {esc(doc.title)}")
    for sec in doc.sections:
        parts.append(f"## {esc(sec.title)}")
        if sec.kind == "skills":
            parts.append(esc(_skills_text(sec)))
            continue
        for it in sec.items:
            head = f"### {esc(it.primary)}"
            if it.period:
                head += f" — {esc(it.period)}"
            chunk = [head]
            sub = " · ".join(v for v in (it.secondary, it.tertiary) if v)
            if sub:
                chunk.append(esc(sub))
            if it.tags and sec.kind == "project":
                chunk.append(esc("기술: " + ", ".join(it.tags)))
            if it.lines:
                chunk.append("\n".join(f"- {esc(ln)}" for ln in it.lines))
            parts.append("\n\n".join(chunk))
    return "\n\n".join(parts) + "\n"


# ---------------------------------------------------------------- DOCX

_PHOTO_CX, _PHOTO_CY = 1080000, 1440000  # 30 × 40 mm (EMU)
_TABLE_W = 9866  # A4 - 좌우 여백 (twips)
_TABLE_COLS = (1530, 2490, 2150, 3696)


def _p(runs: str, *, after: int = 80, before: int = 0, align: str | None = None) -> str:
    jc = f'<w:jc w:val="{align}"/>' if align else ""
    return f'<w:p><w:pPr><w:spacing w:before="{before}" w:after="{after}"/>{jc}</w:pPr>{runs}</w:p>'


def _photo_run(rel_id: str) -> str:
    return (
        "<w:r><w:drawing>"
        '<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{_PHOTO_CX}" cy="{_PHOTO_CY}"/><wp:docPr id="1" name="photo"/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="photo"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rel_id}"/><a:stretch><a:fillRect/></a:stretch>'
        "</pic:blipFill><pic:spPr>"
        f'<a:xfrm><a:off x="0" y="0"/><a:ext cx="{_PHOTO_CX}" cy="{_PHOTO_CY}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
        "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
    )


def _cell(text_runs: str, width: int, *, shade: bool = False, span: int = 1) -> str:
    fill = '<w:shd w:val="clear" w:color="auto" w:fill="F0F0F0"/>' if shade else ""
    grid = f'<w:gridSpan w:val="{span}"/>' if span > 1 else ""
    return (
        f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{grid}{fill}</w:tcPr>'
        f"{text_runs or '<w:p/>'}</w:tc>"
    )


def _table(rows: list[str], cols: Sequence[int]) -> str:
    border = '<w:{0} w:val="single" w:sz="4" w:space="0" w:color="333333"/>'
    borders = "".join(
        border.format(b) for b in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in cols)
    return (
        f'<w:tbl><w:tblPr><w:tblW w:w="{sum(cols)}" w:type="dxa"/><w:tblBorders>{borders}'
        '</w:tblBorders><w:tblLayout w:type="fixed"/><w:tblCellMar><w:left w:w="80" w:type="dxa"/>'
        '<w:right w:w="80" w:type="dxa"/></w:tblCellMar></w:tblPr>'
        f"<w:tblGrid>{grid}</w:tblGrid>{''.join(rows)}</w:tbl>"
    )


def _cell_paras(texts: Sequence[str], *, bold: bool = False) -> str:
    return "".join(_p(_run(t, bold=bold, size_half_points=19), after=20) for t in texts if t)


def _docx_body_flow(doc: RenderDoc, photo_rel: str | None) -> str:
    out: list[str] = []
    p = doc.profile
    if p is not None:
        if photo_rel:
            out.append(_p(_photo_run(photo_rel), align="right", after=0))
        name = p.name or ""
        out.append(_p(_run(name, bold=True, size_half_points=40), after=40))
        if p.name_en:
            out.append(_p(_run(p.name_en, size_half_points=22), after=40))
        if p.headline:
            out.append(_p(_run(p.headline, size_half_points=22), after=60))
        contact = [v for v in (p.email, p.phone, p.address, p.birth_date, p.military) if v]
        contact += [f"{lab} {url}" if lab else url for lab, url in p.links]
        if contact:
            out.append(_p(_run(" · ".join(contact), size_half_points=19), after=160))
    for sec in doc.sections:
        out.append(
            '<w:p><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="80"/>'
            '<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="111111"/></w:pBdr>'
            f"</w:pPr>{_run(sec.title, bold=True, size_half_points=23)}</w:p>"
        )
        if sec.kind == "skills":
            out.append(_p(_run(_skills_text(sec), size_half_points=20)))
            continue
        for it in sec.items:
            head = it.primary + (f"    {it.period}" if it.period else "")
            out.append(
                '<w:p><w:pPr><w:keepNext/><w:spacing w:before="100" w:after="20"/></w:pPr>'
                f"{_run(head, bold=True, size_half_points=21)}</w:p>"
            )
            sub = " · ".join(v for v in (it.secondary, it.tertiary) if v)
            if sub:
                out.append(_p(_run(sub, size_half_points=19), after=40))
            if it.tags and sec.kind == "project":
                out.append(_p(_run("기술: " + ", ".join(it.tags), size_half_points=18), after=40))
            for ln in it.lines:
                out.append(
                    '<w:p><w:pPr><w:spacing w:after="30"/><w:ind w:left="300" w:hanging="200"/>'
                    f"</w:pPr>{_run('• ' + ln, size_half_points=20)}</w:p>"
                )
    return "".join(out)


def _docx_body_table(doc: RenderDoc, photo_rel: str | None) -> str:
    out = [
        _p(_run("이 력 서", bold=True, size_half_points=40), align="center", after=240),
    ]
    p = doc.profile
    if p is not None:
        rows: list[tuple[str, str]] = [
            ("성명", (p.name or "") + (f" ({p.name_en})" if p.name_en else ""))
        ]
        if p.birth_date:
            rows.append(("생년월일", p.birth_date))
        rows += [("연락처", p.phone or ""), ("이메일", p.email or "")]
        if p.address:
            rows.append(("주소", p.address))
        if p.military:
            rows.append(("병역", p.military))
        if p.links:
            rows.append(("링크", ", ".join(f"{lab} {url}" if lab else url for lab, url in p.links)))
        trs = []
        if photo_rel:
            cols = (1900, 1500, _TABLE_W - 3400)
            for n, (k, v) in enumerate(rows):
                photo_cell = ""
                if n == 0:
                    photo_cell = (
                        f'<w:tc><w:tcPr><w:tcW w:w="{cols[0]}" w:type="dxa"/>'
                        '<w:vMerge w:val="restart"/><w:vAlign w:val="center"/></w:tcPr>'
                        f"{_p(_photo_run(photo_rel), align='center', after=0)}</w:tc>"
                    )
                else:
                    photo_cell = (
                        f'<w:tc><w:tcPr><w:tcW w:w="{cols[0]}" w:type="dxa"/><w:vMerge/></w:tcPr>'
                        "<w:p/></w:tc>"
                    )
                trs.append(
                    f"<w:tr>{photo_cell}{_cell(_cell_paras([k], bold=True), cols[1], shade=True)}"
                    f"{_cell(_cell_paras([v]), cols[2])}</w:tr>"
                )
        else:
            cols = (1500, _TABLE_W - 1500)
            for k, v in rows:
                trs.append(
                    f"<w:tr>{_cell(_cell_paras([k], bold=True), cols[0], shade=True)}"
                    f"{_cell(_cell_paras([v]), cols[1])}</w:tr>"
                )
        out.append(_table(trs, cols))
        if p.headline:
            out.append(_p(_run(p.headline, size_half_points=21), before=120, after=0))
    for sec in doc.sections:
        out.append(
            '<w:p><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="80"/></w:pPr>'
            f"{_run(sec.title, bold=True, size_half_points=22)}</w:p>"
        )
        if sec.kind == "skills":
            out.append(
                _table(
                    [f"<w:tr>{_cell(_cell_paras([_skills_text(sec)]), _TABLE_W)}</w:tr>"],
                    (_TABLE_W,),
                )
            )
            continue
        head = _table_head(sec, doc.blind)
        trs = [
            "<w:tr><w:trPr><w:tblHeader/></w:trPr>"
            + "".join(
                _cell(_cell_paras([h], bold=True), w, shade=True)
                for h, w in zip(head, _TABLE_COLS, strict=True)
            )
            + "</w:tr>"
        ]
        for it in sec.items:
            last = ([it.tertiary] if it.tertiary else []) + list(it.lines)
            vals = (
                _cell_paras([it.period or ""]),
                _cell_paras([it.primary]),
                _cell_paras([it.secondary or ""]),
                _cell_paras(last),
            )
            trs.append(
                "<w:tr><w:trPr><w:cantSplit/></w:trPr>"
                + "".join(_cell(v, w) for v, w in zip(vals, _TABLE_COLS, strict=True))
                + "</w:tr>"
            )
        out.append(_table(trs, _TABLE_COLS))
    return "".join(out)


def render_resume_docx(doc: RenderDoc) -> bytes:
    media: list[DocxMedia] = []
    rel = None
    p = doc.profile
    if p is not None and p.photo:
        mime, data = p.photo
        ext = "png" if mime == "image/png" else "jpeg"
        rel = "rIdPhoto"
        media.append(DocxMedia(rel, f"photo.{ext}", mime, data))
    body = (
        _docx_body_table(doc, rel) if doc.template == "korean_table" else _docx_body_flow(doc, rel)
    )
    return docx_package(doc.title, body, media)


# ---------------------------------------------------------------- JSON Resume


def _ym(period: str | None) -> tuple[str | None, str | None]:
    """``2021.03 – 2023.02`` → ("2021-03", "2023-02"). 진행 중이면 끝은 None."""
    if not period:
        return None, None
    parts = [x.strip() for x in period.replace("–", "-").split("-")]

    def conv(x: str) -> str | None:
        x = x.strip()
        if len(x) >= 7 and x[:4].isdigit() and x[4] == "." and x[5:7].isdigit():
            out = f"{x[:4]}-{x[5:7]}"
            if len(x) >= 10 and x[7] == "." and x[8:10].isdigit():
                out += f"-{x[8:10]}"
            return out
        return None

    start = conv(parts[0]) if parts else None
    end = conv(parts[1]) if len(parts) > 1 else None
    if len(parts) == 1:
        end = None
    return start, end


def _dated(it: RenderItem) -> dict[str, Any]:
    start, end = _ym(it.period)
    out: dict[str, Any] = {}
    if start:
        out["startDate"] = start
    if end:
        out["endDate"] = end
    return out


def resume_json(
    doc: RenderDoc, *, version_id: str | None = None, revision: int | None = None
) -> dict:
    out: dict[str, Any] = {"$schema": JSON_RESUME_SCHEMA}
    p = doc.profile
    if p is not None:
        basics: dict[str, Any] = {"name": p.name or ""}
        label = doc.target_role or p.headline
        if label:
            basics["label"] = label
        if p.headline:
            basics["summary"] = p.headline
        if p.email:
            basics["email"] = p.email
        if p.phone:
            basics["phone"] = p.phone
        if p.links:
            basics["url"] = p.links[0][1]
            basics["profiles"] = [{"network": lab or "", "url": url} for lab, url in p.links]
        if p.address:
            basics["location"] = {"address": p.address}
        out["basics"] = basics
    for sec in doc.sections:
        if sec.kind == "work":
            out.setdefault("work", []).extend(
                {
                    "name": it.primary,
                    **({"position": it.secondary} if it.secondary else {}),
                    **_dated(it),
                    **({"highlights": list(it.lines)} if it.lines else {}),
                }
                for it in sec.items
            )
        elif sec.kind in ("project", "publications"):
            key = "projects" if sec.kind == "project" else "publications"
            for it in sec.items:
                row: dict[str, Any] = {"name": it.primary, **_dated(it)}
                if sec.kind == "project":
                    if it.secondary:
                        row["description"] = it.secondary
                    if it.lines:
                        row["highlights"] = list(it.lines)
                    if it.tags:
                        row["keywords"] = list(it.tags)
                else:
                    if it.secondary:
                        row["publisher"] = it.secondary
                    start = row.pop("startDate", None)
                    row.pop("endDate", None)
                    if start:
                        row["releaseDate"] = start
                    if it.lines:
                        row["summary"] = "\n".join(it.lines)
                out.setdefault(key, []).append(row)
        elif sec.kind == "activities":
            out.setdefault("volunteer", []).extend(
                {
                    "organization": it.primary,
                    **({"position": it.secondary} if it.secondary else {}),
                    **_dated(it),
                    **({"highlights": list(it.lines)} if it.lines else {}),
                }
                for it in sec.items
            )
        elif sec.kind == "education":
            rows = []
            for it in sec.items:
                row = {"institution": "" if doc.blind else it.primary, **_dated(it)}
                if it.secondary:
                    row["area"] = it.secondary
                if doc.blind:
                    # 블라인드: 주 칸은 학위 과정, 셋째 칸은 학점 (학교 이름은 없다)
                    row["studyType"] = it.primary
                    if it.tertiary:
                        row["score"] = it.tertiary
                elif it.tertiary:
                    row["studyType"] = it.tertiary
                if it.lines:
                    row["courses"] = list(it.lines)
                rows.append(row)
            out.setdefault("education", []).extend(rows)
        elif sec.kind == "credentials":
            for it in sec.items:
                start, _ = _ym(it.period)
                row = {"name": it.primary}
                if start:
                    row["date"] = start
                if it.secondary:
                    row["issuer"] = it.secondary
                out.setdefault("certificates", []).append(row)
        elif sec.kind == "awards":
            for it in sec.items:
                start, _ = _ym(it.period)
                row = {"title": it.primary}
                if start:
                    row["date"] = start
                if it.secondary:
                    row["awarder"] = it.secondary
                if it.lines:
                    row["summary"] = "\n".join(it.lines)
                out.setdefault("awards", []).append(row)
        elif sec.kind == "skills":
            out.setdefault("skills", []).extend({"name": it.primary} for it in sec.items)
    meta: dict[str, Any] = {"version": "v1.0.0", "x-career-brain": {"blind": doc.blind}}
    if version_id:
        meta["x-career-brain"]["version_id"] = version_id
    if revision is not None:
        meta["x-career-brain"]["revision"] = revision
    out["meta"] = meta
    return out


def render_resume_json(doc: RenderDoc, **meta: Any) -> str:
    return json.dumps(resume_json(doc, **meta), ensure_ascii=False, indent=2) + "\n"


def render_resume(fmt: str, doc: RenderDoc, **meta: Any) -> bytes:
    if fmt == "html":
        return render_resume_html(doc).encode("utf-8")
    if fmt == "md":
        return render_resume_markdown(doc).encode("utf-8")
    if fmt == "docx":
        return render_resume_docx(doc)
    if fmt == "json":
        return render_resume_json(doc, **meta).encode("utf-8")
    raise ValueError("invalid_format")


def resume_filename(title: str, fmt: str) -> str:
    from career_core.domain.document_export import export_filename

    if fmt == "json":
        return export_filename(title, "md")[: -len(".md")] + ".json"
    return export_filename(title, fmt)


__all__ = [
    "CSP",
    "PAGINATE_JS",
    "PAGINATE_SHA256",
    "RESUME_FORMATS",
    "RESUME_MEDIA_TYPES",
    "render_resume",
    "render_resume_docx",
    "render_resume_html",
    "render_resume_json",
    "render_resume_markdown",
    "resume_filename",
    "resume_json",
]

"""HTML에서 공고 본문·구조화 데이터·링크를 꺼낸다 (표준 라이브러리 html.parser만 사용).

본문 고르기: 메뉴·머리말·꼬리말·스크립트를 빼고, 글은 많고 링크 비율은 낮은 요소를 고른다.
article/main 및 id·class에 content·post·job·recruit 같은 말이 있으면 가산점.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

SKIP = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "iframe",
    "head",
    "form",
    "button",
    "select",
    "option",
    "nav",
    "header",
    "footer",
    "aside",
}
BLOCK = {
    "p",
    "div",
    "section",
    "article",
    "main",
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
    "table",
    "dd",
    "dt",
    "dl",
    "blockquote",
    "pre",
    "br",
    "hr",
    "td",
    "th",
    "figcaption",
}
VOID = {
    "br",
    "hr",
    "img",
    "input",
    "meta",
    "link",
    "source",
    "area",
    "base",
    "col",
    "embed",
    "param",
    "track",
    "wbr",
}
HINT = re.compile(r"(?i)content|article|post|entry|job|recruit|posting|detail|description|body")
NOISE_TOKENS = {
    "comment",
    "comments",
    "sidebar",
    "related",
    "share",
    "sharing",
    "breadcrumb",
    "breadcrumbs",
    "cookie",
    "popup",
    "modal",
    "advert",
    "ads",
    "widget",
    "nav",
    "navbar",
    "navigation",
    "menu",
    "gnb",
    "lnb",
    "footer",
    "header",
    "banner",
    "subscribe",
    "newsletter",
}
NEVER_NOISY = {"html", "body", "main", "article"}


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    parent: _Node | None
    children: list[_Node | str] = field(default_factory=list)
    skip: bool = False


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {}, None)
        self.cur = self.root
        self.scripts: list[tuple[dict[str, str], list[str]]] = []
        self._script: list[str] | None = None
        self.title = ""
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.resources: list[str] = []  # script·iframe의 src (채용 시스템 임베드 판별용)

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag in ("script", "iframe") and a.get("src") and len(self.resources) < 200:
            self.resources.append(a["src"][:2000])
        if tag == "meta":
            key = a.get("property") or a.get("name")
            if key and "content" in a:
                self.meta[key.lower()] = a["content"]
            return
        if tag == "script":
            self._script = []
            self.scripts.append((a, self._script))
        if tag == "title":
            self._in_title = True
        if tag in VOID:
            if tag == "br":
                self.cur.children.append("\n")
            return
        noisy = tag not in NEVER_NOISY and _noisy(a)
        node = _Node(tag, a, self.cur, skip=self.cur.skip or tag in SKIP or noisy)
        self.cur.children.append(node)
        self.cur = node

    def handle_endtag(self, tag):
        if tag == "script":
            self._script = None
        if tag == "title":
            self._in_title = False
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent  # type: ignore[assignment]
        if node is not self.root:
            self.cur = node.parent  # type: ignore[assignment]

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
            return
        if self._in_title:
            self.title += data
        self.cur.children.append(data)


def _noisy(attrs: dict[str, str]) -> bool:
    if attrs.get("role") in ("navigation", "banner", "contentinfo", "complementary"):
        return True
    tokens = set(re.split(r"[\s_\-]+", f"{attrs.get('id', '')} {attrs.get('class', '')}".lower()))
    if "elementor" in tokens:
        # Elementor(워드프레스 페이지 빌더)는 본문 글까지 모두 "elementor-widget"으로 감싼다
        tokens.discard("widget")
    return bool(tokens & NOISE_TOKENS)


DUP_MIN_CHARS = 200


def _text(node: _Node) -> str:
    """보이는 글. 반응형 페이지가 같은 본문을 PC·모바일용으로 두 번 넣으면 뒤의 사본은 뺀다."""
    out: list[str] = []

    def walk(n: _Node) -> None:
        emitted: list[str] = []  # 이 요소 아래에서 이미 낸 큰 형제 묶음의 글
        for c in n.children:
            if isinstance(c, str):
                out.append(c)
            elif not c.skip:
                total, _ = _stats(c)
                if total >= DUP_MIN_CHARS:
                    flat = re.sub(r"\s+", "", _flat(c))
                    if any(_duplicate(flat, prev) for prev in emitted):
                        continue
                    emitted.append(flat)
                if c.tag in BLOCK:
                    out.append("\n")
                walk(c)
                if c.tag in BLOCK:
                    out.append("\n")

    walk(node)
    return clean_text("".join(out))


def _flat(node: _Node) -> str:
    parts: list[str] = []

    def walk(n: _Node) -> None:
        for c in n.children:
            if isinstance(c, str):
                parts.append(c)
            elif not c.skip:
                walk(c)

    walk(node)
    return unescape("".join(parts))


def _duplicate(new: str, prev: str) -> bool:
    if new in prev:
        return True
    return prev in new and len(new) - len(prev) <= 0.1 * len(prev)


def clean_text(value: str) -> str:
    lines = []
    for line in unescape(value).replace("\u200b", "").replace("\xa0", " ").split("\n"):
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _stats(node: _Node) -> tuple[int, int]:
    """(글자 수, 링크 안 글자 수)"""
    total = linked = 0

    def walk(n: _Node, in_link: bool) -> None:
        nonlocal total, linked
        for c in n.children:
            if isinstance(c, str):
                k = len(c.strip())
                total += k
                if in_link:
                    linked += k
            elif not c.skip:
                walk(c, in_link or c.tag == "a")

    walk(node, False)
    return total, linked


def _links(node: _Node, base: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    def walk(n: _Node) -> None:
        for c in n.children:
            if isinstance(c, _Node) and not c.skip:
                if c.tag == "a" and c.attrs.get("href"):
                    out.append((urljoin(base, c.attrs["href"]), _text(c).replace("\n", " ")))
                walk(c)

    walk(node)
    return out


@dataclass(frozen=True, slots=True)
class PageData:
    title: str
    main_text: str
    json_ld: list[dict[str, Any]]
    next_data: dict[str, Any] | None
    meta: dict[str, str]
    main_links: list[tuple[str, str]]
    link_density: float
    url: str = ""
    # 인라인 스크립트의 `window.__이름 = {JSON}` 값 (예: Remix `__remixContext`)
    window_state: dict[str, Any] = field(default_factory=dict)
    resource_urls: tuple[str, ...] = ()


def _iter_ld(value: Any):
    if isinstance(value, list):
        for v in value:
            yield from _iter_ld(v)
    elif isinstance(value, dict):
        if "@graph" in value:
            yield from _iter_ld(value["@graph"])
        else:
            yield value


_WINDOW_ASSIGN = re.compile(r"^\s*window\.(__[A-Za-z_][A-Za-z0-9_]*)\s*=\s*")
MAX_WINDOW_STATE = 4 * 1024 * 1024


def _window_state(body: str) -> tuple[str, Any] | None:
    """`window.__X = {...};` 꼴의 인라인 스크립트에서 JSON 값을 꺼낸다 (실행하지 않는다)."""
    m = _WINDOW_ASSIGN.match(body)
    if not m or len(body) > MAX_WINDOW_STATE:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(body, m.end())
    except json.JSONDecodeError:
        return None
    return m.group(1), value


def html_to_text(fragment: str) -> str:
    b = _TreeBuilder()
    b.feed(fragment or "")
    b.close()
    return _text(b.root)


def extract_page(html: str, url: str) -> PageData:
    b = _TreeBuilder()
    b.feed(html)
    b.close()
    json_ld: list[dict[str, Any]] = []
    next_data = None
    window_state: dict[str, Any] = {}
    for attrs, parts in b.scripts:
        body = "".join(parts).strip()
        kind = attrs.get("type", "").lower()
        if attrs.get("id") == "__NEXT_DATA__":
            try:
                next_data = json.loads(body)
            except json.JSONDecodeError:
                next_data = None
        elif kind == "application/ld+json":
            try:
                json_ld.extend(_iter_ld(json.loads(body)))
            except json.JSONDecodeError:
                continue
        elif kind in ("", "text/javascript", "application/javascript") and not attrs.get("src"):
            found = _window_state(body)
            if found and isinstance(found[1], dict):
                window_state.setdefault(found[0], found[1])

    best, best_score = None, 0.0
    stack: list[_Node] = [b.root]
    while stack:
        n = stack.pop()
        for c in n.children:
            if isinstance(c, _Node) and not c.skip:
                stack.append(c)
        if n.skip or n is b.root:
            continue
        total, linked = _stats(n)
        if total < 80:
            continue
        density = linked / total if total else 1.0
        score = total * (1 - density)
        ident = f"{n.attrs.get('id', '')} {n.attrs.get('class', '')}"
        if n.tag in ("article", "main") or HINT.search(ident):
            score *= 1.25
        if score > best_score:
            best, best_score = n, score
    target = best or b.root
    # 큰 묶음이 이겼으면, 링크 아닌 글의 85% 이상(전체 글의 60% 이상)을 가진 자식으로 내려간다
    # (본문 아래 붙은 '함께 보면 좋은 공고' 같은 바뀌는 목록을 빼기 위해).
    while True:
        total, linked = _stats(target)
        own = total - linked
        inner = None
        for c in target.children:
            if isinstance(c, _Node) and not c.skip:
                ct, cl = _stats(c)
                if own and (ct - cl) >= 0.85 * own and ct >= 0.6 * total:
                    inner = c
                    break
        if inner is None:
            break
        target = inner
    # 가장 점수가 높은 요소가 너무 작은 조각이면(본문 일부) 바로 위 묶음까지 넓힌다.
    while target.parent is not None and target.parent is not b.root:
        parent_total, parent_linked = _stats(target.parent)
        total, _ = _stats(target)
        if parent_total and total / parent_total < 0.6 and parent_linked / parent_total < 0.3:
            target = target.parent
        else:
            break
    total, linked = _stats(target)
    return PageData(
        title=clean_text(b.meta.get("og:title") or b.title).replace("\n", " "),
        main_text=_text(target),
        json_ld=json_ld,
        next_data=next_data,
        meta=b.meta,
        main_links=_links(target, url),
        link_density=(linked / total) if total else 0.0,
        url=url,
        window_state=window_state,
        resource_urls=tuple(urljoin(url, r) for r in b.resources),
    )


def listing_candidates(page: PageData, url: str, *, limit: int = 50) -> list[tuple[str, str]]:
    """목록 페이지로 보이면 같은 사이트의 공고 후보 링크. 아니면 빈 목록."""
    host = urlsplit(url).hostname
    seen: dict[str, str] = {}
    for href, text in page.main_links:
        parts = urlsplit(href)
        if parts.hostname != host or parts.scheme not in ("http", "https"):
            continue
        if href.split("#")[0].rstrip("/") == url.rstrip("/") or len(text) < 6:
            continue
        seen.setdefault(href.split("#")[0], text[:200])

    # 경로 모양(숫자를 #로)이 같은 링크가 여러 개면 목록이다 (/archives/123, /archives/456 ...)
    def shape(href: str) -> str:
        return re.sub(r"\d+", "#", urlsplit(href).path.rstrip("/"))

    shapes: dict[str, int] = {}
    for href in seen:
        shapes[shape(href)] = shapes.get(shape(href), 0) + 1
    if not shapes:
        return []
    top = max(shapes, key=lambda k: shapes[k])
    if shapes[top] < 8 or page.link_density < 0.35:
        return []
    return [(h, t) for h, t in seen.items() if shape(h) == top][:limit]

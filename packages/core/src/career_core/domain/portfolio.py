"""포트폴리오 (O18, plan §19.6). 순수 코드: 항목 본문 JSON·수정 연산·공개 범위 규칙·링크·이미지 규칙·출력 모양.

포트폴리오 항목은 **경험 하나에 붙는다**. 본문은 "문제 → 내 역할 → 한 일 → 결과" 구성이다.

- 본문(``portfolio_item_revisions.content``)::

      {"v": 1, "problem": 글|null, "role": 글|null, "actions": [줄, …], "results": [줄, …]}

  ``problem``·``role``은 사람이 쓴 글이라 늘 "근거 없음"이다. 줄은 이력서 줄(``domain.resume``)과 같은 모양
  ``{"id", "text", "claim_id", "basis", "origin"}``이고 같은 함수로 만든다: 확인한 사실에서 고른 줄은 그 사실을
  계속 가리키고(``portfolio_line_claims``), 사실 문장이 바뀌면 "오래됨"(stale), 확인이 풀리면 "근거 사라짐"(broken).
  ``claim_id``가 없는 줄은 "근거 없음"(none)이다. 결과 줄은 그 경험(과 바로 아래 경험)의 확인한 사실에서 고른다.
- 공개 범위(항목·이미지 각각)::

      public       공개 가능  — 모든 출력
      application  지원서에만 — 공고별 묶음의 출력에만
      private      나만       — 어떤 출력에도 나가지 않는다 (편집 화면에서만 보인다)

  출력의 대상(``audience``)은 둘이다: 공고별 묶음(``posting``)은 ``application``, 전체·직무별 묶음과 묶음을 고르지
  않은 출력(Markdown 내보내기, MCP 항목 보기)은 ``public``. 이미지는 그 항목도 보일 때만 보인다
  (``image_shown``). 규칙은 ``visible_in`` 하나다.
- 이미지 바이트는 모델·MCP로 가지 않는다. 출력은 사람이 받는 파일(미리보기·인쇄 HTML·정적 HTML 묶음)뿐이다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from career_core.domain.documents import normalize_text
from career_core.domain.resume import (
    ClaimInfo,
    ResumeConflict,
    ResumeRuleError,
    _claim_line,
    _clean_id,
    _index,
    clean_optional,
    clean_title,
    content_hash,
    line_state,
    line_text,
    new_id,
)

CONTENT_VERSION = 1

VISIBILITIES = ("public", "application", "private")
AUDIENCES = ("public", "application")
BUNDLE_KINDS = ("all", "role", "posting")
# 위 묶음으로 고를 수 있는 종류 (전체 → 직무별 → 공고별). 전체는 위 묶음이 없다.
PARENT_KINDS: dict[str, tuple[str, ...]] = {
    "all": (),
    "role": ("all",),
    "posting": ("all", "role"),
}
LINK_KINDS = ("repo", "demo", "article", "video")
IMAGE_KINDS = ("screenshot", "diagram")
LINE_SECTIONS = ("actions", "results")
TEXT_FIELDS = ("problem", "role")
REVISION_ORIGINS = ("user", "agent")

MAX_TITLE = 200
MAX_TEXT = 2000
MAX_LINES = 20
MAX_TAGS = 20
MAX_TAG = 40
MAX_LINKS = 10
MAX_LINK_URL = 500
MAX_LINK_LABEL = 80
MAX_OPERATIONS = 100
MAX_CAPTION = 200
MAX_INTRO = 1000
MAX_BUNDLE_ITEMS = 50
MAX_IMAGES_PER_ITEM = 12
# 브라우저가 줄이는 한도와 같다 (apps/personal-web/src/lib/imageShrink.ts). 서버가 다시 확인한다.
MAX_EDGE = 1600
MIN_EDGE = 16
MAX_IMAGE_BYTES = 1024 * 1024

# 결과 줄로 고르기 좋은 사실 종류 (화면 순서). 다른 종류도 고를 수 있다.
RESULT_CLAIM_KINDS = ("result", "decision")
ACTION_CLAIM_KINDS = ("action", "responsibility")


class PortfolioRuleError(ResumeRuleError):
    """규칙 위반 (HTTP 422, 일부 409). ``code``만 응답에 나간다.

    ``reset_at``: GitHub 한도(``rate_limited``)가 풀리는 시각 (알 때만)."""

    reset_at: datetime | None = None

    def __init__(self, code: str, *, reset_at: datetime | None = None) -> None:
        super().__init__(code)
        self.reset_at = reset_at


class PortfolioConflict(PortfolioRuleError, ResumeConflict):
    """base_revision이 최신이 아니다 (HTTP 409)."""

    def __init__(self, current: int) -> None:
        ResumeConflict.__init__(self, current)


# ---------------------------------------------------------------- 공개 범위


def check_visibility(value: Any) -> str:
    if value not in VISIBILITIES:
        raise PortfolioRuleError("invalid_visibility")
    return value


def bundle_audience(kind: str) -> str:
    """묶음의 출력 대상: 공고별 묶음만 ``application``(지원서), 나머지는 ``public``."""
    return "application" if kind == "posting" else "public"


def visible_in(visibility: str, audience: str) -> bool:
    """공개 범위 하나가 출력 대상에 보이나. 나만(private)은 어떤 출력에도, 지원서에만은 지원서 출력에만."""
    if visibility == "public":
        return True
    if visibility == "application":
        return audience == "application"
    return False


def image_shown(item_visibility: str, image_visibility: str, audience: str) -> bool:
    return visible_in(item_visibility, audience) and visible_in(image_visibility, audience)


# ---------------------------------------------------------------- 설정 칸 (태그·링크)


def clean_tags(values: Any) -> list[str]:
    if not isinstance(values, list):
        raise PortfolioRuleError("invalid_tags")
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        if not isinstance(v, str):
            raise PortfolioRuleError("invalid_tags")
        t = " ".join(normalize_text(v).split())
        if not t:
            continue
        if len(t) > MAX_TAG:
            raise PortfolioRuleError("tag_too_long")
        if t.casefold() in seen:
            continue
        seen.add(t.casefold())
        out.append(t)
    if len(out) > MAX_TAGS:
        raise PortfolioRuleError("too_many_tags")
    return out


_HOST = re.compile(r"^[a-z0-9.-]+$", re.IGNORECASE)


def clean_url(value: Any) -> str:
    """http(s) 주소만. 계정 정보·공백·제어 문자 거부. 호스트가 있어야 한다."""
    if not isinstance(value, str):
        raise PortfolioRuleError("invalid_link")
    u = value.strip()
    if not u or len(u) > MAX_LINK_URL or any(c.isspace() or ord(c) < 32 for c in u):
        raise PortfolioRuleError("invalid_link")
    try:
        parts = urlsplit(u)
        _ = parts.port
    except ValueError as exc:
        raise PortfolioRuleError("invalid_link") from exc
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise PortfolioRuleError("invalid_link")
    if parts.username or parts.password or not _HOST.match(parts.hostname):
        raise PortfolioRuleError("invalid_link")
    if "." not in parts.hostname:
        raise PortfolioRuleError("invalid_link")
    return u


def clean_links(values: Any) -> list[dict[str, Any]]:
    """``[{id?, kind, url, label?, info?}]`` → 검사한 목록. ``info``(GitHub에서 읽은 정보)는 서버가 채운 값만 남긴다."""
    if not isinstance(values, list):
        raise PortfolioRuleError("invalid_link")
    if len(values) > MAX_LINKS:
        raise PortfolioRuleError("too_many_links")
    out: list[dict[str, Any]] = []
    for v in values:
        if not isinstance(v, Mapping):
            raise PortfolioRuleError("invalid_link")
        kind = v.get("kind")
        if kind not in LINK_KINDS:
            raise PortfolioRuleError("invalid_link_kind")
        link: dict[str, Any] = {
            "id": _clean_id(v.get("id"), "k"),
            "kind": kind,
            "url": clean_url(v.get("url")),
            "label": clean_optional(v.get("label"), MAX_LINK_LABEL, "invalid_link"),
        }
        info = v.get("info")
        if isinstance(info, Mapping):
            link["info"] = clean_repo_info(info)
        out.append(link)
    return out


REPO_INFO_KEYS = (
    "full_name",
    "language",
    "created_at",
    "pushed_at",
    "private",
    "stars",
    "forks",
    "default_branch",
    "description",
)


def clean_repo_info(info: Mapping[str, Any]) -> dict[str, Any]:
    """저장소 정보(참고용). 숫자(별·포크)는 본인 기여의 근거가 아니다 — 화면도 '참고 정보'로만 보인다."""
    out: dict[str, Any] = {}
    for k in REPO_INFO_KEYS:
        v = info.get(k)
        if v is None:
            continue
        if k in ("stars", "forks"):
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                out[k] = v
        elif k == "private":
            out[k] = bool(v)
        elif isinstance(v, str):
            out[k] = v[:300]
    return out


def clean_caption(value: Any) -> str | None:
    return clean_optional(value, MAX_CAPTION, "invalid_caption")


def check_image_kind(value: Any) -> str:
    if value not in IMAGE_KINDS:
        raise PortfolioRuleError("invalid_image_kind")
    return value


def check_image_size(width: int, height: int, size: int) -> None:
    """브라우저에서 줄인 결과의 한도 (서버가 다시 본다). 넘으면 거부한다 — 서버가 다시 줄이지 않는다."""
    if size > MAX_IMAGE_BYTES:
        raise PortfolioRuleError("image_too_large")
    if not (MIN_EDGE <= width and MIN_EDGE <= height):
        raise PortfolioRuleError("invalid_dimensions")
    if max(width, height) > MAX_EDGE:
        raise PortfolioRuleError("image_too_big")


# ---------------------------------------------------------------- 본문


def empty_content() -> dict[str, Any]:
    return {"v": CONTENT_VERSION, "problem": None, "role": None, "actions": [], "results": []}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PortfolioRuleError("invalid_text")
    # 문단 줄바꿈은 남긴다. 줄마다 공백을 고르게.
    lines = [" ".join(ln.split()) for ln in normalize_text(value).replace("\r\n", "\n").split("\n")]
    text = "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) > MAX_TEXT:
        raise PortfolioRuleError("text_too_long")
    return text or None


def iter_lines(content: Mapping[str, Any]):
    for sec in LINE_SECTIONS:
        for ln in content.get(sec) or []:
            yield sec, ln


def check_content(content: Any) -> dict[str, Any]:
    if not isinstance(content, dict) or content.get("v") != CONTENT_VERSION:
        raise PortfolioRuleError("invalid_content")
    if set(content) != {"v", *TEXT_FIELDS, *LINE_SECTIONS}:
        raise PortfolioRuleError("invalid_content")
    for f in TEXT_FIELDS:
        v = content[f]
        if v is not None and (not isinstance(v, str) or len(v) > MAX_TEXT):
            raise PortfolioRuleError("invalid_content")
    ids: set[str] = set()
    for sec in LINE_SECTIONS:
        lines = content[sec]
        if not isinstance(lines, list):
            raise PortfolioRuleError("invalid_content")
        if len(lines) > MAX_LINES:
            raise PortfolioRuleError("too_many_lines")
        for ln in lines:
            if not isinstance(ln, dict) or ln.get("id") in ids:
                raise PortfolioRuleError("invalid_content")
            if ln.get("origin") not in ("claim", "user", "ai"):
                raise PortfolioRuleError("invalid_content")
            ids.add(ln["id"])
    return content


@dataclass(slots=True)
class Lookup:
    """연산이 쓰는 브레인 값: 주장 ID → 정보, 그 항목의 경험과 바로 아래 경험 ID들."""

    claims: dict[str, ClaimInfo] = field(default_factory=dict)
    family: set[str] = field(default_factory=set)


def _check_claim(lookup: Lookup, claim_id: Any) -> ClaimInfo:
    from uuid import UUID

    try:
        cid = str(UUID(str(claim_id)))
    except ValueError as exc:
        raise PortfolioRuleError("unknown_claim") from exc
    info = lookup.claims.get(cid)
    if info is None:
        raise PortfolioRuleError("unknown_claim")
    if not info.confirmed:
        raise PortfolioRuleError("claim_not_confirmed")
    if info.experience_id not in lookup.family:
        raise PortfolioRuleError("claim_not_in_experience")
    return info


def _find_line(content: dict[str, Any], line_id: Any) -> tuple[str, dict[str, Any]]:
    for sec, ln in iter_lines(content):
        if ln["id"] == line_id:
            return sec, ln
    raise PortfolioRuleError("unknown_line")


def _section(value: Any) -> str:
    if value not in LINE_SECTIONS:
        raise PortfolioRuleError("unknown_section")
    return value


def apply_operations(
    content: Mapping[str, Any],
    operations: Sequence[Mapping[str, Any]],
    lookup: Lookup,
    *,
    line_origin: str = "user",
) -> dict[str, Any]:
    """연산을 차례로 적용한 새 본문 (원본은 그대로). 이력서 줄 연산과 같은 이름·같은 규칙.

    - ``set_text {field: problem|role, text: 글|null}``: 사람이 쓴 글 (근거 없음)
    - ``add_line {section: actions|results, claim_id?, text?, index?, line_id?}``: claim_id면 그 확인한 사실 문장
      (글을 주면 그 글, 근거는 유지). 없으면 직접 쓴 줄(근거 없음)
    - ``update_line {line_id, text}`` · ``remove_line {line_id}`` · ``move_line {line_id, index, section?}``
    - ``refresh_line {line_id}``: 근거 사실의 지금 문장으로 (확인한 사실일 때만)
    """
    if not operations:
        raise PortfolioRuleError("no_operations")
    if len(operations) > MAX_OPERATIONS:
        raise PortfolioRuleError("too_many_operations")
    out: dict[str, Any] = json.loads(json.dumps(content))
    try:
        for op in operations:
            if not isinstance(op, Mapping):
                raise PortfolioRuleError("unknown_operation")
            kind = op.get("op")
            if kind == "set_text":
                f = op.get("field")
                if f not in TEXT_FIELDS:
                    raise PortfolioRuleError("unknown_field")
                out[f] = _text(op.get("text"))
            elif kind == "add_line":
                sec = _section(op.get("section"))
                if len(out[sec]) >= MAX_LINES:
                    raise PortfolioRuleError("too_many_lines")
                if op.get("claim_id") is not None:
                    info = _check_claim(lookup, op["claim_id"])
                    if any(ln.get("claim_id") == info.id for _, ln in iter_lines(out)):
                        raise PortfolioRuleError("claim_already_used")
                    line = _claim_line(info, op.get("line_id"))
                    if op.get("text") is not None:
                        text = line_text(op["text"])
                        if text != line["text"]:
                            line = {**line, "text": text, "origin": line_origin}
                else:
                    line = {
                        "id": _clean_id(op.get("line_id"), "l"),
                        "text": line_text(op.get("text")),
                        "claim_id": None,
                        "basis": None,
                        "origin": line_origin,
                    }
                out[sec].insert(_index(op.get("index"), len(out[sec])), line)
            elif kind == "update_line":
                _, line = _find_line(out, op.get("line_id"))
                text = line_text(op.get("text"))
                if text != line["text"]:
                    line["text"] = text
                    line["origin"] = line_origin
            elif kind == "remove_line":
                sec, line = _find_line(out, op.get("line_id"))
                out[sec].remove(line)
            elif kind == "move_line":
                sec, line = _find_line(out, op.get("line_id"))
                target = _section(op["section"]) if op.get("section") else sec
                out[sec].remove(line)
                if len(out[target]) >= MAX_LINES:
                    raise PortfolioRuleError("too_many_lines")
                out[target].insert(_index(op.get("index"), len(out[target])), line)
            elif kind == "refresh_line":
                _, line = _find_line(out, op.get("line_id"))
                if not line.get("claim_id"):
                    raise PortfolioRuleError("no_evidence")
                info = lookup.claims.get(line["claim_id"])
                if info is None or not info.confirmed:
                    raise PortfolioRuleError("claim_not_confirmed")
                line.update(text=line_text(info.statement), basis=info.basis, origin="claim")
            else:
                raise PortfolioRuleError("unknown_operation")
    except PortfolioRuleError:
        raise
    except ResumeRuleError as exc:  # 이력서 함수(줄 글·ID·위치 검사)가 낸 규칙 오류
        raise PortfolioRuleError(exc.code) from exc
    return check_content(out)


def operation_claims(
    content: Mapping[str, Any], operations: Sequence[Mapping[str, Any]]
) -> set[str]:
    out = {ln["claim_id"] for _, ln in iter_lines(content) if ln.get("claim_id")}
    for op in operations:
        if isinstance(op, Mapping) and op.get("claim_id"):
            out.add(str(op["claim_id"]))
    return out


def annotate(content: Mapping[str, Any], claims: Mapping[str, ClaimInfo]) -> dict[str, Any]:
    """읽을 때 줄마다 상태(none·ok·stale·broken)를 붙인 복사본."""
    out = json.loads(json.dumps(content))
    for _, ln in iter_lines(out):
        ln["state"] = line_state(ln, claims.get(ln.get("claim_id") or ""))
    return out


def line_counts(content: Mapping[str, Any], claims: Mapping[str, ClaimInfo]) -> dict[str, int]:
    out = {"none": 0, "ok": 0, "stale": 0, "broken": 0}
    for _, ln in iter_lines(content):
        out[line_state(ln, claims.get(ln.get("claim_id") or ""))] += 1
    return out


# ---------------------------------------------------------------- 출력 모양


@dataclass(frozen=True, slots=True)
class RenderImage:
    """출력 이미지 한 장. ``name``은 정적 묶음 안의 파일 이름 (``images/…``)."""

    name: str
    mime: str
    data: bytes
    width: int
    height: int
    kind: str
    caption: str | None = None


@dataclass(frozen=True, slots=True)
class RenderLink:
    kind: str
    url: str
    label: str | None = None


@dataclass(frozen=True, slots=True)
class RenderPortfolioItem:
    anchor: str  # 문서 안의 id (item-1, item-2 …)
    title: str
    experience_id: str
    meta: str | None  # 조직 · 역할 · 기간
    tags: tuple[str, ...] = ()
    links: tuple[RenderLink, ...] = ()
    problem: str | None = None
    role: str | None = None
    actions: tuple[str, ...] = ()
    results: tuple[str, ...] = ()
    images: tuple[RenderImage, ...] = ()


@dataclass(frozen=True, slots=True)
class RenderPortfolio:
    title: str
    audience: str
    owner_name: str | None = None
    headline: str | None = None
    owner_links: tuple[tuple[str | None, str], ...] = ()
    subtitle: str | None = None  # 목표 직무·지원
    intro: str | None = None
    items: tuple[RenderPortfolioItem, ...] = ()
    # 함께 내보낸 이력서 파일 (정적 묶음에서만, 상대 경로)
    resume_href: str | None = None


LINK_LABEL = {"repo": "저장소", "demo": "데모", "article": "글", "video": "영상"}
IMAGE_KIND_LABEL = {"screenshot": "스크린샷", "diagram": "구성도"}
VISIBILITY_LABEL = {"public": "공개 가능", "application": "지원서에만", "private": "나만"}


def image_ext(mime: str) -> str:
    return "png" if mime == "image/png" else "jpg"


# 쪽 수 어림 (항목 하나가 A4 한두 쪽 안에 드는지). 화면은 미리보기에서 실제로 잰다.
_CHARS_PER_LINE = 56
_LINES_PER_PAGE = 44
_IMAGE_ROWS = 18  # 이미지 한 장 ≈ 쪽의 0.4


def estimate_item_pages(item: RenderPortfolioItem) -> int:
    rows = 6.0 + (1 if item.tags else 0) + len(item.links)
    for text in (item.problem, item.role):
        if text:
            rows += 2 + sum(max(1, -(-len(p) // _CHARS_PER_LINE)) for p in text.split("\n"))
    for lines in (item.actions, item.results):
        if lines:
            rows += 2 + sum(max(1, -(-len(x) // _CHARS_PER_LINE)) for x in lines)
    for img in item.images:
        rows += min(_IMAGE_ROWS, max(6, int(_IMAGE_ROWS * img.height / max(img.width, 1)))) + 1
    return max(1, -(-int(rows) // _LINES_PER_PAGE))


__all__ = [
    "AUDIENCES",
    "BUNDLE_KINDS",
    "CONTENT_VERSION",
    "IMAGE_KINDS",
    "LINE_SECTIONS",
    "LINK_KINDS",
    "MAX_EDGE",
    "MAX_IMAGE_BYTES",
    "PARENT_KINDS",
    "VISIBILITIES",
    "Lookup",
    "PortfolioConflict",
    "PortfolioRuleError",
    "RenderImage",
    "RenderLink",
    "RenderPortfolio",
    "RenderPortfolioItem",
    "annotate",
    "apply_operations",
    "bundle_audience",
    "check_content",
    "clean_links",
    "clean_tags",
    "clean_title",
    "content_hash",
    "empty_content",
    "image_shown",
    "new_id",
    "visible_in",
]

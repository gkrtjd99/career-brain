"""채용 공고 구조화 데이터 어댑터 (schema.org JobPosting, 채용 시스템별 내장 JSON·공개 API).

규칙
- 페이지 어댑터: 가져온 HTML 안의 JSON만 읽는다 (JSON-LD, Next.js `__NEXT_DATA__`, Remix `__remixContext`).
- API 어댑터: 공고 URL에서 공개 JSON 주소를 만든다. 호출은 usecase가 같은 `HttpFetcher`로 하고,
  주소는 `ats_request_allowed`(같은 호스트 또는 문서화한 ATS API 호스트)를 통과해야 한다.
- 모든 값의 근거는 `structured` 경로다 (예: `greenhouse:jobPost.title`). 본문 섹션에서 고른 목록은
  `<경로> § <섹션 제목>`. 원문에 없는 값은 만들지 않는다 (마감이 비어 있으면 비워 둔다).
- 판별은 호스트 이름이 아니라 데이터 모양으로 한다 (자체 도메인을 쓰는 채용 페이지도 있다).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlsplit

from career_core.adapters.html_extract import PageData, html_to_text
from career_core.domain.postings import (
    MAX_CHOICE_OPTIONS,
    FieldName,
    classify_heading,
    clean_question,
    is_heading_line,
)

MAX_LIST_ITEMS = 40

# --- 공통 -------------------------------------------------------------------------


def ev_structured(path: str) -> dict[str, str]:
    return {"kind": "structured", "ref": path}


def put(out: dict, name: str, value: Any, evidence: dict) -> None:
    if value in (None, "", [], {}):
        return
    if isinstance(value, str):
        value = " ".join(value.split())[:1000]
        if not value:
            return
    out[name] = {"value": value, "evidence": evidence}


_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def iso(value: Any) -> str | None:
    """ISO 날짜/시각. 날짜만 있으면 날짜만 둔다 (자정으로 바꾸면 마감 당일이 빠진다)."""
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip()
    if _DATE_ONLY.match(v):
        try:
            return date.fromisoformat(v).isoformat()
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(v).isoformat()
    except ValueError:
        head = v[:10]
        if _DATE_ONLY.match(head):
            try:
                return date.fromisoformat(head).isoformat()
            except ValueError:
                return None
        return None


def name_of(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("name") if isinstance(value.get("name"), str) else None
    return value if isinstance(value, str) else None


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _join_unique(values: list[Any]) -> str:
    seen: list[str] = []
    for v in values:
        if isinstance(v, str) and v.strip() and v.strip() not in seen:
            seen.append(v.strip())
    return ", ".join(seen)


# --- 본문 섹션 → 목록 -----------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head", "button", "form"}
_LINE_TAGS = {
    "p",
    "div",
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
    "td",
    "th",
    "table",
    "section",
    "article",
    "dd",
    "dt",
    "dl",
    "blockquote",
    "pre",
    "br",
    "hr",
}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_BOLD_TAGS = {"strong", "b"}
_BULLET = re.compile(r"^[\s•·∙‧\-–—*▪◦●○■□✓✔➤▶▷>]+")


BOLD_LEVEL = 7  # 굵은 글씨 제목의 단계 (h1~h6 다음)
PLAIN_LEVEL = 8  # "자격요건"·"[자격 요건]"처럼 겉모양으로만 본 제목


class _Lines(HTMLParser):
    """블록마다 한 줄과 제목 단계. h 태그 안의 줄은 1~6, 글 전체가 굵은 짧은 줄은 7, 그 밖은 0."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[tuple[str, int, bool]] = []  # (글, 제목 단계, 목록 항목 안인지)
        self._buf: list[str] = []
        self._bold: list[str] = []
        self._in_h = 0
        self._hs: list[int] = []
        self._b = self._skip = self._li = 0

    def _flush(self) -> None:
        text = " ".join("".join(self._buf).split())
        bold = " ".join("".join(self._bold).split())
        if text:
            level = 0
            if len(text) <= 80:
                if self._in_h:
                    level = self._in_h
                elif bold == text and not self._li:  # 목록 항목 안의 굵은 글씨는 강조일 뿐이다
                    level = BOLD_LEVEL
            self.lines.append((text, level, self._li > 0))
        self._buf, self._bold, self._in_h = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if tag in _LINE_TAGS:
            self._flush()
        if tag in _HEADING_TAGS:
            self._hs.append(int(tag[1]))
        elif tag in _BOLD_TAGS:
            self._b += 1
        elif tag == "li":
            self._li += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag in _LINE_TAGS:
            self._flush()
        if tag in _HEADING_TAGS:
            if self._hs:
                self._hs.pop()
        elif tag in _BOLD_TAGS:
            self._b = max(0, self._b - 1)
        elif tag == "li":
            self._li = max(0, self._li - 1)

    def handle_data(self, data):
        if self._skip:
            return
        self._buf.append(data)
        if self._b or self._hs:
            self._bold.append(data)
        if self._hs and data.strip():
            self._in_h = self._hs[-1]

    def close(self):
        super().close()
        self._flush()


def html_lines(fragment: str) -> list[tuple[str, int, bool]]:
    p = _Lines()
    p.feed(fragment or "")
    p.close()
    return p.lines


def _item(text: str) -> str:
    return _BULLET.sub("", text).strip()[:1000]


def sections_from_html(fragment: str, path: str) -> dict:
    """본문 HTML의 섹션 제목(담당 업무·자격 요건·우대 사항·전형 절차) 아래 줄을 목록으로 고른다.

    제목은 h 태그·굵은 글씨(느슨한 단서)이거나 "자격요건"처럼 제목 낱말과 같은 줄이다.
    분류하지 못한 다른 제목("회사 소개", "혜택")을 만나면 그 섹션은 끝난다.
    """
    found: dict[FieldName, tuple[list[str], list[str]]] = {}
    current: FieldName | None = None
    current_level = 0
    in_list = (
        False  # 이 섹션의 항목이 <li>였는지: 목록 뒤의 일반 문단(복지·안내 문구)은 섹션 밖이다
    )
    for text, level, li in html_lines(fragment):
        if not level and is_heading_line(text):
            level = PLAIN_LEVEL
        if level:
            name = classify_heading(text, certain=level < PLAIN_LEVEL) or classify_heading(text)
            if name is not None:
                current, current_level, in_list = name, level, False
                found.setdefault(name, ([], []))[1].append(text)
            elif current is not None and level > current_level:
                pass  # 더 작은 제목("자격 요건" 아래 "[경험]")은 같은 섹션의 소제목
            else:
                current = None
            continue
        if current is not None:
            items = found[current][0]
            if li:
                in_list = True
            elif in_list:
                current = None
                continue
            item = _item(text)
            if item and item not in items and len(items) < MAX_LIST_ITEMS:
                items.append(item)
    out: dict = {}
    for name, (items, headings) in found.items():
        if items:
            put(out, name, items, ev_structured(f"{path} § {' · '.join(headings)}"))
    return out


# --- schema.org JobPosting (JSON-LD) ----------------------------------------------------


def _address_text(addr: dict) -> str:
    """지역·시군구·도로명을 잇되, 앞 값이 뒤 값에 이미 들어 있으면 뺀다 (원티드 등: 도로명에 지역 포함)."""
    parts = [
        str(addr.get(k)).strip()
        for k in ("addressRegion", "addressLocality", "streetAddress")
        if isinstance(addr.get(k), (str, int)) and str(addr.get(k)).strip()
    ]
    if not parts and isinstance(addr.get("addressCountry"), str):
        parts = [addr["addressCountry"].strip()]
    kept: list[str] = []
    for i, p in enumerate(parts):
        if any(p in later for later in parts[i + 1 :]) or p in kept:
            continue
        kept.append(p)
    return " ".join(kept)


def from_json_ld(page: PageData) -> tuple[dict, str | None]:
    """schema.org JobPosting. (항목, 본문 텍스트)"""
    for d in page.json_ld:
        types = d.get("@type")
        types = types if isinstance(types, list) else [types]
        if "JobPosting" not in types:
            continue
        out: dict = {}
        p = "ld:JobPosting"
        put(out, FieldName.TITLE, d.get("title"), ev_structured(f"{p}.title"))
        put(
            out,
            FieldName.COMPANY,
            name_of(d.get("hiringOrganization")),
            ev_structured(f"{p}.hiringOrganization.name"),
        )
        locs = d.get("jobLocation")
        locs = locs if isinstance(locs, list) else [locs] if locs else []
        names = []
        for loc in locs:
            addr = loc.get("address") if isinstance(loc, dict) else None
            if isinstance(addr, dict):
                names.append(_address_text(addr))
            elif isinstance(addr, str):
                names.append(addr)
        put(
            out,
            FieldName.LOCATION,
            _join_unique(names),
            ev_structured(f"{p}.jobLocation.address"),
        )
        et = d.get("employmentType")
        put(
            out,
            FieldName.EMPLOYMENT_TYPE,
            ", ".join(x for x in et if isinstance(x, str)) if isinstance(et, list) else et,
            ev_structured(f"{p}.employmentType"),
        )
        # experienceRequirements: 글(또는 글 목록)일 때만. 개월 수 객체는 해석하지 않는다.
        exp = d.get("experienceRequirements")
        exp = _join_unique(exp) if isinstance(exp, list) else exp if isinstance(exp, str) else None
        put(out, FieldName.CAREER_LEVEL, exp, ev_structured(f"{p}.experienceRequirements"))
        put(out, FieldName.DEADLINE, iso(d.get("validThrough")), ev_structured(f"{p}.validThrough"))
        put(out, FieldName.POSTED_AT, iso(d.get("datePosted")), ev_structured(f"{p}.datePosted"))
        desc = d.get("description")
        return out, html_to_text(desc) if isinstance(desc, str) else None
    return {}, None


# --- 나인하이어 (Next.js `pageProps.recruitment`) ----------------------------------------

NINEHIRE_EMPLOYMENT = {
    "full_time": "정규직",
    "contract": "계약직",
    "intern": "인턴",
    "part_time": "파트타임",
    "freelance": "프리랜서",
}
NINEHIRE_CAREER = {
    "newcomer": "신입",
    "experienced": "경력",
    "irrelevant": "경력 무관",
    "any": "경력 무관",
}
ALWAYS_OPEN = {"always", "until_hired", "no_deadline", "rolling", "anytime"}


def from_ninehire(page: PageData) -> tuple[dict, str | None]:
    """나인하이어 채용 페이지 (Next.js `pageProps.recruitment`)."""
    props = _dict(_dict(_dict(page.next_data).get("props")).get("pageProps"))
    r = props.get("recruitment")
    if not isinstance(r, dict) or "recruitmentId" not in r:
        return {}, None
    p = "ninehire:recruitment"
    out: dict = {}
    put(
        out,
        FieldName.TITLE,
        r.get("externalTitle") or r.get("title"),
        ev_structured(f"{p}.externalTitle"),
    )
    company = _dict(r.get("affiliation")).get("title") or r.get("companyName")
    put(out, FieldName.COMPANY, company, ev_structured(f"{p}.affiliation.title"))
    locs = [
        loc.get("placeName") or loc.get("addressName")
        for loc in _list(r.get("jobLocations"))
        if isinstance(loc, dict)
    ]
    put(out, FieldName.LOCATION, _join_unique(locs), ev_structured(f"{p}.jobLocations"))
    et = r.get("employmentType") or []
    et = et if isinstance(et, list) else [et]
    put(
        out,
        FieldName.EMPLOYMENT_TYPE,
        ", ".join(NINEHIRE_EMPLOYMENT.get(x, x) for x in et if isinstance(x, str)),
        ev_structured(f"{p}.employmentType"),
    )
    career = _dict(r.get("career")).get("type")
    put(
        out,
        FieldName.CAREER_LEVEL,
        NINEHIRE_CAREER.get(career, career) if isinstance(career, str) else None,
        ev_structured(f"{p}.career.type"),
    )
    reception = _dict(r.get("reception"))
    if str(r.get("deadlineType", "")).lower() in ALWAYS_OPEN:
        put(out, FieldName.DEADLINE, "always", ev_structured(f"{p}.deadlineType"))
    else:
        put(
            out,
            FieldName.DEADLINE,
            iso(reception.get("endAt") or r.get("endAt")),
            ev_structured(f"{p}.reception.endAt"),
        )
    put(
        out,
        FieldName.POSTED_AT,
        iso(reception.get("startAt") or r.get("createdAt")),
        ev_structured(f"{p}.reception.startAt"),
    )
    # 접수 시작: 접수 기간의 시작이 적혀 있을 때만 (createdAt은 접수 시작이 아니다)
    put(
        out,
        FieldName.OPENS_AT,
        iso(reception.get("startAt")),
        ev_structured(f"{p}.reception.startAt"),
    )
    content = _dict(props.get("jobPosting")).get("content")
    if not isinstance(content, str):
        return out, None
    for k, v in sections_from_html(content, "ninehire:jobPosting.content").items():
        out.setdefault(k, v)
    return out, html_to_text(content)


# --- 그리팅 (greetinghr, Next.js + React Query `getOpeningById`) ---------------------------

GREETING_EMPLOYMENT = {
    "FULL_TIME_WORKER": "정규직",
    "CONTRACT_WORKER": "계약직",
    "INTERN_WORKER": "인턴",
    "PART_TIME_WORKER": "파트타임",
    "FREELANCER": "프리랜서",
    "DISPATCHED_WORKER": "파견직",
}


def _greeting_career(c: dict) -> str | None:
    kind = c.get("careerType")
    lo, hi = c.get("careerFrom"), c.get("careerTo")
    if kind == "NEW_COMER":
        return "신입"
    if kind == "NOT_MATTER":
        return "경력 무관"
    if kind == "EXPERIENCED":
        if isinstance(lo, int) and isinstance(hi, int) and lo and hi:
            return f"경력 {lo}~{hi}년"
        if isinstance(lo, int) and lo:
            return f"경력 {lo}년 이상"
        return "경력"
    return kind if isinstance(kind, str) else None


# `questionnairesInfo[].type` (2026-11-17 실제 공개 페이지 3곳·39건에서 확인한 값):
# 100 = 서술형(글자 수 `maxLength`), 200·400 = 선택형(`optionItems`, 유입 경로 등). 서술형은 자기소개서
# 문항(QUESTIONS)으로, 선택형은 c0025부터 별도 항목(APPLICATION_QUESTIONS)으로 둔다.
# 200과 400 중 어느 쪽이 하나만/여러 개 고르기인지는 확인하지 못했다: 원래 번호(`source_type`)만 남긴다.
GREETING_FREE_TEXT = 100
GREETING_CHOICE_TYPES = frozenset({200, 400})
MAX_QUESTIONS = 20


def _greeting_questions(data: dict) -> tuple[list[dict], list[int]]:
    """자기소개서 문항 (서술형만). (문항 [{prompt, char_limit}], 원래 순번)."""
    out: list[dict] = []
    index: list[int] = []
    for i, raw in enumerate(_list(data.get("questionnairesInfo"))):
        q = _dict(raw)
        prompt = q.get("question")
        if q.get("type") != GREETING_FREE_TEXT or not isinstance(prompt, str):
            continue
        prompt, paren = clean_question(prompt[:1000])
        if not prompt:
            continue
        limit = q.get("maxLength")
        ok = isinstance(limit, int) and not isinstance(limit, bool) and 0 < limit <= 100_000
        out.append({"prompt": prompt, "char_limit": limit if ok else paren})
        index.append(i)
        if len(out) >= MAX_QUESTIONS:
            break
    return out, index


def _option_text(raw: Any) -> str | None:
    """보기 하나: 문자열 또는 ``{"item": 글}`` (실제 페이지에서 본 모양). 그 밖의 모양은 버린다."""
    value = raw if isinstance(raw, str) else _dict(raw).get("item")
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())[:200]
    return value or None


def _greeting_choices(data: dict) -> tuple[list[dict], list[int]]:
    """선택형 문항 (type 200·400, 보기 있음). (문항 [{prompt, required, options, source_type}], 원래 순번).

    보기가 하나도 없거나 질문이 비면 건너뛴다. 보기는 ``optionItems``(없으면 ``items``)의 글만, 50개 상한.
    """
    out: list[dict] = []
    index: list[int] = []
    for i, raw in enumerate(_list(data.get("questionnairesInfo"))):
        q = _dict(raw)
        kind, prompt = q.get("type"), q.get("question")
        if kind not in GREETING_CHOICE_TYPES or isinstance(kind, bool):
            continue
        if not isinstance(prompt, str) or not " ".join(prompt.split()):
            continue
        raw_options = _list(q.get("optionItems")) or _list(q.get("items"))
        options = [o for o in (_option_text(x) for x in raw_options) if o][:MAX_CHOICE_OPTIONS]
        if not options:
            continue
        need = q.get("need")
        out.append(
            {
                "prompt": " ".join(prompt.split())[:1000],
                "required": need if isinstance(need, bool) else None,
                "options": options,
                "source_type": kind,
            }
        )
        index.append(i)
        if len(out) >= MAX_QUESTIONS:
            break
    return out, index


def _greeting_opening(page: PageData) -> dict | None:
    props = _dict(_dict(_dict(page.next_data).get("props")).get("pageProps"))
    for q in _list(_dict(props.get("dehydratedState")).get("queries")):
        key = _dict(q).get("queryKey")
        if not isinstance(key, list) or "getOpeningById" not in key:
            continue
        data = _dict(_dict(q.get("state")).get("data"))
        data = _dict(data.get("data")) if "openingsInfo" not in data else data
        if isinstance(data.get("openingsInfo"), dict):
            return data
    return None


def from_greeting(page: PageData) -> tuple[dict, str | None]:
    """그리팅 채용 페이지 (`*.career.greetinghr.com` 또는 그리팅을 쓰는 자체 도메인)."""
    data = _greeting_opening(page)
    if data is None:
        return {}, None
    info = data["openingsInfo"]
    if not isinstance(info.get("title"), str):
        return {}, None
    p = "greeting:opening"
    out: dict = {}
    put(out, FieldName.TITLE, info.get("title"), ev_structured(f"{p}.openingsInfo.title"))
    put(
        out,
        FieldName.COMPANY,
        _dict(data.get("groupInfo")).get("name"),
        ev_structured(f"{p}.groupInfo.name"),
    )
    # dueDate가 없으면 화면에도 마감이 나오지 않는다: 상시로 단정하지 않고 비워 둔다.
    put(
        out,
        FieldName.DEADLINE,
        iso(info.get("dueDate")),
        ev_structured(f"{p}.openingsInfo.dueDate"),
    )
    put(
        out,
        FieldName.POSTED_AT,
        iso(info.get("openDate")),
        ev_structured(f"{p}.openingsInfo.openDate"),
    )
    # 그리팅 화면의 접수 기간은 openDate ~ dueDate다: 접수 시작으로도 쓴다
    put(
        out,
        FieldName.OPENS_AT,
        iso(info.get("openDate")),
        ev_structured(f"{p}.openingsInfo.openDate"),
    )
    positions = [_dict(x) for x in _list(_dict(data.get("jobPositionSetting")).get("jobPositions"))]
    places = [
        _dict(x.get("workspacePlace")).get("place")
        or _dict(x.get("workspacePlace")).get("location")
        for x in positions
    ]
    path = f"{p}.jobPositionSetting.jobPositions"
    put(out, FieldName.LOCATION, _join_unique(places), ev_structured(f"{path}.workspacePlace"))
    kinds = [_dict(x.get("jobPositionEmployment")).get("employmentType") for x in positions]
    put(
        out,
        FieldName.EMPLOYMENT_TYPE,
        _join_unique([GREETING_EMPLOYMENT.get(k, k) for k in kinds if isinstance(k, str)]),
        ev_structured(f"{path}.jobPositionEmployment.employmentType"),
    )
    put(
        out,
        FieldName.CAREER_LEVEL,
        _join_unique([_greeting_career(_dict(x.get("jobPositionCareer"))) for x in positions]),
        ev_structured(f"{path}.jobPositionCareer"),
    )
    questions, index = _greeting_questions(data)
    if questions:
        at = ",".join(str(i) for i in index)
        put(
            out,
            FieldName.QUESTIONS,
            questions,
            ev_structured(f"{p}.questionnairesInfo[{at}].question·maxLength"),
        )
    choices, index = _greeting_choices(data)
    if choices:
        at = ",".join(str(i) for i in index)
        put(
            out,
            FieldName.APPLICATION_QUESTIONS,
            choices,
            ev_structured(f"{p}.questionnairesInfo[{at}].question·need·optionItems"),
        )
    detail = info.get("detail")
    if not isinstance(detail, str):
        return out, None
    for k, v in sections_from_html(detail, f"{p}.openingsInfo.detail").items():
        out.setdefault(k, v)
    return out, html_to_text(detail)


# --- Greenhouse 호스팅 페이지 (Remix `__remixContext` 의 `jobPost`) -------------------------


def from_greenhouse(page: PageData) -> tuple[dict, str | None]:
    """`job-boards.greenhouse.io/<board>/jobs/<id>` (옛 `boards.greenhouse.io`는 여기로 넘어간다)."""
    ctx = _dict(page.window_state.get("__remixContext"))
    loader = _dict(_dict(ctx.get("state")).get("loaderData"))
    jp = None
    for data in loader.values():
        cand = _dict(data).get("jobPost")
        if isinstance(cand, dict) and isinstance(cand.get("title"), str):
            jp = cand
            break
    if jp is None:
        return {}, None
    host = urlsplit(page.url).hostname or ""
    if not (host.endswith("greenhouse.io") or "greenhouse.io" in str(jp.get("public_url", ""))):
        return {}, None
    p = "greenhouse:jobPost"
    out: dict = {}
    put(out, FieldName.TITLE, jp.get("title"), ev_structured(f"{p}.title"))
    put(out, FieldName.COMPANY, jp.get("company_name"), ev_structured(f"{p}.company_name"))
    put(
        out,
        FieldName.LOCATION,
        jp.get("job_post_location"),
        ev_structured(f"{p}.job_post_location"),
    )
    put(out, FieldName.POSTED_AT, iso(jp.get("published_at")), ev_structured(f"{p}.published_at"))
    put(
        out,
        FieldName.DEADLINE,
        iso(jp.get("application_deadline")),
        ev_structured(f"{p}.application_deadline"),
    )
    content = jp.get("content") if isinstance(jp.get("content"), str) else ""
    for k, v in sections_from_html(content, f"{p}.content").items():
        out.setdefault(k, v)
    parts = [jp.get(k) for k in ("introduction", "content", "conclusion")]
    body = html_to_text("\n".join(x for x in parts if isinstance(x, str)))
    return out, body or None


PageAdapter = Callable[[PageData], tuple[dict, str | None]]
# 채용 시스템 전용 페이지 어댑터. JSON-LD보다 먼저 본다 (JSON-LD는 빈 항목만 채운다).
PAGE_ADAPTERS: tuple[tuple[str, PageAdapter], ...] = (
    ("greenhouse", from_greenhouse),
    ("greeting", from_greeting),
    ("ninehire", from_ninehire),
)


# --- 공개 JSON API 어댑터 -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApiAdapter:
    name: str
    # (페이지, 최종 URL) → 공개 JSON 주소. 이 채용 시스템이 아니면 None.
    api_url: Callable[[PageData | None, str], str | None]
    parse: Callable[[Any], tuple[dict, str | None]]


_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,99}$")


def lever_api_url(page: PageData | None, url: str) -> str | None:
    """`jobs.lever.co/<site>/<id>` → `api.lever.co/v0/postings/<site>/<id>` (EU는 api.eu.lever.co)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host not in ("jobs.lever.co", "jobs.eu.lever.co"):
        return None
    seg = [s for s in parts.path.split("/") if s]
    if len(seg) < 2 or not _SLUG.match(seg[0]) or not _UUID.match(seg[1]):
        return None
    api = "api.eu.lever.co" if host == "jobs.eu.lever.co" else "api.lever.co"
    return f"https://{api}/v0/postings/{seg[0]}/{seg[1].lower()}"


def from_lever_api(data: Any) -> tuple[dict, str | None]:
    """Lever Postings API 한 건. 회사 이름은 응답에 없다 (페이지 JSON-LD가 채운다)."""
    d = _dict(data)
    if not isinstance(d.get("text"), str) or "id" not in d:
        return {}, None
    p = "lever:posting"
    cat = _dict(d.get("categories"))
    out: dict = {}
    put(out, FieldName.TITLE, d.get("text"), ev_structured(f"{p}.text"))
    locs = _list(cat.get("allLocations")) or [cat.get("location")]
    put(out, FieldName.LOCATION, _join_unique(locs), ev_structured(f"{p}.categories.allLocations"))
    put(
        out,
        FieldName.EMPLOYMENT_TYPE,
        cat.get("commitment"),
        ev_structured(f"{p}.categories.commitment"),
    )
    created = d.get("createdAt")
    if isinstance(created, int) and created > 0:
        day = datetime.fromtimestamp(created / 1000, UTC).date().isoformat()
        put(out, FieldName.POSTED_AT, day, ev_structured(f"{p}.createdAt"))
    html_parts = [d.get("description") if isinstance(d.get("description"), str) else ""]
    found: dict[FieldName, tuple[list[str], list[str]]] = {}
    for i, lst in enumerate(_list(d.get("lists"))):
        lst = _dict(lst)
        heading, content = lst.get("text"), lst.get("content")
        if not isinstance(heading, str) or not isinstance(content, str):
            continue
        html_parts.append(f"<h3>{_escape(heading)}</h3>{content}")
        name = classify_heading(heading, certain=True)
        if name is None:
            continue
        items, refs = found.setdefault(name, ([], []))
        refs.append(f"lists[{i}]")
        for text, _level, _li in html_lines(content):
            item = _item(text)
            if item and item not in items and len(items) < MAX_LIST_ITEMS:
                items.append(item)
    for name, (items, refs) in found.items():
        put(out, name, items, ev_structured(f"{p}.{','.join(refs)}"))
    if isinstance(d.get("additional"), str):
        html_parts.append(d["additional"])
    return out, html_to_text("\n".join(html_parts)) or None


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_LOCALE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")


def workday_api_url(page: PageData | None, url: str) -> str | None:
    """`<tenant>.wdN.myworkdayjobs.com/[locale/]<site>/job/...` → 같은 호스트의 `/wday/cxs/<tenant>/<site>/job/...`."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not (host.endswith((".myworkdayjobs.com", ".myworkdaysite.com"))):
        return None
    seg = [s for s in parts.path.split("/") if s]
    if (
        seg and seg[0] == "recruiting" and len(seg) >= 4
    ):  # myworkdaysite.com/recruiting/<tenant>/<site>/job/...
        tenant, seg = seg[1], seg[2:]
    else:
        tenant = host.split(".")[0]
        if seg and _LOCALE.match(seg[0]):
            seg = seg[1:]
    if len(seg) < 3 or seg[1] != "job" or not _SLUG.match(seg[0]) or not _SLUG.match(tenant):
        return None
    rest = "/".join(seg[1:])
    return f"https://{host}/wday/cxs/{tenant}/{seg[0]}/{rest}"


def from_workday_api(data: Any) -> tuple[dict, str | None]:
    """Workday CXS `jobPostingInfo`. 페이지의 JSON-LD 설명은 줄바꿈이 사라져 있어 여기 HTML을 본문으로 쓴다."""
    d = _dict(data)
    info = _dict(d.get("jobPostingInfo"))
    if not isinstance(info.get("title"), str):
        return {}, None
    p = "workday:jobPostingInfo"
    out: dict = {}
    put(out, FieldName.TITLE, info.get("title"), ev_structured(f"{p}.title"))
    put(
        out,
        FieldName.COMPANY,
        _dict(d.get("hiringOrganization")).get("name"),
        ev_structured("workday:hiringOrganization.name"),
    )
    locs = [info.get("location"), *_list(info.get("additionalLocations"))]
    put(out, FieldName.LOCATION, _join_unique(locs), ev_structured(f"{p}.location"))
    put(out, FieldName.EMPLOYMENT_TYPE, info.get("timeType"), ev_structured(f"{p}.timeType"))
    put(out, FieldName.POSTED_AT, iso(info.get("startDate")), ev_structured(f"{p}.startDate"))
    put(out, FieldName.DEADLINE, iso(info.get("endDate")), ev_structured(f"{p}.endDate"))
    desc = info.get("jobDescription")
    if not isinstance(desc, str):
        return out, None
    for k, v in sections_from_html(desc, f"{p}.jobDescription").items():
        out.setdefault(k, v)
    return out, html_to_text(desc) or None


_GH_BOARD_HOSTS = ("job-boards.greenhouse.io", "boards.greenhouse.io")
_GH_EMBED = re.compile(
    r"greenhouse\.io/embed/job_(?:board|app)(?:/js)?\?(?:[^#]*&)?for=([A-Za-z0-9_\-]+)"
)


def greenhouse_api_url(page: PageData | None, url: str) -> str | None:
    """Greenhouse Job Board API (`boards-api.greenhouse.io/v1/boards/<board>/jobs/<id>`).

    - 호스팅 페이지 주소(`job-boards.greenhouse.io/<board>/jobs/<id>`)에서 바로, 또는
    - 회사 자체 페이지: URL의 `gh_jid`와 페이지가 불러오는 Greenhouse 임베드 스크립트의 `for=<board>`.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    seg = [s for s in parts.path.split("/") if s]
    board = job = None
    if host in _GH_BOARD_HOSTS and len(seg) >= 3 and seg[1] == "jobs":
        board, job = seg[0], seg[2]
    else:
        jid = parse_qs(parts.query).get("gh_jid", [None])[0]
        if jid and page is not None:
            for src in page.resource_urls:
                m = _GH_EMBED.search(src)
                if m:
                    board, job = m.group(1), jid
                    break
    if not board or not job or not job.isdigit() or not _SLUG.match(board):
        return None
    return f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}"


def from_greenhouse_api(data: Any) -> tuple[dict, str | None]:
    d = _dict(data)
    if not isinstance(d.get("title"), str) or "id" not in d:
        return {}, None
    p = "greenhouse_api:job"
    out: dict = {}
    put(out, FieldName.TITLE, d.get("title"), ev_structured(f"{p}.title"))
    put(out, FieldName.COMPANY, d.get("company_name"), ev_structured(f"{p}.company_name"))
    put(
        out,
        FieldName.LOCATION,
        _dict(d.get("location")).get("name"),
        ev_structured(f"{p}.location.name"),
    )
    put(
        out,
        FieldName.POSTED_AT,
        iso(d.get("first_published")),
        ev_structured(f"{p}.first_published"),
    )
    put(
        out,
        FieldName.DEADLINE,
        iso(d.get("application_deadline")),
        ev_structured(f"{p}.application_deadline"),
    )
    # content는 HTML을 한 번 더 이스케이프해 둔다 (&lt;p&gt;...).
    content = unescape(d["content"]) if isinstance(d.get("content"), str) else ""
    for k, v in sections_from_html(content, f"{p}.content").items():
        out.setdefault(k, v)
    return out, html_to_text(content) or None


# 페이지 어댑터가 못 찾았을 때만 부른다. 순서대로 처음 맞는 주소 하나만 요청한다.
API_ADAPTERS: tuple[ApiAdapter, ...] = (
    ApiAdapter("lever", lever_api_url, from_lever_api),
    ApiAdapter("workday", workday_api_url, from_workday_api),
    ApiAdapter("greenhouse", greenhouse_api_url, from_greenhouse_api),
)

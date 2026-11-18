"""회사 정보 알아서 찾기 (c0053) — 모델·DB 없이 정하는 규칙.

공고를 저장하면 그 회사의 **공개 자료 몇 쪽**만 가져온다. 사이트를 돌아다니지 않는다:

1. 이 회사 공고의 글 (인재상·회사 소개 문단).
2. 회사 홈페이지 한 쪽: 등록한 홈페이지 → 공고 주소(``career.회사.com`` 같은 채용 사이트)에서 짐작 →
   위키데이터의 공식 웹사이트(P856) 순.
3. 그 홈페이지에서 링크 글·주소가 회사 소개·인재상·문화·사업 쪽인 같은 사이트 링크 몇 개 (한 단계만).
4. 위키데이터가 가리키는 위키백과 문서 (한국어 먼저).

가져온 글에서 항목을 뽑는 규칙은 다른 회사 원문과 같다 (원문 인용 필수, 후보 → 사람 확인).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit

MAX_FOUND_LINKS = 4
MAX_POSTING_PAGES = 3

# 공고를 올리는 채용 사이트·채용 시스템: 이 주소로는 회사 홈페이지를 짐작하지 않는다.
JOB_BOARDS = (
    "wanted.co.kr",
    "saramin.co.kr",
    "jobkorea.co.kr",
    "jumpit.co.kr",
    "rocketpunch.com",
    "linkedin.com",
    "greetinghr.com",
    "ninehire.com",
    "ninehire.site",
    "recruiter.co.kr",
    "incruit.com",
    "catch.co.kr",
    "jasoseol.com",
    "programmers.co.kr",
    "remember.co.kr",
    "greenhouse.io",
    "lever.co",
    "myworkdayjobs.com",
    "workday.com",
    "smartrecruiters.com",
    "ashbyhq.com",
    "recruitee.com",
    "notion.site",
    "notion.so",
    "careerly.co.kr",
    "peoplenjob.com",
    "albamon.com",
    "alba.co.kr",
)
# 채용 사이트를 회사 도메인 아래 따로 둘 때 흔한 앞 이름 (career.회사.com → 회사.com)
_CAREER_LABELS = {
    "career",
    "careers",
    "recruit",
    "recruiting",
    "recruitment",
    "jobs",
    "job",
    "hr",
    "talent",
    "apply",
    "join",
    "hire",
    "hiring",
    "people",
    "with",
    "team",
}
# 두 단계 국가 도메인 (회사.co.kr의 "회사"를 찾을 때)
_SECOND_LEVEL = {"co", "or", "ne", "go", "ac", "re", "com", "net", "org", "gov", "edu"}

# 링크 고르기: (점수, 낱말). 링크 글이나 주소에 있으면 그 점수.
_LINK_WORDS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        5,
        (
            "인재상",
            "핵심가치",
            "핵심 가치",
            "기업문화",
            "조직문화",
            "기업 문화",
            "조직 문화",
            "culture",
            "values",
            "talent",
            "people",
            "일하는 방식",
            "way we work",
        ),
    ),
    (
        4,
        (
            "회사소개",
            "기업소개",
            "회사 소개",
            "기업 소개",
            "about",
            "company",
            "who we are",
            "비전",
            "vision",
            "mission",
            "미션",
            "경영이념",
            "경영 이념",
        ),
    ),
    (
        3,
        (
            "사업",
            "business",
            "제품",
            "product",
            "solution",
            "솔루션",
            "서비스 소개",
            "연혁",
            "history",
        ),
    ),
    (
        2,
        (
            "채용",
            "careers",
            "career",
            "recruit",
            "join us",
            "인재채용",
            "ir",
            "투자정보",
            "investor",
        ),
    ),
)
_SKIP_LINK = re.compile(
    r"(login|signin|sign-in|signup|logout|privacy|개인정보|terms|약관|cookie|sitemap|\.pdf$|\.jpe?g$|"
    r"\.png$|\.zip$|mailto:|tel:|javascript:|/news/\d|/board/|/notice|공지|faq|contact|문의)",
    re.IGNORECASE,
)


def registrable_domain(host: str) -> str:
    """``www.kohyoung.com`` → ``kohyoung.com``, ``recruit.example.co.kr`` → ``example.co.kr``."""
    labels = [x for x in (host or "").lower().rstrip(".").split(".") if x]
    if len(labels) <= 2:
        return ".".join(labels)
    if len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def is_job_board(host: str) -> bool:
    h = (host or "").lower().rstrip(".")
    return any(h == b or h.endswith("." + b) for b in JOB_BOARDS)


def homepage_from_posting(url: str) -> str | None:
    """공고 주소가 회사 도메인 아래 채용 사이트면(``career.kohyoung.com``) 그 회사 홈페이지 주소."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return None
    if not host or is_job_board(host):
        return None
    labels = host.split(".")
    base = registrable_domain(host)
    if len(labels) > len(base.split(".")) and labels[0] in _CAREER_LABELS:
        return f"https://{base}"
    return None


def same_site(url: str, home: str) -> bool:
    try:
        a, b = urlsplit(url), urlsplit(home)
    except ValueError:
        return False
    if a.scheme not in ("http", "https") or not a.hostname or not b.hostname:
        return False
    return registrable_domain(a.hostname) == registrable_domain(b.hostname)


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            a = dict(attrs)
            self._href = a.get("href")
            self._text = [a.get("title") or "", a.get("aria-label") or ""]
        elif tag == "img" and self._href is not None:
            self._text.append(dict(attrs).get("alt") or "")

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(" ".join(self._text).split())[:200]))
            self._href = None


def page_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """페이지의 모든 링크 (절대 주소, 링크 글). 조각(#)은 뗀다."""
    p = _Links()
    p.feed(html or "")  # html.parser는 깨진 HTML에도 예외 없이 찾은 만큼 준다
    p.close()
    out = []
    for href, text in p.links:
        if not href or href.startswith("#"):
            continue
        absolute = urljoin(base_url, href.strip()).split("#")[0]
        out.append((absolute, text))
    return out


def link_score(url: str, text: str) -> int:
    hay_text = (text or "").lower()
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        return 0
    path_words = re.sub(r"[/_\-.]+", " ", path)
    best = 0
    for score, words in _LINK_WORDS:
        for w in words:
            wl = w.lower()
            in_path = re.search(rf"(?<![a-z]){re.escape(wl)}(?![a-z])", path_words) is not None
            if wl in hay_text or (wl.isascii() and in_path):
                best = max(best, score)
    return best


def pick_links(
    links: list[tuple[str, str]], home: str, *, skip: set[str], limit: int = MAX_FOUND_LINKS
) -> list[str]:
    """홈페이지 링크 중 같은 사이트의 회사 소개·인재상·문화·사업 쪽을 점수 순으로 ``limit``개."""
    seen: set[str] = set()
    scored: list[tuple[int, int, str]] = []
    for i, (url, text) in enumerate(links):
        key = url.rstrip("/")
        if key in seen or key in skip or not same_site(url, home):
            continue
        seen.add(key)
        if _SKIP_LINK.search(url) or _SKIP_LINK.search(text or ""):
            continue
        if key == home.rstrip("/"):
            continue
        score = link_score(url, text)
        if score:
            scored.append((-score, i, url))
    scored.sort()
    return [u for _s, _i, u in scored[:limit]]


# --- 위키데이터 ------------------------------------------------------------------------

WIKIDATA_API = "https://www.wikidata.org/w/api.php"


def wikidata_search_url(name: str, lang: str = "ko") -> str:
    return (
        f"{WIKIDATA_API}?action=wbsearchentities&format=json&type=item&limit=7"
        f"&language={lang}&uselang={lang}&search={quote(name)}"
    )


def wikidata_entities_url(ids: list[str]) -> str:
    return (
        f"{WIKIDATA_API}?action=wbgetentities&format=json&props=labels%7Caliases%7Cclaims%7Csitelinks"
        f"&languages=ko%7Cen&sitefilter=kowiki%7Cenwiki&ids={'%7C'.join(ids)}"
    )


def wiki_page_url(site: str, title: str) -> str:
    lang = "ko" if site == "kowiki" else "en"
    return f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"


# 회사·기관에만 있는 속성: 업종·본사·직원 수·상장·매출·법인 형태·모회사·자회사·창립자
_ORG_PROPS = ("P452", "P159", "P1128", "P414", "P2139", "P1454", "P749", "P355", "P112", "P1056")
KOREA = "Q884"
_HANGUL = re.compile(r"[가-힣]")


def _claim_ids(e: dict, prop: str) -> list[str]:
    out = []
    for c in (e.get("claims") or {}).get(prop) or []:
        v = (((c or {}).get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(v, dict) and isinstance(v.get("id"), str):
            out.append(v["id"])
        elif isinstance(v, str):
            out.append(v)
    return out


def pick_entity(
    entities: dict, order: list[str], keys: set[str], key_of, *, name: str = ""
) -> dict | None:
    """이름·별칭이 회사 이름 키와 같고 회사·기관 속성이 있는 엔터티. 한글 이름이면 한국(P17) 엔터티 먼저,
    그다음 검색 순서. ``{"qid", "homepage", "wiki"}`` 또는 None (식물·사람·지명은 고르지 않는다)."""
    korean = bool(_HANGUL.search(name or ""))
    ranked: list[tuple[int, int, str, dict]] = []
    for i, qid in enumerate(order):
        e = entities.get(qid)
        if not isinstance(e, dict):
            continue
        names = [
            v.get("value", "") for v in (e.get("labels") or {}).values() if isinstance(v, dict)
        ]
        for vals in (e.get("aliases") or {}).values():
            names += [v.get("value", "") for v in vals or [] if isinstance(v, dict)]
        if not any(key_of(n) in keys for n in names if n):
            continue
        claims = e.get("claims") or {}
        if not any(claims.get(p) for p in _ORG_PROPS):
            continue
        in_korea = KOREA in _claim_ids(e, "P17")
        ranked.append((0 if (in_korea or not korean) else 1, i, qid, e))
    for _k, _i, qid, e in sorted(ranked, key=lambda r: (r[0], r[1])):
        homepage = next(
            (v for v in _claim_ids(e, "P856") if v.startswith(("http://", "https://"))), None
        )
        links = e.get("sitelinks") or {}
        wiki = None
        for site in ("kowiki", "enwiki"):
            t = (links.get(site) or {}).get("title")
            if isinstance(t, str) and t:
                wiki = wiki_page_url(site, t)
                break
        if homepage or wiki:
            return {"qid": qid, "homepage": homepage, "wiki": wiki}
    return None

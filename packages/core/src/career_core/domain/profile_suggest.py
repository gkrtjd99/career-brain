"""내 경험 자료(이력서 등)의 글에서 프로필 칸 후보 찾기 — 규칙만 쓴다 (모델을 부르지 않는다).

찾는 것: 이메일, 휴대전화(``010-1234-5678``로 맞춤), 링크(GitHub·LinkedIn·블로그·포트폴리오), 이름.
이름은 이름표가 붙은 줄(``이름: …``·``성명 …``·``Name: …``)이나, 이력서로 보이는 원본(제목·파일 이름에
이력서·resume·CV)의 앞 세 블록에서 한글 2~4글자뿐인 줄만 본다. 그 밖의 글에서 사람 이름을 짐작하지 않는다.

어느 원본을 볼지(내 경험 자료만, 증빙·참고는 보지 않음)는 부르는 쪽(``usecases.profiles``)이 정한다.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass

FIELD_NAME = "name"
FIELD_EMAIL = "email"
FIELD_PHONE = "phone"
FIELD_LINK = "link"
FIELDS = (FIELD_NAME, FIELD_EMAIL, FIELD_PHONE, FIELD_LINK)
LIMITS = {FIELD_NAME: 1, FIELD_EMAIL: 3, FIELD_PHONE: 3, FIELD_LINK: 8}

LABEL_GITHUB = "GitHub"
LABEL_LINKEDIN = "LinkedIn"
LABEL_BLOG = "블로그"
LABEL_PORTFOLIO = "포트폴리오"
LABEL_LINK = "링크"

QUOTE_CHARS = 160
MIN_NAME, MAX_NAME = 2, 20

_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
)
# 휴대전화: 010 1234 5678 · 010-1234-5678 · 010.1234.5678 · 01012345678 · +82 10-1234-5678 · +82 (0)10 …
_PHONE = re.compile(
    r"(?<![\d+])(?:\+82[\s.-]*(?:\(0\)[\s.-]*)?0?|0)(1[016789])[\s.)-]*(\d{3,4})[\s.-]*(\d{4})(?!\d)"
)
_URL = re.compile(r"https?://[^\s<>\"'()\[\]{}]+", re.IGNORECASE)
# 주소 앞 http(s)://가 없어도 아는 곳이면 링크로 본다 (github.com/kim …)
_BARE = re.compile(
    r"(?<![\w./@-])(?:www\.)?"
    r"(?:(?:github\.com|linkedin\.com|velog\.io|medium\.com|brunch\.co\.kr)/[^\s<>\"'()\[\]{}]+"
    r"|[a-z0-9-]+\.(?:tistory\.com|notion\.site)(?:/[^\s<>\"'()\[\]{}]*)?)",
    re.IGNORECASE,
)
_TRAIL = ".,;:!?…·'\"”’)]}>"
# 이 곳이면 이름표 없이도 링크 후보 (값은 표시 이름)
_HOSTS: tuple[tuple[str, str], ...] = (
    ("github.com", LABEL_GITHUB),
    ("linkedin.com", LABEL_LINKEDIN),
    ("velog.io", LABEL_BLOG),
    ("tistory.com", LABEL_BLOG),
    ("medium.com", LABEL_BLOG),
    ("brunch.co.kr", LABEL_BLOG),
    ("notion.site", LABEL_LINK),
)
_LINE_LABELS = re.compile(r"(포트폴리오|portfolio|블로그|blog)", re.IGNORECASE)
_NAME_LABEL = re.compile(
    r"^\s*(?:이\s?름|성\s?명|name)(?:\s*\([^)\n]{0,10}\))?(?:\s*[:：]\s*|\s+)(.+)$", re.IGNORECASE
)
# 이름 값은 같은 줄의 다음 칸 앞에서 자른다 ("이름: 김예시   이메일: …", "성명 김예시 | 010…")
_NAME_CUT = re.compile(r"\s{2,}|\t|[|/,;·•()\[\]<>]")
_HANGUL_NAME = re.compile(r"[가-힣]{2,4}")
_RESUME_HINT = re.compile(r"이력서|resume|résumé|(?<![a-z])cv(?![a-z])", re.IGNORECASE)
# 이력서 앞쪽에 한글 2~4글자로만 된 줄로 자주 나오는 제목·칸 이름 (이름으로 보지 않는다)
_NOT_NAMES = frozenset(
    [
        "이력서",
        "경력",
        "경력사항",
        "학력",
        "학력사항",
        "인적사항",
        "기본정보",
        "개인정보",
        "신상정보",
        "프로필",
        "연락처",
        "소개",
        "자기소개",
        "요약",
        "핵심역량",
        "보유기술",
        "기술",
        "기술스택",
        "스킬",
        "프로젝트",
        "포트폴리오",
        "자격증",
        "자격",
        "수상",
        "수상경력",
        "활동",
        "대외활동",
        "교육",
        "교육사항",
        "어학",
        "병역",
        "병역사항",
        "경험",
        "경력기술",
        "기술서",
        "직무",
        "지원분야",
        "희망직무",
        "성명",
        "이름",
        "주소",
        "이메일",
        "전화",
        "휴대폰",
        "휴대전화",
        "생년월일",
        "국적",
        "성별",
        "취미",
        "특기",
        "기타",
        "목차",
        "첨부",
        "사진",
        "개요",
        "업무",
        "담당업무",
        "성과",
        "주요성과",
        "회사",
        "회사명",
        "기간",
        "역할",
        "학교",
        "전공",
    ]
)


@dataclass(frozen=True, slots=True)
class Found:
    field: str
    value: str
    label: str | None
    quote: str
    strong: bool = True  # 이름만: 이름표가 붙은 줄이면 True, 이력서 앞줄 짐작이면 False


def _nfkc(value: str | None) -> str:
    return unicodedata.normalize("NFKC", value or "")


def quote_around(text: str, start: int, end: int, limit: int = QUOTE_CHARS) -> str:
    """찾은 자리 둘레의 블록 글 (공백은 한 칸, ``limit``자 이하)."""
    if len(" ".join(text.split())) <= limit:
        return " ".join(text.split())
    pad = max(0, (limit - (end - start)) // 2)
    lo = max(0, start - pad)
    piece = " ".join(text[lo : lo + limit * 2].split())
    return piece[:limit].strip()


def phone_key(value: str | None) -> str:
    """비교 키: 숫자만, 국가 번호 82로 시작하면 0으로 (``+82 10-…`` = ``010-…``)."""
    digits = re.sub(r"\D", "", _nfkc(value))
    if digits.startswith("82") and len(digits) >= 11:
        digits = "0" + digits[2:].lstrip("0")
    return digits


def format_phone(head: str, mid: str, tail: str) -> str:
    return f"0{head}-{mid}-{tail}"


def email_key(value: str | None) -> str:
    return _nfkc(value).strip().casefold()


def name_key(value: str | None) -> str:
    return "".join(_nfkc(value).split()).casefold()


def url_key(value: str | None) -> str:
    """비교 키: 앞의 http(s)://·www.·끝의 / 를 빼고 주소 이름은 소문자 (경로의 대소문자는 둔다)."""
    v = _nfkc(value).strip()
    v = re.sub(r"^https?://", "", v, flags=re.IGNORECASE)
    host, sep, rest = v.partition("/")
    host = host.casefold().removeprefix("www.")
    return (host + sep + rest).rstrip("/")


def _host_label(url: str) -> str | None:
    host = url_key(url).partition("/")[0]
    for suffix, label in _HOSTS:
        if host == suffix or host.endswith("." + suffix):
            return label
    return None


def _useful(url: str) -> bool:
    """주소만 있고 경로가 없는 GitHub·LinkedIn 등 대표 주소(``https://github.com``)는 내 링크가 아니다."""
    host, _, path = url_key(url).partition("/")
    return bool(path) or any(host.endswith("." + h) for h, _ in _HOSTS)


def _clean_url(raw: str) -> str:
    url = raw.rstrip(_TRAIL)
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    return url


def _line_label(line: str) -> str | None:
    m = _LINE_LABELS.search(line)
    if not m:
        return None
    return LABEL_PORTFOLIO if m.group(1).casefold() in ("포트폴리오", "portfolio") else LABEL_BLOG


def _lines(text: str) -> Iterator[tuple[int, str]]:
    pos = 0
    for line in text.split("\n"):
        yield pos, line
        pos += len(line) + 1


def _clean_name(raw: str) -> str | None:
    v = _NAME_CUT.split(raw.strip(), maxsplit=1)[0].strip().strip(":：-").strip()
    if not (MIN_NAME <= len(v) <= MAX_NAME):
        return None
    if "@" in v or any(c.isdigit() for c in v) or "://" in v:
        return None
    if name_key(v) in _NOT_NAMES:
        return None
    return v


def scan_block(text: str) -> list[Found]:
    """블록 하나에서 이메일·휴대전화·링크·이름표 붙은 이름."""
    text = _nfkc(text)
    out: list[Found] = []
    emails: list[tuple[int, int]] = []
    for m in _EMAIL.finditer(text):
        emails.append(m.span())
        out.append(Found(FIELD_EMAIL, m.group(), None, quote_around(text, *m.span())))
    for m in _PHONE.finditer(text):
        out.append(
            Found(FIELD_PHONE, format_phone(*m.groups()), None, quote_around(text, *m.span()))
        )
    for pos, line in _lines(text):
        line_label = _line_label(line)
        taken: list[tuple[int, int]] = []
        for m in _URL.finditer(line):
            taken.append(m.span())
            url = _clean_url(m.group())
            label = _host_label(url) or line_label
            if label is None or not _useful(url):
                continue  # 아는 곳도 아니고 포트폴리오·블로그 줄도 아니다
            if label == LABEL_LINK and line_label:
                label = line_label
            s, e = pos + m.start(), pos + m.end()
            out.append(Found(FIELD_LINK, url, label, quote_around(text, s, e)))
        for m in _BARE.finditer(line):
            s, e = pos + m.start(), pos + m.end()
            if any(a <= m.start() < b for a, b in taken) or any(a <= s < b for a, b in emails):
                continue
            url = _clean_url(m.group())
            if not _useful(url):
                continue
            label = _host_label(url) or LABEL_LINK
            if label == LABEL_LINK and line_label:
                label = line_label
            out.append(Found(FIELD_LINK, url, label, quote_around(text, s, e)))
        nm = _NAME_LABEL.match(line)
        if nm and (name := _clean_name(nm.group(1))):
            s = pos + nm.start(1)
            out.append(Found(FIELD_NAME, name, None, quote_around(text, s, s + len(name))))
    return out


def is_hangul_name(value: str) -> bool:
    return _HANGUL_NAME.fullmatch(value.replace(" ", "")) is not None


def looks_like_resume(*names: str | None) -> bool:
    return any(_RESUME_HINT.search(_nfkc(n)) for n in names if n)


def guess_name(text: str) -> Found | None:
    """이력서 앞 블록의 한 줄이 한글 2~4글자뿐이면 이름으로 본다 (제목·칸 이름은 뺀다)."""
    text = _nfkc(text)
    for pos, line in _lines(text):
        v = line.strip()
        if _HANGUL_NAME.fullmatch(v) and v not in _NOT_NAMES:
            s = pos + line.index(v)
            return Found(FIELD_NAME, v, None, quote_around(text, s, s + len(v)), strong=False)
    return None


def value_key(field: str, value: str) -> str:
    if field == FIELD_EMAIL:
        return email_key(value)
    if field == FIELD_PHONE:
        return phone_key(value)
    if field == FIELD_LINK:
        return url_key(value)
    return name_key(value)


__all__ = [
    "FIELDS",
    "LIMITS",
    "Found",
    "guess_name",
    "is_hangul_name",
    "looks_like_resume",
    "quote_around",
    "scan_block",
    "value_key",
]

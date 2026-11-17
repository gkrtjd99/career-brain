"""채용 공고 저장 규칙 (O06).

- 사용자가 붙여넣은 URL만 한 건씩 가져온다. 목록 페이지는 따라가지 않고 후보 링크만 보여준다.
- 항목마다 근거가 있다: 페이지의 구조화 데이터 경로, 또는 본문 인용(코드가 원문에서 찾은 것).
  근거 없는 값은 비우고 `needs_review`로 둔다. 모델이 만든 날짜·조건은 근거 인용 없이는 저장하지 않는다.
- 가져오기 실패는 마감이 아니다. 실패 사유만 기록한다.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit


class PostingStatus(StrEnum):
    DRAFT = "draft"  # 가져왔지만 사용자가 아직 확인하지 않음
    SAVED = "saved"  # 사용자가 확인해 저장한 공고
    ARCHIVED = "archived"


class FetchStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"  # 새 버전 또는 변경 없음
    LISTING = "listing"  # 공고 목록 페이지였다: 후보 링크만 남긴다
    FAILED = "failed"
    # c0025: 비전 호출(스캔 PDF) 예상 비용이 상한을 넘어 사용자 확인을 기다린다 (job_fetch_confirmations)
    NEEDS_CONFIRMATION = "needs_confirmation"


class FetchError(StrEnum):
    SOURCE_UNAVAILABLE = "source_unavailable"
    LOGIN_REQUIRED = "login_required"
    RATE_LIMITED = "rate_limited"
    # 봇 차단(Cloudflare 등의 자동 요청 확인 화면). 우회하지 않는다: 본문 붙여넣기·캡처 업로드로 안내
    BOT_BLOCKED = "bot_blocked"
    # 알려진 서비스는 아니지만 확인 화면 흔적(캡차·JS 확인 스크립트·빈 본문+스크립트)이 있다 (c0025).
    # 우회하지 않는다. "막혔을 수 있음"으로 안내하고 rate_limited로 보지 않는다.
    MAYBE_BOT_BLOCKED = "maybe_bot_blocked"
    NEEDS_MANUAL_CHECK = "needs_manual_check"
    BLOCKED_ADDRESS = "blocked_address"  # 사설망·메타데이터 등 내부 주소
    TOO_LARGE = "too_large"
    UNSUPPORTED_CONTENT = "unsupported_content"  # 받지 않는 형식 (PDF·이미지는 받는다: postings.md)
    INVALID_URL = "invalid_url"


class FieldName(StrEnum):
    TITLE = "title"
    COMPANY = "company"
    LOCATION = "location"
    EMPLOYMENT_TYPE = "employment_type"
    CAREER_LEVEL = "career_level"
    DEADLINE = "deadline"  # ISO 8601 날짜/시각, 또는 상시채용이면 "always"
    # 접수 시작 (ISO 8601 날짜/시각). 원문이 접수 시작을 적었을 때만 채운다 — 게시일에서 추측하지 않는다
    OPENS_AT = "opens_at"
    POSTED_AT = "posted_at"
    RESPONSIBILITIES = "responsibilities"  # 목록
    REQUIREMENTS = "requirements"  # 목록
    PREFERRED = "preferred"  # 목록
    QUESTIONS = "questions"  # 자기소개서 문항: [{"prompt": str, "char_limit": int|None}]
    PROCESS = "process"  # 전형 절차: 목록
    # c0025: 지원서의 선택형 문항(보기 고르기). 자기소개서가 아니다. 구조화 데이터에서만 (LLM 추출 없음)
    # [{"prompt": str, "required": bool|None, "options": [str], "source_type": int|None}]
    APPLICATION_QUESTIONS = "application_questions"


LIST_FIELDS = {
    FieldName.RESPONSIBILITIES,
    FieldName.REQUIREMENTS,
    FieldName.PREFERRED,
    FieldName.PROCESS,
}
# 채용 시스템 구조화 데이터에서만 채우는 항목: 비어 있어도 LLM을 부르지 않고, 모델 출력에서 받지 않는다.
STRUCTURED_ONLY_FIELDS = frozenset({FieldName.APPLICATION_QUESTIONS})
MAX_CHOICE_OPTIONS = 50
# 비어 있으면 확인이 필요한 항목. 나머지는 원문에 없을 수 있다.
REVIEW_FIELDS = (FieldName.TITLE, FieldName.COMPANY, FieldName.DEADLINE)

TRACKING_PARAMS = re.compile(
    r"^(utm_.*|gclid|fbclid|msclkid|igshid|mc_[ce]id|_hs.*|ref|ref_src|spm|source|trk|"
    r"trackingId|from|share|si)$",
    re.IGNORECASE,
)


class Lifecycle(StrEnum):
    """공고의 접수 상태 (저장한 값과 지금 시각으로 계산한다. 저장하지 않는다)."""

    UPCOMING = "upcoming"  # 접수 시작 전
    OPEN = "open"  # 접수 중 (마감이 오늘이거나 뒤)
    ALWAYS = "always"  # 상시 채용
    CLOSED = "closed"  # 마감이 지남
    UNKNOWN = "unknown"  # 마감일이 없다 (가져오기 실패 포함: 실패는 마감이 아니다)


class PostingRuleError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_url(raw: str) -> str:
    """같은 공고를 같은 문자열로. 추적 파라미터·조각(#)·기본 포트·끝 슬래시를 정리한다."""
    raw = (raw or "").strip()
    if not raw:
        raise PostingRuleError(FetchError.INVALID_URL)
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        port = parts.port  # 숫자가 아닌 포트는 여기서 ValueError
    except ValueError as exc:
        raise PostingRuleError(FetchError.INVALID_URL) from exc
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        raise PostingRuleError(FetchError.INVALID_URL)
    if parts.username or parts.password:
        raise PostingRuleError(FetchError.INVALID_URL)  # URL 안의 계정 정보는 받지 않는다
    host = parts.hostname.lower().rstrip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise PostingRuleError(FetchError.INVALID_URL) from exc
    default = (scheme, port) in (("http", 80), ("https", 443)) or port is None
    shown = f"[{host}]" if ":" in host else host
    netloc = shown if default else f"{shown}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    path = quote(unquote(path), safe="/%:@!$&'()*+,;=-._~")
    if len(path) > 1:
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not TRACKING_PARAMS.match(k)
        )
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def is_public_address(value: str) -> bool:
    """외부 공개 주소만 허용한다 (사설·루프백·링크로컬·메타데이터·멀티캐스트·예약 주소 차단)."""
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return bool(ip.is_global) and not ip.is_multicast


@dataclass(frozen=True, slots=True)
class Evidence:
    """structured: 페이지 구조화 데이터의 경로. quote: 본문에서 찾은 인용. user: 사용자가 직접 입력."""

    kind: str
    ref: str


def date_digits_in_quote(value: str, quote: str) -> bool:
    """모델이 낸 날짜의 월·일 숫자가 인용 안에 실제로 있는지 본다 (날짜를 지어내지 않게)."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", value or "")
    if not m:
        return False
    month, day = int(m.group(2)), int(m.group(3))
    nums = {int(n) for n in re.findall(r"\d{1,4}", quote)}
    return month in nums and day in nums


# --- 채용 시스템(ATS) 추가 요청 ---------------------------------------------------------

# 공고 페이지 밖의 공개 JSON을 읽는 어댑터가 쓸 수 있는 호스트. 공고 페이지와 같은 호스트(Workday CXS)이거나
# 여기 있어야 한다. 문서: docs/architecture/postings.md "채용 시스템 어댑터".
# 요청은 모두 같은 HttpFetcher(공개 주소 검사·IP 고정·크기 상한)를 거친다.
ATS_API_HOSTS = frozenset(
    {
        "api.lever.co",  # Lever Postings API (공개 문서: github.com/lever/postings-api)
        "api.eu.lever.co",
        "boards-api.greenhouse.io",  # Greenhouse Job Board API (공개 문서)
    }
)


def ats_request_allowed(page_url: str, api_url: str) -> bool:
    """어댑터의 추가 요청은 https이고, 공고 페이지와 같은 호스트이거나 문서화한 ATS API 호스트일 때만."""
    try:
        page, api = urlsplit(page_url), urlsplit(api_url)
        port = api.port
    except ValueError:
        return False
    if api.scheme != "https" or not api.hostname or api.username or api.password:
        return False
    if port not in (None, 443):
        return False
    return api.hostname == page.hostname or api.hostname in ATS_API_HOSTS


# --- 섹션 제목 분류 ------------------------------------------------------------------

# h 태그·굵은 글씨처럼 "제목인 줄 확실한" 줄에 쓰는 느슨한 단서 (먼저 맞는 것이 이긴다).
_HEADING_HINTS: tuple[tuple[FieldName, tuple[str, ...]], ...] = (
    (
        FieldName.PREFERRED,
        (
            "우대",
            "preferred",
            "nice to have",
            "nice-to-have",
            "bonus",
            "pluses",
            "a plus",
            "great if",
            "stand out",
            "even better",
        ),
    ),
    (
        FieldName.PROCESS,
        (
            "전형",
            "채용 절차",
            "채용절차",
            "채용 과정",
            "채용과정",
            "합류 여정",
            "합류여정",
            "채용 여정",
            "hiring process",
            "interview process",
            "next steps",
        ),
    ),
    (
        FieldName.REQUIREMENTS,
        (
            "자격",
            "필수",
            "요건",
            "이런 분",
            "requirement",
            "qualification",
            "who you are",
            "what you should have",
            "what you'll bring",
            "what you will bring",
            "what we're looking for",
            "what we are looking for",
            "must have",
            "you have",
            "about you",
            "what we need",
            "need to see",
            "your expertise",
            "you bring",
            "you'll need",
            "you will need",
            "ideal candidate",
        ),
    ),
    (
        FieldName.RESPONSIBILITIES,
        (
            "담당",
            "주요 업무",
            "주요업무",
            "업무 내용",
            "업무내용",
            "하는 일",
            "합류하면",
            "responsibilit",
            "what you'll do",
            "what you will do",
            "what you'll be doing",
            "what you will be doing",
            "your role",
            "in this role",
            "your mission",
            "typical day",
            "day to day",
            "day-to-day",
            "what you'll work on",
            "what you will work on",
        ),
    ),
)
# 겉모양만으로는 제목인지 모르는 평범한 짧은 줄에 쓰는 엄격한 목록 (공백·기호를 뺀 소문자로 비교).
_HEADING_EXACT: tuple[tuple[FieldName, tuple[str, ...]], ...] = (
    (
        FieldName.PREFERRED,
        ("우대사항", "우대조건", "우대요건", "preferredqualifications", "nicetohave"),
    ),
    (
        FieldName.PROCESS,
        ("전형절차", "채용절차", "채용과정", "전형과정", "합류여정", "채용여정", "hiringprocess"),
    ),
    (
        FieldName.REQUIREMENTS,
        (
            "자격요건",
            "지원자격",
            "필수요건",
            "필수자격",
            "자격조건",
            "requirements",
            "qualifications",
            "minimumqualifications",
            "basicqualifications",
        ),
    ),
    (
        FieldName.RESPONSIBILITIES,
        ("주요업무", "담당업무", "업무내용", "하는일", "responsibilities", "whatyoulldo"),
    ),
)
_HEADING_STRIP = re.compile(r"[\s\[\]【】<>〈〉()（）■□▶▷◆◇●○◎※#*:：'’\-·•]+")


def classify_heading(line: str, *, certain: bool = False) -> FieldName | None:
    """공고 섹션 제목 → 목록 항목 이름. `certain`은 h 태그·굵은 글씨처럼 제목임이 확실한 경우.

    확실하지 않은 줄은 "자격요건"처럼 제목 낱말과 같을 때만 제목으로 본다
    ("Airflow 경험 우대" 같은 항목 줄을 제목으로 오인하지 않게).
    """
    text = " ".join((line or "").split())
    if not text or len(text) > (80 if certain else 30):
        return None
    if certain:
        low = text.lower().replace("’", "'")
        for name, hints in _HEADING_HINTS:
            if any(h in low for h in hints):
                return name
        return None
    key = _HEADING_STRIP.sub("", text).lower()
    for name, words in _HEADING_EXACT:
        if key in words:
            return name
    return None


def is_heading_line(line: str) -> bool:
    """본문 텍스트에서 섹션 제목처럼 보이는 줄 (분류 여부와 무관). 목록 경계를 정할 때 쓴다."""
    text = " ".join((line or "").split())
    if not text or len(text) > 30:
        return False
    if classify_heading(text) is not None:
        return True
    return bool(re.match(r"^[\[【<〈■□▶▷◆◇●○◎#].*", text)) and not text.endswith((".", "다"))


# --- 접수 상태·시기 ------------------------------------------------------------------
#
# 날짜만 있는 값은 공간 표시 시간대의 그날로 본다: 마감 "2026-11-30"은 그날 끝(다음 날 0시 직전)까지 접수 중,
# 접수 시작 "2026-12-01"은 그날 0시부터. 시각이 있는 값은 그 시각 그대로 (시간대가 없으면 공간 시간대).

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_when(value: object) -> date | datetime | None:
    if not isinstance(value, str) or not value.strip() or value == "always":
        return None
    v = value.strip()
    try:
        return date.fromisoformat(v) if _ISO_DATE.match(v) else datetime.fromisoformat(v)
    except ValueError:
        return None


def posting_moment(value: object, tz, *, end_of_day: bool = False) -> datetime | None:
    """마감·접수 시작 값 → 시각(시간대 있음). 날짜만 있으면 그날 0시, ``end_of_day``면 다음 날 0시."""
    when = _parse_when(value)
    if when is None:
        return None
    if isinstance(when, datetime):
        return when if when.tzinfo else when.replace(tzinfo=tz)
    day = when + timedelta(days=1) if end_of_day else when
    return datetime.combine(day, time(0), tzinfo=tz)


def posting_lifecycle(deadline: object, opens_at: object, now: datetime, tz) -> Lifecycle:
    """마감 지남 → 마감, 접수 시작 전 → 접수 예정, 상시 → 상시, 마감 전 → 접수 중, 마감일 없음 → 모름."""
    close = posting_moment(deadline, tz, end_of_day=True)
    if close is not None and now >= close:
        return Lifecycle.CLOSED
    start = posting_moment(opens_at, tz)
    if start is not None and now < start:
        return Lifecycle.UPCOMING
    if deadline == "always":
        return Lifecycle.ALWAYS
    if close is not None:
        return Lifecycle.OPEN
    return Lifecycle.UNKNOWN


def posting_season(deadline: object, opens_at: object, fallback: datetime | None, tz) -> str | None:
    """채용 시기 "2026-H1"(1–6월)·"2026-H2"(7–12월). 마감 → 접수 시작 → 저장(추가)한 날 순으로 본다."""
    day: date | None = None
    for value in (deadline, opens_at):
        when = _parse_when(value)
        if isinstance(when, datetime):
            day = (when if when.tzinfo else when.replace(tzinfo=tz)).astimezone(tz).date()
        elif when is not None:
            day = when
        if day is not None:
            break
    if day is None and fallback is not None:
        day = fallback.astimezone(tz).date()
    if day is None:
        return None
    return f"{day.year}-H{1 if day.month <= 6 else 2}"

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


class FetchError(StrEnum):
    SOURCE_UNAVAILABLE = "source_unavailable"
    LOGIN_REQUIRED = "login_required"
    RATE_LIMITED = "rate_limited"
    NEEDS_MANUAL_CHECK = "needs_manual_check"
    BLOCKED_ADDRESS = "blocked_address"  # 사설망·메타데이터 등 내부 주소
    TOO_LARGE = "too_large"
    UNSUPPORTED_CONTENT = "unsupported_content"  # PDF·이미지 등: 업로드로 넣는다
    INVALID_URL = "invalid_url"


class FieldName(StrEnum):
    TITLE = "title"
    COMPANY = "company"
    LOCATION = "location"
    EMPLOYMENT_TYPE = "employment_type"
    CAREER_LEVEL = "career_level"
    DEADLINE = "deadline"  # ISO 8601 날짜/시각, 또는 상시채용이면 "always"
    POSTED_AT = "posted_at"
    RESPONSIBILITIES = "responsibilities"  # 목록
    REQUIREMENTS = "requirements"  # 목록
    PREFERRED = "preferred"  # 목록
    QUESTIONS = "questions"  # 자기소개서 문항: [{"prompt": str, "char_limit": int|None}]
    PROCESS = "process"  # 전형 절차: 목록


LIST_FIELDS = {
    FieldName.RESPONSIBILITIES,
    FieldName.REQUIREMENTS,
    FieldName.PREFERRED,
    FieldName.PROCESS,
}
# 비어 있으면 확인이 필요한 항목. 나머지는 원문에 없을 수 있다.
REVIEW_FIELDS = (FieldName.TITLE, FieldName.COMPANY, FieldName.DEADLINE)

TRACKING_PARAMS = re.compile(
    r"^(utm_.*|gclid|fbclid|msclkid|igshid|mc_[ce]id|_hs.*|ref|ref_src|spm|source|trk|"
    r"trackingId|from|share|si)$",
    re.IGNORECASE,
)


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

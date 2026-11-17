"""회사 조사 규칙 (O13, plan §19.1). 참고 지식 칸: 내 경험이 아니고 문서 근거가 되지 않는다.

- 항목마다 인용이 있어야 한다. 인용은 원문(가져온 페이지·붙여넣은 글) 안에서 코드가 찾는다.
- 기준일(``as_of``)은 원문에 적힌 만큼만: 연도 / 연·월 / 연·월·일 (정밀도를 같이 둔다). 모르면 비운다.
  재무 항목은 기준일이 있어야 한다.
- 오래됨: 기준일(그 기간의 끝)이나, 기준일이 없으면 가져온 날이 ``STALE_DAYS``보다 오래되었으면.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum


class FactKind(StrEnum):
    TALENT = "talent"  # 인재상
    VALUES = "values"  # 핵심 가치
    BUSINESS = "business"  # 주요 사업
    PRODUCT = "product"
    FINANCE = "finance"
    CULTURE = "culture"
    NEWS = "news"
    HIRING = "hiring"  # 채용 절차
    OTHER = "other"


FACT_KINDS = tuple(k.value for k in FactKind)


class FactStatus(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"


class PageStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class CompanyRuleError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


MAX_NAME = 200
MAX_ALIASES = 20
MAX_BODY = 2000
MAX_QUOTE = 2000
# 붙여넣기·가져온 글 상한 (공고 직접 붙여넣기와 같다)
MAX_PAGE_CHARS = 60_000
# 이보다 오래된 기준일·가져온 날의 항목은 "오래됨" (대략 12개월)
STALE_DAYS = 365

# 이름 끝·앞의 법인 표기. 같은 회사를 같은 키로 보기 위해서만 뺀다 (화면 이름은 그대로).
_LEGAL = re.compile(
    r"\(주\)|㈜|\(유\)|\(사\)|\(재\)|주식회사|유한회사|유한책임회사|"
    r"\b(?:co\.?,?\s*ltd\.?|inc\.?|corp\.?|corporation|ltd\.?|llc|gmbh|plc|co\.)(?=\W|$)",
    re.IGNORECASE,
)
_NOT_WORD = re.compile(r"[\W_]+", re.UNICODE)


def name_key(name: str | None) -> str:
    """같은 회사 이름인지 보는 키: 폭·대소문자·공백·기호·법인 표기를 지운다. ``(주)가상회사`` = ``가상회사``."""
    s = unicodedata.normalize("NFKC", name or "").casefold()
    s = _LEGAL.sub(" ", s)
    return _NOT_WORD.sub("", s)


def clean_name(name: str | None) -> str:
    s = " ".join(unicodedata.normalize("NFKC", name or "").split())
    if not s or not name_key(s):
        raise CompanyRuleError("name_required")
    if len(s) > MAX_NAME:
        raise CompanyRuleError("name_too_long")
    return s


def clean_aliases(values: list[str] | None, name: str) -> list[str]:
    out: list[str] = []
    keys = {name_key(name)}
    for raw in values or []:
        s = " ".join(unicodedata.normalize("NFKC", raw or "").split())
        k = name_key(s)
        if not k or k in keys:
            continue
        if len(s) > MAX_NAME:
            raise CompanyRuleError("company_alias_too_long")
        keys.add(k)
        out.append(s)
    if len(out) > MAX_ALIASES:
        raise CompanyRuleError("too_many_company_aliases")
    return out


def clean_digits(value: str | None, length: int, code: str) -> str | None:
    """선택 식별자 (OpenDART 고유번호 8자리, 사업자등록번호 10자리). 하이픈·공백은 뺀다."""
    if value is None or not value.strip():
        return None
    s = re.sub(r"[\s-]", "", value)
    if not re.fullmatch(rf"[0-9]{{{length}}}", s):
        raise CompanyRuleError(code)
    return s


def fact_kind(value: str) -> str:
    try:
        return FactKind(value).value
    except ValueError as exc:
        raise CompanyRuleError("invalid_kind") from exc


@dataclass(frozen=True, slots=True)
class AsOf:
    """기준일: 그 기간의 첫날(``value``)과 정밀도. 연도만 적힌 재무 값은 ``year``."""

    value: date
    precision: str  # year | month | day

    @property
    def end(self) -> date:
        if self.precision == "year":
            return date(self.value.year, 12, 31)
        if self.precision == "month":
            last = calendar.monthrange(self.value.year, self.value.month)[1]
            return date(self.value.year, self.value.month, last)
        return self.value

    def text(self) -> str:
        if self.precision == "year":
            return f"{self.value.year:04d}"
        if self.precision == "month":
            return f"{self.value.year:04d}-{self.value.month:02d}"
        return self.value.isoformat()


_AS_OF = re.compile(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?")


def parse_as_of(value: str | None) -> AsOf | None:
    """``YYYY`` / ``YYYY-MM`` / ``YYYY-MM-DD``. 빈 값은 None, 모양이 틀리면 ``invalid_as_of``."""
    if value is None or not str(value).strip():
        return None
    m = _AS_OF.fullmatch(str(value).strip())
    if not m:
        raise CompanyRuleError("invalid_as_of")
    year = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else None
    day = int(m.group(3)) if m.group(3) else None
    if not 1900 <= year <= 2200:
        raise CompanyRuleError("invalid_as_of")
    try:
        if day is not None:
            return AsOf(date(year, month or 1, day), "day")
        if month is not None:
            return AsOf(date(year, month, 1), "month")
        return AsOf(date(year, 1, 1), "year")
    except ValueError as exc:
        raise CompanyRuleError("invalid_as_of") from exc


def as_of_in_quote(as_of: AsOf, quote: str) -> bool:
    """모델이 낸 기준일의 숫자(연도, 있으면 월·일)가 인용 안에 실제로 있는지 (지어내지 않게)."""
    nums = {int(n) for n in re.findall(r"\d{1,4}", quote or "")}
    if as_of.value.year not in nums:
        return False
    if as_of.precision in ("month", "day") and as_of.value.month not in nums:
        return False
    return not (as_of.precision == "day" and as_of.value.day not in nums)


def as_of_text(value: date | None, precision: str | None) -> str | None:
    if value is None or precision is None:
        return None
    return AsOf(value, precision).text()


def is_stale(
    as_of: date | None, precision: str | None, fetched_at: datetime | None, today: date
) -> bool:
    """기준일이 있으면 그 기간의 끝, 없으면 가져온 날이 ``STALE_DAYS``보다 오래되었는지."""
    limit = today - timedelta(days=STALE_DAYS)
    if as_of is not None and precision is not None:
        return AsOf(as_of, precision).end < limit
    if fetched_at is not None:
        return fetched_at.date() < limit
    return False

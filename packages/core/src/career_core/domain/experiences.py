"""경험의 구조: 종류·상하(한 단계)·월 단위 기간·태그 (plan §20, O19).

- 기간은 월 단위다. 날짜 칸에는 그 달의 1일을 둔다. 원문 ``period_text``는 그대로 두고,
  원문에서 시작·끝 달이 분명할 때만 날짜를 채운다. 모호하면 비운다 (추측하지 않는다).
- 상하는 한 단계뿐이다: 경력 › 그 안의 프로젝트. 자식은 자식을 갖지 못하고, 부모는 자식이 아니다.
- 태그는 기술·주제 낱말. 앞뒤 공백을 지우고 대소문자를 무시해 겹친 것을 뺀다 (처음 쓴 표기를 남긴다).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from career_core.domain.brain import ClaimRuleError


class ExperienceKind(StrEnum):
    WORK = "work"  # 경력 (회사·기관에서 맡은 일)
    PROJECT = "project"  # 프로젝트 (경력 안의 프로젝트, 개인 프로젝트)
    EDUCATION = "education"  # 교육 (학교·부트캠프·과정)
    ACTIVITY = "activity"  # 대외활동 (동아리·봉사·대회)
    OTHER = "other"


EXPERIENCE_KINDS: tuple[str, ...] = tuple(k.value for k in ExperienceKind)

MAX_TAGS = 30
MAX_TAG_CHARS = 40


def experience_kind(value: str) -> str:
    try:
        return ExperienceKind(value).value
    except ValueError as exc:
        raise ClaimRuleError("invalid_experience_kind") from exc


# ---------------------------------------------------------------- 태그


def normalize_tags(values: list[str] | tuple[str, ...] | None, *, strict: bool = True) -> list[str]:
    """앞뒤·안쪽 겹친 공백 정리, 빈 값 빼기, 대소문자 무시 중복 빼기.

    ``strict``면 30개·40자를 넘을 때 거부(``too_many_tags``·``tag_too_long``), 아니면(모델 후보) 넘는 것을 버린다.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or ():
        tag = " ".join(str(raw).split())
        if not tag:
            continue
        if len(tag) > MAX_TAG_CHARS:
            if strict:
                raise ClaimRuleError("tag_too_long")
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
    if len(out) > MAX_TAGS:
        if strict:
            raise ClaimRuleError("too_many_tags")
        out = out[:MAX_TAGS]
    return out


# ---------------------------------------------------------------- 월


_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


def parse_month(value: str | None) -> date | None:
    """``YYYY-MM`` → 그 달 1일. 빈 값은 None, 형식이 틀리면 ``invalid_month``."""
    if value is None or value == "":
        return None
    m = _MONTH.match(value.strip())
    if not m:
        raise ClaimRuleError("invalid_month")
    year, month = int(m.group(1)), int(m.group(2))
    if not (1900 <= year <= 2200 and 1 <= month <= 12):
        raise ClaimRuleError("invalid_month")
    return date(year, month, 1)


def month_text(value: date | None) -> str | None:
    return None if value is None else f"{value.year:04d}-{value.month:02d}"


def check_period(started_on: date | None, ended_on: date | None, ongoing: bool) -> None:
    if ongoing and ended_on is not None:
        raise ClaimRuleError("ongoing_with_end")
    if ended_on is not None and started_on is None:
        raise ClaimRuleError("end_without_start")
    if started_on and ended_on and ended_on < started_on:
        raise ClaimRuleError("end_before_start")


# ---------------------------------------------------------------- 원문 기간 읽기


@dataclass(frozen=True, slots=True)
class Period:
    started_on: date
    ended_on: date | None
    ongoing: bool


# 연·월: 2023.03 / 2023-03 / 2023/3 / 2023. 3. / 2023년 3월. 두 자리 연도·연도만은 받지 않는다.
_YM = r"(\d{4})\s*(?:[./-]\s*|년\s*)(\d{1,2})\s*(?:월|\.)?"
_NOW = r"(현재|지금|재직\s*중|재학\s*중|진행\s*중|활동\s*중|present|now|current|ongoing)"
_SEP = r"\s*(?:[-–—~〜∼]|to|부터)\s*"
# 끝에 붙는 괄호 속 기간 설명 "(1년 6개월)"만 허용한다
_TAIL = r"(?:\s*\([^()]{0,30}\))?\s*\.?"
_RANGE = re.compile(rf"^\s*{_YM}{_SEP}(?:{_YM}|{_NOW})(?:\s*까지)?{_TAIL}$", re.IGNORECASE)


def _ym(year: str, month: str) -> date | None:
    y, m = int(year), int(month)
    if not (1900 <= y <= 2200 and 1 <= m <= 12):
        return None
    return date(y, m, 1)


def parse_period(text: str | None) -> Period | None:
    """원문 기간에서 시작·끝 달이 분명할 때만 읽는다. 아니면 None (비워 둔다).

    받는 모양: ``2023.03 – 2024.08``, ``2023-03 ~ 2024-08``, ``2023년 3월 - 2024년 8월``,
    ``2024.10 – 현재``(진행 중). 연도만·두 자리 연도·달 하나·순서가 거꾸로인 기간·그 밖의 글이 섞인
    기간은 모호하다고 보고 읽지 않는다.
    """
    if not text:
        return None
    m = _RANGE.match(text)
    if not m:
        return None
    start = _ym(m.group(1), m.group(2))
    if start is None:
        return None
    if m.group(5):
        return Period(start, None, True)
    end = _ym(m.group(3), m.group(4))
    if end is None or end < start:
        return None
    return Period(start, end, False)

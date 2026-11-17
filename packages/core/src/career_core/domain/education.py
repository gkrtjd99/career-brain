"""학력 규칙 (c0040, plan §20 "나 › 학력").

한국 지원서의 학력 칸을 따른다: 학교·캠퍼스·전공·복수/부전공·학위 과정·재학 상태·편입·입학/졸업 달·학점(만점).
학점은 만점과 함께만 둔다 (4.5·4.3·4.0·100 만점, 학점 ≤ 만점). 날짜는 달까지만 — 그 달 1일로 둔다.

자격·이수 기록의 ``degree``(학력)는 옛 자리다. ``degree_from_credential``이 그 기록에서 학력 한 줄을 만든다:
값은 기록에 적힌 것만 옮기고(학교 = 발급 기관 또는 이름, 졸업 달 = 취득일의 달), 학위 과정은 이름에 "석사"
같은 낱말이 있을 때만 그것으로, 없으면 학사로 둔다. 사람이 이어서 고친다.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


class EducationRuleError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Degree(StrEnum):
    HIGH_SCHOOL = "high_school"  # 고등학교
    ASSOCIATE = "associate"  # 전문학사
    BACHELOR = "bachelor"  # 학사
    MASTER = "master"  # 석사
    DOCTOR = "doctor"  # 박사


class EducationStatus(StrEnum):
    ENROLLED = "enrolled"  # 재학
    ON_LEAVE = "on_leave"  # 휴학
    EXPECTED = "expected"  # 졸업예정
    GRADUATED = "graduated"  # 졸업
    COMPLETED = "completed"  # 수료
    DROPPED = "dropped"  # 중퇴


class ExtraMajorKind(StrEnum):
    DOUBLE = "double"  # 복수전공
    MINOR = "minor"  # 부전공


GPA_SCALES = (Decimal("4.5"), Decimal("4.3"), Decimal("4.0"), Decimal(100))

MAX_SCHOOL = 100
MAX_CAMPUS = 50
MAX_MAJOR = 100
MAX_NOTE = 1000

TEXT_LIMITS = {
    "school": MAX_SCHOOL,
    "campus": MAX_CAMPUS,
    "major": MAX_MAJOR,
    "extra_major": MAX_MAJOR,
    "note": MAX_NOTE,
}


def clean_text(value: Any, limit: int, code: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EducationRuleError(code)
    s = " ".join(value.split()) if "\n" not in value else value.strip()
    if not s:
        return None
    if len(s) > limit:
        raise EducationRuleError(code)
    return s


def enum_value(cls: type[StrEnum], value: Any, code: str) -> str:
    try:
        return cls(value).value
    except ValueError as exc:
        raise EducationRuleError(code) from exc


_MONTH = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$")


def month(value: Any, code: str = "invalid_month") -> date | None:
    """``YYYY-MM``·``YYYY-MM-DD``·date → 그 달 1일. 빈 값은 None."""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        y, m = value.year, value.month
    elif isinstance(value, str):
        hit = _MONTH.match(value.strip())
        if not hit:
            raise EducationRuleError(code)
        y, m = int(hit.group(1)), int(hit.group(2))
    else:
        raise EducationRuleError(code)
    if not (1900 <= y <= 2100 and 1 <= m <= 12):
        raise EducationRuleError(code)
    return date(y, m, 1)


def gpa_pair(gpa: Any, scale: Any) -> tuple[Decimal | None, Decimal | None]:
    """학점과 만점: 둘 다 있거나 둘 다 없다. 학점은 0 이상 만점 이하, 소수 둘째 자리까지."""
    if gpa in (None, "") and scale in (None, ""):
        return None, None
    if gpa in (None, "") or scale in (None, ""):
        raise EducationRuleError("gpa_needs_scale")
    try:
        g = Decimal(str(gpa))
        sc = Decimal(str(scale))
    except (InvalidOperation, ValueError) as exc:
        raise EducationRuleError("invalid_gpa") from exc
    if sc not in GPA_SCALES:
        raise EducationRuleError("invalid_gpa_scale")
    if not g.is_finite() or g < 0 or g.as_tuple().exponent < -2:  # type: ignore[operator]
        raise EducationRuleError("invalid_gpa")
    if g > sc:
        raise EducationRuleError("gpa_over_scale")
    return g.quantize(Decimal("0.01")), sc.quantize(Decimal("0.01"))


def check_values(values: dict[str, Any]) -> dict[str, Any]:
    """준 칸만 검사·정리해 돌려준다 (PATCH용: 빠진 칸은 그대로 둔다). 칸 사이 규칙은 ``check_row``."""
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key in TEXT_LIMITS:
            out[key] = clean_text(value, TEXT_LIMITS[key], f"invalid_{key}")
        elif key == "degree":
            out[key] = enum_value(Degree, value, "invalid_degree")
        elif key == "status":
            out[key] = enum_value(EducationStatus, value, "invalid_status")
        elif key == "extra_major_kind":
            out[key] = (
                None
                if value in (None, "")
                else enum_value(ExtraMajorKind, value, "invalid_extra_major")
            )
        elif key in ("entered_on", "graduated_on"):
            out[key] = month(value)
        elif key == "transferred":
            out[key] = bool(value)
        elif key in ("gpa", "gpa_scale", "source_id", "experience_id"):
            out[key] = value
        else:
            raise EducationRuleError("unknown_field")
    if "school" in out and out["school"] is None:
        raise EducationRuleError("school_required")
    return out


def check_row(row: Any) -> None:
    """한 행의 칸 사이 규칙 (만들기·고치기 뒤에 한 번). 틀리면 ``EducationRuleError``."""
    if not row.school:
        raise EducationRuleError("school_required")
    if (row.extra_major is None) != (row.extra_major_kind is None):
        raise EducationRuleError("extra_major_needs_kind")
    if row.entered_on and row.graduated_on and row.graduated_on < row.entered_on:
        raise EducationRuleError("graduated_before_entered")
    row.gpa, row.gpa_scale = gpa_pair(row.gpa, row.gpa_scale)


# ---------------------------------------------------------------- 자격·이수의 옛 학력 기록에서

_DEGREE_WORDS: tuple[tuple[Degree, tuple[str, ...]], ...] = (
    (Degree.DOCTOR, ("박사", "doctor", "ph.d", "phd")),
    (Degree.MASTER, ("석사", "master")),
    (Degree.ASSOCIATE, ("전문학사", "associate")),
    (Degree.BACHELOR, ("학사", "bachelor")),
    (Degree.HIGH_SCHOOL, ("고등학교", "고교", "high school")),
)
_SCHOOL_WORD = re.compile(
    r"(대학교|대학원|대학|고등학교|university|college|institute)", re.IGNORECASE
)


def degree_from_credential(name: str, issuer: str | None) -> Degree:
    text = f"{name} {issuer or ''}".casefold()
    for degree, words in _DEGREE_WORDS:
        if any(w in text for w in words):
            return degree
    return Degree.BACHELOR


def school_from_credential(name: str, issuer: str | None) -> str:
    """학교 이름: 발급 기관이 있으면 그것, 없으면 이름에서 학교 낱말이 있는 이름 그대로."""
    for value in (issuer, name):
        if value and _SCHOOL_WORD.search(value):
            return value.strip()[:MAX_SCHOOL]
    return (issuer or name).strip()[:MAX_SCHOOL]


__all__ = [
    "GPA_SCALES",
    "Degree",
    "EducationRuleError",
    "EducationStatus",
    "ExtraMajorKind",
    "check_row",
    "check_values",
    "degree_from_credential",
    "gpa_pair",
    "month",
    "school_from_credential",
]

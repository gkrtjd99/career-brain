"""학습·시험·오답·복습 규칙 (O09).

- 학습은 계획이고 경험이 아니다. 학습 진행·시험 결과·오답은 주장(claim)·경험(experience)을
  만들지 않는다. 사용자가 직접 "경험 후보로 올리기"를 누를 때만 확인 대기(`extracted`) 주장 하나가
  생기고, 확인은 기존 Brain 확인 흐름(approve)으로만 한다.
- 공고 요건은 사용자가 확정한 공고(`saved`)의 값만 쓴다. 요건 문장은 그 시점 그대로 복사해 둔다.
- 복습 일정은 SM-2를 줄인 결정적 규칙이다. 같은 입력이면 같은 날짜가 나온다 (정수 연산만).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class StudyRuleError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RequirementField(StrEnum):
    """학습 항목·시험으로 이어지는 공고 항목 (job_postings.fields 의 목록 항목)."""

    REQUIREMENTS = "requirements"
    PREFERRED = "preferred"
    PROCESS = "process"


class ItemKind(StrEnum):
    SKILL = "skill"  # 기술·도구
    TOPIC = "topic"  # 개념·지식
    OTHER = "other"


class ItemStatus(StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    DROPPED = "dropped"


class AssessmentKind(StrEnum):
    CODING_TEST = "coding_test"
    WRITTEN_TEST = "written_test"  # 필기·인적성·직무 시험
    ASSIGNMENT = "assignment"  # 과제 전형
    INTERVIEW = "interview"
    CERTIFICATION = "certification"
    OTHER = "other"


# 공고 전형 절차에서 "시험 준비 항목"을 만들 종류 (서류·면접은 학습 대상으로 자동 생성하지 않는다).
TEST_KINDS = frozenset(
    {AssessmentKind.CODING_TEST, AssessmentKind.WRITTEN_TEST, AssessmentKind.ASSIGNMENT}
)


class AssessmentStatus(StrEnum):
    PLANNED = "planned"
    DONE = "done"
    CANCELLED = "cancelled"


class AttemptResult(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"  # 채점 결과를 모름 (실제 전형 결과 미발표 등)


class MistakeReason(StrEnum):
    CONCEPT = "concept"  # 개념을 몰랐다
    CARELESS = "careless"  # 실수
    TIME = "time"  # 시간 부족
    MISREAD = "misread"  # 문제를 잘못 읽음
    UNKNOWN = "unknown"


class MistakeStatus(StrEnum):
    OPEN = "open"  # 복습 대상
    RESOLVED = "resolved"  # 사용자가 다 익혔다고 표시


class Coverage(StrEnum):
    """공고 요건 하나가 지금 어디까지 왔는가. 위에서부터 먼저 맞는 것."""

    EVIDENCED = "evidenced"  # 확정된 주장이 연결됨 (입증함)
    CLAIM_PENDING = "claim_pending"  # 연결된 주장이 확인 대기
    STUDIED = "studied"  # 학습 완료, 경험 근거는 없음 (학습함 ≠ 입증함)
    STUDYING = "studying"  # 학습 계획·진행 중
    UNCOVERED = "uncovered"  # 아무것도 없음 → 학습 보완 또는 지원 재검토


# ---------------------------------------------------------------- 전형 절차 나누기

_HEADER = re.compile(r"^\s*(?:\[[^\]]*\]|【[^】]*】|<[^>]*>)\s*")
_NUMBERING = re.compile(r"^\s*(?:\d+\s*[.)]\s*|[①-⑳]\s*|\d+차\s*)")
# 공백으로 둘러싼 하이픈·대시만 구분자로 본다 ("take-home" 같은 낱말은 자르지 않는다).
_STEP_SPLIT = re.compile(r"\s+[-–—~]\s+|\s*(?:->|=>|→|➝|⇒|>|＞|›|»|·|,|\||/)\s*")

_KIND_KEYWORDS: tuple[tuple[AssessmentKind, tuple[str, ...]], ...] = (
    (
        AssessmentKind.CODING_TEST,
        ("코딩테스트", "코딩 테스트", "코테", "알고리즘", "라이브코딩", "라이브 코딩", "coding"),
    ),
    (AssessmentKind.ASSIGNMENT, ("과제", "assignment", "take-home", "takehome")),
    (
        AssessmentKind.WRITTEN_TEST,
        ("필기", "인적성", "적성검사", "적성 검사", "논술", "직무시험", "직무 시험", "written"),
    ),
    (AssessmentKind.CERTIFICATION, ("자격증", "certification")),
    (AssessmentKind.INTERVIEW, ("면접", "interview")),
)


def split_process(text: str) -> list[str]:
    """전형 절차 한 줄을 단계로 나눈다. 예: "[전형 절차] 서류 - 코딩테스트 - 면접"."""
    body = _HEADER.sub("", text or "")
    steps = []
    for part in _STEP_SPLIT.split(body):
        step = _NUMBERING.sub("", part).strip(" \t:：.")
        if step and step not in steps:
            steps.append(step)
    return steps


def classify_step(step: str) -> AssessmentKind | None:
    """단계 이름에서 시험 종류를 고른다. 모르면 None (추측하지 않는다)."""
    low = step.lower()
    for kind, words in _KIND_KEYWORDS:
        if any(w in low for w in words):
            return kind
    if "시험" in step or "테스트" in step or "test" in low:
        return AssessmentKind.WRITTEN_TEST
    return None


# ---------------------------------------------------------------- 복습 일정 (SM-2 축약)


class ReviewGrade(StrEnum):
    AGAIN = "again"  # 틀렸다 / 기억 안 남
    HARD = "hard"  # 겨우 맞혔다
    GOOD = "good"  # 맞혔다
    EASY = "easy"  # 쉽게 맞혔다


START_EASE = 2500  # 배율 2.5 (천분율)
MIN_EASE = 1300
MAX_INTERVAL_DAYS = 365
FIRST_DUE_DAYS = 1  # 오답을 적은 다음 날 첫 복습


@dataclass(frozen=True, slots=True)
class ReviewState:
    repetitions: int = 0
    interval_days: int = 0
    ease: int = START_EASE


def first_due(created_at: datetime) -> datetime:
    return created_at + timedelta(days=FIRST_DUE_DAYS)


def next_review(state: ReviewState, grade: ReviewGrade) -> ReviewState:
    """다음 간격을 정한다. 정수 연산만 쓰므로 결정적이다.

    again: 처음부터(1일), 배율 -0.2 / hard: 1.2배, 배율 -0.15 / good: 1 → 3 → 간격×배율
    easy: 3 → 간격×배율×1.3, 배율 +0.15. 맞힌 경우 간격은 최소 하루씩 늘어난다.
    """
    reps, interval, ease = state.repetitions, state.interval_days, state.ease
    if grade == ReviewGrade.AGAIN:
        return ReviewState(0, 1, max(MIN_EASE, ease - 200))
    if grade == ReviewGrade.HARD:
        interval = 1 if reps == 0 else max(interval + 1, interval * 1200 // 1000)
        ease = max(MIN_EASE, ease - 150)
    elif grade == ReviewGrade.GOOD:
        if reps == 0:
            interval = 1
        elif reps == 1:
            interval = max(3, interval + 1)
        else:
            interval = max(interval + 1, interval * ease // 1000)
    else:  # EASY
        interval = 3 if reps == 0 else max(interval + 1, interval * ease * 1300 // 1_000_000)
        ease = ease + 150
    return ReviewState(reps + 1, min(interval, MAX_INTERVAL_DAYS), ease)


def due_after(reviewed_at: datetime, state: ReviewState) -> datetime:
    return reviewed_at + timedelta(days=state.interval_days)

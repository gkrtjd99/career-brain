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
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from career_core.domain.korean import TechAliases


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
    # 다른 항목으로 합쳐짐 (`merged_into_id`). 기록(응시·후보)은 그대로 두고 합친 항목에서 함께 보인다.
    MERGED = "merged"


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


# ---------------------------------------------------------------- 표시 시간대 (복습 마감·D-day)

# 공간 설정이 없을 때의 표시 시간대. 복습 마감은 이 시간대의 "오늘 끝"이다.
DEFAULT_TIMEZONE = "Asia/Seoul"


def zone(name: str | None) -> ZoneInfo:
    """IANA 시간대 이름 → ZoneInfo. 모르는 이름이면 `invalid_timezone`."""
    name = (name or "").strip()
    if not name or len(name) > 64 or name.startswith(("/", ".")) or ".." in name:
        raise StudyRuleError("invalid_timezone")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise StudyRuleError("invalid_timezone") from exc


def local_date(at: datetime, tz: ZoneInfo) -> date:
    return at.astimezone(tz).date()


def day_start(now: datetime, tz: ZoneInfo) -> datetime:
    """`now`가 속한 표시 시간대 하루의 시작 (UTC로)."""
    return datetime.combine(local_date(now, tz), time(0), tzinfo=tz).astimezone(UTC)


def day_end(now: datetime, tz: ZoneInfo) -> datetime:
    """`now`가 속한 표시 시간대 하루의 끝 = 다음 날 0시 (UTC로). 마감 비교는 `< day_end`."""
    tomorrow = local_date(now, tz) + timedelta(days=1)
    return datetime.combine(tomorrow, time(0), tzinfo=tz).astimezone(UTC)


def d_day(when: datetime, now: datetime, tz: ZoneInfo) -> int:
    """표시 시간대 날짜 기준 남은 날 수. 0 = 오늘(D-day), 1 = 내일(D-1), 음수 = 지남."""
    return (local_date(when, tz) - local_date(now, tz)).days


# ---------------------------------------------------------------- 요건 낱말 묶기 (빈도·검색어)
#
# 저장 공고들 사이의 요건 빈도("저장 공고 N개 중 M개가 Docker 요구")와 요건 → 주장 후보 검색어에 쓴다.
# 가벼운 규칙이다: 영문 기술 낱말 + 한글 표기 별칭 + 짧은 한글 기술 낱말 목록 + "경력 N년 이상".
# 형태소 분석은 하지 않는다. 낱말이 하나도 없는 줄은 정규화한 문장 전체로 센다.

_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[.+#][A-Za-z0-9]+)*[+#]*")
_YEARS = re.compile(r"(\d{1,2})\s*년\s*(?:이상|↑)")

# 소문자 표기 → 대표 키 (같은 기술의 다른 표기)
_ALIASES = {
    "golang": "go",
    "k8s": "kubernetes",
    "postgres": "postgresql",
    "js": "javascript",
    "ts": "typescript",
    "nodejs": "node.js",
    "node": "node.js",
    "reactjs": "react",
    "react.js": "react",
    "vuejs": "vue",
    "vue.js": "vue",
    "springboot": "spring",
    "ci": "ci/cd",
    "cd": "ci/cd",
}
# 한글 표기 → 대표 키 (긴 것부터 맞춘다: "자바스크립트"가 "자바"보다 먼저)
_KO_ALIASES = {
    "자바스크립트": "javascript",
    "타입스크립트": "typescript",
    "파이썬": "python",
    "자바": "java",
    "코틀린": "kotlin",
    "스위프트": "swift",
    "도커": "docker",
    "쿠버네티스": "kubernetes",
    "리눅스": "linux",
    "깃허브": "github",
    "레디스": "redis",
    "카프카": "kafka",
    "스프링": "spring",
    "리액트": "react",
    "장고": "django",
    "파이토치": "pytorch",
    "텐서플로": "tensorflow",
    "엘라스틱서치": "elasticsearch",
    "몽고디비": "mongodb",
}
# 한글 기술·지식 낱말 (그대로 묶는다)
_KO_TERMS = (
    "자료구조",
    "알고리즘",
    "운영체제",
    "네트워크",
    "데이터베이스",
    "클라우드",
    "머신러닝",
    "딥러닝",
    "데이터 분석",
    "테스트 코드",
    "대용량 트래픽",
    "분산 시스템",
    "마이크로서비스",
    "보안",
    "협업",
    "커뮤니케이션",
    "영어",
    "컴퓨터공학",
    "백엔드",
    "프론트엔드",
)
_LABELS = {
    "python": "Python",
    "go": "Go",
    "java": "Java",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "kotlin": "Kotlin",
    "swift": "Swift",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "linux": "Linux",
    "git": "Git",
    "github": "GitHub",
    "redis": "Redis",
    "kafka": "Kafka",
    "spring": "Spring",
    "react": "React",
    "vue": "Vue",
    "django": "Django",
    "fastapi": "FastAPI",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "elasticsearch": "Elasticsearch",
    "mongodb": "MongoDB",
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
    "node.js": "Node.js",
    "ci/cd": "CI/CD",
    "aws": "AWS",
    "gcp": "GCP",
    "sql": "SQL",
    "api": "API",
    "rest": "REST",
    "graphql": "GraphQL",
    "c++": "C++",
    "c#": "C#",
}
_EN_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "or",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "with",
        "the",
        "as",
        "by",
        "from",
        "is",
        "are",
        "be",
        "using",
        "use",
        "used",
        "experience",
        "experienced",
        "knowledge",
        "skill",
        "skills",
        "year",
        "years",
        "plus",
        "etc",
        "understanding",
        "ability",
        "able",
        "familiarity",
        "familiar",
        "strong",
        "good",
        "basic",
        "basics",
        "working",
        "hands",
        "related",
        "degree",
        "preferred",
        "required",
        "requirement",
        "level",
        "including",
        "such",
        "least",
        "more",
        "than",
        "proficiency",
        "proficient",
        "practical",
        "solid",
        "nice",
        "have",
        "has",
    ]
)
# 요건 문장에 흔한 말 (검색어에서 뺀다)
_KO_STOP = frozenset(
    [
        "경험",
        "경험자",
        "이해",
        "이해도",
        "사용",
        "사용한",
        "활용",
        "활용한",
        "능력",
        "역량",
        "가능",
        "가능한",
        "또는",
        "및",
        "등",
        "관련",
        "관련된",
        "있는",
        "있으신",
        "분",
        "우대",
        "우대합니다",
        "이상",
        "보유",
        "보유자",
        "기본",
        "기본적인",
        "지식",
        "숙련",
        "숙련된",
        "능숙",
        "능숙한",
        "업무",
        "해본",
        "갖춘",
        "필수",
        "이용",
        "다룰",
        "있음",
        "있다",
    ]
)
_KO_PARTICLES = (
    "으로",
    "에서",
    "에게",
    "을",
    "를",
    "이",
    "가",
    "은",
    "는",
    "의",
    "에",
    "와",
    "과",
    "로",
)


@dataclass(frozen=True, slots=True)
class RequirementTerm:
    key: str  # 묶는 키 (소문자·대표 표기)
    label: str  # 화면에 보일 이름


def normalize_line(text: str) -> str:
    """문장 전체 비교용: 공백·대소문자·끝 문장부호 정리."""
    return " ".join((text or "").split()).strip(" .,;·").lower()


@dataclass(frozen=True, slots=True)
class _TermMaps:
    latin: dict[str, str]  # 소문자 영문 낱말 → 대표 키
    phrases: tuple[tuple[str, str], ...]  # (소문자 표기, 대표 키): 한글·여러 낱말 표기, 긴 것부터
    labels: dict[str, str]


_BUILTIN_MAPS = _TermMaps(
    dict(_ALIASES),
    tuple((ko, _KO_ALIASES[ko]) for ko in sorted(_KO_ALIASES, key=len, reverse=True)),
    dict(_LABELS),
)


def _term_maps(aliases: TechAliases | None) -> _TermMaps:
    """기본 묶기 표 + 공간 사용자 별칭(c0017). 사용자 항목은 대표 표기 그대로 화면 이름이 된다."""
    entries = aliases.entries if aliases is not None else ()
    if not entries:
        return _BUILTIN_MAPS
    latin = dict(_ALIASES)
    phrases = dict(_KO_ALIASES)
    labels = dict(_LABELS)
    for e in entries:
        key = e.key
        labels[key] = e.term
        for spelling in (e.term, *e.aliases):
            low = " ".join(spelling.split()).lower()
            if _LATIN.fullmatch(spelling.strip()):
                latin[low] = key
            else:
                phrases[low] = key
    ordered = tuple(sorted(phrases.items(), key=lambda kv: len(kv[0]), reverse=True))
    return _TermMaps(latin, ordered, labels)


def requirement_terms(text: str, aliases: TechAliases | None = None) -> list[RequirementTerm]:
    """요건 한 줄의 기술 낱말 (중복 없음, 처음 나온 순서). 없으면 빈 목록.

    `aliases`의 사용자 항목(공간 설정)을 기본 묶기 표 위에 합친다.
    """
    text = text or ""
    maps = _term_maps(aliases)
    found: list[RequirementTerm] = []
    seen: set[str] = set()

    def add(key: str, label: str) -> None:
        if key not in seen:
            seen.add(key)
            found.append(RequirementTerm(key, label))

    for m in _YEARS.finditer(text):
        n = int(m.group(1))
        add(f"years:{n}", f"경력 {n}년 이상")
    # 한글·여러 낱말 표기를 먼저 지운다 ("아마존 웹 서비스", "자바스크립트"가 "자바"보다 먼저)
    rest = text
    phrase_hits: list[tuple[int, str]] = []
    for phrase, key in maps.phrases:
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        m = pattern.search(rest)
        if m:
            phrase_hits.append((m.start(), key))
            rest = pattern.sub(" ", rest)
    for raw in _LATIN.findall(rest):
        low = raw.lower()
        if low in _EN_STOP or (len(low) == 1 and low not in ("c", "r")):
            continue
        key = maps.latin.get(low, low)
        add(key, maps.labels.get(key, raw))
    for _, key in sorted(phrase_hits):
        add(key, maps.labels.get(key, key))
    for ko in _KO_TERMS:
        if ko in rest:
            add(ko, ko)
    return found


def _strip_particle(word: str) -> str:
    for p in _KO_PARTICLES:
        if len(word) > len(p) + 1 and word.endswith(p):
            return word[: -len(p)]
    return word


def requirement_query(text: str) -> str:
    """요건 문장 → 주장 검색어. 흔한 요건 말("경험", "또는", "이상")을 빼고 낱말만 남긴다."""
    words = []
    for raw in re.split(r"[\s,.;:!?()\[\]{}\"'`/·]+", text or ""):
        w = _strip_particle(raw.strip())
        if len(w) < 2 and w.lower() not in ("c", "r"):
            continue
        if w in _KO_STOP or w.lower() in _EN_STOP or re.fullmatch(r"\d+(?:년|개월)?", w):
            continue
        words.append(w)
    return " ".join(words) or " ".join((text or "").split())

"""개인 사실(경험·주장·근거)과 인터뷰의 규칙.

- AI가 뽑은 주장은 `extracted`(후보)다. 사람이 확인하기 전에는 사실이 아니다.
- 근거는 두 가지뿐이다: 사용자가 올린 원문의 위치(source_span), 사용자가 직접 쓴 인터뷰 답변.
  AI가 쓴 문장은 근거가 될 수 없다 (DB 제약으로도 막는다).
- 확인할 때 팀 성과인지 본인 기여인지 정해야 한다.
- 원문 위치가 연결된 주장을 확인하면 `evidence_supported`, 본인 진술뿐이면 `self_confirmed`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ClaimStatus(StrEnum):
    EXTRACTED = "extracted"
    SELF_CONFIRMED = "self_confirmed"
    EVIDENCE_SUPPORTED = "evidence_supported"
    DISPUTED = "disputed"
    RETIRED = "retired"


CONFIRMED_STATUSES = frozenset({ClaimStatus.SELF_CONFIRMED, ClaimStatus.EVIDENCE_SUPPORTED})


class ClaimKind(StrEnum):
    RESPONSIBILITY = "responsibility"  # 맡은 일
    ACTION = "action"  # 직접 한 일
    RESULT = "result"  # 결과 (수치 포함)
    DECISION = "decision"  # 선택과 이유, 버린 대안
    DIFFICULTY = "difficulty"  # 어려웠던 점과 해결
    SKILL = "skill"  # 사용한 기술
    OTHER = "other"


class Contribution(StrEnum):
    PERSONAL = "personal"  # 본인이 한 것
    TEAM = "team"  # 팀 성과 (본인 몫이 따로 있으면 별도 주장으로)
    UNKNOWN = "unknown"


class ClaimOrigin(StrEnum):
    AI_EXTRACTION = "ai_extraction"
    INTERVIEW = "interview"
    USER = "user"


class EvidenceKind(StrEnum):
    SOURCE_SPAN = "source_span"
    INTERVIEW_ANSWER = "interview_answer"


class ExtractionStatus(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"
    PARTIAL = "partial"  # 입력이 길어 일부 블록만 보냈다
    FAILED = "failed"


class QuestionFocus(StrEnum):
    CONTEXT = "context"
    PERSONAL_CONTRIBUTION = "personal_contribution"
    METRIC = "metric"
    DECISION = "decision"
    DIFFICULTY = "difficulty"
    TEAM_VS_PERSONAL = "team_vs_personal"


class ClaimRuleError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def confirmed_status(*, has_source_span: bool, contribution: str) -> ClaimStatus:
    if contribution not in (Contribution.PERSONAL, Contribution.TEAM):
        raise ClaimRuleError("contribution_required")
    return ClaimStatus.EVIDENCE_SUPPORTED if has_source_span else ClaimStatus.SELF_CONFIRMED


@dataclass(frozen=True, slots=True)
class TemplateQuestion:
    focus: QuestionFocus
    prompt: str


# 모델 없이도 인터뷰를 시작할 수 있는 기본 질문. 답을 대신 쓰지 않고 재료를 묻는다.
TEMPLATE_QUESTIONS: tuple[TemplateQuestion, ...] = (
    TemplateQuestion(
        QuestionFocus.CONTEXT,
        "어떤 상황에서 시작된 일인가요? 팀 규모, 기간, 당시 문제를 적어 주세요.",
    ),
    TemplateQuestion(
        QuestionFocus.PERSONAL_CONTRIBUTION,
        "그중 본인이 직접 한 일은 무엇인가요? 다른 사람이 한 일과 구분해 주세요.",
    ),
    TemplateQuestion(
        QuestionFocus.METRIC,
        "결과를 숫자로 말할 수 있나요? 무엇을 언제 어떻게 측정했는지도 적어 주세요.",
    ),
    TemplateQuestion(
        QuestionFocus.DECISION,
        "중요한 선택이 있었나요? 고른 방법, 버린 대안, 그렇게 판단한 이유를 적어 주세요.",
    ),
    TemplateQuestion(
        QuestionFocus.DIFFICULTY,
        "가장 어려웠던 지점은 무엇이었고 어떻게 풀었나요? 못 푼 부분이 있으면 그것도 적어 주세요.",
    ),
    TemplateQuestion(
        QuestionFocus.TEAM_VS_PERSONAL,
        "이 성과에서 팀 전체의 몫과 본인의 몫을 나눈다면 어떻게 나눌 수 있나요?",
    ),
)

"""원본 자료·파싱 상태·용도."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DataClass(StrEnum):
    """외부 전송 등급. restricted는 어떤 제공자 설정으로도 외부에 보내지 않는다."""

    PUBLIC = "public"
    PRIVATE = "private"
    RESTRICTED = "restricted"


class ParseStatus(StrEnum):
    PENDING = "pending"
    WAITING_PROVIDER = "waiting_provider"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


TERMINAL_PARSE_STATUSES = frozenset({ParseStatus.COMPLETE, ParseStatus.PARTIAL, ParseStatus.FAILED})

# 파싱 파이프라인이 바뀌어 재처리가 필요할 때 올린다. 같은 (버전, 파이프라인)은 다시 처리하지 않는다.
PIPELINE_VERSION = 1


class SourcePurpose(StrEnum):
    """원본의 용도 (c0032, plan §20). 용도가 처리를 정한다.

    - ``experience``: 내 경험 자료. 경험·주장 후보를 뽑는다 (c0032 전의 모든 원본).
    - ``proof``: 증빙 서류 (자격증·성적표·증명서 사본·증명사진). 주장을 뽑지 않고, 자료 등급은 늘
      ``restricted``라 외부 모델·외부 임베딩·외부 OCR로 보내지 않는다. 원본 바이트는 그대로 보관한다.
      색인하지 않고, 검색·MCP 결과에 본문이 나오지 않는다.
    - ``reference``: 남의 글·참고 자료 (회사 자료, 다른 사람의 자기소개서, 면접 후기). 주장을 뽑지 않고
      근거로 연결할 수 없다. 색인은 하지만 기본 검색에는 나오지 않는다 (``include_reference``).
    """

    EXPERIENCE = "experience"
    PROOF = "proof"
    REFERENCE = "reference"


# 용도마다 강제하는 자료 등급 (없으면 올린 사람이 고른 등급)
PURPOSE_DATA_CLASS: dict[SourcePurpose, DataClass] = {SourcePurpose.PROOF: DataClass.RESTRICTED}


def source_purpose(value: str | None) -> SourcePurpose:
    """``None``·빈 값은 ``experience`` (c0032 전 API와 같은 뜻). 모르는 값은 ``ValueError``."""
    return SourcePurpose(value) if value else SourcePurpose.EXPERIENCE


# ---------------------------------------------------------------- 용도 제안
#
# 모델 없이 파일 이름·형식·앞부분 글로 제안한다. 사람이 늘 확정한다.
# 파일 이름은 사용자가 붙인 것이라 낱말이 넓어도 된다. 본문은 이력서에도 "자격증"·"TOEIC"·"수료"가
# 흔하므로 서류 자체임을 말하는 낱말("증명서", "수료증", "면접 후기")만 본다.

_PROOF_NAME = (
    "자격증",
    "증명서",
    "증명사진",
    "증명 사진",
    "성적표",
    "성적증명",
    "성적 증명",
    "수료증",
    "졸업증명",
    "졸업 증명",
    "재학증명",
    "재직증명",
    "재직 증명",
    "경력증명",
    "경력 증명",
    "합격증",
    "여권",
    "certificate",
    "transcript",
    "passport",
    "toeic",
    "토익",
    "opic",
    "오픽",
    "toefl",
    "토플",
    "teps",
    "텝스",
    "ielts",
    "hsk",
    "jlpt",
    "jpt",
)
_REFERENCE_NAME = (
    "합격 자소서",
    "합격자소서",
    "합격 자기소개서",
    "합격자기소개서",
    "자소서 모음",
    "자소서모음",
    "자기소개서 모음",
    "면접 후기",
    "면접후기",
    "합격 후기",
    "합격후기",
    "남의",
    "타인",
    "다른 사람",
    "참고",
    "예시",
    "샘플",
    "sample",
    "기업분석",
    "기업 분석",
    "회사소개",
    "회사 소개",
    "직무 소개",
    "직무소개",
)
_PROOF_TEXT = (
    "증명서",
    "증명합니다",
    "수료증",
    "성적표",
    "성적증명",
    "합격증",
    "score report",
    "certificate of",
    "this is to certify",
)
_REFERENCE_TEXT = (
    "합격 자소서",
    "합격자소서",
    "합격 자기소개서",
    "자소서 모음",
    "자기소개서 모음",
    "면접 후기",
    "면접후기",
)
SUGGEST_TEXT_HEAD = 2000  # 앞부분 글은 이만큼만 본다


@dataclass(frozen=True, slots=True)
class PurposeSuggestion:
    purpose: SourcePurpose
    # 제안의 이유: 걸린 낱말(소문자) 또는 "image"(사진 파일). 기본값(experience)이면 None.
    matched: str | None = None


def _norm(value: str | None) -> str:
    return " ".join((value or "").casefold().replace("_", " ").replace("-", " ").split())


def _find(haystack: str, words: tuple[str, ...]) -> str | None:
    return next((w for w in words if w in haystack), None)


def suggest_purpose(
    filename: str | None, mime: str | None = None, text_head: str | None = None
) -> PurposeSuggestion:
    """용도 제안. 순서:

    1. 파일 이름에 증빙 낱말 → proof, 참고 낱말 → reference. 둘 다 있으면 proof
       (외부로 덜 나가는 쪽을 고른다).
    2. 사진 파일(image/*) → proof (증명사진·자격증 사진이 흔하다. 경험 자료 캡처면 사람이 바꾼다).
    3. 앞부분 글에 서류 낱말 → proof, 참고 글 낱말 → reference.
    4. 나머지 → experience.
    """
    name = _norm(filename)
    if hit := _find(name, _PROOF_NAME):
        return PurposeSuggestion(SourcePurpose.PROOF, hit)
    if hit := _find(name, _REFERENCE_NAME):
        return PurposeSuggestion(SourcePurpose.REFERENCE, hit)
    if (mime or "").casefold().startswith("image/"):
        return PurposeSuggestion(SourcePurpose.PROOF, "image")
    head = _norm((text_head or "")[:SUGGEST_TEXT_HEAD])
    if hit := _find(head, _PROOF_TEXT):
        return PurposeSuggestion(SourcePurpose.PROOF, hit)
    if hit := _find(head, _REFERENCE_TEXT):
        return PurposeSuggestion(SourcePurpose.REFERENCE, hit)
    return PurposeSuggestion(SourcePurpose.EXPERIENCE)

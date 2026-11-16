"""코어 DB: SQLAlchemy 모델, 공간 문맥 세션, RLS 도우미, migration 실행기."""

from career_core.db.base import CORE_SCHEMA, Base
from career_core.db.models import (
    Actor,
    Claim,
    ClaimEvidence,
    DocumentBlock,
    Embedding,
    Experience,
    ExtractionRun,
    InterviewAnswer,
    InterviewQuestion,
    InterviewSession,
    Job,
    JobFetchRun,
    JobPosting,
    JobPostingVersion,
    Membership,
    ModelCall,
    ParseRun,
    Source,
    SourceSpan,
    SourceVersion,
    Workspace,
)
from career_core.db.session import Database, normalize_url

__all__ = [
    "CORE_SCHEMA",
    "Actor",
    "Base",
    "Claim",
    "ClaimEvidence",
    "Database",
    "DocumentBlock",
    "Embedding",
    "Experience",
    "ExtractionRun",
    "InterviewAnswer",
    "InterviewQuestion",
    "InterviewSession",
    "Job",
    "JobFetchRun",
    "JobPosting",
    "JobPostingVersion",
    "Membership",
    "ModelCall",
    "ParseRun",
    "Source",
    "SourceSpan",
    "SourceVersion",
    "Workspace",
    "normalize_url",
]

# O09 학습
from career_core.db.models import (
    StudyAssessment,
    StudyAttempt,
    StudyClaimCandidate,
    StudyItem,
    StudyMistake,
    StudyRequirement,
    StudyRequirementClaim,
    StudyReview,
    StudySession,
)

__all__ += [
    "StudyAssessment",
    "StudyAttempt",
    "StudyClaimCandidate",
    "StudyItem",
    "StudyMistake",
    "StudyRequirement",
    "StudyRequirementClaim",
    "StudyReview",
    "StudySession",
]

# O08 실행·승인
from career_core.db.models import Approval, Run, RunEvent

__all__ += ["Approval", "Run", "RunEvent"]

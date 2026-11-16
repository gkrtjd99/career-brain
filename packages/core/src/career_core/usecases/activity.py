"""최근 활동: 주장·공고·가져오기·문서 저장·학습 응시를 시각 순으로 합친 목록.

읽기 전용이다. 따로 기록하는 이벤트 테이블은 없고, 각 테이블의 시각 열을 그대로 모은다.
그래서 "무엇이 언제 생겼다/확인됐다"만 알 수 있고 지워진 것은 나오지 않는다.
문서 저장은 지원 문서와 공용 문서(``refs.shared``, ``application_id`` 없음) 모두 나온다.
보는 사람에게 권한이 없는 종류는 `kinds`에서 빼고 부른다 (경로가 정한다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from career_core.db import Claim, Database, Experience, JobFetchRun, JobPosting
from career_core.db.models import (
    Application,
    ApplicationDocument,
    DocumentRevision,
    StudyAssessment,
    StudyAttempt,
    StudyItem,
)
from career_core.domain.postings import FieldName
from career_core.ports import ActorContext

KINDS = ("claim", "posting", "fetch_run", "document_revision", "study_attempt")
MAX_LIMIT = 100
TITLE_CHARS = 120
SHARED_LABEL = "공용 문서"


@dataclass(frozen=True, slots=True)
class ActivityItem:
    kind: str  # KINDS 중 하나
    action: str  # created | confirmed | saved | fetched | revised | attempted
    id: UUID
    at: datetime
    title: str
    status: str | None = None
    detail: str | None = None
    # 화면이 여는 곳: experience_id, posting_id, application_id, document_id, study_item_id, ...
    refs: dict[str, Any] = field(default_factory=dict)


def _cut(text: str | None) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= TITLE_CHARS else text[: TITLE_CHARS - 1] + "…"


def _posting_name(p: JobPosting) -> str:
    f = p.fields or {}
    name = f.get(FieldName.TITLE)
    company = f.get(FieldName.COMPANY)
    if name and company:
        return f"{company} · {name}"
    return name or company or p.canonical_url


def _ref(**kw: Any) -> dict[str, Any]:
    return {k: str(v) for k, v in kw.items() if v is not None}


async def recent_activity(
    db: Database,
    actor: ActorContext,
    *,
    limit: int = 30,
    before: datetime | None = None,
    kinds: tuple[str, ...] = KINDS,
) -> list[ActivityItem]:
    """최근 활동 (새 것부터). `before`보다 이전만 — 더 보기용 커서."""
    limit = max(1, min(limit, MAX_LIMIT))
    kinds = tuple(k for k in kinds if k in KINDS)
    items: list[ActivityItem] = []

    def older(col):
        return col < before if before is not None else col.isnot(None)

    async with db.workspace_session(actor.workspace_id) as s:
        if "claim" in kinds:
            rows = (
                await s.execute(
                    select(Claim, Experience.title)
                    .join(Experience, Experience.id == Claim.experience_id)
                    .where(older(Claim.created_at))
                    .order_by(Claim.created_at.desc())
                    .limit(limit)
                )
            ).all()
            for c, exp in rows:
                items.append(
                    ActivityItem(
                        "claim",
                        "created",
                        c.id,
                        c.created_at,
                        _cut(c.statement),
                        c.status,
                        exp,
                        _ref(experience_id=c.experience_id),
                    )
                )
            rows = (
                await s.execute(
                    select(Claim, Experience.title)
                    .join(Experience, Experience.id == Claim.experience_id)
                    .where(Claim.confirmed_at.isnot(None), older(Claim.confirmed_at))
                    .order_by(Claim.confirmed_at.desc())
                    .limit(limit)
                )
            ).all()
            for c, exp in rows:
                items.append(
                    ActivityItem(
                        "claim",
                        "confirmed",
                        c.id,
                        c.confirmed_at,
                        _cut(c.statement),
                        c.status,
                        exp,
                        _ref(experience_id=c.experience_id),
                    )
                )

        postings: dict[UUID, JobPosting] = {}
        if "posting" in kinds:
            for p in (
                await s.execute(
                    select(JobPosting)
                    .where(older(JobPosting.created_at))
                    .order_by(JobPosting.created_at.desc())
                    .limit(limit)
                )
            ).scalars():
                postings[p.id] = p
                items.append(
                    ActivityItem(
                        "posting",
                        "created",
                        p.id,
                        p.created_at,
                        _cut(_posting_name(p)),
                        p.status,
                        None,
                        _ref(posting_id=p.id),
                    )
                )
            for p in (
                await s.execute(
                    select(JobPosting)
                    .where(JobPosting.saved_at.isnot(None), older(JobPosting.saved_at))
                    .order_by(JobPosting.saved_at.desc())
                    .limit(limit)
                )
            ).scalars():
                postings[p.id] = p
                items.append(
                    ActivityItem(
                        "posting",
                        "saved",
                        p.id,
                        p.saved_at,
                        _cut(_posting_name(p)),
                        p.status,
                        None,
                        _ref(posting_id=p.id),
                    )
                )

        if "fetch_run" in kinds:
            rows = (
                await s.execute(
                    select(JobFetchRun, JobPosting)
                    .join(JobPosting, JobPosting.id == JobFetchRun.posting_id)
                    .where(older(JobFetchRun.created_at))
                    .order_by(JobFetchRun.created_at.desc())
                    .limit(limit)
                )
            ).all()
            for r, p in rows:
                items.append(
                    ActivityItem(
                        "fetch_run",
                        "fetched",
                        r.id,
                        r.created_at,
                        _cut(_posting_name(p)),
                        r.status,
                        r.error_code,
                        _ref(posting_id=p.id, version_id=r.version_id),
                    )
                )

        if "document_revision" in kinds:
            rows = (
                await s.execute(
                    select(DocumentRevision, ApplicationDocument, Application.title)
                    .join(
                        ApplicationDocument,
                        ApplicationDocument.id == DocumentRevision.document_id,
                    )
                    # 공용 문서(지원 없음, c0012)의 revision도 나온다 (outer join)
                    .outerjoin(Application, Application.id == ApplicationDocument.application_id)
                    .where(older(DocumentRevision.created_at))
                    .order_by(DocumentRevision.created_at.desc())
                    .limit(limit)
                )
            ).all()
            for rev, doc, app_title in rows:
                items.append(
                    ActivityItem(
                        "document_revision",
                        "revised",
                        rev.id,
                        rev.created_at,
                        _cut(doc.title),
                        rev.origin,
                        f"{_cut(app_title) if doc.application_id else SHARED_LABEL}"
                        f" · r{rev.revision_no}",
                        _ref(
                            application_id=doc.application_id,
                            document_id=doc.id,
                            revision_no=rev.revision_no,
                            shared="true" if doc.application_id is None else None,
                        ),
                    )
                )

        if "study_attempt" in kinds:
            rows = (
                await s.execute(
                    select(StudyAttempt, StudyAssessment.title, StudyItem.title)
                    .outerjoin(StudyAssessment, StudyAssessment.id == StudyAttempt.assessment_id)
                    .outerjoin(StudyItem, StudyItem.id == StudyAttempt.study_item_id)
                    .where(older(StudyAttempt.created_at))
                    .order_by(StudyAttempt.created_at.desc())
                    .limit(limit)
                )
            ).all()
            for a, assessment, item in rows:
                score = (
                    f"{a.score}/{a.max_score}"
                    if a.score is not None and a.max_score is not None
                    else (str(a.score) if a.score is not None else None)
                )
                items.append(
                    ActivityItem(
                        "study_attempt",
                        "attempted",
                        a.id,
                        a.created_at,
                        _cut(assessment or item or "응시"),
                        a.result,
                        score,
                        _ref(assessment_id=a.assessment_id, study_item_id=a.study_item_id),
                    )
                )

    items.sort(key=lambda x: (x.at, x.kind, x.action, str(x.id)), reverse=True)
    return items[:limit]

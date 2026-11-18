"""브레인 정리 상태 한 줄 (c0052, 2026-11-18 사용 점검: "브레인에 다 때려박아두고 알아서 정리").

넣은 자료는 서버가 읽고(파싱) → 내 경험 자료면 경험 후보를 뽑고(``extraction.auto_extract``) → 같은 경험에 붙이고
학력·자격·기술을 나눈다. 사람은 여러 화면을 돌지 않고 이 한 줄에서 확인할 것으로 들어간다:

- 진행: 읽는 중·정리 중인 자료 수, 용도 확인이 필요한 자료, 실패한 자료.
- 확인할 것: 사실 후보, "같은 경험일 수 있음", 프로필에 넣을 값, 학력·기술 제안, 자격 후보, 곧 만료.
- 최근 정리: 지난 하루 동안 끝난 추출의 합 (새 경험·합친 경험·확인할 사실·근거로 붙인 사실).

모두 세기만 한다. 모델을 부르지 않고, 증빙 원본의 글은 읽지 않는다 (프로필 값은 ``profiles.profile_suggestions``
규칙 그대로 내 경험 자료에서만)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select

from career_core.db import Database, Experience, ExtractionRun
from career_core.db.base import utcnow
from career_core.domain.brain import ExtractionStatus
from career_core.ports import ActorContext

RECENT_HOURS = 24


@dataclass(frozen=True, slots=True)
class RecentOrganize:
    since: datetime
    sources: int  # 정리를 마친 자료 수
    experiences_created: int
    experiences_matched: int  # 있던 경험에 합침
    claims_created: int
    claims_attached: int  # 있던 사실에 근거로 붙임


@dataclass(frozen=True, slots=True)
class OrganizeStatus:
    processing: int  # 읽는 중·정리(추출) 중
    needs_purpose: int  # 무엇인지 확인 필요
    failed: int  # 읽기·정리 실패
    pending_claims: int
    review_experiences: int
    duplicates: int
    profile_values: int
    education: int
    skills: int
    credentials: int  # 증빙·자료에서 뽑은 자격 후보 (확인 전)
    expiring: int  # 곧 만료 (확인한 기록)
    recent: RecentOrganize
    auto_extract: str  # extraction.auto_extract_state


async def organize_status(
    db: Database, gateway, actor: ActorContext, *, can_extract: bool, now: datetime | None = None
) -> OrganizeStatus:
    from career_core.usecases import credentials as cred_uc
    from career_core.usecases import extraction, profiles, sources
    from career_core.usecases.brain import brain_overview

    now = now or utcnow()
    since = now - timedelta(hours=RECENT_HOURS)
    inbox = await sources.list_sources(db, actor, attention=True, limit=500)
    why = [x.attention for x in inbox]
    overview = await brain_overview(db, actor)
    sugg = await extraction.list_suggestions(db, actor)
    profile_values = await profiles.profile_suggestions(db, actor)
    creds = await cred_uc.list_credentials(db, actor)
    async with db.workspace_session(actor.workspace_id) as s:
        duplicates = await s.scalar(
            select(func.count())
            .select_from(Experience)
            .where(Experience.status == "active", Experience.possible_duplicate_of.is_not(None))
        )
        recent = (
            await s.execute(
                select(
                    func.count(func.distinct(ExtractionRun.parse_run_id)),
                    func.coalesce(func.sum(ExtractionRun.experiences_created), 0),
                    func.coalesce(func.sum(ExtractionRun.experiences_matched), 0),
                    func.coalesce(func.sum(ExtractionRun.claims_created), 0),
                    func.coalesce(func.sum(ExtractionRun.claims_attached), 0),
                ).where(
                    ExtractionRun.status.in_(
                        [ExtractionStatus.COMPLETE.value, ExtractionStatus.PARTIAL.value]
                    ),
                    ExtractionRun.updated_at >= since,
                )
            )
        ).one()
    state = await extraction.auto_extract_state(db, gateway, actor, can_extract=can_extract)
    return OrganizeStatus(
        processing=sum(1 for w in why if w in ("processing", "extracting")),
        needs_purpose=sum(1 for w in why if w == "purpose_unconfirmed"),
        failed=sum(1 for w in why if w in ("parse_failed", "extraction_failed")),
        pending_claims=overview.pending_claims,
        review_experiences=overview.needs_review,
        duplicates=duplicates or 0,
        profile_values=len(profile_values),
        education=len(sugg.get("education", [])),
        skills=len(sugg.get("skills", [])),
        credentials=sum(1 for c in creds if c.status == "extracted" and c.kind != "degree"),
        expiring=sum(1 for c in creds if c.expiry == "soon" and c.status != "extracted"),
        recent=RecentOrganize(since, *(int(v or 0) for v in recent)),
        auto_extract=state,
    )


__all__ = ["OrganizeStatus", "RecentOrganize", "organize_status"]

"""전형 단계 (c0012): 제출본에 묶인 서류·시험·과제·면접 일정과 결과 (plan §11 원칙 7).

- 단계는 제출한 지원에만 만든다. 단계마다 그 지원의 제출본(``submission_id``)을 가리킨다 —
  "어느 문서 버전으로 어떤 결과가 났는지"를 나중에 볼 수 있게.
- 결과(passed·failed…)는 사용자가 기록한다. 시스템은 결과의 원인을 판단·추천하지 않는다.
  개인 단위 표본은 작아서, 원인을 자동으로 확정하면 틀린 교훈을 남긴다.
- 지원 상태(``applications.status``)는 자동으로 바꾸지 않는다. 최종 합격·불합격 기록은 사용자가 따로 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select

from career_core.db import Database
from career_core.db.base import utcnow
from career_core.db.models import ApplicationStage, ApplicationSubmission
from career_core.domain.documents import DocumentRuleError
from career_core.ports import ActorContext
from career_core.usecases.applications import _get_app
from career_core.usecases.brain import NotFound

MAX_STAGES = 30
MAX_NAME_CHARS = 200
MAX_NOTE_CHARS = 2000


class StageKind(StrEnum):
    DOCUMENT = "document"  # 서류
    TEST = "test"  # 코딩 테스트·필기·인적성
    ASSIGNMENT = "assignment"  # 과제
    INTERVIEW = "interview"
    FINAL = "final"  # 최종 발표
    OTHER = "other"


class StageOutcome(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    WITHDRAWN = "withdrawn"
    CANCELLED = "cancelled"


DEFAULT_NAMES = {
    StageKind.DOCUMENT: "서류",
    StageKind.TEST: "테스트",
    StageKind.ASSIGNMENT: "과제",
    StageKind.INTERVIEW: "면접",
    StageKind.FINAL: "최종",
    StageKind.OTHER: "기타",
}


@dataclass(frozen=True, slots=True)
class StageView:
    id: UUID
    application_id: UUID
    submission_id: UUID
    position: int
    kind: str
    name: str
    scheduled_at: datetime | None
    outcome: str
    decided_at: datetime | None
    note: str | None
    created_at: datetime
    updated_at: datetime


def _view(r: ApplicationStage) -> StageView:
    return StageView(
        r.id,
        r.application_id,
        r.submission_id,
        r.position,
        r.kind,
        r.name,
        r.scheduled_at,
        r.outcome,
        r.decided_at,
        r.note,
        r.created_at,
        r.updated_at,
    )


def _kind(value: str) -> str:
    try:
        return StageKind(value).value
    except ValueError as exc:
        raise DocumentRuleError("invalid_stage_kind") from exc


def _outcome(value: str) -> str:
    try:
        return StageOutcome(value).value
    except ValueError as exc:
        raise DocumentRuleError("invalid_outcome") from exc


def _name(value: str | None, kind: str) -> str:
    v = " ".join((value or "").split())[:MAX_NAME_CHARS]
    return v or DEFAULT_NAMES[StageKind(kind)]


def _note(value: str | None) -> str | None:
    return (value or "").strip()[:MAX_NOTE_CHARS] or None


async def list_stages(db: Database, actor: ActorContext, application_id: UUID) -> list[StageView]:
    async with db.workspace_session(actor.workspace_id) as s:
        await _get_app(s, application_id)
        rows = (
            (
                await s.execute(
                    select(ApplicationStage)
                    .where(ApplicationStage.application_id == application_id)
                    .order_by(ApplicationStage.position, ApplicationStage.created_at)
                )
            )
            .scalars()
            .all()
        )
    return [_view(r) for r in rows]


async def add_stage(
    db: Database,
    actor: ActorContext,
    application_id: UUID,
    *,
    kind: str,
    name: str | None = None,
    scheduled_at: datetime | None = None,
    outcome: str = StageOutcome.PENDING,
    note: str | None = None,
) -> StageView:
    """전형 단계를 끝에 더한다. 제출본이 없으면 409 ``not_submitted``."""
    kind = _kind(kind)
    outcome = _outcome(outcome)
    async with db.workspace_session(actor.workspace_id) as s:
        await _get_app(s, application_id, lock=True)
        sub_id = await s.scalar(
            select(ApplicationSubmission.id).where(
                ApplicationSubmission.application_id == application_id
            )
        )
        if sub_id is None:
            raise DocumentRuleError("not_submitted")
        count, last = (
            await s.execute(
                select(func.count(), func.max(ApplicationStage.position)).where(
                    ApplicationStage.application_id == application_id
                )
            )
        ).one()
        if count >= MAX_STAGES:
            raise DocumentRuleError("too_many_stages")
        row = ApplicationStage(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            application_id=application_id,
            submission_id=sub_id,
            position=0 if last is None else last + 1,
            kind=kind,
            name=_name(name, kind),
            scheduled_at=scheduled_at,
            outcome=outcome,
            decided_at=None if outcome == StageOutcome.PENDING else utcnow(),
            note=_note(note),
            created_by=actor.subject_id,
        )
        s.add(row)
        await s.flush()
        await s.refresh(row)
        view = _view(row)
    return view


_FIELDS = {"kind", "name", "scheduled_at", "outcome", "note", "position"}


async def update_stage(
    db: Database,
    actor: ActorContext,
    application_id: UUID,
    stage_id: UUID,
    changes: dict[str, Any],
) -> StageView:
    """일정·이름·결과·메모를 고친다. 결과를 pending에서 바꾸면 결정 시각을 남긴다 (되돌리면 지운다)."""
    if set(changes) - _FIELDS:
        raise DocumentRuleError("unknown_field")
    async with db.workspace_session(actor.workspace_id) as s:
        await _get_app(s, application_id)
        row = await s.scalar(
            select(ApplicationStage)
            .where(
                ApplicationStage.id == stage_id,
                ApplicationStage.application_id == application_id,
            )
            .with_for_update()
        )
        if row is None:
            raise NotFound("stage")
        if "kind" in changes:
            row.kind = _kind(changes["kind"])
        if "name" in changes:
            row.name = _name(changes["name"], row.kind)
        if "scheduled_at" in changes:
            row.scheduled_at = changes["scheduled_at"]
        if "note" in changes:
            row.note = _note(changes["note"])
        if "position" in changes:
            pos = changes["position"]
            if not isinstance(pos, int) or isinstance(pos, bool) or pos < 0:
                raise DocumentRuleError("invalid_position")
            row.position = pos
        if "outcome" in changes:
            outcome = _outcome(changes["outcome"])
            if outcome != row.outcome:
                row.decided_at = None if outcome == StageOutcome.PENDING else utcnow()
            row.outcome = outcome
        await s.flush()
        await s.refresh(row)
        view = _view(row)
    return view


async def delete_stage(
    db: Database, actor: ActorContext, application_id: UUID, stage_id: UUID
) -> None:
    async with db.workspace_session(actor.workspace_id) as s:
        await _get_app(s, application_id)
        row = await s.scalar(
            select(ApplicationStage).where(
                ApplicationStage.id == stage_id,
                ApplicationStage.application_id == application_id,
            )
        )
        if row is None:
            raise NotFound("stage")
        await s.delete(row)


__all__ = [
    "StageKind",
    "StageOutcome",
    "StageView",
    "add_stage",
    "delete_stage",
    "list_stages",
    "update_stage",
]

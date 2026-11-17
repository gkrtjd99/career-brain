"""학력 (c0040, plan §20 "나 › 학력").

- 사람이 쓰는 기록이다 (후보·확인 단계 없음). 증빙(용도 ``proof`` 원본)·경험을 이을 수 있다.
- 자격·이수 기록의 옛 학력(``credentials.kind = degree``)은 그대로 읽힌다. ``move_from_credential``이 그 기록을
  학력 한 줄로 옮기고 원래 기록을 지운다 (한 트랜잭션). 값은 기록에 적힌 것만 옮긴다.
- 자료 등급은 개인 정보(private): 모델로 보내는 경로가 없다. MCP는 읽기 전용 ``education_list``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from career_core.db.base import utcnow
from career_core.db.models import Credential, Education, Experience, Source
from career_core.db.session import Database
from career_core.domain.education import (
    Degree,
    EducationRuleError,
    EducationStatus,
    check_row,
    check_values,
    degree_from_credential,
    school_from_credential,
)
from career_core.domain.sources import SourcePurpose
from career_core.ports import ActorContext
from career_core.usecases.brain import NotFound

EDITABLE = (
    "school",
    "campus",
    "major",
    "extra_major",
    "extra_major_kind",
    "degree",
    "status",
    "transferred",
    "entered_on",
    "graduated_on",
    "gpa",
    "gpa_scale",
    "note",
    "source_id",
    "experience_id",
)


@dataclass(frozen=True, slots=True)
class EducationView:
    id: UUID
    school: str
    campus: str | None
    major: str | None
    extra_major: str | None
    extra_major_kind: str | None
    degree: str
    status: str
    transferred: bool
    entered_on: date | None
    graduated_on: date | None
    gpa: Decimal | None
    gpa_scale: Decimal | None
    note: str | None
    source_id: UUID | None
    source_title: str | None
    experience_id: UUID | None
    experience_title: str | None
    created_at: datetime
    updated_at: datetime


def _order():
    # 최근 학교부터: 졸업(없으면 입학) 달이 늦은 순, 날짜가 없으면 뒤로
    return (
        Education.graduated_on.desc().nulls_last(),
        Education.entered_on.desc().nulls_last(),
        Education.created_at.desc(),
    )


async def _views(s: AsyncSession, rows: list[Education]) -> list[EducationView]:
    src_ids = {r.source_id for r in rows if r.source_id}
    exp_ids = {r.experience_id for r in rows if r.experience_id}
    sources = (
        dict((await s.execute(select(Source.id, Source.title).where(Source.id.in_(src_ids)))).all())
        if src_ids
        else {}
    )
    exps = (
        dict(
            (
                await s.execute(
                    select(Experience.id, Experience.title).where(Experience.id.in_(exp_ids))
                )
            ).all()
        )
        if exp_ids
        else {}
    )
    return [
        EducationView(
            id=r.id,
            school=r.school,
            campus=r.campus,
            major=r.major,
            extra_major=r.extra_major,
            extra_major_kind=r.extra_major_kind,
            degree=r.degree,
            status=r.status,
            transferred=r.transferred,
            entered_on=r.entered_on,
            graduated_on=r.graduated_on,
            gpa=r.gpa,
            gpa_scale=r.gpa_scale,
            note=r.note,
            source_id=r.source_id,
            source_title=sources.get(r.source_id) if r.source_id else None,
            experience_id=r.experience_id,
            experience_title=exps.get(r.experience_id) if r.experience_id else None,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


async def list_education(db: Database, actor: ActorContext) -> list[EducationView]:
    async with db.workspace_session(actor.workspace_id) as s:
        rows = list((await s.scalars(select(Education).order_by(*_order()))).all())
        return await _views(s, rows)


async def get_education(db: Database, actor: ActorContext, education_id: UUID) -> EducationView:
    async with db.workspace_session(actor.workspace_id) as s:
        row = await s.get(Education, education_id)
        if row is None:
            raise NotFound(education_id)
        return (await _views(s, [row]))[0]


async def _check_links(s: AsyncSession, values: dict[str, Any]) -> None:
    if values.get("source_id") is not None:
        purpose = await s.scalar(select(Source.purpose).where(Source.id == values["source_id"]))
        if purpose is None:
            raise EducationRuleError("source_not_found")
        if purpose != SourcePurpose.PROOF:
            raise EducationRuleError("source_not_proof")
    exp_id = values.get("experience_id")
    if exp_id is not None and await s.get(Experience, exp_id) is None:
        raise EducationRuleError("experience_not_found")


async def create_education(db: Database, actor: ActorContext, values: dict[str, Any]) -> UUID:
    clean = check_values({k: v for k, v in values.items() if k in EDITABLE})
    if not clean.get("school"):
        raise EducationRuleError("school_required")
    row = Education(
        id=uuid4(),
        workspace_id=actor.workspace_id,
        degree=clean.pop("degree", Degree.BACHELOR.value),
        status=clean.pop("status", EducationStatus.GRADUATED.value),
        transferred=clean.pop("transferred", False),
        created_by=actor.subject_id,
        **clean,
    )
    check_row(row)
    async with db.workspace_session(actor.workspace_id) as s:
        await _check_links(s, clean)
        s.add(row)
    return row.id


async def update_education(
    db: Database, actor: ActorContext, education_id: UUID, changes: dict[str, Any]
) -> None:
    """준 칸만 바꾼다 (None은 비우기). 학점·만점은 함께 본다."""
    if set(changes) - set(EDITABLE):
        raise EducationRuleError("unknown_field")
    clean = check_values(changes)
    for key in ("degree", "status"):
        if key in changes and changes[key] is None:
            raise EducationRuleError(f"invalid_{key}")
    async with db.workspace_session(actor.workspace_id) as s:
        row = await s.get(Education, education_id, with_for_update=True)
        if row is None:
            raise NotFound(education_id)
        await _check_links(s, clean)
        for key, value in clean.items():
            setattr(row, key, value)
        check_row(row)
        row.updated_at = utcnow()


async def delete_education(db: Database, actor: ActorContext, education_id: UUID) -> None:
    async with db.workspace_session(actor.workspace_id) as s:
        row = await s.get(Education, education_id)
        if row is None:
            raise NotFound(education_id)
        await s.delete(row)


async def move_from_credential(db: Database, actor: ActorContext, credential_id: UUID) -> UUID:
    """자격·이수의 학력 기록 하나를 학력으로 옮긴다. 원래 기록은 지운다. 학력 ID를 돌려준다.

    옮기는 값: 학교(발급 기관 또는 이름), 학위 과정(이름의 낱말, 없으면 학사), 졸업 달(취득일 또는 끝난 날의 달),
    입학 달(시작일의 달), 증빙·경험 연결, 이름·점수·상세는 메모로. 확인 전 후보도 옮길 수 있다 (옮기는 것이 확인).
    """
    async with db.workspace_session(actor.workspace_id) as s:
        c = await s.get(Credential, credential_id, with_for_update=True)
        if c is None:
            raise NotFound(credential_id)
        if c.kind != "degree":
            raise EducationRuleError("credential_not_degree")
        graduated = c.acquired_on or c.ended_on
        note_parts = [c.name, c.score, c.detail]
        row = Education(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            school=school_from_credential(c.name, c.issuer),
            degree=degree_from_credential(c.name, c.issuer).value,
            status=(
                EducationStatus.GRADUATED.value if graduated else EducationStatus.ENROLLED.value
            ),
            transferred=False,
            entered_on=date(c.started_on.year, c.started_on.month, 1) if c.started_on else None,
            graduated_on=date(graduated.year, graduated.month, 1) if graduated else None,
            note="\n".join(p for p in note_parts if p)[:1000] or None,
            source_id=c.source_id,
            experience_id=c.experience_id,
            created_by=actor.subject_id,
        )
        if row.entered_on and row.graduated_on and row.graduated_on < row.entered_on:
            row.entered_on = None
        check_row(row)
        s.add(row)
        await s.flush()
        await s.delete(c)
    return row.id


__all__ = [
    "EDITABLE",
    "EducationView",
    "create_education",
    "delete_education",
    "get_education",
    "list_education",
    "move_from_credential",
    "update_education",
]

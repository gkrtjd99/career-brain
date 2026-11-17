"""학력 경로 (c0040, plan §20 "나 › 학력").

읽기 ``education.read``, 쓰기 ``education.write``. 오류는 ``{"detail": code}``: 없는 학력·기록 404 ``not found``,
학력이 아닌 자격·이수 기록 409 ``credential_not_degree``, 그 밖의 규칙 422 (``school_required``,
``gpa_over_scale``, ``gpa_needs_scale``, ``invalid_gpa_scale``, ``graduated_before_entered``,
``extra_major_needs_kind``, ``source_not_proof``, ``invalid_month`` …).

- ``POST /credentials/{id}/to-education``: 자격·이수의 옛 학력 기록을 학력으로 옮긴다 (원래 기록은 지운다).

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from career_core.domain.education import (
    MAX_CAMPUS,
    MAX_MAJOR,
    MAX_NOTE,
    MAX_SCHOOL,
    EducationRuleError,
)
from career_core.ports import ActorContext
from career_core.usecases import education as uc
from career_core.usecases.brain import NotFound

Degree = Literal["high_school", "associate", "bachelor", "master", "doctor"]
Status = Literal["enrolled", "on_leave", "expected", "graduated", "completed", "dropped"]
ExtraKind = Literal["double", "minor"]
# 입학·졸업은 달까지: "YYYY-MM" (날짜를 주면 그 달로 본다)
Month = Annotated[str, Field(pattern=r"^\d{4}-\d{2}(-\d{2})?$")]

_STATUS = {"credential_not_degree": 409}


def _rule(exc: EducationRuleError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(exc.code, 422), detail=exc.code)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not found")


class EducationIn(BaseModel):
    school: str = Field(min_length=1, max_length=MAX_SCHOOL * 2)
    campus: str | None = Field(default=None, max_length=MAX_CAMPUS * 2)
    major: str | None = Field(default=None, max_length=MAX_MAJOR * 2)
    extra_major: str | None = Field(default=None, max_length=MAX_MAJOR * 2)
    extra_major_kind: ExtraKind | None = None
    degree: Degree = "bachelor"
    status: Status = "graduated"
    transferred: bool = False
    entered_on: Month | None = None
    graduated_on: Month | None = None
    gpa: Decimal | None = Field(default=None, ge=0, le=100)
    gpa_scale: Decimal | None = None
    note: str | None = Field(default=None, max_length=MAX_NOTE * 2)
    source_id: UUID | None = None
    experience_id: UUID | None = None


class EducationPatch(BaseModel):
    """준 칸만 바꾼다. null은 비우기 (학교·학위 과정·상태는 비울 수 없다)."""

    school: str | None = Field(default=None, min_length=1, max_length=MAX_SCHOOL * 2)
    campus: str | None = Field(default=None, max_length=MAX_CAMPUS * 2)
    major: str | None = Field(default=None, max_length=MAX_MAJOR * 2)
    extra_major: str | None = Field(default=None, max_length=MAX_MAJOR * 2)
    extra_major_kind: ExtraKind | None = None
    degree: Degree | None = None
    status: Status | None = None
    transferred: bool | None = None
    entered_on: Month | None = None
    graduated_on: Month | None = None
    gpa: Decimal | None = Field(default=None, ge=0, le=100)
    gpa_scale: Decimal | None = None
    note: str | None = Field(default=None, max_length=MAX_NOTE * 2)
    source_id: UUID | None = None
    experience_id: UUID | None = None


class EducationOut(BaseModel):
    id: UUID
    school: str
    campus: str | None
    major: str | None
    extra_major: str | None
    extra_major_kind: str | None
    degree: str
    status: str
    transferred: bool
    entered_on: date | None  # 그 달 1일
    graduated_on: date | None
    gpa: float | None
    gpa_scale: float | None
    note: str | None
    source_id: UUID | None
    source_title: str | None
    experience_id: UUID | None
    experience_title: str | None
    created_at: datetime
    updated_at: datetime


class CreatedOut(BaseModel):
    id: UUID


def _out(v: uc.EducationView) -> EducationOut:
    data = {f: getattr(v, f) for f in EducationOut.model_fields}
    data["gpa"] = float(v.gpa) if v.gpa is not None else None
    data["gpa_scale"] = float(v.gpa_scale) if v.gpa_scale is not None else None
    return EducationOut(**data)


def add_education_routes(router: APIRouter, deps, authorize) -> None:
    reader = Annotated[ActorContext, Depends(authorize("education.read"))]
    writer = Annotated[ActorContext, Depends(authorize("education.write"))]

    @router.get("/education", response_model=list[EducationOut])
    async def list_education(actor: reader) -> list[EducationOut]:
        return [_out(v) for v in await uc.list_education(deps.db, actor)]

    @router.post("/education", response_model=CreatedOut, status_code=201)
    async def create_education(body: EducationIn, actor: writer) -> CreatedOut:
        try:
            new_id = await uc.create_education(deps.db, actor, body.model_dump())
        except EducationRuleError as exc:
            raise _rule(exc) from exc
        return CreatedOut(id=new_id)

    @router.get("/education/{education_id}", response_model=EducationOut)
    async def get_education(education_id: UUID, actor: reader) -> EducationOut:
        try:
            return _out(await uc.get_education(deps.db, actor, education_id))
        except NotFound as exc:
            raise _not_found() from exc

    @router.patch("/education/{education_id}", response_model=EducationOut)
    async def patch_education(
        education_id: UUID, body: EducationPatch, actor: writer
    ) -> EducationOut:
        try:
            await uc.update_education(
                deps.db, actor, education_id, body.model_dump(exclude_unset=True)
            )
            return _out(await uc.get_education(deps.db, actor, education_id))
        except NotFound as exc:
            raise _not_found() from exc
        except EducationRuleError as exc:
            raise _rule(exc) from exc

    @router.delete("/education/{education_id}", status_code=204)
    async def delete_education(education_id: UUID, actor: writer) -> None:
        try:
            await uc.delete_education(deps.db, actor, education_id)
        except NotFound as exc:
            raise _not_found() from exc

    @router.post("/credentials/{credential_id}/to-education", response_model=EducationOut)
    async def move_from_credential(credential_id: UUID, actor: writer) -> EducationOut:
        try:
            new_id = await uc.move_from_credential(deps.db, actor, credential_id)
            return _out(await uc.get_education(deps.db, actor, new_id))
        except NotFound as exc:
            raise _not_found() from exc
        except EducationRuleError as exc:
            raise _rule(exc) from exc

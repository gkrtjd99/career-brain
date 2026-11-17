"""참고 자료 보관함 경로 (O14, plan §19.2). 다른 사람의 글: 내 경험이 아니고 문서 근거로 쓰지 않는다.

읽기 ``references.read``, 쓰기 ``references.write``. 구조 메모 후보 확인은 사람만 (403 ``human_confirmation_required``).
오류는 ``{"detail": code}``: 없는 참고 글·메모·회사·원본 404 ``not found``, 참고 원본이 아니거나 아직 읽는 중이면
409 (``source_not_reference``, ``source_not_ready``), 그 밖의 규칙 422 (``invalid_kind``, ``text_or_source_required`` …).

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from career_core.domain.references import (
    MAX_NOTE_ITEM,
    MAX_NOTE_ITEMS,
    MAX_QUESTION,
    MAX_ROLE,
    MAX_SOURCE_NOTE,
    MAX_TEXT,
    MAX_TITLE,
    MAX_YEAR,
    MIN_YEAR,
    REFERENCE_KINDS,
    ReferenceRuleError,
)
from career_core.ports import ActorContext
from career_core.usecases import references as uc
from career_core.usecases.brain import NotFound

_STATUS = {
    "human_confirmation_required": 403,
    "source_not_reference": 409,
    "source_not_ready": 409,
    "source_empty": 409,
}

Kind = Literal[REFERENCE_KINDS]  # type: ignore[valid-type]
DataClassIn = Literal["public", "private", "restricted"]
Item = Annotated[str, Field(max_length=MAX_NOTE_ITEM)]


def _rule(exc: ReferenceRuleError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(exc.code, 422), detail=exc.code)


class ReferenceIn(BaseModel):
    """``text``(붙여넣은 글) 또는 ``source_id``(용도가 참고인 원본) 중 하나. ``mask_pii``: 개인정보를 ○○○로 가려 저장."""

    kind: Kind
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    text: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT)
    source_id: UUID | None = None
    company_id: UUID | None = None
    role: str | None = Field(default=None, max_length=MAX_ROLE)
    year: int | None = Field(default=None, ge=MIN_YEAR, le=MAX_YEAR)
    source_note: str | None = Field(default=None, max_length=MAX_SOURCE_NOTE)
    data_class: DataClassIn | None = None
    mask_pii: bool = False


class ReferencePatch(BaseModel):
    """준 칸만 고친다. null은 비우기 (종류·제목은 비울 수 없다). ``mask_pii``: 저장된 글을 지금 가린다."""

    kind: Kind | None = None
    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE)
    company_id: UUID | None = None
    role: str | None = Field(default=None, max_length=MAX_ROLE)
    year: int | None = Field(default=None, ge=MIN_YEAR, le=MAX_YEAR)
    source_note: str | None = Field(default=None, max_length=MAX_SOURCE_NOTE)
    data_class: DataClassIn | None = None
    mask_pii: bool = False


class PiiCheckIn(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT)
    source_id: UUID | None = None


class PiiHitOut(BaseModel):
    kind: str  # resident_registration_number | phone_number | email_address | name | school
    start: int
    end: int
    value: str
    replacement: str


class PiiReportOut(BaseModel):
    hits: list[PiiHitOut]  # 앞 200개
    total: int
    by_kind: dict[str, int]


class NoteIn(BaseModel):
    question: str | None = Field(default=None, max_length=MAX_QUESTION)
    structure: list[Item] = Field(default_factory=list, max_length=MAX_NOTE_ITEMS)
    materials: list[Item] = Field(default_factory=list, max_length=MAX_NOTE_ITEMS)
    competencies: list[Item] = Field(default_factory=list, max_length=MAX_NOTE_ITEMS)


class NotePatch(BaseModel):
    question: str | None = Field(default=None, max_length=MAX_QUESTION)
    structure: list[Item] | None = Field(default=None, max_length=MAX_NOTE_ITEMS)
    materials: list[Item] | None = Field(default=None, max_length=MAX_NOTE_ITEMS)
    competencies: list[Item] | None = Field(default=None, max_length=MAX_NOTE_ITEMS)


class NoteOut(BaseModel):
    id: UUID
    reference_id: UUID
    question: str | None
    structure: list[str]
    materials: list[str]
    competencies: list[str]
    status: str  # candidate | confirmed
    origin: str  # model | user
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SummaryOut(BaseModel):
    id: UUID
    kind: str
    title: str
    company_id: UUID | None
    company_name: str | None
    role: str | None
    year: int | None
    source_id: UUID | None
    data_class: str
    text_chars: int
    pii_masked: int
    notes_confirmed: int
    notes_candidate: int
    created_at: datetime
    updated_at: datetime


class ReferenceOut(BaseModel):
    id: UUID
    kind: str
    title: str
    company_id: UUID | None
    company_name: str | None
    role: str | None
    year: int | None
    source_note: str | None
    source_id: UUID | None
    source_title: str | None
    data_class: str
    text: str
    pii_masked: int
    created_at: datetime
    updated_at: datetime
    notes: list[NoteOut]


class SuggestOut(BaseModel):
    created: int
    notes: list[str]


class IdOut(BaseModel):
    id: UUID


class StatusOut(BaseModel):
    id: UUID
    status: str


def add_reference_routes(router: APIRouter, deps, authorize) -> None:
    db = deps.db
    reader = Annotated[ActorContext, Depends(authorize("references.read"))]
    writer = Annotated[ActorContext, Depends(authorize("references.write"))]

    async def guarded(call):
        try:
            return await call
        except NotFound as exc:
            raise HTTPException(status_code=404, detail="not found") from exc
        except ReferenceRuleError as exc:
            raise _rule(exc) from exc

    @router.get("/references", response_model=list[SummaryOut])
    async def index(
        actor: reader,
        kind: Annotated[Kind | None, Query()] = None,
        company_id: Annotated[UUID | None, Query()] = None,
        year: Annotated[int | None, Query(ge=MIN_YEAR, le=MAX_YEAR)] = None,
        q: Annotated[str | None, Query(max_length=200)] = None,
    ) -> list[SummaryOut]:
        rows = await guarded(
            uc.list_references(db, actor, kind=kind, company_id=company_id, year=year, q=q)
        )
        return [SummaryOut.model_validate(r, from_attributes=True) for r in rows]

    @router.post("/references/pii-check", response_model=PiiReportOut)
    async def pii_check(actor: reader, body: PiiCheckIn) -> PiiReportOut:
        report = await guarded(uc.check_pii(db, actor, text=body.text, source_id=body.source_id))
        return PiiReportOut.model_validate(report, from_attributes=True)

    @router.post("/references", response_model=IdOut, status_code=201)
    async def create(actor: writer, body: ReferenceIn) -> IdOut:
        data = body.model_dump()
        mask = data.pop("mask_pii")
        rid = await guarded(uc.create_reference(db, deps.blobs, actor, **data, mask=mask))
        return IdOut(id=rid)

    @router.get("/references/{reference_id}", response_model=ReferenceOut)
    async def detail(actor: reader, reference_id: UUID) -> ReferenceOut:
        view = await guarded(uc.get_reference(db, actor, reference_id))
        return ReferenceOut.model_validate(view, from_attributes=True)

    @router.patch("/references/{reference_id}", response_model=IdOut)
    async def patch(actor: writer, reference_id: UUID, body: ReferencePatch) -> IdOut:
        data = body.model_dump(exclude_unset=True)
        mask = data.pop("mask_pii", False)
        for required in ("kind", "title"):
            if required in data and data[required] is None:
                raise HTTPException(status_code=422, detail=f"{required}_required")
        await guarded(uc.update_reference(db, actor, reference_id, **data, mask=mask))
        return IdOut(id=reference_id)

    @router.delete("/references/{reference_id}", status_code=204)
    async def remove(actor: writer, reference_id: UUID) -> Response:
        await guarded(uc.delete_reference(db, actor, reference_id))
        return Response(status_code=204)

    # --- 구조 메모

    @router.post("/references/{reference_id}/notes", response_model=IdOut, status_code=201)
    async def add_note(actor: writer, reference_id: UUID, body: NoteIn) -> IdOut:
        nid = await guarded(uc.add_note(db, actor, reference_id, **body.model_dump()))
        return IdOut(id=nid)

    @router.patch("/references/{reference_id}/notes/{note_id}", response_model=IdOut)
    async def patch_note(
        actor: writer, reference_id: UUID, note_id: UUID, body: NotePatch
    ) -> IdOut:
        await guarded(
            uc.update_note(db, actor, reference_id, note_id, **body.model_dump(exclude_unset=True))
        )
        return IdOut(id=note_id)

    @router.post("/references/{reference_id}/notes/{note_id}/confirm", response_model=StatusOut)
    async def confirm_note(actor: writer, reference_id: UUID, note_id: UUID) -> StatusOut:
        status = await guarded(uc.confirm_note(db, actor, reference_id, note_id))
        return StatusOut(id=note_id, status=status)

    @router.delete("/references/{reference_id}/notes/{note_id}", status_code=204)
    async def remove_note(actor: writer, reference_id: UUID, note_id: UUID) -> Response:
        await guarded(uc.delete_note(db, actor, reference_id, note_id))
        return Response(status_code=204)

    @router.post("/references/{reference_id}/suggest-notes", response_model=SuggestOut)
    async def suggest(actor: writer, reference_id: UUID) -> SuggestOut:
        result = await guarded(uc.suggest_notes(db, deps.models, actor, reference_id))
        return SuggestOut(created=result.created, notes=result.notes)


__all__ = ["add_reference_routes"]

"""회사 조사 경로 (O13, plan §19.1). 참고 지식: 항목은 내 경험이 아니고 문서 근거로 쓰지 않는다.

읽기 ``companies.read``, 쓰기 ``companies.write``. 항목 후보 확인은 사람만 (403 ``human_confirmation_required``).
오류는 ``{"detail": code}``: 없는 회사·원문·항목 404 ``not found``, 같은 이름의 회사 409 ``company_exists``,
그 밖의 규칙 422 (``quote_not_found``, ``finance_needs_as_of``, ``invalid_as_of`` …).

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from career_core.domain.companies import (
    MAX_ALIASES,
    MAX_BODY,
    MAX_NAME,
    MAX_PAGE_CHARS,
    MAX_QUOTE,
    CompanyRuleError,
)
from career_core.ports import ActorContext
from career_core.usecases import companies as uc
from career_core.usecases.brain import NotFound

_STATUS = {
    "company_exists": 409,
    "human_confirmation_required": 403,
    "page_not_ready": 409,
}

AS_OF = r"^\d{4}(-\d{1,2}(-\d{1,2})?)?$"


def _rule(exc: CompanyRuleError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(exc.code, 422), detail=exc.code)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not found")


class CompanyIn(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)
    aliases: list[Annotated[str, Field(max_length=MAX_NAME)]] = Field(
        default_factory=list, max_length=MAX_ALIASES
    )
    homepage: str | None = Field(default=None, max_length=500)
    corp_code: str | None = Field(default=None, max_length=20)
    business_no: str | None = Field(default=None, max_length=20)


class CompanyPatch(BaseModel):
    """준 칸만 고친다. null은 비우기 (이름은 비울 수 없다)."""

    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    aliases: list[Annotated[str, Field(max_length=MAX_NAME)]] | None = Field(
        default=None, max_length=MAX_ALIASES
    )
    homepage: str | None = Field(default=None, max_length=500)
    corp_code: str | None = Field(default=None, max_length=20)
    business_no: str | None = Field(default=None, max_length=20)


class SummaryOut(BaseModel):
    id: UUID
    name: str
    aliases: list[str]
    homepage: str | None
    facts_confirmed: int
    facts_candidate: int
    facts_stale: int
    postings: int
    updated_at: datetime


class CitationOut(BaseModel):
    """항목의 인용: 원문(``page_id``) 안의 위치와 그대로 복사한 글, 출처 주소·가져온 시각."""

    page_id: UUID
    quote: str
    start: int
    end: int
    source_url: str | None
    fetched_at: datetime
    origin: str  # url | paste


class FactOut(BaseModel):
    id: UUID
    company_id: UUID
    kind: str
    body: str
    as_of: str | None  # 기준일 YYYY | YYYY-MM | YYYY-MM-DD (원문에 적힌 만큼, 모르면 null)
    status: str  # candidate | confirmed
    origin: str  # model | user
    stale: bool  # 기준일(그 기간의 끝)이나 가져온 날이 약 12개월보다 오래됨
    citation: CitationOut
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PageOut(BaseModel):
    id: UUID
    origin: str  # url | paste
    url: str | None
    final_url: str | None
    title: str | None
    status: str  # pending | done | failed
    error_code: str | None
    fetched_at: datetime | None
    candidates: int
    notes: list[str]
    text_chars: int
    created_at: datetime
    text: str | None = None


class LinkedPostingOut(BaseModel):
    id: UUID
    title: str | None
    company: str | None
    status: str
    deadline: str | None


class LinkedApplicationOut(BaseModel):
    id: UUID
    posting_id: UUID
    title: str
    status: str
    submitted_at: datetime | None


class CompanyOut(BaseModel):
    id: UUID
    name: str
    aliases: list[str]
    homepage: str | None
    corp_code: str | None
    business_no: str | None
    created_at: datetime
    updated_at: datetime
    facts: list[FactOut]
    pages: list[PageOut]
    postings: list[LinkedPostingOut]
    applications: list[LinkedApplicationOut]


class FactIn(BaseModel):
    """원문(``page_id``)에서 고른 항목. ``quote``는 그 글 안에 있어야 한다 (없으면 422 ``quote_not_found``)."""

    page_id: UUID
    kind: str
    body: str = Field(min_length=1, max_length=MAX_BODY)
    quote: str = Field(min_length=1, max_length=MAX_QUOTE)
    as_of: str | None = Field(default=None, pattern=AS_OF)


class FactPatch(BaseModel):
    kind: str | None = None
    body: str | None = Field(default=None, min_length=1, max_length=MAX_BODY)
    quote: str | None = Field(default=None, min_length=1, max_length=MAX_QUOTE)
    as_of: str | None = Field(default=None, pattern=AS_OF)


class ImportUrlIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)


class ImportPasteIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_PAGE_CHARS)
    title: str | None = Field(default=None, max_length=300)


class IdOut(BaseModel):
    id: UUID


class StatusOut(BaseModel):
    id: UUID
    status: str


class PostingIdsIn(BaseModel):
    posting_ids: list[UUID] = Field(min_length=1, max_length=uc.MAX_LINK)


class LinkedOut(BaseModel):
    linked: int


class SuggestionOut(BaseModel):
    posting_id: UUID
    title: str | None
    company: str | None
    matched: str


def add_company_routes(router: APIRouter, deps, authorize) -> None:
    db = deps.db
    reader = Annotated[ActorContext, Depends(authorize("companies.read"))]
    writer = Annotated[ActorContext, Depends(authorize("companies.write"))]

    async def guarded(call):
        try:
            return await call
        except NotFound as exc:
            raise _not_found() from exc
        except CompanyRuleError as exc:
            raise _rule(exc) from exc

    @router.get("/companies", response_model=list[SummaryOut])
    async def index(
        actor: reader, q: Annotated[str | None, Query(max_length=200)] = None
    ) -> list[SummaryOut]:
        rows = await uc.list_companies(db, actor, q)
        return [SummaryOut.model_validate(r, from_attributes=True) for r in rows]

    @router.post("/companies", response_model=IdOut, status_code=201)
    async def create(actor: writer, body: CompanyIn) -> IdOut:
        cid = await guarded(uc.create_company(db, actor, **body.model_dump()))
        return IdOut(id=cid)

    @router.get("/companies/{company_id}", response_model=CompanyOut)
    async def detail(actor: reader, company_id: UUID) -> CompanyOut:
        view = await guarded(uc.get_company(db, actor, company_id))
        return CompanyOut.model_validate(view, from_attributes=True)

    @router.patch("/companies/{company_id}", response_model=IdOut)
    async def patch(actor: writer, company_id: UUID, body: CompanyPatch) -> IdOut:
        await guarded(
            uc.update_company(db, actor, company_id, **body.model_dump(exclude_unset=True))
        )
        return IdOut(id=company_id)

    @router.delete("/companies/{company_id}", status_code=204)
    async def remove(actor: writer, company_id: UUID) -> Response:
        await guarded(uc.delete_company(db, actor, company_id))
        return Response(status_code=204)

    # --- 항목

    @router.post("/companies/{company_id}/facts", response_model=IdOut, status_code=201)
    async def add_fact(actor: writer, company_id: UUID, body: FactIn) -> IdOut:
        fid = await guarded(uc.add_fact(db, actor, company_id, **body.model_dump()))
        return IdOut(id=fid)

    @router.patch("/companies/{company_id}/facts/{fact_id}", response_model=IdOut)
    async def patch_fact(actor: writer, company_id: UUID, fact_id: UUID, body: FactPatch) -> IdOut:
        await guarded(
            uc.update_fact(db, actor, company_id, fact_id, **body.model_dump(exclude_unset=True))
        )
        return IdOut(id=fact_id)

    @router.post("/companies/{company_id}/facts/{fact_id}/confirm", response_model=StatusOut)
    async def confirm_fact(actor: writer, company_id: UUID, fact_id: UUID) -> StatusOut:
        status = await guarded(uc.confirm_fact(db, actor, company_id, fact_id))
        return StatusOut(id=fact_id, status=status)

    @router.delete("/companies/{company_id}/facts/{fact_id}", status_code=204)
    async def remove_fact(actor: writer, company_id: UUID, fact_id: UUID) -> Response:
        await guarded(uc.delete_fact(db, actor, company_id, fact_id))
        return Response(status_code=204)

    # --- 원문 가져오기

    @router.post("/companies/{company_id}/import-url", response_model=StatusOut, status_code=202)
    async def import_url(actor: writer, company_id: UUID, body: ImportUrlIn) -> StatusOut:
        page_id = await guarded(uc.import_url(db, actor, company_id, body.url))
        return StatusOut(id=page_id, status="pending")

    @router.post("/companies/{company_id}/import-paste", response_model=PageOut, status_code=201)
    async def import_paste(actor: writer, company_id: UUID, body: ImportPasteIn) -> PageOut:
        page = await guarded(
            uc.import_paste(db, deps.models, actor, company_id, body.text, title=body.title)
        )
        return PageOut.model_validate(page, from_attributes=True)

    @router.get("/companies/{company_id}/pages/{page_id}", response_model=PageOut)
    async def page(actor: reader, company_id: UUID, page_id: UUID) -> PageOut:
        view = await guarded(uc.get_page(db, actor, company_id, page_id))
        return PageOut.model_validate(view, from_attributes=True)

    # --- 공고 묶기

    @router.get("/companies/{company_id}/posting-suggestions", response_model=list[SuggestionOut])
    async def suggestions(actor: reader, company_id: UUID) -> list[SuggestionOut]:
        rows = await guarded(uc.link_suggestions(db, actor, company_id))
        return [SuggestionOut.model_validate(r, from_attributes=True) for r in rows]

    @router.post("/companies/{company_id}/link-postings", response_model=LinkedOut)
    async def link(actor: writer, company_id: UUID, body: PostingIdsIn) -> LinkedOut:
        n = await guarded(uc.link_postings(db, actor, company_id, body.posting_ids))
        return LinkedOut(linked=n)

    @router.post("/companies/{company_id}/unlink-postings", response_model=LinkedOut)
    async def unlink(actor: writer, company_id: UUID, body: PostingIdsIn) -> LinkedOut:
        n = await guarded(uc.unlink_postings(db, actor, company_id, body.posting_ids))
        return LinkedOut(linked=n)


__all__ = ["add_company_routes"]

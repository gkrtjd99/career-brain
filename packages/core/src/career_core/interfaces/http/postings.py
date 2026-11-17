"""공고 저장 경로.

c0025: 스캔 PDF 비전 비용 확인 — 예상 비용이 상한을 넘은 가져오기는 ``needs_confirmation``이고 실행 한 줄의
``confirmation``에 예상(쪽·이미지·토큰·USD)이 붙는다. ``POST /job-postings/{id}/fetch-runs/{run}/confirm``
(확인 → 다시 가져오기 202) 또는 ``.../decline``. 사람(웹·HTTP 토큰)만, ``jobs.write``.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from career_core.domain.postings import PostingRuleError
from career_core.llm import ModelCallError
from career_core.ports import ActorContext
from career_core.usecases import postings as uc
from career_core.usecases.brain import NotFound


class SaveIn(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=uc.MAX_URLS)


class SaveOut(BaseModel):
    url: str
    posting_id: UUID | None
    fetch_run_id: UUID | None
    deduplicated: bool
    error: str | None


class PostingApplicationOut(BaseModel):
    """이 공고의 지원 요약 (지원이 없으면 null)."""

    id: UUID
    status: str
    questions: int
    answered: int
    submitted_at: datetime | None
    stage: dict | None = None


class SummaryOut(BaseModel):
    id: UUID
    canonical_url: str
    status: str
    title: str | None
    company: str | None
    deadline: str | None
    pending_changes: list[str]
    last_fetch_status: str | None
    last_fetch_error: str | None
    created_at: datetime | None
    saved_at: datetime | None
    company_id: UUID | None = None  # c0033 (O13) 이은 회사
    opens_at: str | None = None  # 접수 시작 (원문에 있을 때만)
    lifecycle: str = "unknown"  # upcoming | open | always | closed | unknown (공간 시간대로 계산)
    season: str | None = None  # 채용 시기 "2026-H2"
    application: PostingApplicationOut | None = None


class CostPartOut(BaseModel):
    """호출 하나의 예상 (비전 읽기 ``job_extract_image``, 항목 추출 ``job_extract``)."""

    purpose: str
    endpoint: str
    model: str
    billing: str  # metered | subscription | free. 종량제만 합계·상한 비교에 들어간다
    input_tokens: int
    max_input_tokens: int
    output_tokens: int
    max_output_tokens: int
    estimated_usd: str | None
    max_usd: str | None
    basis: str  # 출력 추정 근거: per_page | history_<n>(지난 호출 n개 평균) | default(상수)


class ConfirmationOut(BaseModel):
    id: UUID
    status: str  # pending | confirmed | declined | used | superseded
    pages: list[int]  # 읽을 쪽 (1부터)
    pages_total: int
    images: int
    input_tokens: int  # 추정, 호출 합계
    output_tokens: int  # 예상 출력 (추정), 호출 합계
    estimated_usd: str | None  # 예상, 종량제 호출 합계 (단가를 모르면 null)
    max_usd: str | None  # 입력·출력이 상한까지 나올 때. 상한 비교는 이 값으로 한다
    threshold_usd: str
    endpoint: str  # 비전 읽기 호출의 엔드포인트·모델
    model: str
    created_at: datetime
    decided_at: datetime | None
    parts: list[CostPartOut] = []  # 호출별 예상 (옛 요청은 빈 목록)


class RunOut(BaseModel):
    id: UUID
    status: str
    error_code: str | None
    http_status: int | None
    unchanged: bool
    version_id: UUID | None
    candidates: list[dict]
    notes: list[str]
    created_at: datetime
    confirmation: ConfirmationOut | None = None


class VersionOut(BaseModel):
    id: UUID
    version_no: int
    method: str
    adapter: str
    final_url: str
    extracted: dict
    missing: list[str]
    changed_fields: list[str]
    has_screenshot: bool
    created_at: datetime
    text: str | None


class PostingOut(BaseModel):
    id: UUID
    canonical_url: str
    status: str
    fields: dict
    evidence: dict
    pending_changes: list[str]
    confirmed_version_id: UUID | None
    saved_at: datetime | None
    latest: VersionOut | None
    runs: list[RunOut]
    company_id: UUID | None = None  # c0033 (O13) 이은 회사
    lifecycle: str = "unknown"
    season: str | None = None
    application: PostingApplicationOut | None = None


class ConfirmIn(BaseModel):
    version_id: UUID | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)
    clear: list[str] = Field(default_factory=list)


class ManualIn(BaseModel):
    text: str = Field(min_length=1, max_length=uc.MAX_MANUAL_CHARS)


class CaptureIn(BaseModel):
    """내 브라우저에서 보낸 공고 페이지 (c0043). 서버는 이 주소를 가져오지 않는다.

    ``html``: 페이지 HTML (렌더링하지 않고 글·구조화 데이터만 고른다). ``text``: 선택한 부분·공유한 글.
    둘 중 하나는 있어야 한다."""

    url: str = Field(min_length=1, max_length=2000, pattern=r"^https?://")
    title: str | None = Field(default=None, max_length=1000)
    html: str | None = Field(default=None, max_length=uc.MAX_CAPTURE_HTML)
    text: str | None = Field(default=None, max_length=uc.MAX_MANUAL_CHARS)


class CaptureOut(BaseModel):
    posting_id: UUID
    fetch_run_id: UUID
    status: str  # done | listing | failed
    created: bool
    unchanged: bool
    version_id: UUID | None
    error_code: str | None


class StatusOut(BaseModel):
    id: UUID
    status: str


class BulkIn(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=uc.MAX_BULK)


class BulkOut(BaseModel):
    changed: list[UUID]


def _rule(exc: PostingRuleError) -> HTTPException:
    return HTTPException(status_code=422, detail=exc.code)


def add_posting_routes(router: APIRouter, deps, authorize) -> None:
    db = deps.db
    reader = Annotated[ActorContext, Depends(authorize("jobs.read"))]
    writer = Annotated[ActorContext, Depends(authorize("jobs.write"))]

    @router.post("/job-postings", response_model=list[SaveOut], status_code=202)
    async def save(actor: writer, body: SaveIn) -> list[SaveOut]:
        try:
            results = await uc.save_urls(db, actor, body.urls)
        except PostingRuleError as exc:
            raise _rule(exc) from exc
        return [SaveOut.model_validate(r, from_attributes=True) for r in results]

    @router.post("/job-postings/capture", response_model=CaptureOut, status_code=201)
    async def capture(actor: writer, body: CaptureIn) -> CaptureOut:
        """보고 있는 페이지 보내기: 받은 HTML·글로 같은 추출·버전 규칙을 탄다 (가져오기 없음)."""
        try:
            got = await uc.capture_page(
                uc.PostingDeps(db=db, fetcher=None, gateway=deps.models),
                actor,
                body.url,
                html=body.html,
                text=body.text,
                title=body.title,
            )
        except PostingRuleError as exc:
            raise _rule(exc) from exc
        except ModelCallError as exc:
            raise HTTPException(503, "model_unavailable") from exc
        return CaptureOut.model_validate(got, from_attributes=True)

    @router.get("/job-postings", response_model=list[SummaryOut])
    async def index(
        actor: reader, lifecycle: str | None = None, status: str | None = None
    ) -> list[SummaryOut]:
        try:
            rows = await uc.list_postings(db, actor, lifecycle=lifecycle, status=status)
        except PostingRuleError as exc:
            raise _rule(exc) from exc
        return [SummaryOut.model_validate(p, from_attributes=True) for p in rows]

    @router.post("/job-postings/archive", response_model=BulkOut)
    async def archive_many(actor: writer, body: BulkIn) -> BulkOut:
        return BulkOut(changed=await uc.archive_postings(db, actor, body.ids))

    @router.post("/job-postings/unarchive", response_model=BulkOut)
    async def unarchive_many(actor: writer, body: BulkIn) -> BulkOut:
        return BulkOut(changed=await uc.archive_postings(db, actor, body.ids, archived=False))

    @router.get("/job-postings/{posting_id}", response_model=PostingOut)
    async def detail(actor: reader, posting_id: UUID) -> PostingOut:
        try:
            view = await uc.get_posting(db, actor, posting_id)
        except NotFound as exc:
            raise HTTPException(404, "not found") from exc
        return PostingOut.model_validate(view, from_attributes=True)

    @router.get("/job-postings/{posting_id}/versions", response_model=list[VersionOut])
    async def versions(actor: reader, posting_id: UUID) -> list[VersionOut]:
        try:
            rows = await uc.list_versions(db, actor, posting_id)
        except NotFound as exc:
            raise HTTPException(404, "not found") from exc
        return [VersionOut.model_validate(v, from_attributes=True) for v in rows]

    @router.get("/job-postings/{posting_id}/versions/{version_id}", response_model=VersionOut)
    async def version(actor: reader, posting_id: UUID, version_id: UUID) -> VersionOut:
        try:
            view = await uc.get_version(db, actor, posting_id, version_id)
        except NotFound as exc:
            raise HTTPException(404, "not found") from exc
        return VersionOut.model_validate(view, from_attributes=True)

    @router.post("/job-postings/{posting_id}/refetch", response_model=StatusOut, status_code=202)
    async def refetch(actor: writer, posting_id: UUID) -> StatusOut:
        try:
            run_id = await uc.refetch(db, actor, posting_id)
        except NotFound as exc:
            raise HTTPException(404, "not found") from exc
        return StatusOut(id=run_id, status="pending")

    @router.post(
        "/job-postings/{posting_id}/manual-text", response_model=StatusOut, status_code=201
    )
    async def manual(actor: writer, posting_id: UUID, body: ManualIn) -> StatusOut:
        try:
            run_id = await uc.add_manual_text(
                uc.PostingDeps(db=db, fetcher=None, gateway=deps.models),
                actor,
                posting_id,
                body.text,
            )
        except NotFound as exc:
            raise HTTPException(404, "not found") from exc
        except PostingRuleError as exc:
            raise _rule(exc) from exc
        return StatusOut(id=run_id, status="done")

    @router.post("/job-postings/{posting_id}/confirm", response_model=StatusOut)
    async def confirm(actor: writer, posting_id: UUID, body: ConfirmIn) -> StatusOut:
        try:
            status = await uc.confirm_posting(
                db,
                actor,
                posting_id,
                version_id=body.version_id,
                overrides=body.overrides,
                clear=body.clear,
            )
        except NotFound as exc:
            raise HTTPException(404, "not found") from exc
        except PostingRuleError as exc:
            raise _rule(exc) from exc
        return StatusOut(id=posting_id, status=status)

    async def _decide(actor: ActorContext, posting_id: UUID, run_id: UUID, accept: bool):
        try:
            return await uc.decide_fetch_confirmation(db, actor, posting_id, run_id, accept=accept)
        except NotFound as exc:
            raise HTTPException(404, "not found") from exc
        except PostingRuleError as exc:
            code = 403 if exc.code == "human_confirmation_required" else 409
            raise HTTPException(code, exc.code) from exc

    @router.post(
        "/job-postings/{posting_id}/fetch-runs/{run_id}/confirm",
        response_model=StatusOut,
        status_code=202,
    )
    async def confirm_cost(actor: writer, posting_id: UUID, run_id: UUID) -> StatusOut:
        new_run = await _decide(actor, posting_id, run_id, True)
        return StatusOut(id=new_run, status="pending")

    @router.post("/job-postings/{posting_id}/fetch-runs/{run_id}/decline", response_model=StatusOut)
    async def decline_cost(actor: writer, posting_id: UUID, run_id: UUID) -> StatusOut:
        await _decide(actor, posting_id, run_id, False)
        return StatusOut(id=run_id, status="failed")

    @router.post("/job-postings/{posting_id}/archive", response_model=StatusOut)
    async def archive(actor: writer, posting_id: UUID) -> StatusOut:
        try:
            status = await uc.archive_posting(db, actor, posting_id)
        except NotFound as exc:
            raise HTTPException(404, "not found") from exc
        return StatusOut(id=posting_id, status=status)

"""공통 FastAPI router. 각 실행 앱이 ``CoreDeps``를 주입해 include_router로 조립한다.

workspace는 요청 body·path·query로 받지 않는다. 항상 ``IdentityResolver``가 확정한
``ActorContext.workspace_id``를 쓴다.
"""

# NOTE: `from __future__ import annotations`를 쓰지 않는다. build_router 안에서 정의한
# 의존성을 Annotated로 참조하므로 FastAPI가 런타임에 타입을 해석할 수 있어야 한다.

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select

from career_core import __version__
from career_core.db.models import (
    Actor,
    DocumentBlock,
    ExtractionRun,
    Membership,
    ParseRun,
    Source,
    SourceVersion,
    Workspace,
)
from career_core.db.session import Database
from career_core.domain.brain import ClaimRuleError
from career_core.domain.sources import SUGGEST_TEXT_HEAD, suggest_purpose
from career_core.domain.workspace import ScopePermissionPolicy
from career_core.interfaces.http.activity import add_activity_routes
from career_core.interfaces.http.answers import add_answer_routes
from career_core.interfaces.http.applications import add_application_routes
from career_core.interfaces.http.brain import add_brain_routes
from career_core.interfaces.http.companies import add_company_routes
from career_core.interfaces.http.credentials import add_credential_routes
from career_core.interfaces.http.education import add_education_routes
from career_core.interfaces.http.judges import add_judge_routes
from career_core.interfaces.http.maintenance import add_maintenance_routes
from career_core.interfaces.http.models import add_model_routes
from career_core.interfaces.http.postings import add_posting_routes
from career_core.interfaces.http.profiles import add_profile_routes
from career_core.interfaces.http.references import add_reference_routes
from career_core.interfaces.http.search import add_search_routes
from career_core.interfaces.http.settings import add_settings_routes
from career_core.interfaces.http.study import add_study_routes
from career_core.interfaces.http.usage import add_usage_routes
from career_core.llm.gateway import ModelGateway
from career_core.ports import (
    ActorContext,
    AuditSink,
    BlobStore,
    IdentityResolver,
    PermissionPolicy,
)
from career_core.uploads import UploadRejected
from career_core.usecases import sources as sources_uc
from career_core.usecases.sources import upload_source


@dataclass(frozen=True, slots=True)
class CoreDeps:
    identity: IdentityResolver
    db: Database
    permissions: PermissionPolicy = field(default_factory=ScopePermissionPolicy)
    # 없으면 원본 업로드 경로를 등록하지 않는다.
    blobs: BlobStore | None = None
    max_upload_bytes: int = 20 * 1024 * 1024
    # 사용량 응답에 같이 보여줄 월 예산 (models.toml [budget]).
    monthly_budget_usd: Decimal | None = None
    # 없으면 AI 보충 질문 경로가 503을 돌려준다. 추출은 worker가 처리한다.
    models: ModelGateway | None = None
    # MCP 도구 호출 감사 기록. 없으면 core.audit_events(DbAuditSink)에 남긴다.
    audit: AuditSink | None = None
    # 원격 MCP의 외부 OAuth 토큰 검증 (interfaces.mcp.oauth.McpOAuth). 없으면 API 토큰만.
    mcp_oauth: Any = None
    # 웹 복원(묶음 올리기) 상한. 앱의 BodySizeLimit도 그 경로만 이 값으로 (interfaces.http.restore).
    max_restore_bytes: int = 2 * 1024 * 1024 * 1024
    # 시스템 화면 SSE의 상태 변경 힌트 (interfaces.http.live.StateHints). 없으면 DB 주소로 LISTEN.
    live: Any = None
    # API 토큰 관리 (ports.ApiTokenAdmin). 없으면 /core/tokens 경로를 등록하지 않는다.
    tokens: Any = None
    # 웹 복원 업로드를 받는 공유 폴더 (여러 프로세스면 모두 보는 곳). 없으면 CAREER_RESTORE_DIR → 시스템 임시.
    restore_dir: str | None = None
    # c0027: 웹 복원의 끊김 판정(초). 없으면 60. worker의 신호 간격은 RestoreDeps.heartbeat (기본 2)
    restore_stale_seconds: float | None = None
    # O22 화면에서 모델 연결·라우트·예산 (usecases.model_connections.ModelConnections). 없으면
    # /core/models/* 가 404 ``not_enabled`` (서비스는 운영자 설정을 쓴다). 게이트웨이(models)에도 같은 것을
    # overlay로 주어야 화면 설정이 호출에 쓰인다.
    model_connections: Any = None


class WorkspaceOut(BaseModel):
    id: UUID
    name: str


class MeOut(BaseModel):
    actor_id: UUID
    workspace: WorkspaceOut
    scopes: list[str]


class MemberOut(BaseModel):
    actor_id: UUID
    display_name: str
    role: str


class UploadOut(BaseModel):
    source_id: UUID
    version_id: UUID
    parse_run_id: UUID | None
    deduplicated: bool
    # c0029: 숨김 서식 글을 블록에 넣는지(keep)·빼는지(exclude, DOCX만). 같은 파일이면 처음 값
    hidden_text: str = "keep"
    # c0032: 원본의 용도·등급. 같은 파일이면 처음 원본의 용도 (증빙이면 등급은 restricted)
    purpose: str = "experience"
    data_class: str = "private"
    # c0039: 용도 확인을 기다림 (받은 자료함 "확인 필요"), 이 기기에서 읽은 글을 붙인 블록 수
    purpose_pending: bool = False
    text_blocks: int = 0


class ParseOut(BaseModel):
    run_id: UUID
    status: str
    pages_total: int | None
    missing_pages: list[int]
    error_code: str | None
    hidden_text: str = "keep"
    # c0039: 읽은 길 local | device(이 기기) | model(연결된 모델) | provider, 읽은 도구
    read_by: str | None = None
    read_engine: str | None = None


class VersionOut(BaseModel):
    id: UUID
    sha256: str
    mime_type: str
    size_bytes: int
    original_filename: str
    parse: ParseOut | None


class ExtractionBriefOut(BaseModel):
    id: UUID
    status: str
    error_code: str | None
    claims_created: int


class SourceOut(BaseModel):
    id: UUID
    title: str
    data_class: str
    versions: list[VersionOut]
    # c0032
    purpose: str = "experience"
    created_at: datetime | None = None
    # 최신 버전·최신 읽기 결과로 요청한 마지막 경험 추출
    extraction: ExtractionBriefOut | None = None
    # 이 원본에서 뽑은 확인 전 후보 / 이 원본을 근거로 단 확인된 주장
    pending_claims: int = 0
    confirmed_claims: int = 0
    # c0039
    purpose_pending: bool = False
    purpose_hint: str | None = None
    # c0043: 브라우저에서 보낸 글의 원래 주소 (파일로 올린 원본은 null)
    origin_url: str | None = None


class CaptureSourceIn(BaseModel):
    """보고 있는 페이지 보내기 (c0043): 브라우저가 페이지에서 고른 글(Markdown). 증빙은 받지 않는다."""

    url: str = Field(min_length=1, max_length=2000, pattern=r"^https?://")
    title: str | None = Field(default=None, max_length=sources_uc.MAX_CAPTURE_TITLE * 2)
    markdown: str = Field(min_length=1, max_length=sources_uc.MAX_CAPTURE_TEXT)
    purpose: Literal["experience", "reference", "proof"] = "reference"


class PurposePatch(BaseModel):
    purpose: Literal["experience", "proof", "reference"]
    # 선택: 등급도 함께. 증빙은 restricted만
    data_class: Literal["public", "private", "restricted"] | None = None


class PurposeChangeOut(BaseModel):
    source_id: UUID
    purpose: str
    data_class: str
    changed: bool
    discarded_claims: int
    discarded_experiences: int
    cancelled_extractions: int
    extraction_run_id: UUID | None


class SuggestPurposeIn(BaseModel):
    filename: str | None = Field(default=None, max_length=500)
    mime: str | None = Field(default=None, max_length=200)
    text_head: str | None = Field(default=None, max_length=SUGGEST_TEXT_HEAD * 2)


class SuggestPurposeOut(BaseModel):
    suggested_purpose: str
    matched: str | None


class BlockOut(BaseModel):
    id: UUID
    page: int | None
    ordinal: int
    origin: str
    text: str
    # 이 기기에서 읽은 글자의 원본 그림 위 위치 [x0, y0, x1, y1] (c0039, 없으면 null)
    bbox: list[float] | None = None


_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="authentication required",
    headers={"WWW-Authenticate": "Bearer"},
)


def build_router(deps: CoreDeps | None = None) -> APIRouter:
    """``deps`` 없이 만들면 공개 health만 노출한다 (인증이 필요한 경로는 등록하지 않음)."""
    router = APIRouter(prefix="/core", tags=["core"])

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "core_version": __version__}

    if deps is None:
        return router

    bearer = HTTPBearer(auto_error=False)

    async def current_actor(
        cred: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> ActorContext:
        if cred is None or not cred.credentials:
            raise _UNAUTHORIZED
        actor = await deps.identity.resolve(cred.credentials)
        if actor is None:
            raise _UNAUTHORIZED
        return actor

    def authorize(action: str):
        async def dependency(
            actor: Annotated[ActorContext, Depends(current_actor)],
        ) -> ActorContext:
            if not deps.permissions.allows(actor, action):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
            return actor

        return dependency

    @router.get("/me", response_model=MeOut)
    async def me(actor: Annotated[ActorContext, Depends(authorize("workspace.read"))]) -> MeOut:
        async with deps.db.workspace_session(actor.workspace_id) as s:
            ws = await s.get(Workspace, actor.workspace_id)
        if ws is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workspace not found")
        return MeOut(
            actor_id=actor.subject_id,
            workspace=WorkspaceOut(id=ws.id, name=ws.name),
            scopes=sorted(actor.scopes),
        )

    @router.get("/members", response_model=list[MemberOut])
    async def members(
        actor: Annotated[ActorContext, Depends(authorize("members.read"))],
    ) -> list[MemberOut]:
        async with deps.db.workspace_session(actor.workspace_id) as s:
            rows = (
                await s.execute(
                    select(Membership.actor_id, Actor.display_name, Membership.role)
                    .join(Actor, Actor.id == Membership.actor_id)
                    .order_by(Membership.created_at)
                )
            ).all()
        return [
            MemberOut(actor_id=r.actor_id, display_name=r.display_name, role=r.role) for r in rows
        ]

    add_usage_routes(router, deps, authorize)
    add_model_routes(router, deps, authorize)  # O22 (켜지 않으면 404 not_enabled)
    add_brain_routes(router, deps, authorize)
    add_search_routes(router, deps, authorize)
    add_posting_routes(router, deps, authorize)
    add_company_routes(router, deps, authorize)  # O13 회사 조사 (참고 지식)
    add_credential_routes(router, deps, authorize)  # O20 자격·이수 기록, 증빙 서류함
    add_reference_routes(router, deps, authorize)  # O14 참고 자료 보관함 (다른 사람의 글)
    add_profile_routes(
        router, deps, authorize
    )  # O21 프로필·증명사진 (사진 올리기는 blobs가 있을 때)
    add_education_routes(router, deps, authorize)  # c0040 학력
    add_answer_routes(router, deps, authorize)  # c0042 공통 답변·지난 제출본·기술 보기
    add_application_routes(router, deps, authorize)
    add_study_routes(router, deps, authorize)
    add_settings_routes(router, deps, authorize)  # c0021 공간 공용 설정
    add_judge_routes(router, deps, authorize)  # 판정 모델 (참고 의견)
    # O08 실행·승인·MCP. MCP는 같은 identity로 인증하고 같은 PermissionPolicy를 쓴다.
    from career_core.interfaces.http.runs import add_approval_routes, add_run_routes
    from career_core.interfaces.mcp.http import add_mcp_routes

    add_run_routes(router, deps, authorize)
    add_approval_routes(router, deps, authorize)
    add_mcp_routes(router, deps)
    add_maintenance_routes(router, deps, authorize)
    add_activity_routes(router, deps, authorize)
    from career_core.interfaces.http.audit import add_audit_routes

    add_audit_routes(router, deps, authorize)
    # c0019: 원격 MCP OAuth 연결·토큰 관리, 시스템 화면 실시간 갱신(SSE)
    from career_core.interfaces.http.live import add_live_routes
    from career_core.interfaces.http.oauth import add_oauth_routes

    add_oauth_routes(router, deps, authorize)
    add_live_routes(router, deps, authorize)

    if deps.tokens is not None:
        from career_core.interfaces.http.tokens import add_token_routes

        add_token_routes(router, deps, authorize)
    if deps.blobs is not None:
        _add_source_routes(router, deps, authorize)

    return router


def _add_source_routes(router: APIRouter, deps: CoreDeps, authorize) -> None:
    blobs = deps.blobs
    not_found = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    from career_core.interfaces.http.ingest import add_ingest_routes

    add_ingest_routes(router, deps, authorize)  # c0039 브레인에 넣기

    @router.post("/sources", response_model=UploadOut)
    async def upload(
        actor: Annotated[ActorContext, Depends(authorize("sources.write"))],
        file: Annotated[UploadFile, File()],
        title: Annotated[str | None, Form()] = None,
        data_class: Annotated[str, Form()] = "private",
        hidden_text: Annotated[str, Form()] = "keep",
        purpose: Annotated[str, Form()] = "experience",
        purpose_pending: Annotated[bool, Form()] = False,
        purpose_hint: Annotated[str | None, Form(max_length=500)] = None,
        # c0039: 이 기기에서 읽은 글 (JSON, ``PUT /sources/{id}/text-layer``와 같은 모양)
        text_layer: Annotated[str | None, Form(max_length=2_000_000)] = None,
    ) -> UploadOut:
        layer = None
        if text_layer:
            from pydantic import ValidationError

            from career_core.interfaces.http.ingest import TextLayerIn, to_layer

            try:
                layer = to_layer(TextLayerIn.model_validate_json(text_layer))
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail="invalid_text_layer") from exc
        data = await file.read(deps.max_upload_bytes + 1)
        try:
            result = await upload_source(
                deps.db,
                blobs,
                actor,
                filename=file.filename,
                data=data,
                declared_mime=file.content_type,
                max_bytes=deps.max_upload_bytes,
                title=title,
                data_class=data_class,
                hidden_text=hidden_text,
                purpose=purpose,
                purpose_pending=purpose_pending,
                purpose_hint=purpose_hint,
                text_layer=layer,
            )
        except UploadRejected as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from exc
        if layer is not None and result.deduplicated:
            # 같은 파일을 다시 넣음: 읽은 글이 아직 없을 때만 붙인다
            from career_core.domain.ingest import TextLayerError
            from career_core.usecases.ingest import attach_text_layer

            try:
                await attach_text_layer(
                    deps.db, actor, result.source_id, layer, version_id=result.version_id
                )
            except TextLayerError:
                pass
        return UploadOut(
            source_id=result.source_id,
            version_id=result.version_id,
            parse_run_id=result.parse_run_id,
            deduplicated=result.deduplicated,
            hidden_text=result.hidden_text,
            purpose=result.purpose,
            data_class=result.data_class,
            purpose_pending=result.purpose_pending,
            text_blocks=result.text_blocks,
        )

    @router.post("/sources/capture", response_model=UploadOut, status_code=201)
    async def capture(
        body: CaptureSourceIn,
        actor: Annotated[ActorContext, Depends(authorize("sources.write"))],
    ) -> UploadOut:
        """내 브라우저에서 보낸 페이지 글 (c0043). 참고 자료(기본)·내 경험 자료로 넣는다. 증빙은 422."""
        try:
            result = await sources_uc.capture_text_source(
                deps.db,
                blobs,
                actor,
                url=body.url,
                title=body.title,
                markdown=body.markdown,
                purpose=body.purpose,
                max_bytes=deps.max_upload_bytes,
            )
        except UploadRejected as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from exc
        return UploadOut.model_validate(result, from_attributes=True)

    @router.post("/sources/suggest-purpose", response_model=SuggestPurposeOut)
    async def suggest(
        body: SuggestPurposeIn,
        actor: Annotated[ActorContext, Depends(authorize("sources.read"))],
    ) -> SuggestPurposeOut:
        """올리기 전에 용도를 제안한다 (모델 없이 파일 이름·형식·앞부분 글). 사람이 확정한다."""
        got = suggest_purpose(body.filename, body.mime, body.text_head)
        return SuggestPurposeOut(suggested_purpose=got.purpose.value, matched=got.matched)

    @router.patch("/sources/{source_id}", response_model=PurposeChangeOut)
    async def change_purpose(
        source_id: UUID,
        body: PurposePatch,
        actor: Annotated[ActorContext, Depends(authorize("sources.write"))],
    ) -> PurposeChangeOut:
        try:
            result = await sources_uc.change_purpose(
                deps.db,
                actor,
                source_id,
                body.purpose,
                data_class=body.data_class,
                can_extract=deps.permissions.allows(actor, "brain.extract"),
            )
        except sources_uc.SourceNotFound as exc:
            raise not_found from exc
        except ClaimRuleError as exc:
            conflicts = ("source_has_confirmed_claims", "source_has_reference_items")
            code = 409 if exc.code in conflicts else 422
            raise HTTPException(status_code=code, detail=exc.code) from exc
        return PurposeChangeOut.model_validate(result, from_attributes=True)

    @router.get("/sources/{source_id}", response_model=SourceOut)
    async def get_source(
        source_id: UUID, actor: Annotated[ActorContext, Depends(authorize("sources.read"))]
    ) -> SourceOut:
        async with deps.db.workspace_session(actor.workspace_id) as s:
            source = await s.get(Source, source_id)
            if source is None:
                raise not_found
            rows = (
                await s.execute(
                    select(SourceVersion, ParseRun)
                    .outerjoin(ParseRun, ParseRun.source_version_id == SourceVersion.id)
                    .where(SourceVersion.source_id == source_id)
                    .order_by(SourceVersion.created_at, ParseRun.pipeline_version)
                )
            ).all()
        versions = [
            VersionOut(
                id=v.id,
                sha256=v.sha256,
                mime_type=v.mime_type,
                size_bytes=v.size_bytes,
                original_filename=v.original_filename,
                parse=None
                if r is None
                else ParseOut(
                    run_id=r.id,
                    status=r.status,
                    pages_total=r.pages_total,
                    missing_pages=list(r.missing_pages or []),
                    error_code=r.error_code,
                    hidden_text=r.hidden_text,
                    read_by=sources_uc.read_by(r),
                    read_engine=(r.text_layer or {}).get("engine"),
                ),
            )
            for v, r in rows
        ]
        latest = next((v for v in reversed(versions) if v.parse is not None), None)
        extraction = None
        if latest is not None:
            async with deps.db.workspace_session(actor.workspace_id) as s:
                ext = await s.scalar(
                    select(ExtractionRun)
                    .where(ExtractionRun.parse_run_id == latest.parse.run_id)
                    .order_by(ExtractionRun.created_at.desc())
                    .limit(1)
                )
            if ext is not None:
                extraction = ExtractionBriefOut(
                    id=ext.id,
                    status=ext.status,
                    error_code=ext.error_code,
                    claims_created=ext.claims_created,
                )
        counts = await sources_uc.source_claim_counts(deps.db, actor, source.id)
        return SourceOut(
            id=source.id,
            title=source.title,
            data_class=source.data_class,
            versions=versions,
            purpose=source.purpose,
            created_at=source.created_at,
            extraction=extraction,
            pending_claims=counts.pending,
            confirmed_claims=counts.confirmed,
            purpose_pending=source.purpose_pending,
            purpose_hint=source.purpose_hint,
            origin_url=source.origin_url,
        )

    @router.get("/parse-runs/{run_id}/blocks", response_model=list[BlockOut])
    async def get_blocks(
        run_id: UUID, actor: Annotated[ActorContext, Depends(authorize("sources.read"))]
    ) -> list[BlockOut]:
        async with deps.db.workspace_session(actor.workspace_id) as s:
            if await s.get(ParseRun, run_id) is None:
                raise not_found
            blocks = (
                await s.execute(
                    select(DocumentBlock)
                    .where(DocumentBlock.parse_run_id == run_id)
                    .order_by(DocumentBlock.ordinal)
                )
            ).scalars()
            return [
                BlockOut(
                    id=b.id,
                    page=b.page,
                    ordinal=b.ordinal,
                    origin=b.origin,
                    text=b.text,
                    bbox=b.bbox,
                )
                for b in blocks
            ]


__all__ = ["CoreDeps", "build_router"]

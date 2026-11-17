"""자격·이수 기록과 증빙 서류함 경로 (c0034, O20, plan §20).

읽기 ``credentials.read``, 쓰기 ``credentials.write``. 후보 확인은 사람만 (403 ``human_confirmation_required``).
번호는 끝 4자리만 보인다. ``?reveal=true``는 쓰기 권한이 있어야 하고(없으면 403 ``forbidden``) 감사 기록에 남는다.
오류는 ``{"detail": code}``: 없는 기록·원본 404 ``not found``, 증빙이 아닌 원본 409 ``source_not_proof``
(뽑기)·422 (잇기), 그 밖의 규칙 422.

- ``GET /proofs``: 증빙 서류함 (파일 정보, 읽기 상태, 주민등록번호 표시, 이어진 기록).
- ``POST /sources/{id}/extract-credentials``: 증빙 글에서 다시 뽑기 (로컬 규칙, 모델 호출 없음).
- ``GET /sources/{id}/original``: 원본 바이트. 기록한 sha256과 같을 때만 (다르면 409 ``original_changed``).
  제한 등급 원본은 감사 기록에 남는다.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import date, datetime
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from career_core.domain.credentials import (
    MAX_DETAIL,
    MAX_HOURS,
    MAX_ISSUER,
    MAX_NAME,
    MAX_NUMBER,
    MAX_SCORE,
    CredentialRuleError,
)
from career_core.domain.study import StudyRuleError
from career_core.ports import ActorContext
from career_core.usecases import credentials as uc
from career_core.usecases.brain import NotFound

Kind = Literal["license", "language", "course", "award", "degree", "other"]
Status = Literal["extracted", "self_confirmed", "evidence_supported"]

_STATUS = {
    "human_confirmation_required": 403,
}


def _rule(exc: CredentialRuleError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(exc.code, 422), detail=exc.code)


class CredentialIn(BaseModel):
    kind: Kind
    name: str = Field(min_length=1, max_length=MAX_NAME)
    issuer: str | None = Field(default=None, max_length=MAX_ISSUER)
    number: str | None = Field(default=None, max_length=MAX_NUMBER)
    acquired_on: date | None = None
    expires_on: date | None = None
    started_on: date | None = None
    ended_on: date | None = None
    score: str | None = Field(default=None, max_length=MAX_SCORE)
    hours: int | None = Field(default=None, ge=1, le=MAX_HOURS)
    detail: str | None = Field(default=None, max_length=MAX_DETAIL)
    source_id: UUID | None = None
    experience_id: UUID | None = None


class CredentialPatch(BaseModel):
    """준 칸만 바꾼다. null은 비우기 (이름·종류는 비울 수 없다)."""

    kind: Kind | None = None
    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    issuer: str | None = Field(default=None, max_length=MAX_ISSUER)
    number: str | None = Field(default=None, max_length=MAX_NUMBER)
    acquired_on: date | None = None
    expires_on: date | None = None
    started_on: date | None = None
    ended_on: date | None = None
    score: str | None = Field(default=None, max_length=MAX_SCORE)
    hours: int | None = Field(default=None, ge=1, le=MAX_HOURS)
    detail: str | None = Field(default=None, max_length=MAX_DETAIL)
    source_id: UUID | None = None
    experience_id: UUID | None = None


class FieldSpanOut(BaseModel):
    """뽑은 칸의 원문 위치: ``parse_run_id``의 블록 순번과 그 글 안의 [start, end). 글은 싣지 않는다."""

    block: int
    start: int
    end: int
    page: int | None = None


class CredentialOut(BaseModel):
    id: UUID
    kind: str
    name: str
    issuer: str | None
    number: str | None  # 가린 값 "••••1234" (reveal이면 원래 값)
    number_masked: bool
    acquired_on: date | None
    expires_on: date | None
    started_on: date | None
    ended_on: date | None
    score: str | None
    hours: int | None
    detail: str | None
    status: str
    source_id: UUID | None
    source_title: str | None
    experience_id: UUID | None
    experience_title: str | None
    parse_run_id: UUID | None
    field_spans: dict[str, FieldSpanOut]
    expiry: str | None  # expired | soon (90일 안) | null
    days_left: int | None
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IdOut(BaseModel):
    id: UUID


class StatusOut(BaseModel):
    id: UUID
    status: str


class ExtractOut(BaseModel):
    source_id: UUID
    credential_id: UUID | None
    # 후보가 없는 이유: not_parsed | no_text | nothing_found | already_recorded
    reason: str | None
    replaced: int
    fields: list[str]


class CredentialBriefOut(BaseModel):
    id: UUID
    kind: str
    name: str
    status: str
    expires_on: date | None


class ProofOut(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    original_filename: str | None
    mime_type: str | None
    size_bytes: int | None
    sha256: str | None
    parse_status: str | None
    parse_error: str | None
    resident_number: bool  # 주민등록번호로 보이는 값이 있다 (값은 싣지 않는다)
    credentials: list[CredentialBriefOut]
    candidates: int


def add_credential_routes(router: APIRouter, deps, authorize) -> None:
    db = deps.db
    reader = Annotated[ActorContext, Depends(authorize("credentials.read"))]
    writer = Annotated[ActorContext, Depends(authorize("credentials.write"))]
    source_reader = Annotated[ActorContext, Depends(authorize("sources.read"))]

    def _audit_sink():
        if deps.audit is not None:
            return deps.audit
        from career_core.adapters.audit import DbAuditSink

        return DbAuditSink(db)

    async def audit(actor: ActorContext, event: str) -> None:
        await _audit_sink().record(actor, event, {"channel": "http", "status": "ok"})

    async def guarded(call):
        try:
            return await call
        except NotFound as exc:
            raise HTTPException(status_code=404, detail="not found") from exc
        except CredentialRuleError as exc:
            raise _rule(exc) from exc
        except StudyRuleError as exc:  # 모르는 시간대
            raise HTTPException(status_code=422, detail=exc.code) from exc

    def can_reveal(actor: ActorContext, reveal: bool) -> bool:
        if reveal and not deps.permissions.allows(actor, "credentials.write"):
            raise HTTPException(status_code=403, detail="forbidden")
        return reveal

    def out(view) -> CredentialOut:
        return CredentialOut.model_validate(view, from_attributes=True)

    @router.get("/credentials", response_model=list[CredentialOut])
    async def index(
        actor: reader,
        kind: Kind | None = None,
        status: Status | None = None,
        expiring_within: Annotated[
            int | None,
            Query(
                ge=0, le=uc.MAX_EXPIRING_WITHIN, description="오늘부터 이 날 수 안에 만료되는 것만"
            ),
        ] = None,
        source_id: UUID | None = None,
        reveal: bool = False,
        tz: Annotated[str | None, Query(max_length=64)] = None,
    ) -> list[CredentialOut]:
        reveal = can_reveal(actor, reveal)
        rows = await guarded(
            uc.list_credentials(
                db,
                actor,
                kind=kind,
                status=status,
                expiring_within=expiring_within,
                source_id=source_id,
                reveal=reveal,
                tz=tz,
            )
        )
        if reveal and any(r.number for r in rows):
            await audit(actor, "credential_number_reveal")
        return [out(r) for r in rows]

    @router.post("/credentials", response_model=IdOut, status_code=201)
    async def create(actor: writer, body: CredentialIn) -> IdOut:
        cid = await guarded(uc.create_credential(db, actor, body.model_dump()))
        return IdOut(id=cid)

    @router.get("/credentials/{credential_id}", response_model=CredentialOut)
    async def detail(
        actor: reader,
        credential_id: UUID,
        reveal: bool = False,
        tz: Annotated[str | None, Query(max_length=64)] = None,
    ) -> CredentialOut:
        reveal = can_reveal(actor, reveal)
        view = await guarded(uc.get_credential(db, actor, credential_id, reveal=reveal, tz=tz))
        if reveal and view.number:
            await audit(actor, "credential_number_reveal")
        return out(view)

    @router.patch("/credentials/{credential_id}", response_model=IdOut)
    async def patch(actor: writer, credential_id: UUID, body: CredentialPatch) -> IdOut:
        values = body.model_dump(exclude_unset=True)
        for k in ("kind", "name"):
            if k in values and values[k] is None:
                raise HTTPException(status_code=422, detail=f"credential_{k}_required")
        await guarded(uc.update_credential(db, actor, credential_id, values))
        return IdOut(id=credential_id)

    @router.post("/credentials/{credential_id}/confirm", response_model=StatusOut)
    async def confirm(actor: writer, credential_id: UUID) -> StatusOut:
        status = await guarded(uc.confirm_credential(db, actor, credential_id))
        return StatusOut(id=credential_id, status=status)

    @router.delete("/credentials/{credential_id}", status_code=204)
    async def remove(actor: writer, credential_id: UUID) -> Response:
        await guarded(uc.delete_credential(db, actor, credential_id))
        return Response(status_code=204)

    @router.get("/proofs", response_model=list[ProofOut])
    async def proofs(actor: reader, source_id: UUID | None = None) -> list[ProofOut]:
        rows = await uc.list_proofs(db, actor, source_id=source_id)
        return [ProofOut.model_validate(r, from_attributes=True) for r in rows]

    @router.post("/sources/{source_id}/extract-credentials", response_model=ExtractOut)
    async def extract(actor: writer, source_id: UUID) -> ExtractOut:
        try:
            res = await uc.extract_from_source(db, actor, source_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail="not found") from exc
        except CredentialRuleError as exc:
            code = 409 if exc.code == "source_not_proof" else 422
            raise HTTPException(status_code=code, detail=exc.code) from exc
        return ExtractOut.model_validate(res, from_attributes=True)

    if deps.blobs is None:
        return

    @router.get("/sources/{source_id}/original")
    async def original(
        actor: source_reader, source_id: UUID, version_id: UUID | None = None
    ) -> Response:
        """원본 바이트를 그대로 (해시가 기록과 같을 때만). 브라우저가 열지 않고 내려받게 한다."""
        try:
            f = await uc.read_original(db, deps.blobs, actor, source_id, version_id=version_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail="not found") from exc
        except (FileNotFoundError, LookupError) as exc:
            raise HTTPException(status_code=404, detail="original_missing") from exc
        except uc.OriginalChanged as exc:
            raise HTTPException(status_code=409, detail="original_changed") from exc
        if f.data_class == "restricted":
            await audit(actor, "source_original_download")
        return Response(
            content=f.data,
            media_type=f.mime_type,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(f.filename)}",
                "X-Content-SHA256": f.sha256,
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )


__all__ = ["add_credential_routes"]

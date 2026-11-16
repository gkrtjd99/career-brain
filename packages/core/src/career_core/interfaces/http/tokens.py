"""API 토큰 목록·발급·폐기 경로. 소유자(workspace-admin)만, ``CoreDeps.tokens``를 주입했을 때만.

- 토큰 원문은 발급 응답에서 한 번만 돌려준다 (``Cache-Control: no-store``). 목록에는 원문·해시가 없다.
- 지금 요청에 쓴 토큰(``ActorContext.credential_id``)은 폐기하지 않는다 (잠겨 나가지 않게, 409).
- 다른 공간의 토큰 ID는 보이지 않고 폐기도 404다 (구현이 그 공간 문맥 안에서만 다룬다).

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from career_core.db.base import utcnow
from career_core.ports import ActorContext, ApiTokenRecord

MAX_LABEL = 100
MAX_EXPIRES_DAYS = 3650


class TokenOut(BaseModel):
    id: UUID
    label: str
    actor_id: UUID
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    status: str  # active | expired | revoked
    current: bool  # 지금 이 요청에 쓴 토큰


class TokenIn(BaseModel):
    label: str = Field(min_length=1, max_length=MAX_LABEL)
    expires_days: int | None = Field(default=None, ge=1, le=MAX_EXPIRES_DAYS)


class IssuedOut(BaseModel):
    token: TokenOut
    secret: str  # 한 번만 보인다


def token_status(r: ApiTokenRecord, now: datetime | None = None) -> str:
    if r.revoked_at is not None:
        return "revoked"
    if r.expires_at is not None and r.expires_at <= (now or utcnow()):
        return "expired"
    return "active"


def _out(r: ApiTokenRecord, actor: ActorContext) -> TokenOut:
    return TokenOut(
        id=r.id,
        label=r.label,
        actor_id=r.actor_id,
        created_at=r.created_at,
        expires_at=r.expires_at,
        last_used_at=r.last_used_at,
        revoked_at=r.revoked_at,
        status=token_status(r),
        current=actor.credential_id is not None and r.id == actor.credential_id,
    )


def add_token_routes(router: APIRouter, deps, authorize) -> None:
    admin = deps.tokens

    @router.get("/tokens", response_model=list[TokenOut])
    async def tokens(
        actor: Annotated[ActorContext, Depends(authorize("tokens.read"))],
    ) -> list[TokenOut]:
        return [_out(r, actor) for r in await admin.list(actor)]

    @router.post("/tokens", response_model=IssuedOut, status_code=201)
    async def issue(
        body: TokenIn,
        response: Response,
        actor: Annotated[ActorContext, Depends(authorize("tokens.manage"))],
    ) -> IssuedOut:
        label = " ".join(body.label.split())
        if not label:
            raise HTTPException(422, "invalid_label")
        record, secret = await admin.issue(actor, label=label, expires_days=body.expires_days)
        response.headers["Cache-Control"] = "no-store"
        return IssuedOut(token=_out(record, actor), secret=secret)

    @router.post("/tokens/{token_id}/revoke", response_model=TokenOut)
    async def revoke(
        token_id: UUID,
        actor: Annotated[ActorContext, Depends(authorize("tokens.manage"))],
    ) -> TokenOut:
        if actor.credential_id is not None and token_id == actor.credential_id:
            raise HTTPException(409, "token_in_use")
        if not await admin.revoke(actor, token_id):
            raise HTTPException(404, "not found")
        row = next((r for r in await admin.list(actor) if r.id == token_id), None)
        if row is None:  # 폐기 직후 사라질 일은 없지만 (공간 삭제 등) 원문 없이 404
            raise HTTPException(404, "not found")
        return _out(row, actor)

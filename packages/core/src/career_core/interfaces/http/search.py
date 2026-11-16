"""검색 경로.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from career_core.ports import ActorContext
from career_core.usecases import search as search_uc
from career_core.usecases.index_queue import enqueue_index


class HitOut(BaseModel):
    kind: str
    id: UUID
    snippet: str
    score: float
    keyword_rank: int | None
    semantic_rank: int | None
    claim_status: str | None
    experience_id: UUID | None
    experience_title: str | None
    source_id: UUID | None
    source_title: str | None
    page: int | None
    question: str | None


class SearchOut(BaseModel):
    query: str
    mode: str
    semantic_note: str | None
    hits: list[HitOut]


class QueuedOut(BaseModel):
    status: str


def add_search_routes(router: APIRouter, deps, authorize) -> None:
    reader = Annotated[ActorContext, Depends(authorize("search.read"))]
    writer = Annotated[ActorContext, Depends(authorize("search.reindex"))]

    @router.get("/search", response_model=SearchOut)
    async def search(
        actor: reader,
        q: Annotated[str, Query(max_length=search_uc.MAX_QUERY_CHARS)],
        kinds: Annotated[str | None, Query(description="claim,block,answer")] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
    ) -> SearchOut:
        wanted = tuple(k.strip() for k in kinds.split(",")) if kinds else search_uc.KINDS
        result = await search_uc.search(
            deps.db, actor, q, gateway=deps.models, kinds=wanted, limit=limit
        )
        return SearchOut(
            query=result.query,
            mode=result.mode,
            semantic_note=result.semantic_note,
            hits=[HitOut.model_validate(h, from_attributes=True) for h in result.hits],
        )

    @router.post("/search/reindex", response_model=QueuedOut, status_code=202)
    async def reindex(actor: writer) -> QueuedOut:
        await enqueue_index(deps.db, actor.workspace_id)
        return QueuedOut(status="queued")

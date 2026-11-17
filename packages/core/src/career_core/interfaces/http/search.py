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
    block_id: UUID | None = None
    parse_run_id: UUID | None = None
    ordinal: int | None = None
    match_start: int | None = None
    match_end: int | None = None
    # experience | source | study | reference (학습 기록·참고 자료는 내 경험이 아니다)
    record: str = "experience"
    study_item_id: UUID | None = None
    study_title: str | None = None
    # c0032: 원본 용도 (experience | reference). 증빙 원본의 글은 결과에 없다
    purpose: str = "experience"
    # 다시 정렬(routes.rerank)한 결과만: 앞 묶음 안의 순위. score는 RRF 점수 그대로
    rerank_rank: int | None = None


class ExpandedTermOut(BaseModel):
    term: str
    canonical: str
    spellings: list[str]


class SearchOut(BaseModel):
    query: str
    mode: str
    semantic_note: str | None
    hits: list[HitOut]
    include_reference: bool = False
    # 공간 별칭 사전으로 키워드 검색에 더한 표기 ("패스트API" → FastAPI, fast api)
    expanded_terms: list[ExpandedTermOut] = []
    # 앞 결과를 다시 정렬했는가 (routes.rerank가 있을 때만). 못 했으면 rerank_note에 이유
    reranked: bool = False
    rerank_note: str | None = None


class QueuedOut(BaseModel):
    status: str


def add_search_routes(router: APIRouter, deps, authorize) -> None:
    reader = Annotated[ActorContext, Depends(authorize("search.read"))]
    writer = Annotated[ActorContext, Depends(authorize("search.reindex"))]

    @router.get("/search", response_model=SearchOut)
    async def search(
        actor: reader,
        q: Annotated[str, Query(max_length=search_uc.MAX_QUERY_CHARS)],
        kinds: Annotated[
            str | None,
            Query(description="claim,block,answer (기본) + study_note,study_mistake (학습 기록)"),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
        include_reference: Annotated[
            bool,
            Query(
                description="참고 자료(남의 글·회사 자료)의 원문도 찾기. 결과는 record=reference"
            ),
        ] = False,
    ) -> SearchOut:
        wanted = tuple(k.strip() for k in kinds.split(",")) if kinds else search_uc.KINDS
        result = await search_uc.search(
            deps.db,
            actor,
            q,
            gateway=deps.models,
            kinds=wanted,
            limit=limit,
            include_reference=include_reference,
            rerank=True,
        )
        return SearchOut(
            query=result.query,
            mode=result.mode,
            semantic_note=result.semantic_note,
            include_reference=include_reference,
            hits=[HitOut.model_validate(h, from_attributes=True) for h in result.hits],
            expanded_terms=[
                ExpandedTermOut.model_validate(e, from_attributes=True)
                for e in result.expanded_terms
            ],
            reranked=result.reranked,
            rerank_note=result.rerank_note,
        )

    @router.post("/search/reindex", response_model=QueuedOut, status_code=202)
    async def reindex(actor: writer) -> QueuedOut:
        await enqueue_index(deps.db, actor.workspace_id)
        return QueuedOut(status="queued")

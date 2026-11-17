"""검색 색인(임베딩) 작업.

- 색인 대상: 폐기되지 않은 주장, 각 원본의 최신 파싱 블록, 인터뷰 답, 학습 메모(`study_note`),
  오답(`study_mistake`). 학습 메모·오답은 사용자가 쓴 글이므로 private 등급이다 (경험이 아니라 학습 기록).
- 본문 해시가 같은 벡터가 이미 있으면 다시 만들지 않는다. 본문이 바뀌면 새로 만든다.
- 자료 등급은 대상마다 정한다. API 임베딩이 허용하지 않는 등급(restricted 등)은 보내지 않고 건너뛴다.
  로컬 임베딩은 기계 밖으로 나가지 않으므로 모든 등급을 색인한다.
- 작업 하나는 공간(workspace) 단위다. 대기 중인 색인 작업이 있으면 새로 넣지 않는다.
- 남은 대상이 없을 때까지 단계를 반복하고, 마지막 확인과 작업 종료를 lock 아래 한 트랜잭션으로 한다
  (`index_queue` 참고). 실행 중에 바뀐 자료도 같은 작업이나 바로 다음 작업에서 색인된다.
- lease 확인(``jobs.lease.guard_lease``): 벡터 저장과 "남은 대상 없음" 종료는 먼저 작업 행을 잠그고 이
  worker가 아직 lease를 쥐고 있는지 본다. 임베딩 호출 도중 lease를 잃은 옛 worker가 새 worker가 이미 넣은
  새 본문 벡터를 옛 본문 벡터로 덮거나(다음 색인 요청까지 남는다), 새 worker의 작업을 대신 끝내지 않는다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from career_core.db import Database, Embedding
from career_core.domain.sources import DataClass
from career_core.jobs.lease import guard_lease
from career_core.llm import EMBED_PASSAGE, ModelCallError, ModelDenied
from career_core.llm.gateway import ModelGateway, scoped_gateway
from career_core.ports import ActorContext
from career_core.usecases.index_queue import (
    INDEX_JOB,
    enqueue_index,
    finish_running,
    lock_for_check,
    request_index,
)
from career_core.usecases.parsing import StepOutcome

__all__ = ["INDEX_JOB", "IndexDeps", "enqueue_index", "process_index", "request_index"]

SYSTEM_ACTOR = UUID(int=0)  # 장부에 남는 "시스템 작업" 주체
MAX_TEXT_CHARS = 8000  # 임베딩 모델 입력 상한 근처. 더 긴 블록은 앞부분만 쓴다.
_RANK = {DataClass.PUBLIC: 0, DataClass.PRIVATE: 1, DataClass.RESTRICTED: 2}


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# 각 대상 종류의 "현재 본문"과 자료 등급. 검색도 같은 정의를 쓴다.
CURRENT_BLOCKS = """
    SELECT DISTINCT ON (sv.source_id) pr.id
    FROM core.parse_runs pr
    JOIN core.source_versions sv ON sv.id = pr.source_version_id
    WHERE pr.status IN ('complete', 'partial')
    ORDER BY sv.source_id, sv.created_at DESC, pr.pipeline_version DESC, pr.created_at DESC
"""

# 학습 오답의 검색 본문: 문제·내 답·정답·이유 메모. 검색(search.py)도 같은 식을 쓴다 (본문 해시 비교).
STUDY_MISTAKE_BODY = (
    "concat_ws(chr(10), m.question, m.wrong_answer, m.correct_answer, m.reason_note)"
)

TARGETS_SQL = f"""
WITH items AS (
    SELECT 'claim' AS kind, c.id, c.statement AS body,
           COALESCE((
               SELECT max(CASE s.data_class WHEN 'restricted' THEN 2 WHEN 'private' THEN 1 ELSE 0 END)
               FROM core.claim_evidence ce
               JOIN core.source_spans sp ON sp.id = ce.source_span_id
               JOIN core.document_blocks b ON b.id = sp.document_block_id
               JOIN core.parse_runs pr ON pr.id = b.parse_run_id
               JOIN core.source_versions sv ON sv.id = pr.source_version_id
               JOIN core.sources s ON s.id = sv.source_id
               WHERE ce.claim_id = c.id
           ), 1) AS rank
    FROM core.claims c WHERE c.status <> 'retired'
    UNION ALL
    SELECT 'block', b.id, b.text,
           CASE s.data_class WHEN 'restricted' THEN 2 WHEN 'private' THEN 1 ELSE 0 END
    FROM core.document_blocks b
    JOIN core.parse_runs pr ON pr.id = b.parse_run_id
    JOIN core.source_versions sv ON sv.id = pr.source_version_id
    JOIN core.sources s ON s.id = sv.source_id
    WHERE b.parse_run_id IN ({CURRENT_BLOCKS})
    UNION ALL
    SELECT 'answer', a.id, a.answer, 1 FROM core.interview_answers a
    UNION ALL
    SELECT 'study_note', ss.id, ss.notes, 1 FROM core.study_sessions ss
    WHERE ss.notes IS NOT NULL AND btrim(ss.notes) <> ''
    UNION ALL
    SELECT 'study_mistake', m.id, {STUDY_MISTAKE_BODY}, 1 FROM core.study_mistakes m
)
SELECT i.kind, i.id, i.body, i.rank
FROM items i
LEFT JOIN core.embeddings e
  ON e.target_kind = i.kind AND e.target_id = i.id AND e.space = :space
 AND e.content_hash = encode(sha256(convert_to(left(i.body, {MAX_TEXT_CHARS}), 'UTF8')), 'hex')
WHERE e.id IS NULL AND i.rank = ANY(:ranks)
ORDER BY i.kind, i.id
LIMIT :limit
"""


@dataclass(frozen=True, slots=True)
class IndexDeps:
    db: Database
    gateway: ModelGateway | None
    per_step: int = 256  # 한 번에 처리할 대상 수. 남으면 작업을 다시 큐에 넣는다.


async def process_index(deps: IndexDeps, ws: UUID, _target: UUID) -> StepOutcome:
    gw = await scoped_gateway(deps.gateway, ws)  # 화면에서 덮은 임베딩 라우트 (O22)
    space = gw.embedding_space() if gw else None
    if gw is None or space is None:
        return StepOutcome(done=True)  # 임베딩을 쓰지 않는 설정: 키워드 검색만
    actor = ActorContext(SYSTEM_ACTOR, ws)
    ranks = [r for dc, r in _RANK.items() if gw.embedding_allows(actor, dc)]
    if not ranks:
        return StepOutcome(done=True)
    async with deps.db.workspace_session(ws) as s:
        # 작업 행을 먼저 잠근다 (lease 확인). 그 다음 예약 트랜잭션(공유 lock)이 끝난 뒤에 남은 대상을 본다.
        # 없으면 같은 트랜잭션에서 작업을 끝낸다.
        await guard_lease(s, ws)
        await lock_for_check(s, ws)
        rows = (
            await s.execute(
                text(TARGETS_SQL), {"space": space, "ranks": ranks, "limit": deps.per_step}
            )
        ).all()
        if not rows:
            await finish_running(s, ws)
    if not rows:
        return StepOutcome(done=True)

    batch = gw.config.embedding.batch_size
    by_rank: dict[int, list] = {}
    for row in rows:
        by_rank.setdefault(row.rank, []).append(row)
    names = {r: dc.value for dc, r in _RANK.items()}
    for rank, items in by_rank.items():
        for i in range(0, len(items), batch):
            chunk = items[i : i + batch]
            bodies = [r.body[:MAX_TEXT_CHARS] for r in chunk]
            try:
                result = await gw.embed(
                    actor,
                    bodies,
                    data_classes=frozenset({names[rank]}),
                    input_type=EMBED_PASSAGE,
                )
            except ModelDenied:
                return StepOutcome(done=True)  # 예산·키 문제: 다음 색인 요청 때 다시 시도
            except ModelCallError as exc:
                if exc.retryable:
                    raise
                return StepOutcome(done=True)
            if result.space != space:
                return StepOutcome(done=True)  # 설정이 바뀌는 중: 다음 색인 요청 때 다시
            await _store(deps.db, ws, result, chunk, bodies)
    # 남은 대상이 없다고 확인될 때까지 다시 돈다 (그 사이에 바뀐 자료도 다음 단계가 본다).
    return StepOutcome(done=False, retry_after=0)


async def _store(db: Database, ws: UUID, result, chunk, bodies) -> None:
    async with db.workspace_session(ws) as s:
        await guard_lease(s, ws)  # 색인 작업의 대상은 공간 ID
        for row, body, vector in zip(chunk, bodies, result.vectors, strict=True):
            stmt = insert(Embedding).values(
                id=uuid4(),
                workspace_id=ws,
                target_kind=row.kind,
                target_id=row.id,
                space=result.space,
                dims=len(vector),
                content_hash=content_hash(body),
                embedding=list(vector),
                model_call_id=UUID(result.call_id),
            )
            await s.execute(
                stmt.on_conflict_do_update(
                    index_elements=["workspace_id", "target_kind", "target_id", "space"],
                    set_={
                        "dims": stmt.excluded.dims,
                        "content_hash": stmt.excluded.content_hash,
                        "embedding": stmt.excluded.embedding,
                        "model_call_id": stmt.excluded.model_call_id,
                        "updated_at": text("now()"),
                    },
                )
            )

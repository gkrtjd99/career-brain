"""모델에 보낼 주장 묶음의 자료 등급.

주장은 근거 원본의 등급을 이어받는다 (색인과 같은 규칙: `indexing.TARGETS_SQL`).
근거가 없는 주장·사용자가 직접 쓴 글은 private로 본다.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CLAIM_CLASSES_SQL = text("""
SELECT DISTINCT s.data_class
FROM core.claim_evidence ce
JOIN core.source_spans sp ON sp.id = ce.source_span_id
JOIN core.document_blocks b ON b.id = sp.document_block_id
JOIN core.parse_runs pr ON pr.id = b.parse_run_id
JOIN core.source_versions sv ON sv.id = pr.source_version_id
JOIN core.sources s ON s.id = sv.source_id
WHERE ce.claim_id = ANY(:ids)
""")

RESTRICTED_CLAIMS_SQL = text("""
SELECT DISTINCT ce.claim_id
FROM core.claim_evidence ce
JOIN core.source_spans sp ON sp.id = ce.source_span_id
JOIN core.document_blocks b ON b.id = sp.document_block_id
JOIN core.parse_runs pr ON pr.id = b.parse_run_id
JOIN core.source_versions sv ON sv.id = pr.source_version_id
JOIN core.sources s ON s.id = sv.source_id
WHERE ce.claim_id = ANY(:ids) AND s.data_class = 'restricted'
""")


async def claim_data_classes(s: AsyncSession, claim_ids: Iterable[UUID]) -> frozenset[str]:
    """주장들의 근거 원본 등급 + private. restricted가 있으면 게이트웨이가 외부 전송을 거부한다."""
    ids = [UUID(str(i)) for i in claim_ids]
    if not ids:
        return frozenset({"private"})
    rows = (await s.execute(CLAIM_CLASSES_SQL, {"ids": ids})).scalars()
    return frozenset({"private", *rows})


async def restricted_claims(s: AsyncSession, claim_ids: Iterable[UUID]) -> set[UUID]:
    ids = [UUID(str(i)) for i in claim_ids]
    if not ids:
        return set()
    return set((await s.execute(RESTRICTED_CLAIMS_SQL, {"ids": ids})).scalars())

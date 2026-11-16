"""core: 판정 응답 캐시에 대상 ID — 주장·지원 문서를 지우면 그 응답도 지운다

Revision ID: c0026
Revises: c0025

- ``core.judge_cache.claim_ids``·``document_ids`` (uuid[], 기본 빈 배열): 그 응답을 만든 요청에 들어간 주장
  (``judge_claim``: 후보 주장들)과 지원 문서(``judge_visibility``: 합쳐 보낸 문서들).
- trigger ``judge_cache_forget`` (AFTER DELETE, 문장 단위, 지운 행 전이 표): ``core.claims``·``core.application_documents``
  에서 지운 ID가 들어 있는 같은 공간의 캐시 행을 지운다. 경험·지원을 지울 때의 CASCADE, 복원의 삭제 기록 재적용도
  같은 trigger를 지난다. SECURITY INVOKER라 지우는 세션의 공간 RLS(FORCE) 아래에서 돈다.
- 이 migration 전의 행은 대상을 모른다 (빈 배열). 만료(기본 30일)나 ``DELETE /core/judges/cache``로만 지워진다.
- 내보내지 않는 표(export.EXCLUDED_TABLES)와 trigger뿐이라 묶음 변환은 ``no_data_change("c0026")``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "c0026"
down_revision = "c0025"
branch_labels = None
depends_on = None

S = "core"
# 지우면 캐시를 따라 지우는 표 → 캐시의 칸
TARGETS = {"claims": "claim_ids", "application_documents": "document_ids"}

_FUNCTION = """
CREATE FUNCTION "{s}".judge_cache_forget() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  -- 지운 행(전이 표 gone)의 ID가 든 같은 공간의 판정 응답을 지운다. 지우는 세션의 RLS 아래에서 돈다.
  IF TG_TABLE_NAME = 'claims' THEN
    DELETE FROM core.judge_cache c
     USING (SELECT workspace_id, array_agg(id) AS ids FROM gone GROUP BY workspace_id) g
     WHERE c.workspace_id = g.workspace_id AND c.claim_ids && g.ids;
  ELSIF TG_TABLE_NAME = 'application_documents' THEN
    DELETE FROM core.judge_cache c
     USING (SELECT workspace_id, array_agg(id) AS ids FROM gone GROUP BY workspace_id) g
     WHERE c.workspace_id = g.workspace_id AND c.document_ids && g.ids;
  END IF;
  RETURN NULL;
END
$$;
"""


def upgrade() -> None:
    for column in TARGETS.values():
        op.add_column(
            "judge_cache",
            sa.Column(column, ARRAY(sa.Uuid()), nullable=False, server_default="{}"),
            schema=S,
        )
    op.execute(_FUNCTION.format(s=S))
    for table in TARGETS:
        op.execute(
            f'CREATE TRIGGER judge_cache_forget AFTER DELETE ON "{S}"."{table}" '
            "REFERENCING OLD TABLE AS gone FOR EACH STATEMENT "
            f'EXECUTE FUNCTION "{S}".judge_cache_forget()'
        )


def downgrade() -> None:
    for table in TARGETS:
        op.execute(f'DROP TRIGGER judge_cache_forget ON "{S}"."{table}"')
    op.execute(f'DROP FUNCTION "{S}".judge_cache_forget()')
    for column in TARGETS.values():
        op.drop_column("judge_cache", column, schema=S)

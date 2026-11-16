"""core: 감사 기록·삭제 기록·외부 OAuth 신원, 승인 즉시 무효화 trigger (O08·O11 백로그)

- ``audit_events``: MCP 도구 호출 등 감사 사건 (추가·조회만). 인자 원문은 없고 sha256만.
- ``deletions``: 사용자가 지운 원본·공고·지원·학습 항목의 삭제 기록 (추가·조회만). 오래된 묶음을
  복원한 뒤 다시 적용해 지운 자료가 되살아나지 않게 한다. DELETE trigger가 남긴다.
- ``oauth_identities``: 외부 인가 서버의 (issuer, subject) → (공간, actor) 연결. 토큰이 공간을 정한다.
- 승인 대상(지원·문서·공고)이 바뀌면 그 대상의 열린 승인을 같은 트랜잭션에서 ``invalidated``로 닫고,
  그 승인을 기다리던 실행을 깨운다. 읽을 때·쓸 때의 해시 비교는 그대로 둔다 (최종 방어).

Revision ID: c0014
Revises: c0013
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import JSONB

revision = "c0014"
down_revision = "c0013"
branch_labels = None
depends_on = None

S = "core"

# 사용자 삭제를 기록하는 표. 다른 표는 이 표들의 FK CASCADE로 같이 지워진다.
TOMBSTONE_TABLES = ("sources", "job_postings", "applications", "study_items")


def _ws() -> sa.Column:
    return sa.Column(
        "workspace_id",
        sa.Uuid(),
        sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )


def _ts(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    # ------------------------------------------------------------ 감사 기록
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("actor_id", sa.Uuid()),
        sa.Column("actor_kind", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),  # mcp-http | mcp-stdio | …
        sa.Column("event", sa.Text(), nullable=False),  # tool_call
        sa.Column("tool", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),  # ok | error
        sa.Column("error_code", sa.Text()),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        # 인자 정규 JSON의 sha256. 원문 인자는 남기지 않는다.
        sa.Column("args_sha256", sa.Text()),
        sa.Column("attributes", JSONB(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.CheckConstraint("actor_kind in ('human', 'agent')", name="ck_audit_events_actor_kind"),
        sa.CheckConstraint("status in ('ok', 'error')", name="ck_audit_events_status"),
        sa.CheckConstraint("duration_ms >= 0", name="ck_audit_events_duration"),
        sa.CheckConstraint(
            "args_sha256 is null or args_sha256 ~ '^[0-9a-f]{64}$'", name="ck_audit_events_sha256"
        ),
        sa.CheckConstraint("length(coalesce(tool, '')) <= 100", name="ck_audit_events_tool"),
        schema=S,
    )
    op.create_index(
        "ix_audit_events_workspace_created",
        "audit_events",
        ["workspace_id", "created_at"],
        schema=S,
    )

    # ------------------------------------------------------------ 삭제 기록
    op.create_table(
        "deletions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("target_table", sa.Text(), nullable=False),  # core.<table>
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "target_table", "target_id"),
        sa.CheckConstraint(
            "target_table in (" + ", ".join(f"'core.{t}'" for t in TOMBSTONE_TABLES) + ")",
            name="ck_deletions_target_table",
        ),
        schema=S,
    )
    op.execute(
        f"""
CREATE FUNCTION "{S}".record_deletion() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- 그 공간 문맥의 삭제(사용자·앱)만 기록한다. 공간 자체를 지우는 CASCADE와 문맥 없는 관리 작업은 뺀다.
  IF core.current_workspace_id() IS DISTINCT FROM OLD.workspace_id
     OR NOT EXISTS (SELECT 1 FROM core.workspaces w WHERE w.id = OLD.workspace_id) THEN
    RETURN OLD;
  END IF;
  INSERT INTO core.deletions (id, workspace_id, target_table, target_id, deleted_at, created_at)
  VALUES (gen_random_uuid(), OLD.workspace_id, TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME, OLD.id,
          now(), now())
  ON CONFLICT DO NOTHING;
  RETURN OLD;
END
$$;
"""
    )
    for table in TOMBSTONE_TABLES:
        op.execute(
            f'CREATE TRIGGER record_deletion AFTER DELETE ON "{S}"."{table}" '
            f'FOR EACH ROW EXECUTE FUNCTION "{S}".record_deletion()'
        )

    # ------------------------------------------------------------ 외부 OAuth 신원
    op.create_table(
        "oauth_identities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        _ts(),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["workspace_id", "actor_id"],
            [f"{S}.memberships.workspace_id", f"{S}.memberships.actor_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "length(issuer) between 1 and 500 and length(subject) between 1 and 500",
            name="ck_oauth_identities_length",
        ),
        schema=S,
    )
    # 한 (issuer, subject)는 살아 있는 연결 하나만 (어느 공간인지 토큰이 정하려면 유일해야 한다)
    op.create_index(
        "uq_oauth_identities_active",
        "oauth_identities",
        ["issuer", "subject"],
        unique=True,
        schema=S,
        postgresql_where=sa.text("revoked_at is null"),
    )

    for table in ("audit_events", "deletions", "oauth_identities"):
        for stmt in rls.workspace_isolation_sql(S, table):
            op.execute(stmt)
    # 공간 문맥 없이 (issuer, subject)로 찾는 조회 함수(소유자 권한)만 읽을 수 있는 정책.
    # FORCE는 그대로라서 앱 계정은 자기 공간 행만 본다.
    op.execute(
        f'CREATE POLICY oauth_lookup ON "{S}".oauth_identities FOR SELECT TO CURRENT_USER '
        "USING (true)"
    )
    op.execute(
        f"""
CREATE FUNCTION "{S}".lookup_oauth_identity(p_issuer text, p_subject text)
RETURNS TABLE (workspace_id uuid, actor_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $$
  SELECT i.workspace_id, i.actor_id
  FROM core.oauth_identities i
  WHERE i.issuer = p_issuer AND i.subject = p_subject AND i.revoked_at IS NULL
$$;
"""
    )
    op.execute(f'REVOKE ALL ON FUNCTION "{S}".lookup_oauth_identity(text, text) FROM PUBLIC')
    op.execute(
        f'GRANT EXECUTE ON FUNCTION "{S}".lookup_oauth_identity(text, text) TO {rls.APP_ROLE}'
    )
    # 감사·삭제 기록은 고치거나 지우지 않는다.
    op.execute(rls.grant_app_sql(S, "audit_events", "SELECT, INSERT"))
    op.execute(rls.grant_app_sql(S, "deletions", "SELECT, INSERT"))
    op.execute(rls.grant_app_sql(S, "oauth_identities", "SELECT, INSERT, UPDATE"))

    # ------------------------------------------------------------ 승인 즉시 무효화
    # 제출 승인(applications.submit)의 대상 버전에 들어가는 칸이 바뀌면 열린 승인을 바로 닫는다.
    # 칸 목록은 usecases/applications.py::submission_target과 같다. 거기에 칸을 더하면 여기도 더한다
    # (빠져도 조회·결정·사용 때의 해시 비교가 막는다).
    op.execute(
        f"""
CREATE FUNCTION "{S}".invalidate_application_approvals(p_application uuid, p_reason text)
RETURNS void LANGUAGE sql AS $$
  UPDATE core.approvals
     SET status = 'invalidated', invalid_reason = p_reason, updated_at = now()
   WHERE target_kind = 'application' AND target_id = p_application
     AND status IN ('pending', 'approved')
     -- 복원이 행을 넣는 동안(트랜잭션 로컬 설정)은 그대로 둔다: 묶음의 승인 상태를 바꾸지 않게
     AND coalesce(current_setting('career.restoring', true), '') <> 'on';
$$;
"""
    )
    op.execute(
        f"""
CREATE FUNCTION "{S}".approvals_on_application_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    PERFORM core.invalidate_application_approvals(OLD.id, 'target_missing');
  ELSIF NEW.status IS DISTINCT FROM OLD.status OR NEW.posting_id IS DISTINCT FROM OLD.posting_id THEN
    PERFORM core.invalidate_application_approvals(NEW.id, 'target_changed');
  END IF;
  RETURN NULL;
END
$$;
"""
    )
    op.execute(
        f"""
CREATE FUNCTION "{S}".approvals_on_document_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    PERFORM core.invalidate_application_approvals(NEW.application_id, 'target_changed');
  ELSIF TG_OP = 'DELETE' THEN
    PERFORM core.invalidate_application_approvals(OLD.application_id, 'target_changed');
  ELSIF NEW.current_revision IS DISTINCT FROM OLD.current_revision
     OR NEW.char_limit IS DISTINCT FROM OLD.char_limit
     OR NEW.byte_limit IS DISTINCT FROM OLD.byte_limit
     OR NEW.count_rule IS DISTINCT FROM OLD.count_rule
     OR NEW.question_prompt IS DISTINCT FROM OLD.question_prompt
     OR NEW.application_id IS DISTINCT FROM OLD.application_id THEN
    PERFORM core.invalidate_application_approvals(OLD.application_id, 'target_changed');
    PERFORM core.invalidate_application_approvals(NEW.application_id, 'target_changed');
  END IF;
  RETURN NULL;
END
$$;
"""
    )
    op.execute(
        f"""
CREATE FUNCTION "{S}".approvals_on_posting_change() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE a uuid;
BEGIN
  IF NEW.confirmed_version_id IS DISTINCT FROM OLD.confirmed_version_id
     OR NEW.fields IS DISTINCT FROM OLD.fields
     OR NEW.pending_changes IS DISTINCT FROM OLD.pending_changes THEN
    FOR a IN SELECT id FROM core.applications WHERE posting_id = NEW.id LOOP
      PERFORM core.invalidate_application_approvals(a, 'target_changed');
    END LOOP;
  END IF;
  RETURN NULL;
END
$$;
"""
    )
    op.execute(
        f'CREATE TRIGGER approvals_on_application_change AFTER UPDATE OR DELETE ON "{S}".applications '
        f'FOR EACH ROW EXECUTE FUNCTION "{S}".approvals_on_application_change()'
    )
    op.execute(
        f"CREATE TRIGGER approvals_on_document_change "
        f'AFTER INSERT OR UPDATE OR DELETE ON "{S}".application_documents '
        f'FOR EACH ROW EXECUTE FUNCTION "{S}".approvals_on_document_change()'
    )
    op.execute(
        f'CREATE TRIGGER approvals_on_posting_change AFTER UPDATE ON "{S}".job_postings '
        f'FOR EACH ROW EXECUTE FUNCTION "{S}".approvals_on_posting_change()'
    )

    # 열린 승인이 무효·만료로 닫히면, 그 승인을 기다리던 실행을 다시 큐에 넣는다 (다음 단계가 사유를 보고
    # 실패로 끝낸다). 어디서 닫혔든(위 trigger, 조회 때 확인) 같은 길로 간다.
    op.execute(
        f"""
CREATE FUNCTION "{S}".approvals_wake_run() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE r core.runs%ROWTYPE;
BEGIN
  IF NEW.run_id IS NULL OR OLD.status NOT IN ('pending', 'approved')
     OR NEW.status NOT IN ('invalidated', 'expired') THEN
    RETURN NULL;
  END IF;
  UPDATE core.runs SET status = 'queued', updated_at = now()
   WHERE id = NEW.run_id AND status = 'waiting_approval' AND approval_id = NEW.id
  RETURNING * INTO r;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  INSERT INTO core.run_events (id, workspace_id, run_id, seq, kind, status, step, code, data,
                               created_at)
  VALUES (gen_random_uuid(), r.workspace_id, r.id,
          (SELECT coalesce(max(e.seq), 0) + 1 FROM core.run_events e WHERE e.run_id = r.id),
          'status', 'queued', r.step, 'approval_' || NEW.status, '{{}}'::jsonb, now());
  INSERT INTO core.jobs (id, workspace_id, kind, target_id, status, attempts, available_at,
                         created_at)
  VALUES (gen_random_uuid(), r.workspace_id, 'run', r.id, 'queued', 0, now(), now())
  ON CONFLICT DO NOTHING;
  RETURN NULL;
END
$$;
"""
    )
    op.execute(
        f'CREATE TRIGGER approvals_wake_run AFTER UPDATE OF status ON "{S}".approvals '
        f'FOR EACH ROW EXECUTE FUNCTION "{S}".approvals_wake_run()'
    )


def downgrade() -> None:
    op.execute(f'DROP TRIGGER approvals_wake_run ON "{S}".approvals')
    op.execute(f'DROP FUNCTION "{S}".approvals_wake_run()')
    op.execute(f'DROP TRIGGER approvals_on_posting_change ON "{S}".job_postings')
    op.execute(f'DROP TRIGGER approvals_on_document_change ON "{S}".application_documents')
    op.execute(f'DROP TRIGGER approvals_on_application_change ON "{S}".applications')
    op.execute(f'DROP FUNCTION "{S}".approvals_on_posting_change()')
    op.execute(f'DROP FUNCTION "{S}".approvals_on_document_change()')
    op.execute(f'DROP FUNCTION "{S}".approvals_on_application_change()')
    op.execute(f'DROP FUNCTION "{S}".invalidate_application_approvals(uuid, text)')
    op.execute(f'DROP FUNCTION "{S}".lookup_oauth_identity(text, text)')
    for table in TOMBSTONE_TABLES:
        op.execute(f'DROP TRIGGER record_deletion ON "{S}"."{table}"')
    op.execute(f'DROP FUNCTION "{S}".record_deletion()')
    op.drop_table("oauth_identities", schema=S)
    op.drop_table("deletions", schema=S)
    op.drop_table("audit_events", schema=S)

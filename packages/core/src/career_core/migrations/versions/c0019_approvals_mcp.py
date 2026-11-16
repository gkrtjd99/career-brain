"""core: 승인 대상 버전을 DB 함수 하나로, OAuth 토큰 기록·폐기, 감사 기록 보존, 상태 변경 알림

Revision ID: c0019
Revises: c0018

- 승인 대상 버전: ``core.application_target_version(app)``(SQL 표준 본문 ``BEGIN ATOMIC``) 하나가
  제출 승인의 대상 버전 해시를 계산한다. 앱의 조회·결정·사용 확인(``submission_target``)과 즉시 무효화
  trigger가 **같은 함수**를 부른다. trigger는 칸 목록을 따로 두지 않고, 대상 표의 행이 바뀌면 열린 승인이
  있을 때만 버전을 다시 계산해 요청 때와 다르면 닫는다. 그래서 칸 목록을 두 곳에서 맞출 일이 없다.
  ``BEGIN ATOMIC`` 본문은 PostgreSQL이 읽는 표·칸을 ``pg_depend``에 적어 두므로, 함수가 읽는 표마다
  다시 계산 trigger가 있는지 시험이 확인한다 (``test_approvals_mcp.py``).
  c0014의 칸 비교 trigger(``approvals_on_*``)는 지운다. 버전 형식이 바뀌므로 지금 열린 제출 승인 중
  새 버전과 다른 것은 ``target_changed``로 닫는다 (기다리던 실행은 c0014 ``approvals_wake_run``이 깨운다).
- ``oauth_tokens``: 원격 MCP에서 본 외부 액세스 토큰(``jti``, 없으면 토큰 sha256) 기록과 폐기 목록.
  공간 문맥에서만 읽고 쓴다 (FORCE RLS). 연결(``oauth_identities``)이 지워지면 같이 지워진다.
- 감사 기록 보존: ``core.purge_audit_events(keep_days)``(SECURITY DEFINER, ``search_path`` 고정).
  앱 역할은 ``audit_events``에 SELECT·INSERT만 있으므로 지우기는 이 함수로만 한다. 호출한 트랜잭션의
  공간 문맥 안에서, ``keep_days``(최소 7일)보다 오래된 행만 지운다.
- 상태 변경 알림: ``runs``·``approvals``가 바뀐 문장마다 ``NOTIFY career_state, '<표 이름>'``.
  LISTEN에는 권한 검사가 없으므로 공간·대상 ID를 넣지 않는다. 받는 쪽(SSE)이 RLS 아래에서 다시 읽는다.
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls

revision = "c0019"
down_revision = "c0018"
branch_labels = None
depends_on = None

S = "core"
STATE_CHANNEL = "career_state"  # career_core.interfaces.http.live.STATE_CHANNEL
MIN_AUDIT_KEEP_DAYS = 7  # career_core.usecases.audit.MIN_RETENTION_DAYS


def upgrade() -> None:
    _approval_target_version()
    _oauth_tokens()
    _audit_retention()
    _state_notify()


# ---------------------------------------------------------------- 1. 승인 대상 버전


def _approval_target_version() -> None:
    # 제출 승인(applications.submit)의 대상 버전. 칸을 더하거나 빼는 곳은 여기 한 곳이다.
    # jsonb::text는 키 순서가 정해진 정규 형태라 같은 상태면 같은 해시가 나온다.
    op.execute(
        f"""
CREATE FUNCTION "{S}".application_target_version(p_application uuid) RETURNS text
LANGUAGE sql STABLE
BEGIN ATOMIC
  SELECT encode(sha256(convert_to(jsonb_build_object(
      'status', a.status,
      'posting_id', p.id,
      'posting_version_id', p.confirmed_version_id,
      'posting_fields', p.fields,
      'posting_pending_changes', CASE jsonb_typeof(p.pending_changes)
          WHEN 'array' THEN (SELECT coalesce(jsonb_agg(x.value ORDER BY x.value), '[]'::jsonb)
                               FROM jsonb_array_elements(p.pending_changes) AS x)
          ELSE coalesce(p.pending_changes, '[]'::jsonb) END,
      'documents', (SELECT coalesce(jsonb_agg(jsonb_build_array(
                         d.id, d.current_revision, r.content_hash, d.char_limit, d.byte_limit,
                         d.count_rule, d.question_prompt) ORDER BY d.id), '[]'::jsonb)
                      FROM core.application_documents d
                      LEFT JOIN core.document_revisions r
                        ON r.document_id = d.id AND r.revision_no = d.current_revision
                     WHERE d.application_id = a.id)
    )::text, 'UTF8')), 'hex')
  FROM core.applications a
  JOIN core.job_postings p ON p.id = a.posting_id
  WHERE a.id = p_application;
END;
"""
    )
    # 열린 승인이 있을 때만 다시 계산한다 (문서 저장마다 해시를 만들지 않게).
    op.execute(
        f"""
CREATE FUNCTION "{S}".recheck_application_approvals(p_application uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE v text;
BEGIN
  -- 복원이 행을 넣는 동안(트랜잭션 로컬 설정)은 그대로 둔다: 묶음의 승인 상태를 바꾸지 않게
  IF p_application IS NULL OR coalesce(current_setting('career.restoring', true), '') = 'on' THEN
    RETURN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM core.approvals a
                  WHERE a.target_kind = 'application' AND a.target_id = p_application
                    AND a.status IN ('pending', 'approved')) THEN
    RETURN;
  END IF;
  v := core.application_target_version(p_application);
  UPDATE core.approvals a
     SET status = 'invalidated',
         invalid_reason = CASE WHEN v IS NULL THEN 'target_missing' ELSE 'target_changed' END,
         updated_at = now()
   WHERE a.target_kind = 'application' AND a.target_id = p_application
     AND a.status IN ('pending', 'approved')
     AND a.target_version IS DISTINCT FROM v;
END
$$;
"""
    )
    op.execute(
        f"""
CREATE FUNCTION "{S}".approvals_recheck_application() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE a uuid;
BEGIN
  IF TG_TABLE_NAME = 'applications' THEN
    PERFORM core.recheck_application_approvals(CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END);
  ELSIF TG_TABLE_NAME = 'application_documents' THEN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
      PERFORM core.recheck_application_approvals(OLD.application_id);
    END IF;
    IF TG_OP = 'INSERT' OR (TG_OP = 'UPDATE'
                            AND NEW.application_id IS DISTINCT FROM OLD.application_id) THEN
      PERFORM core.recheck_application_approvals(NEW.application_id);
    END IF;
  ELSIF TG_TABLE_NAME = 'document_revisions' THEN
    PERFORM core.recheck_application_approvals(
      (SELECT d.application_id FROM core.application_documents d WHERE d.id = NEW.document_id));
  ELSIF TG_TABLE_NAME = 'job_postings' THEN
    FOR a IN SELECT x.id FROM core.applications x
              WHERE x.posting_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END LOOP
      PERFORM core.recheck_application_approvals(a);
    END LOOP;
  END IF;
  RETURN NULL;
END
$$;
"""
    )
    events = {
        "applications": "UPDATE OR DELETE",
        "application_documents": "INSERT OR UPDATE OR DELETE",
        "document_revisions": "INSERT",  # 추가만 되는 표
        "job_postings": "UPDATE OR DELETE",
    }
    for table, when in events.items():
        op.execute(
            f'CREATE TRIGGER approvals_recheck AFTER {when} ON "{S}"."{table}" '
            f'FOR EACH ROW EXECUTE FUNCTION "{S}".approvals_recheck_application()'
        )
    _drop_c0014_invalidation()
    # 지금 열린 제출 승인은 새 형식의 버전과 다르면 닫는다 (FORCE RLS: 공간마다 문맥을 켜고)
    op.execute(
        f"""
DO $$
DECLARE w uuid;
BEGIN
  FOR w IN SELECT * FROM core.maintenance_workspace_ids() LOOP
    PERFORM set_config('{rls.WORKSPACE_SETTING}', w::text, true);
    UPDATE core.approvals a
       SET status = 'invalidated', updated_at = now(),
           invalid_reason = CASE WHEN core.application_target_version(a.target_id) IS NULL
                                 THEN 'target_missing' ELSE 'target_changed' END
     WHERE a.target_kind = 'application' AND a.status IN ('pending', 'approved')
       AND a.target_version IS DISTINCT FROM core.application_target_version(a.target_id);
  END LOOP;
  PERFORM set_config('{rls.WORKSPACE_SETTING}', '', true);
END
$$;
"""
    )


def _drop_c0014_invalidation() -> None:
    op.execute(f'DROP TRIGGER approvals_on_posting_change ON "{S}".job_postings')
    op.execute(f'DROP TRIGGER approvals_on_document_change ON "{S}".application_documents')
    op.execute(f'DROP TRIGGER approvals_on_application_change ON "{S}".applications')
    op.execute(f'DROP FUNCTION "{S}".approvals_on_posting_change()')
    op.execute(f'DROP FUNCTION "{S}".approvals_on_document_change()')
    op.execute(f'DROP FUNCTION "{S}".approvals_on_application_change()')
    op.execute(f'DROP FUNCTION "{S}".invalidate_application_approvals(uuid, text)')


# ---------------------------------------------------------------- 2. OAuth 토큰 기록·폐기


def _oauth_tokens() -> None:
    # 복합 FK 대상 (공간이 다른 연결을 가리키지 못하게)
    op.create_unique_constraint(
        "uq_oauth_identities_workspace_id", "oauth_identities", ["workspace_id", "id"], schema=S
    )
    op.create_table(
        "oauth_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        # 토큰 식별: jti가 있으면 그 값, 없으면 토큰의 sha256 (토큰 원문은 저장하지 않는다)
        sa.Column("token_ref", sa.Text(), nullable=False),
        sa.Column("ref_kind", sa.Text(), nullable=False),  # jti | sha256
        sa.Column("via", sa.Text(), nullable=False),  # jwt | introspection
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by", sa.Uuid()),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint(
            "workspace_id", "issuer", "token_ref", name="uq_oauth_tokens_workspace_ref"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "identity_id"],
            [f"{S}.oauth_identities.workspace_id", f"{S}.oauth_identities.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("ref_kind in ('jti', 'sha256')", name="ck_oauth_tokens_ref_kind"),
        sa.CheckConstraint("via in ('jwt', 'introspection')", name="ck_oauth_tokens_via"),
        sa.CheckConstraint(
            "length(token_ref) between 1 and 500 and length(issuer) <= 500 "
            "and length(subject) <= 500",
            name="ck_oauth_tokens_length",
        ),
        sa.CheckConstraint("uses >= 1", name="ck_oauth_tokens_uses"),
        sa.CheckConstraint(
            "(revoked_at is null) = (revoked_by is null)", name="ck_oauth_tokens_revoked"
        ),
        schema=S,
    )
    op.create_index(
        "ix_oauth_tokens_workspace_seen", "oauth_tokens", ["workspace_id", "last_seen_at"], schema=S
    )
    for stmt in rls.workspace_isolation_sql(S, "oauth_tokens"):
        op.execute(stmt)
    # DELETE: 끝난 토큰 기록 정리 (주기 유지보수)
    op.execute(rls.grant_app_sql(S, "oauth_tokens", "SELECT, INSERT, UPDATE, DELETE"))


# ---------------------------------------------------------------- 3. 감사 기록 보존


def _audit_retention() -> None:
    op.execute(
        f"""
CREATE FUNCTION "{S}".purge_audit_events(p_keep_days integer) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  ws uuid := core.current_workspace_id();
  n integer;
BEGIN
  IF p_keep_days IS NULL OR p_keep_days < {MIN_AUDIT_KEEP_DAYS} THEN
    RAISE EXCEPTION 'audit retention must be at least {MIN_AUDIT_KEEP_DAYS} days'
      USING ERRCODE = '22023';
  END IF;
  IF ws IS NULL THEN
    RETURN 0;  -- 공간 문맥이 없으면 아무것도 지우지 않는다
  END IF;
  -- 소유자 권한이라도 FORCE RLS와 이 WHERE가 겹으로 그 공간·기준 시각 이전만 고른다
  DELETE FROM core.audit_events e
   WHERE e.workspace_id = ws
     AND e.created_at < now() - make_interval(days => p_keep_days);
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END
$$;
"""
    )
    op.execute(f'REVOKE ALL ON FUNCTION "{S}".purge_audit_events(integer) FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION "{S}".purge_audit_events(integer) TO {rls.APP_ROLE}')


# ---------------------------------------------------------------- 4. 상태 변경 알림


def _state_notify() -> None:
    op.execute(
        f"""
CREATE FUNCTION "{S}".notify_state_change() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  -- 표 이름만. 공간·대상 ID는 넣지 않는다 (LISTEN은 아무 역할이나 할 수 있다)
  PERFORM pg_notify('{STATE_CHANNEL}', TG_TABLE_NAME);
  RETURN NULL;
END
$$;
"""
    )
    op.execute(f'REVOKE ALL ON FUNCTION "{S}".notify_state_change() FROM PUBLIC')
    for table in ("runs", "approvals"):
        op.execute(
            f'CREATE TRIGGER notify_state AFTER INSERT OR UPDATE ON "{S}"."{table}" '
            f'FOR EACH STATEMENT EXECUTE FUNCTION "{S}".notify_state_change()'
        )


# ---------------------------------------------------------------- downgrade


def downgrade() -> None:
    for table in ("runs", "approvals"):
        op.execute(f'DROP TRIGGER notify_state ON "{S}"."{table}"')
    op.execute(f'DROP FUNCTION "{S}".notify_state_change()')
    op.execute(f'DROP FUNCTION "{S}".purge_audit_events(integer)')
    op.drop_table("oauth_tokens", schema=S)
    op.drop_constraint(
        "uq_oauth_identities_workspace_id", "oauth_identities", schema=S, type_="unique"
    )
    for table in ("applications", "application_documents", "document_revisions", "job_postings"):
        op.execute(f'DROP TRIGGER approvals_recheck ON "{S}"."{table}"')
    op.execute(f'DROP FUNCTION "{S}".approvals_recheck_application()')
    op.execute(f'DROP FUNCTION "{S}".recheck_application_approvals(uuid)')
    op.execute(f'DROP FUNCTION "{S}".application_target_version(uuid)')
    _restore_c0014_invalidation()


def _restore_c0014_invalidation() -> None:
    """c0014의 칸 비교 trigger를 그대로 되살린다. 열린 승인은 앱이 조회·사용 때 다시 확인한다."""
    op.execute(
        f"""
CREATE FUNCTION "{S}".invalidate_application_approvals(p_application uuid, p_reason text)
RETURNS void LANGUAGE sql AS $$
  UPDATE core.approvals
     SET status = 'invalidated', invalid_reason = p_reason, updated_at = now()
   WHERE target_kind = 'application' AND target_id = p_application
     AND status IN ('pending', 'approved')
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

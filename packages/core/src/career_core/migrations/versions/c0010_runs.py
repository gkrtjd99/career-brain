"""core: runs, run_events, approvals (O08)

Revision ID: c0010
Revises: c0009
"""

import sqlalchemy as sa
from alembic import op
from career_core.db import rls
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "c0010"
down_revision = "c0009"
branch_labels = None
depends_on = None

S = "core"

RUN_STATUSES = (
    "queued",
    "running",
    "waiting_approval",
    "interrupted",
    "cancelled",
    "failed",
    "done",
)
TERMINAL = ("cancelled", "failed", "done")
EVENT_KINDS = ("status", "step", "wait", "resumed", "error")
APPROVAL_STATUSES = ("pending", "approved", "rejected", "used", "invalidated", "expired")
ACTOR_KINDS = ("human", "agent")


def _ws() -> sa.Column:
    return sa.Column(
        "workspace_id",
        sa.Uuid(),
        sa.ForeignKey(f"{S}.workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )


def _ts(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _in(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    listed = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} in ({listed})", name=name)


def _fk_set_null(table: str, column: str, parent: str) -> None:
    """선택 연결. 부모가 지워지면 이 칸만 비운다 (workspace_id는 그대로, PostgreSQL 15+)."""
    op.execute(
        f'ALTER TABLE "{S}"."{table}" ADD CONSTRAINT "fk_{table}_{column}" '
        f'FOREIGN KEY (workspace_id, {column}) REFERENCES "{S}"."{parent}" (workspace_id, id) '
        f"ON DELETE SET NULL ({column})"
    )


def upgrade() -> None:
    terminal = ", ".join(f"'{v}'" for v in TERMINAL)
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("workflow", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        # 입력·재개 위치·결과에는 대상 ID와 짧은 값만 둔다 (원문 금지)
        sa.Column("input", JSONB(), nullable=False),
        sa.Column("position", JSONB(), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("steps_total", sa.Integer()),
        sa.Column("result", JSONB()),
        sa.Column("stop_requested", sa.Text()),
        # 외부 한도 대기 사유 (rate_limited 등). 대기 중에도 status는 queued
        sa.Column("wait_reason", sa.Text()),
        sa.Column("waits", sa.Integer(), nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("approval_id", sa.Uuid()),
        sa.Column("error_code", sa.Text()),
        sa.Column("cost_usd", sa.Numeric(18, 10), nullable=False),
        sa.Column("cost_unknown", sa.Boolean(), nullable=False),
        sa.Column("model_calls", sa.Integer(), nullable=False),
        sa.Column("resumes", sa.Integer(), nullable=False),
        # 시작한 actor의 종류와 그때의 범위. worker는 매 단계 현재 역할과의 교집합만 쓴다.
        sa.Column("actor_kind", sa.Text(), nullable=False),
        sa.Column("actor_scopes", ARRAY(sa.Text()), nullable=False),
        sa.Column("idempotency_key", sa.Text()),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts(),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "idempotency_key"),
        _in("status", RUN_STATUSES, "ck_runs_status"),
        _in("actor_kind", ACTOR_KINDS, "ck_runs_actor_kind"),
        sa.CheckConstraint(
            "stop_requested is null or stop_requested in ('cancel', 'interrupt')",
            name="ck_runs_stop_requested",
        ),
        sa.CheckConstraint(
            f"(status in ({terminal})) = (finished_at is not null)", name="ck_runs_finished"
        ),
        sa.CheckConstraint(
            "status <> 'failed' or error_code is not null", name="ck_runs_failed_reason"
        ),
        sa.CheckConstraint(
            "step >= 0 and waits >= 0 and resumes >= 0 and model_calls >= 0 and cost_usd >= 0",
            name="ck_runs_counters",
        ),
        schema=S,
    )
    op.create_index("ix_runs_workspace_created", "runs", ["workspace_id", "created_at"], schema=S)

    op.create_table(
        "run_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text()),
        sa.Column("step", sa.Integer()),
        sa.Column("code", sa.Text()),
        sa.Column("data", JSONB(), nullable=False),
        _ts(),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("run_id", "seq"),
        _in("kind", EVENT_KINDS, "ck_run_events_kind"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            [f"{S}.runs.workspace_id", f"{S}.runs.id"],
            ondelete="CASCADE",
        ),
        schema=S,
    )

    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _ws(),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_kind", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        # 요청 시점 대상 상태의 해시 (문서 revision·공고 버전 등). 쓸 때 다시 계산해 비교한다.
        sa.Column("target_version", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        # 승인할 내용 (ID·번호·짧은 메모). 승인 화면이 그대로 보여준다.
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("summary", JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("requested_via", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Uuid()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.Uuid()),
        sa.Column("decided_via", sa.Text()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_note", sa.Text()),
        sa.Column("used_by", sa.Uuid()),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("used_result_id", sa.Uuid()),
        sa.Column("invalid_reason", sa.Text()),
        sa.Column("idempotency_key", sa.Text()),
        _ts(),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "idempotency_key"),
        _in("status", APPROVAL_STATUSES, "ck_approvals_status"),
        _in("requested_via", ACTOR_KINDS, "ck_approvals_requested_via"),
        sa.CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="ck_approvals_payload_hash"),
        sa.CheckConstraint("expires_at > created_at", name="ck_approvals_expiry"),
        # 승인·사용된 승인은 사람이 결정한 것이어야 한다 (AI는 승인을 만들 수 없다)
        sa.CheckConstraint(
            "status not in ('approved', 'used') or "
            "(decided_by is not null and decided_at is not null and decided_via = 'human')",
            name="ck_approvals_human_decision",
        ),
        sa.CheckConstraint(
            "status <> 'rejected' or (decided_by is not null and decided_at is not null)",
            name="ck_approvals_rejected",
        ),
        sa.CheckConstraint("(status = 'used') = (used_at is not null)", name="ck_approvals_used"),
        schema=S,
    )
    op.create_index(
        "ix_approvals_target", "approvals", ["workspace_id", "target_kind", "target_id"], schema=S
    )
    _fk_set_null("approvals", "run_id", "runs")
    _fk_set_null("runs", "approval_id", "approvals")

    # 묶음(행위·대상·버전·내용)은 만든 뒤 고칠 수 없다. 바꾸려면 새 승인을 요청한다.
    op.execute(
        f"""
CREATE FUNCTION "{S}".approvals_binding_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.action IS DISTINCT FROM OLD.action
     OR NEW.target_kind IS DISTINCT FROM OLD.target_kind
     OR NEW.target_id IS DISTINCT FROM OLD.target_id
     OR NEW.target_version IS DISTINCT FROM OLD.target_version
     OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash
     OR NEW.payload IS DISTINCT FROM OLD.payload
     OR NEW.requested_by IS DISTINCT FROM OLD.requested_by
     OR NEW.requested_via IS DISTINCT FROM OLD.requested_via
     OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
     OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id THEN
    RAISE EXCEPTION 'approval binding is immutable' USING ERRCODE = 'check_violation';
  END IF;
  -- 끝난 승인(사용·거부·무효·만료)은 다시 열 수 없다
  IF OLD.status IN ('used', 'rejected', 'invalidated', 'expired')
     AND NEW.status IS DISTINCT FROM OLD.status THEN
    RAISE EXCEPTION 'approval is closed' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
"""
    )
    op.execute(
        f'CREATE TRIGGER approvals_binding_immutable BEFORE UPDATE ON "{S}".approvals '
        f'FOR EACH ROW EXECUTE FUNCTION "{S}".approvals_binding_immutable()'
    )

    for table in ("runs", "run_events", "approvals"):
        for stmt in rls.workspace_isolation_sql(S, table):
            op.execute(stmt)
    # 실행·승인은 지우지 않는다 (기록). 사건은 추가·조회만.
    op.execute(rls.grant_app_sql(S, "runs", "SELECT, INSERT, UPDATE"))
    op.execute(rls.grant_app_sql(S, "approvals", "SELECT, INSERT, UPDATE"))
    op.execute(rls.grant_app_sql(S, "run_events", "SELECT, INSERT"))


def downgrade() -> None:
    op.execute(f'ALTER TABLE "{S}".runs DROP CONSTRAINT "fk_runs_approval_id"')
    op.drop_table("approvals", schema=S)
    op.execute(f'DROP FUNCTION "{S}".approvals_binding_immutable()')
    op.drop_table("run_events", schema=S)
    op.drop_table("runs", schema=S)

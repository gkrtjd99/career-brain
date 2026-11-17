"""core: Markdown으로 내보내기 기록 — ``export_runs.kind``에 ``markdown`` (plan §21 백업·유지보수)

Revision ID: c0046
Revises: c0043

- 노트 앱(Obsidian 등)에서 여는 읽기 전용 Markdown 묶음(zip, ``usecases.markdown_export``)을 내려받을 때마다
  백업 묶음과 같은 표에 한 줄을 남긴다. ``archive_sha256``·``archive_bytes``는 zip 파일, ``rows_total``은
  노트(.md) 수, ``blobs_total``은 넣은 원본 파일 수, ``format_version``은 Markdown 묶음의 형식 버전.
- "마지막 백업"(``maintenance_info``의 ``last_export``)은 ``kind = 'export'``만 본다. Markdown 묶음은 복원할 수
  없으므로 백업으로 세지 않는다.
- 표 모양은 그대로다 (CHECK만 넓힌다). 내보내기 묶음에는 ``no_data_change("c0046")``.
"""

from alembic import op

revision = "c0046"
down_revision = "c0043"
branch_labels = None
depends_on = None

S = "core"


def upgrade() -> None:
    op.execute(f'ALTER TABLE "{S}".export_runs DROP CONSTRAINT ck_export_runs_kind')
    op.execute(
        f'ALTER TABLE "{S}".export_runs ADD CONSTRAINT ck_export_runs_kind '
        "CHECK (kind in ('export', 'restore', 'merge', 'markdown'))"
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM \"{S}\".export_runs WHERE kind = 'markdown'")
    op.execute(f'ALTER TABLE "{S}".export_runs DROP CONSTRAINT ck_export_runs_kind')
    op.execute(
        f'ALTER TABLE "{S}".export_runs ADD CONSTRAINT ck_export_runs_kind '
        "CHECK (kind in ('export', 'restore', 'merge'))"
    )

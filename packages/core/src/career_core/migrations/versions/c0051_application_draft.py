"""core: 지원서 AI 초안 — ``document_revisions.origin``에 ``ai_draft`` (plan §22)

Revision ID: c0051
Revises: c0050

- 지원서 초안 쓰기(``usecases.application_draft``, workflow ``draft_application``)가 문항마다 새 revision을
  남긴다. 그 revision의 ``origin``은 ``ai_draft``다 (수정안 적용 ``ai_suggestion``과 달리 ``suggestion_id`` 없음).
  문장 근거는 예전처럼 블록 ``spans``와 ``document_claims``에 들어간다.
- 표 모양은 그대로다 (CHECK만 넓힌다). 내보내기 묶음에는 ``no_data_change("c0051")``.
"""

from alembic import op

revision = "c0051"
down_revision = "c0050"
branch_labels = None
depends_on = None

S = "core"


def upgrade() -> None:
    op.execute(f'ALTER TABLE "{S}".document_revisions DROP CONSTRAINT ck_document_revisions_origin')
    op.execute(
        f'ALTER TABLE "{S}".document_revisions ADD CONSTRAINT ck_document_revisions_origin '
        "CHECK (origin in ('user', 'ai_suggestion', 'ai_draft'))"
    )


def downgrade() -> None:
    # 초안 revision은 지우지 않는다 (문서의 지금 revision일 수 있다). 사람이 쓴 것으로 되돌린다
    op.execute(f"UPDATE \"{S}\".document_revisions SET origin = 'user' WHERE origin = 'ai_draft'")
    op.execute(f'ALTER TABLE "{S}".document_revisions DROP CONSTRAINT ck_document_revisions_origin')
    op.execute(
        f'ALTER TABLE "{S}".document_revisions ADD CONSTRAINT ck_document_revisions_origin '
        "CHECK (origin in ('user', 'ai_suggestion'))"
    )

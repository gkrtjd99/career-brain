"""core: 삭제 기록 대상 표 넓히기, 합치기 기록 (O11 백로그)

- ``record_deletion`` trigger를 사용자가 직접 지우는 표로 넓힌다: 경험·주장·근거 연결·인터뷰 세션·질문,
  지원 문서·전형 단계, 학습 요건·요건↔주장 연결·시험·학습 기록·오답. ``ck_deletions_target_table``도 같이.
- 규칙 하나를 더한다: **직접 지운 행만** 남긴다. FK CASCADE로 같이 지워진 행(부모가 이미 없음)은 남기지
  않는다 — 부모의 삭제 기록을 다시 적용하면 같이 지워지므로 기록만 커진다. 표마다 CASCADE 부모
  (칸, 표)를 trigger 인자로 준다. RI CASCADE가 부른 삭제의 AFTER trigger는 바깥 문장 끝에 깊이 1로
  돌기 때문에 ``pg_trigger_depth()``로는 가릴 수 없어서, 그때 부모 행이 아직 있는지 본다.
  그 공간 문맥의 삭제만 남기는 c0014 규칙은 그대로다 (공간 통째 삭제·문맥 없는 관리 작업은 빠진다).
- 인터뷰 답(``interview_answers``)은 앱 계정이 지울 수 없다 (권한 없음, 질문·세션의 CASCADE로만 지워진다).
  그래서 표에 넣지 않는다: 부모 기록이 덮는다.
- ``export_runs.kind``에 ``merge``(기존 공간에 묶음 합치기)를 더한다.

Revision ID: c0018
Revises: c0015
"""

from alembic import op

revision = "c0018"
down_revision = "c0015"
branch_labels = None
depends_on = None

S = "core"

# c0014에서 trigger를 단 표
OLD_TABLES = ("sources", "job_postings", "applications", "study_items")
# 이번에 더하는 표 → ON DELETE CASCADE 부모 (칸, 부모 표). 사용자가 지울 수 있는 기록만이고
# 파생 자료(블록·파싱·벡터·가져오기 기록)는 뺀다. 새 CASCADE FK를 더하면 여기도 (test_export.py가 비교).
NEW_TABLES: dict[str, tuple[tuple[str, str], ...]] = {
    "experiences": (),
    "claims": (("experience_id", "core.experiences"),),
    "claim_evidence": (("claim_id", "core.claims"),),
    "interview_sessions": (("experience_id", "core.experiences"),),
    "interview_questions": (("session_id", "core.interview_sessions"),),
    "application_documents": (("application_id", "core.applications"),),
    "application_stages": (("application_id", "core.applications"),),
    "study_requirements": (("posting_id", "core.job_postings"),),
    "study_requirement_claims": (
        ("requirement_id", "core.study_requirements"),
        ("claim_id", "core.claims"),
    ),
    "study_assessments": (),
    "study_sessions": (("study_item_id", "core.study_items"),),
    "study_mistakes": (
        ("study_item_id", "core.study_items"),
        ("attempt_id", "core.study_attempts"),
    ),
}


def _target_check(tables: tuple[str, ...]) -> str:
    return "target_table in (" + ", ".join(f"'core.{t}'" for t in tables) + ")"


_FUNCTION = """
CREATE OR REPLACE FUNCTION "{s}".record_deletion() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  i int := 0;
  parent text;
  present boolean;
BEGIN
  -- 그 공간 문맥의 삭제(사용자·앱)만 기록한다. 공간 자체를 지우는 CASCADE와 문맥 없는 관리 작업은 뺀다.
  IF core.current_workspace_id() IS DISTINCT FROM OLD.workspace_id
     OR NOT EXISTS (SELECT 1 FROM core.workspaces w WHERE w.id = OLD.workspace_id) THEN
    RETURN OLD;
  END IF;
  -- 인자 = (칸, CASCADE 부모 표) 쌍. 부모가 이미 없으면 CASCADE로 같이 지워진 행이다: 부모 기록이 덮는다.
  WHILE i + 1 < TG_NARGS LOOP
    parent := to_jsonb(OLD) ->> TG_ARGV[i];
    IF parent IS NOT NULL THEN
      EXECUTE format('SELECT EXISTS (SELECT 1 FROM %s p WHERE p.id = $1::uuid)', TG_ARGV[i + 1])
        INTO present USING parent;
      IF NOT present THEN
        RETURN OLD;
      END IF;
    END IF;
    i := i + 2;
  END LOOP;
  INSERT INTO core.deletions (id, workspace_id, target_table, target_id, deleted_at, created_at)
  VALUES (gen_random_uuid(), OLD.workspace_id, TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME, OLD.id,
          now(), now())
  ON CONFLICT DO NOTHING;
  RETURN OLD;
END
$$;
"""
# c0014의 함수 (downgrade)
_OLD_FUNCTION = """
CREATE OR REPLACE FUNCTION "{s}".record_deletion() RETURNS trigger
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


def _args(parents: tuple[tuple[str, str], ...]) -> str:
    return ", ".join(f"'{x}'" for pair in parents for x in pair)


def upgrade() -> None:
    op.execute(_FUNCTION.format(s=S))
    op.execute(f'ALTER TABLE "{S}".deletions DROP CONSTRAINT ck_deletions_target_table')
    op.execute(
        f'ALTER TABLE "{S}".deletions ADD CONSTRAINT ck_deletions_target_table '
        f"CHECK ({_target_check(OLD_TABLES + tuple(NEW_TABLES))})"
    )
    for table, parents in NEW_TABLES.items():
        op.execute(
            f'CREATE TRIGGER record_deletion AFTER DELETE ON "{S}"."{table}" '
            f'FOR EACH ROW EXECUTE FUNCTION "{S}".record_deletion({_args(parents)})'
        )

    op.execute(f'ALTER TABLE "{S}".export_runs DROP CONSTRAINT ck_export_runs_kind')
    op.execute(
        f'ALTER TABLE "{S}".export_runs ADD CONSTRAINT ck_export_runs_kind '
        "CHECK (kind in ('export', 'restore', 'merge'))"
    )


def downgrade() -> None:
    op.execute(f"UPDATE \"{S}\".export_runs SET kind = 'restore' WHERE kind = 'merge'")
    op.execute(f'ALTER TABLE "{S}".export_runs DROP CONSTRAINT ck_export_runs_kind')
    op.execute(
        f'ALTER TABLE "{S}".export_runs ADD CONSTRAINT ck_export_runs_kind '
        "CHECK (kind in ('export', 'restore'))"
    )
    for table in NEW_TABLES:
        op.execute(f'DROP TRIGGER record_deletion ON "{S}"."{table}"')
    op.execute(f'DELETE FROM "{S}".deletions WHERE NOT ({_target_check(OLD_TABLES)})')
    op.execute(f'ALTER TABLE "{S}".deletions DROP CONSTRAINT ck_deletions_target_table')
    op.execute(
        f'ALTER TABLE "{S}".deletions ADD CONSTRAINT ck_deletions_target_table '
        f"CHECK ({_target_check(OLD_TABLES)})"
    )
    op.execute(_OLD_FUNCTION.format(s=S))

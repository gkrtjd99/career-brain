"""승인 대상 종류마다 즉시 무효화 장치가 다 있는지 (plan §18 승인(후속), ``docs/architecture/runs.md``).

새 승인 행위(``default_binders``)를 더하면서 대상 종류(``target_kind``)의 DB 쪽을 잊으면 이 시험이 실패한다.
종류 ``K``마다 필요한 것:

1. ``core.K_target_version(uuid) RETURNS text`` — SQL 표준 본문(``BEGIN ATOMIC``)이라 읽는 표가 ``pg_depend``에
   남는다 (plpgsql·문자열 본문이면 의존성이 없어 아래 3을 확인할 수 없다).
2. trigger 함수 ``core.approvals_recheck_K()``.
3. 1이 읽는 표마다 그 함수를 부르는 ``approvals_recheck`` trigger.
4. 앱의 ``ApprovalBinder.version``이 같은 DB 함수로 버전을 계산한다 (앱 확인과 trigger 판정이 하나).

반대쪽도 본다: DB 함수 본문이 ``target_kind = '…'``로 다루는 종류는 모두 코드에 binder가 있다.
"""

import inspect
import re

import pytest
from career_core.usecases.approvals import ApprovalBinder, default_binders
from sqlalchemy import text

pytestmark = pytest.mark.anyio

TRIGGER_NAME = "approvals_recheck"


def code_kinds(binders) -> dict[str, list[ApprovalBinder]]:
    out: dict[str, list[ApprovalBinder]] = {}
    for b in binders.values():
        out.setdefault(b.target_kind, []).append(b)
    return out


def db_kinds(conn) -> set[str]:
    """core 함수 본문(plpgsql·SQL 표준 본문)이 ``target_kind = '…'``로 다루는 종류."""
    bodies = conn.scalars(
        text(
            """
            SELECT coalesce(p.prosrc, '') || ' ' || coalesce(pg_get_functiondef(p.oid), '')
            FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'core' AND p.prokind = 'f'
            """
        )
    )
    found: set[str] = set()
    for body in bodies:
        found |= set(re.findall(r"target_kind\s*=\s*'([a-z_]+)'", body))
    return found


def approval_kind_problems(conn, binders) -> list[str]:
    problems: list[str] = []
    for kind, kind_binders in sorted(code_kinds(binders).items()):
        fn = conn.execute(
            text(
                """
                SELECT p.oid, p.prosqlbody IS NOT NULL AS atomic,
                       pg_get_function_result(p.oid) AS result
                FROM pg_proc p WHERE p.oid = to_regprocedure(:sig)
                """
            ),
            {"sig": f"core.{kind}_target_version(uuid)"},
        ).first()
        if fn is None:
            problems.append(f"{kind}: core.{kind}_target_version(uuid) 없음")
            continue
        if fn.result != "text":
            problems.append(f"{kind}: {kind}_target_version은 text를 돌려줘야 한다 ({fn.result})")
        if not fn.atomic:
            problems.append(f"{kind}: {kind}_target_version은 BEGIN ATOMIC 본문이어야 한다")
        recheck = conn.scalar(
            text(
                """
                SELECT p.oid FROM pg_proc p
                WHERE p.oid = to_regprocedure(:sig) AND p.prorettype = 'trigger'::regtype
                """
            ),
            {"sig": f"core.approvals_recheck_{kind}()"},
        )
        if recheck is None:
            problems.append(f"{kind}: trigger 함수 core.approvals_recheck_{kind}() 없음")
            continue
        read = set(
            conn.scalars(
                text(
                    """
                    SELECT DISTINCT d.refobjid::regclass::text FROM pg_depend d
                    JOIN pg_class c ON c.oid = d.refobjid AND c.relkind IN ('r', 'p')
                    WHERE d.classid = 'pg_proc'::regclass AND d.objid = :fn
                      AND d.refclassid = 'pg_class'::regclass
                    """
                ),
                {"fn": fn.oid},
            )
        )
        triggered = set(
            conn.scalars(
                text(
                    """
                    SELECT t.tgrelid::regclass::text FROM pg_trigger t
                    WHERE t.tgname = :tg AND NOT t.tgisinternal AND t.tgenabled <> 'D'
                      AND t.tgfoid = :fn
                    """
                ),
                {"tg": TRIGGER_NAME, "fn": recheck},
            )
        )
        if not read:
            problems.append(f"{kind}: {kind}_target_version이 읽는 표가 pg_depend에 없다")
        for table in sorted(read - triggered):
            problems.append(
                f"{kind}: {table}에 {TRIGGER_NAME} trigger(approvals_recheck_{kind}) 없음"
            )
        for b in kind_binders:
            if f"{kind}_target_version" not in inspect.getsource(b.version):
                problems.append(
                    f"{b.action}: binder.version이 {kind}_target_version을 부르지 않는다"
                )
    for kind in sorted(db_kinds(conn) - set(code_kinds(binders))):
        problems.append(f"{kind}: DB 함수가 다루는 대상 종류에 binder가 없다")
    return problems


async def test_every_approval_kind_has_version_function_and_recheck_triggers(db, owner_engine):
    binders = default_binders()
    assert code_kinds(binders)  # 적어도 제출 승인
    with owner_engine.connect() as conn:
        assert approval_kind_problems(conn, binders) == []
        assert set(code_kinds(binders)) == db_kinds(conn)


async def test_the_check_catches_a_forgotten_function_or_trigger(db, owner_engine):
    """시험이 실제로 잡는지: 함수 없는 종류, trigger 하나를 지운 상태 (되돌리는 트랜잭션 안)."""
    binders = dict(default_binders())
    [base] = binders.values()

    async def version(s, target_id):  # 앱 쪽만 있고 DB 함수는 잊은 새 행위
        return "", {}

    binders["synthetic.act"] = ApprovalBinder(
        "synthetic.act", "synthetic_kind", base.request_action, base.normalize, version
    )
    with owner_engine.connect() as conn:
        assert approval_kind_problems(conn, binders) == [
            "synthetic_kind: core.synthetic_kind_target_version(uuid) 없음"
        ]
        conn.rollback()  # 위 조회의 자동 트랜잭션을 닫고, 지우기는 되돌릴 트랜잭션 안에서
        tx = conn.begin()
        try:
            conn.execute(text(f"DROP TRIGGER {TRIGGER_NAME} ON core.job_postings"))
            assert approval_kind_problems(conn, default_binders()) == [
                "application: core.job_postings에 approvals_recheck trigger(approvals_recheck_application) 없음"
            ]
        finally:
            tx.rollback()
        assert approval_kind_problems(conn, default_binders()) == []

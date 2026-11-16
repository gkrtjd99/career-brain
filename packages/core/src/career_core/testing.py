"""테스트 도우미 (career-brain과 이를 사용하는 실행 앱의 테스트에서 재사용).

실제 PostgreSQL에서 임시 DB를 만들고, migration은 소유자 계정으로, 앱 접근은
``career_app`` 멤버 로그인 계정으로 수행한다.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import URL, Connection, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from career_core.db.migrate import MigrationChain, upgrade
from career_core.db.rls import APP_ROLE
from career_core.db.session import normalize_url

DEFAULT_ADMIN_URL = "postgresql+psycopg://career:career-dev-only@127.0.0.1:5432/postgres"
TEST_APP_LOGIN = "career_app_test"
TEST_APP_PASSWORD = "career-app-test-only"


@dataclass(frozen=True, slots=True)
class PgUrls:
    owner: URL
    app: URL


def admin_url() -> URL:
    return normalize_url(os.environ.get("CAREER_TEST_ADMIN_URL", DEFAULT_ADMIN_URL))


def postgres_available(url: URL) -> bool:
    engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 2})
    try:
        with engine.connect():
            return True
    except OperationalError:
        return False
    finally:
        engine.dispose()


@contextmanager
def migrated_database(chains: Sequence[MigrationChain] = ()) -> Iterator[PgUrls]:
    admin = admin_url()
    name = f"career_test_{secrets.token_hex(4)}"
    eng = create_engine(admin, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    owner = admin.set(database=name)
    try:
        upgrade(owner, chains)
        with eng.connect() as conn:
            conn.execute(
                text(
                    f"""DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{TEST_APP_LOGIN}') THEN
                      CREATE ROLE {TEST_APP_LOGIN} LOGIN PASSWORD '{TEST_APP_PASSWORD}' IN ROLE {APP_ROLE};
                    END IF; END $$;"""
                )
            )
        yield PgUrls(
            owner=owner, app=owner.set(username=TEST_APP_LOGIN, password=TEST_APP_PASSWORD)
        )
    finally:
        with eng.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        eng.dispose()


def workspace_isolation_problems(
    conn: Connection, schemas: Sequence[str], *, allow_unforced: set[tuple[str, str]] = frozenset()
) -> list[str]:
    """``workspace_id`` 컬럼이 있는 모든 테이블에 RLS(+FORCE)가 켜져 있는지 검사."""
    rows = conn.execute(
        text(
            """
            SELECT n.nspname AS schema, c.relname AS table,
                   c.relrowsecurity AS rls, c.relforcerowsecurity AS force
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'workspace_id' AND NOT a.attisdropped
            WHERE c.relkind = 'r' AND n.nspname = ANY(:schemas)
            """
        ),
        {"schemas": list(schemas)},
    ).all()
    problems = []
    for r in rows:
        if not r.rls:
            problems.append(f"{r.schema}.{r.table}: RLS disabled")
        elif not r.force and (r.schema, r.table) not in allow_unforced:
            problems.append(f"{r.schema}.{r.table}: RLS not forced")
    return problems


def make_pdf(pages: Sequence[str]) -> bytes:
    """합성 PDF. 빈 문자열인 쪽은 텍스트 없는 쪽(스캔본 흉내)이 된다. ASCII만."""

    def esc(t: str) -> str:
        return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    n = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, page_text in enumerate(pages):
        content = f"BT /F1 12 Tf 72 720 Td ({esc(page_text)}) Tj ET".encode() if page_text else b""
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode()
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)

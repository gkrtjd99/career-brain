"""소유 영역별 Alembic chain을 하나의 잠금 아래 정해진 순서로 실행한다.

- core chain은 항상 먼저. 그다음 실행 앱(개인 identity / 서비스 platform) chain.
- 각 chain은 자기 schema에 ``alembic_version`` 테이블을 가진다.
- API 시작 시 자동 실행하지 않는다. 배포 명령(예: ``personal-api migrate``)으로만 실행.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, MetaData, create_engine, text
from sqlalchemy.pool import NullPool

from career_core.db.session import normalize_url

MIGRATION_LOCK_KEY = 7_420_250_101  # pg_advisory_lock 키 (임의 고정값)


@dataclass(frozen=True, slots=True)
class MigrationChain:
    name: str
    script_location: Path
    schema: str


CORE_CHAIN = MigrationChain(
    name="core",
    script_location=Path(__file__).resolve().parent.parent / "migrations",
    schema="core",
)


def upgrade(url: str | URL, chains: Sequence[MigrationChain] = (), revision: str = "head") -> None:
    engine = create_engine(normalize_url(url), poolclass=NullPool)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
            conn.commit()
            try:
                for chain in (CORE_CHAIN, *chains):
                    cfg = Config()
                    cfg.set_main_option("script_location", str(chain.script_location))
                    cfg.attributes["connection"] = conn
                    cfg.attributes["schema"] = chain.schema
                    command.upgrade(cfg, revision)
                    conn.commit()
            finally:
                conn.rollback()
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
                conn.commit()
    finally:
        engine.dispose()


def run_env(target_metadata: MetaData | None) -> None:
    """각 chain의 env.py에서 호출한다. ``upgrade()``가 넘긴 연결만 사용한다."""
    from alembic import context

    conn = context.config.attributes.get("connection")
    schema = context.config.attributes.get("schema")
    if conn is None or schema is None:
        raise RuntimeError("run migrations through career_core.db.migrate.upgrade()")
    conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    context.configure(
        connection=conn,
        target_metadata=target_metadata,
        version_table_schema=schema,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()

import os

import pytest
from career_core.db import Database
from career_core.testing import admin_url, migrated_database, postgres_available
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool


@pytest.fixture(scope="session")
def pg():
    if not postgres_available(admin_url()):
        msg = "PostgreSQL not reachable (docker compose -f deploy/compose/compose.yaml up -d db)"
        if os.environ.get("CI"):
            pytest.fail(msg)
        pytest.skip(msg)
    with migrated_database() as urls:
        yield urls


@pytest.fixture(scope="session")
def owner_engine(pg):
    engine = create_engine(pg.owner, poolclass=NullPool)
    yield engine
    engine.dispose()


@pytest.fixture
async def db(pg, owner_engine):
    database = Database(pg.app, poolclass=NullPool)
    yield database
    await database.dispose()
    # 작업 큐는 공간을 가리지 않고 집는다 (claim_job). 시험들이 DB 하나를 같이 쓰므로, 한 시험이
    # 남긴 대기 작업(재시도 대기 중인 것 포함)이 다음 시험의 run_once에 집히지 않게 닫는다.
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE core.jobs SET status = 'failed', last_error = 'test_teardown', "
                "locked_until = NULL WHERE status IN ('queued', 'running')"
            )
        )

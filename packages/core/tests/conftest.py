import os

import pytest
from career_core.db import Database
from career_core.testing import admin_url, migrated_database, postgres_available
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


@pytest.fixture
async def db(pg):
    database = Database(pg.app, poolclass=NullPool)
    yield database
    await database.dispose()

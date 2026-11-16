from __future__ import annotations

import os
from dataclasses import dataclass

# 로컬 개발 기본값 (deploy/compose). 인터넷에 노출하는 배포에서는 반드시 환경변수로 바꾼다.
_DEV_APP_URL = "postgresql+psycopg://career_api:career-api-dev-only@127.0.0.1:5432/career_brain"
_DEV_MIGRATION_URL = "postgresql+psycopg://career:career-dev-only@127.0.0.1:5432/career_brain"


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = _DEV_APP_URL
    migration_database_url: str = _DEV_MIGRATION_URL

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.environ.get("CAREER_DATABASE_URL", _DEV_APP_URL),
            migration_database_url=os.environ.get(
                "CAREER_MIGRATION_DATABASE_URL", _DEV_MIGRATION_URL
            ),
        )

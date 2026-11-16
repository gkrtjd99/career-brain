from __future__ import annotations

import os
from dataclasses import dataclass

from personal_api.envfile import load_env_file

# 로컬 개발 기본값 (deploy/compose). 인터넷에 노출하는 배포에서는 반드시 환경변수로 바꾼다.
_DEV_APP_URL = "postgresql+psycopg://career_api:career-api-dev-only@127.0.0.1:5432/career_brain"
_DEV_MIGRATION_URL = "postgresql+psycopg://career:career-dev-only@127.0.0.1:5432/career_brain"


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = _DEV_APP_URL
    migration_database_url: str = _DEV_MIGRATION_URL
    blob_dir: str = "data/blobs"
    max_upload_bytes: int = 20 * 1024 * 1024
    # 모델 엔드포인트·라우트·예산. 없으면 모델 호출은 전부 거부된다. 예시: models.example.toml
    models_config: str = "models.toml"
    # 동적 공고 페이지를 Playwright로 렌더링 (선택 설치: extra `browser` + `playwright install chromium`)
    browser_fetch: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        # 로컬 `.env`(git 제외)로 키·토큰을 둘 수 있다. 셸 환경변수가 우선한다.
        load_env_file(os.environ.get("CAREER_ENV_FILE", ".env"))
        return cls(
            database_url=os.environ.get("CAREER_DATABASE_URL", _DEV_APP_URL),
            migration_database_url=os.environ.get(
                "CAREER_MIGRATION_DATABASE_URL", _DEV_MIGRATION_URL
            ),
            blob_dir=os.environ.get("CAREER_BLOB_DIR", "data/blobs"),
            max_upload_bytes=int(os.environ.get("CAREER_MAX_UPLOAD_MB", "20")) * 1024 * 1024,
            models_config=os.environ.get("CAREER_MODELS_CONFIG", "models.toml"),
            browser_fetch=os.environ.get("CAREER_BROWSER_FETCH", "0").lower() in ("1", "true"),
        )

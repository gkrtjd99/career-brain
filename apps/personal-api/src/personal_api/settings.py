from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

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
    # 웹 복원(백업 묶음 올리기) 상한. 이 경로만 위 상한 대신 이 값을 쓴다.
    max_restore_bytes: int = 2048 * 1024 * 1024
    # 모델 엔드포인트·라우트·예산. 없으면 모델 호출은 전부 거부된다. 예시: models.example.toml
    models_config: str = "models.toml"
    # 동적 공고 페이지를 Playwright로 렌더링 (선택 설치: extra `browser` + `playwright install chromium`)
    browser_fetch: bool = False
    # 스캔 PDF 비전 호출(종량제)의 예상 최대 비용이 이 금액(USD)을 넘으면 웹에서 확인을 받는다 (c0025).
    # None = 묻지 않음 (CAREER_VISION_CONFIRM_USD=off)
    vision_confirm_usd: Decimal | None = Decimal("0.10")
    # 원격 MCP OAuth (선택). issuer를 정하면 켜진다: docs/architecture/mcp.md "원격 연결 (OAuth)"
    public_url: str | None = (
        None  # 예: https://career.example.com (MCP 자원 = {public_url}/api/core/mcp)
    )
    oauth_issuer: str | None = None
    oauth_jwks_url: str | None = None
    oauth_audience: str | None = None
    # 불투명 토큰 introspection (RFC 7662, 선택). 비밀은 환경변수로만.
    oauth_introspection_url: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    # jti 없는 외부 토큰 거부 (토큰 하나씩 폐기할 수 있게)
    oauth_require_jti: bool = False
    # 서명이 맞는 JWT도 introspection으로 확인 (인가 서버 폐기를 캐시 시간 안에 반영, c0025)
    oauth_introspect_jwt: bool = False
    # introspection 활성 결과 캐시(초). 인가 서버 폐기 반영 지연의 상한 (프로세스 수와 무관)
    oauth_introspection_cache_seconds: int = 10

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
            max_restore_bytes=int(os.environ.get("CAREER_MAX_RESTORE_MB", "2048")) * 1024 * 1024,
            models_config=os.environ.get("CAREER_MODELS_CONFIG", "models.toml"),
            browser_fetch=os.environ.get("CAREER_BROWSER_FETCH", "0").lower() in ("1", "true"),
            vision_confirm_usd=_usd_or_off(os.environ.get("CAREER_VISION_CONFIRM_USD", "0.10")),
            public_url=os.environ.get("CAREER_PUBLIC_URL") or None,
            oauth_issuer=os.environ.get("CAREER_OAUTH_ISSUER") or None,
            oauth_jwks_url=os.environ.get("CAREER_OAUTH_JWKS_URL") or None,
            oauth_audience=os.environ.get("CAREER_OAUTH_AUDIENCE") or None,
            oauth_introspection_url=os.environ.get("CAREER_OAUTH_INTROSPECTION_URL") or None,
            oauth_client_id=os.environ.get("CAREER_OAUTH_CLIENT_ID") or None,
            oauth_client_secret=os.environ.get("CAREER_OAUTH_CLIENT_SECRET") or None,
            oauth_require_jti=os.environ.get("CAREER_OAUTH_REQUIRE_JTI", "0").lower()
            in ("1", "true", "yes", "on"),
            oauth_introspect_jwt=os.environ.get("CAREER_OAUTH_INTROSPECT_JWT", "0").lower()
            in ("1", "true", "yes", "on"),
            oauth_introspection_cache_seconds=int(
                os.environ.get("CAREER_OAUTH_INTROSPECTION_CACHE_SECONDS", "10")
            ),
        )


def _usd_or_off(raw: str) -> Decimal | None:
    """``off``·``none``·빈 값이면 None (확인 안 함). 그 밖에는 0 이상의 USD 금액."""
    value = raw.strip().lower()
    if value in ("", "off", "none", "no", "false"):
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"CAREER_VISION_CONFIRM_USD must be a USD amount or 'off': {raw}") from exc
    if amount < 0 or not amount.is_finite():
        raise ValueError(f"CAREER_VISION_CONFIRM_USD must be >= 0: {raw}")
    return amount

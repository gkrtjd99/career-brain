"""원격 MCP의 OAuth 2.1 자원 서버 부분 (MCP Authorization 규격, RFC 9728·RFC 8707·RFC 9068).

이 서버는 **인가 서버를 포함하지 않는다.** 직접 운영하는 외부 인가 서버(Keycloak·Authentik·Auth0 등)가
발급한 JWT 액세스 토큰을 검증만 한다. 하는 일:

- 보호 자원 메타데이터 ``/.well-known/oauth-protected-resource[/<MCP 경로>]`` (RFC 9728):
  자원 식별자(MCP 주소), 인가 서버(issuer), 지원 범위. 401 응답의 ``WWW-Authenticate``가 이 주소를 알려 준다.
- 토큰 검증: 서명(JWKS, 허용 알고리즘만), ``iss``, ``aud``(= 이 MCP 자원, RFC 8707), ``exp``·``nbf``, ``sub``.
- 공간: 토큰의 어떤 값도 공간 ID로 믿지 않는다. (issuer, sub)를 ``core.oauth_identities``에서 찾아
  그 연결이 가리키는 (공간, actor)를 쓴다. 연결은 공간 소유자가 만든다 (``personal-api oauth-link``).
- 범위: 토큰 ``scope``를 역할 범위로 옮기고 **현재 역할 범위와 교집합**만 쓴다. ``approve``·``workspace-admin``은
  토큰으로 줄 수 없다. actor는 항상 ``agent``다 (MCP).
- 토큰을 다른 곳으로 전달하지 않는다 (token passthrough 금지).

하지 않는 것: 인가 서버, 동적 클라이언트 등록, 토큰 introspection(불투명 토큰), 토큰 교환. API 토큰
(``cbp_…``)이 기본이고 이 기능은 설정했을 때만 켜진다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select, text

from career_core.db import Database, Membership
from career_core.domain.workspace import scopes_for_role
from career_core.ports import ACTOR_AGENT, ActorContext

log = logging.getLogger(__name__)

METADATA_PATH = "/.well-known/oauth-protected-resource"

# 토큰 범위 → 코어 범위. approve·workspace-admin은 일부러 없다 (승인·관리는 웹에서 사람이).
DEFAULT_SCOPE_MAP: Mapping[str, frozenset[str]] = {
    "career:read": frozenset({"read"}),
    "career:write": frozenset({"read", "draft-write"}),
    "career:ai": frozenset({"egress"}),
}
NEVER_GRANTED = frozenset({"approve", "workspace-admin"})
DEFAULT_ALGORITHMS = ("RS256", "PS256", "ES256", "EdDSA")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class OAuthConfigError(ValueError):
    pass


def _check_url(name: str, url: str) -> None:
    parts = urlsplit(url)
    local = (parts.hostname or "") in _LOCAL_HOSTS
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise OAuthConfigError(f"{name} must be https (http only for localhost): {url}")
    if parts.fragment or not parts.netloc:
        raise OAuthConfigError(f"{name} is not a valid URL: {url}")


@dataclass(frozen=True, slots=True)
class OAuthConfig:
    """``resource``: MCP 자원 식별자 = 클라이언트가 접속하는 주소 (예: https://career.example.com/api/core/mcp)."""

    resource: str
    issuer: str
    jwks_url: str
    # 없으면 resource. 인가 서버가 다른 값을 aud로 넣는다면 그 값 (그래도 이 자원 전용이어야 한다)
    audience: str | None = None
    algorithms: tuple[str, ...] = DEFAULT_ALGORITHMS
    scope_map: Mapping[str, frozenset[str]] = field(default_factory=lambda: dict(DEFAULT_SCOPE_MAP))
    leeway_seconds: int = 60
    resource_name: str = "career-brain"

    def __post_init__(self) -> None:
        _check_url("resource", self.resource)
        _check_url("issuer", self.issuer)
        _check_url("jwks_url", self.jwks_url)
        if "none" in {a.lower() for a in self.algorithms} or not self.algorithms:
            raise OAuthConfigError("algorithms must not be empty or include 'none'")
        for scope, granted in self.scope_map.items():
            if granted & NEVER_GRANTED:
                raise OAuthConfigError(
                    f"scope {scope} must not grant {sorted(granted & NEVER_GRANTED)}"
                )

    @property
    def expected_audience(self) -> str:
        return self.audience or self.resource

    @property
    def metadata_url(self) -> str:
        """RFC 9728 §3.1: 자원 주소의 경로를 well-known 뒤에 붙인다."""
        parts = urlsplit(self.resource)
        path = parts.path.rstrip("/")
        return f"{parts.scheme}://{parts.netloc}{METADATA_PATH}{path}"

    @property
    def metadata_paths(self) -> tuple[str, ...]:
        path = urlsplit(self.resource).path.rstrip("/")
        return (METADATA_PATH + path, METADATA_PATH) if path else (METADATA_PATH,)

    def metadata(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "authorization_servers": [self.issuer],
            "scopes_supported": sorted(self.scope_map),
            "bearer_methods_supported": ["header"],
            "resource_name": self.resource_name,
            "resource_signing_alg_values_supported": list(self.algorithms),
        }

    def www_authenticate(self, *, invalid_token: bool = False) -> str:
        parts = [f'resource_metadata="{self.metadata_url}"']
        if invalid_token:
            parts.insert(0, 'error="invalid_token"')
        parts.append(f'scope="{" ".join(sorted(self.scope_map))}"')
        return "Bearer " + ", ".join(parts)


def granted_scopes(
    claims: Mapping[str, Any], scope_map: Mapping[str, frozenset[str]]
) -> frozenset[str]:
    """토큰의 ``scope``(공백 구분) 또는 ``scp``(목록)를 코어 범위로. 모르는 범위는 버린다."""
    raw = claims.get("scope")
    names: list[str] = []
    if isinstance(raw, str):
        names = raw.split()
    elif isinstance(claims.get("scp"), list):
        names = [s for s in claims["scp"] if isinstance(s, str)]
    out: set[str] = set()
    for name in names:
        out |= scope_map.get(name, frozenset())
    return frozenset(out - NEVER_GRANTED)


class JwtVerifier:
    """JWKS로 서명을 확인하고 표준 클레임을 검사한다 (PyJWT, 선택 설치 ``career-core[oauth]``)."""

    def __init__(self, config: OAuthConfig, *, jwks_client: Any = None) -> None:
        try:
            import jwt  # noqa: F401
        except ImportError as exc:  # pragma: no cover - 설치 안내
            raise OAuthConfigError(
                "install career-core[oauth] (PyJWT) to verify OAuth tokens"
            ) from exc
        self.config = config
        if jwks_client is None:
            from jwt import PyJWKClient

            jwks_client = PyJWKClient(config.jwks_url, cache_keys=True, lifespan=300, timeout=10)
        self._jwks = jwks_client

    async def verify(self, token: str) -> dict[str, Any] | None:
        import jwt

        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in self.config.algorithms:
                return None
            # JWKS 조회는 동기 HTTP라 스레드에서 (키는 캐시된다)
            key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)
            return jwt.decode(
                token,
                key.key,
                algorithms=list(self.config.algorithms),
                audience=self.config.expected_audience,
                issuer=self.config.issuer,
                leeway=self.config.leeway_seconds,
                options={"require": ["exp", "iss", "sub", "aud"]},
            )
        except jwt.PyJWTError as exc:
            log.info("oauth token rejected: %s", type(exc).__name__)
            return None


class OAuthIdentityResolver:
    """외부 JWT → ``ActorContext``. 공간은 (issuer, sub) 연결이 정한다. 실패하면 None."""

    def __init__(self, db: Database, config: OAuthConfig, verifier: JwtVerifier | None = None):
        self.db = db
        self.config = config
        self.verifier = verifier or JwtVerifier(config)

    async def resolve(self, credential: str) -> ActorContext | None:
        if credential.count(".") != 2:
            return None  # JWT가 아님 (API 토큰 등)
        claims = await self.verifier.verify(credential)
        if claims is None:
            return None
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            return None
        granted = granted_scopes(claims, self.config.scope_map)
        if not granted:
            return None
        async with self.db.system_session() as s:
            row = (
                await s.execute(
                    text("SELECT workspace_id, actor_id FROM core.lookup_oauth_identity(:i, :s)"),
                    {"i": self.config.issuer, "s": subject},
                )
            ).first()
        if row is None:
            return None
        async with self.db.workspace_session(row.workspace_id) as s:
            role = await s.scalar(
                select(Membership.role).where(Membership.actor_id == row.actor_id)
            )
        if role is None:
            return None
        scopes = scopes_for_role(role) & granted
        if not scopes:
            return None
        return ActorContext(row.actor_id, row.workspace_id, scopes, kind=ACTOR_AGENT)


@dataclass(frozen=True, slots=True)
class McpOAuth:
    """MCP 경로에만 붙는 OAuth 설정. ``CoreDeps.mcp_oauth``로 넘긴다."""

    config: OAuthConfig
    resolver: Any  # IdentityResolver


def add_metadata_routes(app_or_router: Any, config: OAuthConfig) -> None:
    """보호 자원 메타데이터 경로를 **앱 루트**에 등록한다 (``/api`` 같은 접두어 밖)."""
    from fastapi.responses import JSONResponse

    async def metadata() -> JSONResponse:
        return JSONResponse(config.metadata(), headers={"Cache-Control": "public, max-age=3600"})

    for path in config.metadata_paths:
        app_or_router.add_api_route(path, metadata, methods=["GET"], include_in_schema=False)


__all__ = [
    "DEFAULT_SCOPE_MAP",
    "METADATA_PATH",
    "JwtVerifier",
    "McpOAuth",
    "OAuthConfig",
    "OAuthConfigError",
    "OAuthIdentityResolver",
    "add_metadata_routes",
    "granted_scopes",
]

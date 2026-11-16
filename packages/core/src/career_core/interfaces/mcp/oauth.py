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
- 토큰을 다른 곳으로 전달하지 않는다 (token passthrough 금지). 예외는 설정한 인가 서버의 introspection
  주소 하나다 (그 토큰을 발급한 곳이 유효한지 답한다).

c0019 보강:

- 불투명 토큰 introspection (RFC 7662, 선택): ``introspection_url``을 설정하면 JWT가 아닌 토큰(또는 JWKS를
  설정하지 않았으면 모든 토큰)을 인가 서버에 물어본다. ``active``·``iss``·``aud``·``exp``·``sub``를 JWT와
  같은 규칙으로 본다. 결과는 짧게(기본 10초, 만료 전까지만) 캐시한다. 그래서 인가 서버에서 폐기한 토큰은
  캐시 시간 안에 막힌다. 이 설치의 API 토큰 접두어(``local_token_prefixes``)는 보내지 않는다.
- 폐기 목록·토큰 기록: 받은 토큰마다 ``core.oauth_tokens``에 ``jti``(없으면 sha256)를 남긴다. 소유자가 폐기한
  토큰과 끊은 연결의 토큰은 거부한다. 같은 ``jti``가 다른 연결로 오면 거부한다.
- ``require_jti``: 켜면 ``jti`` 없는 토큰을 거부한다 (토큰 하나만 골라 폐기할 수 있게).

c0025 보강:

- ``introspect_jwt``: 켜면 서명이 맞는 JWT도 introspection으로 한 번 더 묻는다 (``active``·``sub`` 일치).
  인가 서버에서 폐기한 JWT가 만료까지 통하지 않고 캐시 시간 안에 막힌다. 같은 캐시를 지난다.
- 캐시는 프로세스 메모리다. 공유 캐시(DB 표)를 두지 않고 캐시 시간을 짧게(기본 10초) 한다: 공유 캐시는
  인가 서버 호출 수만 줄이고 폐기 반영 지연(최대 = 캐시 시간)은 줄이지 못한다. 프로세스가 몇 개든 지연의
  상한은 캐시 시간 하나다 (``docs/architecture/mcp.md`` "introspection 캐시").

하지 않는 것: 인가 서버, 동적 클라이언트 등록, 토큰 교환, 한 번만 쓰는 토큰(액세스 토큰은 만료까지 요청마다
다시 보내므로 재사용 자체는 정상이다. 탈취한 토큰의 재전송을 막으려면 DPoP(RFC 9449) 같은 소유 증명이
필요하다, ``docs/architecture/mcp.md``). API 토큰(``cbp_…``)이 기본이고 이 기능은 설정했을 때만 켜진다.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select, text

from career_core.db import Database, Membership
from career_core.db.models import OAuthIdentity
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
    # JWT 서명 확인용. introspection만 쓰면 비워도 된다 (둘 중 하나는 있어야 한다).
    jwks_url: str | None = None
    # 없으면 resource. 인가 서버가 다른 값을 aud로 넣는다면 그 값 (그래도 이 자원 전용이어야 한다)
    audience: str | None = None
    algorithms: tuple[str, ...] = DEFAULT_ALGORITHMS
    scope_map: Mapping[str, frozenset[str]] = field(default_factory=lambda: dict(DEFAULT_SCOPE_MAP))
    leeway_seconds: int = 60
    resource_name: str = "career-brain"
    # RFC 7662 introspection (선택). 클라이언트 인증은 client_secret_basic.
    introspection_url: str | None = None
    introspection_client_id: str | None = None
    introspection_client_secret: str | None = field(default=None, repr=False)
    # 활성 결과 캐시 시간(초, 0 = 캐시 없음). 인가 서버의 폐기는 최대 이 시간 뒤에 반영된다 (프로세스마다 따로).
    introspection_cache_seconds: int = 10
    # True면 서명이 맞는 JWT도 introspection으로 확인한다 (인가 서버의 폐기를 캐시 시간 안에 반영)
    introspect_jwt: bool = False
    # True면 jti 없는 토큰을 거부한다 (토큰 하나씩 폐기할 수 있게)
    require_jti: bool = False
    # 이 설치의 다른 토큰 접두어(예: API 토큰 "cbp_"). 이런 토큰은 인가 서버로 보내지 않는다.
    local_token_prefixes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _check_url("resource", self.resource)
        _check_url("issuer", self.issuer)
        if not self.jwks_url and not self.introspection_url:
            raise OAuthConfigError("set jwks_url or introspection_url")
        if self.jwks_url:
            _check_url("jwks_url", self.jwks_url)
        if self.introspection_url:
            _check_url("introspection_url", self.introspection_url)
            if not self.introspection_client_id or not self.introspection_client_secret:
                raise OAuthConfigError("introspection needs a client id and secret")
        if not 0 <= self.introspection_cache_seconds <= 600:
            raise OAuthConfigError("introspection_cache_seconds must be between 0 and 600")
        if self.introspect_jwt and not (self.introspection_url and self.jwks_url):
            raise OAuthConfigError("introspect_jwt needs both jwks_url and introspection_url")
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
            if not config.jwks_url:
                raise OAuthConfigError("jwks_url is not set")
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


def _audiences(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


class IntrospectionVerifier:
    """RFC 7662 토큰 introspection. 활성 토큰의 클레임(``iss``·``aud``·``exp``·``sub``·``scope`` 등)을 돌려준다.

    JWT 검증과 같은 규칙: ``active = true``, ``iss``가 있으면 설정한 issuer, ``aud``에 이 자원(필수),
    ``exp``·``nbf``(여유 포함), ``sub`` 필수. 결과는 토큰 sha256을 키로 짧게 캐시한다 (거부는 캐시하지 않음).
    캐시 키는 해시뿐이고 원문 토큰은 메모리 캐시에도 두지 않는다.
    """

    MAX_CACHE = 1024

    def __init__(
        self,
        config: OAuthConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Any = time.time,
    ) -> None:
        if not config.introspection_url:
            raise OAuthConfigError("introspection_url is not set")
        self.config = config
        self._transport = transport
        self._clock = clock
        self._cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self.calls = 0

    def _check(self, data: Any) -> dict[str, Any] | None:
        cfg = self.config
        if not isinstance(data, dict) or data.get("active") is not True:
            return None
        if data.get("iss") is not None and data.get("iss") != cfg.issuer:
            return None
        if cfg.expected_audience not in _audiences(data.get("aud")):
            return None
        now = self._clock()
        exp, nbf = _number(data.get("exp")), _number(data.get("nbf"))
        if exp is not None and exp + cfg.leeway_seconds <= now:
            return None
        if nbf is not None and nbf - cfg.leeway_seconds > now:
            return None
        if not isinstance(data.get("sub"), str) or not data["sub"]:
            return None
        return data

    async def verify(self, token: str) -> dict[str, Any] | None:
        cfg = self.config
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = self._clock()
        hit = self._cache.get(key)
        if hit is not None:
            if hit[0] > now:
                return hit[1]
            self._cache.pop(key, None)
        self.calls += 1
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=10, follow_redirects=False
            ) as client:
                r = await client.post(
                    cfg.introspection_url or "",
                    data={"token": token, "token_type_hint": "access_token"},
                    auth=(cfg.introspection_client_id or "", cfg.introspection_client_secret or ""),
                    headers={"Accept": "application/json"},
                )
            data = r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as exc:
            log.info("oauth introspection failed: %s", type(exc).__name__)
            return None
        claims = self._check(data)
        if claims is None:
            return None
        ttl = float(cfg.introspection_cache_seconds)
        exp = _number(claims.get("exp"))
        if exp is not None:
            ttl = min(ttl, exp - now)
        if ttl > 0:
            self._cache[key] = (now + ttl, claims)
            while len(self._cache) > self.MAX_CACHE:
                self._cache.popitem(last=False)
        return claims


def _is_jwt(credential: str) -> bool:
    return credential.count(".") == 2


class OAuthIdentityResolver:
    """외부 액세스 토큰 → ``ActorContext``. 공간은 (issuer, sub) 연결이 정한다. 실패하면 None.

    JWT 모양이면 JWKS로(설정했을 때), 그 밖에는 introspection으로(설정했을 때) 확인한다. 그 뒤 연결을
    찾고, 공간 문맥에서 토큰 기록·폐기 목록(c0019)을 확인한다.
    """

    def __init__(
        self,
        db: Database,
        config: OAuthConfig,
        verifier: JwtVerifier | None = None,
        introspector: IntrospectionVerifier | None = None,
    ):
        self.db = db
        self.config = config
        self.verifier = verifier or (JwtVerifier(config) if config.jwks_url else None)
        self.introspector = introspector or (
            IntrospectionVerifier(config) if config.introspection_url else None
        )
        # 마지막 거부 사유 (시험·로그용, 토큰 내용은 없음)
        self.last_rejection: str | None = None

    async def _claims(self, credential: str) -> tuple[dict[str, Any] | None, str]:
        if any(credential.startswith(p) for p in self.config.local_token_prefixes):
            return None, "local_token"
        if _is_jwt(credential) and self.verifier is not None:
            claims = await self.verifier.verify(credential)
            if claims is not None and self.config.introspect_jwt and self.introspector is not None:
                # 서명이 맞아도 인가 서버가 폐기했을 수 있다: 같은 캐시를 지나 active를 묻는다
                active = await self.introspector.verify(credential)
                if active is None:
                    return None, "jwt_inactive"
                if active.get("sub") != claims.get("sub"):
                    return None, "jwt_subject_mismatch"
            return claims, "jwt"
        if self.introspector is not None:
            return await self.introspector.verify(credential), "introspection"
        return None, "unsupported"

    def _reject(self, reason: str) -> None:
        self.last_rejection = reason
        log.info("oauth token rejected: %s", reason)

    async def resolve(self, credential: str) -> ActorContext | None:
        from career_core.usecases.oauth_identities import record_token_use, token_ref

        self.last_rejection = None
        claims, via = await self._claims(credential)
        if claims is None:
            keep = ("local_token", "unsupported", "jwt_inactive", "jwt_subject_mismatch")
            self.last_rejection = via if via in keep else "invalid"
            return None
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            self._reject("no_subject")
            return None
        if self.config.require_jti and not isinstance(claims.get("jti"), str):
            self._reject("jti_required")
            return None
        granted = granted_scopes(claims, self.config.scope_map)
        if not granted:
            self._reject("no_scope")
            return None
        async with self.db.system_session() as s:
            row = (
                await s.execute(
                    text("SELECT workspace_id, actor_id FROM core.lookup_oauth_identity(:i, :s)"),
                    {"i": self.config.issuer, "s": subject},
                )
            ).first()
        if row is None:
            self._reject("not_linked")
            return None
        ref, ref_kind = token_ref(credential, claims.get("jti"))
        exp = _number(claims.get("exp"))
        async with self.db.workspace_session(row.workspace_id) as s:
            role = await s.scalar(
                select(Membership.role).where(Membership.actor_id == row.actor_id)
            )
            identity_id = await s.scalar(
                select(OAuthIdentity.id).where(
                    OAuthIdentity.issuer == self.config.issuer,
                    OAuthIdentity.subject == subject,
                    OAuthIdentity.revoked_at.is_(None),
                )
            )
            if role is None or identity_id is None:
                self._reject("not_linked")
                return None
            verdict = await record_token_use(
                s,
                workspace_id=row.workspace_id,
                identity_id=identity_id,
                issuer=self.config.issuer,
                subject=subject,
                ref=ref,
                ref_kind=ref_kind,
                via=via,
                expires_at=datetime.fromtimestamp(exp, UTC) if exp is not None else None,
            )
        if verdict != "ok":
            self._reject(verdict)
            return None
        scopes = scopes_for_role(role) & granted
        if not scopes:
            self._reject("no_scope")
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
    "IntrospectionVerifier",
    "JwtVerifier",
    "McpOAuth",
    "OAuthConfig",
    "OAuthConfigError",
    "OAuthIdentityResolver",
    "add_metadata_routes",
    "granted_scopes",
]

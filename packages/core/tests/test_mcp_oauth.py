"""원격 MCP OAuth (자원 서버 부분): 보호 자원 메타데이터, 외부 JWT 검증, 토큰 → 공간 연결.

키는 시험마다 새로 만든다. 모든 자료는 합성이다.
"""

import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest
from career_core.db.models import AuditEvent
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.interfaces.mcp.oauth import (
    JwtVerifier,
    McpOAuth,
    OAuthConfig,
    OAuthConfigError,
    OAuthIdentityResolver,
    add_metadata_routes,
    granted_scopes,
)
from career_core.ports import ACTOR_AGENT, ActorContext
from career_core.usecases import brain
from career_core.usecases.oauth_identities import (
    OAuthLinkError,
    link_oauth_identity,
    revoke_oauth_identity,
)
from career_core.usecases.workspaces import create_workspace
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from sqlalchemy import select

pytestmark = pytest.mark.anyio

RESOURCE = "https://career.example.test/api/core/mcp"
ISSUER = "https://auth.example.test/realms/career"


class NoTokens:
    async def resolve(self, credential):
        return None


class FakeJwks:
    """PyJWKClient 대신: 네트워크 없이 공개 키 하나를 돌려준다."""

    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=self.public_key)


def config(**kw):
    return OAuthConfig(
        resource=RESOURCE, issuer=ISSUER, jwks_url=ISSUER + "/protocol/openid-connect/certs", **kw
    )


def make_token(env, key=None, **claims):
    now = int(time.time())
    body = {
        "iss": ISSUER,
        "aud": RESOURCE,
        "sub": env.sub,
        "iat": now,
        "exp": now + 300,
        "scope": "career:read career:write",
        **claims,
    }
    body = {k: v for k, v in body.items() if v is not None}
    return jwt.encode(body, key or env.key, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
async def env(db):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cfg = config()
    resolver = OAuthIdentityResolver(
        db, cfg, JwtVerifier(cfg, jwks_client=FakeJwks(key.public_key()))
    )
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    owner = ActorContext(a, ws_a, scopes_for_role(Role.OWNER))
    other = ActorContext(b, ws_b, scopes_for_role(Role.OWNER))
    await brain.create_experience(db, owner, title="A의 합성 경험")
    await brain.create_experience(db, other, title="B의 합성 경험")
    sub = f"user-{uuid4().hex[:12]}"  # 시험 DB를 같이 쓰므로 시험마다 다른 사용자
    link = await link_oauth_identity(db, owner, issuer=ISSUER, subject=sub)
    deps = CoreDeps(identity=NoTokens(), db=db, mcp_oauth=McpOAuth(cfg, resolver))
    app = FastAPI()
    add_metadata_routes(app, cfg)
    app.include_router(build_router(deps), prefix="/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="https://career.example.test"
    ) as c:
        yield SimpleNamespace(
            c=c, db=db, key=key, sub=sub, owner=owner, other=other, link=link, resolver=resolver
        )


async def mcp(c, token, method, params=None):
    return await c.post(
        "/api/core/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )


async def test_protected_resource_metadata_and_challenge(env):
    for path in (
        "/.well-known/oauth-protected-resource/api/core/mcp",
        "/.well-known/oauth-protected-resource",
    ):
        r = await env.c.get(path)
        assert r.status_code == 200
        m = r.json()
        assert m["resource"] == RESOURCE and m["authorization_servers"] == [ISSUER]
        assert m["scopes_supported"] == ["career:ai", "career:read", "career:write"]
        assert m["bearer_methods_supported"] == ["header"]
    r = await mcp(env.c, None, "tools/list")
    assert r.status_code == 401
    challenge = r.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert (
        'resource_metadata="https://career.example.test/.well-known/oauth-protected-resource'
        '/api/core/mcp"' in challenge
    )
    assert "invalid_token" not in challenge
    r = await mcp(env.c, "a.b.c", "tools/list")
    assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"]


async def test_valid_token_maps_to_the_linked_workspace_only(env):
    # 토큰에 다른 공간 ID를 넣어도 쓰지 않는다: 공간은 (issuer, sub) 연결이 정한다
    token = make_token(env, workspace_id=str(env.other.workspace_id))
    r = await mcp(env.c, token, "tools/call", {"name": "list_experiences", "arguments": {}})
    assert r.status_code == 200, r.text
    titles = [e["title"] for e in r.json()["result"]["structuredContent"]["experiences"]]
    assert titles == ["A의 합성 경험"]
    actor = await env.resolver.resolve(token)
    assert actor.workspace_id == env.owner.workspace_id and actor.kind == ACTOR_AGENT
    assert actor.scopes == frozenset({"read", "draft-write"})  # 역할 ∩ 토큰 범위, approve 없음
    async with env.db.workspace_session(env.owner.workspace_id) as s:
        [row] = (await s.execute(select(AuditEvent))).scalars().all()
    assert (row.tool, row.actor_id, row.actor_kind) == (
        "list_experiences",
        env.owner.subject_id,
        "agent",
    )


async def test_rejected_tokens(env):
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    bad = {
        "wrong audience": make_token(env, aud="https://other.example.test/mcp"),
        "no audience": make_token(env, aud=None),
        "expired": make_token(env, exp=now - 3600, iat=now - 7200),
        "wrong issuer": make_token(env, iss="https://evil.example.test"),
        "other key": make_token(env, other_key),
        "no known scope": make_token(env, scope="openid profile"),
        "unlinked subject": make_token(env, sub="someone-else"),
        "alg none": jwt.encode(
            {"iss": ISSUER, "aud": RESOURCE, "sub": env.sub}, None, algorithm="none"
        ),
    }
    for name, token in bad.items():
        r = await mcp(env.c, token, "tools/list")
        assert r.status_code == 401, name
    # 연결을 끊으면 같은 토큰이 바로 막힌다
    good = make_token(env)
    assert (await mcp(env.c, good, "tools/list")).status_code == 200
    assert await revoke_oauth_identity(env.db, env.owner, env.link.id)
    assert (await mcp(env.c, good, "tools/list")).status_code == 401


async def test_hs256_tokens_are_never_accepted(env):
    """허용 알고리즘(비대칭)만. 헤더의 alg가 HS256이면 키를 찾기 전에 거부한다."""
    token = jwt.encode(
        {"iss": ISSUER, "aud": RESOURCE, "sub": env.sub, "exp": int(time.time()) + 300},
        "shared-secret-synthetic-0123456789abcdef",
        algorithm="HS256",
    )
    assert await env.resolver.resolve(token) is None


async def test_read_scope_cannot_write(env):
    token = make_token(env, scope="career:read")
    r = await mcp(
        env.c,
        token,
        "tools/call",
        {"name": "save_job_urls", "arguments": {"urls": ["https://jobs.example.test/x"]}},
    )
    assert r.json()["result"]["structuredContent"] == {"error": "forbidden"}
    r = await mcp(env.c, token, "tools/call", {"name": "list_experiences", "arguments": {}})
    assert not r.json()["result"]["isError"]


async def test_linking_rules(env):
    editor = ActorContext(
        env.owner.subject_id, env.owner.workspace_id, scopes_for_role(Role.EDITOR)
    )
    agent = ActorContext(
        env.owner.subject_id, env.owner.workspace_id, scopes_for_role(Role.OWNER), kind=ACTOR_AGENT
    )
    for actor in (editor, agent):
        with pytest.raises(OAuthLinkError, match="forbidden"):
            await link_oauth_identity(env.db, actor, issuer=ISSUER, subject="new-sub")
    # 같은 subject를 다른 공간에 또 연결할 수 없다 (토큰 하나 = 공간 하나)
    with pytest.raises(OAuthLinkError, match="subject_already_linked"):
        await link_oauth_identity(env.db, env.other, issuer=ISSUER, subject=env.sub)
    again = await link_oauth_identity(env.db, env.owner, issuer=ISSUER, subject=env.sub)
    assert again.id == env.link.id


def test_config_is_strict():
    with pytest.raises(OAuthConfigError, match="https"):
        OAuthConfig(resource="http://career.example.test/mcp", issuer=ISSUER, jwks_url=ISSUER)
    with pytest.raises(OAuthConfigError, match="none"):
        config(algorithms=("RS256", "none"))
    with pytest.raises(OAuthConfigError, match="must not grant"):
        config(scope_map={"career:all": frozenset({"read", "approve"})})
    local = OAuthConfig(
        resource="http://127.0.0.1:8000/api/core/mcp",
        issuer="http://localhost:9000",
        jwks_url="http://localhost:9000/jwks",
    )
    assert (
        local.metadata_url
        == "http://127.0.0.1:8000/.well-known/oauth-protected-resource/api/core/mcp"
    )
    m = config().scope_map
    assert granted_scopes({"scope": "career:read career:ai junk"}, m) == {"read", "egress"}
    assert granted_scopes({"scp": ["career:write"]}, m) == {"read", "draft-write"}
    assert granted_scopes({}, m) == frozenset()

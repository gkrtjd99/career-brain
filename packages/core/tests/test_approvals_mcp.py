"""c0019: 승인 대상 버전 한 곳(DB 함수), OAuth 보강(introspection·토큰 폐기·jti), 감사 기록 보존,
시스템 화면 실시간 갱신(SSE).

모든 자료는 합성이다. 키는 시험마다 새로 만든다.
"""

import asyncio
import json
import time
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import psycopg
import pytest
from career_core.db import JobPosting, JobPostingVersion
from career_core.db.base import utcnow
from career_core.db.models import Application, ApplicationDocument, Approval, AuditEvent
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.interfaces.http.live import StateHints, Subscription
from career_core.interfaces.mcp.oauth import (
    IntrospectionVerifier,
    JwtVerifier,
    McpOAuth,
    OAuthConfig,
    OAuthConfigError,
    OAuthIdentityResolver,
)
from career_core.jobs.maintenance import run_maintenance
from career_core.jobs.wake import libpq_kwargs
from career_core.ports import ActorContext
from career_core.usecases import applications as app_uc
from career_core.usecases import approvals as approval_uc
from career_core.usecases import audit as audit_uc
from career_core.usecases import postings as posting_uc
from career_core.usecases import runs as run_uc
from career_core.usecases.oauth_identities import (
    link_oauth_identity,
    list_oauth_tokens,
    prune_oauth_tokens,
    revoke_oauth_identity,
    revoke_oauth_token,
)
from career_core.usecases.workspaces import create_workspace
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, ProgrammingError

pytestmark = pytest.mark.anyio

QUESTION = {"prompt": "지원 동기를 작성해 주세요. (500자 이내)", "char_limit": 500}
RESOURCE = "https://career.example.test/api/core/mcp"
ISSUER = "https://auth.example.test/realms/career"
INTROSPECT = ISSUER + "/protocol/openid-connect/token/introspect"


class FakeResolver:
    def __init__(self, tokens: dict[str, ActorContext]):
        self.tokens = tokens

    async def resolve(self, credential: str) -> ActorContext | None:
        return self.tokens.get(credential)


@pytest.fixture
async def env(db):
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    owner = ActorContext(a, ws_a, scopes_for_role(Role.OWNER))
    return SimpleNamespace(
        owner=owner,
        editor=ActorContext(a, ws_a, scopes_for_role(Role.EDITOR)),
        other=ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
    )


# ---------------------------------------------------------------- 1. 승인 대상 버전


async def saved_posting(db, actor, url: str):
    async with db.workspace_session(actor.workspace_id) as s:
        p = JobPosting(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            canonical_url=url,
            source_url=url,
            status="draft",
            created_by=actor.subject_id,
        )
        s.add(p)
        await s.flush()
        s.add(
            JobPostingVersion(
                id=uuid4(),
                workspace_id=actor.workspace_id,
                posting_id=p.id,
                version_no=1,
                final_url=url,
                method="manual_text",
                adapter="text",
                content_hash=uuid4().hex,
                text="합성 공고 본문",
                extracted={},
                missing=[],
                changed_fields=[],
                created_by=actor.subject_id,
            )
        )
    await posting_uc.confirm_posting(
        db,
        actor,
        p.id,
        overrides={"company": "가상회사", "title": "백엔드 개발자", "questions": [QUESTION]},
    )
    return p.id


async def draft_application(db, owner, url: str):
    posting = await saved_posting(db, owner, url)
    aid = await app_uc.create_application(db, owner, posting)
    [doc] = (await app_uc.get_application(db, owner, aid)).documents
    await app_uc.save_revision(
        db,
        owner,
        aid,
        doc.id,
        base_revision=0,
        operations=[{"op": "insert", "text": "합성 지원 동기 문단입니다."}],
    )
    return aid, doc.id, posting


async def request(db, actor, aid, did):
    return await approval_uc.request_approval(
        db,
        actor,
        action="applications.submit",
        target_id=aid,
        payload={"revisions": {str(did): 1}, "note": None},
    )


async def approval_state(db, ws, approval_id):
    async with db.workspace_session(ws) as s:
        row = await s.get(Approval, approval_id)
        return row.status, row.invalid_reason


async def test_version_function_reads_only_tables_that_recheck(db, owner_engine):
    """함수가 읽는 표(pg_depend)마다 다시 계산 trigger가 있다. 새 표를 읽게 바꾸면 이 시험이 잡는다."""
    with owner_engine.connect() as conn:
        read = set(
            conn.scalars(
                text(
                    """
                    SELECT DISTINCT d.refobjid::regclass::text
                    FROM pg_depend d
                    WHERE d.classid = 'pg_proc'::regclass
                      AND d.objid = 'core.application_target_version(uuid)'::regprocedure
                      AND d.refclassid = 'pg_class'::regclass
                    """
                )
            )
        )
        triggered = set(
            conn.scalars(
                text(
                    """
                    SELECT t.tgrelid::regclass::text FROM pg_trigger t
                    WHERE t.tgname = 'approvals_recheck' AND NOT t.tgisinternal
                      AND t.tgfoid = 'core.approvals_recheck_application()'::regprocedure
                    """
                )
            )
        )
        # c0014의 칸 비교 trigger는 남아 있지 않다 (칸 목록이 두 곳에 있지 않게)
        old = conn.scalar(
            text("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'approvals_on_%'")
        )
    assert read == {
        "core.applications",
        "core.application_documents",
        "core.document_revisions",
        "core.job_postings",
    }
    assert read <= triggered, read - triggered
    assert old == 0


async def test_submission_target_version_comes_from_the_db_function(db, env):
    aid, _, _ = await draft_application(db, env.owner, "https://jobs.example.test/v/1")
    async with db.workspace_session(env.owner.workspace_id) as s:
        version, summary = await app_uc.submission_target(s, aid)
        sql = await s.scalar(select(func.core.application_target_version(aid)))
        missing = await s.scalar(select(func.core.application_target_version(uuid4())))
    assert version == sql and len(version) == 64
    assert summary["application_id"] == str(aid)
    assert missing is None
    # 다른 공간에서는 대상이 보이지 않는다 (RLS) → 버전 없음
    async with db.workspace_session(env.other.workspace_id) as s:
        assert await s.scalar(select(func.core.application_target_version(aid))) is None


# 대상 버전에 든 칸 하나씩: 바꾸면 그 트랜잭션에서 열린 승인이 닫힌다
VERSION_CHANGES = {
    "document char_limit": ("doc", {"char_limit": 321}),
    "document byte_limit": ("doc", {"byte_limit": 999}),
    "document count_rule": ("doc", {"count_rule": {"spaces": False}}),
    "document question_prompt": ("doc", {"question_prompt": "바뀐 문항 (합성)"}),
    "application status": ("app", {"status": "withdrawn"}),
    "posting fields": ("posting", {"fields": {"company": "다른 가상회사"}}),
    "posting pending_changes": ("posting", {"pending_changes": ["title"]}),
}


@pytest.mark.parametrize("name", sorted(VERSION_CHANGES))
async def test_each_version_field_invalidates_and_title_does_not(db, env, name):
    owner, editor = env.owner, env.editor
    ws = owner.workspace_id
    aid, did, posting = await draft_application(db, owner, f"https://jobs.example.test/f/{name}")
    req = await request(db, editor, aid, did)
    # 버전 밖의 칸(제목)은 그대로
    async with db.workspace_session(ws) as s:
        await s.execute(
            update(ApplicationDocument).where(ApplicationDocument.id == did).values(title="새 제목")
        )
        await s.execute(update(Application).where(Application.id == aid).values(title="새 지원"))
    assert await approval_state(db, ws, req.id) == ("pending", None)
    kind, values = VERSION_CHANGES[name]
    model, key = {
        "doc": (ApplicationDocument, did),
        "app": (Application, aid),
        "posting": (JobPosting, posting),
    }[kind]
    async with db.workspace_session(ws) as s:
        await s.execute(update(model).where(model.id == key).values(**values))
        # 같은 트랜잭션 안에서 이미 닫혔다
        assert (await s.get(Approval, req.id)).status == "invalidated"
    assert await approval_state(db, ws, req.id) == ("invalidated", "target_changed")


async def test_new_revision_and_new_document_invalidate_delete_is_missing(db, env):
    owner, editor = env.owner, env.editor
    ws = owner.workspace_id
    aid, did, _ = await draft_application(db, owner, "https://jobs.example.test/r/1")
    first = await request(db, editor, aid, did)
    await app_uc.save_revision(
        db, owner, aid, did, base_revision=1, operations=[{"op": "insert", "text": "둘째 문단."}]
    )
    assert await approval_state(db, ws, first.id) == ("invalidated", "target_changed")
    second = await approval_uc.request_approval(
        db,
        editor,
        action="applications.submit",
        target_id=aid,
        payload={"revisions": {str(did): 2}, "note": None},
    )
    async with db.workspace_session(ws) as s:
        await s.execute(text("DELETE FROM core.applications WHERE id = :a"), {"a": aid})
    assert await approval_state(db, ws, second.id) == ("invalidated", "target_missing")


# ---------------------------------------------------------------- 2. OAuth 보강


class FakeJwks:
    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=self.public_key)


def oauth_config(**kw):
    base = {
        "resource": RESOURCE,
        "issuer": ISSUER,
        "jwks_url": ISSUER + "/protocol/openid-connect/certs",
        "introspection_url": INTROSPECT,
        "introspection_client_id": "career-brain-test",
        "introspection_client_secret": "synthetic-secret-not-real",
        "local_token_prefixes": ("cbp_",),
    }
    return OAuthConfig(**{**base, **kw})


class FakeAuthServer:
    """introspection 주소 흉내: 등록한 불투명 토큰만 active."""

    def __init__(self):
        self.tokens: dict[str, dict] = {}
        self.seen: list[str] = []
        self.auth: list[str | None] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
        self.seen.append(form.get("token", ""))
        self.auth.append(request.headers.get("authorization"))
        return httpx.Response(200, json=self.tokens.get(form.get("token"), {"active": False}))


@pytest.fixture
async def oauth(db, env):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server = FakeAuthServer()
    cfg = oauth_config()
    resolver = OAuthIdentityResolver(
        db,
        cfg,
        JwtVerifier(cfg, jwks_client=FakeJwks(key.public_key())),
        IntrospectionVerifier(cfg, transport=httpx.MockTransport(server.handler)),
    )
    sub = f"user-{uuid4().hex[:12]}"
    link = await link_oauth_identity(db, env.owner, issuer=ISSUER, subject=sub)
    return SimpleNamespace(key=key, server=server, cfg=cfg, resolver=resolver, sub=sub, link=link)


def make_jwt(o, sub=None, **claims):
    now = int(time.time())
    body = {
        "iss": ISSUER,
        "aud": RESOURCE,
        "sub": sub or o.sub,
        "iat": now,
        "exp": now + 300,
        "scope": "career:read",
        **claims,
    }
    body = {k: v for k, v in body.items() if v is not None}
    return jwt.encode(body, o.key, algorithm="RS256", headers={"kid": "k1"})


def opaque(o, token, **claims):
    now = int(time.time())
    o.server.tokens[token] = {
        "active": True,
        "iss": ISSUER,
        "aud": [RESOURCE, "other-resource"],
        "sub": o.sub,
        "exp": now + 300,
        "scope": "career:read career:write",
        "jti": f"opaque-{uuid4().hex[:8]}",
        **claims,
    }
    return token


async def test_config_needs_a_verifier_and_introspection_credentials():
    with pytest.raises(OAuthConfigError):
        OAuthConfig(resource=RESOURCE, issuer=ISSUER)
    with pytest.raises(OAuthConfigError):
        OAuthConfig(resource=RESOURCE, issuer=ISSUER, introspection_url=INTROSPECT)
    with pytest.raises(OAuthConfigError):
        oauth_config(introspection_url="http://auth.example.test/introspect")  # https만
    # 비밀은 repr에 나오지 않는다
    assert "synthetic-secret" not in repr(oauth_config())
    # introspection만 (JWKS 없이)
    assert oauth_config(jwks_url=None).jwks_url is None


async def test_opaque_token_via_introspection(db, env, oauth):
    token = opaque(oauth, "opaque-token-" + uuid4().hex)
    actor = await oauth.resolver.resolve(token)
    assert actor is not None and actor.workspace_id == env.owner.workspace_id
    assert actor.kind == "agent" and actor.scopes == frozenset({"read", "draft-write"})
    assert oauth.server.auth[0].startswith("Basic ")  # client_secret_basic
    # 캐시: 두 번째는 인가 서버에 묻지 않는다
    assert await oauth.resolver.resolve(token) is not None
    assert oauth.resolver.introspector.calls == 1
    [row] = await list_oauth_tokens(db, env.owner)
    assert (row.via, row.ref_kind, row.uses) == ("introspection", "jti", 2)
    assert row.token_ref == oauth.server.tokens[token]["jti"]


@pytest.mark.parametrize(
    "claims",
    [
        {"active": False},
        {"aud": "https://other.example.test/mcp"},
        {"iss": "https://evil.example.test"},
        {"exp": int(time.time()) - 3600},
        {"sub": None},
    ],
    ids=["inactive", "audience", "issuer", "expired", "no-sub"],
)
async def test_introspection_rejects_like_jwt(db, env, oauth, claims):
    token = opaque(oauth, "opaque-bad-" + uuid4().hex, **claims)
    assert await oauth.resolver.resolve(token) is None


async def test_local_api_tokens_are_never_sent_to_the_auth_server(db, env, oauth):
    assert await oauth.resolver.resolve("cbp_" + "x" * 40) is None
    assert oauth.resolver.last_rejection == "local_token"
    assert oauth.server.seen == []


async def test_revoked_jti_is_rejected_until_expiry_and_others_still_work(db, env, oauth):
    token = make_jwt(oauth, jti="jti-" + uuid4().hex)
    other = make_jwt(oauth, jti="jti-" + uuid4().hex)
    assert await oauth.resolver.resolve(token) is not None
    assert await oauth.resolver.resolve(other) is not None
    rows = {r.token_ref: r for r in await list_oauth_tokens(db, env.owner)}
    target = rows[jwt.decode(token, options={"verify_signature": False})["jti"]]
    assert target.via == "jwt" and target.expires_at is not None
    # 편집자·에이전트는 폐기할 수 없다 (소유자 사람만)
    from career_core.usecases.oauth_identities import OAuthLinkError

    with pytest.raises(OAuthLinkError):
        await revoke_oauth_token(db, env.editor, target.id)
    assert await revoke_oauth_token(db, env.owner, target.id) is True
    assert await revoke_oauth_token(db, env.owner, target.id) is False
    assert await oauth.resolver.resolve(token) is None
    assert oauth.resolver.last_rejection == "revoked"
    assert await oauth.resolver.resolve(other) is not None
    # 연결을 끊으면 그 사용자의 모든 토큰이 막힌다
    assert await revoke_oauth_identity(db, env.owner, oauth.link.id)
    assert await oauth.resolver.resolve(other) is None
    assert oauth.resolver.last_rejection == "not_linked"


async def test_token_without_jti_is_tracked_by_hash_or_rejected_when_required(db, env, oauth):
    token = make_jwt(oauth)  # jti 없음
    assert await oauth.resolver.resolve(token) is not None
    [row] = await list_oauth_tokens(db, env.owner)
    assert row.ref_kind == "sha256" and len(row.token_ref) == 64 and token not in row.token_ref
    strict = oauth_config(require_jti=True)
    resolver = OAuthIdentityResolver(
        db, strict, JwtVerifier(strict, jwks_client=FakeJwks(oauth.key.public_key()))
    )
    assert await resolver.resolve(token) is None
    assert resolver.last_rejection == "jti_required"
    assert await resolver.resolve(make_jwt(oauth, jti="jti-" + uuid4().hex)) is not None


async def test_same_jti_from_another_subject_is_rejected(db, env, oauth):
    second = f"user-{uuid4().hex[:12]}"
    await link_oauth_identity(db, env.owner, issuer=ISSUER, subject=second)
    jti = "jti-" + uuid4().hex
    assert await oauth.resolver.resolve(make_jwt(oauth, jti=jti)) is not None
    assert await oauth.resolver.resolve(make_jwt(oauth, sub=second, jti=jti)) is None
    assert oauth.resolver.last_rejection == "jti_conflict"
    # 같은 토큰을 여러 번 쓰는 것은 정상이다 (bearer 토큰)
    assert await oauth.resolver.resolve(make_jwt(oauth, jti=jti)) is not None


async def test_token_records_are_workspace_scoped_and_pruned(db, env, oauth, owner_engine):
    assert await oauth.resolver.resolve(make_jwt(oauth, jti="jti-" + uuid4().hex))
    assert await list_oauth_tokens(db, env.other) == []
    async with db.workspace_session(env.other.workspace_id) as s:
        assert (await s.execute(text("SELECT count(*) FROM core.oauth_tokens"))).scalar() == 0
    async with db.workspace_session(env.owner.workspace_id) as s:
        await s.execute(text("UPDATE core.oauth_tokens SET expires_at = now() - interval '3 days'"))
    assert await prune_oauth_tokens(db, env.owner.workspace_id) == 1
    assert await list_oauth_tokens(db, env.owner) == []


async def test_oauth_http_screen_is_owner_only(db, env, oauth):
    deps = CoreDeps(
        identity=FakeResolver({"owner": env.owner, "editor": env.editor}),
        db=db,
        mcp_oauth=McpOAuth(oauth.cfg, oauth.resolver),
    )
    app = FastAPI()
    app.include_router(build_router(deps), prefix="/api")
    owner = {"Authorization": "Bearer owner"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="https://career.example.test"
    ) as c:
        assert (await c.get("/api/core/oauth")).status_code == 401
        assert (
            await c.get("/api/core/oauth", headers={"Authorization": "Bearer editor"})
        ).status_code == 403
        await oauth.resolver.resolve(make_jwt(oauth, jti="jti-http"))
        r = await c.get("/api/core/oauth", headers=owner)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["config"] == {
            "enabled": True,
            "issuer": ISSUER,
            "resource": RESOURCE,
            "jwks": True,
            "introspection": True,
            "require_jti": False,
            "introspect_jwt": False,
            "introspection_cache_seconds": 10,
        }
        assert [link["subject"] for link in body["links"]] == [oauth.sub]
        [tok] = body["tokens"]
        assert tok["token_ref"] == "jti-http" and "synthetic" not in r.text
        sub2 = f"user-{uuid4().hex[:12]}"
        r = await c.post(
            "/api/core/oauth/links", json={"subject": sub2, "label": "노트북"}, headers=owner
        )
        assert r.status_code == 201 and r.json()["issuer"] == ISSUER
        dup = await c.post("/api/core/oauth/links", json={"subject": sub2}, headers=owner)
        assert dup.status_code == 201  # 같은 연결이면 그대로
        r = await c.post(f"/api/core/oauth/tokens/{tok['id']}/revoke", headers=owner)
        assert r.json() == {"revoked": True}
        r = await c.post(f"/api/core/oauth/links/{oauth.link.id}/revoke", headers=owner)
        assert r.json() == {"revoked": True}
        listed = (await c.get("/api/core/oauth", headers=owner)).json()
        assert {x["subject"]: x["revoked_at"] is not None for x in listed["links"]} == {
            oauth.sub: True,
            sub2: False,
        }
        # 다른 공간의 연결 ID는 없는 것과 같다
        other = await link_oauth_identity(db, env.other, issuer=ISSUER, subject=f"u-{uuid4().hex}")
        r = await c.post(f"/api/core/oauth/links/{other.id}/revoke", headers=owner)
        assert r.json() == {"revoked": False}


# ---------------------------------------------------------------- 3. 감사 기록 보존


async def add_audit(db, actor, days_ago: float):
    async with db.workspace_session(actor.workspace_id) as s:
        s.add(
            AuditEvent(
                workspace_id=actor.workspace_id,
                actor_id=actor.subject_id,
                actor_kind="agent",
                channel="mcp-http",
                event="tool_call",
                tool="search_brain",
                status="ok",
                duration_ms=1,
                attributes={},
                created_at=utcnow() - timedelta(days=days_ago),
            )
        )


async def audit_count(db, actor) -> int:
    async with db.workspace_session(actor.workspace_id) as s:
        return await s.scalar(select(func.count()).select_from(AuditEvent))


async def test_audit_retention_deletes_only_old_rows_of_this_workspace(db, env):
    for days in (400, 200, 3, 0):
        await add_audit(db, env.owner, days)
        await add_audit(db, env.other, days)
    assert await audit_uc.purge_audit_events(db, env.owner.workspace_id, 180) == 2
    assert await audit_count(db, env.owner) == 2
    assert await audit_count(db, env.other) == 4  # 다른 공간은 그대로
    assert await audit_uc.purge_audit_events(db, env.owner.workspace_id, 0) == 0  # 0 = 계속 보관
    # 함수의 최소 보존 기간: 7일보다 짧게는 지울 수 없다
    assert await audit_uc.purge_audit_events(db, env.owner.workspace_id, 1) == 0  # 7일로 올림
    assert await audit_count(db, env.owner) == 2
    async with db.workspace_session(env.owner.workspace_id) as s:
        with pytest.raises(DBAPIError, match="at least 7 days"):
            await s.execute(text("SELECT core.purge_audit_events(3)"))


async def test_app_role_cannot_delete_audit_rows_directly(db, env):
    await add_audit(db, env.owner, 400)
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with db.workspace_session(env.owner.workspace_id) as s:
            await s.execute(text("DELETE FROM core.audit_events"))
    # 공간 문맥 없이 부르면 아무것도 지우지 않는다
    async with db.system_session() as s:
        assert await s.scalar(text("SELECT core.purge_audit_events(30)")) == 0
    assert await audit_count(db, env.owner) == 1


async def test_purge_function_is_pinned_security_definer(owner_engine):
    with owner_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT prosecdef, proconfig FROM pg_proc "
                "WHERE oid = 'core.purge_audit_events(integer)'::regprocedure"
            )
        ).one()
        public = conn.scalar(
            text(
                "SELECT has_function_privilege('public', "
                "'core.purge_audit_events(integer)', 'EXECUTE')"
            )
        )
    assert row.prosecdef is True and "search_path=pg_catalog" in row.proconfig
    assert public is False


def test_retention_setting():
    assert audit_uc.retention_days("") == 180
    assert audit_uc.retention_days("0") == 0
    assert audit_uc.retention_days("-5") == 0
    assert audit_uc.retention_days("3") == 7
    assert audit_uc.retention_days("90") == 90
    assert audit_uc.retention_days("abc") == 180


async def test_periodic_maintenance_runs_the_retention_step(db, env, monkeypatch):
    monkeypatch.setenv(audit_uc.RETENTION_ENV, "30")
    await add_audit(db, env.owner, 40)
    await add_audit(db, env.owner, 1)
    report = await run_maintenance(db)
    assert report.ran and report.errors == 0
    assert report.counts["prune_audit_and_tokens"] >= 1
    assert await audit_count(db, env.owner) == 1


# ---------------------------------------------------------------- 4. 실시간 갱신 (SSE)


class FakeHints:
    """LISTEN 없이 힌트를 직접 보내는 공급자."""

    listening = True

    def __init__(self):
        self.subs: set[Subscription] = set()

    @property
    def subscribers(self):
        return len(self.subs)

    @contextmanager
    def subscribe(self):
        sub = Subscription()
        self.subs.add(sub)
        try:
            yield sub
        finally:
            self.subs.discard(sub)

    def publish(self, payload):
        for s in list(self.subs):
            s.push(payload)


def parse_sse(body: str) -> list[tuple[str, dict]]:
    out = []
    for chunk in body.split("\n\n"):
        lines = chunk.strip().splitlines()
        name = next((x[7:] for x in lines if x.startswith("event: ")), None)
        data = next((x[6:] for x in lines if x.startswith("data: ")), None)
        if name:
            out.append((name, json.loads(data or "{}")))
    return out


def live_app(db, tokens, hints):
    deps = CoreDeps(identity=FakeResolver(tokens), db=db, live=hints)
    app = FastAPI()
    app.include_router(build_router(deps), prefix="/api")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test")


async def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("timed out")
        await asyncio.sleep(0.02)


async def start_run(db, actor, n=1):
    return await run_uc.start_run(
        run_uc.RunDeps(db=db),
        actor,
        "save_job_urls",
        {"urls": [f"https://jobs.example.test/live/{uuid4().hex}" for _ in range(n)]},
    )


async def test_live_requires_the_same_token_rules(db, env):
    async with live_app(db, {"o": env.owner}, FakeHints()) as c:
        assert (await c.get("/api/core/live")).status_code == 401
        r = await c.get("/api/core/live", headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401
        # 토큰을 URL로 받지 않는다
        assert (await c.get("/api/core/live?token=o&access_token=o")).status_code == 401


async def test_live_sends_events_only_for_changes_in_this_workspace(db, env):
    hints = FakeHints()
    tokens = {"a": env.owner, "b": env.other}
    async with live_app(db, tokens, hints) as c:
        auth = {"Authorization": "Bearer a"}
        mine = asyncio.create_task(c.get("/api/core/live?max_seconds=2", headers=auth))
        theirs = asyncio.create_task(
            c.get("/api/core/live?max_seconds=2", headers={"Authorization": "Bearer b"})
        )
        await wait_for(lambda: hints.subscribers == 2)
        await start_run(db, env.owner)
        hints.publish("runs")  # 힌트는 모든 구독자에게 간다
        a, b = await mine, await theirs
    assert a.status_code == 200 and a.headers["content-type"].startswith("text/event-stream")
    ev_a, ev_b = parse_sse(a.text), parse_sse(b.text)
    assert ev_a[0] == ("ready", {"channels": ["runs", "approvals"], "listening": True})
    assert [n for n, _ in ev_a].count("runs") == 1 and "approvals" not in [n for n, _ in ev_a]
    # 다른 공간: 힌트는 받았지만 다시 읽은 요약이 같으므로 아무 이벤트도 없다
    assert [n for n, _ in ev_b] == ["ready", "bye"]
    # 이벤트 데이터에 공간·실행 ID가 없다
    assert str(env.owner.workspace_id) not in a.text and "id" not in json.dumps(ev_a[1][1])
    assert hints.subscribers == 0


async def test_live_without_hints_falls_back_to_periodic_check(db, env, monkeypatch):
    from career_core.interfaces.http import live

    monkeypatch.setattr(live, "CHECK_POLLING_SECONDS", 0.3)
    hints = FakeHints()
    hints.listening = False
    async with live_app(db, {"a": env.owner}, hints) as c:
        task = asyncio.create_task(
            c.get("/api/core/live?max_seconds=2", headers={"Authorization": "Bearer a"})
        )
        await wait_for(lambda: hints.subscribers == 1)
        await start_run(db, env.owner)  # 알림 없음
        r = await task
    assert "runs" in [n for n, _ in parse_sse(r.text)]


async def test_live_closes_when_the_token_is_revoked(db, env, monkeypatch):
    from career_core.interfaces.http import live

    monkeypatch.setattr(live, "REAUTH_SECONDS", 0.1)
    hints = FakeHints()
    tokens = {"a": env.owner}
    async with live_app(db, tokens, hints) as c:
        task = asyncio.create_task(
            c.get("/api/core/live?max_seconds=5", headers={"Authorization": "Bearer a"})
        )
        await wait_for(lambda: hints.subscribers == 1)
        tokens.clear()  # 폐기
        await asyncio.sleep(0.2)
        hints.publish("runs")
        r = await task
    assert [n for n, _ in parse_sse(r.text)] == ["ready", "unauthorized"]


async def test_viewer_without_approval_scope_gets_runs_only(db, env):
    viewer = ActorContext(env.owner.subject_id, env.owner.workspace_id, frozenset({"read"}))
    async with live_app(db, {"v": viewer}, FakeHints()) as c:
        r = await c.get("/api/core/live?max_seconds=1", headers={"Authorization": "Bearer v"})
    assert parse_sse(r.text)[0][1]["channels"] == ["runs", "approvals"]  # read면 둘 다
    nobody = ActorContext(env.owner.subject_id, env.owner.workspace_id, frozenset())
    async with live_app(db, {"n": nobody}, FakeHints()) as c:
        r = await c.get("/api/core/live?max_seconds=1", headers={"Authorization": "Bearer n"})
    assert r.status_code == 403


async def test_state_notify_payload_has_no_ids(db, env, pg):
    conn = await psycopg.AsyncConnection.connect(**libpq_kwargs(pg.app), autocommit=True)
    async with conn:
        await conn.execute("LISTEN career_state")
        run = await start_run(db, env.owner)
        await run_uc.cancel_run(db, env.owner, run.id)
        payloads = []

        async def collect():
            async for n in conn.notifies():
                payloads.append(n.payload)
                if len(payloads) >= 2:
                    return

        await asyncio.wait_for(collect(), 5)
    assert set(payloads) <= {"runs", "approvals"}
    assert all(str(run.id) not in p and str(env.owner.workspace_id) not in p for p in payloads)


async def test_real_listen_hints_drive_the_stream(db, env):
    hints = StateHints(db.engine.url)
    async with live_app(db, {"a": env.owner}, hints) as c:
        task = asyncio.create_task(
            c.get("/api/core/live?max_seconds=3", headers={"Authorization": "Bearer a"})
        )
        await wait_for(lambda: hints.subscribers == 1)
        assert await hints.wait_listening(5)
        await start_run(db, env.owner)
        r = await task
    assert "runs" in [n for n, _ in parse_sse(r.text)]
    assert hints.subscribers == 0 and not hints.listening


# ---------------------------------------------------------------- 5. c0025: JWT introspection·캐시


@pytest.fixture
async def oauth_jwt_checked(db, env):
    """서명이 맞는 JWT도 introspection으로 확인하는 설정."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server = FakeAuthServer()
    cfg = oauth_config(introspect_jwt=True)
    resolver = OAuthIdentityResolver(
        db,
        cfg,
        JwtVerifier(cfg, jwks_client=FakeJwks(key.public_key())),
        IntrospectionVerifier(cfg, transport=httpx.MockTransport(server.handler)),
    )
    sub = f"user-{uuid4().hex[:12]}"
    await link_oauth_identity(db, env.owner, issuer=ISSUER, subject=sub)
    return SimpleNamespace(key=key, server=server, cfg=cfg, resolver=resolver, sub=sub)


def jwt_is_active(o, token, **claims):
    """인가 서버 흉내에 그 JWT를 활성으로 등록한다 (introspection 응답)."""
    body = jwt.decode(token, options={"verify_signature": False})
    o.server.tokens[token] = {"active": True, **body, **claims}


async def test_introspect_jwt_config_needs_both_verifiers():
    with pytest.raises(OAuthConfigError):
        oauth_config(introspect_jwt=True, introspection_url=None)
    with pytest.raises(OAuthConfigError):
        oauth_config(introspect_jwt=True, jwks_url=None)
    assert oauth_config().introspection_cache_seconds == 10  # 짧은 기본값 (반영 지연 상한)


async def test_jwt_revoked_at_the_auth_server_is_rejected_after_the_cache(
    db, env, oauth_jwt_checked
):
    o = oauth_jwt_checked
    clock = [time.time()]
    o.resolver.introspector._clock = lambda: clock[0]
    token = make_jwt(o, jti="jti-" + uuid4().hex)
    jwt_is_active(o, token)
    assert await o.resolver.resolve(token) is not None
    assert await o.resolver.resolve(token) is not None
    assert o.resolver.introspector.calls == 1  # 같은 캐시를 지난다
    # 인가 서버에서 폐기: 서명·exp는 그대로지만 introspection이 inactive
    o.server.tokens[token] = {"active": False}
    assert await o.resolver.resolve(token) is not None  # 캐시 시간 안
    clock[0] += o.cfg.introspection_cache_seconds + 1
    assert await o.resolver.resolve(token) is None
    assert o.resolver.last_rejection == "jwt_inactive"
    # 원문 토큰은 캐시 키가 아니다 (해시만)
    assert token not in o.resolver.introspector._cache


async def test_jwt_introspection_subject_must_match(db, env, oauth_jwt_checked):
    o = oauth_jwt_checked
    token = make_jwt(o)
    jwt_is_active(o, token, sub="someone-else")
    assert await o.resolver.resolve(token) is None
    assert o.resolver.last_rejection == "jwt_subject_mismatch"


async def test_jwt_signature_still_checked_first(db, env, oauth_jwt_checked):
    o = oauth_jwt_checked
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        {"iss": ISSUER, "aud": RESOURCE, "sub": o.sub, "exp": int(time.time()) + 300},
        other,
        algorithm="RS256",
    )
    o.server.tokens[forged] = {"active": True, "aud": RESOURCE, "sub": o.sub}
    assert await o.resolver.resolve(forged) is None
    assert o.server.seen == []  # 서명이 틀린 토큰은 인가 서버로 보내지 않는다


async def test_jwt_without_introspect_option_does_not_call_the_auth_server(db, env, oauth):
    token = make_jwt(oauth)
    assert await oauth.resolver.resolve(token) is not None
    assert oauth.server.seen == []


async def test_zero_cache_asks_every_time(db, env, oauth_jwt_checked):
    o = oauth_jwt_checked
    cfg = oauth_config(introspect_jwt=True, introspection_cache_seconds=0)
    intro = IntrospectionVerifier(cfg, transport=httpx.MockTransport(o.server.handler))
    token = make_jwt(o)
    jwt_is_active(o, token)
    assert await intro.verify(token) is not None and await intro.verify(token) is not None
    assert intro.calls == 2 and not intro._cache


# ---------------------------------------------------------------- 6. c0025: SSE 공간별 묶어 읽기


async def test_many_connections_in_one_workspace_share_one_query_per_hint(db, env, monkeypatch):
    from career_core.interfaces.http import live

    calls: list = []
    real = live.workspace_fingerprints

    async def counted(database, workspace_id):
        calls.append(workspace_id)
        return await real(database, workspace_id)

    monkeypatch.setattr(live, "workspace_fingerprints", counted)
    hints = FakeHints()
    n = 6
    tokens = {"a": env.owner, "b": env.other}
    async with live_app(db, tokens, hints) as c:
        auth = {"Authorization": "Bearer a"}
        mine = [
            asyncio.create_task(c.get("/api/core/live?max_seconds=3", headers=auth))
            for _ in range(n)
        ]
        theirs = asyncio.create_task(
            c.get("/api/core/live?max_seconds=3", headers={"Authorization": "Bearer b"})
        )
        await wait_for(lambda: hints.subscribers == n + 1)
        await asyncio.sleep(0.05)
        calls.clear()  # 연결할 때의 첫 읽기는 빼고 센다
        await start_run(db, env.owner)
        hints.publish("runs")
        await asyncio.sleep(0.6)
        first_hint = list(calls)
        await start_run(db, env.owner)
        hints.publish("runs")
        await asyncio.sleep(0.6)
        second_hint = calls[len(first_hint) :]
        responses = [await t for t in mine]
        other = await theirs
    # 힌트 하나에 공간마다 쿼리 하나 (연결 수와 무관)
    assert sorted(map(str, first_hint)) == sorted(
        [str(env.owner.workspace_id), str(env.other.workspace_id)]
    )
    assert len(second_hint) == 2
    for r in responses:
        assert [x for x, _ in parse_sse(r.text)].count("runs") == 2
    assert [x for x, _ in parse_sse(other.text)] == ["ready", "bye"]


async def test_workspace_summaries_share_inflight_and_recent_results(db, env, monkeypatch):
    from career_core.interfaces.http import live

    gate = asyncio.Event()
    calls = []

    async def slow(database, workspace_id):
        calls.append(workspace_id)
        await gate.wait()
        return {"runs": f"r{len(calls)}", "approvals": "a"}

    monkeypatch.setattr(live, "workspace_fingerprints", slow)
    now = [100.0]
    shared = live.WorkspaceSummaries(db, clock=lambda: now[0])
    ws = env.owner.workspace_id
    waiting = [asyncio.create_task(shared.read(ws, 99.0)) for _ in range(5)]
    await asyncio.sleep(0.01)
    waiting[0].cancel()  # 한 연결이 끊겨도 나머지는 같은 쿼리를 받는다
    gate.set()
    got = await asyncio.gather(*waiting[1:])
    assert len(calls) == 1 and all(g["runs"] == "r1" for g in got)
    assert (await shared.read(ws, 100.0))["runs"] == "r1"  # 그 뒤 결과면 다시 읽지 않는다
    now[0] = 101.0
    assert (await shared.read(ws, 100.5))["runs"] == "r2"  # 힌트가 더 새로우면 새로 읽는다
    assert len(calls) == 2

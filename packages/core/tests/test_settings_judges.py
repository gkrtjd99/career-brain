"""c0021: 공간 공용 설정(표시 시간대·기술명 별칭), MCP 학습 기록 검색 옵션, 판정 모델(참고 의견).

모든 자료는 합성이다. 판정 모델은 각본대로 답하는 가짜 ModelClient로 게이트웨이를 지나 부른다.
"""

import importlib
import json
import re
import secrets
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import Claim, DocumentBlock, ModelCall, StudyRequirementClaim
from career_core.db.migrate import CORE_CHAIN, upgrade
from career_core.domain.documents import (
    BlockInput,
    CheckContext,
    fact_changes,
    requirement_visibility,
)
from career_core.domain.korean import (
    AliasError,
    canonical_tech,
    found_spellings,
    merge_aliases,
    parse_alias_entries,
    use_aliases,
)
from career_core.domain.study import requirement_terms
from career_core.domain.workspace import Role, scopes_for_role
from career_core.evals import judge_eval
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import parsing_handlers, run_until_idle
from career_core.llm import ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext
from career_core.testing import admin_url
from career_core.usecases import applications as app_uc
from career_core.usecases import brain, judges
from career_core.usecases import postings as postings_uc
from career_core.usecases import study as study_uc
from career_core.usecases import workspace_settings as ws_uc
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.sources import upload_source
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 11, 17, 14, 30, tzinfo=UTC)  # 서울 23:30
QUESTIONS = [{"prompt": "지원 동기 (500자)", "char_limit": 500}]


class FakeResolver:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def env(db):
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    tokens = {
        "owner": ActorContext(a, ws_a, scopes_for_role(Role.OWNER)),
        "editor": ActorContext(a, ws_a, scopes_for_role(Role.EDITOR)),
        "viewer": ActorContext(a, ws_a, scopes_for_role(Role.VIEWER)),
        "other": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
    }
    return {**tokens, "tokens": tokens}


async def saved_posting(db, actor, url, fields):
    [r] = await postings_uc.save_urls(db, actor, [url])
    await postings_uc.confirm_posting(db, actor, r.posting_id, overrides=fields)
    return r.posting_id


# ---------------------------------------------------------------- 별칭 규칙


def test_alias_entries_merge_over_builtin():
    entries = parse_alias_entries(
        [
            {"term": "FastAPI", "aliases": ["패스트API", "fast api", "FastAPI"]},
            {"term": "kubernetes", "aliases": ["쿠베"]},  # 기본 기술에 별칭 더하기
            {"term": "TeamSpeak", "aliases": ["ts"]},  # 기본 표의 typescript 별칭을 가져간다
        ]
    )
    assert entries[0].aliases == ("패스트API", "fast api")  # 자기 이름·중복은 뺀다
    table = merge_aliases(entries)
    assert canonical_tech("패스트api", table) == "fastapi"
    assert canonical_tech("쿠베", table) == canonical_tech("k8s", table) == "kubernetes"
    assert canonical_tech("ts", table) == "teamspeak"
    assert "ts" not in table.spellings("typescript")
    assert found_spellings("쿠베 클러스터를 운영했다", "Kubernetes", table) == ["쿠베"]
    # 기본 표만 쓰면 모른다
    assert found_spellings("쿠베 클러스터를 운영했다", "Kubernetes") == []
    with use_aliases(table):
        assert found_spellings("쿠베 클러스터를 운영했다", "Kubernetes") == ["쿠베"]
    assert found_spellings("쿠베 클러스터를 운영했다", "Kubernetes") == []

    for bad, code in (
        ([{"term": "Go", "aliases": ["고"]}, {"term": "Gopher", "aliases": ["고"]}], "conflict"),
        ([{"term": "", "aliases": []}], "invalid_alias_term"),
        ([{"term": "x" * 41}], "invalid_alias_term"),
        ([{"term": "a", "aliases": ["b"] * 21}], "too_many_aliases"),
        ("python", "invalid_aliases"),
    ):
        with pytest.raises(AliasError, match=code):
            parse_alias_entries(bad)


def test_aliases_reach_document_checks_and_frequency_grouping():
    table = merge_aliases(parse_alias_entries([{"term": "kubernetes", "aliases": ["쿠베"]}]))
    blocks = [BlockInput("b1", "쿠베 클러스터를 직접 운영했습니다.")]
    req = (("requirements", "Kubernetes 운영 경험"),)
    [row] = requirement_visibility(CheckContext(requirements=req), blocks)
    assert row["verdict"] == "unmet" and row["document_terms"] == []
    [row] = requirement_visibility(CheckContext(requirements=req, aliases=table), blocks)
    assert row["verdict"] == "overclaim_risk" and row["document_terms"] == ["kubernetes"]
    # 사실 변경: 같은 기술의 다른 표기는 새 기술이 아니다
    assert fact_changes("쿠베로 배포했다", "Kubernetes로 배포했다") == [
        {"code": "new_term", "values": ["kubernetes"]}
    ]
    assert fact_changes("쿠베로 배포했다", "Kubernetes로 배포했다", aliases=table) == []
    # 요건 빈도 묶기
    fast = merge_aliases(
        parse_alias_entries([{"term": "FastAPI", "aliases": ["패스트API", "fast api"]}])
    )
    keys = lambda t, a=None: [x.key for x in requirement_terms(t, a)]
    assert keys("패스트API 경험") == ["api"]
    assert keys("패스트API 경험", fast) == ["fastapi"]
    assert keys("Fast API 개발 경험", fast) == ["fastapi"]
    assert [x.label for x in requirement_terms("fastapi", fast)] == ["FastAPI"]
    assert keys("쿠버네티스와 Docker") == ["docker", "kubernetes"]  # 기본 표는 그대로


# ---------------------------------------------------------------- 공간 설정 API


async def test_workspace_settings_http_and_study_compat(db, env):
    deps = CoreDeps(identity=FakeResolver(env["tokens"]), db=db)
    app = FastAPI()
    app.include_router(build_router(deps))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        r = await c.get("/core/workspace-settings", headers=auth("viewer"))
        body = r.json()
        assert r.status_code == 200
        assert body["timezone"] == "Asia/Seoul" and body["timezone_is_default"] is True
        assert body["tech_aliases"] == []
        assert {"term": "python", "aliases": ["파이썬"]} in body["builtin_aliases"]
        r = await c.put(
            "/core/workspace-settings", headers=auth("viewer"), json={"timezone": "UTC"}
        )
        assert r.status_code == 403
        r = await c.put(
            "/core/workspace-settings",
            headers=auth("editor"),
            json={"timezone": "Mars/Base"},
        )
        assert (r.status_code, r.json()["detail"]) == (422, "invalid_timezone")
        r = await c.put(
            "/core/workspace-settings",
            headers=auth("editor"),
            json={
                "tech_aliases": [{"term": "a", "aliases": ["x"]}, {"term": "b", "aliases": ["x"]}]
            },
        )
        assert (r.status_code, r.json()["detail"]) == (422, "alias_conflict")
        r = await c.put(
            "/core/workspace-settings",
            headers=auth("editor"),
            json={
                "timezone": "America/New_York",
                "tech_aliases": [{"term": "FastAPI", "aliases": [" 패스트API "]}],
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["tech_aliases"] == [{"term": "FastAPI", "aliases": ["패스트API"]}]
        # 빠진 칸은 그대로
        r = await c.put("/core/workspace-settings", headers=auth("editor"), json={})
        assert r.json()["timezone"] == "America/New_York" and r.json()["tech_aliases"]
        # 학습 설정 API는 같은 값을 읽고 쓴다
        r = await c.get("/core/study/settings", headers=auth("viewer"))
        assert r.json()["timezone"] == "America/New_York" and r.json()["is_default"] is False
        r = await c.put(
            "/core/study/settings", headers=auth("editor"), json={"timezone": "Europe/Berlin"}
        )
        assert r.status_code == 200
        r = await c.get("/core/workspace-settings", headers=auth("viewer"))
        assert r.json()["timezone"] == "Europe/Berlin"
        assert r.json()["tech_aliases"] == [{"term": "FastAPI", "aliases": ["패스트API"]}]
        # 기본값으로 되돌리기
        r = await c.put("/core/workspace-settings", headers=auth("editor"), json={"timezone": None})
        assert r.json()["timezone_is_default"] is True
        # 다른 공간은 영향 없음
        r = await c.get("/core/workspace-settings", headers=auth("other"))
        assert r.json()["tech_aliases"] == [] and r.json()["timezone_is_default"] is True
    view = await ws_uc.get_settings(db, env["owner"], now=T0)
    assert str(view.today) == "2026-11-17"  # 서울 23:30
    await ws_uc.update_settings(db, env["owner"], timezone="UTC", now=T0)
    assert (await study_uc.get_settings(db, env["owner"], now=T0)).timezone == "UTC"


async def test_workspace_aliases_used_by_frequency_and_document_checks(db, env):
    owner = env["owner"]
    base = {"title": "백엔드 (합성)", "company": "가상회사", "questions": QUESTIONS}
    await saved_posting(
        db, owner, "https://jobs.example.test/al/1", {**base, "requirements": ["FastAPI 경험"]}
    )
    pid = await saved_posting(
        db,
        owner,
        "https://jobs.example.test/al/2",
        {**base, "requirements": ["패스트API 개발 경험", "Kubernetes 운영 경험"]},
    )
    freq = await study_uc.requirement_frequency(db, owner)
    assert "fastapi" in {g.key for g in freq.groups}
    assert {g.key: g.postings for g in freq.groups}["fastapi"] == 1
    await ws_uc.update_settings(
        db,
        owner,
        tech_aliases=[
            {"term": "FastAPI", "aliases": ["패스트API"]},
            {"term": "kubernetes", "aliases": ["쿠베"]},
        ],
    )
    freq = await study_uc.requirement_frequency(db, owner)
    group = {g.key: g for g in freq.groups}["fastapi"]
    assert (group.postings, group.label) == (2, "FastAPI")
    # 다른 공간의 묶기는 기본 표 그대로
    assert (await study_uc.requirement_frequency(db, env["other"])).groups == []

    aid = await app_uc.create_application(db, owner, pid)
    [doc] = (await app_uc.get_application(db, owner, aid)).documents
    await app_uc.save_revision(
        db,
        owner,
        aid,
        doc.id,
        base_revision=0,
        operations=[{"op": "insert", "text": "쿠베 클러스터 운영을 맡았습니다."}],
    )
    result = await app_uc.validate_document(db, owner, aid, doc.id)
    rows = {r["requirement"]: r for r in result["requirements"]}
    assert rows["Kubernetes 운영 경험"]["document_terms"] == ["kubernetes"]


# ---------------------------------------------------------------- migration: study_settings → workspace_settings


def _alembic(conn):
    cfg = Config()
    cfg.set_main_option("script_location", str(CORE_CHAIN.script_location))
    cfg.attributes["connection"] = conn
    cfg.attributes["schema"] = CORE_CHAIN.schema
    return cfg


def test_c0021_moves_study_timezone_and_back(pg):
    mod = importlib.import_module("career_core.migrations.versions.c0021_study_search_judges")
    before = mod.down_revision
    admin = admin_url()
    name = f"career_test_{secrets.token_hex(4)}"
    eng = create_engine(admin, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    owner = admin.set(database=name)
    ws, actor = uuid4(), uuid4()
    try:
        upgrade(owner, revision=before)
        e = create_engine(owner, poolclass=NullPool)
        with e.begin() as conn:
            conn.execute(text("INSERT INTO core.workspaces (id, name) VALUES (:w, 'm')"), {"w": ws})
            conn.execute(
                text(
                    "INSERT INTO core.study_settings (id, workspace_id, timezone, updated_by) "
                    "VALUES (:i, :w, 'Europe/Paris', :a)"
                ),
                {"i": uuid4(), "w": ws, "a": actor},
            )
        upgrade(owner, revision="c0021")
        with e.begin() as conn:
            row = conn.execute(
                text("SELECT timezone, tech_aliases FROM core.workspace_settings")
            ).one()
            assert (row.timezone, row.tech_aliases) == ("Europe/Paris", [])
            assert conn.scalar(text("SELECT to_regclass('core.study_settings')")) is None
            forced = conn.scalar(
                text(
                    "SELECT relforcerowsecurity FROM pg_class "
                    "WHERE oid = 'core.workspace_settings'::regclass"
                )
            )
            assert forced is True
        with e.connect() as conn:
            command.downgrade(_alembic(conn), before)
            conn.commit()
        with e.begin() as conn:
            assert conn.scalar(text("SELECT timezone FROM core.study_settings")) == "Europe/Paris"
            assert conn.scalar(text("SELECT to_regclass('core.workspace_settings')")) is None
        e.dispose()
    finally:
        with eng.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        eng.dispose()


# ---------------------------------------------------------------- MCP search_brain 학습 기록 옵션


async def test_mcp_search_brain_study_records_are_opt_in_and_labelled(db, env):
    owner = env["owner"]
    exp = await brain.create_experience(db, owner, title="합성 경험")
    await brain.add_claim(db, owner, exp, statement="합성큐 컨슈머를 운영했다", kind="skill")
    item = await study_uc.create_item(db, owner, title="합성큐 공부")
    await study_uc.log_session(db, owner, item, minutes=30, notes="합성큐 파티션 개념 정리")
    deps = CoreDeps(identity=FakeResolver(env["tokens"]), db=db)
    app = FastAPI()
    app.include_router(build_router(deps))

    async def search(token, **args):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
            res = await c.post(
                "/core/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "search_brain", "arguments": {"query": "합성큐", **args}},
                },
                headers=auth(token),
            )
        assert res.status_code == 200, res.text
        return res.json()["result"]["structuredContent"]

    default = await search("viewer")
    assert {h["record"] for h in default["hits"]} == {"experience"}
    assert all("note" not in h for h in default["hits"])
    out = await search("viewer", include_study=True)
    assert out["include_study"] is True
    study = [h for h in out["hits"] if h["record"] == "study"]
    assert [h["kind"] for h in study] == ["study_note"]
    assert study[0]["note"] == "study, not experience"
    assert study[0]["study_title"] == "합성큐 공부"
    assert all(h.get("note") is None for h in out["hits"] if h["record"] != "study")
    # 학습 종류를 kinds로 직접 넘길 수는 없다 (옵션으로만)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        res = await c.post(
            "/core/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "search_brain",
                    "arguments": {"query": "합성큐", "kinds": ["study_note"]},
                },
            },
            headers=auth("viewer"),
        )
    assert res.json()["result"]["isError"] is True
    # 다른 공간에서는 0건
    assert (await search("other", include_study=True))["hits"] == []


# ---------------------------------------------------------------- 판정 모델 (가짜 ModelClient)

JUDGE_CONFIG = parse_models_config("""
[endpoints.proxy]
preset = "cliproxyapi"
data_classes = ["public", "private"]

[routes]
judge_claim = "proxy/judge-model"
judge_visibility = "proxy/judge-model"
""")


def _payload(request):
    body = request.messages[-1].content
    return json.loads(body[body.index("{") : body.rindex("}") + 1])


class ScriptedJudge:
    """요청 자료를 읽고 정해진 규칙(answer)으로 답하는 가짜 ModelClient. 요청을 모두 기록한다."""

    def __init__(self, answer):
        self.answer = answer
        self.requests = []

    async def complete(self, model, request):
        self.requests.append(request)
        out = self.answer(_payload(request))
        return ModelCompletion(
            text=json.dumps({"results": out}, ensure_ascii=False),
            model=model,
            stop_reason="end",
            parts=(UsagePart(model, TokenUsage(input_tokens=300, output_tokens=60)),),
        )


def judge_gateway(db, client, config=JUDGE_CONFIG):
    return ModelGateway(
        db,
        config,
        PriceBook.load(),
        client_factory=lambda ep, key: client,
        env={"CLIPROXY_API_KEY": "k"},
    )


def docker_rule(payload):
    if "candidates" in payload:
        return [
            {
                "id": c["id"],
                "verdict": "yes" if "Docker" in c["statement"] else "no",
                "reason": "합성 이유",
            }
            for c in payload["candidates"]
        ]
    doc = payload["document"]
    out = []
    for r in payload["requirements"]:
        if "Docker" in r["text"]:
            out.append({"id": r["id"], "verdict": "yes", "quote": "컨테이너 이미지를 줄였습니다"})
        elif "Kafka" in r["text"]:
            # 문서에 없는 인용을 지어낸다 → 코드가 unclear로 내린다
            out.append({"id": r["id"], "verdict": "yes", "quote": "카프카를 운영했습니다"})
        else:
            out.append({"id": r["id"], "verdict": "no", "reason": "보이지 않음"})
    assert "컨테이너" in doc
    return out


async def restricted_claim(db, tmp_path, actor, exp, statement):
    blobs = LocalBlobStore(tmp_path)
    await upload_source(
        db,
        blobs,
        actor,
        filename="secret.txt",
        data=statement.encode(),
        declared_mime="text/plain",
        max_bytes=1 << 20,
        data_class="restricted",
        title="합성 기밀",
    )
    deps = ParsingDeps(
        db=db, blobs=blobs, egress=ProviderEgressPolicy(), local_parsers=default_local_parsers()
    )
    await run_until_idle(db, parsing_handlers(deps))
    async with db.workspace_session(actor.workspace_id) as s:
        block = await s.scalar(select(DocumentBlock).where(DocumentBlock.text == statement))
    span = await brain.create_span(db, actor, block.id, 0, len(statement))
    return await brain.add_claim(db, actor, exp, statement=statement, kind="skill", span_ids=[span])


async def test_claim_judge_is_advisory_and_never_sends_restricted(db, env, tmp_path):
    owner = env["owner"]
    fields = {
        "title": "백엔드 (합성)",
        "company": "가상회사",
        "requirements": ["Docker 사용 경험"],
    }
    pid = await saved_posting(db, owner, "https://jobs.example.test/j/1", fields)
    exp = await brain.create_experience(db, owner, title="가상 배포 개선")
    normal = await brain.add_claim(
        db, owner, exp, statement="Docker로 합성 서비스를 컨테이너화했다", kind="skill"
    )
    secret = await restricted_claim(db, tmp_path, owner, exp, "Docker 비밀 프로젝트 합성")

    # 라우트가 없으면 검색 제안만 (지금 기준선)
    res = await study_uc.suggest_claims(db, owner, pid, judge=True)
    assert res.judge.status == "unavailable" and res.judge.note == "no_route"
    assert all(c.judge is None for ln in res.lines for c in ln.candidates)

    client = ScriptedJudge(docker_rule)
    gw = judge_gateway(db, client)
    # egress 범위가 없으면 부르지 않는다
    res = await study_uc.suggest_claims(db, env["viewer"], pid, gateway=gw, judge=True)
    assert res.judge.note == "egress_scope_required" and client.requests == []
    # judge를 묻지 않으면 부르지 않는다
    res = await study_uc.suggest_claims(db, owner, pid, gateway=gw)
    assert res.judge is None and client.requests == []

    res = await study_uc.suggest_claims(db, owner, pid, gateway=gw, judge=True)
    assert res.judge.status == "ok" and res.judge.calls == 1
    assert res.judge.basis == "judge_advisory" and res.basis == "search_suggestion"
    by_id = {c.claim_id: c for c in res.lines[0].candidates}
    assert set(by_id) == {normal, secret}
    assert by_id[normal].judge.verdict == "yes" and by_id[normal].judge.reason == "합성 이유"
    assert by_id[secret].judge is None and by_id[secret].judge_note == "restricted_not_sent"
    [req] = client.requests
    assert "비밀 프로젝트" not in req.messages[-1].content
    assert "restricted" not in req.data_classes and "private" in req.data_classes
    # 참고 의견일 뿐: 연결·충족이 바뀌지 않는다
    async with db.workspace_session(owner.workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(StudyRequirementClaim)) == 0
        calls = (await s.execute(select(ModelCall.purpose, ModelCall.status))).all()
    assert calls == [("judge_claim", "ok")]
    cov = await study_uc.posting_coverage(db, owner, pid)
    assert [r.coverage for r in cov.requirements] == ["uncovered"]


async def test_visibility_judge_quotes_are_checked_and_advisory(db, env, tmp_path):
    owner = env["owner"]
    fields = {
        "title": "백엔드 (합성)",
        "company": "가상회사",
        "requirements": ["Docker 사용 경험", "Kafka 운영 경험", "팀 리딩 경험"],
        "questions": QUESTIONS,
    }
    pid = await saved_posting(db, owner, "https://jobs.example.test/j/2", fields)
    aid = await app_uc.create_application(db, owner, pid)
    [doc] = (await app_uc.get_application(db, owner, aid)).documents
    exp = await brain.create_experience(db, owner, title="가상 배포")
    secret = await restricted_claim(db, tmp_path, owner, exp, "기밀 합성 문단")
    async with db.workspace_session(owner.workspace_id) as s:
        c = await s.get(Claim, secret)
        c.status, c.contribution = "self_confirmed", "personal"
        c.confirmed_by, c.confirmed_at = owner.subject_id, T0
    await app_uc.save_revision(
        db,
        owner,
        aid,
        doc.id,
        base_revision=0,
        operations=[
            {"op": "insert", "text": "컨테이너 이미지를 줄였습니다. 빌드가 빨라졌습니다."},
            {"op": "insert", "text": "기밀 합성 문단", "claim_ids": [str(secret)]},
        ],
    )
    before = await app_uc.validate_document(db, owner, aid, doc.id)

    res = await judges.judge_application_visibility(db, None, owner, aid)
    assert res.judge.status == "unavailable" and all(r.judge is None for r in res.rows)
    assert [r.baseline_verdict for r in res.rows] == ["unmet", "unmet", "unmet"]

    client = ScriptedJudge(docker_rule)
    gw = judge_gateway(db, client)
    res = await judges.judge_application_visibility(db, gw, owner, aid, document_id=doc.id)
    assert res.judge.status == "ok" and res.blocks_withheld == 1
    rows = {r.requirement: r for r in res.rows}
    docker = rows["Docker 사용 경험"]
    assert (docker.judge.verdict, docker.judge.quote) == ("yes", "컨테이너 이미지를 줄였습니다")
    assert docker.baseline_verdict == "unmet" and docker.agrees is False  # 기준선은 못 봄
    kafka = rows["Kafka 운영 경험"]
    assert (kafka.judge.verdict, kafka.judge.note) == ("unclear", "quote_not_found")
    assert kafka.agrees is None
    assert rows["팀 리딩 경험"].judge.verdict == "no" and rows["팀 리딩 경험"].agrees is True
    [req] = client.requests
    assert "기밀 합성 문단" not in req.messages[-1].content  # restricted 근거 문단은 보내지 않음
    assert req.data_classes == frozenset({"private"})
    # 문서 검사 결과는 그대로 (저장·자동 충족 없음)
    assert await app_uc.validate_document(db, owner, aid, doc.id) == before
    with pytest.raises(app_uc.NotFound):
        await judges.judge_application_visibility(db, gw, env["other"], aid)

    deps = CoreDeps(identity=FakeResolver(env["tokens"]), db=db, models=gw)
    app = FastAPI()
    app.include_router(build_router(deps))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        r = await c.post(f"/core/applications/{aid}/visibility-judge", headers=auth("editor"))
        assert r.status_code == 403  # 문서를 밖으로 보내므로 egress 범위
        r = await c.post(f"/core/applications/{aid}/visibility-judge", headers=auth("owner"))
        assert r.status_code == 200, r.text
        body = r.json()
        # 위에서 같은 요건·같은 글을 이미 물었다: 응답 캐시(c0022)에서 꺼내고 다시 부르지 않는다
        assert body["basis"] == "judge_advisory"
        assert (body["judge"]["calls"], body["judge"]["cached"]) == (0, 1)
        assert len(client.requests) == 1
        r = await c.get(
            f"/core/study/postings/{pid}/claim-suggestions",
            params={"judge": "true"},
            headers=auth("owner"),
        )
        assert r.status_code == 200 and r.json()["judge"]["status"] in ("ok", "partial")


# ---------------------------------------------------------------- 평가 스크립트


def _gold(fx):
    claims = {c["claim"]: c["label"] for c in fx["claims"]}
    vis = {(d["text"], r["text"]): r["label"] for d in fx["documents"] for r in d["requirements"]}

    def answer(payload):
        if "candidates" in payload:
            return [
                {"id": c["id"], "verdict": claims[c["statement"]], "reason": "정답"}
                for c in payload["candidates"]
            ]
        doc = payload["document"]
        out = []
        for r in payload["requirements"]:
            label = vis[(doc, r["text"])]
            # 정답이 yes면 문서의 첫 문장을 인용 (문서에 실제로 있는 글)
            quote = re.split(r"(?<=[.:])\s", doc)[0] if label == "yes" else None
            out.append({"id": r["id"], "verdict": label, "quote": quote})
        return out

    return answer


async def test_judge_eval_with_scripted_client(db, env):
    fx = judge_eval.load_fixture()
    # 합성 100건: 주장 64 + 가시성 36 (c0022에서 40건 → 100건, 실제 공고·이력서 말투)
    assert len(fx["claims"]) == 64 and sum(len(d["requirements"]) for d in fx["documents"]) == 36
    report = await judge_eval.evaluate(fx, None)
    base = report.scores
    assert (base["claim"]["baseline"].correct, base["visibility"]["baseline"].correct) == (31, 21)
    assert "judge" not in base["claim"] and report.calls == 0

    client = ScriptedJudge(_gold(fx))
    gw = judge_gateway(db, client)
    report = await judge_eval.evaluate(fx, judges.gateway_caller(gw, env["owner"]))
    assert report.scores["claim"]["judge"].correct == 64
    assert report.scores["visibility"]["judge"].correct == 36
    assert report.calls == len(client.requests) == 43 + 9  # 같은 요건 문장은 한 번에
    assert all(r.data_classes == frozenset({"public"}) for r in client.requests)
    assert any("format" in line or "accuracy" in line for line in judge_eval.format_report(report))

    # 인용 없이 모두 yes라고 답하는 판정: 가시성은 전부 unclear로 내려간다
    lazy = ScriptedJudge(
        lambda p: [
            {"id": x["id"], "verdict": "yes"} for x in p.get("candidates") or p["requirements"]
        ]
    )
    report = await judge_eval.evaluate(
        fx, judges.gateway_caller(judge_gateway(db, lazy), env["owner"])
    )
    vis = report.scores["visibility"]["judge"]
    assert vis.unclear == 36 and vis.correct == 17  # 정답이 no인 17개만 맞음
    assert report.scores["claim"]["judge"].correct == 38  # 정답이 yes인 38개

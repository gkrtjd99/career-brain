"""O08 완료 기준: 재시작 후 재개, 승인 변경 무효화, 사람만 승인, 공간 격리, 한도 초과 시 대기·실패.

고정 회귀 시험 1(run ID), 10, 14. 모든 자료는 합성이다.
"""

import asyncio
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from career_core.db import Job, JobPosting, JobPostingVersion, ModelCall
from career_core.db.base import utcnow
from career_core.db.models import Application, ApplicationSubmission, Approval, Run, RunEvent
from career_core.domain.runs import ApprovalError, RunRuleError, content_hash
from career_core.domain.workspace import Role, ScopePermissionPolicy, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import run_handlers, run_once, run_until_idle
from career_core.llm import (
    ChatMessage,
    ModelCallError,
    ModelCompletion,
    ModelRequest,
    TokenUsage,
    UsagePart,
)
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.ports import ACTOR_AGENT, ActorContext
from career_core.testing import workspace_isolation_problems
from career_core.usecases import applications as app_uc
from career_core.usecases import approvals as approval_uc
from career_core.usecases import postings as posting_uc
from career_core.usecases import runs as run_uc
from career_core.usecases.approvals import summary_changes
from career_core.usecases.brain import NotFound
from career_core.usecases.runs import RunDeps, StepContext, StepResult, Workflow
from career_core.usecases.workspaces import create_workspace
from career_core.workflows.builtin import WORKFLOWS
from fastapi import FastAPI
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.anyio

QUESTION = {"prompt": "지원 동기를 작성해 주세요. (500자 이내)", "char_limit": 500}


class FakeResolver:
    def __init__(self, tokens: dict[str, ActorContext]):
        self.tokens = tokens

    async def resolve(self, credential: str) -> ActorContext | None:
        return self.tokens.get(credential)


async def saved_posting(db, actor, url: str) -> object:
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
    return aid, doc.id


@pytest.fixture
async def env(db):
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    owner = ActorContext(a, ws_a, scopes_for_role(Role.OWNER))
    return {
        "owner": owner,
        "editor": ActorContext(a, ws_a, scopes_for_role(Role.EDITOR)),
        "viewer": ActorContext(a, ws_a, scopes_for_role(Role.VIEWER)),
        # 같은 사람의 owner 토큰을 AI 클라이언트가 쓰는 경우
        "agent": ActorContext(a, ws_a, scopes_for_role(Role.OWNER), kind=ACTOR_AGENT),
        "other": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
    }


async def events(db, actor, run_id):
    return [(e.kind, e.code) for e in await run_uc.run_events(db, actor, run_id)]


# ---------------------------------------------------------------- 재시작 후 재개


async def test_run_resumes_after_simulated_restart_without_duplicate_effects(db, env):
    owner = env["owner"]
    external: dict[str, int] = {}  # 멱등 키를 받는 바깥 시스템 흉내
    calls: list[str] = []
    entered = asyncio.Event()
    first_attempt = True

    async def prepare(ctx: StepContext) -> StepResult:
        calls.append("prepare")
        return StepResult(position={"prepared": True})

    async def send(ctx: StepContext) -> StepResult:
        nonlocal first_attempt
        calls.append("send")
        external.setdefault(ctx.effect_key("send"), 0)
        external[ctx.effect_key("send")] = 1  # 같은 키면 덮어쓴다 (멱등)
        if first_attempt:
            first_attempt = False
            entered.set()
            await asyncio.Event().wait()  # 여기서 worker가 죽는다
        return StepResult(position={"sent": True})

    async def finish(ctx: StepContext) -> StepResult:
        calls.append("finish")
        assert ctx.position == {"prepared": True, "sent": True}
        return StepResult(result={"ok": True})

    wf = Workflow("synthetic", "jobs.write", (prepare, send, finish), lambda raw: raw)
    deps = RunDeps(db=db, workflows={"synthetic": wf})
    handlers = run_handlers(deps)
    view = await run_uc.start_run(deps, owner, "synthetic", {"n": 1})
    assert (view.status, view.step, view.steps_total) == ("queued", 0, 3)

    worker = asyncio.create_task(run_once(db, handlers))
    await asyncio.wait_for(entered.wait(), 10)
    worker.cancel()  # 프로세스 종료 흉내: 정리 코드 없이 끊긴다
    with pytest.raises(asyncio.CancelledError):
        await worker

    mid = await run_uc.get_run(db, owner, view.id)
    assert (mid.status, mid.step, mid.position) == ("running", 1, {"prepared": True})
    # lease가 지나기 전에는 다른 worker가 집지 않는다
    assert await run_until_idle(db, handlers) == 0
    async with db.workspace_session(owner.workspace_id) as s:
        await s.execute(
            update(Job)
            .where(Job.target_id == view.id)
            .values(locked_until=utcnow() - timedelta(seconds=1))
        )
    assert await run_until_idle(db, handlers) == 1

    done = await run_uc.get_run(db, owner, view.id)
    assert (done.status, done.result, done.resumes) == ("done", {"ok": True}, 1)
    assert calls == ["prepare", "send", "send", "finish"]  # 끝난 단계는 다시 하지 않는다
    assert external == {f"run:{view.id}:send": 1}
    kinds = await events(db, owner, view.id)
    assert ("resumed", None) in kinds and kinds[-1] == ("status", None)
    assert done.finished_at is not None


async def test_save_job_urls_run_is_idempotent_and_resumable(db, env):
    owner = env["owner"]
    deps = RunDeps(db=db)
    urls = ["https://jobs.example.test/r/1", "https://jobs.example.test/r/2?utm_source=x"]
    view = await run_uc.start_run(
        deps, owner, "save_job_urls", {"urls": urls}, idempotency_key="k-1"
    )
    again = await run_uc.start_run(
        deps, owner, "save_job_urls", {"urls": urls}, idempotency_key="k-1"
    )
    assert again.id == view.id
    with pytest.raises(RunRuleError, match="idempotency_key_reused"):
        await run_uc.start_run(
            deps, owner, "save_job_urls", {"urls": urls[:1]}, idempotency_key="k-1"
        )
    # 이미 저장된 공고가 있어도 다시 만들지 않는다
    await posting_uc.save_urls(db, owner, [urls[0]])
    await run_until_idle(db, run_handlers(deps))
    done = await run_uc.get_run(db, owner, view.id)
    assert done.status == "done"
    saved = done.result["saved"]
    assert [r["deduplicated"] for r in saved] == [True, False]
    async with db.workspace_session(owner.workspace_id) as s:
        n = await s.scalar(select(func.count()).select_from(JobPosting))
    assert n == 2
    # 같은 위치에서 다시 돌려도(재개 흉내) 공고는 늘지 않는다
    async with db.workspace_session(owner.workspace_id) as s:
        await s.execute(
            update(Run)
            .where(Run.id == view.id)
            .values(status="queued", step=0, position={}, finished_at=None)
        )
        s.add(Job(workspace_id=owner.workspace_id, kind="run", target_id=view.id))
    await run_until_idle(db, run_handlers(deps))
    async with db.workspace_session(owner.workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(JobPosting)) == 2


async def test_step_errors_retry_then_fail_cleanly(db, env):
    owner = env["owner"]
    attempts = 0

    async def broken(ctx):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("synthetic")

    wf = Workflow("broken", "jobs.write", (broken,), lambda raw: raw)
    deps = RunDeps(db=db, workflows={"broken": wf})
    view = await run_uc.start_run(deps, owner, "broken", {})
    handlers = run_handlers(deps)
    assert await run_once(db, handlers)
    mid = await run_uc.get_run(db, owner, view.id)
    assert mid.status == "running"  # 재시도 대기 (같은 위치부터)
    async with db.workspace_session(owner.workspace_id) as s:
        await s.execute(
            update(Job).where(Job.target_id == view.id).values(attempts=10, available_at=utcnow())
        )
    assert await run_once(db, handlers)
    failed = await run_uc.get_run(db, owner, view.id)
    assert (failed.status, failed.error_code, attempts) == ("failed", "step_error", 2)
    assert ("error", "RuntimeError") in await events(db, owner, view.id)


async def test_revoked_member_run_fails(db, env):
    owner = env["owner"]

    async def step(ctx):
        return StepResult()

    wf = Workflow("one", "jobs.write", (step,), lambda raw: raw)
    deps = RunDeps(db=db, workflows={"one": wf})
    view = await run_uc.start_run(deps, owner, "one", {})
    async with db.workspace_session(owner.workspace_id) as s:
        await s.execute(
            text("DELETE FROM core.memberships WHERE actor_id = :a"), {"a": owner.subject_id}
        )
    await run_until_idle(db, run_handlers(deps))
    async with db.workspace_session(owner.workspace_id) as s:
        run = await s.get(Run, view.id)
        assert (run.status, run.error_code) == ("failed", "actor_revoked")


# ---------------------------------------------------------------- 취소·중단


async def test_cancel_interrupt_resume_between_steps(db, env):
    owner = env["owner"]
    seen: list[int] = []
    gate = asyncio.Event()
    entered = asyncio.Event()

    async def slow(ctx):
        seen.append(ctx.step)
        if ctx.step == 0:
            entered.set()
            await gate.wait()
        return StepResult(position={f"s{ctx.step}": True})

    wf = Workflow("slow", "jobs.write", (slow, slow, slow), lambda raw: raw)
    deps = RunDeps(db=db, workflows={"slow": wf})
    handlers = run_handlers(deps)

    view = await run_uc.start_run(deps, owner, "slow", {})
    task = asyncio.create_task(run_once(db, handlers))
    await asyncio.wait_for(entered.wait(), 10)
    r = await run_uc.interrupt_run(db, owner, view.id)
    assert (r.status, r.stop_requested) == ("running", "interrupt")
    gate.set()
    await task
    r = await run_uc.get_run(db, owner, view.id)
    assert (r.status, r.step) == ("interrupted", 1)  # 단계는 끝까지, 다음 단계 전에 멈춤
    assert seen == [0]
    with pytest.raises(RunRuleError, match="run_not_active"):
        await run_uc.interrupt_run(db, owner, view.id)
    await run_uc.resume_run(db, owner, view.id)
    await run_until_idle(db, handlers)
    r = await run_uc.get_run(db, owner, view.id)
    assert (r.status, seen) == ("done", [0, 1, 2])
    with pytest.raises(RunRuleError, match="run_finished"):
        await run_uc.cancel_run(db, owner, view.id)

    queued = await run_uc.start_run(deps, owner, "slow", {})
    cancelled = await run_uc.cancel_run(db, owner, queued.id)
    assert (cancelled.status, cancelled.finished_at is not None) == ("cancelled", True)
    await run_until_idle(db, handlers)
    assert (await run_uc.get_run(db, owner, queued.id)).status == "cancelled"


# ---------------------------------------------------------------- 외부 한도 (§17.14)


CONFIG = parse_models_config("""
[endpoints.claude]
preset = "anthropic"
data_classes = ["public", "private"]

[endpoints.backup]
preset = "openai"
data_classes = ["public", "private"]

[routes]
default = "claude/claude-opus-5-5"
other = "backup/gpt-6.1-sol"
""")
ENV = {"ANTHROPIC_API_KEY": "k", "OPENAI_API_KEY": "k"}


class FlakyClient:
    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    async def complete(self, model, request):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return ModelCompletion(
            text="ok",
            model=model,
            stop_reason="end_turn",
            parts=(UsagePart(model, TokenUsage(input_tokens=1000, output_tokens=100)),),
        )


def model_workflow():
    async def ask(ctx: StepContext) -> StepResult:
        req = ModelRequest(
            messages=(ChatMessage("user", "합성 질문"),), data_classes=frozenset({"private"})
        )
        res = await ctx.model("default", req)
        return StepResult(result={"text": res.text})

    return Workflow("ask", "jobs.write", (ask,), lambda raw: raw)


async def test_rate_limit_waits_on_same_endpoint_then_fails_without_paid_fallback(db, env):
    owner = env["owner"]
    client = FlakyClient([ModelCallError("rate_limited", retryable=True)] * 5)
    asked: list[str] = []

    def factory(endpoint, key):
        asked.append(endpoint.name)
        return client

    gateway = ModelGateway(db, CONFIG, PriceBook.load(), client_factory=factory, env=ENV)
    deps = RunDeps(
        db=db,
        gateway=gateway,
        workflows={"ask": model_workflow()},
        wait_base_seconds=0,
        max_waits=2,
    )
    view = await run_uc.start_run(deps, owner, "ask", {})
    handlers = run_handlers(deps)
    assert await run_once(db, handlers)
    waiting = await run_uc.get_run(db, owner, view.id)
    # 기다리는 동안 사유가 보인다
    assert (waiting.status, waiting.wait_reason) == ("queued", "rate_limited")
    assert waiting.retry_at is not None
    await run_until_idle(db, handlers)
    failed = await run_uc.get_run(db, owner, view.id)
    assert (failed.status, failed.error_code) == ("failed", "rate_limited")
    assert client.calls == 3 and set(asked) == {"claude"}  # 다른 제공자로 바꾸지 않았다
    assert [k for k in await events(db, owner, view.id) if k[0] == "wait"] == [
        ("wait", "rate_limited"),
        ("wait", "rate_limited"),
    ]
    async with db.workspace_session(owner.workspace_id) as s:
        ledger = (await s.execute(select(ModelCall.endpoint, ModelCall.error_code))).all()
    assert set(ledger) == {("claude", "rate_limited")}
    assert failed.model_calls == 0  # 성공한 호출이 없다


async def test_quota_exceeded_fails_with_reason_and_success_records_cost(db, env):
    owner = env["owner"]
    client = FlakyClient([ModelCallError("quota_exceeded")])
    gateway = ModelGateway(
        db, CONFIG, PriceBook.load(), client_factory=lambda ep, key: client, env=ENV
    )
    deps = RunDeps(db=db, gateway=gateway, workflows={"ask": model_workflow()}, wait_base_seconds=0)
    handlers = run_handlers(deps)
    first = await run_uc.start_run(deps, owner, "ask", {})
    await run_until_idle(db, handlers)
    r = await run_uc.get_run(db, owner, first.id)
    assert (r.status, r.error_code, r.wait_reason) == ("failed", "quota_exceeded", None)
    # 한도가 풀린 뒤의 새 실행은 비용을 남긴다
    second = await run_uc.start_run(deps, owner, "ask", {})
    await run_until_idle(db, handlers)
    r = await run_uc.get_run(db, owner, second.id)
    assert (r.status, r.model_calls, r.result) == ("done", 1, {"text": "ok"})
    assert Decimal(r.cost_usd) > 0 and not r.cost_unknown


def test_openai_insufficient_quota_is_not_retryable():
    import openai
    from career_core.adapters.llm_openai import _call_error

    req = httpx.Request("POST", "https://api.example.invalid/v1/chat/completions")
    body = {"error": {"code": "insufficient_quota", "message": "synthetic"}}
    exc = openai.RateLimitError(
        "quota", response=httpx.Response(429, request=req, json=body), body=body["error"]
    )
    err = _call_error(openai, exc)
    assert (err.code, err.retryable) == ("quota_exceeded", False)
    plain = openai.RateLimitError("slow down", response=httpx.Response(429, request=req), body=None)
    assert (_call_error(openai, plain).code, _call_error(openai, plain).retryable) == (
        "rate_limited",
        True,
    )


# ---------------------------------------------------------------- 승인


def payload(doc_id, revision, note=None):
    return {"revisions": {str(doc_id): revision}, "note": note}


async def test_approval_binds_target_and_payload_and_is_single_use(db, env):
    owner, editor = env["owner"], env["editor"]
    aid, did = await draft_application(db, owner, "https://jobs.example.test/ap/1")
    req = await approval_uc.request_approval(
        db, editor, action="applications.submit", target_id=aid, payload=payload(did, 1, "메모")
    )
    assert (req.status, req.requested_via, req.target_kind) == ("pending", "human", "application")
    assert req.payload_hash == content_hash({"revisions": {str(did): 1}, "note": "메모"})
    assert req.summary["documents"][0]["revision"] == 1
    # 같은 승인은 내용 해시로 확인한다 (승인 화면이 본 것과 다르면 거부)
    with pytest.raises(ApprovalError, match="payload_mismatch"):
        await approval_uc.decide_approval(db, owner, req.id, approve=True, payload_hash="0" * 64)
    ok = await approval_uc.decide_approval(
        db, owner, req.id, approve=True, payload_hash=req.payload_hash
    )
    assert (ok.status, ok.decided_by) == ("approved", owner.subject_id)
    # 편집자는 직접 제출할 수 없지만 승인 묶음으로는 제출할 수 있다
    from career_core.domain.documents import DocumentRuleError

    with pytest.raises(DocumentRuleError, match="submit_requires_approve"):
        await app_uc.submit_application(db, editor, aid, revisions={did: 1}, note="메모")
    sub = await app_uc.submit_application(
        db, editor, aid, revisions={did: 1}, note="메모", approval_id=req.id
    )
    used = await approval_uc.get_approval(db, owner, req.id)
    assert (used.status, used.used_result_id) == ("used", sub.id)
    with pytest.raises(ApprovalError, match="approval_used"):
        async with db.workspace_session(owner.workspace_id) as s:
            await approval_uc.consume_approval(
                s,
                editor,
                req.id,
                action="applications.submit",
                target_id=aid,
                payload=payload(did, 1, "메모"),
            )


async def test_change_after_approval_invalidates_it(db, env):
    """§17.10: 승인 후 문서·대상이 바뀌면 기존 승인을 쓸 수 없다."""
    owner, editor = env["owner"], env["editor"]
    aid, did = await draft_application(db, owner, "https://jobs.example.test/ap/2")
    req = await approval_uc.request_approval(
        db, editor, action="applications.submit", target_id=aid, payload=payload(did, 1)
    )
    await approval_uc.decide_approval(db, owner, req.id, approve=True)
    doc = await app_uc.get_document(db, owner, aid, did)
    await app_uc.save_revision(
        db,
        editor,
        aid,
        did,
        base_revision=1,
        operations=[{"op": "replace", "block_id": doc.blocks[0].id, "text": "고친 문단입니다."}],
    )
    # 문서를 저장한 그 트랜잭션에서 승인이 바로 닫힌다 (c0014 trigger, 조회 전에도)
    async with db.workspace_session(owner.workspace_id) as s:
        row = await s.get(Approval, req.id)
        assert (row.status, row.invalid_reason) == ("invalidated", "target_changed")
    # 승인한 내용 그대로 보내도 쓸 수 없다
    with pytest.raises(ApprovalError, match="approval_invalidated"):
        await app_uc.submit_application(db, editor, aid, revisions={did: 1}, approval_id=req.id)
    stale = await approval_uc.get_approval(db, owner, req.id)
    assert (stale.status, stale.invalid_reason) == ("invalidated", "target_changed")
    with pytest.raises(ApprovalError, match="approval_invalidated"):
        await app_uc.submit_application(db, editor, aid, revisions={did: 2}, approval_id=req.id)
    async with db.workspace_session(owner.workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(ApplicationSubmission)) == 0

    # 내용(메모)을 바꿔 쓰려 해도 무효
    req2 = await approval_uc.request_approval(
        db, editor, action="applications.submit", target_id=aid, payload=payload(did, 2)
    )
    await approval_uc.decide_approval(db, owner, req2.id, approve=True)
    with pytest.raises(ApprovalError, match="payload_changed"):
        await app_uc.submit_application(
            db, editor, aid, revisions={did: 2}, note="다른 메모", approval_id=req2.id
        )
    assert (await approval_uc.get_approval(db, owner, req2.id)).status == "invalidated"

    # 결정 전에 대상이 바뀌어도 승인할 수 없다
    req3 = await approval_uc.request_approval(
        db, editor, action="applications.submit", target_id=aid, payload=payload(did, 2)
    )
    await app_uc.update_document(db, owner, aid, did, {"char_limit": 400})
    assert (await approval_uc.get_approval(db, owner, req3.id)).status == "invalidated"
    with pytest.raises(ApprovalError, match="approval_not_pending"):
        await approval_uc.decide_approval(db, owner, req3.id, approve=True)

    # 만료
    req4 = await approval_uc.request_approval(
        db,
        editor,
        action="applications.submit",
        target_id=aid,
        payload=payload(did, 2),
        expires_in=timedelta(minutes=1),
    )
    with pytest.raises(ApprovalError, match="approval_expired"):
        await approval_uc.decide_approval(
            db, owner, req4.id, approve=True, now=utcnow() + timedelta(minutes=2)
        )
    assert (await approval_uc.get_approval(db, owner, req4.id)).status == "expired"


async def test_viewer_and_ai_actor_cannot_approve(db, env):
    owner, viewer, agent = env["owner"], env["viewer"], env["agent"]
    aid, did = await draft_application(db, owner, "https://jobs.example.test/ap/3")
    with pytest.raises(ApprovalError, match="forbidden"):
        await approval_uc.request_approval(
            db, viewer, action="applications.submit", target_id=aid, payload=payload(did, 1)
        )
    # AI는 승인을 요청할 수 있다
    req = await approval_uc.request_approval(
        db, agent, action="applications.submit", target_id=aid, payload=payload(did, 1)
    )
    assert req.requested_via == "agent"
    for actor in (viewer, agent, env["editor"]):
        with pytest.raises(ApprovalError, match="human_approval_required"):
            await approval_uc.decide_approval(db, actor, req.id, approve=True)
    # owner 범위를 가진 agent도 approve 범위를 쓸 수 없다 (주장 확정·직접 제출 포함)
    policy = ScopePermissionPolicy()
    assert agent.has_scope("draft-write") and not agent.has_scope("approve")
    for action in ("approvals.decide", "claims.confirm", "applications.submit"):
        assert not policy.allows(agent, action)
        assert policy.allows(owner, action)
    from career_core.domain.documents import DocumentRuleError

    with pytest.raises(DocumentRuleError, match="submit_requires_approve"):
        await app_uc.submit_application(db, agent, aid, revisions={did: 1})
    # DB도 사람 결정이 아닌 승인 상태를 받지 않는다
    with pytest.raises(IntegrityError):
        async with db.workspace_session(owner.workspace_id) as s:
            await s.execute(
                update(Approval)
                .where(Approval.id == req.id)
                .values(
                    status="approved",
                    decided_by=agent.subject_id,
                    decided_at=utcnow(),
                    decided_via="agent",
                )
            )
    assert (await approval_uc.get_approval(db, owner, req.id)).status == "pending"


async def test_approval_binding_is_immutable_and_events_append_only(db, env):
    owner = env["owner"]
    aid, did = await draft_application(db, owner, "https://jobs.example.test/ap/4")
    req = await approval_uc.request_approval(
        db, owner, action="applications.submit", target_id=aid, payload=payload(did, 1)
    )
    for values in (
        {"payload_hash": "a" * 64},
        {"target_version": "x"},
        {"expires_at": utcnow() + timedelta(days=30)},
    ):
        with pytest.raises(DBAPIError, match="immutable"):
            async with db.workspace_session(owner.workspace_id) as s:
                await s.execute(update(Approval).where(Approval.id == req.id).values(**values))
    await approval_uc.decide_approval(db, owner, req.id, approve=False, note="아직")
    with pytest.raises(DBAPIError, match="closed"):
        async with db.workspace_session(owner.workspace_id) as s:
            await s.execute(update(Approval).where(Approval.id == req.id).values(status="pending"))
    deps = RunDeps(db=db)
    run = await run_uc.start_run(deps, owner, "save_job_urls", {"urls": ["https://e.test/1"]})
    for stmt in (
        update(RunEvent).values(code="x"),
        text("DELETE FROM core.run_events"),
        text("DELETE FROM core.runs"),
    ):
        with pytest.raises(ProgrammingError):
            async with db.workspace_session(owner.workspace_id) as s:
                await s.execute(stmt)
    await run_uc.cancel_run(db, owner, run.id)


# ---------------------------------------------------------------- 실행 + 승인 + 제출


async def test_submit_run_waits_for_human_approval_then_submits(db, env):
    owner, agent = env["owner"], env["agent"]
    aid, did = await draft_application(db, owner, "https://jobs.example.test/ap/5")
    deps = RunDeps(db=db)
    handlers = run_handlers(deps)
    run = await run_uc.start_run(
        deps,
        agent,
        "submit_application",
        {"application_id": str(aid), "revisions": {str(did): 1}, "note": "합성 메모"},
    )
    await run_until_idle(db, handlers)
    waiting = await run_uc.get_run(db, owner, run.id)
    assert waiting.status == "waiting_approval" and waiting.approval_id is not None
    approval = await approval_uc.get_approval(db, owner, waiting.approval_id)
    assert (approval.status, approval.requested_via, approval.run_id) == (
        "pending",
        "agent",
        run.id,
    )
    # 승인 전에는 아무리 돌려도 제출되지 않는다
    assert await run_until_idle(db, handlers) == 0
    with pytest.raises(ApprovalError, match="human_approval_required"):
        await approval_uc.decide_approval(db, agent, approval.id, approve=True)
    await approval_uc.decide_approval(db, owner, approval.id, approve=True)
    await run_until_idle(db, handlers)
    done = await run_uc.get_run(db, owner, run.id)
    assert done.status == "done"
    sub = await app_uc.get_submission(db, owner, aid)
    assert done.result == {"submission_id": str(sub.id)} and sub.note == "합성 메모"
    assert (await approval_uc.get_approval(db, owner, approval.id)).status == "used"


async def test_submit_run_fails_when_target_changes_or_rejected(db, env):
    owner, editor = env["owner"], env["editor"]
    deps = RunDeps(db=db)
    handlers = run_handlers(deps)
    aid, did = await draft_application(db, owner, "https://jobs.example.test/ap/6")
    run = await run_uc.start_run(
        deps, editor, "submit_application", {"application_id": str(aid), "revisions": {str(did): 1}}
    )
    await run_until_idle(db, handlers)
    w = await run_uc.get_run(db, owner, run.id)
    await approval_uc.decide_approval(db, owner, w.approval_id, approve=True)
    await app_uc.update_document(db, owner, aid, did, {"title": "고친 제목"})  # 제목은 대상 버전 밖
    doc = await app_uc.get_document(db, owner, aid, did)
    await app_uc.save_revision(
        db,
        owner,
        aid,
        did,
        base_revision=1,
        operations=[{"op": "replace", "block_id": doc.blocks[0].id, "text": "승인 뒤 고침"}],
    )
    await run_until_idle(db, handlers)
    r = await run_uc.get_run(db, owner, run.id)
    assert (r.status, r.error_code) == ("failed", "approval_invalidated")
    with pytest.raises(NotFound):
        await app_uc.get_submission(db, owner, aid)

    run2 = await run_uc.start_run(
        deps, editor, "submit_application", {"application_id": str(aid), "revisions": {str(did): 2}}
    )
    await run_until_idle(db, handlers)
    w2 = await run_uc.get_run(db, owner, run2.id)
    await approval_uc.decide_approval(db, owner, w2.approval_id, approve=False)
    await run_until_idle(db, handlers)
    r2 = await run_uc.get_run(db, owner, run2.id)
    assert (r2.status, r2.error_code) == ("failed", "approval_rejected")

    # 승인 대기 중 취소하면 승인 요청도 닫힌다
    run3 = await run_uc.start_run(
        deps, editor, "submit_application", {"application_id": str(aid), "revisions": {str(did): 2}}
    )
    await run_until_idle(db, handlers)
    w3 = await run_uc.get_run(db, owner, run3.id)
    await run_uc.cancel_run(db, editor, run3.id)
    closed = await approval_uc.get_approval(db, owner, w3.approval_id)
    assert (closed.status, closed.invalid_reason) == ("invalidated", "run_cancelled")


async def test_start_requires_workflow_action(db, env):
    deps = RunDeps(db=db)
    with pytest.raises(RunRuleError, match="forbidden"):
        await run_uc.start_run(deps, env["viewer"], "save_job_urls", {"urls": ["https://e.test"]})
    with pytest.raises(RunRuleError, match="unknown_workflow"):
        await run_uc.start_run(deps, env["owner"], "shell", {})
    with pytest.raises(RunRuleError, match="invalid_input"):
        await run_uc.start_run(deps, env["owner"], "save_job_urls", {"urls": []})
    assert set(WORKFLOWS) == {"save_job_urls", "submit_application", "suggest_document_batch"}


# ---------------------------------------------------------------- 공간 격리 (§17.1)


async def test_runs_and_approvals_are_workspace_scoped(db, env, pg):
    owner, other = env["owner"], env["other"]
    deps = RunDeps(db=db)
    run = await run_uc.start_run(deps, owner, "save_job_urls", {"urls": ["https://e.test/iso"]})
    aid, did = await draft_application(db, owner, "https://jobs.example.test/iso")
    req = await approval_uc.request_approval(
        db, owner, action="applications.submit", target_id=aid, payload=payload(did, 1)
    )
    for call in (
        run_uc.get_run(db, other, run.id),
        run_uc.run_events(db, other, run.id),
        run_uc.cancel_run(db, other, run.id),
        run_uc.interrupt_run(db, other, run.id),
        approval_uc.get_approval(db, other, req.id),
        approval_uc.decide_approval(db, other, req.id, approve=True),
    ):
        with pytest.raises(NotFound):
            await call
    # 다른 공간의 대상으로는 승인을 요청할 수 없다
    with pytest.raises(NotFound):
        await approval_uc.request_approval(
            db, other, action="applications.submit", target_id=aid, payload=payload(did, 1)
        )
    assert await run_uc.list_runs(db, other) == []
    assert await approval_uc.list_approvals(db, other) == []
    assert [a.id for a in await approval_uc.list_approvals(db, owner)] == [req.id]
    # 다른 공간의 승인을 내 제출에 쓸 수 없다
    other_aid, other_did = await draft_application(db, other, "https://jobs.example.test/iso-b")
    with pytest.raises(ApprovalError, match="approval_not_found"):
        await app_uc.submit_application(
            db, other, other_aid, revisions={other_did: 1}, approval_id=req.id
        )
    await run_uc.cancel_run(db, owner, run.id)
    eng = create_engine(pg.owner, poolclass=NullPool)
    try:
        with eng.connect() as conn:
            assert (
                workspace_isolation_problems(conn, ["core"], allow_unforced={("core", "jobs")})
                == []
            )
    finally:
        eng.dispose()


# ---------------------------------------------------------------- HTTP


@pytest.fixture
async def client(db, env):
    tokens = {
        "owner": env["owner"],
        "viewer": env["viewer"],
        "editor": env["editor"],
        "other": env["other"],
    }
    app = FastAPI()
    app.include_router(build_router(CoreDeps(identity=FakeResolver(tokens), db=db)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        yield c


def auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_http_runs_and_approvals(db, env, client):
    owner = env["owner"]
    res = await client.post(
        "/core/runs",
        json={"workflow": "save_job_urls", "input": {"urls": ["https://e.test/h/1"]}},
        headers=auth("viewer"),
    )
    assert res.status_code == 403
    res = await client.post(
        "/core/runs",
        json={"workflow": "save_job_urls", "input": {"urls": ["https://e.test/h/1"]}},
        headers=auth("editor"),
    )
    assert res.status_code == 202, res.text
    run_id = res.json()["id"]
    assert (await client.get(f"/core/runs/{run_id}", headers=auth("viewer"))).json()[
        "status"
    ] == "queued"
    assert (await client.get(f"/core/runs/{run_id}", headers=auth("other"))).status_code == 404
    assert (
        await client.post(f"/core/runs/{run_id}/cancel", headers=auth("viewer"))
    ).status_code == 403
    res = await client.post(f"/core/runs/{run_id}/cancel", headers=auth("editor"))
    assert res.json()["status"] == "cancelled"
    assert (
        await client.post(f"/core/runs/{run_id}/cancel", headers=auth("editor"))
    ).status_code == 409
    ev = (await client.get(f"/core/runs/{run_id}/events", headers=auth("viewer"))).json()
    assert [e["code"] for e in ev] == [None, "cancelled_by_user"]
    names = [
        w["name"] for w in (await client.get("/core/runs/workflows", headers=auth("viewer"))).json()
    ]
    assert sorted(names) == ["save_job_urls", "submit_application", "suggest_document_batch"]

    aid, did = await draft_application(db, owner, "https://jobs.example.test/h/ap")
    body = {"action": "applications.submit", "target_id": str(aid), "payload": payload(did, 1)}
    assert (
        await client.post("/core/approvals", json=body, headers=auth("viewer"))
    ).status_code == 403
    res = await client.post("/core/approvals", json=body, headers=auth("editor"))
    assert res.status_code == 201, res.text
    ap = res.json()
    listed = (await client.get("/core/approvals", headers=auth("viewer"))).json()
    assert [a["id"] for a in listed] == [ap["id"]]
    for token in ("viewer", "editor"):
        res = await client.post(f"/core/approvals/{ap['id']}/approve", json={}, headers=auth(token))
        assert res.status_code == 403
    res = await client.post(
        f"/core/approvals/{ap['id']}/approve",
        json={"payload_hash": ap["payload_hash"]},
        headers=auth("owner"),
    )
    assert res.json()["status"] == "approved"
    # 편집자: 승인 묶음 없으면 403, 있으면 제출
    submit = {"revisions": {str(did): 1}}
    res = await client.post(f"/core/applications/{aid}/submit", json=submit, headers=auth("editor"))
    assert res.status_code == 403
    res = await client.post(
        f"/core/applications/{aid}/submit",
        json={**submit, "note": "다름", "approval_id": ap["id"]},
        headers=auth("editor"),
    )
    assert (res.status_code, res.json()["detail"]) == (409, "payload_changed")
    ap2 = (await client.post("/core/approvals", json=body, headers=auth("editor"))).json()
    await client.post(f"/core/approvals/{ap2['id']}/approve", json={}, headers=auth("owner"))
    res = await client.post(
        f"/core/applications/{aid}/submit",
        json={**submit, "approval_id": ap2["id"]},
        headers=auth("editor"),
    )
    assert res.status_code == 201, res.text
    assert (await client.get(f"/core/approvals/{ap2['id']}", headers=auth("viewer"))).json()[
        "status"
    ] == "used"


# ---------------------------------------------------------------- 승인 즉시 무효화·변경 비교 (c0014)


async def _status(db, ws, approval_id):
    async with db.workspace_session(ws) as s:
        row = await s.get(Approval, approval_id)
        return row.status, row.invalid_reason


async def _request(db, actor, aid, did, revision=1):
    return await approval_uc.request_approval(
        db, actor, action="applications.submit", target_id=aid, payload=payload(did, revision)
    )


async def test_target_change_closes_open_approvals_in_the_same_transaction(db, env):
    owner, editor = env["owner"], env["editor"]
    ws = owner.workspace_id
    aid, did = await draft_application(db, owner, "https://jobs.example.test/inv/1")
    pending = await _request(db, editor, aid, did)
    approved = await _request(db, editor, aid, did)
    await approval_uc.decide_approval(db, owner, approved.id, approve=True)

    # 대상 버전 밖의 칸(제목)은 승인을 건드리지 않는다
    await app_uc.update_document(db, owner, aid, did, {"title": "새 제목 (합성)"})
    async with db.workspace_session(ws) as s:
        await s.execute(update(Application).where(Application.id == aid).values(title="새 지원"))
    assert await _status(db, ws, pending.id) == ("pending", None)
    assert await _status(db, ws, approved.id) == ("approved", None)

    # 공고 확정 값이 바뀌면 그 공고의 지원에 걸린 열린 승인이 모두 닫힌다
    async with db.workspace_session(ws) as s:
        posting_id = await s.scalar(select(Application.posting_id).where(Application.id == aid))
        await s.execute(
            update(JobPosting)
            .where(JobPosting.id == posting_id)
            .values(pending_changes=["deadline"])
        )
    assert await _status(db, ws, pending.id) == ("invalidated", "target_changed")
    assert await _status(db, ws, approved.id) == ("invalidated", "target_changed")

    # 지원 상태가 바뀌어도 (예: 승인 없이 직접 제출) 열린 승인이 닫힌다
    again = await _request(db, editor, aid, did)
    await app_uc.submit_application(db, owner, aid, revisions={did: 1})
    assert await _status(db, ws, again.id) == ("invalidated", "target_changed")
    with pytest.raises(ApprovalError, match="approval_not_pending"):
        await approval_uc.decide_approval(db, owner, again.id, approve=True)


async def test_waiting_run_is_woken_and_fails_when_target_changes(db, env):
    """승인 대기 중인 실행: 대상이 바뀌면 누가 승인을 조회하지 않아도 바로 실패로 끝난다."""
    owner, editor = env["owner"], env["editor"]
    deps = RunDeps(db=db)
    handlers = run_handlers(deps)
    aid, did = await draft_application(db, owner, "https://jobs.example.test/inv/2")
    run = await run_uc.start_run(
        deps, editor, "submit_application", {"application_id": str(aid), "revisions": {str(did): 1}}
    )
    await run_until_idle(db, handlers)
    assert (await run_uc.get_run(db, owner, run.id)).status == "waiting_approval"
    doc = await app_uc.get_document(db, owner, aid, did)
    await app_uc.save_revision(
        db,
        owner,
        aid,
        did,
        base_revision=1,
        operations=[{"op": "replace", "block_id": doc.blocks[0].id, "text": "고친 문단 (합성)"}],
    )
    async with db.workspace_session(owner.workspace_id) as s:
        assert (await s.get(Run, run.id)).status == "queued"
        jobs = (
            await s.execute(
                text("SELECT status FROM core.jobs WHERE kind = 'run' AND target_id = :r"),
                {"r": run.id},
            )
        ).scalars()
        assert "queued" in list(jobs)
    await run_until_idle(db, handlers)
    r = await run_uc.get_run(db, owner, run.id)
    assert (r.status, r.error_code) == ("failed", "approval_invalidated")
    codes = [e.code for e in await run_uc.run_events(db, owner, run.id)]
    assert "approval_invalidated" in codes


async def test_approval_diff_shows_what_changed_since_the_request(db, env):
    owner, editor = env["owner"], env["editor"]
    aid, did = await draft_application(db, owner, "https://jobs.example.test/inv/3")
    req = await _request(db, editor, aid, did)
    same = await approval_uc.approval_diff(db, owner, req.id)
    assert same.changes == [] and not same.target_changed and same.current == same.requested

    doc = await app_uc.get_document(db, owner, aid, did)
    await app_uc.save_revision(
        db,
        owner,
        aid,
        did,
        base_revision=1,
        operations=[{"op": "replace", "block_id": doc.blocks[0].id, "text": "바뀐 문단 (합성)"}],
    )
    await app_uc.update_document(db, owner, aid, did, {"char_limit": 300})
    diff = await approval_uc.approval_diff(db, owner, req.id)
    assert diff.target_changed and diff.approval.status == "invalidated"
    by_path = {c.path: c for c in diff.changes}
    rev = by_path[f"documents[{did}].revision"]
    assert (rev.kind, rev.before, rev.after) == ("changed", 1, 2)
    assert rev.label  # 문서 제목
    assert by_path[f"documents[{did}].char_limit"].after == 300
    assert f"documents[{did}].content_hash" in by_path
    # 다른 공간에서는 보이지 않는다
    with pytest.raises(NotFound):
        await approval_uc.approval_diff(db, env["other"], req.id)


def test_summary_changes_pairs_list_items_by_id():
    before = {
        "title": "A",
        "documents": [{"document_id": "d1", "title": "자기소개", "revision": 1}],
    }
    after = {
        "title": "A",
        "status": "submitted",
        "documents": [
            {"document_id": "d2", "title": "포트폴리오", "revision": 1},
            {"document_id": "d1", "title": "자기소개", "revision": 3},
        ],
    }
    got = [(c.path, c.kind, c.before, c.after, c.label) for c in summary_changes(before, after)]
    assert got == [
        ("documents[d1].revision", "changed", 1, 3, "자기소개"),
        ("documents[d2]", "added", None, after["documents"][0], "포트폴리오"),
        ("status", "added", None, "submitted", None),
    ]


async def test_read_time_hash_check_still_catches_changes_the_trigger_missed(db, env, pg):
    """trigger를 끈 채(복제 모드) 바꾼 변경도 결정·사용 때 해시 비교가 막는다 (최종 방어)."""
    owner, editor = env["owner"], env["editor"]
    aid, did = await draft_application(db, owner, "https://jobs.example.test/inv/4")
    req = await _request(db, editor, aid, did)
    eng = create_engine(pg.owner, poolclass=NullPool)
    try:
        with eng.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(
                text("UPDATE core.application_documents SET char_limit = 123 WHERE id = :d"),
                {"d": did},
            )
    finally:
        eng.dispose()
    assert await _status(db, owner.workspace_id, req.id) == ("pending", None)
    with pytest.raises(ApprovalError, match="target_changed"):
        await approval_uc.decide_approval(db, owner, req.id, approve=True)
    assert await _status(db, owner.workspace_id, req.id) == ("invalidated", "target_changed")

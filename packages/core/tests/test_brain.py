"""O04 완료 기준: AI는 원문에 있는 문장만 고른다, 근거 없는 주장은 버린다, 확인은 사람이 한다."""

import json
from uuid import uuid4

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import (
    Claim,
    ClaimEvidence,
    Experience,
    ExtractionRun,
    InterviewAnswer,
    ModelCall,
    SourceSpan,
)
from career_core.domain.brain import ClaimRuleError, ClaimStatus
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import extraction_handlers, parsing_handlers, run_until_idle
from career_core.llm import ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext
from career_core.usecases import brain, interviews, wiki
from career_core.usecases.extraction import (
    ExtractionDeps,
    find_quote,
    get_extraction,
    parse_model_json,
    request_extraction,
)
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.sources import upload_source
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, ProgrammingError

pytestmark = pytest.mark.anyio

CONFIG = parse_models_config("""
[endpoints.proxy]
preset = "cliproxyapi"
price_as = "openai"
data_classes = ["public", "private"]

[routes]
default = "proxy/gpt-6.1-sol"
""")
ENV = {"CLIPROXY_API_KEY": "k"}

RESUME = (
    "Acme Corp, Backend Engineer, 2021.03 - 2023.02\n\n"
    "Built the  order   service in FastAPI and cut p95 latency from 800ms to 200ms.\n\n"
    "Our team migrated billing to PostgreSQL."
)


class ScriptedClient:
    """정해진 응답을 돌려주는 모델. 받은 요청을 기록한다."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.calls = []

    async def complete(self, model, request):
        self.calls.append(request)
        text = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        usage = TokenUsage(input_tokens=500, output_tokens=200)
        return ModelCompletion(
            text=text,
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, usage),),
            request_id="req",
        )


def gateway(db, client):
    return ModelGateway(
        db, CONFIG, PriceBook.load(), client_factory=lambda ep, key: client, env=ENV
    )


@pytest.fixture
async def people(db):
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    return {
        "owner": ActorContext(a, ws_a, scopes_for_role(Role.OWNER)),
        "editor": ActorContext(a, ws_a, scopes_for_role(Role.EDITOR)),
        "other": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
    }


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blobs")


async def parsed(db, blobs, actor, text=RESUME, *, data_class="private", parse=True):
    up = await upload_source(
        db,
        blobs,
        actor,
        filename="cv.txt",
        data=text.encode(),
        declared_mime="text/plain",
        max_bytes=1 << 20,
        data_class=data_class,
    )
    if parse:
        deps = ParsingDeps(
            db=db, blobs=blobs, egress=ProviderEgressPolicy(), local_parsers=default_local_parsers()
        )
        await run_until_idle(db, parsing_handlers(deps))
    return up.parse_run_id


def extraction_reply(*experiences):
    return "```json\n" + json.dumps({"experiences": list(experiences)}) + "\n```"


async def all_rows(db, actor, model):
    async with db.workspace_session(actor.workspace_id) as s:
        return (await s.execute(select(model))).scalars().all()


# --- 인용 확인 ----------------------------------------------------------------


def test_find_quote_exact_and_whitespace_tolerant():
    block = "Built the  order   service in FastAPI."
    assert find_quote(block, "in FastAPI") == (27, 37)
    start, end = find_quote(block, "the order service")
    assert block[start:end] == "the  order   service"
    assert find_quote(block, "in Django") is None
    assert find_quote(block, "   ") is None


def test_parse_model_json_accepts_fenced_output():
    assert parse_model_json('Sure:\n```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError, match="no_json"):
        parse_model_json("no json here")


# --- 추출 ---------------------------------------------------------------------


async def test_extraction_keeps_only_quoted_claims(db, blobs, people):
    owner = people["owner"]
    run_id = await parsed(db, blobs, owner)
    client = ScriptedClient(
        extraction_reply(
            {
                "title": "Order service",
                "organization": "Acme Corp",
                "role": "Backend Engineer",
                "period": "2021.03 - 2023.02",
                "claims": [
                    {
                        "kind": "result",
                        "statement": "주문 서비스 p95 지연을 800ms에서 200ms로 줄였다.",
                        "contribution": "personal",
                        # 모델이 공백을 바꿔 옮겨도 원문 위치를 찾고, 인용은 원문 그대로 저장한다
                        "quotes": [
                            {"block": 1, "text": "Built the order\n service  in FastAPI"},
                            {"block": 1, "text": "cut p95 latency from 800ms to 200ms"},
                        ],
                    },
                    {  # 원문에 없는 숫자: 버려야 한다
                        "kind": "result",
                        "statement": "매출을 30% 올렸다.",
                        "contribution": "personal",
                        "quotes": [{"block": 1, "text": "increased revenue by 30%"}],
                    },
                    {  # 인용 없음: 버려야 한다
                        "kind": "skill",
                        "statement": "Kubernetes 운영",
                        "quotes": [],
                    },
                ],
            },
            {  # 근거 있는 주장이 하나도 없는 경험은 만들지 않는다
                "title": "Invented project",
                "claims": [
                    {
                        "statement": "led 12 people",
                        "quotes": [{"block": 9, "text": "led 12 people"}],
                    }
                ],
            },
        )
    )
    ext_id = await request_extraction(db, owner, run_id)
    assert await request_extraction(db, owner, run_id) == ext_id  # 대기 중이면 재사용
    handlers = extraction_handlers(ExtractionDeps(db, gateway(db, client)))
    assert await run_until_idle(db, handlers) == 1

    view = await get_extraction(db, owner, ext_id)
    assert (view.status, view.experiences_created, view.claims_created, view.claims_dropped) == (
        "complete",
        1,
        1,
        3,
    )
    [exp] = await brain.list_experiences(db, owner)
    assert (exp.title, exp.period_text, exp.origin) == (
        "Order service",
        "2021.03 - 2023.02",
        "ai_extraction",
    )
    assert exp.needs_review == 1
    full = await brain.get_experience(db, owner, exp.id)
    [claim] = full.claims
    # AI가 personal이라고 해도 사람이 확인하기 전까지는 후보다
    assert claim.status == ClaimStatus.EXTRACTED
    quotes = sorted(e.quote for e in claim.evidence)
    assert quotes == ["Built the order service in FastAPI", "cut p95 latency from 800ms to 200ms"]
    for e in claim.evidence:
        span = await brain.get_span(db, owner, e.span_id)
        assert span.block_text[span.start_char : span.end_char] == span.quote
        assert span.original_filename == "cv.txt"

    [call] = await all_rows(db, owner, ModelCall)
    assert (call.purpose, call.status) == ("extract", "ok")
    sent = client.calls[0]
    assert sent.data_classes == frozenset({"private"})
    assert "[block 1]" in sent.messages[0].content


async def test_restricted_source_is_never_sent(db, blobs, people):
    owner = people["owner"]
    run_id = await parsed(db, blobs, owner, data_class="restricted")
    client = ScriptedClient(extraction_reply())
    ext_id = await request_extraction(db, owner, run_id)
    await run_until_idle(db, extraction_handlers(ExtractionDeps(db, gateway(db, client))))
    view = await get_extraction(db, owner, ext_id)
    assert view.status == "failed" and view.error_code
    assert client.calls == []
    assert await all_rows(db, owner, Experience) == []


async def test_invalid_output_and_unparsed_source(db, blobs, people):
    owner = people["owner"]
    with pytest.raises(ClaimRuleError, match="parse_not_finished"):
        await request_extraction(db, owner, await parsed(db, blobs, owner, "x y", parse=False))
    run_id = await parsed(db, blobs, owner)
    ext_id = await request_extraction(db, owner, run_id)
    client = ScriptedClient("I cannot help with that.")
    await run_until_idle(db, extraction_handlers(ExtractionDeps(db, gateway(db, client))))
    view = await get_extraction(db, owner, ext_id)
    assert (view.status, view.error_code) == ("failed", "invalid_model_output")


async def test_extraction_is_workspace_scoped(db, blobs, people):
    owner, other = people["owner"], people["other"]
    run_id = await parsed(db, blobs, owner)
    with pytest.raises(brain.NotFound):
        await request_extraction(db, other, run_id)
    ext_id = await request_extraction(db, owner, run_id)
    with pytest.raises(brain.NotFound):
        await get_extraction(db, other, ext_id)


# --- 주장 상태 규칙 -------------------------------------------------------------


async def first_block(db, actor):
    from career_core.db import DocumentBlock

    async with db.workspace_session(actor.workspace_id) as s:
        return await s.scalar(select(DocumentBlock).order_by(DocumentBlock.ordinal).limit(1))


async def test_confirm_rules(db, blobs, people):
    owner, editor = people["owner"], people["editor"]
    await parsed(db, blobs, owner)
    block = await first_block(db, owner)
    exp = await brain.create_experience(db, editor, title="Acme")

    # 편집자는 쓸 수 있지만 확인할 수 없다
    claim = await brain.add_claim(
        db, editor, exp, statement="주문 서비스를 만들었다", kind="action", contribution="personal"
    )
    assert await status_of(db, owner, claim) == "extracted"
    with pytest.raises(ClaimRuleError, match="confirm_requires_approve"):
        await brain.confirm_claim(db, editor, claim)
    # 팀/본인 구분 없이는 확인되지 않는다
    unknown = await brain.add_claim(db, owner, exp, statement="결제 이전", kind="action")
    assert await status_of(db, owner, unknown) == "extracted"
    with pytest.raises(ClaimRuleError, match="contribution_required"):
        await brain.confirm_claim(db, owner, unknown)
    assert await brain.confirm_claim(db, owner, unknown, contribution="team") == "self_confirmed"

    # 본인 확인 + 원문 근거 = evidence_supported
    assert await brain.confirm_claim(db, owner, claim) == "self_confirmed"
    span = await brain.create_span(db, owner, block.id, 0, 9)
    assert await brain.attach_span(db, editor, claim, span) == "evidence_supported"
    assert await brain.attach_span(db, editor, claim, span) == "evidence_supported"  # 중복 무시

    # 확인 권한 없는 사람이 고치면 다시 확인받아야 한다
    assert await brain.edit_claim(db, editor, claim, statement="주문 서비스를 혼자 만들었다") == (
        "extracted"
    )
    async with db.workspace_session(owner.workspace_id) as s:
        row = await s.get(Claim, claim)
        assert row.confirmed_by is None and row.confirmed_at is None
    assert await brain.confirm_claim(db, owner, claim) == "evidence_supported"

    assert await brain.set_claim_status(db, editor, claim, ClaimStatus.RETIRED) == "retired"
    with pytest.raises(ClaimRuleError, match="claim_retired"):
        await brain.confirm_claim(db, owner, claim)
    with pytest.raises(ClaimRuleError, match="use_confirm"):
        await brain.set_claim_status(db, owner, unknown, ClaimStatus.SELF_CONFIRMED)

    # 사람이 바로 쓴 주장: 확인 권한 + 구분 있음 + 근거 위치
    direct = await brain.add_claim(
        db, owner, exp, statement="FastAPI", kind="skill", contribution="personal", span_ids=[span]
    )
    assert await status_of(db, owner, direct) == "evidence_supported"


async def status_of(db, actor, claim_id):
    async with db.workspace_session(actor.workspace_id) as s:
        return (await s.get(Claim, claim_id)).status


async def test_span_must_be_inside_block(db, blobs, people):
    owner = people["owner"]
    await parsed(db, blobs, owner)
    block = await first_block(db, owner)
    with pytest.raises(ClaimRuleError, match="span_out_of_range"):
        await brain.create_span(db, owner, block.id, 5, len(block.text) + 1)
    first = await brain.create_span(db, owner, block.id, 0, 4)
    assert await brain.create_span(db, owner, block.id, 0, 4) == first


async def test_database_guards(db, blobs, people):
    owner = people["owner"]
    await parsed(db, blobs, owner)
    block = await first_block(db, owner)
    exp = await brain.create_experience(db, owner, title="Acme")
    claim = await brain.add_claim(db, owner, exp, statement="s", kind="other")
    span = await brain.create_span(db, owner, block.id, 0, 4)
    ws = owner.workspace_id

    # 확정 상태인데 확인자·팀/본인 구분이 없으면 DB가 거부한다
    with pytest.raises(IntegrityError, match="ck_claims_confirmed"):
        async with db.workspace_session(ws) as s:
            await s.execute(update(Claim).where(Claim.id == claim).values(status="self_confirmed"))
    # 근거 칸은 원문 위치나 인터뷰 답만 가리킨다
    with pytest.raises(IntegrityError, match="ck_claim_evidence_kind"):
        async with db.workspace_session(ws) as s:
            s.add(
                ClaimEvidence(
                    id=uuid4(),
                    workspace_id=ws,
                    claim_id=claim,
                    kind="source_span",
                    created_by=owner.subject_id,
                )
            )
    with pytest.raises(IntegrityError, match="ck_claim_evidence_kind"):
        async with db.workspace_session(ws) as s:
            s.add(
                ClaimEvidence(
                    id=uuid4(),
                    workspace_id=ws,
                    claim_id=claim,
                    kind="model_output",
                    source_span_id=span,
                    created_by=owner.subject_id,
                )
            )
    # 인용 문자열은 범위 길이와 같아야 한다
    with pytest.raises(IntegrityError, match="ck_source_spans_range"):
        async with db.workspace_session(ws) as s:
            s.add(
                SourceSpan(
                    id=uuid4(),
                    workspace_id=ws,
                    document_block_id=block.id,
                    start_char=10,
                    end_char=12,
                    quote="too long",
                    created_by=owner.subject_id,
                )
            )
    # 근거 원본은 고칠 수 없다 (앱 역할에 UPDATE 권한 없음)
    for model in (SourceSpan, InterviewAnswer):
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with db.workspace_session(ws) as s:
                await s.execute(update(model).values(created_at=func.now()))


async def test_cross_workspace_brain_access(db, blobs, people):
    owner, other = people["owner"], people["other"]
    await parsed(db, blobs, owner)
    block = await first_block(db, owner)
    exp = await brain.create_experience(db, owner, title="Acme")
    claim = await brain.add_claim(db, owner, exp, statement="s", kind="other")
    span = await brain.create_span(db, owner, block.id, 0, 4)
    other_exp = await brain.create_experience(db, other, title="B")
    other_claim = await brain.add_claim(db, other, other_exp, statement="b", kind="other")

    assert await brain.list_experiences(db, other) == [
        s for s in await brain.list_experiences(db, other) if s.id == other_exp
    ]
    for call in (
        brain.get_experience(db, other, exp),
        brain.confirm_claim(db, other, claim, contribution="personal"),
        brain.attach_span(db, other, other_claim, span),  # 남의 원문을 근거로 못 붙인다
        brain.get_span(db, other, span),
        brain.create_span(db, other, block.id, 0, 4),
        interviews.start_interview(db, other, exp),
    ):
        with pytest.raises(brain.NotFound):
            await call


# --- 인터뷰 -------------------------------------------------------------------


async def test_interview_answers_become_claims(db, people):
    owner, editor = people["owner"], people["editor"]
    exp = await brain.create_experience(db, owner, title="Acme")
    sid = await interviews.start_interview(db, owner, exp)
    view = await interviews.get_interview(db, owner, sid)
    assert len(view.questions) == 6
    assert {q.origin for q in view.questions} == {"template"}
    q1, q2, q3 = view.questions[:3]

    mine = await interviews.answer_question(
        db, owner, q1.id, answer="제가 혼자 주문 API를 설계했습니다.", contribution="personal"
    )
    assert mine.claim_status == "self_confirmed"
    by_editor = await interviews.answer_question(
        db,
        editor,
        q2.id,
        answer="팀이 결제를 옮겼습니다.",
        contribution="team",
        statement="결제 시스템 이전 (팀)",
    )
    assert by_editor.claim_status == "extracted"
    note = await interviews.answer_question(db, owner, q3.id, answer="메모", make_claim=False)
    assert note.claim_id is None

    full = await brain.get_experience(db, owner, exp)
    by_id = {c.id: c for c in full.claims}
    [ev] = by_id[mine.claim_id].evidence
    assert (ev.kind, ev.answer, ev.question) == (
        "interview_answer",
        "제가 혼자 주문 API를 설계했습니다.",
        q1.prompt,
    )
    assert by_id[by_editor.claim_id].statement == "결제 시스템 이전 (팀)"
    again = await interviews.get_interview(db, owner, sid)
    assert again.questions[2].answers[0][1] == "메모"

    loose = await interviews.start_interview(db, owner)
    lq = (await interviews.get_interview(db, owner, loose)).questions[0]
    with pytest.raises(ClaimRuleError, match="experience_required"):
        await interviews.answer_question(db, owner, lq.id, answer="답")
    with pytest.raises(ClaimRuleError, match="answer_required"):
        await interviews.answer_question(db, owner, lq.id, answer="  ")
    ok = await interviews.answer_question(db, owner, lq.id, answer="답", experience_id=exp)
    assert ok.claim_id is not None


async def test_ai_follow_up_questions(db, people):
    owner = people["owner"]
    exp = await brain.create_experience(db, owner, title="Acme", role="Backend")
    await brain.add_claim(db, owner, exp, statement="주문 서비스 지연을 줄였다", kind="result")
    sid = await interviews.start_interview(db, owner, exp)
    template = (await interviews.get_interview(db, owner, sid)).questions[0].prompt
    client = ScriptedClient(
        json.dumps(
            {
                "questions": [
                    {"focus": "metric", "question": "지연 시간은 어떤 도구로 측정했나요?"},
                    {"focus": "context", "question": template},  # 이미 한 질문
                    {"focus": "nonsense", "question": "본인이 맡은 부분과 팀이 맡은 부분은?"},
                ]
            }
        )
    )
    new = await interviews.add_ai_questions(db, gateway(db, client), owner, sid, count=3)
    assert len(new) == 2
    view = await interviews.get_interview(db, owner, sid)
    added = [q for q in view.questions if q.origin == "ai"]
    assert [(q.ordinal, q.focus) for q in added] == [(7, "metric"), (8, "context")]
    sent = client.calls[0]
    assert sent.data_classes == frozenset({"private"})
    assert "주문 서비스 지연을 줄였다" in sent.messages[0].content
    [call] = await all_rows(db, owner, ModelCall)
    assert call.purpose == "interview"

    bad = ScriptedClient("no")
    with pytest.raises(ClaimRuleError, match="invalid_model_output"):
        await interviews.add_ai_questions(db, gateway(db, bad), owner, sid)


# --- HTTP ---------------------------------------------------------------------


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


async def client_for(db, blobs, people, models):
    tokens = {
        "owner": people["owner"],
        "editor": people["editor"],
        "viewer": ActorContext(
            people["owner"].subject_id, people["owner"].workspace_id, scopes_for_role(Role.VIEWER)
        ),
        "other": people["other"],
    }
    app = FastAPI()
    app.include_router(
        build_router(
            CoreDeps(
                identity=Tokens(tokens),
                db=db,
                blobs=blobs,
                max_upload_bytes=4096,
                models=models,
            )
        )
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t")


async def test_brain_http_flow(db, blobs, people):
    run_id = await parsed(db, blobs, people["owner"])
    reply = extraction_reply(
        {
            "title": "Order service",
            "claims": [
                {
                    "kind": "action",
                    "statement": "FastAPI로 주문 서비스를 만들었다",
                    "contribution": "personal",
                    "quotes": [{"block": 1, "text": "order   service in FastAPI"}],
                }
            ],
        }
    )
    scripted = ScriptedClient(reply)
    gw = gateway(db, scripted)
    async with await client_for(db, blobs, people, gw) as c:
        r = await c.post(f"/core/parse-runs/{run_id}/extractions", headers=auth("editor"))
        assert r.status_code == 403  # 외부 전송 권한(egress) 필요
        r = await c.post(f"/core/parse-runs/{run_id}/extractions", headers=auth("owner"))
        assert r.status_code == 202, r.text
        ext_id = r.json()["id"]
        await run_until_idle(db, extraction_handlers(ExtractionDeps(db, gw)))
        r = await c.get(f"/core/extraction-runs/{ext_id}", headers=auth("viewer"))
        assert r.json()["status"] == "complete"

        r = await c.get("/core/experiences", headers=auth("viewer"))
        [summary] = r.json()
        assert summary["needs_review"] == 1
        r = await c.get(f"/core/experiences/{summary['id']}", headers=auth("viewer"))
        [claim] = r.json()["claims"]
        assert claim["evidence"][0]["quote"] == "order service in FastAPI"
        span_id = claim["evidence"][0]["span_id"]
        r = await c.get(f"/core/source-spans/{span_id}", headers=auth("viewer"))
        assert r.json()["source_title"]

        assert (
            await c.post(f"/core/claims/{claim['id']}/confirm", json={}, headers=auth("editor"))
        ).status_code == 403
        r = await c.post(f"/core/claims/{claim['id']}/confirm", json={}, headers=auth("owner"))
        assert r.json()["status"] == "evidence_supported"
        r = await c.patch(
            f"/core/claims/{claim['id']}", json={"statement": "수정"}, headers=auth("editor")
        )
        assert r.json()["status"] == "extracted"
        r = await c.post(f"/core/claims/{claim['id']}/dispute", headers=auth("editor"))
        assert r.json()["status"] == "disputed"
        assert (
            await c.get(f"/core/experiences/{summary['id']}", headers=auth("other"))
        ).status_code == 404

        r = await c.post(
            "/core/experiences", json={"title": "Side project"}, headers=auth("editor")
        )
        assert r.status_code == 201
        exp_id = r.json()["id"]
        r = await c.post(
            f"/core/experiences/{exp_id}/claims",
            json={"statement": "혼자 만들었다", "contribution": "personal"},
            headers=auth("owner"),
        )
        assert r.json()["status"] == "self_confirmed"
        r = await c.post(
            f"/core/experiences/{exp_id}/claims",
            json={"statement": "x", "kind": "bogus"},
            headers=auth("owner"),
        )
        assert (r.status_code, r.json()["detail"]) == (422, "invalid_kind")
        r = await c.post("/core/experiences", json={"title": "x"}, headers=auth("viewer"))
        assert r.status_code == 403

        r = await c.post("/core/interviews", json={"experience_id": exp_id}, headers=auth("editor"))
        assert r.status_code == 201
        interview = r.json()
        qid = interview["questions"][0]["id"]
        r = await c.post(
            f"/core/interview-questions/{qid}/answers",
            json={"answer": "제가 했습니다", "contribution": "personal"},
            headers=auth("owner"),
        )
        assert r.status_code == 201 and r.json()["claim_status"] == "self_confirmed"

        scripted.replies = [json.dumps({"questions": [{"focus": "metric", "question": "몇 명?"}]})]
        r = await c.post(
            f"/core/interviews/{interview['id']}/ai-questions", json={}, headers=auth("owner")
        )
        assert r.status_code == 200, r.text
        assert r.json()["questions"][-1]["origin"] == "ai"

    async with await client_for(db, blobs, people, None) as c:
        r = await c.post(
            f"/core/interviews/{interview['id']}/ai-questions", json={}, headers=auth("owner")
        )
        assert (r.status_code, r.json()["detail"]) == (503, "models_not_configured")


async def test_extraction_rows_are_isolated(db, blobs, people):
    owner, other = people["owner"], people["other"]
    run_id = await parsed(db, blobs, owner)
    await request_extraction(db, owner, run_id)
    assert len(await all_rows(db, owner, ExtractionRun)) == 1
    assert await all_rows(db, other, ExtractionRun) == []


# --- 위키 ---------------------------------------------------------------------


async def test_wiki_shows_only_confirmed_claims(db, blobs, people):
    owner = people["owner"]
    await parsed(db, blobs, owner)
    block = await first_block(db, owner)
    exp = await brain.create_experience(db, owner, title="Acme", role="Backend", period_text="2021")
    span = await brain.create_span(db, owner, block.id, 0, 9)
    await brain.add_claim(
        db,
        owner,
        exp,
        statement="주문 서비스를 만들었다",
        kind="action",
        contribution="personal",
        span_ids=[span],
    )
    await brain.add_claim(db, owner, exp, statement="아직 후보", kind="result")
    retired = await brain.add_claim(
        db, owner, exp, statement="폐기됨", kind="result", contribution="team"
    )
    await brain.set_claim_status(db, owner, retired, ClaimStatus.RETIRED)
    sid = await interviews.start_interview(db, owner, exp)
    q = (await interviews.get_interview(db, owner, sid)).questions[0]
    await interviews.answer_question(
        db, owner, q.id, answer="팀 전체가 결제를 옮겼다", kind="result", contribution="team"
    )

    page = await wiki.wiki_page(db, owner, exp)
    assert (page.confirmed, page.pending) == (2, 1)
    assert [s.title for s in page.sections] == ["한 일", "결과"]
    md = wiki.render_markdown(page)
    assert "주문 서비스를 만들었다 _(본인 · 원문 근거)_" in md
    assert "> Acme Corp — cv.txt" in md or "— cv.txt" in md
    assert "인터뷰: " in md and "팀 전체가 결제를 옮겼다" in md
    assert "아직 후보" not in md and "폐기됨" not in md
    assert "확인 대기 주장 1건" in md

    async with await client_for(db, blobs, people, None) as c:
        r = await c.get(f"/core/wiki/experiences/{exp}", headers=auth("viewer"))
        assert r.status_code == 200 and r.json()["confirmed"] == 2
        r = await c.get(f"/core/wiki/experiences/{exp}/markdown", headers=auth("viewer"))
        assert r.headers["content-type"].startswith("text/markdown") and r.text == md
        r = await c.get(f"/core/wiki/experiences/{exp}", headers=auth("other"))
        assert r.status_code == 404

"""§22 지원서 초안: 후보 사실만 쓴다, 참고 글·증빙 사실은 보내지 않는다, 확인 전 사실에 기댄 문장 표시,
글자 수 한도, 글이 있는 문서는 overwrite 없이 건드리지 않는다. 모든 자료는 합성이다.
"""

import json
from uuid import uuid4

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import DocumentBlock, JobPosting, JobPostingVersion
from career_core.db.models import DocumentRevision
from career_core.domain.documents import count_text, document_text
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import parsing_handlers, run_handlers, run_until_idle
from career_core.llm import ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext
from career_core.usecases import application_draft as draft
from career_core.usecases import applications as app_uc
from career_core.usecases import brain
from career_core.usecases import postings as posting_uc
from career_core.usecases import references as ref_uc
from career_core.usecases import runs as run_uc
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.runs import RunDeps
from career_core.usecases.sources import upload_source
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import select

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

QUESTIONS = [
    {"prompt": "성능을 개선한 경험을 작성해 주세요. (공백 포함 600자)", "char_limit": 600},
    {"prompt": "데이터를 다룬 협업 경험을 작성해 주세요. (공백 포함 160자)", "char_limit": 160},
]
S_LATENCY = "Python 주문 API p95 지연을 800ms에서 200ms로 줄이고 SQL 정산 조회를 정리했다"
S_SQL = "정산 SQL 집계 쿼리를 정리해 배치 실패를 줄였다"
S_DRAFTED = "정산 리포트 자동화 스크립트를 만들어 담당 팀에 배포했다"  # 확인 전 (추출 후보)
S_SECRET = "사내 기밀 정산 시스템 성능을 개선했다"  # restricted 원본
REFERENCE_TEXT = "참고글고유문장: 저는 어릴 때부터 성능 개선에 매료되었습니다."


async def saved_posting(db, actor, url):
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
        overrides={
            "company": "가상회사",
            "title": "백엔드 개발자",
            "questions": QUESTIONS,
            "requirements": ["Python 백엔드 개발", "SQL 이해"],
        },
    )
    return p.id


async def sourced_claim(
    db, blobs, actor, exp, statement, *, contribution="personal", data_class="private"
):
    """합성 원본을 올리고 파싱해 그 글 전체를 근거로 한 주장. contribution=unknown이면 확인 전으로 남는다."""
    await upload_source(
        db,
        blobs,
        actor,
        filename=f"{uuid4().hex[:8]}.txt",
        data=statement.encode(),
        declared_mime="text/plain",
        max_bytes=1 << 20,
        title="합성 경력 기술서",
        data_class=data_class,
    )
    deps = ParsingDeps(
        db=db, blobs=blobs, egress=ProviderEgressPolicy(), local_parsers=default_local_parsers()
    )
    await run_until_idle(db, parsing_handlers(deps))
    async with db.workspace_session(actor.workspace_id) as s:
        block = await s.scalar(select(DocumentBlock).where(DocumentBlock.text == statement))
    span = await brain.create_span(db, actor, block.id, 0, len(statement))
    return await brain.add_claim(
        db,
        actor,
        exp,
        statement=statement,
        kind="result",
        contribution=contribution,
        span_ids=[span],
    )


class DraftLLM:
    """문장마다 받은 사실을 하나씩 인용하는 모델. 가짜 ID·없는 숫자 문장·사실 없는 지원 동기 문장도 섞는다.
    ``long``이면 한도를 넘게 길게 쓴다 (두 번째 요청부터는 짧게)."""

    def __init__(self, *, long=False):
        self.long = long
        self.requests = []
        self.datas = []

    async def complete(self, model, request):
        self.requests.append(request)
        body = request.messages[0].content
        data = json.loads(body.split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])
        self.datas.append(data)
        sentences = []
        for e in data["experiences"]:
            for c in e["claims"]:
                sentences.append({"text": c["statement"] + "습니다.", "claim_ids": [c["claim_id"]]})
        first = data["experiences"][0]["claims"][0]["claim_id"]
        sentences += [
            {"text": "매출을 300% 늘렸습니다.", "claim_ids": [first]},  # 근거에 없는 숫자 → 버림
            {"text": "결과를 정리했습니다.", "claim_ids": [str(uuid4())]},  # 가짜 ID만 → 버림
            {"text": "가상회사의 백엔드 개발자로 함께 성장하고 싶습니다.", "claim_ids": []},
        ]
        paragraphs = [{"sentences": sentences}]
        if self.long and "previous_chars" not in data:
            paragraphs = [{"sentences": sentences}] * 6
        return ModelCompletion(
            text=json.dumps({"paragraphs": paragraphs}, ensure_ascii=False),
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, TokenUsage(input_tokens=500, output_tokens=300)),),
        )


def gateway(db, llm):
    return ModelGateway(db, CONFIG, PriceBook.load(), client_factory=lambda ep, k: llm, env=ENV)


@pytest.fixture
async def env(db, tmp_path):
    blobs = LocalBlobStore(tmp_path / "blobs")
    ws, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    owner = ActorContext(a, ws, scopes_for_role(Role.OWNER))
    other = ActorContext(b, ws_b, scopes_for_role(Role.OWNER))
    posting = await saved_posting(db, owner, "https://jobs.example.test/draft/1")
    exp1 = await brain.create_experience(
        db, owner, title="주문 서비스 개선", organization="가상커머스", role="백엔드 엔지니어"
    )
    c1 = await sourced_claim(db, blobs, owner, exp1, S_LATENCY)
    exp2 = await brain.create_experience(db, owner, title="정산 데이터 정리")
    c2 = await sourced_claim(db, blobs, owner, exp2, S_SQL)
    c3 = await sourced_claim(db, blobs, owner, exp2, S_DRAFTED, contribution="unknown")
    exp_secret = await brain.create_experience(db, owner, title="기밀 정산 성능 개선")
    c_secret = await sourced_claim(db, blobs, owner, exp_secret, S_SECRET, data_class="restricted")
    await ref_uc.create_reference(
        db, blobs, owner, kind="accepted_essay", title="합격 자소서 (합성)", text=REFERENCE_TEXT
    )
    aid = await app_uc.create_application(db, owner, posting)
    view = await app_uc.get_application(db, owner, aid)
    return {
        "blobs": blobs,
        "owner": owner,
        "other": other,
        "application": aid,
        "docs": [d.id for d in view.documents],
        "claims": {"c1": str(c1), "c2": str(c2), "c3": str(c3), "secret": str(c_secret)},
    }


async def run_draft(db, actor, llm, inp):
    deps = RunDeps(db=db, gateway=gateway(db, llm))
    view = await run_uc.start_run(deps, actor, draft.DRAFT_WORKFLOW, inp)
    await run_until_idle(db, run_handlers(deps))
    return await run_uc.get_run(db, actor, view.id)


async def current(db, actor, doc_id):
    async with db.workspace_session(actor.workspace_id) as s:
        from career_core.db.models import ApplicationDocument

        doc = await s.get(ApplicationDocument, doc_id)
        rev = await s.scalar(
            select(DocumentRevision).where(
                DocumentRevision.document_id == doc_id,
                DocumentRevision.revision_no == doc.current_revision,
            )
        )
        return doc, rev


async def test_draft_uses_only_own_candidate_facts(db, env):
    owner, aid = env["owner"], env["application"]
    claims = env["claims"]
    llm = DraftLLM()
    run = await run_draft(db, owner, llm, {"application_id": str(aid)})
    assert run.status == "done", run.error_code
    assert run.result["written"] == 2 and run.result["skipped"] == 0

    # 두 문항 다 지난 경험을 묻는다. 담당 업무도 맥락으로 간다
    assert [d["question_focus"] for d in llm.datas] == ["past", "past"]
    assert all("responsibilities" in d for d in llm.datas)
    assert all(d["talent"] == [] for d in llm.datas)  # 공고에 인재상이 없으면 빈 목록 (c0053)
    sent = [json.dumps(d, ensure_ascii=False) for d in llm.datas]
    sent_ids = {c["claim_id"] for d in llm.datas for e in d["experiences"] for c in e["claims"]}
    # 참고 글·restricted 사실은 모델로 가지 않는다
    assert all("참고글고유문장" not in b for b in sent)
    assert claims["secret"] not in sent_ids and all(S_SECRET not in b for b in sent)
    for req in llm.requests:
        assert "restricted" not in req.data_classes
    # 확인한 사실이 모자라 확인 전 사실(추출 후보)도 보냈고, 표시해 보냈다
    unconf = [
        c for d in llm.datas for e in d["experiences"] for c in e["claims"] if not c["confirmed"]
    ]
    assert {c["claim_id"] for c in unconf} == {claims["c3"]}

    allowed = {claims["c1"], claims["c2"], claims["c3"]}
    for d in env["docs"]:
        _doc, rev = await current(db, owner, d)
        assert rev.origin == draft.REVISION_ORIGIN and rev.revision_no == 1
        text = document_text(rev.blocks)
        assert "300%" not in text and "결과를 정리했습니다" not in text
        assert "함께 성장하고 싶습니다" in text  # 사실 없는 지원 동기 문장은 남는다
        used = {c for b in rev.blocks for c in b["claim_ids"]}
        assert used and used <= allowed
        for b in rev.blocks:
            assert b["origin"] == "ai"
            for sp in b["spans"]:
                assert set(sp["claim_ids"]) <= used
        # 글자 수는 문서 기준으로 코드가 센다
        assert not rev.counts["over_char_limit"]

    view = await draft.draft_view(db, owner, aid)
    assert view.run_id == run.id
    marked = {str(dv.document_id): dv for dv in view.documents}
    total = sum(len(dv.unconfirmed_sentences) for dv in marked.values())
    assert total == view.unconfirmed_total and total >= 1
    for dv in marked.values():
        assert dv.drafted
        for c in dv.claims:
            assert c["confirmed"] == (c["claim_id"] != claims["c3"])
            if c["claim_id"] == claims["c1"]:
                assert c["quote"] == S_LATENCY

    # 사람이 확인하면 "확인 전" 표시가 사라진다
    from uuid import UUID

    await brain.confirm_claim(db, owner, UUID(claims["c3"]), contribution="personal")
    assert (await draft.draft_view(db, owner, aid)).unconfirmed_total == 0


@pytest.mark.parametrize(
    ("question", "focus"),
    [
        ("입사 후 이루고 싶은 목표를 적어 주세요. (공백 포함 500자 이내)", "future"),
        ("입사 후 포부를 지난 경험과 연결해 적어 주세요.", "future"),
        ("앞으로 5년의 커리어 계획은 무엇입니까?", "future"),
        ("당사에서 하고 싶은 일을 적어 주세요.", "future"),
        ("목표를 세워 달성한 경험을 적어 주세요.", "past"),
        ("지원 직무와 관련해 가장 자신 있는 경험을 적어 주세요.", "past"),
        ("지원 동기를 적어 주세요.", "past"),
    ],
)
def test_question_focus(question, focus):
    # 포부 문항은 앞으로 할 일 중심으로 쓰게 모델에 알린다 (지난 경험은 짧은 근거로만)
    assert draft.question_focus(question) == focus


async def test_char_limit_retry_then_trim(db, env):
    owner, aid = env["owner"], env["application"]
    short_doc = env["docs"][1]  # 160자
    llm = DraftLLM(long=True)
    run = await run_draft(
        db, owner, llm, {"application_id": str(aid), "document_ids": [str(short_doc)]}
    )
    assert run.status == "done", run.error_code
    assert len(llm.requests) == 2  # 넘어서 한 번 더 줄여 달라고 했다
    assert llm.datas[1]["previous_chars"] > 160
    _doc, rev = await current(db, owner, short_doc)
    counts = count_text(document_text(rev.blocks), char_limit=160)
    assert counts.chars <= 160 and not rev.counts["over_char_limit"]
    # 다른 문항은 대상이 아니었다
    other, _ = await current(db, owner, env["docs"][0])
    assert other.current_revision == 0


async def test_written_documents_untouched_without_overwrite(db, env):
    owner, aid = env["owner"], env["application"]
    q1, q2 = env["docs"]
    await app_uc.save_revision(
        db,
        owner,
        aid,
        q1,
        base_revision=0,
        operations=[{"op": "insert", "text": "제가 직접 쓴 문단입니다."}],
    )
    llm = DraftLLM()
    run = await run_draft(db, owner, llm, {"application_id": str(aid)})
    assert run.status == "done"
    assert [r["document_id"] for r in run.result["results"]] == [str(q2)]
    _doc, rev = await current(db, owner, q1)
    assert (rev.revision_no, rev.origin) == (1, "user")
    assert document_text(rev.blocks) == "제가 직접 쓴 문단입니다."

    # 모두 쓴 뒤 다시 누르면 쓸 것이 없다
    run = await run_draft(db, owner, llm, {"application_id": str(aid)})
    assert (run.status, run.error_code) == ("failed", "nothing_to_draft")

    # 문항을 골라 overwrite면 새 revision으로 쓴다 (이전 글은 기록에 남는다)
    run = await run_draft(
        db, owner, llm, {"application_id": str(aid), "document_ids": [str(q1)], "overwrite": True}
    )
    assert run.status == "done" and run.result["written"] == 1
    _doc, rev = await current(db, owner, q1)
    assert (rev.revision_no, rev.origin) == (2, draft.REVISION_ORIGIN)
    old = await app_uc.get_document(db, owner, aid, q1, revision=1)
    assert old.text == "제가 직접 쓴 문단입니다."

    # 사람이 그 사이 저장하면(계획한 revision과 다르면) 건드리지 않는다
    out = await draft.draft_document(
        db, None, owner, aid, q1, call_model=None, overwrite=True, base_revision=1
    )
    assert out.status == draft.SKIPPED_CHANGED


async def test_avoids_same_main_experience(db, env):
    owner, aid = env["owner"], env["application"]
    llm = DraftLLM()
    run = await run_draft(db, owner, llm, {"application_id": str(aid)})
    assert run.status == "done"
    mains = [d["experiences"][0]["experience_id"] for d in llm.datas]
    # 첫 문항의 주 경험은 다음 문항에서 주 경험이 아니다 (다른 후보가 있으므로)
    assert len(set(mains)) == len(mains)


async def test_falls_back_to_other_own_experience(db, env):
    """검색이 다른 문항의 주 경험만 찾으면, 검색에 안 걸린 다른 내 경험(확인한 사실)을 주 경험으로 쓴다."""
    owner, aid = env["owner"], env["application"]
    exp3 = await brain.create_experience(db, owner, title="신규 입사자 온보딩")
    c4 = await sourced_claim(
        db, env["blobs"], owner, exp3, "온보딩 문서를 만들어 신규 입사자 3명을 도왔다"
    )
    async with db.workspace_session(owner.workspace_id) as s:
        from career_core.db import Claim

        used = [
            str(c.experience_id)
            for c in (await s.execute(select(Claim))).scalars()
            if c.experience_id != exp3
        ]
    llm = DraftLLM()
    out = await draft.draft_document(db, gateway(db, llm), owner, aid, env["docs"][1], avoid=used)
    assert out.status == draft.WRITTEN
    [data] = llm.datas
    assert data["experiences"][0]["experience_id"] == str(exp3)
    sent = {c["claim_id"] for e in data["experiences"] for c in e["claims"]}
    assert str(c4) in sent and env["claims"]["secret"] not in sent


async def test_isolation_and_http(db, env):
    owner, aid = env["owner"], env["application"]
    llm = DraftLLM()
    # 다른 공간에서는 이 지원이 없다
    run = await run_draft(db, env["other"], llm, {"application_id": str(aid)})
    assert (run.status, run.error_code) == ("failed", "target_not_found") and not llm.requests

    editor = ActorContext(owner.subject_id, owner.workspace_id, scopes_for_role(Role.EDITOR))
    viewer = ActorContext(owner.subject_id, owner.workspace_id, scopes_for_role(Role.VIEWER))

    class Tokens:
        async def resolve(self, credential):
            return {"editor": editor, "viewer": viewer, "owner": owner}.get(credential)

    deps = CoreDeps(identity=Tokens(), db=db)
    app = FastAPI()
    app.include_router(build_router(deps))
    h = {k: {"Authorization": f"Bearer {k}"} for k in ("editor", "viewer", "owner")}
    base = f"/core/applications/{aid}/draft"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        r = await c.get(base, headers=h["viewer"])
        assert r.status_code == 200, r.text
        assert r.json()["unconfirmed_total"] == 0 and r.json()["run_id"] is None
        assert (await c.post(base, json={}, headers=h["viewer"])).status_code == 403
        r = await c.post(f"{base}/estimate", json={}, headers=h["viewer"])
        assert r.status_code == 200 and r.json()["units"] == 2, r.text
        r = await c.post(base, json={}, headers=h["owner"])
        assert r.status_code == 202, r.text
        assert r.json()["workflow"] == draft.DRAFT_WORKFLOW
        assert (await c.get(base, headers=h["viewer"])).json()["run_id"] == r.json()["id"]
        r = await c.get(f"/core/applications/{uuid4()}/draft", headers=h["viewer"])
        assert r.status_code == 404

"""O15 완료 기준: 모든 후보 경험에 원문 근거, 후보 밖 경험 0건, 사람이 고르기 전 본문 자동 작성 없음,
공고·경험이 바뀌면 "오래됨". 공간 격리, 내보내기·복원. 모든 자료는 합성이다.
"""

import json
import tarfile
from uuid import uuid4

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import Database, DocumentBlock, JobPosting, JobPostingVersion, ModelCall
from career_core.db.models import ApplicationPrep, DocumentRevision
from career_core.domain.documents import DocumentRuleError
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.interfaces.mcp.tools import TOOLS_BY_NAME, ToolEnv
from career_core.jobs.worker import parsing_handlers, run_until_idle
from career_core.llm import ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext
from career_core.testing import migrated_database
from career_core.usecases import application_prep as prep
from career_core.usecases import applications as app_uc
from career_core.usecases import brain
from career_core.usecases import companies as company_uc
from career_core.usecases import postings as posting_uc
from career_core.usecases.export import export_workspace, restore_workspace
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.sources import upload_source
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.anyio

CONFIG = parse_models_config("""
[endpoints.proxy]
preset = "cliproxyapi"
price_as = "openai"
data_classes = ["public", "private"]
[routes]
default = "proxy/gpt-6.1-sol"
""")

QUESTIONS = [
    {"prompt": "지원 동기와 성능 개선 경험을 작성해 주세요. (공백 포함 700자)", "char_limit": 700},
    {"prompt": "데이터를 다룬 협업 경험을 작성해 주세요.", "char_limit": 500},
]
REQUIREMENTS = ["Python 백엔드 개발", "SQL 이해", "Kubernetes 운영 경험"]
PREFERRED = ["FastAPI 사용 경험"]

# 원문(근거)이 있는 주장들
S_LATENCY = "주문 API p95 지연을 800ms에서 200ms로 줄였다 (Python, FastAPI)"
S_SQL = "SQL 집계 쿼리를 정리해 정산 배치를 안정시켰다"
S_NO_SOURCE = "Go로 사내 협업 도구를 만들었다"  # 원문 근거 없는 확인 주장


async def saved_posting(db, actor, *, url, company="가상회사", title="백엔드 개발자"):
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
            "company": company,
            "title": title,
            "questions": QUESTIONS,
            "requirements": REQUIREMENTS,
            "preferred": PREFERRED,
        },
    )
    return p.id


async def sourced_claim(
    db, blobs, actor, exp, statement, *, contribution="personal", kind="result"
):
    """합성 원본을 올리고 파싱해, 그 글 전체를 근거 범위로 하는 확인 주장을 만든다."""
    await upload_source(
        db,
        blobs,
        actor,
        filename=f"{uuid4().hex[:8]}.txt",
        data=statement.encode(),
        declared_mime="text/plain",
        max_bytes=1 << 20,
        title="합성 경력 기술서",
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
        kind=kind,
        contribution=contribution,
        span_ids=[span],
    )


@pytest.fixture
async def env(db, tmp_path):
    blobs = LocalBlobStore(tmp_path / "blobs")
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    owner = ActorContext(a, ws_a, scopes_for_role(Role.OWNER))
    other = ActorContext(b, ws_b, scopes_for_role(Role.OWNER))
    posting = await saved_posting(db, owner, url="https://jobs.example.test/prep/1")
    exp1 = await brain.create_experience(
        db,
        owner,
        title="주문 서비스 개선",
        organization="가상커머스",
        role="백엔드 엔지니어",
        period_text="2021.03 - 2023.02",
    )
    c1 = await sourced_claim(db, blobs, owner, exp1, S_LATENCY)
    exp2 = await brain.create_experience(db, owner, title="정산 데이터 정리")
    c2 = await sourced_claim(db, blobs, owner, exp2, S_SQL, contribution="team", kind="action")
    exp3 = await brain.create_experience(db, owner, title="협업 도구")
    await brain.add_claim(
        db, owner, exp3, statement=S_NO_SOURCE, kind="action", contribution="personal"
    )
    # 다른 공간: 같은 낱말의 경험이 있어도 보이지 않아야 한다
    other_exp = await brain.create_experience(db, other, title="B 공간 Python SQL 경험")
    await sourced_claim(db, blobs, other, other_exp, "B 공간에서 Python과 SQL로 배치를 만들었다")
    aid = await app_uc.create_application(db, owner, posting)
    view = await app_uc.get_application(db, owner, aid)
    return {
        "blobs": blobs,
        "owner": owner,
        "other": other,
        "posting": posting,
        "application": aid,
        "docs": [d.id for d in view.documents],
        "exp": (exp1, exp2, exp3),
        "claims": (str(c1), str(c2)),
        "other_exp": other_exp,
    }


def candidates(view):
    return [c for q in view.questions for c in q["candidates"]]


async def revisions(db, actor):
    async with db.workspace_session(actor.workspace_id) as s:
        return await s.scalar(select(func.count()).select_from(DocumentRevision))


class PickingLLM:
    """문항마다 후보 밖 ID(가짜)와 다른 공간 ID를 먼저, 그다음 진짜 후보를 고르는 모델. 받은 요청을 기록한다."""

    def __init__(self, bogus):
        self.bogus = bogus
        self.requests = []

    async def complete(self, model, request):
        self.requests.append(request)
        body = request.messages[0].content
        data = json.loads(body.split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])
        out = []
        for q in data["questions"]:
            picks = [
                {"experience_id": str(uuid4()), "reason": "없는 경험"},
                {"experience_id": self.bogus, "reason": "다른 공간 경험"},
            ]
            picks += [
                {
                    "experience_id": c["experience_id"],
                    "reason": "공고 요건 Python 백엔드 개발과 이어집니다.",
                    "fact_ids": ["not-a-fact"] + [f["fact_id"] for f in data["company_facts"]],
                }
                for c in q["candidates"]
            ]
            out.append({"qid": q["qid"], "focus": "성능을 개선한 경험을 묻습니다.", "picks": picks})
        out.append({"qid": "unknown-question", "picks": [{"experience_id": self.bogus}]})
        return ModelCompletion(
            text=json.dumps({"questions": out}, ensure_ascii=False),
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, TokenUsage(input_tokens=400, output_tokens=200)),),
        )


# ---------------------------------------------------------------- 검색 순위 (모델 없음)


async def test_search_fallback_candidates_have_source_quotes(db, env):
    owner, aid = env["owner"], env["application"]
    exp1, exp2, exp3 = (str(e) for e in env["exp"])
    assert (await prep.get_prep(db, owner, aid)).status == "none"
    view = await prep.generate_prep(db, None, owner, aid)
    assert view.status == "ready" and view.mode == prep.MODE_SEARCH and not view.stale
    assert "model_denied:no_route" in view.notes
    assert [q["document_id"] for q in view.questions] == [str(d) for d in env["docs"]]
    cands = candidates(view)
    assert cands
    for c in cands:
        # 모든 후보에 원문 근거 (원본 인용), 모델 없이 고른 후보는 이유 칸이 비고 검색으로 표시
        assert c["evidence"] and all(ev["quote"] for ev in c["evidence"])
        assert c["reason"] is None and c["reason_source"] == prep.MODE_SEARCH
    ids = {c["experience_id"] for c in cands}
    assert exp1 in ids
    assert exp3 not in ids  # 원문 근거가 없는 확인 주장뿐인 경험은 후보가 아니다
    assert str(env["other_exp"]) not in ids
    latency = next(c for c in cands if c["experience_id"] == exp1)
    assert latency["evidence"][0]["quote"] == S_LATENCY
    assert latency["evidence"][0]["source"]["kind"] == "span"
    assert latency["gaps"] == []
    assert any(m["requirement"] == "Python 백엔드 개발" for m in latency["matched"])
    sql = next((c for c in cands if c["experience_id"] == exp2), None)
    if sql is not None:
        assert set(sql["gaps"]) == {
            prep.GAP_NO_METRIC,
            prep.GAP_ROLE_UNCLEAR,
            prep.GAP_PERIOD_UNKNOWN,
        }
    levels = {r["requirement"]: r["level"] for r in view.requirements}
    assert levels["SQL 이해"] == prep.LEVEL_STRONG
    assert levels["Kubernetes 운영 경험"] == prep.LEVEL_NONE
    assert levels["FastAPI 사용 경험"] == prep.LEVEL_STRONG
    # 준비 문서는 글을 쓰지 않는다
    assert await revisions(db, owner) == 0


async def test_choosing_records_but_writes_no_body_text(db, env):
    owner, aid = env["owner"], env["application"]
    q1, q2 = env["docs"]
    exp1, _, exp3 = env["exp"]
    await prep.generate_prep(db, None, owner, aid)
    res = await prep.choose_experience(db, owner, aid, q1, exp1)
    assert res.claim_ids == [env["claims"][0]]
    # 사람이 골라도 문서에는 글이 생기지 않는다 (빈 문단은 웹 편집기가 만들고 사람이 쓴다)
    assert await revisions(db, owner) == 0
    for d in env["docs"]:
        doc = await app_uc.get_document(db, owner, aid, d)
        assert doc.current_revision == 0 and doc.text == ""
    with pytest.raises(DocumentRuleError, match="not_a_candidate"):
        await prep.choose_experience(db, owner, aid, q1, exp3)
    with pytest.raises(DocumentRuleError, match="not_a_candidate"):
        await prep.choose_experience(db, owner, aid, q1, env["other_exp"])
    # 고를 근거 주장은 그 후보의 근거 안에서만
    res = await prep.choose_experience(db, owner, aid, q1, exp1, claim_ids=[str(uuid4())])
    assert res.claim_ids == [env["claims"][0]]
    view = await prep.get_prep(db, owner, aid)
    assert [c["experience_id"] for c in view.choices[str(q1)]] == [str(exp1)]
    # 같은 경험을 두 문항에 쓰면 경고
    q2_ids = {c["experience_id"] for c in view.questions[1]["candidates"]}
    if str(exp1) in q2_ids:
        await prep.choose_experience(db, owner, aid, q2, exp1)
    else:
        await app_uc.save_revision(
            db,
            owner,
            aid,
            q2,
            base_revision=0,
            operations=[
                {"op": "insert", "text": "직접 쓴 문단입니다.", "claim_ids": [env["claims"][0]]}
            ],
        )
    view = await prep.get_prep(db, owner, aid)
    assert [o["experience_id"] for o in view.overlaps] == [str(exp1)]
    assert view.overlaps[0]["document_ids"] == [str(q1), str(q2)]
    await prep.unchoose_experience(db, owner, aid, q1, exp1)
    assert str(q1) not in (await prep.get_prep(db, owner, aid)).choices


# ---------------------------------------------------------------- 모델


async def test_model_picks_inside_candidates_only(db, env):
    owner, aid = env["owner"], env["application"]
    llm = PickingLLM(str(env["other_exp"]))
    gw = ModelGateway(
        db,
        CONFIG,
        PriceBook.load(),
        client_factory=lambda ep, key: llm,
        env={"CLIPROXY_API_KEY": "k"},
    )
    # 회사 항목(확인한 것만)이 있으면 함께 보낸다
    cid = await company_uc.create_company(db, owner, name="가상회사")
    page = await company_uc.import_paste(
        db, None, owner, cid, "가상회사의 인재상은 끝까지 파고드는 사람입니다.", title="채용 페이지"
    )
    fact = await company_uc.add_fact(
        db,
        owner,
        cid,
        page_id=page.id,
        kind="talent",
        body="끝까지 파고드는 사람",
        quote="끝까지 파고드는 사람",
    )
    await company_uc.link_postings(db, owner, cid, [env["posting"]])
    view = await prep.generate_prep(db, gw, owner, aid)
    assert view.mode == prep.MODE_MODEL
    sent = llm.requests[0].messages[0].content
    # 원문 근거가 없는 경험·다른 공간 경험은 모델에도 보내지 않는다
    assert S_NO_SOURCE not in sent and "B 공간" not in sent
    assert "끝까지 파고드는 사람" in sent
    cands = candidates(view)
    allowed = {str(env["exp"][0]), str(env["exp"][1])}
    assert cands and {c["experience_id"] for c in cands} <= allowed  # 후보 밖 경험 0건
    for c in cands:
        assert c["reason"] and c["reason_source"] == prep.MODE_MODEL
        assert c["evidence"] and all(ev["quote"] for ev in c["evidence"])
        assert c["fact_ids"] == [str(fact)]  # 모르는 항목 ID는 버린다
    assert view.questions[0]["focus"] == "성능을 개선한 경험을 묻습니다."
    assert any(n.startswith("model_dropped:") for n in view.notes)
    assert view.company["facts"][0]["id"] == str(fact)
    async with db.workspace_session(owner.workspace_id) as s:
        call = await s.scalar(select(ModelCall).where(ModelCall.id == view.model_call_id))
    assert call.purpose == prep.PREP_PURPOSE  # 라우트가 없으면 default로, 장부는 이 용도
    assert await revisions(db, owner) == 0

    # 모델을 쓰지 않기로 하면 보내지 않는다
    n = len(llm.requests)
    view = await prep.generate_prep(db, gw, owner, aid, use_model=False)
    assert view.mode == prep.MODE_SEARCH and len(llm.requests) == n


def test_review_picks_drops_unknown_ids():
    parsed = prep._PrepOut.model_validate(
        {
            "questions": [
                {"qid": "q1", "picks": [{"experience_id": "x"}, {"experience_id": "a"}]},
                {"qid": "zz", "picks": [{"experience_id": "a"}]},
                {"qid": "q1", "picks": [{"experience_id": "b"}]},
            ]
        }
    )
    out, dropped = prep.review_picks(parsed, {"q1": ["a", "b"]}, set())
    assert [p["experience_id"] for p in out["q1"]["picks"]] == ["a"]
    assert dropped == 3


# ---------------------------------------------------------------- 오래됨


async def test_stale_when_posting_or_experience_changes(db, env):
    owner, aid = env["owner"], env["application"]
    q1 = env["docs"][0]
    exp1 = env["exp"][0]
    await prep.generate_prep(db, None, owner, aid)
    await prep.choose_experience(db, owner, aid, q1, exp1)
    assert not (await prep.get_prep(db, owner, aid)).stale
    await brain.update_experience(db, owner, exp1, role="리드 엔지니어")
    view = await prep.get_prep(db, owner, aid)
    assert view.stale and view.stale_reasons == [prep.STALE_EXPERIENCE]
    assert view.stale_experiences == [str(exp1)]
    view = await prep.generate_prep(db, None, owner, aid)
    assert not view.stale
    assert [c["experience_id"] for c in view.choices[str(q1)]] == [str(exp1)]  # 고른 것은 남는다
    # 공고를 다시 저장하면 오래됨
    await posting_uc.confirm_posting(
        db,
        owner,
        env["posting"],
        overrides={
            "company": "가상회사",
            "title": "백엔드 개발자",
            "questions": QUESTIONS,
            "requirements": [*REQUIREMENTS, "Redis 경험"],
            "preferred": PREFERRED,
        },
    )
    view = await prep.get_prep(db, owner, aid)
    assert prep.STALE_POSTING in view.stale_reasons
    # 문항을 더하면 새 문항으로 보이고 오래됨
    new_doc = await app_uc.add_document(
        db, owner, aid, kind="question", title="문항 3", question_prompt="입사 후 포부를 써 주세요."
    )
    view = await prep.get_prep(db, owner, aid)
    assert prep.STALE_QUESTIONS in view.stale_reasons and view.new_questions == [str(new_doc)]
    view = await prep.generate_prep(db, None, owner, aid)
    assert not view.stale and len(view.questions) == 3


# ---------------------------------------------------------------- 공간 격리·HTTP·MCP·내보내기


async def test_workspace_isolation(db, env):
    owner, other, aid = env["owner"], env["other"], env["application"]
    await prep.generate_prep(db, None, owner, aid)
    for call in (
        prep.get_prep(db, other, aid),
        prep.generate_prep(db, None, other, aid),
        prep.choose_experience(db, other, aid, env["docs"][0], env["exp"][0]),
    ):
        with pytest.raises(brain.NotFound):
            await call
    async with db.workspace_session(other.workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(ApplicationPrep)) == 0
    # B의 지원 준비에는 A의 경험이 없다
    b_posting = await saved_posting(db, other, url="https://jobs.example.test/prep/b")
    b_app = await app_uc.create_application(db, other, b_posting)
    view = await prep.generate_prep(db, None, other, b_app)
    ids = {c["experience_id"] for c in candidates(view)}
    assert ids == {str(env["other_exp"])}


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


async def test_http_and_mcp(db, env):
    owner, aid = env["owner"], env["application"]
    editor = ActorContext(owner.subject_id, owner.workspace_id, scopes_for_role(Role.EDITOR))
    viewer = ActorContext(owner.subject_id, owner.workspace_id, scopes_for_role(Role.VIEWER))
    deps = CoreDeps(identity=Tokens({"editor": editor, "viewer": viewer}), db=db)
    app = FastAPI()
    app.include_router(build_router(deps))
    h = {k: {"Authorization": f"Bearer {k}"} for k in ("editor", "viewer")}
    base = f"/core/applications/{aid}/prep"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        r = await c.get(base, headers=h["viewer"])
        assert (r.status_code, r.json()["status"]) == (200, "none")
        assert (await c.post(base, json={}, headers=h["viewer"])).status_code == 403
        r = await c.post(base, json={}, headers=h["editor"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "search" and "model_not_allowed" in body["notes"]
        q1 = env["docs"][0]
        exp1 = str(env["exp"][0])
        r = await c.post(
            f"{base}/questions/{q1}/choose", json={"experience_id": exp1}, headers=h["editor"]
        )
        assert r.status_code == 200 and r.json()["claim_ids"] == [env["claims"][0]]
        r = await c.post(
            f"{base}/questions/{q1}/choose",
            json={"experience_id": str(env["exp"][2])},
            headers=h["editor"],
        )
        assert (r.status_code, r.json()["detail"]) == (422, "not_a_candidate")
        r = await c.delete(f"{base}/questions/{q1}/choose/{exp1}", headers=h["editor"])
        assert r.status_code == 204
        assert (
            await c.get(f"/core/applications/{uuid4()}/prep", headers=h["viewer"])
        ).status_code == 404
    tool = TOOLS_BY_NAME["application_prep_get"]
    out = await tool.handler(ToolEnv(deps, viewer), tool.args(application_id=aid))
    assert out["status"] == "ready" and "never draft" in out["note"].lower()
    assert all(
        ev["quote"] for q in out["questions"] for c in q["candidates"] for ev in c["evidence"]
    )


async def test_export_restore_keeps_prep(db, env, tmp_path):
    owner, aid = env["owner"], env["application"]
    await prep.generate_prep(db, None, owner, aid)
    await prep.choose_experience(db, owner, aid, env["docs"][0], env["exp"][0])
    out = tmp_path / "export.tar.gz"
    report = await export_workspace(db, env["blobs"], owner, out)
    tables = {t["name"]: t["rows"] for t in report.manifest["tables"]}
    assert tables["core.application_preps"] == 1
    with tarfile.open(out) as tar:
        names = tar.getnames()
    assert "tables/core.application_preps.jsonl" in names
    with migrated_database() as urls:
        target = Database(urls.app, poolclass=NullPool)
        try:
            await restore_workspace(target, LocalBlobStore(tmp_path / "restored"), out)
            view = await prep.get_prep(target, owner, aid)
            assert view.status == "ready"
            assert [c["experience_id"] for c in view.choices[str(env["docs"][0])]] == [
                str(env["exp"][0])
            ]
        finally:
            await target.dispose()

"""O16 완료 기준: 질문마다 출처, 제출본 문장에서 나온 질문은 그 제출본에 묶임, 답변은 사람이 씀.

그리고: 참고 글은 근거가 되지 않음, 모델 출력의 후보 밖 ID는 버림, 모델 없이도 규칙 질문, restricted 주장은 모델에
가지 않음, 공간 격리, 권한, MCP, 내보내기·복원. 모든 자료는 합성이다.
"""

import json
import tarfile
from uuid import uuid4

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import Database, DocumentBlock, JobPosting, JobPostingVersion, ModelCall
from career_core.db.models import (
    AnswerAsset,
    ApplicationSubmission,
    InterviewPrep,
    InterviewPrepQuestion,
)
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
from career_core.usecases import answers as answer_uc
from career_core.usecases import applications as app_uc
from career_core.usecases import brain
from career_core.usecases import companies as company_uc
from career_core.usecases import interview_prep as ip
from career_core.usecases import postings as posting_uc
from career_core.usecases import references as reference_uc
from career_core.usecases.export import export_workspace, restore_workspace
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.sources import upload_source
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
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

QUESTIONS = [{"prompt": "성능을 개선한 경험을 작성해 주세요.", "char_limit": 700}]
REQUIREMENTS = ["Python 백엔드 개발", "SQL 이해"]
PREFERRED = ["FastAPI 사용 경험"]

S_LATENCY = "주문 API p95 지연을 800ms에서 200ms로 줄였다 (Python, FastAPI)"
S_SQL = "SQL 집계 쿼리를 정리해 정산 배치를 안정시켰다"
S_NO_SOURCE = "Go로 사내 협업 도구를 만들었다"
S_RESTRICTED = "비공개 자료: Python 백엔드 개발로 사내 결제 장애 대응을 맡았다"
SUBMITTED = "주문 API 응답 시간을 800ms에서 200ms로 줄인 경험이 있습니다. 이 경험으로 지원합니다."
RESUBMITTED = "정산 배치를 3시간에서 40분으로 줄였습니다. 데이터를 다루는 일을 좋아합니다."
REF_TEXT = "면접 후기: 저는 남의 답 문장 REF_OTHER_ANSWER 으로 대답했습니다."
REF_QUESTION = "가장 어려웠던 기술 문제와 Python으로 푼 과정을 말씀해 주세요."


async def saved_posting(db, actor, *, url, requirements=REQUIREMENTS):
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
    await confirm(db, actor, p.id, requirements)
    return p.id


async def confirm(db, actor, posting_id, requirements):
    await posting_uc.confirm_posting(
        db,
        actor,
        posting_id,
        overrides={
            "company": "가상회사",
            "title": "백엔드 개발자",
            "questions": QUESTIONS,
            "requirements": requirements,
            "preferred": PREFERRED,
        },
    )


async def sourced_claim(db, blobs, actor, exp, statement, *, data_class="private"):
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
        contribution="personal",
        span_ids=[span],
    )


@pytest.fixture
async def env(db, tmp_path):
    blobs = LocalBlobStore(tmp_path / "blobs")
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    owner = ActorContext(a, ws_a, scopes_for_role(Role.OWNER))
    other = ActorContext(b, ws_b, scopes_for_role(Role.OWNER))
    posting = await saved_posting(db, owner, url="https://jobs.example.test/ip/1")
    exp1 = await brain.create_experience(
        db, owner, title="주문 서비스 개선", organization="가상커머스", period_text="2021 - 2023"
    )
    c1 = await sourced_claim(db, blobs, owner, exp1, S_LATENCY)
    exp2 = await brain.create_experience(db, owner, title="정산 데이터 정리")
    c2 = await sourced_claim(db, blobs, owner, exp2, S_SQL)
    exp3 = await brain.create_experience(db, owner, title="협업 도구")
    await brain.add_claim(
        db, owner, exp3, statement=S_NO_SOURCE, kind="action", contribution="personal"
    )
    exp_r = await brain.create_experience(db, owner, title="결제 장애 대응")
    cr = await sourced_claim(db, blobs, owner, exp_r, S_RESTRICTED, data_class="restricted")
    other_exp = await brain.create_experience(db, other, title="B 공간 Python SQL 경험")
    await sourced_claim(db, blobs, other, other_exp, "B 공간에서 Python과 SQL로 배치를 만들었다")
    # 회사 (확인한 인재상)와 같은 회사의 참고 면접 후기 (확인한 구조 메모)
    cid = await company_uc.create_company(db, owner, name="가상회사")
    page = await company_uc.import_paste(
        db, None, owner, cid, "가상회사의 인재상은 끝까지 파고드는 사람입니다.", title="채용"
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
    await company_uc.link_postings(db, owner, cid, [posting])
    ref = await reference_uc.create_reference(
        db,
        blobs,
        owner,
        kind="interview_review",
        title="가상회사 면접 후기",
        text=REF_TEXT,
        company_id=cid,
    )
    note = await reference_uc.add_note(
        db, owner, ref, question=REF_QUESTION, competencies=["문제 해결"]
    )
    aid = await app_uc.create_application(db, owner, posting)
    view = await app_uc.get_application(db, owner, aid)
    doc = view.documents[0].id
    await app_uc.save_revision(
        db,
        owner,
        aid,
        doc,
        base_revision=0,
        operations=[{"op": "insert", "text": SUBMITTED, "claim_ids": [str(c1)]}],
    )
    return {
        "blobs": blobs,
        "owner": owner,
        "other": other,
        "posting": posting,
        "application": aid,
        "doc": doc,
        "exp": (exp1, exp2, exp3),
        "exp_restricted": exp_r,
        "claims": (str(c1), str(c2), str(cr)),
        "other_exp": other_exp,
        "company": cid,
        "fact": fact,
        "reference": ref,
        "note": note,
    }


async def submit(db, env):
    owner, aid = env["owner"], env["application"]
    app = await app_uc.get_application(db, owner, aid)
    return await app_uc.submit_application(
        db, owner, aid, revisions={d.id: d.current_revision for d in app.documents}
    )


async def submission_id(db, owner, aid):
    async with db.workspace_session(owner.workspace_id) as s:
        return await s.scalar(
            select(ApplicationSubmission.id).where(ApplicationSubmission.application_id == aid)
        )


def dumps(view) -> str:
    from career_core.interfaces.mcp.tools import jsonable

    return json.dumps(jsonable(view), ensure_ascii=False)


def suggestion_ids(view) -> set[str]:
    return {c["experience_id"] for q in view.questions for c in q.suggestions}


class FakeLLM:
    """질문마다 다듬은 문구 + 답 글(받으면 안 됨) + 후보 밖·다른 공간 ID를 섞어 돌려준다. 받은 요청을 남긴다."""

    def __init__(self, bogus):
        self.bogus = bogus
        self.requests = []

    async def complete(self, model, request):
        self.requests.append(request)
        body = request.messages[0].content
        data = json.loads(body.split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])
        if "items" in data:
            out = [
                {
                    "sid": it["sid"],
                    "question": f"모델이 다듬은 질문 {it['sid']}입니까?",
                    "answer": "모델이 쓴 답변입니다.",
                    "experience_ids": [str(uuid4()), self.bogus]
                    + [c["experience_id"] for c in it["candidates"]],
                }
                for it in data["items"]
            ]
            out.append({"sid": "nope", "question": "모르는 출처", "experience_ids": [self.bogus]})
            payload = {"questions": out}
        else:
            payload = {
                "points": [
                    {"kind": "unsupported", "text": "근거에 없는 수치가 있습니다."},
                    {"kind": "rewrite", "text": data["answer"]},  # 답을 그대로 옮긴 지적은 버린다
                ]
            }
        return ModelCompletion(
            text=json.dumps(payload, ensure_ascii=False),
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, TokenUsage(input_tokens=300, output_tokens=100)),),
        )


def gateway(db, llm):
    return ModelGateway(
        db,
        CONFIG,
        PriceBook.load(),
        client_factory=lambda ep, key: llm,
        env={"CLIPROXY_API_KEY": "k"},
    )


# ---------------------------------------------------------------- 출처


async def test_every_question_has_a_source_without_model(db, env):
    owner, aid = env["owner"], env["application"]
    assert (await ip.get(db, owner, aid)).status == "none"
    view = await ip.generate(db, None, owner, aid)
    assert view.status == "ready" and view.mode == ip.MODE_SEARCH and not view.stale
    assert "model_denied:no_route" in view.notes and "no_submission" in view.notes
    kinds = {q.source_kind for q in view.questions}
    assert kinds == {ip.KIND_POSTING, ip.KIND_COMPANY, ip.KIND_REFERENCE, ip.KIND_COMMON}
    for q in view.questions:
        assert q.question and q.source["kind"] == q.source_kind and q.origin == ip.ORIGIN_RULE
        assert q.answer == "" and q.answer_asset is None  # 답변은 만들지 않는다
        for c in q.suggestions:
            assert c["evidence"] and all(ev["quote"] for ev in c["evidence"])
            assert c["picked_by"] == ip.MODE_SEARCH
    posting = [q for q in view.questions if q.source_kind == ip.KIND_POSTING]
    assert {q.source["text"] for q in posting} == set(REQUIREMENTS + PREFERRED)
    assert all(q.source["posting_version_id"] and q.source["field"] for q in posting)
    company = next(q for q in view.questions if q.source_kind == ip.KIND_COMPANY)
    assert company.source["fact_id"] == str(env["fact"]) and "as_of" in company.source
    ref = next(q for q in view.questions if q.source_kind == ip.KIND_REFERENCE)
    assert ref.question == REF_QUESTION and ref.source["note_id"] == str(env["note"])
    common = {q.common_kind for q in view.questions if q.source_kind == ip.KIND_COMMON}
    assert {"intro", "motivation", "conflict", "failure"} <= common
    # 후보 밖: 원문 근거 없는 경험·다른 공간 경험은 없다
    ids = suggestion_ids(view)
    assert str(env["exp"][0]) in ids
    assert str(env["exp"][2]) not in ids and str(env["other_exp"]) not in ids
    # 참고 글 본문(남의 답)은 어디에도 없다 — 참고는 근거가 아니다
    blob = dumps(view)
    assert "REF_OTHER_ANSWER" not in blob
    for c in ref.suggestions:
        assert all(ev["source"]["kind"] in ("span", "answer") for ev in c["evidence"])


async def test_db_refuses_a_question_without_source(db, env):
    owner, aid = env["owner"], env["application"]
    await ip.generate(db, None, owner, aid)
    async with db.workspace_session(owner.workspace_id) as s:
        prep = await s.scalar(select(InterviewPrep))
    for source, kind in (({}, "posting"), ({"kind": "submission"}, "submission")):
        with pytest.raises(IntegrityError):
            async with db.workspace_session(owner.workspace_id) as s:
                s.add(
                    InterviewPrepQuestion(
                        workspace_id=owner.workspace_id,
                        prep_id=prep.id,
                        position=99,
                        source_kind=kind,
                        source_key=f"x:{uuid4()}",
                        origin="user",
                        question="출처 없는 질문",
                        source=source,
                        suggestions=[],
                        answer="",
                    )
                )


# ---------------------------------------------------------------- 제출본


async def test_submission_questions_are_bound_to_that_submission(db, env, owner_engine):
    owner, aid = env["owner"], env["application"]
    exp1 = str(env["exp"][0])
    await submit(db, env)
    sub1 = await submission_id(db, owner, aid)
    view = await ip.generate(db, None, owner, aid)
    assert view.submission_id == sub1 and not view.stale
    subs = [q for q in view.questions if q.source_kind == ip.KIND_SUBMISSION]
    assert subs
    for q in subs:
        src = q.source
        assert src["submission_id"] == str(sub1)
        assert src["revision_id"] and src["document_id"] == str(env["doc"])
        assert SUBMITTED[src["start"] : src["end"]] == src["quote"]  # 정확한 위치
    first = subs[0]
    assert first.source["quote"].startswith("주문 API 응답 시간을 800ms")
    assert first.source["claim_ids"] == [env["claims"][0]]
    # 그 문단에 근거로 이은 주장의 경험이 맨 앞
    assert first.suggestions[0]["experience_id"] == exp1
    await ip.set_answer(db, owner, aid, first.id, "제가 직접 쓴 답입니다.")

    # 다른 제출본이 이 지원의 제출본이 된다 (지금은 지원당 하나라, 옛 제출본을 지우고 다시 낸다)
    with owner_engine.begin() as conn:
        conn.execute(text("DELETE FROM core.application_submissions WHERE id = :i"), {"i": sub1})
        conn.execute(
            text("UPDATE core.applications SET status = 'draft' WHERE id = :i"), {"i": aid}
        )
    await app_uc.save_revision(
        db,
        owner,
        aid,
        env["doc"],
        base_revision=1,
        operations=[{"op": "insert", "text": RESUBMITTED}],
    )
    await submit(db, env)
    sub2 = await submission_id(db, owner, aid)
    view = await ip.get(db, owner, aid)
    assert view.stale and ip.STALE_SUBMISSION in view.stale_reasons
    old = next(q for q in view.questions if q.id == first.id)
    assert old.stale and old.stale_reasons == [ip.STALE_SUBMISSION_REPLACED]
    assert old.source["submission_id"] == str(sub1)  # 출처는 그 제출본 그대로
    assert old.answer == "제가 직접 쓴 답입니다."
    # 다시 만들면 새 제출본의 질문이 생기고, 답을 쓴 옛 질문은 남는다 (오래됨). 답 없는 옛 질문은 빠진다
    view = await ip.generate(db, None, owner, aid)
    subs = [q for q in view.questions if q.source_kind == ip.KIND_SUBMISSION]
    assert {q.source["submission_id"] for q in subs} == {str(sub1), str(sub2)}
    kept = [q for q in subs if q.source["submission_id"] == str(sub1)]
    assert [q.id for q in kept] == [first.id] and kept[0].stale
    assert any("3시간에서 40분" in q.source["quote"] for q in subs if not q.stale)


# ---------------------------------------------------------------- 모델


async def test_model_rephrases_and_picks_inside_candidates_only(db, env):
    owner, aid = env["owner"], env["application"]
    await submit(db, env)
    llm = FakeLLM(str(env["other_exp"]))
    view = await ip.generate(db, gateway(db, llm), owner, aid)
    assert view.mode == ip.MODE_MODEL
    sent = llm.requests[0].messages[0].content
    # restricted 원본의 주장·참고 글 본문·원문 근거 없는 주장·다른 공간은 보내지 않는다
    assert S_RESTRICTED not in sent and "REF_OTHER_ANSWER" not in sent
    assert S_NO_SOURCE not in sent and "B 공간" not in sent
    assert "restricted" not in llm.requests[0].data_classes
    assert any(n.startswith("model_dropped:") for n in view.notes)
    allowed = {str(env["exp"][0]), str(env["exp"][1])}
    for q in view.questions:
        assert q.answer == "" and q.answer_asset is None  # 모델이 보낸 답 글은 받지 않는다
        assert {c["experience_id"] for c in q.suggestions} <= allowed | {str(env["exp_restricted"])}
        if q.source_kind == ip.KIND_COMMON:
            assert (
                q.origin == ip.ORIGIN_RULE and "모델" not in q.question
            )  # 공통 질문 문구는 그대로
        else:
            assert q.origin == ip.ORIGIN_MODEL and q.question.startswith("모델이 다듬은 질문")
        for c in q.suggestions:
            if c["picked_by"] == ip.MODE_MODEL:
                assert c["experience_id"] in allowed
    assert "모델이 쓴 답변" not in dumps(view)
    async with db.workspace_session(owner.workspace_id) as s:
        call = await s.scalar(select(ModelCall).where(ModelCall.id == view.model_call_id))
    assert call.purpose == ip.PURPOSE
    # 모델을 쓰지 않기로 하면 보내지 않고 규칙 질문으로 돌아간다 (답 없는 질문만)
    n = len(llm.requests)
    view = await ip.generate(db, gateway(db, llm), owner, aid, use_model=False)
    assert view.mode == ip.MODE_SEARCH and len(llm.requests) == n
    assert all(q.origin == ip.ORIGIN_RULE for q in view.questions)


def test_review_questions_drops_unknown_ids():
    parsed = ip._Out.model_validate(
        {
            "questions": [
                {"sid": "s1", "question": " 다듬은\n질문 ", "experience_ids": ["x", "a", "a"]},
                {"sid": "zz", "question": "모름", "experience_ids": ["a"]},
                {"sid": "s1", "question": "겹침", "experience_ids": ["b"]},
                {"sid": "s2", "question": "공통 질문을 바꿈", "experience_ids": ["b"]},
            ]
        }
    )
    out, dropped = ip.review_questions(parsed, {"s1": ["a", "b"], "s2": ["b"]}, {"s2"})
    assert out["s1"] == {"question": "다듬은 질문", "experience_ids": ["a"]}
    assert out["s2"] == {"question": None, "experience_ids": ["b"]}
    assert dropped == 2 + 2 + 2  # x·겹친 a, 모르는 sid(1+1), 겹친 sid(1+1)


# ---------------------------------------------------------------- 답변은 사람이


async def test_answers_are_written_by_the_person(db, env):
    owner, aid = env["owner"], env["application"]
    view = await ip.generate(db, None, owner, aid)
    motivation = next(q for q in view.questions if q.common_kind == "motivation")
    conflict = next(q for q in view.questions if q.common_kind == "conflict")
    posting_q = next(q for q in view.questions if q.source_kind == ip.KIND_POSTING)
    with pytest.raises(ip.InterviewPrepError, match="answer_required"):
        await ip.save_as_answer_asset(db, owner, aid, motivation.id)
    with pytest.raises(ip.InterviewPrepError, match="answer_too_long"):
        await ip.set_answer(db, owner, aid, posting_q.id, "가" * (ip.MAX_ANSWER_CHARS + 1))
    await ip.set_answer(db, owner, aid, motivation.id, "지원 동기는 제가 씁니다.")
    await ip.set_answer(db, owner, aid, posting_q.id, "  요건 답 메모  ")
    # 공통 답변으로 저장: 같은 종류의 새 공통 답변, 메모는 그쪽으로 옮긴다
    asset = await ip.save_as_answer_asset(db, owner, aid, motivation.id)
    got = await answer_uc.get_answer(db, owner, asset)
    assert (got.kind, got.body) == ("motivation", "지원 동기는 제가 씁니다.")
    # 공통 답변에서 가져오기: 다른 질문에 잇는다 (글은 복사하지 않고 이은 공통 답변을 보인다)
    other = await answer_uc.create_answer(
        db, owner, kind="conflict", title="갈등 경험", body="갈등을 풀어 낸 이야기"
    )
    await ip.link_answer_asset(db, owner, aid, conflict.id, other)
    view = await ip.get(db, owner, aid)
    by_id = {q.id: q for q in view.questions}
    assert by_id[motivation.id].answer == "" and by_id[motivation.id].answer_asset.id == asset
    assert by_id[conflict.id].answer_asset.body == "갈등을 풀어 낸 이야기"
    assert by_id[posting_q.id].answer == "요건 답 메모" and by_id[posting_q.id].chars == 7
    assert view.answered == 3
    # 다시 만들어도 답과 문구는 그대로
    text_before = by_id[posting_q.id].question
    view = await ip.generate(db, None, owner, aid)
    by_id = {q.id: q for q in view.questions}
    assert (
        by_id[posting_q.id].answer == "요건 답 메모" and by_id[posting_q.id].question == text_before
    )
    assert by_id[conflict.id].answer_asset.id == other
    # 공통 답변을 지우면 잇기만 풀린다
    await answer_uc.delete_answer(db, owner, other)
    assert {q.id: q for q in (await ip.get(db, owner, aid)).questions}[
        conflict.id
    ].answer_asset is None
    await ip.unlink_answer_asset(db, owner, aid, motivation.id)
    # 직접 더한 질문은 출처 user, 지울 수 있다
    qid = await ip.add_question(db, owner, aid, "이직 이유는 무엇입니까?")
    view = await ip.get(db, owner, aid)
    mine = next(q for q in view.questions if q.id == qid)
    assert mine.source == {"kind": "user"} and mine.origin == ip.ORIGIN_USER
    await ip.generate(db, None, owner, aid)
    assert qid in {q.id for q in (await ip.get(db, owner, aid)).questions}
    await ip.delete_question(db, owner, aid, qid)
    assert qid not in {q.id for q in (await ip.get(db, owner, aid)).questions}


async def test_feedback_only_on_request_and_never_stored(db, env):
    owner, aid = env["owner"], env["application"]
    llm = FakeLLM(str(env["other_exp"]))
    gw = gateway(db, llm)
    view = await ip.generate(db, None, owner, aid)
    q = next(q for q in view.questions if q.source_kind == ip.KIND_POSTING)
    async with db.workspace_session(owner.workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(ModelCall)) == 0
    with pytest.raises(ip.InterviewPrepError, match="answer_required"):
        await ip.answer_feedback(db, gw, owner, aid, q.id)
    answer = "Python으로 주문 API를 고쳐 지연을 크게 줄였고 팀과 함께 배포까지 맡았습니다."
    await ip.set_answer(db, owner, aid, q.id, answer)
    fb = await ip.answer_feedback(db, gw, owner, aid, q.id, instruction="짧게")
    assert fb.points == [{"kind": "unsupported", "text": "근거에 없는 수치가 있습니다."}]
    assert fb.dropped == 1
    sent = llm.requests[-1].messages[0].content
    assert answer in sent and S_RESTRICTED not in sent and "REF_OTHER_ANSWER" not in sent
    after = {x.id: x for x in (await ip.get(db, owner, aid)).questions}[q.id]
    assert after.answer == answer  # 의견은 저장하지 않고 답을 바꾸지 않는다
    async with db.workspace_session(owner.workspace_id) as s:
        call = await s.scalar(select(ModelCall).where(ModelCall.id == fb.model_call_id))
    assert call.purpose == ip.FEEDBACK_PURPOSE
    with pytest.raises(ip.InterviewPrepError, match="models_not_configured"):
        await ip.answer_feedback(db, None, owner, aid, q.id)


# ---------------------------------------------------------------- 오래됨


async def test_stale_when_company_reference_experience_or_posting_changes(db, env):
    owner, aid = env["owner"], env["application"]
    view = await ip.generate(db, None, owner, aid)
    assert not view.stale
    await company_uc.update_fact(db, owner, env["company"], env["fact"], body="끝까지 해내는 사람")
    await reference_uc.update_note(db, owner, env["reference"], env["note"], competencies=["끈기"])
    await brain.update_experience(db, owner, env["exp"][0], role="리드 엔지니어")
    view = await ip.get(db, owner, aid)
    by_kind = {}
    for q in view.questions:
        by_kind.setdefault(q.source_kind, []).append(q)
    assert ip.STALE_COMPANY in by_kind[ip.KIND_COMPANY][0].stale_reasons
    assert ip.STALE_REFERENCE in by_kind[ip.KIND_REFERENCE][0].stale_reasons
    assert {ip.STALE_COMPANY, ip.STALE_REFERENCE, ip.STALE_EXPERIENCE} <= set(view.stale_reasons)
    assert any(
        ip.STALE_EXPERIENCE in q.stale_reasons
        for q in view.questions
        if any(c["experience_id"] == str(env["exp"][0]) for c in q.suggestions)
    )
    view = await ip.generate(db, None, owner, aid)
    assert not view.stale
    await confirm(db, owner, env["posting"], [*REQUIREMENTS, "Redis 경험"])
    view = await ip.get(db, owner, aid)
    assert ip.STALE_POSTING in view.stale_reasons
    assert all(
        ip.STALE_POSTING in q.stale_reasons
        for q in view.questions
        if q.source_kind == ip.KIND_POSTING
    )


# ---------------------------------------------------------------- 공간 격리·권한·HTTP·MCP·내보내기


async def test_workspace_isolation(db, env):
    owner, other, aid = env["owner"], env["other"], env["application"]
    view = await ip.generate(db, None, owner, aid)
    qid = view.questions[0].id
    for call in (
        ip.get(db, other, aid),
        ip.generate(db, None, other, aid),
        ip.set_answer(db, other, aid, qid, "남의 답"),
        ip.add_question(db, other, aid, "질문"),
    ):
        with pytest.raises(brain.NotFound):
            await call
    async with db.workspace_session(other.workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(InterviewPrep)) == 0
        assert await s.scalar(select(func.count()).select_from(InterviewPrepQuestion)) == 0
    # B의 면접 준비에는 A의 경험·회사·참고가 없고, A의 공통 답변을 이을 수 없다
    b_posting = await saved_posting(db, other, url="https://jobs.example.test/ip/b")
    b_app = await app_uc.create_application(db, other, b_posting)
    b_view = await ip.generate(db, None, other, b_app)
    assert suggestion_ids(b_view) <= {str(env["other_exp"])}
    assert not {q.source_kind for q in b_view.questions} & {ip.KIND_COMPANY, ip.KIND_REFERENCE}
    a_asset = await answer_uc.create_answer(db, owner, kind="intro", title="소개", body="A의 글")
    with pytest.raises(brain.NotFound):
        await ip.link_answer_asset(db, other, b_app, b_view.questions[0].id, a_asset)
    async with db.workspace_session(other.workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(AnswerAsset)) == 0


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


async def test_http_permissions_and_mcp(db, env):
    owner, aid = env["owner"], env["application"]
    editor = ActorContext(owner.subject_id, owner.workspace_id, scopes_for_role(Role.EDITOR))
    viewer = ActorContext(owner.subject_id, owner.workspace_id, scopes_for_role(Role.VIEWER))
    deps = CoreDeps(
        identity=Tokens({"owner": owner, "editor": editor, "viewer": viewer}),
        db=db,
        models=gateway(db, FakeLLM(str(env["other_exp"]))),
    )
    app = FastAPI()
    app.include_router(build_router(deps))
    h = {k: {"Authorization": f"Bearer {k}"} for k in ("owner", "editor", "viewer")}
    base = f"/core/applications/{aid}/interview-prep"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        r = await c.get(base, headers=h["viewer"])
        assert (r.status_code, r.json()["status"]) == (200, "none")
        r = await c.post(f"{base}/questions", json={"question": "질문"}, headers=h["editor"])
        assert (r.status_code, r.json()["detail"]) == (409, "prep_not_ready")
        assert (await c.post(base, json={}, headers=h["viewer"])).status_code == 403
        r = await c.post(base, json={}, headers=h["editor"])
        assert r.status_code == 200, r.text
        body = r.json()
        # 편집자는 egress 권한이 없어 모델 없이 만든다
        assert body["mode"] == "search" and "model_not_allowed" in body["notes"]
        qid = body["questions"][0]["id"]
        one = f"{base}/questions/{qid}"
        assert (
            await c.put(f"{one}/answer", json={"answer": "답"}, headers=h["viewer"])
        ).status_code == 403
        r = await c.put(f"{one}/answer", json={"answer": "제가 쓴 답입니다."}, headers=h["editor"])
        assert r.status_code == 204
        r = await c.post(f"{one}/feedback", json={}, headers=h["editor"])
        assert r.status_code == 403  # 모델 의견은 egress 권한
        r = await c.post(f"{one}/feedback", json={}, headers=h["owner"])
        assert r.status_code == 200 and r.json()["points"]
        r = await c.post(f"{one}/save-answer", json={"kind": "other"}, headers=h["editor"])
        assert r.status_code == 201
        asset = r.json()["id"]
        r = await c.delete(f"{one}/answer-asset", headers=h["editor"])
        assert r.status_code == 204
        r = await c.put(f"{one}/answer-asset", json={"answer_id": asset}, headers=h["editor"])
        assert r.status_code == 204
        r = await c.post(f"{base}/questions", json={"question": "  "}, headers=h["editor"])
        assert (r.status_code, r.json()["detail"]) == (422, "question_empty")
        r = await c.post(
            f"{base}/questions", json={"question": "직접 더한 질문"}, headers=h["editor"]
        )
        assert r.status_code == 201
        assert (
            await c.delete(f"{base}/questions/{r.json()['id']}", headers=h["editor"])
        ).status_code == 204
        assert (
            await c.get(f"/core/applications/{uuid4()}/interview-prep", headers=h["viewer"])
        ).status_code == 404
        got = (await c.get(base, headers=h["viewer"])).json()
    tool = TOOLS_BY_NAME["interview_prep_get"]
    assert tool.read_only and tool.untrusted and tool.action == "interview_prep.read"
    out = await tool.handler(ToolEnv(deps, viewer), tool.args(application_id=aid))
    assert out["status"] == "ready" and "never write" in out["note"].lower()
    assert [q["id"] for q in out["questions"]] == [q["id"] for q in got["questions"]]
    assert all(q["source"]["kind"] == q["source_kind"] for q in out["questions"])
    ref = next(q for q in out["questions"] if q["source_kind"] == "reference")
    assert ref["source"]["record"] == "reference"
    # restricted 원본의 인용은 MCP에서 뺀다
    for q in out["questions"]:
        for c in q["suggestions"]:
            for ev in c["evidence"]:
                assert (ev["quote"] is None) == ev["restricted"]
    # MCP에는 쓰기 도구가 없다 (답변은 사람이 웹에서 쓴다)
    assert not [n for n in TOOLS_BY_NAME if n.startswith("interview_prep") and n != tool.name]


async def test_export_restore_keeps_questions_and_answers(db, env, tmp_path):
    owner, aid = env["owner"], env["application"]
    await submit(db, env)
    view = await ip.generate(db, None, owner, aid)
    sub_q = next(q for q in view.questions if q.source_kind == ip.KIND_SUBMISSION)
    await ip.set_answer(db, owner, aid, sub_q.id, "복원돼야 하는 답")
    out = tmp_path / "export.tar.gz"
    report = await export_workspace(db, env["blobs"], owner, out)
    tables = {t["name"]: t["rows"] for t in report.manifest["tables"]}
    assert tables["core.interview_preps"] == 1
    assert tables["core.interview_prep_questions"] == len(view.questions)
    with tarfile.open(out) as tar:
        assert "tables/core.interview_prep_questions.jsonl" in tar.getnames()
    with migrated_database() as urls:
        target = Database(urls.app, poolclass=NullPool)
        try:
            await restore_workspace(target, LocalBlobStore(tmp_path / "restored"), out)
            got = await ip.get(target, owner, aid)
            assert len(got.questions) == len(view.questions) and not got.stale
            q = next(x for x in got.questions if x.id == sub_q.id)
            assert q.answer == "복원돼야 하는 답" and q.source == sub_q.source
        finally:
            await target.dispose()

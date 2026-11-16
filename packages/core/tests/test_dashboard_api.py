"""대시보드용 읽기 경로: 인터뷰 목록, 원본 목록, 최근 활동, 공고 버전 목록, 공고 시각,
검색 원문 위치, 문항 미작성 수, 편집 중 글자 수, 오답 목록. 모든 자료는 합성이다."""

from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from career_core.db import (
    DocumentBlock,
    JobPosting,
    JobPostingVersion,
    ParseRun,
    Source,
    SourceVersion,
)
from career_core.db.base import utcnow
from career_core.db.models import ExtractionRun
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.ports import ActorContext
from career_core.usecases import applications as app_uc
from career_core.usecases import brain, interviews
from career_core.usecases import postings as posting_uc
from career_core.usecases import study as study_uc
from career_core.usecases.search import first_match
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI

pytestmark = pytest.mark.anyio


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


async def add_source(db, actor, *, title, text, status="complete"):
    ws = actor.workspace_id
    async with db.workspace_session(ws) as s:
        src = Source(
            id=uuid4(),
            workspace_id=ws,
            title=title,
            data_class="private",
            created_by=actor.subject_id,
        )
        s.add(src)
        await s.flush()
        ver = SourceVersion(
            id=uuid4(),
            workspace_id=ws,
            source_id=src.id,
            sha256=uuid4().hex,
            size_bytes=len(text.encode()),
            mime_type="text/plain",
            original_filename=f"{title}.txt",
            blob_key=f"k/{uuid4().hex}",
        )
        s.add(ver)
        await s.flush()
        run = ParseRun(
            id=uuid4(),
            workspace_id=ws,
            source_version_id=ver.id,
            pipeline_version=1,
            status=status,
            requested_by=actor.subject_id,
            pages_total=1,
        )
        s.add(run)
        await s.flush()
        block = DocumentBlock(
            id=uuid4(),
            workspace_id=ws,
            parse_run_id=run.id,
            page=1,
            ordinal=0,
            origin="text",
            text=text,
        )
        s.add(block)
    return src.id, run.id, block.id


async def add_posting(db, actor, *, url, company, title, questions=(), versions=1):
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
        for n in range(1, versions + 1):
            s.add(
                JobPostingVersion(
                    id=uuid4(),
                    workspace_id=actor.workspace_id,
                    posting_id=p.id,
                    version_no=n,
                    final_url=url,
                    method="manual_text",
                    adapter="text",
                    content_hash=uuid4().hex,
                    text=f"합성 공고 본문 v{n}",
                    extracted={},
                    missing=[],
                    changed_fields=[] if n == 1 else ["deadline"],
                    created_by=actor.subject_id,
                )
            )
            await s.flush()
    await posting_uc.confirm_posting(
        db,
        actor,
        p.id,
        overrides={"company": company, "title": title, "questions": list(questions)},
    )
    return p.id


@pytest.fixture
async def env(db):
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    owner = ActorContext(a, ws_a, scopes_for_role(Role.OWNER))
    other = ActorContext(b, ws_b, scopes_for_role(Role.OWNER))
    # 읽기는 되지만 지원·학습 활동은 볼 수 없는 토큰 (scope 좁힘)
    narrow = ActorContext(a, ws_a, frozenset())
    app = FastAPI()
    app.include_router(
        build_router(
            CoreDeps(
                identity=Tokens(
                    {
                        "owner": owner,
                        "viewer": ActorContext(a, ws_a, scopes_for_role(Role.VIEWER)),
                        "other": other,
                        "none": narrow,
                    }
                ),
                db=db,
            )
        )
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t")
    yield {"owner": owner, "other": other, "c": client}
    await client.aclose()


def h(name):
    return {"Authorization": f"Bearer {name}"}


async def test_interview_list_per_experience_and_overall(db, env):
    owner, c = env["owner"], env["c"]
    e1 = await brain.create_experience(db, owner, title="합성 경험 하나")
    e2 = await brain.create_experience(db, owner, title="합성 경험 둘")
    s1 = await interviews.start_interview(db, owner, e1)
    s2 = await interviews.start_interview(db, owner, e2)
    s3 = await interviews.start_interview(db, owner, None)
    iv = await interviews.get_interview(db, owner, s1)
    await interviews.answer_question(
        db, owner, iv.questions[0].id, answer="합성 답변", make_claim=False
    )
    await interviews.start_interview(db, env["other"], None)

    r = await c.get("/core/interviews", headers=h("viewer"))
    assert r.status_code == 200
    rows = r.json()
    assert [x["id"] for x in rows] == [str(s3), str(s2), str(s1)]  # 최근 것부터, 다른 공간 없음
    first = rows[2]
    assert first["experience_title"] == "합성 경험 하나"
    assert first["questions"] == len(iv.questions) and first["answered"] == 1
    assert first["last_answer_at"] is not None and rows[0]["experience_id"] is None
    r = await c.get("/core/interviews", params={"experience_id": str(e2)}, headers=h("viewer"))
    assert [x["id"] for x in r.json()] == [str(s2)]
    assert (await c.get("/core/interviews", headers=h("none"))).status_code == 403
    # 기존 상세 경로는 그대로
    assert (await c.get(f"/core/interviews/{s1}", headers=h("viewer"))).status_code == 200


async def test_source_list_with_latest_parse_status(db, env):
    owner, c = env["owner"], env["c"]
    src_ok, run_ok, _ = await add_source(db, owner, title="합성 이력서", text="주문 API 개선")
    src_bad, _, _ = await add_source(db, owner, title="깨진 파일", text="x", status="failed")
    async with db.workspace_session(owner.workspace_id) as s:
        s.add(
            ExtractionRun(
                id=uuid4(),
                workspace_id=owner.workspace_id,
                parse_run_id=run_ok,
                status="complete",
                requested_by=owner.subject_id,
                claims_created=3,
            )
        )
    await add_source(db, env["other"], title="B 자료", text="다른 공간")
    r = await c.get("/core/sources", headers=h("viewer"))
    assert r.status_code == 200
    by_id = {x["id"]: x for x in r.json()}
    assert set(by_id) == {str(src_ok), str(src_bad)}
    ok = by_id[str(src_ok)]
    assert (ok["parse_status"], ok["parse_run_id"], ok["versions"]) == ("complete", str(run_ok), 1)
    assert (ok["extraction_status"], ok["claims_created"]) == ("complete", 3)
    assert ok["original_filename"] == "합성 이력서.txt"
    assert by_id[str(src_bad)]["parse_status"] == "failed"
    assert by_id[str(src_bad)]["extraction_status"] is None
    assert (await c.get("/core/sources", headers=h("none"))).status_code == 403


async def test_posting_versions_and_list_timestamps(db, env):
    owner, c = env["owner"], env["c"]
    pid = await add_posting(
        db,
        owner,
        url="https://jobs.example.test/v",
        company="가상회사",
        title="합성 직무",
        versions=3,
    )
    r = await c.get(f"/core/job-postings/{pid}/versions", headers=h("viewer"))
    assert r.status_code == 200
    vs = r.json()
    assert [v["version_no"] for v in vs] == [3, 2, 1]
    assert all(v["text"] is None for v in vs)  # 본문은 단건 조회로만
    assert vs[0]["changed_fields"] == ["deadline"]
    full = await c.get(f"/core/job-postings/{pid}/versions/{vs[2]['id']}", headers=h("viewer"))
    assert full.json()["text"] == "합성 공고 본문 v1"
    assert (
        await c.get(f"/core/job-postings/{uuid4()}/versions", headers=h("viewer"))
    ).status_code == 404
    other = await c.get(f"/core/job-postings/{pid}/versions", headers=h("other"))
    assert other.status_code == 404
    [summary] = (await c.get("/core/job-postings", headers=h("viewer"))).json()
    assert summary["created_at"] and summary["saved_at"]
    assert summary["saved_at"] >= summary["created_at"]


async def test_block_search_hit_points_to_source(db, env):
    owner, c = env["owner"], env["c"]
    text = "2023년 하반기 주문 API의 Kafka 소비자 지연을 줄였다."
    src, run, block = await add_source(db, owner, title="합성 회고", text=text)
    r = await c.get(
        "/core/search", params={"q": "kafka 지연", "kinds": "block"}, headers=h("viewer")
    )
    [hit] = r.json()["hits"]
    assert (hit["block_id"], hit["parse_run_id"], hit["source_id"]) == (
        str(block),
        str(run),
        str(src),
    )
    assert (hit["page"], hit["ordinal"]) == (1, 0)
    assert text[hit["match_start"] : hit["match_end"]] == "Kafka"
    assert first_match("abc", ["zz"]) is None
    # 주장 결과에는 원문 위치가 없다
    exp = await brain.create_experience(db, owner, title="합성")
    await brain.add_claim(
        db, owner, exp, statement="Kafka 지연 개선", kind="result", contribution="personal"
    )
    r = await c.get("/core/search", params={"q": "Kafka", "kinds": "claim"}, headers=h("viewer"))
    [hit] = r.json()["hits"]
    assert hit["block_id"] is None and hit["match_start"] is None


async def test_unwritten_questions_and_draft_counts(db, env):
    owner, c = env["owner"], env["c"]
    pid = await add_posting(
        db,
        owner,
        url="https://jobs.example.test/q",
        company="가상회사",
        title="합성 직무",
        questions=[
            {"prompt": "지원 동기 (공백 제외 10자)", "char_limit": 10},
            {"prompt": "협업 경험", "char_limit": None},
        ],
    )
    aid = await app_uc.create_application(db, owner, pid)
    view = await app_uc.get_application(db, owner, aid)
    d1, d2 = view.documents
    await app_uc.add_document(db, owner, aid, kind="resume", title="이력서")
    [summary] = (await c.get("/core/applications", headers=h("viewer"))).json()
    assert (summary["documents"], summary["questions"], summary["unwritten"]) == (3, 2, 2)
    await app_uc.save_revision(
        db, owner, aid, d1.id, base_revision=0, operations=[{"op": "insert", "text": "합성 문단"}]
    )
    [summary] = (await c.get("/core/applications", headers=h("viewer"))).json()
    assert summary["unwritten"] == 1

    base = f"/core/applications/{aid}/documents/{d1.id}"
    r = await c.post(
        base + "/count",
        json={"blocks": ["  가나 다  ", "", "라마바사아자차카"]},
        headers=h("viewer"),
    )
    assert r.status_code == 200
    counts = r.json()
    # 저장할 때처럼 앞뒤 공백·빈 문단을 빼고 줄바꿈 하나로 잇는다. 이 문항은 공백 제외 기준.
    assert (counts["chars_with_spaces"], counts["chars_without_spaces"]) == (13, 11)
    assert counts["chars"] == 11 and counts["over_char_limit"] is True
    # 세기만 하고 저장하지 않는다
    assert (await app_uc.get_document(db, owner, aid, d1.id)).current_revision == 1
    r = await c.post(
        f"/core/applications/{aid}/documents/{d2.id}/count",
        json={"blocks": []},
        headers=h("viewer"),
    )
    assert r.json()["chars"] == 0
    r = await c.post(base + "/count", json={"blocks": ["x"]}, headers=h("other"))
    assert r.status_code == 404


async def test_activity_feed_is_ordered_and_scoped(db, env):
    owner, c = env["owner"], env["c"]
    exp = await brain.create_experience(db, owner, title="합성 경험")
    claim = await brain.add_claim(
        db, owner, exp, statement="합성 주장", kind="result", contribution="personal"
    )
    pid = await add_posting(
        db,
        owner,
        url="https://jobs.example.test/a",
        company="가상회사",
        title="합성 직무",
        questions=[{"prompt": "문항", "char_limit": None}],
    )
    aid = await app_uc.create_application(db, owner, pid)
    doc = (await app_uc.get_application(db, owner, aid)).documents[0]
    await app_uc.save_revision(
        db, owner, aid, doc.id, base_revision=0, operations=[{"op": "insert", "text": "합성"}]
    )
    item = await study_uc.create_item(db, owner, title="합성 학습")
    await study_uc.record_attempt(
        db, owner, result="failed", study_item_id=item, score=3, max_score=10
    )
    other_exp = await brain.create_experience(db, env["other"], title="B")
    await brain.add_claim(
        db, env["other"], other_exp, statement="B 주장", kind="result", contribution="personal"
    )

    r = await c.get("/core/activity", headers=h("viewer"))
    assert r.status_code == 200
    rows = r.json()
    kinds = {(x["kind"], x["action"]) for x in rows}
    assert {
        ("claim", "created"),
        ("claim", "confirmed"),
        ("posting", "created"),
        ("posting", "saved"),
        ("document_revision", "revised"),
        ("study_attempt", "attempted"),
    } <= kinds
    assert [x["at"] for x in rows] == sorted((x["at"] for x in rows), reverse=True)
    assert all(x["title"] != "B 주장" for x in rows)
    rev = next(x for x in rows if x["kind"] == "document_revision")
    assert rev["refs"] == {
        "application_id": str(aid),
        "document_id": str(doc.id),
        "revision_no": "1",
    }
    att = next(x for x in rows if x["kind"] == "study_attempt")
    assert (att["status"], att["detail"], att["title"]) == ("failed", "3.00/10.00", "합성 학습")
    made = next(x for x in rows if x["kind"] == "claim" and x["action"] == "created")
    assert made["id"] == str(claim) and made["refs"] == {"experience_id": str(exp)}

    r = await c.get("/core/activity", params={"kinds": "claim", "limit": 1}, headers=h("viewer"))
    assert len(r.json()) == 1 and r.json()[0]["kind"] == "claim"
    oldest = rows[-1]["at"]
    r = await c.get("/core/activity", params={"before": oldest}, headers=h("viewer"))
    assert all(x["at"] < oldest for x in r.json())
    future = (utcnow() + timedelta(days=1)).isoformat()
    r = await c.get(
        "/core/activity", params={"before": future, "kinds": "posting"}, headers=h("viewer")
    )
    assert {x["kind"] for x in r.json()} == {"posting"}
    assert (await c.get("/core/activity", headers=h("none"))).status_code == 403


async def test_mistake_list(db, env):
    owner, c = env["owner"], env["c"]
    item = await study_uc.create_item(db, owner, title="합성 알고리즘")
    m1 = await study_uc.add_mistake(db, owner, question="합성 문제 1", study_item_id=item)
    m2 = await study_uc.add_mistake(db, owner, question="합성 문제 2", study_item_id=item)
    await study_uc.set_mistake_status(db, owner, m2, "resolved")
    r = await c.get("/core/study/mistakes", headers=h("viewer"))
    assert [x["id"] for x in r.json()] == [str(m1), str(m2)]
    assert r.json()[0]["study_item_title"] == "합성 알고리즘"
    r = await c.get("/core/study/mistakes", params={"status": "open"}, headers=h("viewer"))
    assert [x["id"] for x in r.json()] == [str(m1)]
    r = await c.get("/core/study/mistakes", params={"status": "nope"}, headers=h("viewer"))
    assert r.status_code == 422
    assert (await c.get("/core/study/mistakes", headers=h("other"))).json() == []


async def test_claims_by_ids(db, env):
    owner, c = env["owner"], env["c"]
    exp = await brain.create_experience(db, owner, title="합성 경험")
    c1 = await brain.add_claim(
        db, owner, exp, statement="첫 주장", kind="result", contribution="personal"
    )
    c2 = await brain.add_claim(db, owner, exp, statement="둘째 주장", kind="action")
    other_exp = await brain.create_experience(db, env["other"], title="B")
    foreign = await brain.add_claim(
        db, env["other"], other_exp, statement="B 주장", kind="other", contribution="personal"
    )
    ids = ",".join(str(x) for x in (c2, foreign, c1, c2))
    r = await c.get("/core/claims", params={"ids": ids}, headers=h("viewer"))
    assert r.status_code == 200
    rows = r.json()
    assert [x["id"] for x in rows] == [str(c2), str(c1)]  # 요청 순서, 다른 공간 없음, 중복 없음
    assert rows[0]["status"] == "extracted" and rows[1]["status"] == "self_confirmed"
    assert rows[0]["experience_title"] == "합성 경험"
    r = await c.get("/core/claims", params={"ids": "x"}, headers=h("viewer"))
    assert (r.status_code, r.json()["detail"]) == (422, "invalid_claim_ids")

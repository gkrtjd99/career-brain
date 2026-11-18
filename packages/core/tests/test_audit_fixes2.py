"""2026-11-18 사용 점검 (2): 제안 지우기, 이력서에서 프로필 채울 값 찾기, 면접 답 의견의 근거 범위.

모든 자료는 합성이다 (가상 이름·example.com·010-0000-000X)."""

import json
from uuid import uuid4

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import DocumentBlock, ExtractionRun, JobPosting, JobPostingVersion
from career_core.domain import profile_suggest as ps
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import parsing_handlers, run_until_idle
from career_core.llm import ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext
from career_core.usecases import applications as app_uc
from career_core.usecases import brain
from career_core.usecases import interview_prep as ip
from career_core.usecases import postings as posting_uc
from career_core.usecases import profiles as profile_uc
from career_core.usecases.extraction import dismiss_suggestion, list_suggestions
from career_core.usecases.parsing import ParsingDeps
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


@pytest.fixture
async def people(db):
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    return {
        "owner": ActorContext(a, ws_a, scopes_for_role(Role.OWNER)),
        "viewer": ActorContext(a, ws_a, scopes_for_role(Role.VIEWER)),
        "other": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
    }


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blobs")


async def upload(db, blobs, actor, text, *, filename=None, title=None, **kw):
    up = await upload_source(
        db,
        blobs,
        actor,
        filename=filename or f"{uuid4().hex[:8]}.txt",
        data=text.encode(),
        declared_mime="text/plain",
        max_bytes=1 << 20,
        title=title,
        **kw,
    )
    deps = ParsingDeps(
        db=db, blobs=blobs, egress=ProviderEgressPolicy(), local_parsers=default_local_parsers()
    )
    await run_until_idle(db, parsing_handlers(deps))
    return up


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def http_client(db, blobs, people):
    app = FastAPI()
    app.include_router(
        build_router(
            CoreDeps(
                identity=Tokens(
                    {
                        "owner": people["owner"],
                        "viewer": people["viewer"],
                        "other": people["other"],
                    }
                ),
                db=db,
                blobs=blobs,
                max_upload_bytes=1 << 20,
            )
        )
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t")


# ---------------------------------------------------------------- 1. 제안 지우기


async def seed_run(db, actor, parse_run_id, suggestions):
    async with db.workspace_session(actor.workspace_id) as s:
        s.add(
            ExtractionRun(
                id=uuid4(),
                workspace_id=actor.workspace_id,
                parse_run_id=parse_run_id,
                status="complete",
                requested_by=actor.subject_id,
                suggestions=suggestions,
            )
        )


def school(name):
    return {"school": name, "major": "컴퓨터공학", "degree": "학사", "period_text": "2015 - 2021"}


async def test_dismiss_extraction_suggestion(db, blobs, people):
    owner, other = people["owner"], people["other"]
    a = await upload(db, blobs, owner, "합성 이력서 A")
    b = await upload(db, blobs, other, "합성 이력서 B")
    await seed_run(
        db,
        owner,
        a.parse_run_id,
        {
            "skills": ["Kafka", "Redis"],
            "education": [school("한빛대학교(가상)")],
            "possible_duplicates": [{"title": "남는 제안"}],
        },
    )
    await seed_run(
        db, owner, a.parse_run_id, {"skills": ["kafka", "Go"], "education": [school("새솔대학교")]}
    )
    await seed_run(
        db, other, b.parse_run_id, {"skills": ["Kafka"], "education": [school("한빛대")]}
    )
    before = await list_suggestions(db, owner)
    assert sorted(x.casefold() for x in before["skills"]) == ["go", "kafka", "redis"]

    async with http_client(db, blobs, people) as c:
        body = {"kind": "skill", "value": "KAFKA"}
        r = await c.post("/core/extraction-suggestions/dismiss", json=body, headers=auth("viewer"))
        assert r.status_code == 403  # 쓰기 권한
        r = await c.post("/core/extraction-suggestions/dismiss", json=body, headers=auth("owner"))
        assert r.status_code == 204
        # 같은 요청·없는 값도 204
        r = await c.post("/core/extraction-suggestions/dismiss", json=body, headers=auth("owner"))
        assert r.status_code == 204
        r = await c.post(
            "/core/extraction-suggestions/dismiss",
            json={"kind": "skill", "value": "없는기술"},
            headers=auth("owner"),
        )
        assert r.status_code == 204
        r = await c.post(
            "/core/extraction-suggestions/dismiss",
            json={"kind": "title", "value": "x"},
            headers=auth("owner"),
        )
        assert r.status_code == 422
        # 학력: 법인 표시·괄호·공백을 무시하고 맞춘다
        r = await c.post(
            "/core/extraction-suggestions/dismiss",
            json={"kind": "education", "value": "한빛대학교 (가상)"},
            headers=auth("owner"),
        )
        assert r.status_code == 204
        got = (await c.get("/core/extraction-suggestions", headers=auth("owner"))).json()
    assert sorted(got["skills"]) == ["Go", "Redis"]
    assert [e["school"] for e in got["education"]] == ["새솔대학교"]
    # 다른 제안은 남는다
    async with db.workspace_session(owner.workspace_id) as s:
        runs = (await s.scalars(select(ExtractionRun))).all()
    assert any(r.suggestions.get("possible_duplicates") for r in runs)
    assert not any("kafka" in [x.casefold() for x in r.suggestions.get("skills", [])] for r in runs)
    # 다른 공간은 그대로
    theirs = await list_suggestions(db, other)
    assert theirs["skills"] == ["Kafka"] and [e["school"] for e in theirs["education"]] == [
        "한빛대"
    ]
    assert await dismiss_suggestion(db, owner, "skill", "Redis") == 1
    assert await dismiss_suggestion(db, owner, "skill", "Redis") == 0


# ---------------------------------------------------------------- 2. 프로필 채울 값


RESUME = """김예시

이메일: yesi.kim@example.com | 휴대폰 +82 10-0000-0001
GitHub: github.com/yesi-example  LinkedIn https://www.linkedin.com/in/yesi-example/

포트폴리오: https://yesi.example.com/works
블로그 https://yesi-example.tistory.com

보조 연락처 010 0000 0002, yesi.backup@example.com

경력
누리데이터(가상) 백엔드 엔지니어 2021.03 - 2024.08"""

PROOF = "성명: 김증빙\n증빙 이메일 proof.only@example.com 010-0000-0009"
REFERENCE = (
    "이름: 박참고\n다른 사람 이메일 other.person@example.com 010-0000-0008 github.com/other-person"
)


def test_scan_rules():
    found = ps.scan_block(
        "이름: 김예시   생년월일 2000.01.01\nName: Yesi Kim\n"
        "전화 010.0000.0003 / 01000000004 / 2010 1234 5678\n"
        "https://github.com https://example.com/x 연락 kim@velog.io"
    )
    by = {(f.field, f.value) for f in found}
    assert ("name", "김예시") in by and ("name", "Yesi Kim") in by
    assert ("phone", "010-0000-0003") in by and ("phone", "010-0000-0004") in by
    assert not any(f.field == "phone" and "1234" in f.value for f in found)
    assert ("email", "kim@velog.io") in by
    # 대표 주소만 있는 GitHub·이름표 없는 모르는 곳·이메일의 주소 부분은 링크가 아니다
    assert not any(f.field == "link" for f in found)
    assert ps.guess_name("경력사항\n김예시") is not None
    assert ps.guess_name("경력사항\n학력") is None
    assert ps.looks_like_resume("김예시_이력서.pdf") and ps.looks_like_resume("my CV")
    assert not ps.looks_like_resume("cvs 정리")
    assert ps.value_key("phone", "+82 10 0000 0001") == ps.value_key("phone", "010-0000-0001")
    assert ps.value_key("link", "http://www.GitHub.com/kim/") == ps.value_key(
        "link", "https://github.com/kim"
    )


async def test_profile_suggestions_from_my_resume(db, blobs, people):
    owner, other = people["owner"], people["other"]
    await upload(db, blobs, owner, RESUME, filename="yesi_resume.txt", title="김예시 이력서")
    await upload(db, blobs, owner, PROOF, purpose="proof")
    await upload(db, blobs, owner, REFERENCE, purpose="reference")
    await upload(db, blobs, owner, "이메일 pending.only@example.com", purpose_pending=True)
    await upload(db, blobs, other, "이름: 이다른\nb.space@example.com")

    async with http_client(db, blobs, people) as c:
        r = await c.get("/core/profile/suggestions", headers=auth("viewer"))
        assert r.status_code == 200
        got = r.json()["suggestions"]
    by = {(x["field"], x["value"]): x for x in got}
    assert set(by) == {
        ("name", "김예시"),
        ("email", "yesi.kim@example.com"),
        ("email", "yesi.backup@example.com"),
        ("phone", "010-0000-0001"),
        ("phone", "010-0000-0002"),
        ("link", "https://github.com/yesi-example"),
        ("link", "https://www.linkedin.com/in/yesi-example/"),
        ("link", "https://yesi.example.com/works"),
        ("link", "https://yesi-example.tistory.com"),
    }
    labels = {x["value"]: x["label"] for x in got if x["field"] == "link"}
    assert labels == {
        "https://github.com/yesi-example": "GitHub",
        "https://www.linkedin.com/in/yesi-example/": "LinkedIn",
        "https://yesi.example.com/works": "포트폴리오",
        "https://yesi-example.tistory.com": "블로그",
    }
    email = by[("email", "yesi.kim@example.com")]
    assert email["source_title"] == "김예시 이력서" and email["label"] is None
    assert "yesi.kim@example.com" in email["quote"] and len(email["quote"]) <= 160
    dumped = json.dumps(got, ensure_ascii=False)
    for leaked in ("김증빙", "proof.only", "박참고", "other.person", "pending.only", "이다른"):
        assert leaked not in dumped  # 증빙·참고·확인 대기·다른 공간은 보지 않는다

    # 이미 프로필에 있는 값은 뺀다 (맞춘 꼴로 비교)
    await profile_uc.save_profile(
        db,
        owner,
        name="김 예시",
        email="YESI.KIM@example.com",
        phone="+82 10 0000 0001",
        links=[("GitHub", "http://github.com/yesi-example/")],
    )
    left = {(x.field, x.value) for x in await profile_uc.profile_suggestions(db, owner)}
    assert ("name", "김예시") not in left
    assert ("email", "yesi.kim@example.com") not in left
    assert ("phone", "010-0000-0001") not in left
    assert ("link", "https://github.com/yesi-example") not in left
    assert ("email", "yesi.backup@example.com") in left and ("phone", "010-0000-0002") in left


# ---------------------------------------------------------------- 3. 면접 답 의견의 근거 범위


S_API = "Python 백엔드 개발로 주문 API 지연을 800ms에서 200ms로 줄였다"
S_COPY = "정산 적재를 단계별로 분리하고 COPY 적재로 배치 시간을 3시간에서 40분으로 줄였다"
S_UNCONFIRMED = "확인 전: COPY 적재 단계 분리로 야간 배치를 없앴다"
S_RESTRICTED = "비공개: COPY 적재 단계 분리로 결제 정산을 옮겼다"
S_OTHER_WS = "B 공간: COPY 적재 단계 분리"
ANSWER = (
    "주문 API를 Python으로 고쳐 지연을 줄였습니다. 정산 쪽에서는 적재를 단계로 분리하고 "
    "COPY 적재를 써서 배치 시간을 크게 줄였습니다."
)


class FakeLLM:
    def __init__(self):
        self.requests = []

    async def complete(self, model, request):
        self.requests.append(request)
        return ModelCompletion(
            text=json.dumps({"points": [{"kind": "structure", "text": "결론을 먼저 말하세요."}]}),
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, TokenUsage(input_tokens=100, output_tokens=20)),),
        )


async def sourced_claim(db, blobs, actor, exp, statement, *, data_class="private", **kw):
    await upload(db, blobs, actor, statement, title="합성 경력 기술서", data_class=data_class)
    async with db.workspace_session(actor.workspace_id) as s:
        block = await s.scalar(select(DocumentBlock).where(DocumentBlock.text == statement))
    span = await brain.create_span(db, actor, block.id, 0, len(statement))
    return await brain.add_claim(
        db,
        actor,
        exp,
        statement=statement,
        kind=kw.get("kind", "result"),
        contribution=kw.get("contribution", "personal"),
        span_ids=[span],
    )


async def saved_posting(db, actor):
    url = f"https://jobs.example.test/fb/{uuid4().hex[:6]}"
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
            "questions": [{"prompt": "성능을 개선한 경험을 작성해 주세요.", "char_limit": 700}],
            "requirements": ["Python 백엔드 개발"],
            "preferred": [],
        },
    )
    return p.id


def test_feedback_queries_split_long_answers():
    qs = ip.feedback_queries("어떤 경험이 있습니까?", ANSWER)
    assert qs[0] == "어떤 경험이 있습니까?"
    words = " ".join(qs[1:]).split()
    assert "COPY" in words and "적재" in words and "Python" in words
    assert "있습니다" not in words and all(len(w) >= 2 for w in words)
    long = " ".join(f"낱말{i}" for i in range(200))
    assert len(ip.feedback_queries("", long)) == ip.FEEDBACK_ANSWER_QUERIES


async def test_feedback_sends_confirmed_claims_from_other_experiences(db, blobs, people):
    owner, other = people["owner"], people["other"]
    posting = await saved_posting(db, owner)
    e_api = await brain.create_experience(db, owner, title="주문 서비스 개선")
    c_api = await sourced_claim(db, blobs, owner, e_api, S_API)
    e_copy = await brain.create_experience(db, owner, title="정산 데이터 적재")
    await sourced_claim(db, blobs, owner, e_copy, S_COPY, kind="action")
    e_draft = await brain.create_experience(db, owner, title="야간 배치 정리")
    await sourced_claim(db, blobs, owner, e_draft, S_UNCONFIRMED, contribution="unknown")
    e_secret = await brain.create_experience(db, owner, title="결제 정산 이전")
    await sourced_claim(db, blobs, owner, e_secret, S_RESTRICTED, data_class="restricted")
    await upload(db, blobs, owner, "참고 글: COPY 적재 단계 분리 REF_TEXT", purpose="reference")
    e_b = await brain.create_experience(db, other, title="B 공간 경험")
    await sourced_claim(db, blobs, other, e_b, S_OTHER_WS)

    aid = await app_uc.create_application(db, owner, posting)
    view = await ip.generate(db, None, owner, aid)
    q = next(q for q in view.questions if q.source_kind == ip.KIND_POSTING)
    suggested = {ev["claim_id"] for c in q.suggestions for ev in c["evidence"]}
    assert str(c_api) in suggested  # 질문 후보에는 정산 경험이 없다
    assert not any(S_COPY == ev["statement"] for c in q.suggestions for ev in c["evidence"])
    await ip.set_answer(db, owner, aid, q.id, ANSWER)

    llm = FakeLLM()
    gw = ModelGateway(
        db,
        CONFIG,
        PriceBook.load(),
        client_factory=lambda ep, key: llm,
        env={"CLIPROXY_API_KEY": "k"},
    )
    fb = await ip.answer_feedback(db, gw, owner, aid, q.id)
    assert fb.points == [{"kind": "structure", "text": "결론을 먼저 말하세요."}]
    [request] = llm.requests  # 임베딩 라우트가 없어 키워드 검색만 했다 (모델 호출은 의견 하나)
    body = request.messages[0].content
    data = json.loads(body.split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])
    claims = data["confirmed_claims"]
    assert claims[0] == S_API  # 질문 후보의 근거가 먼저
    assert S_COPY in claims  # 다른 경험의 확인한 사실
    for absent in (S_UNCONFIRMED, S_RESTRICTED, S_OTHER_WS, "REF_TEXT"):
        assert absent not in body
    assert len(claims) == len(set(claims)) <= ip.FEEDBACK_MAX_CLAIMS
    assert request.data_classes == frozenset({"private"})

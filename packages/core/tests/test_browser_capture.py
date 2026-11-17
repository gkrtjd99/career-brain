"""보고 있는 페이지 보내기 (c0043, plan §8): 내 브라우저에서 보낸 공고·자료.

- 공고: 받은 HTML·글로 같은 추출·버전 규칙을 탄다. 서버는 어떤 주소에도 요청하지 않는다 (fetcher를 부르면
  시험이 실패한다). 버전 method는 ``browser_capture``, 모델에는 ``private`` 등급으로 보낸다.
- 자료: Markdown 원본(``text/markdown``)으로 넣고 원래 주소를 ``origin_url``에 둔다. 참고 자료가 기본,
  내 경험 자료도 되고 증빙은 받지 않는다. 참고 자료는 근거가 되지 않는다.
- 크기 상한, 공간 분리. 페이지는 모두 합성이고 네트워크·실제 모델을 쓰지 않는다.
"""

import json

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import JobFetchRun, JobPosting, JobPostingVersion, Source, SourceVersion
from career_core.domain.brain import ClaimRuleError
from career_core.domain.postings import PostingRuleError
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import parsing_handlers, run_until_idle
from career_core.llm import ModelCallError, ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext
from career_core.uploads import UploadRejected
from career_core.usecases import brain, sources
from career_core.usecases import postings as uc
from career_core.usecases.brain import NotFound
from career_core.usecases.extraction import request_extraction
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import func, select, text

pytestmark = pytest.mark.anyio

URL = "https://login-only.example.test/jobs/42?utm_source=mail"
CANONICAL = "https://login-only.example.test/jobs/42"

# 로그인해야 보이는 공고 페이지 (합성): 머리에 로그인한 사람 이름, 본문에 JSON-LD
POSTING_HTML = """<!doctype html><html><head><title>데이터 분석가 - 가상물류</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",
"title":"데이터 분석가 (경력 2년 이상)","hiringOrganization":{"@type":"Organization","name":"가상물류"},
"datePosted":"2026-11-02","validThrough":"2026-12-05T18:00:00+09:00",
"description":"<p>[담당 업무]</p><ul><li>물류 지표 대시보드 운영</li><li>수요 예측 모델 개선</li></ul><p>[자격 요건]</p><ul><li>SQL 활용 능력</li><li>Python 분석 경험</li></ul>"}
</script><script>window.__track = 1;</script></head>
<body><header>홍길동님 로그인 중 · 내 지원 현황</header><main><h1>데이터 분석가 (경력 2년 이상)</h1>
<p>[담당 업무]</p><ul><li>물류 지표 대시보드 운영</li><li>수요 예측 모델 개선</li></ul>
<p>[자격 요건]</p><ul><li>SQL 활용 능력</li><li>Python 분석 경험</li></ul>
<p>[근무지] 경기 이천</p><img src="https://tracker.example.test/p.gif" onerror="alert(1)">
</main></body></html>"""

# 스크립트로 그리는 공고: 구조화 데이터 없이 본문만
PLAIN_HTML = (
    "<html><head><title>가상마켓 | 프론트엔드 개발자</title></head><body><div id='app'>"
    "<h1>가상마켓 | 프론트엔드 개발자</h1><p>[근무지] 서울 성수동</p>"
    "<p>[자격 요건] React 경험, TypeScript 이해</p>"
    + "<p>합성 공고 본문입니다. 화면은 스크립트로 그려졌습니다.</p>" * 8
    + "</div></body></html>"
)

LISTING_HTML = (
    "<html><body><main><h1>채용 공고 목록</h1>"
    + "".join(
        f'<div class="card"><a href="/jobs/{900 + i}">가상기업{i} 백엔드 개발자 채용</a></div>'
        for i in range(10)
    )
    + "</main></body></html>"
)

LLM_REPLY = json.dumps(
    {
        "page_type": "posting",
        "fields": {
            "location": {"value": "경기 이천", "quote": "[근무지] 경기 이천"},
            "career_level": {"value": "경력 2년 이상", "quote": "데이터 분석가 (경력 2년 이상)"},
        },
    }
)


class NoFetch:
    """가져오기 경로를 부르면 실패한다 (보낸 페이지는 서버가 다시 가져오지 않는다)."""

    async def fetch(self, *args, **kwargs):
        raise AssertionError("browser capture must not fetch anything")


class ScriptedLLM:
    def __init__(self, reply=LLM_REPLY, error: ModelCallError | None = None):
        self.reply = reply
        self.error = error
        self.calls = 0
        self.seen: list[str] = []

    async def complete(self, model, request):
        self.calls += 1
        self.seen.append(request.messages[0].content)
        if self.error is not None:
            raise self.error
        return ModelCompletion(
            text=self.reply,
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, TokenUsage(input_tokens=100, output_tokens=50)),),
        )


def config(classes: str):
    return parse_models_config(f"""
[endpoints.proxy]
preset = "cliproxyapi"
price_as = "openai"
data_classes = {classes}
[routes]
default = "proxy/gpt-6.1-sol"
""")


def gateway(db, llm, classes='["public", "private"]'):
    return ModelGateway(
        db,
        config(classes),
        PriceBook.load(),
        client_factory=lambda ep, key: llm,
        env={"CLIPROXY_API_KEY": "k"},
    )


@pytest.fixture
async def people(db):
    ws_a, a = await create_workspace(db, name="A (합성)", owner_display_name="amy")
    ws_b, b = await create_workspace(db, name="B (합성)", owner_display_name="ben")
    return {
        "owner": ActorContext(a, ws_a, scopes_for_role(Role.OWNER)),
        "viewer": ActorContext(a, ws_a, scopes_for_role(Role.VIEWER)),
        "other": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
    }


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blobs")


async def versions(db, actor, posting_id):
    async with db.workspace_session(actor.workspace_id) as s:
        return list(
            await s.scalars(
                select(JobPostingVersion)
                .where(JobPostingVersion.posting_id == posting_id)
                .order_by(JobPostingVersion.version_no)
            )
        )


async def count(db, actor, model, *where):
    async with db.workspace_session(actor.workspace_id) as s:
        return await s.scalar(select(func.count()).select_from(model).where(*where))


# ---------------------------------------------------------------- 공고


async def test_capture_posting_uses_same_extraction_without_fetching(db, people):
    owner = people["owner"]
    llm = ScriptedLLM()
    deps = uc.PostingDeps(db=db, fetcher=NoFetch(), gateway=gateway(db, llm), browser=NoFetch())

    got = await uc.capture_page(deps, owner, URL, html=POSTING_HTML, title="데이터 분석가")
    assert (got.status, got.created, got.unchanged, got.error_code) == ("done", True, False, None)

    view = await uc.get_posting(db, owner, got.posting_id)
    assert view.canonical_url == CANONICAL and view.status == "draft"
    assert view.latest.method == "browser_capture"
    assert view.latest.adapter == "json_ld"
    # 구조화 데이터가 이기고, 모델은 빈 항목만 채운다 (인용 확인)
    found = {k: x["value"] for k, x in view.latest.extracted.items()}
    assert found["title"] == "데이터 분석가 (경력 2년 이상)"
    assert found["company"] == "가상물류"
    assert found["location"] == "경기 이천"
    assert view.runs[0].notes[0] == "browser_capture"
    # 스크립트·이벤트 속성은 본문에 들어가지 않는다 (렌더링하지 않고 글만 고른다)
    [v] = await versions(db, owner, got.posting_id)
    assert "alert(1)" not in v.text and "__track" not in v.text
    assert llm.calls == 1 and "alert(1)" not in llm.seen[0]

    # 같은 페이지를 다시 보내면 버전을 늘리지 않고 모델도 부르지 않는다
    again = await uc.capture_page(deps, owner, CANONICAL, html=POSTING_HTML)
    assert (again.posting_id, again.created, again.unchanged) == (got.posting_id, False, True)
    assert llm.calls == 1
    assert len(await versions(db, owner, got.posting_id)) == 1


async def test_capture_is_private_to_models(db, people):
    """로그인한 화면일 수 있어 private 등급으로 보낸다: public만 받는 연결은 부르지 않는다."""
    owner = people["owner"]
    llm = ScriptedLLM()
    deps = uc.PostingDeps(db=db, fetcher=None, gateway=gateway(db, llm, '["public"]'))
    got = await uc.capture_page(deps, owner, URL, html=POSTING_HTML)
    assert got.status == "done" and llm.calls == 0
    view = await uc.get_posting(db, owner, got.posting_id)
    assert any(n.startswith("llm_denied:") for n in view.runs[0].notes)
    # 구조화 데이터 항목은 그대로 남는다
    found = view.latest.extracted
    assert found["company"]["value"] == "가상물류" and "location" not in found


async def test_capture_joins_saved_url_and_marks_changes(db, people):
    """서버가 가져오지 못한 공고(로그인 필요)에 보낸 페이지가 새 버전으로 더해진다."""
    owner = people["owner"]
    [saved] = await uc.save_urls(db, owner, [URL])
    deps = uc.PostingDeps(db=db, fetcher=None, gateway=gateway(db, ScriptedLLM()))
    got = await uc.capture_page(deps, owner, URL, html=PLAIN_HTML)
    assert (got.posting_id, got.created) == (saved.posting_id, False)
    await uc.confirm_posting(db, owner, got.posting_id, overrides={"title": "프론트엔드 개발자"})

    newer = PLAIN_HTML.replace("서울 성수동", "서울 강남구")
    third = await uc.capture_page(deps, owner, URL, html=newer)
    assert third.status == "done" and not third.unchanged
    view = await uc.get_posting(db, owner, got.posting_id)
    assert view.status == "saved"
    assert [v.version_no for v in await versions(db, owner, got.posting_id)] == [1, 2]


async def test_capture_selection_is_the_body(db, people):
    owner = people["owner"]
    deps = uc.PostingDeps(db=db, fetcher=None, gateway=None)
    selection = "[자격 요건] SQL 활용 능력\n\n\n[근무지] 경기 이천\n" + "선택한 부분입니다. " * 10
    got = await uc.capture_page(deps, owner, URL, html=POSTING_HTML, text=selection)
    [v] = await versions(db, owner, got.posting_id)
    assert v.text.startswith("[자격 요건] SQL 활용 능력")
    assert "홍길동님" not in v.text  # 선택하지 않은 머리글은 들어가지 않는다
    assert v.extracted["company"]["value"] == "가상물류"  # 구조화 데이터는 HTML에서

    # 휴대폰 공유: HTML 없이 글만
    shared = await uc.capture_page(
        deps, owner, "https://share.example.test/p/1", text=selection, title="공유한 공고"
    )
    [v2] = await versions(db, owner, shared.posting_id)
    assert v2.method == "browser_capture" and v2.adapter == "text"


async def test_capture_listing_page_keeps_candidates(db, people):
    owner = people["owner"]
    deps = uc.PostingDeps(db=db, fetcher=None, gateway=None)
    got = await uc.capture_page(deps, owner, "https://list.example.test/jobs", html=LISTING_HTML)
    assert got.status == "listing" and got.version_id is None
    view = await uc.get_posting(db, owner, got.posting_id)
    assert len(view.runs[0].candidates) == 10
    assert view.runs[0].candidates[0]["url"].startswith("https://list.example.test/jobs/")


async def test_capture_limits_and_rejections_create_nothing(db, people):
    owner = people["owner"]
    deps = uc.PostingDeps(db=db, fetcher=None, gateway=None)
    cases = [
        ("javascript:alert(1)", {"html": POSTING_HTML}, "invalid_url"),
        ("https://user:pw@a.example.test/x", {"html": POSTING_HTML}, "invalid_url"),
        (URL, {"html": "x" * (uc.MAX_CAPTURE_HTML + 1)}, "html_too_large"),
        (URL, {"text": "가" * (uc.MAX_MANUAL_CHARS + 1)}, "text_too_long"),
        (URL, {}, "content_required"),
        (URL, {"html": "   ", "text": "  "}, "content_required"),
        (URL, {"html": "<html><body><p>로그인</p></body></html>"}, "capture_too_short"),
    ]
    for url, kwargs, code in cases:
        with pytest.raises(PostingRuleError, match=code):
            await uc.capture_page(deps, owner, url, **kwargs)
    assert await count(db, owner, JobPosting) == 0
    assert await count(db, owner, JobFetchRun) == 0
    # 상한 바로 아래는 받는다
    edge = PLAIN_HTML + " " * (uc.MAX_CAPTURE_HTML - len(PLAIN_HTML))
    assert (await uc.capture_page(deps, owner, URL, html=edge)).status == "done"


async def test_capture_model_busy_closes_run(db, people):
    owner = people["owner"]
    llm = ScriptedLLM(error=ModelCallError("upstream_timeout", retryable=True))
    deps = uc.PostingDeps(db=db, fetcher=None, gateway=gateway(db, llm))
    with pytest.raises(ModelCallError):
        await uc.capture_page(deps, owner, URL, html=PLAIN_HTML)
    async with db.workspace_session(owner.workspace_id) as s:
        run = await s.scalar(select(JobFetchRun))
    assert run.status == "failed" and run.error_code == "needs_manual_check"
    assert await count(db, owner, JobPostingVersion) == 0
    # 다시 보내면 된다 (모델이 돌아오면)
    llm.error = None
    assert (await uc.capture_page(deps, owner, URL, html=PLAIN_HTML)).status == "done"


async def test_capture_workspace_isolation(db, people):
    owner, other = people["owner"], people["other"]
    deps = uc.PostingDeps(db=db, fetcher=None, gateway=None)
    mine = await uc.capture_page(deps, owner, URL, html=PLAIN_HTML)
    with pytest.raises(NotFound):
        await uc.get_posting(db, other, mine.posting_id)
    assert [p.id for p in await uc.list_postings(db, other)] == []
    # 다른 공간의 같은 주소는 그 공간의 새 공고다
    theirs = await uc.capture_page(deps, other, URL, html=PLAIN_HTML)
    assert theirs.created and theirs.posting_id != mine.posting_id
    assert await count(db, owner, JobPosting) == 1
    assert await count(db, other, JobPosting) == 1


# ---------------------------------------------------------------- 자료 (참고·내 경험)


async def test_capture_source_markdown_with_origin(db, blobs, people):
    owner = people["owner"]
    got = await sources.capture_text_source(
        db,
        blobs,
        owner,
        url="https://blog.example.test/posts/7?utm_campaign=x#top",
        title="  면접 준비   글  ",
        markdown="## 질문 목록\r\n\r\n- 지원 동기\n- 협업 경험\x00",
        max_bytes=1 << 20,
    )
    assert (got.purpose, got.data_class, got.purpose_pending) == ("reference", "private", False)
    async with db.workspace_session(owner.workspace_id) as s:
        src = await s.get(Source, got.source_id)
        ver = await s.get(SourceVersion, got.version_id)
        assert src.origin_url == "https://blog.example.test/posts/7"
        assert src.title == "면접 준비 글"
        assert (ver.mime_type, ver.original_filename) == ("text/markdown", "면접 준비 글.md")
    data = await blobs.get(owner.workspace_id, ver.blob_key)
    body = data.decode()
    assert body.startswith("# 면접 준비 글\n\n출처: <https://blog.example.test/posts/7>\n\n## 질문")
    assert "\r" not in body and "\x00" not in body

    # 같은 글을 다시 보내면 처음 원본 (용도도 처음 그대로)
    again = await sources.capture_text_source(
        db,
        blobs,
        owner,
        url="https://blog.example.test/posts/7",
        title="면접 준비 글",
        markdown="## 질문 목록\n\n- 지원 동기\n- 협업 경험",
        purpose="experience",
        max_bytes=1 << 20,
    )
    assert again.deduplicated and again.source_id == got.source_id and again.purpose == "reference"


async def test_capture_source_purpose_rules(db, blobs, people):
    owner = people["owner"]

    async def send(purpose, markdown="내가 쓴 회고 글입니다. 주문 서비스 성능을 개선했습니다."):
        return await sources.capture_text_source(
            db,
            blobs,
            owner,
            url="https://notes.example.test/me/1",
            title="회고",
            markdown=markdown,
            purpose=purpose,
            max_bytes=1 << 20,
        )

    with pytest.raises(UploadRejected, match="capture_proof_not_allowed"):
        await send("proof")
    with pytest.raises(UploadRejected, match="invalid_purpose"):
        await send("diary")
    mine = await send("experience")
    assert mine.purpose == "experience"
    ref = await send("reference", markdown="남이 쓴 합격 후기입니다. 플라밍고 프로젝트 이야기.")
    assert ref.purpose == "reference"
    assert await count(db, owner, Source, Source.purpose == "proof") == 0

    await run_until_idle(
        db,
        parsing_handlers(
            ParsingDeps(
                db=db,
                blobs=blobs,
                egress=ProviderEgressPolicy(),
                local_parsers=default_local_parsers(),
            )
        ),
    )
    # 내 경험 자료는 경험 추출을 요청할 수 있다. 참고 자료는 추출하지 않고 근거가 되지 않는다
    assert await request_extraction(db, owner, mine.parse_run_id)
    with pytest.raises(ClaimRuleError, match="source_not_experience"):
        await request_extraction(db, owner, ref.parse_run_id)
    async with db.workspace_session(owner.workspace_id) as s:
        block_id = await s.scalar(
            text("SELECT id FROM core.document_blocks WHERE parse_run_id = :r LIMIT 1"),
            {"r": ref.parse_run_id},
        )
    assert block_id is not None
    with pytest.raises(ClaimRuleError, match="source_not_experience"):
        await brain.create_span(db, owner, block_id, 0, 3)


async def test_capture_source_limits(db, blobs, people):
    owner = people["owner"]

    async def send(**kw):
        args = {"url": "https://a.example.test/x", "title": "t", "markdown": "본문", **kw}
        return await sources.capture_text_source(db, blobs, owner, max_bytes=1 << 20, **args)

    for kw, code in (
        ({"url": "file:///etc/passwd"}, "invalid_url"),
        ({"url": "https://a.example.test/" + "x" * 2100}, "invalid_url"),
        ({"markdown": "  \n "}, "empty"),
        ({"markdown": "가" * (sources.MAX_CAPTURE_TEXT + 1)}, "text_too_long"),
    ):
        with pytest.raises(UploadRejected, match=code):
            await send(**kw)
    # 서버 업로드 상한도 지킨다
    with pytest.raises(UploadRejected, match="too_large"):
        await sources.capture_text_source(
            db,
            blobs,
            owner,
            url="https://a.example.test/x",
            title="t",
            markdown="가" * 2000,
            max_bytes=1024,
        )
    assert await count(db, owner, Source) == 0


# ---------------------------------------------------------------- HTTP


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def client(db, blobs, people):
    app = FastAPI()
    tokens = {"owner": people["owner"], "viewer": people["viewer"], "other": people["other"]}
    deps = CoreDeps(
        identity=Tokens(tokens),
        db=db,
        blobs=blobs,
        max_upload_bytes=1 << 20,
        models=gateway(db, ScriptedLLM()),
    )
    app.include_router(build_router(deps))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        yield c


async def test_capture_http(client, db, people):
    body = {"url": URL, "title": "데이터 분석가", "html": POSTING_HTML}
    r = await client.post("/core/job-postings/capture", json=body, headers=auth("viewer"))
    assert r.status_code == 403
    r = await client.post("/core/job-postings/capture", json=body, headers=auth("owner"))
    assert r.status_code == 201, r.text
    got = r.json()
    assert (got["status"], got["created"], got["unchanged"]) == ("done", True, False)
    detail = await client.get(f"/core/job-postings/{got['posting_id']}", headers=auth("owner"))
    assert detail.json()["latest"]["method"] == "browser_capture"
    other = await client.get(f"/core/job-postings/{got['posting_id']}", headers=auth("other"))
    assert other.status_code == 404

    for bad, code in (
        ({**body, "url": "javascript:alert(1)"}, 422),
        ({**body, "html": "x" * (uc.MAX_CAPTURE_HTML + 1)}, 422),
        ({"url": URL}, 422),
    ):
        r = await client.post("/core/job-postings/capture", json=bad, headers=auth("owner"))
        assert r.status_code == code
    r = await client.post("/core/job-postings/capture", json={"url": URL}, headers=auth("owner"))
    assert r.json()["detail"] == "content_required"

    src = {"url": "https://blog.example.test/a", "title": "참고 글", "markdown": "# 참고\n\n본문"}
    r = await client.post("/core/sources/capture", json=src, headers=auth("viewer"))
    assert r.status_code == 403
    r = await client.post("/core/sources/capture", json=src, headers=auth("owner"))
    assert r.status_code == 201, r.text
    assert r.json()["purpose"] == "reference"
    sid = r.json()["source_id"]
    shown = (await client.get(f"/core/sources/{sid}", headers=auth("owner"))).json()
    assert shown["origin_url"] == "https://blog.example.test/a"
    assert shown["versions"][0]["mime_type"] == "text/markdown"
    assert (await client.get(f"/core/sources/{sid}", headers=auth("other"))).status_code == 404
    r = await client.post(
        "/core/sources/capture", json={**src, "purpose": "proof"}, headers=auth("owner")
    )
    assert (r.status_code, r.json()["detail"]) == (422, "capture_proof_not_allowed")
    r = await client.post(
        "/core/sources/capture",
        json={**src, "markdown": "가" * (sources.MAX_CAPTURE_TEXT + 1)},
        headers=auth("owner"),
    )
    assert r.status_code == 422


async def test_capture_http_model_busy(db, blobs, people):
    app = FastAPI()
    llm = ScriptedLLM(error=ModelCallError("upstream_timeout", retryable=True))
    deps = CoreDeps(
        identity=Tokens({"owner": people["owner"]}),
        db=db,
        blobs=blobs,
        models=gateway(db, llm),
    )
    app.include_router(build_router(deps))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        r = await c.post(
            "/core/job-postings/capture",
            json={"url": URL, "html": PLAIN_HTML},
            headers=auth("owner"),
        )
    assert (r.status_code, r.json()["detail"]) == (503, "model_unavailable")

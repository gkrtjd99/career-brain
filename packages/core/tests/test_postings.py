"""O06 완료 기준: 같은 URL 중복 없음, 원문 근거 있는 항목 추출·미확인 항목 비움,
다시 가져오기 무변경 시 버전 없음, 실패를 마감으로 처리하지 않음. (모든 페이지는 합성)"""

import json
from datetime import timedelta

import httpx
import pytest
from career_core.adapters.html_extract import extract_page, listing_candidates
from career_core.adapters.web_fetch import AddressBlocked, HttpFetcher
from career_core.db import Job, JobPosting, JobPostingVersion
from career_core.db.base import utcnow
from career_core.domain.postings import (
    PostingRuleError,
    date_digits_in_quote,
    normalize_url,
)
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import posting_handlers, run_until_idle
from career_core.llm import ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.ports import ActorContext
from career_core.usecases import postings as uc
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.exc import ProgrammingError

pytestmark = pytest.mark.anyio

JSON_LD_PAGE = """<!doctype html><html><head><title>백엔드 개발자 - 가상회사</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",
"title":"백엔드 개발자 (신입)","hiringOrganization":{"@type":"Organization","name":"가상회사"},
"jobLocation":{"@type":"Place","address":{"addressRegion":"서울","addressLocality":"강남구"}},
"employmentType":"FULL_TIME","datePosted":"2026-11-01","validThrough":"2026-11-30T23:59:00+09:00",
"description":"<p>[담당 업무]</p><ul><li>주문 API 개발</li><li>배치 작업 운영</li></ul><p>[자격 요건]</p><ul><li>Python 또는 Go 경험</li><li>SQL 이해</li></ul><p>[우대 사항]</p><ul><li>AWS 운영 경험</li></ul><p>[전형 절차] 서류 - 코딩테스트 - 면접</p><p>[자기소개서] 지원 동기를 작성해 주세요. (500자 이내)</p>"}
</script></head><body><nav>메뉴 채용 회사소개</nav><main><h1>백엔드 개발자 (신입)</h1>
<p>[담당 업무]</p><ul><li>주문 API 개발</li><li>배치 작업 운영</li></ul>
<p>[자격 요건]</p><ul><li>Python 또는 Go 경험</li><li>SQL 이해</li></ul>
<p>[우대 사항]</p><ul><li>AWS 운영 경험</li></ul>
<p>[전형 절차] 서류 - 코딩테스트 - 면접</p>
<p>[자기소개서] 지원 동기를 작성해 주세요. (500자 이내)</p></main><footer>회사 주소</footer>
</body></html>"""

NINEHIRE_PAGE = """<html><head><meta property="og:title" content="[신입] 데이터 엔지니어 | 가상테크">
</head><body><div id="__next"><p>가상테크 데이터 엔지니어 지원하기</p></div>
<script id="__NEXT_DATA__" type="application/json">{}</script></body></html>""".format(
    json.dumps(
        {
            "props": {
                "pageProps": {
                    "recruitment": {
                        "recruitmentId": 1,
                        "externalTitle": "[신입] 데이터 엔지니어",
                        "affiliation": {"title": "가상테크"},
                        "employmentType": ["full_time"],
                        "career": {"type": "newcomer"},
                        "deadlineType": "custom_deadline",
                        "reception": {
                            "startAt": "2026-11-01T00:00:00Z",
                            "endAt": "2026-11-20T14:59:59Z",
                        },
                        "jobLocations": [{"placeName": "가상테크 판교 사옥"}],
                    },
                    "jobPosting": {
                        "content": "<h4>[담당 업무]</h4><p>데이터 파이프라인 구축과 운영</p>"
                        "<p>로그 수집 시스템 개선</p><h4>[자격 요건]</h4><p>SQL 활용 가능자</p>"
                        "<p>Airflow 사용 경험 우대</p><h4>[채용 절차]</h4><p>서류전형 - 면접</p>"
                        + "<p>가상테크는 합성 데이터로 만든 회사입니다.</p>"
                        * 3
                    },
                }
            }
        },
        ensure_ascii=False,
    )
)

ARTICLE_PAGE = (
    """<html><head><title>가상스튜디오｜프론트엔드 인턴</title></head><body>
<header class="site-header"><a href="/">홈</a><a href="/it">IT</a></header>
<div class="entry-content"><h1>가상스튜디오｜프론트엔드 인턴</h1>
<p>[담당 업무] 웹 화면 개발</p><p>[자격 요건] React 경험, TypeScript 이해</p>
<p>[접수 기간] 2026.11.10 ~ 2026.11.24 23:59</p><p>[근무지] 서울 성수동</p>
<p>인턴 기간 3개월, 평가 후 정규직 전환 검토</p>"""
    + "<p>합성 공고 본문입니다.</p>" * 10
    + """
</div><aside class="sidebar"><a href="/archives/1">다른 공고</a></aside></body></html>"""
)

LISTING_PAGE = (
    "<html><body><main><h1>IT 공고</h1>"
    + "".join(
        f'<div class="card"><a href="/archives/{1000 + i}">가상기업{i}｜백엔드 개발자 채용</a></div>'
        for i in range(12)
    )
    + "</main></body></html>"
)

LLM_REPLY = json.dumps(
    {
        "page_type": "posting",
        "fields": {
            "title": {
                "value": "가상스튜디오｜프론트엔드 인턴",
                "quote": "가상스튜디오｜프론트엔드 인턴",
            },
            "company": {"value": "가상스튜디오", "quote": "가상스튜디오｜프론트엔드 인턴"},
            "location": {"value": "서울 성수동", "quote": "[근무지] 서울 성수동"},
            "deadline": {"value": "2026-11-24T23:59", "quote": "2026.11.10 ~ 2026.11.24 23:59"},
            "career_level": {
                "value": "경력 3년 이상",
                "quote": "인턴 기간 3개월",
            },  # 원문에 없는 값
            "employment_type": {
                "value": "인턴",
                "quote": "정규직 전환 검토 인턴",
            },  # 원문에 없는 인용
            "requirements": [
                {"quote": "React 경험, TypeScript 이해"},
                {"quote": "Kubernetes 운영"},
            ],
            "responsibilities": [{"quote": "[담당 업무] 웹 화면 개발"}],
        },
    }
)

PAGES = {
    "jobs.example.test": ("/b/1", JSON_LD_PAGE),
    "careers.example.test": ("/job_posting/abc", NINEHIRE_PAGE),
    "board.example.test": ("/archives/7", ARTICLE_PAGE),
    "list.example.test": ("/it", LISTING_PAGE),
}
IPS = {
    "jobs.example.test": "93.184.215.10",
    "careers.example.test": "93.184.215.11",
    "board.example.test": "93.184.215.12",
    "list.example.test": "93.184.215.13",
    "login.example.test": "93.184.215.14",
    "redirect.example.test": "93.184.215.15",
    "big.example.test": "93.184.215.16",
    "gone.example.test": "93.184.215.17",
    "internal.example.test": "10.0.0.5",
}


class FakeSite:
    def __init__(self):
        self.pages = dict(PAGES)
        self.requests: list[tuple[str, str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        self.requests.append((host, str(request.url), request.headers.get("cookie", "")))
        assert request.url.host == IPS[host]  # 확인한 IP로만 접속한다
        assert request.extensions.get("sni_hostname") == host
        if host == "login.example.test":
            return httpx.Response(403, html="<form>로그인이 필요합니다</form>")
        if host == "redirect.example.test":
            return httpx.Response(302, headers={"location": "http://internal.example.test/admin"})
        if host == "big.example.test":
            return httpx.Response(
                200, content=b"x" * (1024 * 100), headers={"content-type": "text/html"}
            )
        if host == "gone.example.test":
            return httpx.Response(404, html="not found")
        _path, body = self.pages[host]
        return httpx.Response(200, html=body)


async def fake_resolve(host, port):
    ip = IPS.get(host)
    if ip is None:
        raise ConnectionError("dns_failed")
    if ip.startswith("10."):
        raise AddressBlocked(host)
    return ip


class ScriptedLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def complete(self, model, request):
        self.calls += 1
        return ModelCompletion(
            text=self.reply,
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, TokenUsage(input_tokens=100, output_tokens=50)),),
        )


CONFIG = parse_models_config("""
[endpoints.proxy]
preset = "cliproxyapi"
price_as = "openai"
data_classes = ["public"]
[routes]
default = "proxy/gpt-6.1-sol"
""")


@pytest.fixture
async def env(db):
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    site = FakeSite()
    llm = ScriptedLLM(LLM_REPLY)
    gw = ModelGateway(
        db,
        CONFIG,
        PriceBook.load(),
        client_factory=lambda ep, key: llm,
        env={"CLIPROXY_API_KEY": "k"},
    )
    fetcher = HttpFetcher(
        transport=httpx.MockTransport(site.handler), resolver=fake_resolve, max_bytes=1024 * 64
    )
    deps = uc.PostingDeps(db=db, fetcher=fetcher, gateway=gw)
    return {
        "owner": ActorContext(a, ws_a, scopes_for_role(Role.OWNER)),
        "viewer": ActorContext(a, ws_a, scopes_for_role(Role.VIEWER)),
        "other": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
        "site": site,
        "llm": llm,
        "deps": deps,
        "gateway": gw,
    }


async def work(env):
    return await run_until_idle(env["deps"].db, posting_handlers(env["deps"]))


# --- 규칙 ---------------------------------------------------------------------


def test_normalize_url():
    assert normalize_url("Jobs.Example.TEST/b/1/?utm_source=x&gclid=1#apply") == (
        "https://jobs.example.test/b/1"
    )
    assert normalize_url("https://a.test/x?b=2&a=1") == normalize_url("https://a.test/x?a=1&b=2")
    for bad in ("", "ftp://a.test/x", "https://user:pw@a.test/", "javascript:alert(1)"):
        with pytest.raises(PostingRuleError):
            normalize_url(bad)


def test_date_digits_and_listing_detection():
    assert date_digits_in_quote("2026-11-24", "2026.11.10 ~ 2026.11.24 23:59")
    assert not date_digits_in_quote("2026-12-24", "2026.11.10 ~ 2026.11.24")
    page = extract_page(LISTING_PAGE, "https://list.example.test/it")
    assert len(listing_candidates(page, "https://list.example.test/it")) == 12
    article = extract_page(ARTICLE_PAGE, "https://board.example.test/archives/7")
    assert listing_candidates(article, "https://board.example.test/archives/7") == []
    assert "다른 공고" not in article.main_text and "[근무지] 서울 성수동" in article.main_text


# --- 저장·추출 ------------------------------------------------------------------


async def test_same_url_saved_once(db, env):
    owner = env["owner"]
    urls = [
        "https://jobs.example.test/b/1",
        "jobs.example.test/b/1/?utm_campaign=z",
        "https://JOBS.example.test/b/1#top",
    ]
    results = await uc.save_urls(db, owner, urls)
    assert len({r.posting_id for r in results}) == 1
    assert [r.deduplicated for r in results] == [False, True, True]
    for _ in range(10):
        await uc.save_urls(db, owner, ["https://jobs.example.test/b/1"])
    async with db.workspace_session(owner.workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(JobPosting)) == 1
        assert (
            await s.scalar(select(func.count()).select_from(Job).where(Job.kind == uc.FETCH_JOB))
            == 1
        )
    # 다른 공간은 같은 URL이어도 별개
    other = await uc.save_urls(db, env["other"], ["https://jobs.example.test/b/1"])
    assert other[0].posting_id != results[0].posting_id and not other[0].deduplicated


async def test_json_ld_posting(db, env):
    owner = env["owner"]
    [r] = await uc.save_urls(db, owner, ["https://jobs.example.test/b/1"])
    await work(env)
    view = await uc.get_posting(db, owner, r.posting_id)
    v = view.latest
    assert v.adapter == "json_ld" and v.method == "http"
    ex = v.extracted
    assert ex["title"]["value"] == "백엔드 개발자 (신입)"
    assert ex["title"]["evidence"] == {"kind": "structured", "ref": "ld:JobPosting.title"}
    assert ex["company"]["value"] == "가상회사"
    assert ex["deadline"]["value"] == "2026-11-30T23:59:00+09:00"
    assert ex["location"]["value"] == "서울 강남구"
    assert v.missing == []
    # 쿠키를 보내지 않는다
    assert all(cookie == "" for _, _, cookie in env["site"].requests)


async def test_ninehire_posting(db, env):
    owner = env["owner"]
    [r] = await uc.save_urls(db, owner, ["https://careers.example.test/job_posting/abc"])
    await work(env)
    v = (await uc.get_posting(db, owner, r.posting_id)).latest
    assert v.adapter == "ninehire"
    ex = v.extracted
    assert (ex["title"]["value"], ex["company"]["value"]) == ("[신입] 데이터 엔지니어", "가상테크")
    assert ex["employment_type"]["value"] == "정규직"
    assert ex["career_level"]["value"] == "신입"
    assert ex["deadline"]["value"] == "2026-11-20T14:59:59+00:00"
    assert ex["opens_at"]["value"] == "2026-11-01T00:00:00+00:00"  # reception.startAt
    assert ex["opens_at"]["evidence"]["ref"] == "ninehire:recruitment.reception.startAt"
    assert ex["location"]["value"] == "가상테크 판교 사옥"
    full = await uc.get_version(db, owner, r.posting_id, v.id)
    assert "데이터 파이프라인 구축과 운영" in full.text


async def test_llm_fields_need_verified_quotes(db, env):
    owner = env["owner"]
    [r] = await uc.save_urls(db, owner, ["https://board.example.test/archives/7"])
    await work(env)
    view = await uc.get_posting(db, owner, r.posting_id)
    v = view.latest
    ex = v.extracted
    assert v.adapter == "text"
    assert ex["title"]["value"] == "가상스튜디오｜프론트엔드 인턴"
    assert ex["deadline"]["value"] == "2026-11-24T23:59"
    assert ex["deadline"]["evidence"]["kind"] == "quote"
    assert ex["location"]["value"] == "서울 성수동"
    # 원문에 없는 값·인용은 버린다
    assert "career_level" not in ex and "employment_type" not in ex
    assert ex["requirements"]["value"] == ["React 경험, TypeScript 이해"]
    assert any(n.startswith("llm_dropped:") for n in view.runs[0].notes)
    assert v.missing == []


async def test_refetch_unchanged_and_changed(db, env):
    owner = env["owner"]
    [r] = await uc.save_urls(db, owner, ["https://jobs.example.test/b/1"])
    await work(env)
    assert await uc.confirm_posting(db, owner, r.posting_id) == "saved"
    await work(env)  # 저장이 건 회사 정보 찾기 (c0053)
    calls = env["llm"].calls
    for _ in range(3):
        await uc.refetch(db, owner, r.posting_id)
        await work(env)
    assert await uc.count_versions(db, owner, r.posting_id) == 1  # 내용이 같으면 버전 없음
    assert env["llm"].calls == calls
    view = await uc.get_posting(db, owner, r.posting_id)
    assert view.runs[0].unchanged and view.pending_changes == []

    env["site"].pages["jobs.example.test"] = (
        "/b/1",
        JSON_LD_PAGE.replace("2026-11-30T23:59:00+09:00", "2026-12-07T23:59:00+09:00").replace(
            "SQL 이해", "SQL 이해와 튜닝 경험"
        ),
    )
    await uc.refetch(db, owner, r.posting_id)
    await work(env)
    view = await uc.get_posting(db, owner, r.posting_id)
    assert view.latest.version_no == 2
    assert "deadline" in view.latest.changed_fields
    assert "deadline" in view.pending_changes
    # 확정한 값은 사용자가 다시 확인하기 전까지 그대로다
    assert view.fields["deadline"] == "2026-11-30T23:59:00+09:00"
    await uc.confirm_posting(db, owner, r.posting_id)
    view = await uc.get_posting(db, owner, r.posting_id)
    assert view.fields["deadline"] == "2026-12-07T23:59:00+09:00" and view.pending_changes == []


async def test_failures_are_not_deadlines(db, env):
    owner = env["owner"]
    results = await uc.save_urls(
        db,
        owner,
        [
            "https://login.example.test/p/1",
            "https://redirect.example.test/p/2",
            "https://big.example.test/p/3",
            "https://gone.example.test/p/4",
            "https://internal.example.test/p/5",
            "https://list.example.test/it",
        ],
    )
    await work(env)
    by_url = {}
    for r in results:
        view = await uc.get_posting(db, owner, r.posting_id)
        by_url[r.url.split("/")[2].split(".")[0]] = view
    codes = {k: (v.runs[0].status, v.runs[0].error_code) for k, v in by_url.items()}
    assert codes == {
        "login": ("failed", "login_required"),
        "redirect": ("failed", "blocked_address"),  # 리다이렉트도 내부 주소면 막는다
        "big": ("failed", "too_large"),
        "gone": ("failed", "source_unavailable"),
        "internal": ("failed", "blocked_address"),
        "list": ("listing", None),
    }
    assert len(by_url["list"].runs[0].candidates) == 12
    for v in by_url.values():
        assert v.status == "draft" and "deadline" not in v.fields  # 마감으로 처리하지 않는다
    hosts = {h for h, _, _ in env["site"].requests}
    assert "internal.example.test" not in hosts


async def test_manual_text_and_confirm_overrides(db, env):
    owner = env["owner"]
    [r] = await uc.save_urls(db, owner, ["https://login.example.test/p/9"])
    await work(env)
    text = ARTICLE_PAGE.split('<div class="entry-content">')[1].split("</div>")[0]
    from career_core.adapters.html_extract import html_to_text

    await uc.add_manual_text(env["deps"], owner, r.posting_id, html_to_text(text))
    view = await uc.get_posting(db, owner, r.posting_id)
    assert view.latest.method == "manual_text"
    with pytest.raises(PostingRuleError, match="invalid_deadline"):
        await uc.confirm_posting(db, owner, r.posting_id, overrides={"deadline": "11월 말"})
    await uc.confirm_posting(
        db,
        owner,
        r.posting_id,
        overrides={
            "employment_type": "인턴",
            "questions": [{"prompt": "지원 동기", "char_limit": 700}],
        },
        clear=["location"],
    )
    view = await uc.get_posting(db, owner, r.posting_id)
    assert view.status == "saved"
    assert view.fields["employment_type"] == "인턴"
    assert view.evidence["employment_type"]["kind"] == "user"
    assert "location" not in view.fields


async def test_versions_immutable_purge_and_isolation(db, env):
    owner, other = env["owner"], env["other"]
    [r] = await uc.save_urls(db, owner, ["https://jobs.example.test/b/1"])
    [keep] = await uc.save_urls(db, owner, ["https://careers.example.test/job_posting/abc"])
    await work(env)
    await uc.confirm_posting(db, owner, keep.posting_id)
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with db.workspace_session(owner.workspace_id) as s:
            await s.execute(update(JobPostingVersion).values(text="고친 본문"))
    with pytest.raises(uc.NotFound):
        await uc.get_posting(db, other, r.posting_id)
    assert await uc.list_postings(db, other) == []
    assert await uc.purge_unconfirmed(db, owner.workspace_id) == 0
    removed = await uc.purge_unconfirmed(db, owner.workspace_id, now=utcnow() + timedelta(days=8))
    assert removed == 1  # 확정하지 않은 초안만 정리
    assert [p.id for p in await uc.list_postings(db, owner)] == [keep.posting_id]


# --- HTTP ---------------------------------------------------------------------


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


async def test_postings_http(db, env, tmp_path):
    from career_core.adapters.blobstore import LocalBlobStore

    app = FastAPI()
    app.include_router(
        build_router(
            CoreDeps(
                identity=Tokens({"owner": env["owner"], "viewer": env["viewer"]}),
                db=db,
                blobs=LocalBlobStore(tmp_path),
                max_upload_bytes=4096,
                models=env["gateway"],
            )
        )
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        owner, viewer = {"Authorization": "Bearer owner"}, {"Authorization": "Bearer viewer"}
        r = await c.post(
            "/core/job-postings", json={"urls": ["https://jobs.example.test/b/1"]}, headers=viewer
        )
        assert r.status_code == 403
        r = await c.post(
            "/core/job-postings",
            json={"urls": ["https://jobs.example.test/b/1", "ftp://x"]},
            headers=owner,
        )
        assert r.status_code == 202
        ok, bad = r.json()
        assert bad["error"] == "invalid_url"
        await work(env)
        pid = ok["posting_id"]
        r = await c.get(f"/core/job-postings/{pid}", headers=viewer)
        body = r.json()
        assert body["latest"]["extracted"]["title"]["value"] == "백엔드 개발자 (신입)"
        vid = body["latest"]["id"]
        r = await c.get(f"/core/job-postings/{pid}/versions/{vid}", headers=viewer)
        assert "주문 API 개발" in r.json()["text"]
        r = await c.post(
            f"/core/job-postings/{pid}/confirm", json={"overrides": {"title": ""}}, headers=owner
        )
        assert (r.status_code, r.json()["detail"]) == (422, "title_required")
        r = await c.post(f"/core/job-postings/{pid}/confirm", json={}, headers=owner)
        assert r.json()["status"] == "saved"
        r = await c.get("/core/job-postings", headers=viewer)
        [summary] = r.json()
        assert (summary["title"], summary["deadline"]) == (
            "백엔드 개발자 (신입)",
            "2026-11-30T23:59:00+09:00",
        )
        r = await c.post(f"/core/job-postings/{pid}/refetch", headers=owner)
        assert r.status_code == 202


def test_responsive_copies_and_related_list_are_dropped():
    body = "".join(f"<li>합성 공고 요건 {i}번: 관련 경험이 있는 분</li>" for i in range(12))
    related = "".join(
        f'<li><a href="/archives/{i}">가상기업{i}｜신입 채용 공고</a></li>' for i in range(4)
    )
    html = (
        '<html><body><div class="post-content"><div class="wrap"><p>'
        f'<div class="mobile-only"><ul>{body}</ul></div>'
        f'<div class="desktop"><div class="inner"><ul>{body}</ul></div></div></p></div>'
        f'<div class="more"><ul>{related}</ul></div></div></body></html>'
    )
    page = extract_page(html, "https://board.example.test/archives/9")
    assert page.main_text.count("합성 공고 요건 3번") == 1
    assert "가상기업1" not in page.main_text


async def test_compressed_bomb_is_capped():
    import gzip

    bomb = gzip.compress(b"<p>" + b"a" * (8 * 1024 * 1024) + b"</p>")
    assert len(bomb) < 64 * 1024

    async def stream(data):
        for i in range(0, len(data), 4096):
            yield data[i : i + 4096]

    def handler(request):
        return httpx.Response(
            200,
            content=stream(bomb),
            headers={"content-type": "text/html", "content-encoding": "gzip"},
        )

    fetcher = HttpFetcher(
        transport=httpx.MockTransport(handler), resolver=fake_resolve, max_bytes=1024 * 1024
    )
    result = await fetcher.fetch("https://jobs.example.test/b/1")
    assert result.code == "too_large"
    small = gzip.compress(JSON_LD_PAGE.encode())

    def ok(request):
        return httpx.Response(
            200,
            content=stream(small),
            headers={"content-type": "text/html", "content-encoding": "gzip"},
        )

    fetcher = HttpFetcher(transport=httpx.MockTransport(ok), resolver=fake_resolve)
    page = await fetcher.fetch("https://jobs.example.test/b/1")
    assert "백엔드 개발자" in page.text


async def test_body_size_limit_rejects_before_reading():
    from career_core.interfaces.http.limits import BodySizeLimit
    from fastapi import Request

    app = FastAPI()
    seen = []

    @app.post("/up")
    async def up(request: Request):
        seen.append(len(await request.body()))
        return {"ok": True}

    limited = BodySizeLimit(app, max_bytes=1000)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(limited), base_url="http://t") as c:
        assert (await c.post("/up", content=b"x" * 500)).status_code == 200
        assert (await c.post("/up", content=b"x" * 5000)).status_code == 413

        async def chunks():
            for _ in range(10):
                yield b"x" * 500

        assert (await c.post("/up", content=chunks())).status_code == 413
    assert seen == [500]


def _chromium_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            pw.chromium.launch().close()
    except Exception:  # noqa: BLE001
        return False
    return True


@pytest.mark.skipif(not _chromium_ready(), reason="playwright chromium not installed")
async def test_browser_never_reaches_internal_addresses():
    """Chromium은 직접 나가지 않는다: 하위 요청·리다이렉트·스크립트 요청 모두 검사된 fetcher를 거친다."""
    from career_core.adapters.web_fetch import BrowserFetcher

    seen: list[str] = []
    page = """<html><body><main><h1>합성 공고</h1><p>본문</p>
    <img src="http://internal.example.test/pixel.png">
    <script>fetch('http://internal.example.test/secret').catch(()=>{});
    fetch('https://jobs.example.test/api', {method: 'POST', body: 'x'}).catch(()=>{});
    document.body.insertAdjacentHTML('beforeend', '<p id=js>스크립트로 그린 글</p>');</script>
    </main></body></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        seen.append(f"{request.method} {host}{request.url.path}")
        if host == "redirect.example.test":
            return httpx.Response(302, headers={"location": "http://internal.example.test/admin"})
        return httpx.Response(200, html=page)

    fetcher = BrowserFetcher(
        transport=httpx.MockTransport(handler), resolver=fake_resolve, screenshot=False
    )
    result = await fetcher.fetch("https://jobs.example.test/b/1")
    assert "스크립트로 그린 글" in result.text  # 렌더링은 된다
    assert not any("internal" in s for s in seen)
    assert not any(s.startswith("POST") for s in seen)
    blocked = await fetcher.fetch("https://redirect.example.test/p")
    assert blocked.code == "blocked_address"
    assert not any("internal" in s for s in seen)

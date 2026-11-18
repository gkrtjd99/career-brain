"""회사 정보 알아서 찾기 (c0053) + 공고의 인재상·혜택·근무 조건 목록.

회사·사이트·위키데이터 응답은 모두 합성이다. 네트워크는 쓰지 않는다 (MockTransport + 가짜 DNS).
"""

import json
from uuid import uuid4

import httpx
import pytest
from career_core.adapters.web_fetch import HttpFetcher
from career_core.db import Job, JobPosting, JobPostingVersion
from career_core.db.models import Company, CompanyFact, CompanyPage
from career_core.domain.companies import name_key
from career_core.domain.company_research import (
    homepage_from_posting,
    link_score,
    page_links,
    pick_entity,
    pick_links,
    registrable_domain,
)
from career_core.domain.postings import FieldName, classify_heading
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import posting_handlers, run_until_idle
from career_core.llm import ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.ports import ActorContext
from career_core.usecases import companies as uc
from career_core.usecases import company_research as research
from career_core.usecases import postings as posting_uc
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio

# --- 규칙 (DB 없이) ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "field"),
    [
        ("[가상전자가 함께 일하고 싶은 사람]", FieldName.TALENT),
        ("인재상", FieldName.TALENT),
        ("[공통 사항]", FieldName.CONDITIONS),
        ("혜택 및 복지", FieldName.BENEFITS),
        ("[혜택 및 복지]", FieldName.BENEFITS),
        ("[채용 절차]", FieldName.PROCESS),
        ("[회사소개 영상]", None),
        ("[필수] Python 경험", None),  # 괄호로 시작해도 항목 줄은 제목이 아니다
        ("Airflow 경험 우대", None),
        ("[지원자격 및 우대사항]", None),  # 예전 목록은 그대로 엄격하게
    ],
)
def test_new_posting_headings(line, field):
    assert classify_heading(line) == field


def test_homepage_and_link_rules():
    assert registrable_domain("www.gasang.example") == "gasang.example"
    assert registrable_domain("recruit.gasang.co.kr") == "gasang.co.kr"
    assert homepage_from_posting("https://career.gasang.example/job/1") == "https://gasang.example"
    assert homepage_from_posting("https://recruit.gasang.co.kr/a") == "https://gasang.co.kr"
    # 채용 사이트·채용 시스템 주소로는 짐작하지 않는다
    assert homepage_from_posting("https://www.wanted.co.kr/wd/1") is None
    assert homepage_from_posting("https://gasang.career.greetinghr.com/o/1") is None
    assert homepage_from_posting("https://gasang.example/jobs/1") is None  # 앞 이름이 없다

    html = (
        '<nav><a href="/about">회사소개</a><a href="/talent">인재상</a>'
        '<a href="/login">로그인</a><a href="https://other.example/culture">문화</a>'
        '<a href="/products/x">제품</a><a href="/news/1">소식</a>'
        '<a href="/en/company/culture"><img alt="Culture"></a><a href="#top">위로</a>'
        '<a href="/about#history">연혁</a><a href="/privacy">개인정보</a></nav>'
    )
    links = page_links(html, "https://www.gasang.example/")
    assert ("https://www.gasang.example/about", "회사소개") in links
    assert all("#" not in u for u, _t in links)
    assert link_score("https://www.gasang.example/talent", "인재상") == 5
    assert link_score("https://www.gasang.example/x", "") == 0
    picked = pick_links(links, "https://www.gasang.example/", skip=set(), limit=3)
    assert picked == [
        "https://www.gasang.example/talent",
        "https://www.gasang.example/en/company/culture",
        "https://www.gasang.example/about",
    ]
    assert "https://www.gasang.example/talent" not in pick_links(
        links, "https://www.gasang.example/", skip={"https://www.gasang.example/talent"}
    )


def _site(url):
    return [{"mainsnak": {"datavalue": {"value": url}}}]


def _item(qid):
    return [{"mainsnak": {"datavalue": {"value": {"id": qid}}}}]


def test_pick_entity_needs_same_name_and_an_organization():
    entities = {
        "Q1": {"labels": {"ko": {"value": "가상전자공업"}}, "claims": {"P452": _item("Q5")}},
        # 이름은 같지만 회사가 아니다 (식물·지명 같은 것): 업종·본사 같은 속성이 없다
        "Q3": {
            "labels": {"ko": {"value": "가상전자"}},
            "claims": {"P856": _site("https://plant.example/")},
            "sitelinks": {"kowiki": {"title": "가상전자 (식물)"}},
        },
        # 이름이 같은 외국 회사: 한글 이름이면 한국 회사 뒤로
        "Q4": {
            "labels": {"ko": {"value": "가상전자"}},
            "claims": {
                "P452": _item("Q9"),
                "P17": _item("Q29"),
                "P856": _site("https://es.example/"),
            },
        },
        "Q2": {
            "labels": {"en": {"value": "Gasang Electronics"}},
            "aliases": {"ko": [{"value": "(주)가상전자"}]},
            "claims": {
                "P856": _site("https://www.gasang.example/"),
                "P159": _item("Q8684"),
                "P17": _item("Q884"),
            },
            "sitelinks": {"kowiki": {"title": "가상 전자"}},
        },
    }
    key = {name_key("가상전자")}
    got = pick_entity(entities, ["Q1", "Q3", "Q4", "Q2"], key, name_key, name="가상전자")
    assert got == {
        "qid": "Q2",
        "homepage": "https://www.gasang.example/",
        "wiki": "https://ko.wikipedia.org/wiki/%EA%B0%80%EC%83%81_%EC%A0%84%EC%9E%90",
    }
    assert pick_entity(entities, ["Q1", "Q3"], key, name_key, name="가상전자") is None
    # 영문 이름이면 나라를 따지지 않고 검색 순서
    en = pick_entity(entities, ["Q4", "Q2"], key, name_key, name="Gasang")
    assert en is not None and en["qid"] == "Q4"


# --- 찾기 (DB) ----------------------------------------------------------------------------

POSTING_TEXT = (
    "가상전자 백엔드 개발자 (합성)\n"
    "[주요 업무]\n- 센서 데이터 수집 API 개발\n"
    "[가상전자가 함께 일하고 싶은 사람]\n"
    "실패를 두려워하지 않고 도전하는 사람\n"
    "동료와 함께 더 큰 성과를 만드는 사람\n"
)
HOME_HTML = (
    "<html><head><title>가상전자</title></head><body>"
    '<nav><a href="/about">회사소개</a><a href="/talent">인재상</a><a href="/login">로그인</a>'
    '<a href="https://elsewhere.example/x">문화</a></nav><main>'
    "<p>가상전자는 산업용 센서와 데이터 분석 플랫폼을 만듭니다.</p>"
    + "<p>가상전자는 합성 데이터로 만든 회사입니다.</p>" * 6
    + "</main></body></html>"
)
TALENT_HTML = (
    "<html><head><title>인재상</title></head><body><main>"
    "<p>인재상: 고객의 문제를 끝까지 파고드는 사람</p>"
    + "<p>가상전자는 합성 데이터로 만든 회사입니다.</p>" * 6
    + "</main></body></html>"
)
WIKI_HTML = (
    "<html><head><title>가상 전자 - 위키백과</title></head><body><main>"
    "<p>가상전자는 대한민국의 합성 센서 기업이다.</p>"
    + "<p>가상전자는 합성 데이터로 만든 회사입니다.</p>" * 6
    + "</main></body></html>"
)
SEARCH = {"search": [{"id": "Q7"}]}
ENTITIES = {
    "entities": {
        "Q7": {
            "labels": {"ko": {"value": "가상전자"}},
            "claims": {
                "P856": [{"mainsnak": {"datavalue": {"value": "https://www.gasang.example/"}}}],
                "P452": [{"mainsnak": {"datavalue": {"value": {"id": "Q11661"}}}}],
            },
            "sitelinks": {"kowiki": {"title": "가상 전자"}},
        }
    }
}
FACTS = json.dumps(
    {
        "facts": [
            {
                "kind": "talent",
                "body": "도전하는 사람",
                "quote": "실패를 두려워하지 않고 도전하는 사람",
            },
            {
                "kind": "talent",
                "body": "고객 문제를 끝까지 파고드는 사람",
                "quote": "인재상: 고객의 문제를 끝까지 파고드는 사람",
            },
            {
                "kind": "business",
                "body": "산업용 센서와 데이터 분석 플랫폼",
                "quote": "가상전자는 산업용 센서와 데이터 분석 플랫폼을 만듭니다.",
            },
        ]
    },
    ensure_ascii=False,
)

IPS = {
    "www.gasang.example": "93.184.215.50",
    "www.wikidata.org": "93.184.215.51",
    "ko.wikipedia.org": "93.184.215.52",
    "career.gasang.example": "93.184.215.53",
}


async def fake_resolve(host, port):
    if host not in IPS:
        raise ConnectionError("dns_failed")
    return IPS[host]


class Site:
    def __init__(self):
        self.requests: list[str] = []
        self.wikidata = True

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.headers.get("host")
        path = request.url.path
        self.requests.append(f"{host}{path}")
        if host == "www.wikidata.org":
            if not self.wikidata:
                return httpx.Response(200, json={"search": []})
            q = dict(request.url.params)
            return httpx.Response(
                200, json=SEARCH if q["action"] == "wbsearchentities" else ENTITIES
            )
        if host == "ko.wikipedia.org":
            return httpx.Response(200, html=WIKI_HTML)
        if host == "www.gasang.example":
            if path == "/talent":
                return httpx.Response(200, html=TALENT_HTML)
            if path == "/kr":  # 그림 위주 홈페이지: 글은 거의 없고 링크만
                return httpx.Response(
                    200,
                    html='<html><body><a href="/talent">인재상</a><img alt="배너"></body></html>',
                )
            if path == "/about":
                return httpx.Response(404)
            return httpx.Response(200, html=HOME_HTML)
        return httpx.Response(404)


class FactsLLM:
    def __init__(self):
        self.calls = 0

    async def complete(self, model, request):
        self.calls += 1
        return ModelCompletion(
            text=FACTS,
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, TokenUsage(input_tokens=100, output_tokens=40)),),
        )


CONFIG = parse_models_config("""
[endpoints.proxy]
preset = "cliproxyapi"
price_as = "openai"
data_classes = ["public", "private"]
[routes]
default = "proxy/gpt-6.1-sol"
""")


@pytest.fixture
async def env(db):
    ws, me = await create_workspace(db, name="합성 공간", owner_display_name="me")
    llm = FactsLLM()
    gw = ModelGateway(
        db,
        CONFIG,
        PriceBook.load(),
        client_factory=lambda ep, key: llm,
        env={"CLIPROXY_API_KEY": "k"},
    )
    site = Site()
    fetcher = HttpFetcher(transport=httpx.MockTransport(site.handler), resolver=fake_resolve)
    owner = ActorContext(me, ws, scopes_for_role(Role.OWNER))
    return {
        "owner": owner,
        "site": site,
        "llm": llm,
        "deps": posting_uc.PostingDeps(db=db, fetcher=fetcher, gateway=gw),
    }


async def saved_posting(db, actor, url, company, text=POSTING_TEXT, talent=None):
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
                method="http",
                adapter="text",
                content_hash=uuid4().hex,
                text=text,
                extracted={},
                missing=[],
                changed_fields=[],
                created_by=actor.subject_id,
            )
        )
    overrides = {"company": company, "title": "백엔드 개발자"}
    if talent:
        overrides["talent"] = talent
    await posting_uc.confirm_posting(db, actor, p.id, overrides=overrides)
    return p.id


async def test_saving_a_posting_links_company_and_researches(db, env):
    owner, deps = env["owner"], env["deps"]
    pid = await saved_posting(db, owner, "https://career.gasang.example/job/1", "(주)가상전자")
    cid = (await posting_uc.get_posting(db, owner, pid)).company_id
    assert cid is not None
    view = await uc.get_company(db, owner, cid)
    assert view.name == "(주)가상전자" and view.research_status == "pending"

    await run_until_idle(db, posting_handlers(deps))
    view = await uc.get_company(db, owner, cid)
    assert view.research_status == "done" and view.researched_at is not None
    # 공고 주소(career.가상전자)에서 짐작한 홈페이지가 위키데이터보다 먼저
    assert view.homepage == "https://gasang.example/"
    assert "homepage:posting_url" in view.research_notes
    assert "wikidata:Q7" in view.research_notes
    by_origin = {}
    for p in view.pages:
        by_origin.setdefault(p.origin, []).append(p)
    assert [p.url for p in by_origin["posting"]] == ["https://career.gasang.example/job/1"]
    # gasang.example은 가짜 DNS에 없다 → 홈페이지 실패를 메모하고, 위키백과는 가져온다
    assert any(n.startswith("homepage_failed:") for n in view.research_notes)
    assert {p.url for p in by_origin["found"]} == {
        "https://ko.wikipedia.org/wiki/%EA%B0%80%EC%83%81_%EC%A0%84%EC%9E%90"
    }
    # 항목은 원문 인용이 있는 것만, 모두 후보
    kinds = {(f.kind, f.body) for f in view.facts}
    assert ("talent", "도전하는 사람") in kinds
    assert all(f.status == "candidate" for f in view.facts)
    for f in view.facts:
        page = await uc.get_page(db, owner, cid, f.citation.page_id)
        assert page.text[f.citation.start : f.citation.end] == f.citation.quote

    # 같은 이름의 다른 공고는 같은 회사에 이어지고, 그 공고 글만 새로 찾는다 (홈페이지·위키는 다시 안 받음)
    env["site"].requests.clear()
    pid2 = await saved_posting(
        db, owner, "https://www.wanted.co.kr/wd/9", "가상전자", text=POSTING_TEXT + "두 번째 공고\n"
    )
    assert (await posting_uc.get_posting(db, owner, pid2)).company_id == cid
    await run_until_idle(db, posting_handlers(deps))
    view = await uc.get_company(db, owner, cid)
    assert sum(p.origin == "posting" for p in view.pages) == 2
    assert not any("wikipedia" in r for r in env["site"].requests)


async def test_research_follows_homepage_links_one_step(db, env):
    owner, deps = env["owner"], env["deps"]
    cid = await uc.create_company(db, owner, name="가상전자")
    assert await research.request_research(db, owner, cid) == "pending"
    assert await research.request_research(db, owner, cid) == "pending"  # 작업은 하나만
    async with db.workspace_session(owner.workspace_id) as s:
        jobs = await s.scalar(
            select(func.count()).select_from(Job).where(Job.kind == research.RESEARCH_JOB)
        )
    assert jobs == 1
    await run_until_idle(db, posting_handlers(deps))
    view = await uc.get_company(db, owner, cid)
    assert view.homepage == "https://www.gasang.example/"  # 위키데이터 공식 웹사이트
    found = {p.url: p for p in view.pages if p.origin == "found"}
    assert set(found) == {
        "https://www.gasang.example/",
        "https://www.gasang.example/talent",
        "https://www.gasang.example/about",
        "https://ko.wikipedia.org/wiki/%EA%B0%80%EC%83%81_%EC%A0%84%EC%9E%90",
    }
    assert found["https://www.gasang.example/about"].status == "failed"  # 404는 실패로만
    assert found["https://www.gasang.example/talent"].status == "done"
    # 다른 사이트·로그인 링크는 따라가지 않는다
    assert not any("elsewhere" in r or r.endswith("/login") for r in env["site"].requests)
    talents = [f for f in view.facts if f.kind == "talent"]
    assert [f.body for f in talents] == ["고객 문제를 끝까지 파고드는 사람"]

    # 다시 찾기: 이미 가져온 쪽은 다시 받지 않는다
    env["site"].requests.clear()
    await research.request_research(db, owner, cid)
    await run_until_idle(db, posting_handlers(deps))
    assert not any(r.startswith("www.gasang.example") for r in env["site"].requests)


async def test_text_poor_homepage_still_gives_links(db, env):
    owner, deps = env["owner"], env["deps"]
    env["site"].wikidata = False
    cid = await uc.create_company(
        db, owner, name="가상전자", homepage="https://www.gasang.example/kr"
    )
    await research.request_research(db, owner, cid)
    await run_until_idle(db, posting_handlers(deps))
    view = await uc.get_company(db, owner, cid)
    assert "homepage_no_text" in view.research_notes and "homepage:saved" in view.research_notes
    assert [(p.url, p.status) for p in view.pages] == [
        ("https://www.gasang.example/talent", "done")
    ]


async def test_no_homepage_found_is_noted(db, env):
    owner, deps = env["owner"], env["deps"]
    env["site"].wikidata = False
    cid = await uc.create_company(db, owner, name="없는회사")
    await research.request_research(db, owner, cid)
    await run_until_idle(db, posting_handlers(deps))
    view = await uc.get_company(db, owner, cid)
    assert view.research_status == "done"
    assert view.research_notes == ["homepage:none"]
    assert view.pages == []


async def test_other_workspace_cannot_research(db, env):
    ws_b, b = await create_workspace(db, name="B (합성)", owner_display_name="ben")
    other = ActorContext(b, ws_b, scopes_for_role(Role.OWNER))
    cid = await uc.create_company(db, env["owner"], name="가상전자")
    from career_core.usecases.brain import NotFound

    with pytest.raises(NotFound):
        await research.request_research(db, other, cid)
    async with db.workspace_session(ws_b) as s:
        assert await s.scalar(select(func.count()).select_from(Company)) == 0
        assert await s.scalar(select(func.count()).select_from(CompanyPage)) == 0
        assert await s.scalar(select(func.count()).select_from(CompanyFact)) == 0


async def test_research_http(db, env):
    owner = env["owner"]
    cid = await uc.create_company(db, owner, name="가상전자")

    class Resolver:
        async def resolve(self, credential):
            return owner if credential == "t" else None

    app = FastAPI()
    app.include_router(build_router(CoreDeps(identity=Resolver(), db=db)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        h = {"Authorization": "Bearer t"}
        r = await c.post(f"/core/companies/{cid}/research", headers=h)
        assert r.status_code == 202 and r.json()["status"] == "pending"
        got = (await c.get(f"/core/companies/{cid}", headers=h)).json()
        assert got["research_status"] == "pending" and got["research_notes"] == []
        r = await c.post(f"/core/companies/{uuid4()}/research", headers=h)
        assert r.status_code == 404


async def test_posting_talent_list_is_kept(db, env):
    owner = env["owner"]
    pid = await saved_posting(
        db,
        owner,
        "https://career.gasang.example/job/2",
        "가상전자",
        talent=["실패를 두려워하지 않고 도전하는 사람"],
    )
    view = await posting_uc.get_posting(db, owner, pid)
    assert view.fields["talent"] == ["실패를 두려워하지 않고 도전하는 사람"]


def test_elementor_page_text_is_not_noise():
    """Elementor(워드프레스)는 본문까지 elementor-widget으로 감싼다. 그래도 본문은 본문이다."""
    from career_core.adapters.html_extract import extract_page

    body = "가상전자는 산업용 센서와 데이터 분석 플랫폼을 만드는 합성 회사입니다. " * 4
    html = (
        '<html><body><a class="skip-link" href="#content">Skip to content</a>'
        '<div class="elementor elementor-location-header"><a href="/">로고</a></div>'
        '<div data-elementor-type="wp-page" class="elementor">'
        '<div class="elementor-widget elementor-widget-text-editor">'
        f'<div class="elementor-widget-container"><p>{body}</p></div></div></div>'
        '<div class="sidebar widget"><p>다른 글 목록</p></div></body></html>'
    )
    text = extract_page(html, "https://www.gasang.example/kr/").main_text
    assert "산업용 센서" in text
    assert "다른 글 목록" not in text  # Elementor가 아닌 widget은 여전히 곁가지

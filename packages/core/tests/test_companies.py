"""O13 회사 조사 (참고 지식): 인용 없는 항목 저장 안 됨, 기준일 없는 재무 값 거부, 오래됨 표시,
같은 회사 공고 묶기(사람 확인), 다른 공간 0건, 내보내기·복원, MCP 출력, 문서 근거가 될 수 없음.

회사·페이지·글은 모두 합성이다. 네트워크는 쓰지 않는다 (MockTransport + 가짜 DNS).
"""

import json
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.web_fetch import AddressBlocked, HttpFetcher
from career_core.db import Job, JobPosting, JobPostingVersion, ModelCall
from career_core.db.models import CompanyFact, CompanyPage, DocumentClaim
from career_core.domain.companies import (
    AsOf,
    CompanyRuleError,
    as_of_in_quote,
    is_stale,
    name_key,
    parse_as_of,
)
from career_core.domain.documents import DocumentRuleError
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import posting_handlers, run_until_idle
from career_core.llm import ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.ports import ACTOR_AGENT, ActorContext
from career_core.usecases import applications as app_uc
from career_core.usecases import brain
from career_core.usecases import companies as uc
from career_core.usecases import documents as docs_uc
from career_core.usecases import postings as posting_uc
from career_core.usecases.export import export_workspace, restore_workspace
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import func, select, update

pytestmark = pytest.mark.anyio

PAGE_TEXT = (
    "가상전자 회사 소개\n"
    "인재상: 고객의 문제를 끝까지 파고드는 사람\n"
    "핵심 가치: 정직, 도전, 협력\n"
    "주요 사업: 산업용 센서와 데이터 분석 플랫폼\n"
    "2024년 매출액 1,200억 원 (연결 기준)\n"
    "영업이익은 꾸준히 늘었습니다.\n"
    "채용 절차: 서류 전형 - 코딩 테스트 - 면접\n"
)
COMPANY_HTML = (
    "<html><head><title>가상전자 회사 소개</title></head><body><main>"
    + "".join(f"<p>{line}</p>" for line in PAGE_TEXT.splitlines())
    + "<p>가상전자는 합성 데이터로 만든 회사입니다.</p>" * 5
    + "</main></body></html>"
)
MODEL_REPLY = json.dumps(
    {
        "facts": [
            {
                "kind": "talent",
                "body": "고객의 문제를 끝까지 파고드는 사람을 찾는다.",
                "quote": "인재상: 고객의 문제를 끝까지 파고드는 사람",
                "as_of": None,
            },
            {
                "kind": "finance",
                "body": "2024년 매출액은 1,200억 원이다.",
                "quote": "2024년 매출액 1,200억 원 (연결 기준)",
                "as_of": "2024",
                "as_of_quote": "2024년 매출액 1,200억 원",
            },
            # 기준일이 원문에 없는 재무 값: 버린다
            {
                "kind": "finance",
                "body": "영업이익이 늘었다.",
                "quote": "영업이익은 꾸준히 늘었습니다.",
                "as_of": "2025",
            },
            # 원문에 없는 인용: 버린다
            {"kind": "culture", "body": "주 4일 근무", "quote": "주 4일 근무제를 운영합니다"},
            # 기준일 숫자가 인용에 없다: 기준일만 비운다 (추측하지 않는다)
            {
                "kind": "business",
                "body": "산업용 센서와 데이터 분석 플랫폼 사업",
                "quote": "주요 사업: 산업용 센서와 데이터 분석 플랫폼",
                "as_of": "2023-05",
            },
            {"kind": "unknown_kind", "body": "x", "quote": "정직"},
        ]
    },
    ensure_ascii=False,
)

IPS = {"corp.example.test": "93.184.215.40", "blocked.example.test": "10.0.0.9"}


async def fake_resolve(host, port):
    ip = IPS.get(host)
    if ip is None:
        raise ConnectionError("dns_failed")
    if ip.startswith("10."):
        raise AddressBlocked(host)
    return ip


class Site:
    def __init__(self):
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        assert request.headers.get("cookie") is None
        return httpx.Response(200, html=COMPANY_HTML)


class ScriptedLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def complete(self, model, request):
        self.calls += 1
        assert "public" in request.data_classes
        return ModelCompletion(
            text=self.reply,
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, TokenUsage(input_tokens=120, output_tokens=60)),),
        )


CONFIG = parse_models_config("""
[endpoints.proxy]
preset = "cliproxyapi"
price_as = "openai"
data_classes = ["public"]
[routes]
default = "proxy/gpt-6.1-sol"
""")


class FakeResolver:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


async def make_posting(db, actor, url, company, title="백엔드 개발자"):
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
                text=f"{company} {title} 채용 (합성)",
                extracted={},
                missing=[],
                changed_fields=[],
                created_by=actor.subject_id,
            )
        )
    await posting_uc.confirm_posting(
        db, actor, p.id, overrides={"company": company, "title": title}
    )
    return p.id


@pytest.fixture
async def env(db):
    ws_a, a = await create_workspace(db, name="A (합성)", owner_display_name="amy")
    ws_b, b = await create_workspace(db, name="B (합성)", owner_display_name="ben")
    llm = ScriptedLLM(MODEL_REPLY)
    gw = ModelGateway(
        db,
        CONFIG,
        PriceBook.load(),
        client_factory=lambda ep, key: llm,
        env={"CLIPROXY_API_KEY": "k"},
    )
    site = Site()
    fetcher = HttpFetcher(transport=httpx.MockTransport(site.handler), resolver=fake_resolve)
    owner = ActorContext(a, ws_a, scopes_for_role(Role.OWNER))
    return {
        "owner": owner,
        "editor": ActorContext(a, ws_a, scopes_for_role(Role.EDITOR)),
        "viewer": ActorContext(a, ws_a, scopes_for_role(Role.VIEWER)),
        "agent": ActorContext(a, ws_a, scopes_for_role(Role.OWNER), kind=ACTOR_AGENT),
        "other": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
        "gateway": gw,
        "llm": llm,
        "site": site,
        "deps": posting_uc.PostingDeps(db=db, fetcher=fetcher, gateway=gw),
    }


# ---------------------------------------------------------------- 규칙 (DB 없이)


def test_name_key_and_as_of_rules():
    assert name_key("(주)가상전자") == name_key("가상전자 주식회사") == name_key("가상 전자")
    assert name_key("Fakeco Inc.") == name_key("FAKECO") == "fakeco"
    assert name_key("㈜") == ""
    assert parse_as_of("2024") == AsOf(date(2024, 1, 1), "year")
    assert parse_as_of("2024-06").end == date(2024, 6, 30)
    assert parse_as_of("2024-02-29").precision == "day"
    assert parse_as_of(None) is None and parse_as_of("  ") is None
    for bad in ("24", "2024-13", "2024-02-30", "2024/06", "1800"):
        with pytest.raises(CompanyRuleError, match="invalid_as_of"):
            parse_as_of(bad)
    assert as_of_in_quote(parse_as_of("2024"), "2024년 매출액")
    assert not as_of_in_quote(parse_as_of("2024"), "24년 매출액")
    assert not as_of_in_quote(parse_as_of("2024-05"), "2024년 매출액")
    today = date(2026, 11, 17)
    # 연도 기준일은 그해 끝에서 센다: 2025년 값은 아직, 2024년 값은 오래됨
    assert not is_stale(date(2025, 1, 1), "year", None, today)
    assert is_stale(date(2024, 1, 1), "year", None, today)
    # 기준일이 없으면 가져온 날
    assert is_stale(None, None, datetime(2025, 10, 1, tzinfo=UTC), today)
    assert not is_stale(None, None, datetime(2026, 1, 1, tzinfo=UTC), today)


# ---------------------------------------------------------------- 인용·재무 기준일


async def test_paste_without_model_keeps_text_and_citation_is_required(db, env):
    owner = env["owner"]
    cid = await uc.create_company(db, owner, name="(주)가상전자", aliases=["Gasang Electronics"])
    with pytest.raises(CompanyRuleError, match="company_exists"):
        await uc.create_company(db, owner, name="가상전자 주식회사")
    page = await uc.import_paste(db, None, owner, cid, PAGE_TEXT, title="회사 소개 (합성)")
    assert (page.status, page.candidates, page.notes) == ("done", 0, ["no_model"])

    # 원문에 없는 인용은 저장하지 않는다
    with pytest.raises(CompanyRuleError, match="quote_not_found"):
        await uc.add_fact(
            db, owner, cid, page_id=page.id, kind="culture", body="주 4일", quote="주 4일 근무"
        )
    # 재무는 기준일이 있어야 한다. 추측하지 않으므로 없으면 거부
    with pytest.raises(CompanyRuleError, match="finance_needs_as_of"):
        await uc.add_fact(
            db, owner, cid, page_id=page.id, kind="finance", body="매출", quote="매출액 1,200억 원"
        )
    fid = await uc.add_fact(
        db,
        owner,
        cid,
        page_id=page.id,
        kind="finance",
        body="2024년 매출 1,200억 원",
        quote="2024년  매출액 1,200억 원",  # 공백 차이는 같은 인용
        as_of="2024",
    )
    talent = await uc.add_fact(
        db,
        owner,
        cid,
        page_id=page.id,
        kind="talent",
        body="끝까지 파고드는 사람",
        quote="고객의 문제를 끝까지 파고드는 사람",
    )
    view = await uc.get_company(db, owner, cid, today=date(2026, 11, 17))
    facts = {f.id: f for f in view.facts}
    assert facts[fid].citation.quote == "2024년 매출액 1,200억 원"
    assert (
        PAGE_TEXT[facts[fid].citation.start : facts[fid].citation.end] == "2024년 매출액 1,200억 원"
    )
    assert (facts[fid].as_of, facts[fid].stale) == ("2024", True)  # 2024년 값은 2026-11에 오래됨
    # 사람이 직접 고른 항목은 확인됨. 기준일 모름 = 비움
    assert (facts[talent].status, facts[talent].as_of, facts[talent].stale) == (
        "confirmed",
        None,
        False,
    )
    # 기준일을 지우면 재무 규칙에 걸린다
    with pytest.raises(CompanyRuleError, match="finance_needs_as_of"):
        await uc.update_fact(db, owner, cid, fid, as_of=None)
    with pytest.raises(CompanyRuleError, match="quote_not_found"):
        await uc.update_fact(db, owner, cid, talent, quote="원문에 없는 말")
    await uc.update_fact(db, owner, cid, talent, quote="정직, 도전, 협력", kind="values")
    # DB도 같은 규칙: 인용 없는 항목, 기준일 없는 재무 항목은 들어가지 않는다
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with db.workspace_session(owner.workspace_id) as s:
            await s.execute(update(CompanyFact).where(CompanyFact.id == fid).values(as_of=None))


async def test_http_errors_and_permissions(db, env):
    tokens = {"owner": env["owner"], "viewer": env["viewer"], "other": env["other"]}
    app = FastAPI()
    app.include_router(build_router(CoreDeps(identity=FakeResolver(tokens), db=db)))

    def h(t):
        return {"Authorization": f"Bearer {t}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        res = await c.post("/core/companies", json={"name": "가상전자"}, headers=h("owner"))
        assert res.status_code == 201
        cid = res.json()["id"]
        res = await c.post("/core/companies", json={"name": "㈜ 가상전자"}, headers=h("owner"))
        assert (res.status_code, res.json()) == (409, {"detail": "company_exists"})
        res = await c.post("/core/companies", json={"name": "보기전용"}, headers=h("viewer"))
        assert res.status_code == 403
        res = await c.post(
            f"/core/companies/{cid}/import-paste", json={"text": PAGE_TEXT}, headers=h("owner")
        )
        assert res.status_code == 201 and res.json()["notes"] == ["no_model"]
        page_id = res.json()["id"]
        fact = {"page_id": page_id, "kind": "finance", "body": "매출", "quote": "매출액"}
        res = await c.post(f"/core/companies/{cid}/facts", json=fact, headers=h("owner"))
        assert (res.status_code, res.json()) == (422, {"detail": "finance_needs_as_of"})
        res = await c.post(
            f"/core/companies/{cid}/facts",
            json={**fact, "kind": "culture", "quote": "없는 문장"},
            headers=h("owner"),
        )
        assert (res.status_code, res.json()) == (422, {"detail": "quote_not_found"})
        res = await c.post(
            f"/core/companies/{cid}/facts",
            json={**fact, "quote": "2024년 매출액 1,200억 원", "as_of": "2024"},
            headers=h("owner"),
        )
        assert res.status_code == 201
        body = (await c.get(f"/core/companies/{cid}", headers=h("viewer"))).json()
        [f] = body["facts"]
        assert f["as_of"] == "2024" and f["stale"] is True and f["status"] == "confirmed"
        assert f["citation"]["page_id"] == page_id and f["citation"]["origin"] == "paste"
        page = (await c.get(f"/core/companies/{cid}/pages/{page_id}", headers=h("viewer"))).json()
        assert page["text"].startswith("가상전자 회사 소개")
        # 다른 공간은 0건, 없는 회사는 404
        assert (await c.get("/core/companies", headers=h("other"))).json() == []
        res = await c.get(f"/core/companies/{cid}", headers=h("other"))
        assert (res.status_code, res.json()) == (404, {"detail": "not found"})
        res = await c.delete(f"/core/companies/{cid}", headers=h("owner"))
        assert res.status_code == 204


# ---------------------------------------------------------------- 모델 후보·URL 가져오기


async def test_url_import_extracts_verified_candidates(db, env):
    owner = env["owner"]
    cid = await uc.create_company(db, owner, name="가상전자")
    page_id = await uc.import_url(db, owner, cid, "https://corp.example.test/about?utm_source=x")
    # 같은 주소가 대기 중이면 새로 넣지 않는다
    assert await uc.import_url(db, owner, cid, "corp.example.test/about") == page_id
    await run_until_idle(db, posting_handlers(env["deps"]))
    assert env["site"].requests == ["https://93.184.215.40/about"]
    page = await uc.get_page(db, owner, cid, page_id)
    assert page.status == "done" and page.final_url == "https://corp.example.test/about"
    assert "llm_dropped:3" in page.notes  # 원문에 없는 인용, 기준일 없는 재무, 모르는 종류
    view = await uc.get_company(db, owner, cid, today=date(2025, 6, 1))
    got = {(f.kind, f.as_of) for f in view.facts}
    assert got == {("talent", None), ("finance", "2024"), ("business", None)}
    assert all(f.status == "candidate" and f.origin == "model" for f in view.facts)
    full = (await uc.get_page(db, owner, cid, page_id)).text
    for f in view.facts:
        assert full[f.citation.start : f.citation.end] == f.citation.quote
        assert f.citation.source_url == "https://corp.example.test/about"
    async with db.workspace_session(owner.workspace_id) as s:
        purposes = (await s.scalars(select(ModelCall.purpose))).all()
    assert purposes == ["company_facts"]  # 라우트가 없으면 default로, 사용량은 장부에

    # 후보 확인은 사람만 (AI 클라이언트는 못 한다)
    fact = view.facts[0]
    with pytest.raises(CompanyRuleError, match="human_confirmation_required"):
        await uc.confirm_fact(db, env["agent"], cid, fact.id)
    assert await uc.confirm_fact(db, owner, cid, fact.id) == "confirmed"

    # 내부 주소는 가져오지 않는다
    bad = await uc.import_url(db, owner, cid, "https://blocked.example.test/x")
    await run_until_idle(db, posting_handlers(env["deps"]))
    failed = await uc.get_page(db, owner, cid, bad)
    assert (failed.status, failed.error_code) == ("failed", "blocked_address")
    with pytest.raises(CompanyRuleError, match="page_not_ready"):
        await uc.add_fact(db, owner, cid, page_id=bad, kind="other", body="x", quote="x")


async def test_paste_with_model_runs_in_the_request(db, env):
    owner = env["owner"]
    cid = await uc.create_company(db, owner, name="가상전자")
    page = await uc.import_paste(db, env["gateway"], owner, cid, PAGE_TEXT)
    assert page.candidates == 3 and env["llm"].calls == 1
    async with db.workspace_session(owner.workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(Job)) == 0  # 붙여넣기는 작업 없음


# ---------------------------------------------------------------- 공고 묶기·공간 분리


async def test_posting_link_suggestions_need_a_person(db, env):
    owner, other = env["owner"], env["other"]
    cid = await uc.create_company(db, owner, name="가상전자", aliases=["Gasang Electronics"])
    p1 = await make_posting(db, owner, "https://jobs.example.test/c/1", "(주)가상전자")
    p2 = await make_posting(db, owner, "https://jobs.example.test/c/2", "GASANG ELECTRONICS")
    p3 = await make_posting(db, owner, "https://jobs.example.test/c/3", "다른회사")
    foreign = await make_posting(db, other, "https://jobs.example.test/c/9", "가상전자")
    sugg = await uc.link_suggestions(db, owner, cid)
    assert {s.posting_id for s in sugg} == {p1, p2}
    assert {s.matched for s in sugg} == {"가상전자", "Gasang Electronics"}
    # 제안만으로는 아무것도 잇지 않는다
    assert (await uc.get_company(db, owner, cid)).postings == []
    # 사람이 고른 것만. 다른 공간의 공고 ID는 없는 공고
    with pytest.raises(CompanyRuleError, match="unknown_posting"):
        await uc.link_postings(db, owner, cid, [p1, foreign])
    assert await uc.link_postings(db, owner, cid, [p1]) == 1
    aid = await app_uc.create_application(db, owner, p1)
    view = await uc.get_company(db, owner, cid)
    assert [p.id for p in view.postings] == [p1] and view.postings[0].company == "(주)가상전자"
    assert [a.id for a in view.applications] == [aid]
    assert (await posting_uc.get_posting(db, owner, p1)).company_id == cid
    assert {s.posting_id for s in await uc.link_suggestions(db, owner, cid)} == {p2}
    [summary] = await uc.list_companies(db, owner, "gasang")
    assert summary.postings == 1
    assert await uc.unlink_postings(db, owner, cid, [p1, p3]) == 1

    # 다른 공간: 목록 0건, 조회·쓰기는 없음
    assert await uc.list_companies(db, other) == []
    for call in (
        uc.get_company(db, other, cid),
        uc.link_suggestions(db, other, cid),
        uc.link_postings(db, other, cid, [foreign]),
        uc.import_paste(db, None, other, cid, PAGE_TEXT),
        uc.delete_company(db, other, cid),
    ):
        with pytest.raises(brain.NotFound):
            await call
    # 회사를 지우면 공고는 남고 연결만 비워진다
    await uc.link_postings(db, owner, cid, [p1])
    await uc.delete_company(db, owner, cid)
    assert (await posting_uc.get_posting(db, owner, p1)).company_id is None


# ---------------------------------------------------------------- 근거가 될 수 없음


async def test_company_fact_cannot_be_document_evidence(db, env):
    owner = env["owner"]
    cid = await uc.create_company(db, owner, name="가상전자")
    page = await uc.import_paste(db, None, owner, cid, PAGE_TEXT)
    fid = await uc.add_fact(
        db,
        owner,
        cid,
        page_id=page.id,
        kind="finance",
        body="2024년 매출 1,200억 원",
        quote="2024년 매출액 1,200억 원",
        as_of="2024",
    )
    doc = await docs_uc.create_shared_document(db, owner, kind="resume", title="합성 이력서")
    with pytest.raises(DocumentRuleError, match="unknown_claim"):
        await app_uc.save_revision(
            db,
            owner,
            None,
            doc,
            base_revision=0,
            operations=[{"op": "insert", "text": "정직하게 일합니다.", "claim_ids": [str(fid)]}],
        )
    await app_uc.save_revision(
        db,
        owner,
        None,
        doc,
        base_revision=0,
        operations=[{"op": "insert", "text": "2024년 매출액 1,200억 원을 함께 만들었습니다."}],
    )
    async with db.workspace_session(owner.workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(DocumentClaim)) == 0
    # 근거 없는 문장 검사: 회사 항목 문장과 같아도 근거로 치지 않는다
    checked = await app_uc.validate_document(db, owner, None, doc)
    assert checked["summary"].get("unsupported_statement", 0) == 1


# ---------------------------------------------------------------- MCP


async def test_mcp_company_tools_are_reference_and_read_only(db, env):
    from career_core.interfaces.mcp.tools import TOOLS_BY_NAME

    owner = env["owner"]
    cid = await uc.create_company(db, owner, name="가상전자")
    await uc.import_paste(db, env["gateway"], owner, cid, PAGE_TEXT)
    talent = next(f for f in (await uc.get_company(db, owner, cid)).facts if f.kind == "talent")
    await uc.confirm_fact(db, owner, cid, talent.id)
    tokens = {"owner": owner, "other": env["other"]}
    app = FastAPI()
    app.include_router(build_router(CoreDeps(identity=FakeResolver(tokens), db=db)))
    for name in ("company_list", "company_get"):
        tool = TOOLS_BY_NAME[name]
        assert tool.read_only and tool.action == "companies.read"
        assert "NOT the user's experience" in tool.description

    async def call(token, name, args):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
            res = await c.post(
                "/core/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": args},
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        return res.json()["result"]

    listed = (await call("owner", "company_list", {}))["structuredContent"]
    assert listed["record"] == "reference" and "never cite" in listed["note"].lower()
    [row] = listed["companies"]
    assert (row["name"], row["facts_confirmed"], row["facts_candidate"]) == ("가상전자", 1, 2)
    got = (await call("owner", "company_get", {"company_id": cid.hex}))["structuredContent"]
    assert got["untrusted_content"] is True and got["record"] == "reference"
    assert "pages" not in got
    [fact] = got["facts"]  # 기본은 확인된 항목만
    assert fact["kind"] == "talent" and fact["stale"] is False
    assert fact["citation"]["quote"] == "인재상: 고객의 문제를 끝까지 파고드는 사람"
    both = (
        await call("owner", "company_get", {"company_id": str(cid), "include_candidates": True})
    )["structuredContent"]
    assert {f["kind"] for f in both["facts"]} == {"talent", "finance", "business"}
    # 다른 공간: 0건·없음
    assert (await call("other", "company_list", {}))["structuredContent"]["companies"] == []
    res = await call("other", "company_get", {"company_id": str(cid)})
    assert res["isError"] is True


# ---------------------------------------------------------------- 내보내기·복원


async def test_export_restore_round_trip_keeps_companies(db, env, tmp_path):
    owner = env["owner"]
    blobs = LocalBlobStore(tmp_path / "blobs")
    cid = await uc.create_company(db, owner, name="가상전자", homepage="corp.example.test")
    page = await uc.import_paste(db, env["gateway"], owner, cid, PAGE_TEXT)
    pid = await make_posting(db, owner, "https://jobs.example.test/r/1", "가상전자")
    await uc.link_postings(db, owner, cid, [pid])
    # 오래된 원문 (가져온 날이 1년 넘음) → 기준일 없는 항목은 오래됨
    async with db.workspace_session(owner.workspace_id) as s:
        old = datetime.now(UTC) - timedelta(days=400)
        await s.execute(update(CompanyPage).values(fetched_at=old))
        await s.execute(update(CompanyFact).values(fetched_at=old))
    before = await uc.get_company(db, owner, cid)
    assert len(before.facts) == 3 and all(f.stale for f in before.facts)

    out = tmp_path / "a.tar.gz"
    report = await export_workspace(db, blobs, owner, out)
    import tarfile

    with tarfile.open(out, "r:gz") as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())
    rows = {t["name"]: t["rows"] for t in manifest["tables"]}
    assert (rows["core.companies"], rows["core.company_pages"], rows["core.company_facts"]) == (
        1,
        1,
        3,
    )
    copy = await restore_workspace(db, blobs, out, new_ids=True)
    assert copy.rows_total == report.rows_total
    [new_owner] = copy.owners
    actor = ActorContext(new_owner, copy.workspace_id, scopes_for_role(Role.OWNER))
    [restored] = await uc.list_companies(db, actor)
    assert restored.id != cid and restored.postings == 1
    after = await uc.get_company(db, actor, restored.id)
    assert sorted(
        (f.kind, f.body, f.as_of, f.stale, f.citation.quote) for f in after.facts
    ) == sorted((f.kind, f.body, f.as_of, f.stale, f.citation.quote) for f in before.facts)
    assert {f.citation.page_id for f in after.facts} == {after.pages[0].id} != {page.id}
    assert after.homepage == "https://corp.example.test/"

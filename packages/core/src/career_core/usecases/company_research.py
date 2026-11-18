"""회사 정보 알아서 찾기 (c0053). 규칙: ``career_core.domain.company_research``.

- 공고를 저장하면(``confirm_posting``) 공고의 회사 이름으로 회사를 잇는다. 이름·별칭 키가 같은 회사가 있으면
  그 회사, 없으면 새로 만든다. 다른 회사로 잘못 이어졌으면 회사 화면에서 끊는다.
- 회사에 처음 이어지거나 새 공고가 이어지면 찾기 작업(``company_research``)을 한 번 건다. 사람이 회사 화면에서
  "다시 찾기"로도 건다. 주기로 다시 찾지 않는다.
- 찾은 글은 회사 원문(``company_pages``, origin ``posting``·``found``)이 되고 항목은 다른 원문과 같은 규칙으로
  뽑는다: 원문 인용이 있는 것만, 후보로. 모델이 없으면 원문만 남는다.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from sqlalchemy import or_, select

from career_core.db import Database, Job, JobPosting, JobPostingVersion
from career_core.db.base import utcnow
from career_core.db.models import Company, CompanyPage
from career_core.domain.companies import CompanyRuleError, PageStatus, clean_name, name_key
from career_core.domain.company_research import (
    MAX_POSTING_PAGES,
    homepage_from_posting,
    page_links,
    pick_entity,
    pick_links,
    wikidata_entities_url,
    wikidata_search_url,
)
from career_core.domain.postings import FieldName, PostingRuleError, normalize_url
from career_core.jobs.lease import guard_lease
from career_core.ports import ActorContext
from career_core.usecases import companies as cuc
from career_core.usecases.brain import NotFound
from career_core.usecases.parsing import StepOutcome

RESEARCH_JOB = "company_research"
PRIVATE_METHODS = {"browser_capture"}  # 내 브라우저에서 보낸 공고 글: 로그인한 화면일 수 있다


# ---------------------------------------------------------------- 공고 → 회사 잇기


async def link_posting_company(db: Database, actor: ActorContext, posting_id: UUID) -> UUID | None:
    """저장한 공고를 회사에 잇고(없으면 만든다) 필요하면 찾기를 건다. 이은 회사 ID (회사 이름이 없으면 None)."""
    async with db.workspace_session(actor.workspace_id) as s:
        posting = await s.get(JobPosting, posting_id)
        if posting is None:
            raise NotFound("posting")
        written = (posting.fields or {}).get(FieldName.COMPANY)
        company_id = posting.company_id
        if company_id is None:
            if not isinstance(written, str) or not name_key(written):
                return None
            key = name_key(written)
            rows = (await s.scalars(select(Company))).all()
            match = next(
                (c for c in rows if key in {name_key(n) for n in [c.name, *c.aliases]}), None
            )
            if match is None:
                try:
                    clean = clean_name(written)
                except CompanyRuleError:
                    return None
                match = Company(
                    id=uuid4(),
                    workspace_id=actor.workspace_id,
                    name=clean,
                    name_key=name_key(clean),
                    aliases=[],
                    created_by=actor.subject_id,
                )
                s.add(match)
                await s.flush()
            posting.company_id = match.id
            posting.updated_at = utcnow()
            company_id = match.id
        page_urls = set(
            (
                await s.scalars(
                    select(CompanyPage.url).where(
                        CompanyPage.company_id == company_id, CompanyPage.origin == "posting"
                    )
                )
            ).all()
        )
        company = await s.get(Company, company_id)
        needs = company.research_status is None or posting.canonical_url not in page_urls
    if needs:
        await request_research(db, actor, company_id)
    return company_id


async def request_research(db: Database, actor: ActorContext, company_id: UUID) -> str:
    """찾기 작업을 건다. 이미 기다리는 중이면 그대로 둔다. 회사의 찾기 상태를 돌려준다."""
    async with db.workspace_session(actor.workspace_id) as s:
        company = await s.get(Company, company_id)
        if company is None:
            raise NotFound("company")
        queued = await s.scalar(
            select(Job.id).where(
                Job.kind == RESEARCH_JOB,
                Job.target_id == company_id,
                Job.status.in_(("queued", "running")),
            )
        )
        company.research_status = PageStatus.PENDING.value
        company.research_requested_by = actor.subject_id
        company.updated_at = utcnow()
        if queued is None:
            s.add(Job(workspace_id=actor.workspace_id, kind=RESEARCH_JOB, target_id=company_id))
        return company.research_status


# ---------------------------------------------------------------- 찾기 (worker)


async def _json(deps, url: str) -> dict | None:
    got = await deps.fetcher.fetch(url)
    text = getattr(got, "text", None)
    if not isinstance(text, str) or not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def wikidata_lookup(deps, name: str, aliases: list[str]) -> dict | None:
    """회사 이름으로 위키데이터를 찾아 공식 웹사이트·위키백과 문서. 이름·별칭이 같은 엔터티만 받는다."""
    keys = {k for k in (name_key(n) for n in [name, *aliases]) if k}
    if not keys:
        return None
    order: list[str] = []
    for lang in ("ko", "en"):
        found = await _json(deps, wikidata_search_url(name, lang))
        for hit in (found or {}).get("search") or []:
            qid = hit.get("id") if isinstance(hit, dict) else None
            if isinstance(qid, str) and qid.startswith("Q") and qid not in order:
                order.append(qid)
        if order:
            break  # 한국어로 찾으면 영어로는 묻지 않는다 (요청은 공고당 두세 번)
    if not order:
        return None
    data = await _json(deps, wikidata_entities_url(order[:10]))
    return pick_entity((data or {}).get("entities") or {}, order, keys, name_key, name=name)


def _new_page(ws: UUID, company_id: UUID, origin: str, url: str, by: UUID) -> CompanyPage:
    return CompanyPage(
        id=uuid4(),
        workspace_id=ws,
        company_id=company_id,
        origin=origin,
        url=url[:2000],
        status=PageStatus.PENDING.value,
        requested_by=by,
    )


async def _posting_pages(deps, ws: UUID, company_id: UUID, by: UUID, known: set) -> int:
    """이 회사 공고의 글을 원문으로 (공고마다 최근 버전, 같은 글이면 건너뛴다)."""
    db = deps.db
    async with db.workspace_session(ws) as s:
        postings = (
            await s.scalars(
                select(JobPosting)
                .where(JobPosting.company_id == company_id, JobPosting.status != "archived")
                .order_by(JobPosting.updated_at.desc())
                .limit(MAX_POSTING_PAGES)
            )
        ).all()
        items = []
        for p in postings:
            v = await s.scalar(
                select(JobPostingVersion)
                .where(JobPostingVersion.posting_id == p.id)
                .order_by(JobPostingVersion.version_no.desc())
                .limit(1)
            )
            if v is None or not (v.text or "").strip():
                continue
            text = v.text[: cuc.MAX_PAGE_CHARS]
            if (p.canonical_url, cuc._digest(text)) in known:
                continue
            title = (p.fields or {}).get(FieldName.TITLE)
            items.append((p.canonical_url, title, text, v.method))
    added = 0
    actor = ActorContext(by, ws)
    for url, title, text, method in items:
        data_class = "private" if method in PRIVATE_METHODS else "public"
        found, call_id, notes = await cuc.extract_candidates(
            deps.gateway, actor, text, data_class=data_class
        )
        async with db.workspace_session(ws) as s:
            page = _new_page(ws, company_id, "posting", url, by)
            page.status = PageStatus.DONE.value
            page.final_url = url[:2000]
            page.method = method
            page.title = (title if isinstance(title, str) else None) and title[:300]
            page.text = text
            page.content_hash = cuc._digest(text)
            page.fetched_at = utcnow()
            page.model_call_id = call_id
            page.notes = notes
            s.add(page)
            await s.flush()
            page.candidates = cuc._store_candidates(s, ws, by, page, found, call_id)
        added += 1
    return added


async def process_research(deps, ws: UUID, company_id: UUID) -> StepOutcome:
    db = deps.db
    async with db.workspace_session(ws) as s:
        company = await s.get(Company, company_id)
        if company is None or company.research_status != PageStatus.PENDING:
            return StepOutcome(done=True)
        by = company.research_requested_by or company.created_by
        name, aliases, homepage = company.name, list(company.aliases), company.homepage
        pages = (
            await s.execute(
                select(CompanyPage.url, CompanyPage.final_url, CompanyPage.content_hash).where(
                    CompanyPage.company_id == company_id,
                    or_(CompanyPage.status != PageStatus.FAILED, CompanyPage.origin == "found"),
                )
            )
        ).all()
        posting_urls = (
            await s.scalars(
                select(JobPosting.canonical_url).where(JobPosting.company_id == company_id)
            )
        ).all()
    known_urls = {u.rstrip("/") for row in pages for u in (row[0], row[1]) if u}
    known = {(row[0], row[2]) for row in pages}
    notes: list[str] = []
    actor = ActorContext(by, ws)

    added = await _posting_pages(deps, ws, company_id, by, known)
    if added:
        notes.append(f"posting_pages:{added}")

    # 홈페이지: 등록한 것 → 공고 주소에서 짐작 → 위키데이터
    source = "saved" if homepage else None
    if not homepage:
        for u in posting_urls:
            if guess := homepage_from_posting(u):
                homepage, source = guess, "posting_url"
                break
    wiki = None
    entity = await wikidata_lookup(deps, name, aliases)
    if entity:
        notes.append(f"wikidata:{entity['qid']}")
        wiki = entity.get("wiki")
        if not homepage and entity.get("homepage"):
            homepage, source = entity["homepage"], "wikidata"
    if homepage:
        try:
            homepage = normalize_url(homepage)
        except PostingRuleError:
            homepage = None
    queue: list[str] = []
    if homepage:
        notes.append(f"homepage:{source}")
        if homepage.rstrip("/") not in known_urls:
            got = await cuc.fetch_text(deps, homepage)
            if isinstance(got, cuc.FetchedText):
                found, call_id, fnotes = await cuc.extract_candidates(deps.gateway, actor, got.text)
                async with db.workspace_session(ws) as s:
                    page = _new_page(ws, company_id, "found", homepage, by)
                    cuc.fill_page(page, got, call_id, fnotes)
                    s.add(page)
                    await s.flush()
                    page.candidates = cuc._store_candidates(s, ws, by, page, found, call_id)
                raw = got.result
            else:
                raw = got.page  # 글이 짧은 홈페이지도 링크는 쓴다
                notes.append(
                    "homepage_no_text" if raw is not None else f"homepage_failed:{got.code}"
                )
            if raw is not None:
                known_urls |= {homepage.rstrip("/"), raw.final_url.rstrip("/")}
                links = page_links(raw.text, raw.final_url)
                queue += pick_links(links, raw.final_url, skip=known_urls)
    else:
        notes.append("homepage:none")
    if wiki and wiki.rstrip("/") not in known_urls:
        queue.append(wiki)

    async with db.workspace_session(ws) as s:
        await guard_lease(s, company_id)
        company = await s.get(Company, company_id)
        if company is None:
            return StepOutcome(done=True)
        for url in queue:
            page = _new_page(ws, company_id, "found", url, by)
            s.add(page)
            await s.flush()
            s.add(Job(workspace_id=ws, kind=cuc.COMPANY_FETCH_JOB, target_id=page.id))
        if queue:
            notes.append(f"found_pages:{len(queue)}")
        if homepage and not company.homepage and source in ("posting_url", "wikidata"):
            company.homepage = homepage[:500]
        company.research_status = PageStatus.DONE.value
        company.researched_at = utcnow()
        company.research_notes = notes
        company.updated_at = utcnow()
    return StepOutcome(done=True)


async def give_up_research(deps, ws: UUID, company_id: UUID) -> None:
    async with deps.db.workspace_session(ws) as s:
        company = await s.get(Company, company_id)
        if company is not None and company.research_status == PageStatus.PENDING:
            company.research_status = PageStatus.FAILED.value
            company.researched_at = utcnow()
            company.updated_at = utcnow()

"""지원 문서 ↔ 브레인: 써 둔 글을 브레인에 넣고, 확인한 주장을 원래 문단에 다시 잇는다 (2026-11-19). 글은 합성."""

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import DocumentBlock, ParseRun, Source, SourceVersion
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import parsing_handlers, run_until_idle
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext
from career_core.usecases import applications as app_uc
from career_core.usecases import brain
from career_core.usecases import document_brain as uc
from career_core.usecases import postings as postings_uc
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import select

pytestmark = pytest.mark.anyio

PARAS = [
    "[세 번 실패한 배포를 끝까지 살렸습니다]",
    "결제 서버 배포가 세 번 실패했을 때 실패 로그를 하나씩 대조해 환경 변수 누락을 찾았습니다.",
    "배포 뒤 일주일 동안 매일 아침 오류 지표를 직접 확인했습니다.",
    "입사 후에는 끝까지 책임지는 개발자가 되겠습니다.",
]


class FakeResolver:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


@pytest.fixture
async def env(db, tmp_path):
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    owner = ActorContext(a, ws_a, scopes_for_role(Role.OWNER))
    other = ActorContext(b, ws_b, scopes_for_role(Role.OWNER))
    blobs = LocalBlobStore(tmp_path / "blobs")
    return {"owner": owner, "other": other, "blobs": blobs}


async def essay_document(db, owner):
    fields = {
        "title": "백엔드 (합성)",
        "company": "가상테크",
        "questions": [{"prompt": "어려움을 이겨 낸 경험"}],
    }
    [r] = await postings_uc.save_urls(db, owner, ["https://jobs.example.test/b/1"])
    await postings_uc.confirm_posting(db, owner, r.posting_id, overrides=fields)
    aid = await app_uc.create_application(db, owner, r.posting_id)
    [doc] = (await app_uc.get_application(db, owner, aid)).documents
    await app_uc.save_revision(
        db,
        owner,
        aid,
        doc.id,
        base_revision=0,
        operations=[{"op": "insert", "text": t} for t in PARAS],
    )
    return aid, doc.id


async def parse_all(db, blobs):
    deps = ParsingDeps(
        db=db, blobs=blobs, egress=ProviderEgressPolicy(), local_parsers=default_local_parsers()
    )
    await run_until_idle(db, parsing_handlers(deps))


async def block_with(db, owner, source_id, needle):
    async with db.workspace_session(owner.workspace_id) as s:
        rows = (
            await s.scalars(
                select(DocumentBlock)
                .join(ParseRun, ParseRun.id == DocumentBlock.parse_run_id)
                .join(SourceVersion, SourceVersion.id == ParseRun.source_version_id)
                .where(SourceVersion.source_id == source_id)
            )
        ).all()
    return next(b for b in rows if needle in b.text)


async def claim_on(db, owner, block, needle, *, confirm):
    start = block.text.index(needle)
    span = await brain.create_span(db, owner, block.id, start, start + len(needle))
    exp = await brain.create_experience(db, owner, title="결제 서버 배포 (합성)")
    claim = await brain.add_claim(db, owner, exp, statement=needle, kind="action", span_ids=[span])
    if confirm:
        await brain.confirm_claim(db, owner, claim, contribution="personal")
    return claim


async def test_send_to_brain_and_link_back(db, env):
    owner, blobs = env["owner"], env["blobs"]
    aid, did = await essay_document(db, owner)

    before = await uc.brain_matches(db, owner, aid, did)
    assert (before.in_brain, before.blocks, before.confirmed_total) == (False, [], 0)

    res, rendered = await uc.send_to_brain(
        db, blobs, owner, aid, did, max_bytes=1 << 20, auto_extract=False
    )
    assert rendered.title == "가상테크 · 어려움을 이겨 낸 경험 (지원서)"
    assert rendered.paragraphs == 4 and rendered.withheld == 0
    text = rendered.data.decode()
    assert text.startswith("# 가상테크 · 어려움을 이겨 낸 경험 (지원서)\n\n[세 번")
    assert "\n\n".join(PARAS) in text
    async with db.workspace_session(owner.workspace_id) as s:
        src = await s.get(Source, res.source_id)
    assert (src.purpose, src.data_class) == ("experience", "private")
    # 같은 글을 다시 보내면 처음 원본
    again, _ = await uc.send_to_brain(
        db, blobs, owner, aid, did, max_bytes=1 << 20, auto_extract=False
    )
    assert again.deduplicated and again.source_id == res.source_id

    await parse_all(db, blobs)
    b1 = await block_with(db, owner, res.source_id, "결제 서버 배포가")
    b2 = await block_with(db, owner, res.source_id, "오류 지표")
    sure = await claim_on(
        db, owner, b1, "실패 로그를 하나씩 대조해 환경 변수 누락을 찾았습니다", confirm=True
    )
    await claim_on(db, owner, b2, "매일 아침 오류 지표를 직접 확인했습니다", confirm=False)
    # 너무 짧은 인용은 우연히 겹칠 수 있어 보지 않는다
    await claim_on(db, owner, b2, "확인했습니다", confirm=True)

    got = await uc.brain_matches(db, owner, aid, did)
    assert got.in_brain and got.source_id == res.source_id
    assert got.review_source_id == res.source_id  # 확인 전 후보가 나온 원본
    assert (got.confirmed_total, got.pending_total) == (1, 1)
    view = await app_uc.get_document(db, owner, aid, did)
    ids = [b.id for b in view.blocks]
    by = {m.block_id: m for m in got.blocks}
    assert [c.claim_id for c in by[ids[1]].confirmed] == [sure] and by[ids[1]].pending == 0
    assert by[ids[2]].confirmed == [] and by[ids[2]].pending == 1
    assert by[ids[1]].confirmed[0].experience_title == "결제 서버 배포 (합성)"

    # 사람이 연결하면 후보에서 빠지고, 검사의 "근거 없는 경험·수치"도 그 문단에서 빠진다
    await app_uc.save_revision(
        db,
        owner,
        aid,
        did,
        base_revision=view.revision_no,
        operations=[{"op": "link", "block_id": ids[1], "claim_ids": [str(sure)]}],
    )
    after = await uc.brain_matches(db, owner, aid, did)
    assert after.confirmed_total == 0 and after.pending_total == 1
    assert after.in_brain  # 연결만 바뀌고 글은 같다 (내용 해시는 글만 본다)


async def test_other_workspaces_and_http(db, env):
    owner, other, blobs = env["owner"], env["other"], env["blobs"]
    aid, did = await essay_document(db, owner)
    # 참고 자료·증빙은 근거(원문 위치)가 될 수 없다: brain.create_span이 막는다 (source_not_experience)
    with pytest.raises(app_uc.NotFound):
        await uc.brain_matches(db, other, aid, did)
    with pytest.raises(app_uc.NotFound):
        await uc.send_to_brain(db, blobs, other, aid, did, max_bytes=1 << 20, auto_extract=False)

    tokens = {"owner": owner, "other": other}
    deps = CoreDeps(identity=FakeResolver(tokens), db=db, blobs=blobs)
    app = FastAPI()
    app.include_router(build_router(deps))
    base = f"/core/applications/{aid}/documents/{did}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        h = {"Authorization": "Bearer owner"}
        r = await c.post(base + "/to-brain", headers=h)
        assert r.status_code == 201, r.text
        assert r.json()["paragraphs"] == 4 and r.json()["deduplicated"] is False
        r = await c.get(base + "/brain-matches", headers=h)
        assert r.status_code == 200 and r.json()["in_brain"] is True
        r = await c.get(base + "/brain-matches", headers={"Authorization": "Bearer other"})
        assert r.status_code == 404

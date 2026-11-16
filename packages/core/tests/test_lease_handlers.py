"""실행 밖의 작업 처리기(추출·공고 가져오기)도 lease를 잃은 뒤의 늦은 저장을 버린다 (``guard_lease``).

두 worker: A가 처리 도중 멈춰 lease(1초)를 잃고, B가 다시 집어 끝낸다. 늦게 깨어난 A의 저장은 되돌려진다.
모든 자료는 합성이다.
"""

import asyncio
import json

import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.adapters.web_fetch import FetchedPage
from career_core.db import Experience, ExtractionRun, Job, JobFetchRun, JobPostingVersion
from career_core.domain.workspace import Role, scopes_for_role
from career_core.jobs.worker import (
    extraction_handlers,
    parsing_handlers,
    posting_handlers,
    run_once,
    run_until_idle,
)
from career_core.llm import ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext
from career_core.usecases import postings as posting_uc
from career_core.usecases.extraction import ExtractionDeps, get_extraction, request_extraction
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.sources import upload_source
from career_core.usecases.workspaces import create_workspace
from sqlalchemy import func, select, update

pytestmark = pytest.mark.anyio

CONFIG = parse_models_config("""
[endpoints.proxy]
preset = "cliproxyapi"
price_as = "openai"
data_classes = ["public", "private"]

[routes]
default = "proxy/gpt-6.1-sol"
""")
ENV = {"CLIPROXY_API_KEY": "k"}

RESUME = (
    "Synthetic Co, Backend Engineer, 2021.03 - 2023.02\n\n"
    "Built the order service in FastAPI and cut p95 latency from 800ms to 200ms."
)
GOOD_REPLY = json.dumps(
    {
        "experiences": [
            {
                "title": "Order service",
                "claims": [
                    {
                        "kind": "result",
                        "statement": "p95 지연을 800ms에서 200ms로 줄였다.",
                        "quotes": [{"block": 1, "text": "cut p95 latency from 800ms to 200ms"}],
                    }
                ],
            }
        ]
    }
)


@pytest.fixture
async def owner(db):
    ws, a = await create_workspace(db, name="L", owner_display_name="lee")
    return ActorContext(a, ws, scopes_for_role(Role.OWNER))


class FirstCallWaits:
    """첫 호출(worker A)은 신호를 받을 때까지 멈췄다가 ``first``를, 다음 호출(worker B)은 바로 ``rest``를."""

    def __init__(self, first, rest):
        self.first, self.rest = first, rest
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def wait_if_first(self):
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            await self.release.wait()
            return self.first
        return self.rest


class WaitingModel(FirstCallWaits):
    async def complete(self, model, request):
        text = await self.wait_if_first()
        return ModelCompletion(
            text=text,
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, TokenUsage(input_tokens=50, output_tokens=20)),),
            request_id="req",
        )


class WaitingFetcher(FirstCallWaits):
    async def fetch(self, url):
        body = await self.wait_if_first()
        return FetchedPage(url, url, 200, "text/plain", body)


async def two_workers(db, handlers, gate: FirstCallWaits):
    """A(lease 1초, heartbeat 꺼짐 = 멈춘 프로세스)가 멈춘 사이 B가 집어 끝낸다. 그 뒤 A를 깨운다."""
    a = asyncio.create_task(run_once(db, handlers, lease_seconds=1, heartbeat_seconds=0))
    await asyncio.wait_for(gate.entered.wait(), 10)
    await asyncio.sleep(1.3)
    assert await run_once(db, handlers, lease_seconds=60, heartbeat_seconds=0)  # B
    gate.release.set()
    assert await asyncio.wait_for(a, 10) is True


async def job_of(db, ws, target_id):
    async with db.workspace_session(ws) as s:
        return await s.scalar(select(Job).where(Job.target_id == target_id))


async def test_stale_extraction_worker_does_not_overwrite_the_finished_extraction(
    db, owner, tmp_path
):
    """A는 늦게 잘못된 출력을 받는다. 확인이 없으면 ``_fail``이 B의 ``complete``를 ``failed``로 덮었을 것이다."""
    blobs = LocalBlobStore(tmp_path / "blobs")
    up = await upload_source(
        db,
        blobs,
        owner,
        filename="cv.txt",
        data=RESUME.encode(),
        declared_mime="text/plain",
        max_bytes=1 << 20,
        data_class="private",
    )
    parse_deps = ParsingDeps(
        db=db, blobs=blobs, egress=ProviderEgressPolicy(), local_parsers=default_local_parsers()
    )
    await run_until_idle(db, parsing_handlers(parse_deps))
    ext_id = await request_extraction(db, owner, up.parse_run_id)

    model = WaitingModel("not json at all", GOOD_REPLY)
    gw = ModelGateway(db, CONFIG, PriceBook.load(), client_factory=lambda ep, key: model, env=ENV)
    handlers = extraction_handlers(ExtractionDeps(db, gw))
    await two_workers(db, handlers, model)

    view = await get_extraction(db, owner, ext_id)
    assert (view.status, view.error_code, view.experiences_created, view.claims_created) == (
        "complete",
        None,
        1,
        1,
    )
    async with db.workspace_session(owner.workspace_id) as s:
        assert await s.scalar(select(func.count()).select_from(Experience)) == 1
        ext = await s.get(ExtractionRun, ext_id)
    assert ext.model_call_id is not None
    job = await job_of(db, owner.workspace_id, ext_id)
    assert job.status == "done"
    assert model.calls == 2  # 두 worker 모두 모델을 불렀다 (비용은 장부에 남는다), 저장은 B만


async def test_stale_fetch_worker_does_not_add_an_older_version(db, owner):
    """A는 옛 본문을 늦게 받는다. 확인이 없으면 B의 버전 뒤에 옛 본문이 최신 버전으로 더해졌을 것이다."""
    old = "가상회사 백엔드 개발자 (옛 공고) " + "합성 공고 본문입니다. " * 10
    new = "가상회사 백엔드 개발자 (새 공고) " + "합성 공고 본문입니다. " * 10
    fetcher = WaitingFetcher(old, new)
    deps = posting_uc.PostingDeps(db=db, fetcher=fetcher)
    [saved] = await posting_uc.save_urls(db, owner, ["https://jobs.example.test/lease/1"])
    await two_workers(db, posting_handlers(deps), fetcher)

    async with db.workspace_session(owner.workspace_id) as s:
        versions = (
            await s.scalars(
                select(JobPostingVersion).where(JobPostingVersion.posting_id == saved.posting_id)
            )
        ).all()
        run = await s.get(JobFetchRun, saved.fetch_run_id)
    assert [v.text.split("(")[1].split(")")[0] for v in versions] == ["새 공고"]
    assert (run.status, run.version_id, run.unchanged) == ("done", versions[0].id, False)
    assert (await job_of(db, owner.workspace_id, saved.fetch_run_id)).status == "done"


async def test_expired_fetch_lease_without_a_new_owner_is_discarded_then_redone(db, owner):
    """lease가 지나기만 했어도 저장하지 않는다. 작업은 ``running``으로 남고 다음 worker가 다시 한다."""
    body = "가상회사 데이터 엔지니어 " + "합성 공고 본문입니다. " * 10
    fetcher = WaitingFetcher(body, body)
    deps = posting_uc.PostingDeps(db=db, fetcher=fetcher)
    handlers = posting_handlers(deps)
    [saved] = await posting_uc.save_urls(db, owner, ["https://jobs.example.test/lease/2"])
    a = asyncio.create_task(run_once(db, handlers, lease_seconds=1, heartbeat_seconds=0))
    await asyncio.wait_for(fetcher.entered.wait(), 10)
    await asyncio.sleep(1.3)
    fetcher.release.set()
    assert await asyncio.wait_for(a, 10) is True
    async with db.workspace_session(owner.workspace_id) as s:
        run = await s.get(JobFetchRun, saved.fetch_run_id)
        assert run.status == "pending" and run.version_id is None
    assert (await job_of(db, owner.workspace_id, saved.fetch_run_id)).status == "running"
    assert await run_until_idle(db, handlers) == 1
    async with db.workspace_session(owner.workspace_id) as s:
        assert (await s.get(JobFetchRun, saved.fetch_run_id)).status == "done"


class BrokenFetcher:
    async def fetch(self, url):
        raise RuntimeError("synthetic")


async def test_give_up_still_records_after_the_worker_closed_the_job(db, owner):
    """마지막 시도가 예외로 끝나면 worker가 작업을 ``failed``로 닫고 ``give_up``을 부른다. 그 저장은 lease 확인
    (이제 ``running``이 아님)에 막히지 않는다."""
    deps = posting_uc.PostingDeps(db=db, fetcher=BrokenFetcher())
    [saved] = await posting_uc.save_urls(db, owner, ["https://jobs.example.test/lease/3"])
    async with db.workspace_session(owner.workspace_id) as s:
        await s.execute(update(Job).where(Job.target_id == saved.fetch_run_id).values(attempts=4))
    assert await run_once(db, posting_handlers(deps), heartbeat_seconds=0)
    job = await job_of(db, owner.workspace_id, saved.fetch_run_id)
    assert (job.status, job.attempts, job.last_error) == ("failed", 5, "RuntimeError")
    async with db.workspace_session(owner.workspace_id) as s:
        run = await s.get(JobFetchRun, saved.fetch_run_id)
    assert (run.status, run.error_code) == ("failed", "source_unavailable")

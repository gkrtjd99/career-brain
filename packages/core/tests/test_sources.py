"""O03 완료 기준: 같은 파일 중복 처리 방지, 부분 파싱 검출, 금지 자료 전송 차단."""

from uuid import UUID

import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import DocumentBlock, Job, ParseRun, SourceVersion
from career_core.domain.workspace import Role, scopes_for_role
from career_core.jobs.worker import JobHandler, parsing_handlers, run_once, run_until_idle
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext, ParsedBlock, ProviderParseStatus
from career_core.testing import make_pdf
from career_core.uploads import UploadRejected
from career_core.usecases.parsing import ParsingDeps, StepOutcome
from career_core.usecases.sources import upload_source
from career_core.usecases.workspaces import create_workspace
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio
MAX = 1024 * 1024


class FakeOcr:
    name = "fake-ocr"

    def __init__(self, *, pending_polls: int = 0, returned: set[int] | None = None):
        self.submissions: list[tuple[list[int], str]] = []
        self.polls = 0
        self.pending_polls = pending_polls
        self.returned = returned

    async def submit(self, data, mime_type, pages, idempotency_key):
        self.submissions.append((list(pages), idempotency_key))
        return f"job-{len(self.submissions)}"

    async def poll(self, provider_job_id):
        self.polls += 1
        if self.polls <= self.pending_polls:
            return ProviderParseStatus(state="pending")
        pages = self.returned if self.returned is not None else set(self.submissions[-1][0])
        return ProviderParseStatus(
            state="done",
            blocks=tuple(ParsedBlock(page=p, text=f"ocr text page {p}") for p in sorted(pages)),
            returned_pages=frozenset(pages),
        )


@pytest.fixture
async def owner(db):
    ws, actor = await create_workspace(db, name="A", owner_display_name="alice")
    return ActorContext(actor, ws, scopes_for_role(Role.OWNER))


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blobs")


def deps(db, blobs, *, provider=None, allowed=None):
    return ParsingDeps(
        db=db,
        blobs=blobs,
        egress=ProviderEgressPolicy(allowed or {}),
        local_parsers=default_local_parsers(),
        provider=provider,
        poll_delay_seconds=0,
    )


async def upload(db, blobs, actor, data, *, name="a.txt", mime="text/plain", data_class="private"):
    return await upload_source(
        db,
        blobs,
        actor,
        filename=name,
        data=data,
        declared_mime=mime,
        max_bytes=MAX,
        data_class=data_class,
    )


async def run_of(db, actor, run_id: UUID) -> ParseRun:
    async with db.workspace_session(actor.workspace_id) as s:
        return await s.get(ParseRun, run_id)


async def count(db, actor, model) -> int:
    async with db.workspace_session(actor.workspace_id) as s:
        return await s.scalar(select(func.count()).select_from(model))


# --- 중복 처리 방지 ---------------------------------------------------------


async def test_same_file_is_stored_and_parsed_once(db, blobs, owner, tmp_path):
    first = await upload(db, blobs, owner, b"Built a job tracker.\n\nUsed FastAPI.")
    second = await upload(
        db, blobs, owner, b"Built a job tracker.\n\nUsed FastAPI.", name="copy.txt"
    )
    assert (second.source_id, second.version_id, second.parse_run_id) == (
        first.source_id,
        first.version_id,
        first.parse_run_id,
    )
    assert second.deduplicated and not first.deduplicated
    assert await count(db, owner, SourceVersion) == 1
    assert await count(db, owner, ParseRun) == 1
    assert await count(db, owner, Job) == 1
    assert len([p for p in (tmp_path / "blobs").rglob("*") if p.is_file()]) == 1

    handlers = parsing_handlers(deps(db, blobs))
    assert await run_until_idle(db, handlers) == 1
    third = await upload(db, blobs, owner, b"Built a job tracker.\n\nUsed FastAPI.")
    assert third.deduplicated
    assert await run_until_idle(db, handlers) == 0  # 이미 처리된 파일은 다시 파싱하지 않는다
    run = await run_of(db, owner, first.parse_run_id)
    assert run.status == "complete"
    assert await count(db, owner, DocumentBlock) == 2


async def test_dedupe_does_not_cross_workspaces(db, blobs, owner):
    ws_b, actor_b = await create_workspace(db, name="B", owner_display_name="bob")
    other = ActorContext(actor_b, ws_b, scopes_for_role(Role.OWNER))
    a = await upload(db, blobs, owner, b"same bytes")
    b = await upload(db, blobs, other, b"same bytes")
    assert not b.deduplicated
    assert a.version_id != b.version_id


# --- 부분 파싱 검출 ---------------------------------------------------------


async def test_pdf_with_textless_page_is_partial(db, blobs, owner):
    res = await upload(
        db,
        blobs,
        owner,
        make_pdf(["Resume page one", "", "Page three"]),
        name="cv.pdf",
        mime="application/pdf",
    )
    await run_until_idle(db, parsing_handlers(deps(db, blobs)))
    run = await run_of(db, owner, res.parse_run_id)
    assert (run.status, run.pages_total, run.missing_pages, run.error_code) == (
        "partial",
        3,
        [2],
        "ocr_unavailable",
    )
    async with db.workspace_session(owner.workspace_id) as s:
        pages = (
            (await s.execute(select(DocumentBlock.page).order_by(DocumentBlock.ordinal)))
            .scalars()
            .all()
        )
    assert pages == [1, 3]


async def test_image_without_ocr_fails_with_reason(db, blobs, owner):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    res = await upload(db, blobs, owner, png, name="scan.png", mime="image/png")
    await run_until_idle(db, parsing_handlers(deps(db, blobs)))
    run = await run_of(db, owner, res.parse_run_id)
    assert (run.status, run.missing_pages, run.error_code) == ("failed", [1], "ocr_unavailable")


async def test_provider_returns_fewer_pages_than_requested(db, blobs, owner):
    ocr = FakeOcr(returned={2})
    res = await upload(
        db, blobs, owner, make_pdf(["one", "", ""]), name="cv.pdf", mime="application/pdf"
    )
    await run_until_idle(
        db, parsing_handlers(deps(db, blobs, provider=ocr, allowed={"fake-ocr": ["private"]}))
    )
    run = await run_of(db, owner, res.parse_run_id)
    assert ocr.submissions[0][0] == [2, 3]
    assert (run.status, run.missing_pages, run.error_code) == ("partial", [3], "provider_partial")


# --- 외부 전송 정책 ---------------------------------------------------------


async def test_restricted_source_is_never_sent(db, blobs, owner):
    ocr = FakeOcr()
    res = await upload(
        db,
        blobs,
        owner,
        make_pdf(["", ""]),
        name="id.pdf",
        mime="application/pdf",
        data_class="restricted",
    )
    # restricted를 설정에 넣어도 정책이 걸러낸다
    d = deps(db, blobs, provider=ocr, allowed={"fake-ocr": ["public", "private", "restricted"]})
    await run_until_idle(db, parsing_handlers(d))
    run = await run_of(db, owner, res.parse_run_id)
    assert ocr.submissions == []
    assert (run.status, run.error_code, run.missing_pages) == ("failed", "egress_denied", [1, 2])


async def test_unconfigured_provider_is_denied(db, blobs, owner):
    ocr = FakeOcr()
    res = await upload(
        db, blobs, owner, make_pdf(["text", ""]), name="cv.pdf", mime="application/pdf"
    )
    await run_until_idle(db, parsing_handlers(deps(db, blobs, provider=ocr, allowed={})))
    assert ocr.submissions == []
    assert (await run_of(db, owner, res.parse_run_id)).error_code == "egress_denied"


async def test_allowed_provider_is_polled_not_resubmitted(db, blobs, owner):
    ocr = FakeOcr(pending_polls=2)
    res = await upload(
        db, blobs, owner, make_pdf(["intro", ""]), name="cv.pdf", mime="application/pdf"
    )
    handlers = parsing_handlers(deps(db, blobs, provider=ocr, allowed={"fake-ocr": ["private"]}))
    assert await run_once(db, handlers)  # 로컬 추출 + 제출
    run = await run_of(db, owner, res.parse_run_id)
    assert (run.status, run.provider_job_id) == ("waiting_provider", "job-1")
    # worker 재시작: 새 handler 묶음이어도 제출하지 않고 조회만 한다
    handlers = parsing_handlers(deps(db, blobs, provider=ocr, allowed={"fake-ocr": ["private"]}))
    await run_until_idle(db, handlers)
    assert len(ocr.submissions) == 1
    assert ocr.submissions[0] == ([2], str(res.parse_run_id))
    run = await run_of(db, owner, res.parse_run_id)
    assert (run.status, run.missing_pages) == ("complete", [])
    async with db.workspace_session(owner.workspace_id) as s:
        blocks = (
            (await s.execute(select(DocumentBlock).order_by(DocumentBlock.ordinal))).scalars().all()
        )
    assert [(b.page, b.origin) for b in blocks] == [(1, "local"), (2, "provider:fake-ocr")]


# --- 업로드 검사 ------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "mime", "name", "code"),
    [
        (b"MZ\x90\x00\x03", "application/pdf", "a.pdf", "unsupported_type"),
        (b"%PDF-1.4 fake", "image/png", "a.png", "mime_mismatch"),
        (b"plain text", "application/pdf", "a.pdf", "mime_mismatch"),
        (b"# readme", "application/pdf", "README.md", "mime_mismatch"),
        (b"# readme", "image/png", "notes.markdown", "mime_mismatch"),
        (b"", "text/plain", "a.txt", "empty"),
        (b"x" * (MAX + 1), "text/plain", "a.txt", "too_large"),
    ],
)
async def test_upload_rejections(db, blobs, owner, data, mime, name, code):
    with pytest.raises(UploadRejected) as err:
        await upload(db, blobs, owner, data, name=name, mime=mime)
    assert err.value.code == code
    assert await count(db, owner, SourceVersion) == 0


async def test_filename_path_is_not_used_for_storage(db, blobs, owner, tmp_path):
    res = await upload(db, blobs, owner, b"hello", name="../../etc/passwd")
    async with db.workspace_session(owner.workspace_id) as s:
        version = await s.get(SourceVersion, res.version_id)
    assert version.original_filename == "passwd"
    stored = [p for p in (tmp_path / "blobs").rglob("*") if p.is_file()]
    assert stored and all(
        p.is_relative_to(tmp_path / "blobs" / str(owner.workspace_id)) for p in stored
    )


async def test_html_scripts_are_not_extracted(db, blobs, owner):
    html = (
        b"<!doctype html><html><head><title>t</title><script>alert(1)</script></head>"
        b"<body><h1>Backend engineer</h1><p>Python &amp; FastAPI</p></body></html>"
    )
    res = await upload(db, blobs, owner, html, name="job.html", mime="text/html")
    await run_until_idle(db, parsing_handlers(deps(db, blobs)))
    async with db.workspace_session(owner.workspace_id) as s:
        texts = (
            (
                await s.execute(
                    select(DocumentBlock.text)
                    .where(DocumentBlock.parse_run_id == res.parse_run_id)
                    .order_by(DocumentBlock.ordinal)
                )
            )
            .scalars()
            .all()
        )
    assert texts == ["Backend engineer", "Python & FastAPI"]


# --- worker ---------------------------------------------------------------


async def test_failing_job_retries_then_gives_up(db, blobs, owner):
    res = await upload(db, blobs, owner, b"text")
    calls = {"run": 0, "give_up": 0}

    async def boom(ws, target):
        calls["run"] += 1
        raise RuntimeError("secret content must not be logged")

    async def give_up(ws, target):
        calls["give_up"] += 1

    handlers = {"parse": JobHandler(run=boom, give_up=give_up)}
    for _ in range(10):
        await run_once(db, handlers)
        async with db.workspace_session(owner.workspace_id) as s:
            job = await s.scalar(select(Job).where(Job.target_id == res.parse_run_id))
            job.available_at = job.created_at  # 백오프 기다리지 않기
    async with db.workspace_session(owner.workspace_id) as s:
        job = await s.scalar(select(Job).where(Job.target_id == res.parse_run_id))
    assert (job.status, job.last_error, calls) == (
        "failed",
        "RuntimeError",
        {"run": 5, "give_up": 1},
    )


async def test_jobs_table_is_not_readable_without_context(db, blobs, owner):
    await upload(db, blobs, owner, b"text")
    async with db.system_session() as s:
        assert await s.scalar(select(func.count()).select_from(Job)) == 0


async def test_handler_outcome_types():
    assert StepOutcome(done=True).retry_after is None

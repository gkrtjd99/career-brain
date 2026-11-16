import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import parsing_handlers, run_until_idle
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext
from career_core.testing import make_pdf
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI

pytestmark = pytest.mark.anyio


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


@pytest.fixture
async def env(db, tmp_path):
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    tokens = {
        "owner-a": ActorContext(a, ws_a, scopes_for_role(Role.OWNER)),
        "viewer-a": ActorContext(a, ws_a, scopes_for_role(Role.VIEWER)),
        "owner-b": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
    }
    blobs = LocalBlobStore(tmp_path)
    app = FastAPI()
    app.include_router(
        build_router(CoreDeps(identity=Tokens(tokens), db=db, blobs=blobs, max_upload_bytes=4096))
    )
    parsing = ParsingDeps(
        db=db, blobs=blobs, egress=ProviderEgressPolicy(), local_parsers=default_local_parsers()
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as client:
        yield client, parsing


def auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_upload_parse_and_read_back(env, db):
    client, parsing = env
    pdf = make_pdf(["Hello", ""])
    res = await client.post(
        "/core/sources",
        headers=auth("owner-a"),
        files={"file": ("cv.pdf", pdf, "application/pdf")},
        data={"title": "CV"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["deduplicated"] is False
    await run_until_idle(db, parsing_handlers(parsing))

    src = (await client.get(f"/core/sources/{body['source_id']}", headers=auth("owner-a"))).json()
    assert src["title"] == "CV"
    parse = src["versions"][0]["parse"]
    assert (parse["status"], parse["missing_pages"], parse["error_code"]) == (
        "partial",
        [2],
        "ocr_unavailable",
    )
    blocks = (
        await client.get(f"/core/parse-runs/{parse['run_id']}/blocks", headers=auth("owner-a"))
    ).json()
    assert [b["text"] for b in blocks] == ["Hello"]

    again = await client.post(
        "/core/sources",
        headers=auth("owner-a"),
        files={"file": ("cv-copy.pdf", pdf, "application/pdf")},
    )
    assert again.json()["deduplicated"] is True


async def test_upload_requires_write_scope_and_auth(env):
    client, _ = env
    files = {"file": ("a.txt", b"hello", "text/plain")}
    assert (await client.post("/core/sources", files=files)).status_code == 401
    assert (
        await client.post("/core/sources", headers=auth("viewer-a"), files=files)
    ).status_code == 403


async def test_rejections_map_to_http_status(env):
    client, _ = env
    big = await client.post(
        "/core/sources",
        headers=auth("owner-a"),
        files={"file": ("a.txt", b"x" * 5000, "text/plain")},
    )
    assert (big.status_code, big.json()["detail"]) == (413, "too_large")
    bad = await client.post(
        "/core/sources",
        headers=auth("owner-a"),
        files={"file": ("a.pdf", b"not a pdf", "application/pdf")},
    )
    assert (bad.status_code, bad.json()["detail"]) == (415, "mime_mismatch")


async def test_other_workspace_source_is_not_found(env):
    client, _ = env
    res = await client.post(
        "/core/sources", headers=auth("owner-a"), files={"file": ("a.txt", b"mine", "text/plain")}
    )
    body = res.json()
    assert (
        await client.get(f"/core/sources/{body['source_id']}", headers=auth("owner-b"))
    ).status_code == 404
    assert (
        await client.get(f"/core/parse-runs/{body['parse_run_id']}/blocks", headers=auth("owner-b"))
    ).status_code == 404

"""포트폴리오 (c0049, O18, plan §19.6).

- 항목은 경험 하나에 붙는다. 결과·한 일 줄은 확인한 사실에서 고르고 근거 연결을 유지한다 (portfolio_line_claims).
  직접 쓴 줄·문제·내 역할은 근거 없음. 사실이 바뀌면 오래됨/근거 사라짐으로 보이고 조용히 바뀌지 않는다.
- 공개 범위: 나만 항목·이미지는 미리보기·HTML 묶음·인쇄용 HTML·Markdown·MCP 어디에도 0건.
  지원서에만은 공고별 묶음의 출력에만 있다 (바이트로 확인).
- 이미지: 서버가 형식·용량·크기를 다시 보고 메타데이터를 지운다. 넘으면 거부한다.
- GitHub 가져오기: 가짜 HTTP(MockTransport + 가짜 DNS)만. 공개·한도·토큰·잘못된 주소·다른 호스트.
- 정적 HTML 묶음은 밖 주소 없이 상대 경로만. 백업·복원 왕복(이미지 포함), 다른 공간 0건, 권한.

모든 자료는 합성이다. 실제 네트워크를 쓰지 않는다.
"""

import base64
import io
import json
import re
import struct
import tarfile
import zipfile
from uuid import UUID, uuid4

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.github import ALLOWED_HOSTS, GitHubClient, parse_repo_url
from career_core.adapters.secret_box import SecretBox
from career_core.adapters.web_fetch import HttpFetcher
from career_core.db import Experience, JobPosting, JobPostingVersion
from career_core.db.models import (
    ApplicationPrep,
    GitHubToken,
    PortfolioImage,
    PortfolioItem,
    PortfolioLineClaim,
    Source,
)
from career_core.domain.brain import ClaimStatus
from career_core.domain.portfolio import image_shown, visible_in
from career_core.domain.portfolio_export import CSP_BUNDLE, CSP_SINGLE
from career_core.domain.profiles import has_metadata
from career_core.domain.resume_export import PAGINATE_JS
from career_core.domain.workspace import ACTION_SCOPES, Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.ports import ActorContext
from career_core.usecases import applications as app_uc
from career_core.usecases import brain
from career_core.usecases import portfolios as uc
from career_core.usecases import postings as posting_uc
from career_core.usecases import profiles as profile_uc
from career_core.usecases import resumes as resume_uc
from career_core.usecases.brain import NotFound
from career_core.usecases.export import export_workspace, restore_workspace
from career_core.usecases.markdown_export import MarkdownOptions, export_markdown
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import select

pytestmark = pytest.mark.anyio

TOKEN = "ghp_synthetic0000000000000000000000TEST"
README = "# 합성 저장소\n\n주문 서비스의 합성 README입니다.\n"


class FakeResolver:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


async def mcp_call(c, token, name, arguments):
    res = await c.post(
        "/core/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=auth(token),
    )
    assert res.status_code == 200, res.text
    return res.json()["result"]


def _seg(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def jpeg(w=354, h=472) -> bytes:
    """구조만 맞춘 합성 baseline JPEG."""
    out = b"\xff\xd8" + _seg(0xE0, b"JFIF\x00\x01\x01\x01\x01\x2c\x01\x2c\x00\x00")
    out += _seg(0xDB, b"\x00" + bytes(i % 255 + 1 for i in range(64)))
    out += _seg(
        0xC0, b"\x08" + struct.pack(">HH", h, w) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    )
    out += _seg(0xC4, b"\x00" + bytes([1] + [0] * 15) + b"\x00")
    out += _seg(0xDA, b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00")
    return out + b"\x12\x34\xff\x00\x56\x00\xff\xd9"


async def saved_posting(db, actor, *, company, title, url, text="합성 공고 본문"):
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
                text=text,
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
            "company": company,
            "title": title,
            "questions": [{"prompt": "지원 동기를 작성해 주세요. (500자 이내)", "char_limit": 500}],
        },
    )
    return p.id


def jpeg_with_exif(w=640, h=400) -> bytes:
    """EXIF(APP1)·주석(COM)이 붙은 합성 JPEG."""
    body = jpeg(w, h)
    return (
        body[:2]
        + _seg(0xE1, b"Exif\x00\x00GPS 37.0 127.0 synthetic")
        + _seg(0xFE, b"synthetic comment")
        + body[2:]
    )


# ---------------------------------------------------------------- 가짜 GitHub


IPS = {"api.github.com": "140.82.112.6", "raw.githubusercontent.com": "185.199.108.133"}


async def fake_resolve(host, port):
    ip = IPS.get(host)
    if ip is None:
        raise ConnectionError("dns_failed")
    return ip


class FakeGitHub:
    """api.github.com만 흉내 낸다. 본 요청(경로·Authorization 유무)을 남긴다."""

    def __init__(self):
        self.seen: list[tuple[str, str, bool]] = []
        self.mode = "ok"
        self.private_repos = {"amy/secret"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.headers.get("host", "")
        authz = request.headers.get("authorization")
        self.seen.append((host, request.url.path, authz is not None))
        if self.mode == "rate_limited":
            return httpx.Response(
                403,
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1893456000"},
                json={"message": "API rate limit exceeded"},
            )
        if self.mode == "too_many":
            return httpx.Response(429, headers={"x-ratelimit-reset": "1893456000"})
        if self.mode == "redirect_out":
            return httpx.Response(302, headers={"location": "https://evil.example.test/x"})
        path = request.url.path
        m = re.match(r"^/repos/([^/]+)/([^/]+)(/readme)?$", path)
        if host != "api.github.com" or m is None:
            return httpx.Response(404)
        full = f"{m.group(1)}/{m.group(2)}"
        if full in self.private_repos and authz != f"Bearer {TOKEN}":
            return httpx.Response(404, json={"message": "Not Found"})
        if m.group(3):
            return httpx.Response(200, content=README.encode())
        return httpx.Response(
            200,
            json={
                "full_name": full,
                "private": full in self.private_repos,
                "language": "Python",
                "created_at": "2021-03-01T00:00:00Z",
                "pushed_at": "2023-01-15T09:00:00Z",
                "default_branch": "main",
                "description": "합성 저장소",
                "stargazers_count": 42,
                "forks_count": 3,
            },
        )


# ---------------------------------------------------------------- 공용 준비


@pytest.fixture
async def env(db, tmp_path):
    ws_a, a = await create_workspace(db, name="A (합성)", owner_display_name="amy")
    ws_b, b = await create_workspace(db, name="B (합성)", owner_display_name="ben")
    owner = ActorContext(a, ws_a, scopes_for_role(Role.OWNER))
    editor = ActorContext(a, ws_a, scopes_for_role(Role.EDITOR))
    viewer = ActorContext(a, ws_a, scopes_for_role(Role.VIEWER))
    other = ActorContext(b, ws_b, scopes_for_role(Role.OWNER))
    blobs = LocalBlobStore(tmp_path / "blobs")
    await profile_uc.save_profile(
        db, owner, name="김합성", email="amy@example.test", headline="백엔드 개발자"
    )
    work = await brain.create_experience(
        db,
        owner,
        title="주문 서비스 개선",
        organization="가상커머스",
        role="백엔드 개발",
        kind="work",
        started_on="2021-03",
        ended_on="2023-02",
        tags=["Python"],
    )
    project = await brain.create_experience(
        db, owner, title="정산 배치 재작성", kind="project", started_on="2022-01", parent_id=work
    )
    side = await brain.create_experience(db, owner, title="사이드 프로젝트", kind="project")
    result = await brain.add_claim(
        db,
        owner,
        work,
        statement="주문 API 지연을 800ms에서 200ms로 줄였다",
        kind="result",
        contribution="personal",
    )
    action = await brain.add_claim(
        db,
        owner,
        work,
        statement="결제 대기열을 Redis로 나눠 처리했다",
        kind="action",
        contribution="personal",
    )
    child_claim = await brain.add_claim(
        db,
        owner,
        project,
        statement="정산 배치를 4시간에서 40분으로 줄였다",
        kind="result",
        contribution="personal",
    )
    side_claim = await brain.add_claim(
        db, owner, side, statement="사이드 결과", kind="result", contribution="personal"
    )
    pending = await brain.add_claim(
        db, editor, work, statement="확인 전 주장", kind="result", contribution="personal"
    )
    posting = await saved_posting(
        db, owner, company="(주)가상회사", title="백엔드 개발자", url="https://jobs.example.test/p1"
    )
    application = await app_uc.create_application(db, owner, posting)
    gh = FakeGitHub()
    github = uc.GitHubDeps(
        GitHubClient(HttpFetcher(transport=httpx.MockTransport(gh.handler), resolver=fake_resolve)),
        SecretBox(key_file=tmp_path / "secret.key"),
    )
    tokens = {"owner": owner, "editor": editor, "viewer": viewer, "other": other}
    deps = CoreDeps(identity=FakeResolver(tokens), db=db, blobs=blobs, github=github)
    app = FastAPI()
    app.include_router(build_router(deps))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        yield {
            "c": c,
            "deps": deps,
            "owner": owner,
            "editor": editor,
            "viewer": viewer,
            "other": other,
            "blobs": blobs,
            "work": work,
            "project": project,
            "side": side,
            "result": result,
            "action": action,
            "child_claim": child_claim,
            "side_claim": side_claim,
            "pending": pending,
            "application": application,
            "gh": gh,
            "github": github,
        }


def lines_of(view, section):
    return view.content[section]


# ---------------------------------------------------------------- 항목·근거


async def test_every_item_has_an_experience_and_fill_keeps_claim_links(db, env):
    owner = env["owner"]
    with pytest.raises(NotFound):
        await uc.create_item(db, owner, experience_id=uuid4())
    # 다른 공간의 경험에는 붙지 않는다
    other_exp = await brain.create_experience(db, env["other"], title="B의 경험")
    with pytest.raises(NotFound):
        await uc.create_item(db, owner, experience_id=other_exp)
    iid = await uc.create_item(db, owner, experience_id=env["work"], fill=True)
    view = await uc.get_item(db, owner, iid)
    assert view.summary.experience_id == env["work"] and view.summary.title == "주문 서비스 개선"
    assert view.summary.tags == ["Python"] and view.revision_no == 1
    # 결과 = 결과 사실(아래 경험 포함), 한 일 = 한 일 사실. 확인 전 사실은 없다
    assert {ln["claim_id"] for ln in lines_of(view, "results")} == {
        str(env["result"]),
        str(env["child_claim"]),
    }
    assert [ln["claim_id"] for ln in lines_of(view, "actions")] == [str(env["action"])]
    assert all(ln["state"] == "ok" for ln in view.content["results"] + view.content["actions"])
    async with db.workspace_session(owner.workspace_id) as s:
        linked = set(
            (
                await s.scalars(
                    select(PortfolioLineClaim.claim_id).where(
                        PortfolioLineClaim.revision_id == view.revision_id
                    )
                )
            ).all()
        )
        # DB도 경험 없는 항목을 받지 않는다
        assert PortfolioItem.__table__.c.experience_id.nullable is False
    assert linked == {env["result"], env["child_claim"], env["action"]}
    # 확인 전·다른 경험·이미 쓴 사실은 고를 수 없다
    for claim, code in (
        (env["pending"], "claim_not_confirmed"),
        (env["side_claim"], "claim_not_in_experience"),
        (env["result"], "claim_already_used"),
    ):
        with pytest.raises(uc.PortfolioRuleError, match=code):
            await uc.save_item(
                db,
                owner,
                iid,
                base_revision=1,
                operations=[{"op": "add_line", "section": "results", "claim_id": str(claim)}],
            )
    # 직접 쓴 줄·문제·내 역할 = 근거 없음
    await uc.save_item(
        db,
        owner,
        iid,
        base_revision=1,
        operations=[
            {"op": "set_text", "field": "problem", "text": "주문이 몰리면 결제가 늦었다"},
            {"op": "set_text", "field": "role", "text": "백엔드 담당"},
            {"op": "add_line", "section": "results", "text": "고객 만족도가 크게 올랐다"},
        ],
    )
    view = await uc.get_item(db, owner, iid)
    human = next(ln for ln in view.content["results"] if not ln["claim_id"])
    assert (human["origin"], human["state"]) == ("user", "none")
    assert view.summary.lines["none"] == 1
    # 보관한 경험에는 새 항목을 만들지 않는다
    async with db.workspace_session(owner.workspace_id) as s:
        (await s.get(Experience, env["side"])).status = "retired"
    with pytest.raises(uc.PortfolioRuleError, match="experience_not_active"):
        await uc.create_item(db, owner, experience_id=env["side"])
    # 피커: 이 경험 가족의 확인한 사실만, 쓴 것은 used
    picker = await uc.item_picker(db, owner, iid)
    ids = {c["id"]: c for c in picker["claims"]}
    assert set(ids) == {str(env["result"]), str(env["action"]), str(env["child_claim"])}
    assert all(c["used"] for c in ids.values())


async def test_stale_and_broken_lines_are_shown_not_changed(db, env):
    owner = env["owner"]
    iid = await uc.create_item(db, owner, experience_id=env["work"], fill=True)
    view = await uc.get_item(db, owner, iid)
    by_claim = {ln["claim_id"]: ln for _, ln in _all_lines(view)}
    r_line, a_line = by_claim[str(env["result"])], by_claim[str(env["action"])]
    await brain.edit_claim(
        db, owner, env["result"], statement="주문 API 지연을 800ms에서 150ms로 줄였다"
    )
    await brain.set_claim_status(db, owner, env["action"], ClaimStatus.RETIRED)
    view = await uc.get_item(db, owner, iid)
    lines = {ln["id"]: ln for _, ln in _all_lines(view)}
    assert lines[r_line["id"]]["text"] == "주문 API 지연을 800ms에서 200ms로 줄였다"
    assert lines[r_line["id"]]["state"] == "stale"
    assert lines[a_line["id"]]["state"] == "broken"
    bid = await uc.create_bundle(db, owner, kind="all", fill=True)
    codes = {c["code"] for c in (await uc.check_bundle(db, owner, bid))["checklist"]}
    assert {"lines_stale", "lines_evidence_broken"} <= codes
    with pytest.raises(uc.PortfolioRuleError, match="claim_not_confirmed"):
        await uc.save_item(
            db,
            owner,
            iid,
            base_revision=1,
            operations=[{"op": "refresh_line", "line_id": a_line["id"]}],
        )
    res = await uc.save_item(
        db,
        owner,
        iid,
        base_revision=1,
        operations=[
            {"op": "refresh_line", "line_id": r_line["id"]},
            {"op": "remove_line", "line_id": a_line["id"]},
        ],
    )
    assert res.revision_no == 2
    fixed = {ln["id"]: ln for _, ln in _all_lines(await uc.get_item(db, owner, iid))}
    assert fixed[r_line["id"]]["state"] == "ok" and a_line["id"] not in fixed
    # 지난 revision은 그대로
    old = await uc.get_item(db, owner, iid, revision=1)
    assert len(list(_all_lines(old))) == 3


def _all_lines(view):
    for sec in ("actions", "results"):
        for ln in view.content[sec]:
            yield sec, ln


async def test_conflict_idempotency_and_same_content(db, env):
    c, owner = env["c"], env["owner"]
    iid = await uc.create_item(db, owner, experience_id=env["work"])
    op = [{"op": "add_line", "section": "results", "claim_id": str(env["result"])}]
    res = await uc.save_item(db, owner, iid, base_revision=0, operations=op, idempotency_key="k1")
    assert (res.revision_no, res.created) == (1, True)
    again = await uc.save_item(db, owner, iid, base_revision=0, operations=op, idempotency_key="k1")
    assert (again.revision_no, again.created) == (1, False)
    with pytest.raises(uc.PortfolioConflict):
        await uc.save_item(db, owner, iid, base_revision=0, operations=op)
    http = await c.post(
        f"/core/portfolio/items/{iid}/revisions",
        json={"base_revision": 0, "operations": op},
        headers=auth("owner"),
    )
    assert http.status_code == 409
    assert http.json()["detail"] == {"code": "revision_conflict", "current_revision": 1}
    # 같은 내용이면 새 revision 없음
    same = await uc.save_item(
        db,
        owner,
        iid,
        base_revision=1,
        operations=[{"op": "set_text", "field": "problem", "text": None}],
    )
    assert (same.revision_no, same.created) == (1, False)


# ---------------------------------------------------------------- 공개 범위 (모든 출력)


VIS = {"public": "공개", "application": "지원", "private": "나만"}


async def _matrix(db, env):
    """항목 3개(공개 가능·지원서에만·나만) × 이미지 3장(공개 가능·지원서에만·나만). 글·이미지 바이트가 모두 다르다."""
    owner, blobs = env["owner"], env["blobs"]
    exps = {"public": env["work"], "application": env["project"], "private": env["work"]}
    items, images = {}, {}
    n = 0
    for vis, mark in VIS.items():
        iid = await uc.create_item(
            db, owner, experience_id=exps[vis], title=f"항목-{mark}", visibility=vis
        )
        await uc.save_item(
            db,
            owner,
            iid,
            base_revision=0,
            operations=[
                {"op": "set_text", "field": "problem", "text": f"문제글-{mark}"},
                {"op": "add_line", "section": "results", "text": f"결과줄-{mark}"},
            ],
        )
        items[vis] = iid
        for ivis, imark in VIS.items():
            n += 1
            data = jpeg(100 + n, 80 + n)
            view, _, _ = await uc.upload_image(
                db,
                blobs,
                owner,
                iid,
                data=data,
                caption=f"캡션-{mark}-{imark}",
                visibility=ivis,
            )
            images[(vis, ivis)] = (view.id, await blobs.get(owner.workspace_id, _key(view)))
    return items, images


def _key(view):
    return f"{view.sha256}"


def _expected(audience):
    """보여야 하는 (항목, 이미지) 표시 문자열과 보이면 안 되는 문자열."""
    shown, hidden = set(), set()
    for vis, mark in VIS.items():
        (shown if visible_in(vis, audience) else hidden).update(
            {f"항목-{mark}", f"문제글-{mark}", f"결과줄-{mark}"}
        )
        for ivis, imark in VIS.items():
            cap = f"캡션-{mark}-{imark}"
            (shown if image_shown(vis, ivis, audience) else hidden).add(cap)
    return shown, hidden


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


async def test_visibility_matrix_across_every_output(db, env, tmp_path):
    owner, blobs, c = env["owner"], env["blobs"], env["c"]
    items, images = await _matrix(db, env)
    all_id = await uc.create_bundle(db, owner, kind="all")
    await uc.set_bundle_items(db, owner, all_id, base_revision=1, item_ids=list(items.values()))
    post_id = await uc.create_bundle(
        db, owner, kind="posting", parent_id=all_id, application_id=env["application"]
    )
    bundles = {"public": all_id, "application": post_id}
    for audience, bid in bundles.items():
        shown, hidden = _expected(audience)
        preview = await uc.preview_html(db, blobs, owner, bid)
        html_res = await uc.export_bundle(db, blobs, owner, bid, fmt="html")
        # 미리보기 = 내보낸 HTML
        assert preview == html_res.data
        page = preview.decode()
        for text in shown:
            assert text in page, (audience, text)
        for text in hidden:
            assert text not in page, (audience, text)
        for (vis, ivis), (_, data) in images.items():
            assert (_b64(data) in page) == image_shown(vis, ivis, audience), (audience, vis, ivis)
        # 정적 HTML 묶음
        z = await uc.export_bundle(db, blobs, owner, bid, fmt="zip")
        with zipfile.ZipFile(io.BytesIO(z.data)) as zf:
            index = zf.read("index.html").decode()
            files = {n: zf.read(n) for n in zf.namelist()}
        for text in hidden:
            assert text not in index
        stored = {d for n, d in files.items() if n.startswith("images/")}
        for (vis, ivis), (_, data) in images.items():
            assert (data in stored) == image_shown(vis, ivis, audience)
        # HTTP 미리보기도 같은 바이트
        http = await c.get(f"/core/portfolio/bundles/{bid}/preview", headers=auth("viewer"))
        assert http.status_code == 200 and http.content == preview
    # 지원서에만은 공고별 묶음에서만: 전체 묶음 = 공개 가능만
    full = (await uc.preview_html(db, blobs, owner, all_id)).decode()
    assert "항목-지원" not in full and "캡션-공개-지원" not in full
    post = (await uc.preview_html(db, blobs, owner, post_id)).decode()
    assert "항목-지원" in post and "캡션-공개-지원" in post and "항목-나만" not in post
    # 묶음 보기: 안 보이는 까닭
    view = await uc.get_bundle(db, owner, all_id)
    reasons = {x["item"].title: x["hidden_reason"] for x in view.items}
    assert reasons == {"항목-공개": None, "항목-지원": "application", "항목-나만": "private"}
    assert view.summary.shown == 1 and view.summary.audience == "public"

    # Markdown 내보내기: 공개 규칙
    out = tmp_path / "notes.zip"
    await export_markdown(db, owner, out, blobs=blobs, options=MarkdownOptions())
    with zipfile.ZipFile(out) as zf:
        md = {n: zf.read(n).decode() for n in zf.namelist() if n.endswith(".md")}
    port = {n: t for n, t in md.items() if n.startswith("포트폴리오/")}
    assert set(port) == {"포트폴리오/항목-공개.md"}
    everything = "\n".join(md.values())
    shown, hidden = _expected("public")
    for text in shown:
        assert text in everything, text
    for text in hidden:
        assert text not in everything, text
    assert "결과줄-공개 (근거 없음)" in port["포트폴리오/항목-공개.md"]
    assert not any(n.startswith("포트폴리오/") and not n.endswith(".md") for n in md)

    # MCP: 목록·항목은 공개 규칙, 묶음은 그 묶음의 규칙. 이미지 바이트·나만은 어디에도 없다
    listed = (await mcp_call(c, "viewer", "portfolio_list", {}))["structuredContent"]
    assert [i["title"] for i in listed["items"]] == ["항목-공개"]
    got = {}
    for audience, bid in bundles.items():
        got[audience] = (await mcp_call(c, "viewer", "portfolio_get", {"bundle_id": str(bid)}))[
            "structuredContent"
        ]
        text = json.dumps(got[audience], ensure_ascii=False)
        shown, hidden = _expected(audience)
        for t in shown:
            assert t in text, (audience, t)
        for t in hidden:
            assert t not in text, (audience, t)
        for _, data in images.values():
            assert _b64(data)[:40] not in text
        assert "blob_key" not in text and "sha256" not in text
    one = await mcp_call(c, "viewer", "portfolio_get", {"item_id": str(items["public"])})
    assert one["structuredContent"]["item"]["title"] == "항목-공개"
    for vis in ("private", "application"):
        res = await mcp_call(c, "viewer", "portfolio_get", {"item_id": str(items[vis])})
        assert res["structuredContent"] == {"error": "not_found"}
    # 인쇄용 HTML·묶음은 밖에서 가져오지 않는다
    assert CSP_SINGLE in full and PAGINATE_JS in full


async def test_posting_bundle_suggests_prep_choices_first(db, env):
    owner = env["owner"]
    side_item = await uc.create_item(db, owner, experience_id=env["side"], title="사이드")
    work_item = await uc.create_item(db, owner, experience_id=env["work"], title="주문")
    proj_item = await uc.create_item(db, owner, experience_id=env["project"], title="정산")
    hidden = await uc.create_item(
        db, owner, experience_id=env["project"], title="정산-나만", visibility="private"
    )
    all_id = await uc.create_bundle(db, owner, kind="all", fill=True)
    all_view = await uc.get_bundle(db, owner, all_id)
    # 처음 채우기: 나만 항목은 넣지 않는다
    assert hidden not in [x["item"].id for x in all_view.items]
    assert {x["item"].id for x in all_view.items} == {side_item, work_item, proj_item}
    # 계보: 직무별은 전체를 복사, 출처 revision을 남긴다
    role_id = await uc.create_bundle(db, owner, kind="role", parent_id=all_id, target_role="백엔드")
    role = await uc.get_bundle(db, owner, role_id)
    assert [x["item"].id for x in role.items] == [x["item"].id for x in all_view.items]
    assert role.summary.source_revision == all_view.summary.revision
    assert role.summary.title == "백엔드 포트폴리오" and role.lineage[0]["id"] == str(all_id)
    with pytest.raises(uc.PortfolioRuleError, match="invalid_parent"):
        await uc.create_bundle(db, owner, kind="role", parent_id=role_id)
    with pytest.raises(uc.PortfolioRuleError, match="application_needs_posting"):
        await uc.create_bundle(db, owner, kind="role", application_id=env["application"])
    post_id = await uc.create_bundle(
        db, owner, kind="posting", parent_id=role_id, application_id=env["application"]
    )
    post = await uc.get_bundle(db, owner, post_id)
    assert post.prep_order == []
    with pytest.raises(uc.PortfolioRuleError, match="prep_not_chosen"):
        await uc.apply_prep_order(db, owner, post_id, base_revision=post.summary.revision)
    # 지원 준비에서 "정산" 경험을 고른다
    async with db.workspace_session(owner.workspace_id) as s:
        app = await s.get(app_uc.Application, env["application"])
        [doc] = (
            await s.scalars(
                select(app_uc.ApplicationDocument).where(
                    app_uc.ApplicationDocument.application_id == app.id
                )
            )
        ).all()
        s.add(
            ApplicationPrep(
                workspace_id=owner.workspace_id,
                application_id=app.id,
                posting_id=app.posting_id,
                mode="search",
                choices={
                    str(doc.id): [
                        {
                            "experience_id": str(env["project"]),
                            "claim_ids": [str(env["child_claim"])],
                            "chosen_at": "2026-01-01T00:00:00+00:00",
                            "chosen_by": str(owner.subject_id),
                        }
                    ]
                },
                generated_by=owner.subject_id,
                created_by=owner.subject_id,
            )
        )
    post = await uc.get_bundle(db, owner, post_id)
    # 제안: 고른 경험의 항목 (나만 항목은 제안하지 않는다)
    assert post.prep_order == [proj_item]
    check = await uc.check_bundle(db, owner, post_id)
    assert "prep_order" in {x["code"] for x in check["checklist"]}
    rev = await uc.apply_prep_order(db, owner, post_id, base_revision=post.summary.revision)
    post = await uc.get_bundle(db, owner, post_id)
    assert post.items[0]["item"].id == proj_item and post.summary.revision == rev
    # 지원에서 바로 만든 공고별 묶음은 고른 경험의 항목부터 시작한다
    direct = await uc.create_bundle(db, owner, kind="posting", application_id=env["application"])
    assert [x["item"].id for x in (await uc.get_bundle(db, owner, direct)).items] == [proj_item]
    # 목록 순서 변경은 409를 지킨다
    with pytest.raises(uc.PortfolioConflict):
        await uc.set_bundle_items(db, owner, post_id, base_revision=0, item_ids=[work_item])
    # 아래 묶음이 있으면 지우지 않는다
    with pytest.raises(uc.PortfolioRuleError, match="bundle_has_children"):
        await uc.delete_bundle(db, owner, all_id)
    # 항목을 지우면 묶음에서도 빠진다 (묶음 revision이 오른다)
    await uc.delete_item(db, env["blobs"], owner, proj_item)
    after = await uc.get_bundle(db, owner, post_id)
    assert proj_item not in [x["item"].id for x in after.items]
    assert after.summary.revision == rev + 1


# ---------------------------------------------------------------- 이미지 계약


async def test_image_contract_enforced_server_side(db, env):
    owner, blobs, c = env["owner"], env["blobs"], env["c"]
    iid = await uc.create_item(db, owner, experience_id=env["work"])
    for data, code in (
        (b"", "empty"),
        (b"\xff\xd8\xff" + b"\x00" * (1024 * 1024 + 10), "image_too_large"),
        (jpeg(1601, 900), "image_too_big"),
        (jpeg(10, 10), "invalid_dimensions"),
        (b"GIF89a" + b"\x00" * 64, "unsupported_type"),
    ):
        with pytest.raises(uc.PortfolioRuleError, match=code):
            await uc.upload_image(db, blobs, owner, iid, data=data)
    raw = jpeg_with_exif()
    assert has_metadata(raw)
    view, dedup, removed = await uc.upload_image(db, blobs, owner, iid, data=raw)
    assert not dedup and removed and view.visibility == "private"  # 기본은 나만
    stored = await blobs.get(owner.workspace_id, _key(view))
    assert not has_metadata(stored) and b"GPS" not in stored and b"synthetic comment" not in stored
    assert (view.width_px, view.height_px) == (640, 400)
    # 같은 이미지는 한 번만
    again, dedup, _ = await uc.upload_image(db, blobs, owner, iid, data=raw)
    assert dedup and again.id == view.id
    # HTTP: multipart, 형식 거부는 415, 너무 크면 413
    res = await c.post(
        f"/core/portfolio/items/{iid}/images",
        files={"file": ("a.gif", b"GIF89a" + b"\x00" * 64, "image/gif")},
        headers=auth("editor"),
    )
    assert res.status_code == 415 and res.json()["detail"] == "unsupported_type"
    res = await c.post(
        f"/core/portfolio/items/{iid}/images",
        files={"file": ("big.jpg", b"\xff\xd8\xff" + b"\x00" * (1024 * 1024 + 10), "image/jpeg")},
        headers=auth("editor"),
    )
    assert res.status_code == 413
    res = await c.post(
        f"/core/portfolio/items/{iid}/images",
        files={"file": ("d.jpg", jpeg_with_exif(300, 200), "image/jpeg")},
        data={"kind": "diagram", "caption": "구성도 합성", "visibility": "public"},
        headers=auth("editor"),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["kind"] == "diagram" and body["visibility"] == "public"
    assert body["removed_metadata"]
    f = await c.get(f"/core/portfolio/images/{body['id']}/file", headers=auth("viewer"))
    assert f.status_code == 200 and f.headers["content-type"] == "image/jpeg"
    assert not has_metadata(f.content)
    # 순서·공개 범위 바꾸기
    moved = await uc.update_image(db, owner, UUID(body["id"]), {"position": 0})
    assert moved.position == 0
    # 한 항목 12장까지
    for n in range(10):
        await uc.upload_image(db, blobs, owner, iid, data=jpeg(200 + n, 100))
    with pytest.raises(uc.PortfolioRuleError, match="too_many_images"):
        await uc.upload_image(db, blobs, owner, iid, data=jpeg(500, 100))
    # 지우면 다른 행이 쓰지 않는 파일도 지운다
    await uc.delete_image(db, blobs, owner, view.id)
    with pytest.raises((FileNotFoundError, KeyError, OSError)):
        await blobs.get(owner.workspace_id, _key(view))


# ---------------------------------------------------------------- GitHub 가져오기


async def test_github_import_with_mocked_api(db, env):
    owner, editor, c, gh = env["owner"], env["editor"], env["c"], env["gh"]
    iid = await uc.create_item(db, owner, experience_id=env["work"])
    # 주소 검사: 다른 호스트·잘못된 모양
    for url, code in (
        ("https://gitlab.example.test/amy/repo", "not_github"),
        ("http://169.254.169.254/latest", "not_github"),
        ("https://github.com/amy", "invalid_url"),
        ("https://github.com/amy/re po", "invalid_url"),
        ("https://user:pw@github.com/amy/repo", "invalid_url"),
        ("javascript:alert(1)", "invalid_url"),
    ):
        with pytest.raises(uc.PortfolioRuleError, match=code):
            await uc.github_import(db, env["blobs"], env["github"], editor, url=url)
    assert gh.seen == []
    assert parse_repo_url("github.com/amy/shop.git").full_name == "amy/shop"
    # 공개 저장소: 토큰 없이, README는 원본(내 경험 자료)으로, 저장소 정보는 링크의 참고 정보로
    res = await c.post(
        "/core/portfolio/github/import",
        json={"url": "https://github.com/amy/shop", "item_id": str(iid)},
        headers=auth("editor"),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["readme"] is True and body["repo"]["language"] == "Python"
    assert {h for h, _, _ in gh.seen} <= ALLOWED_HOSTS
    assert not any(authz for _, _, authz in gh.seen)
    async with db.workspace_session(owner.workspace_id) as s:
        src = await s.get(Source, UUID(body["source_id"]))
        assert src.purpose == "experience" and src.origin_url == "https://github.com/amy/shop"
    view = await uc.get_item(db, owner, iid)
    [link] = view.summary.links
    assert link["kind"] == "repo" and link["url"] == "https://github.com/amy/shop"
    assert link["info"]["language"] == "Python" and link["info"]["pushed_at"].startswith("2023")
    # 별·포크는 참고 정보: 근거(줄)로 들어가지 않는다
    assert view.content["results"] == [] and view.content["actions"] == []
    # 같은 저장소를 다시 가져오면 링크가 늘지 않는다
    await uc.github_import(
        db, env["blobs"], env["github"], editor, url="https://github.com/amy/shop", item_id=iid
    )
    assert len((await uc.get_item(db, owner, iid)).summary.links) == 1
    # 한도: 403 + 남은 한도 0, 429 → 429 rate_limited (다시 해도 되는 시각)
    for mode in ("rate_limited", "too_many"):
        gh.mode = mode
        res = await c.post(
            "/core/portfolio/github/import",
            json={"url": "https://github.com/amy/shop"},
            headers=auth("editor"),
        )
        assert res.status_code == 429
        assert res.json()["detail"]["code"] == "rate_limited"
        assert res.json()["detail"]["reset_at"].startswith("2030-01-01")
    # 허용 밖 호스트로 넘기면 따라가지 않는다
    gh.mode = "redirect_out"
    with pytest.raises(uc.PortfolioRuleError, match="blocked_address"):
        await uc.github_import(
            db, env["blobs"], env["github"], editor, url="https://github.com/amy/shop"
        )
    gh.mode = "ok"
    # 비공개 저장소: 토큰 없이는 없음, 토큰은 공간 관리자만 저장
    res = await c.post(
        "/core/portfolio/github/import",
        json={"url": "https://github.com/amy/secret"},
        headers=auth("editor"),
    )
    assert res.status_code == 404 and res.json()["detail"] == "repo_not_found"
    res = await c.post(
        "/core/portfolio/github/import",
        json={"url": "https://github.com/amy/secret", "private": True},
        headers=auth("editor"),
    )
    assert res.status_code == 403
    res = await c.put("/core/portfolio/github/token", json={"token": TOKEN}, headers=auth("editor"))
    assert res.status_code == 403
    res = await c.post(
        "/core/portfolio/github/import",
        json={"url": "https://github.com/amy/secret", "private": True},
        headers=auth("owner"),
    )
    assert res.status_code == 409 and res.json()["detail"] == "token_missing"
    res = await c.put("/core/portfolio/github/token", json={"token": TOKEN}, headers=auth("owner"))
    assert res.status_code == 200 and res.json() == {
        "set": True,
        "last4": "TEST",
        "available": True,
    }
    assert TOKEN not in res.text
    async with db.workspace_session(owner.workspace_id) as s:
        row = await s.scalar(select(GitHubToken))
        assert TOKEN.encode() not in row.ciphertext and TOKEN not in repr(row)
    gh.seen.clear()
    res = await c.post(
        "/core/portfolio/github/import",
        json={"url": "https://github.com/amy/secret", "private": True},
        headers=auth("owner"),
    )
    assert res.status_code == 200, res.text
    assert res.json()["repo"]["private"] is True and TOKEN not in res.text
    assert all(authz for _, _, authz in gh.seen) and len(gh.seen) == 2
    status = (await c.get("/core/portfolio/github/token", headers=auth("viewer"))).json()
    assert status["set"] is True and TOKEN not in json.dumps(status)
    assert (
        await c.delete("/core/portfolio/github/token", headers=auth("owner"))
    ).status_code == 204
    assert (await c.get("/core/portfolio/github/token", headers=auth("owner"))).json()[
        "set"
    ] is False


# ---------------------------------------------------------------- 정적 HTML 묶음·이력서 링크


async def test_static_zip_is_offline_with_relative_paths(db, env):
    owner, blobs = env["owner"], env["blobs"]
    proj = await uc.create_item(db, owner, experience_id=env["project"], fill=True)
    await uc.update_item(
        db,
        owner,
        proj,
        {"links": [{"kind": "demo", "url": "https://demo.example.test/app"}], "tags": ["Go"]},
    )
    await uc.upload_image(db, blobs, owner, proj, data=jpeg(320, 200), visibility="public")
    bid = await uc.create_bundle(db, owner, kind="all", fill=True)
    mid = await resume_uc.create_version(db, owner, kind="master", fill=True, fill_claims=True)
    res = await uc.export_bundle(db, blobs, owner, bid, fmt="zip", resume_version_id=mid)
    assert res.filename.endswith(".zip") and res.media_type == "application/zip"
    with zipfile.ZipFile(io.BytesIO(res.data)) as zf:
        names = set(zf.namelist())
        index = zf.read("index.html").decode()
        resume = zf.read("resume.html").decode()
    assert "index.html" in names and "resume.html" in names
    assert all(n == "index.html" or n == "resume.html" or n.startswith("images/") for n in names)
    for page in (index, resume):
        # 밖 주소·절대 경로를 가리키는 src·href가 없다
        refs = re.findall(r'(?:src|href)="([^"]*)"', page)
        assert refs
        for ref in refs:
            assert not re.match(r"^(?:[a-z]+:|//|/)", ref), ref
    assert CSP_BUNDLE in index and "default-src 'none'" in resume
    srcs = re.findall(r'<img src="([^"]+)"', index)
    assert srcs and all(s in names for s in srcs)
    assert "https://demo.example.test/app" in index  # 링크는 글로만
    assert 'href="https://' not in index
    # 이력서 프로젝트 항목 → 같은 경험의 포트폴리오 항목 (상대 링크)
    anchor = re.search(r'href="index\.html#(item-\d+)"', resume)
    assert anchor and f'id="{anchor.group(1)}"' in index
    assert 'href="resume.html"' in index
    # 이력서를 따로 내보내면 링크가 없다
    alone = (await resume_uc.export_version(db, blobs, owner, mid, fmt="html")).data.decode()
    assert "index.html#" not in alone
    # 인쇄용 HTML: 표지·목차, 항목마다 새 쪽
    html = (await uc.export_bundle(db, blobs, owner, bid, fmt="html")).data.decode()
    assert "목차" in html and html.count(" data-break>") == len(
        (await uc.get_bundle(db, owner, bid)).items
    )
    assert 'data-toc="item-1"' in html and "hidden_text_markup" not in res.summary
    with pytest.raises(uc.PortfolioRuleError, match="resume_needs_zip"):
        await uc.export_bundle(db, blobs, owner, bid, fmt="html", resume_version_id=mid)
    empty = await uc.create_bundle(db, owner, kind="all")
    with pytest.raises(uc.PortfolioRuleError, match="nothing_to_export"):
        await uc.export_bundle(db, blobs, owner, empty, fmt="zip")


# ---------------------------------------------------------------- 백업·복원·권한·공간


async def test_export_restore_roundtrip_with_images(db, env, tmp_path):
    owner, blobs = env["owner"], env["blobs"]
    iid = await uc.create_item(db, owner, experience_id=env["work"], fill=True)
    img, _, _ = await uc.upload_image(
        db, blobs, owner, iid, data=jpeg(222, 111), caption="합성 화면", visibility="public"
    )
    original = await blobs.get(owner.workspace_id, _key(img))
    all_id = await uc.create_bundle(db, owner, kind="all", fill=True)
    await uc.create_bundle(db, owner, kind="role", parent_id=all_id, target_role="백엔드")
    await uc.set_github_token(db, env["github"], owner, TOKEN)
    out = tmp_path / "ws.tar.gz"
    report = await export_workspace(db, blobs, owner, out)
    with tarfile.open(out, "r:gz") as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())
        blob_names = [m.name for m in tar.getmembers() if m.name.startswith("blobs/")]
        everything = b"".join(tar.extractfile(m).read() for m in tar.getmembers() if m.isfile())
    rows = {t["name"]: t["rows"] for t in manifest["tables"]}
    assert rows["core.portfolio_items"] == 1 and rows["core.portfolio_bundles"] == 2
    assert rows["core.portfolio_images"] == 1 and rows["core.portfolio_line_claims"] == 3
    assert rows["core.portfolio_bundle_items"] == 2
    assert "core.github_tokens" not in rows and TOKEN.encode() not in everything
    assert any(img.sha256 in n for n in blob_names)
    target_blobs = LocalBlobStore(tmp_path / "r")
    copy = await restore_workspace(db, target_blobs, out, new_ids=True)
    assert copy.rows_total == report.rows_total
    [new_owner] = copy.owners
    actor = ActorContext(new_owner, copy.workspace_id, scopes_for_role(Role.OWNER))
    [item] = await uc.list_items(db, actor)
    assert item.id != iid and item.images == 1 and item.lines["ok"] == 3
    view = await uc.get_item(db, actor, item.id)
    _, data = await uc.image_file(db, target_blobs, actor, view.images[0].id)
    assert data == original
    bundles = {b.kind: b for b in await uc.list_bundles(db, actor)}
    assert bundles["role"].parent_id == bundles["all"].id
    assert (await uc.github_token_status(db, actor))["set"] is False


async def test_permissions_and_workspace_isolation(db, env):
    c, owner, other = env["c"], env["owner"], env["other"]
    for action in ("portfolio.read", "portfolio.write", "portfolio.import", "portfolio.token"):
        assert action in ACTION_SCOPES
    res = await c.post(
        "/core/portfolio/items", json={"experience_id": str(env["work"])}, headers=auth("viewer")
    )
    assert res.status_code == 403
    res = await c.post(
        "/core/portfolio/items",
        json={"experience_id": str(env["work"]), "fill": True},
        headers=auth("editor"),
    )
    assert res.status_code == 201
    iid = res.json()["id"]
    assert (await c.get(f"/core/portfolio/items/{iid}", headers=auth("viewer"))).status_code == 200
    res = await c.patch(
        f"/core/portfolio/items/{iid}", json={"visibility": "private"}, headers=auth("viewer")
    )
    assert res.status_code == 403
    res = await c.patch(
        f"/core/portfolio/items/{iid}",
        json={"links": [{"kind": "repo", "url": "ftp://x.example.test/r"}]},
        headers=auth("editor"),
    )
    assert res.status_code == 422
    res = await c.post("/core/portfolio/bundles", json={"kind": "all"}, headers=auth("viewer"))
    assert res.status_code == 403
    bid = (
        await c.post(
            "/core/portfolio/bundles", json={"kind": "all", "fill": True}, headers=auth("editor")
        )
    ).json()["id"]
    res = await c.get(f"/core/portfolio/bundles/{bid}/export?format=zip", headers=auth("viewer"))
    assert res.status_code == 200 and res.content[:2] == b"PK"
    assert "attachment" in res.headers["content-disposition"]
    # 다른 공간: 0건, 404
    for path in (
        f"/core/portfolio/items/{iid}",
        f"/core/portfolio/bundles/{bid}",
        f"/core/portfolio/bundles/{bid}/preview",
    ):
        assert (await c.get(path, headers=auth("other"))).status_code == 404
    assert (await c.get("/core/portfolio/items", headers=auth("other"))).json() == []
    assert (await c.get("/core/portfolio/bundles", headers=auth("other"))).json() == []
    with pytest.raises(NotFound):
        await uc.get_item(db, other, UUID(iid))
    res = await mcp_call(c, "other", "portfolio_get", {"item_id": iid})
    assert res["structuredContent"] == {"error": "not_found"}
    assert (await mcp_call(c, "other", "portfolio_list", {}))["structuredContent"]["items"] == []
    # 다른 공간의 항목은 내 묶음에 넣지 못한다
    other_exp = await brain.create_experience(db, other, title="B의 경험")
    foreign = await uc.create_item(db, other, experience_id=other_exp)
    view = await uc.get_bundle(db, owner, UUID(bid))
    with pytest.raises(uc.PortfolioRuleError, match="unknown_item"):
        await uc.set_bundle_items(
            db, owner, UUID(bid), base_revision=view.summary.revision, item_ids=[foreign]
        )
    # 다른 공간의 경험에서 온 이미지 행은 보이지 않는다
    async with db.workspace_session(owner.workspace_id) as s:
        assert (await s.scalars(select(PortfolioImage))).all() == []

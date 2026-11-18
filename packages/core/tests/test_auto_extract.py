"""c0052 알아서 정리: 읽기가 끝난 내 경험 자료는 서버가 경험 후보 뽑기를 건다 (사람이 누르지 않아도).

- 설정 켜짐 + AI 연결 + 넣은 사람에게 경험 뽑기 권한 → 추출 1번, 비용 장부 1줄, 정리 상태에 결과가 보인다.
- 설정 끔 / AI 연결 없음 / 연결이 private을 받지 않음 / 권한 없는 사람이 넣음 → 추출 0번.
- 증빙·참고·용도 확인 대기 자료는 어떤 경우에도 추출 0번, 모델 호출 0번.
- 같은 읽기에 두 번 걸지 않는다 (실패했어도).

합성 자료만 쓴다. 모델은 가짜 게이트웨이 (실제 네트워크 없음).
"""

import json

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import ExtractionRun, ModelCall
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import extraction_handlers, parsing_handlers, run_until_idle
from career_core.llm import ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext
from career_core.usecases import sources
from career_core.usecases.extraction import ExtractionDeps, auto_extract
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.workspace_settings import update_settings
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio

CONFIG = """
[endpoints.proxy]
preset = "cliproxyapi"
price_as = "openai"
data_classes = [{classes}]

[routes]
default = "proxy/gpt-6.1-sol"
"""
ENV = {"CLIPROXY_API_KEY": "k"}

RESUME = (
    "가상상사 (합성), 백엔드 개발자, 2021.03 - 2023.02\n\n"
    "주문 서비스를 FastAPI로 다시 만들어 응답 시간을 800ms에서 200ms로 줄였습니다."
)
PROOF = "정보처리기사 자격증 사본 (합성)\n\n위 사람은 시험에 합격하였음을 증명합니다. 증명번호 ZEBRA-0000"
REFERENCE = "합격 자소서 모음 (합성)\n\n저는 플라밍고 프로젝트를 이끌었습니다."


def reply_for(text):
    quote = text.split("\n\n")[1][:30]
    return json.dumps(
        {
            "experiences": [
                {
                    "title": "주문 서비스 (합성)",
                    "organization": "가상상사 (합성)",
                    "kind": "work",
                    "claims": [
                        {
                            "kind": "result",
                            "statement": "응답 시간을 줄였다",
                            "contribution": "personal",
                            "quotes": [{"block": 1, "text": quote}],
                        }
                    ],
                }
            ]
        }
    )


class FakeModel:
    def __init__(self, reply: str):
        self.reply = reply
        self.completions: list = []

    async def complete(self, model, request):
        self.completions.append(request)
        return ModelCompletion(
            text=self.reply,
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, TokenUsage(input_tokens=100, output_tokens=50)),),
            request_id="req",
        )


def gateway(db, client, classes='"public", "private"'):
    config = parse_models_config(CONFIG.format(classes=classes))
    return ModelGateway(
        db, config, PriceBook.load(), client_factory=lambda ep, key: client, env=ENV
    )


@pytest.fixture
async def people(db):
    ws, a = await create_workspace(db, name="A (합성)", owner_display_name="amy")
    return {
        "owner": ActorContext(a, ws, scopes_for_role(Role.OWNER)),
        "editor": ActorContext(a, ws, scopes_for_role(Role.EDITOR)),
    }


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blobs")


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def client_for(db, blobs, people, gw):
    app = FastAPI()
    app.include_router(
        build_router(CoreDeps(identity=Tokens(people), db=db, blobs=blobs, models=gw))
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t")


async def parse_all(db, blobs, gw):
    deps = ParsingDeps(
        db=db,
        blobs=blobs,
        egress=ProviderEgressPolicy(),
        local_parsers=default_local_parsers(),
        gateway=gw,
    )
    await run_until_idle(db, parsing_handlers(deps))


async def extract_all(db, gw):
    await run_until_idle(db, extraction_handlers(ExtractionDeps(db=db, gateway=gw)))


async def runs(db, actor):
    async with db.workspace_session(actor.workspace_id) as s:
        return (await s.scalars(select(ExtractionRun))).all()


async def calls(db, actor):
    async with db.workspace_session(actor.workspace_id) as s:
        return await s.scalar(select(func.count()).select_from(ModelCall))


async def upload(c, name, text, *, token="owner", **data):
    r = await c.post(
        "/core/sources",
        files={"file": (name, text.encode(), "text/plain")},
        data=data,
        headers=auth(token),
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_on_with_model_extracts_without_a_click_and_records_cost(db, blobs, people):
    owner = people["owner"]
    model = FakeModel(reply_for(RESUME))
    gw = gateway(db, model)
    async with client_for(db, blobs, people, gw) as c:
        r = await c.get("/core/ingest/capabilities", headers=auth("owner"))
        assert r.json()["auto_extract"] == "on"
        r = await c.get("/core/ingest/capabilities", headers=auth("editor"))
        assert r.json()["auto_extract"] == "no_permission"

        up = await upload(c, "cv.txt", RESUME)
        await parse_all(db, blobs, gw)
        [run] = await runs(db, owner)
        assert run.status == "pending" and str(run.parse_run_id) == up["parse_run_id"]

        r = await c.get("/core/brain/organize", headers=auth("owner"))
        assert r.json()["processing"] == 1  # 정리 중

        await extract_all(db, gw)
        assert len(model.completions) == 1
        assert await calls(db, owner) == 1  # 비용 장부는 수동 추출과 같은 길

        r = await c.get("/core/brain/organize", headers=auth("owner"))
        got = r.json()
        assert (got["processing"], got["pending_claims"], got["review_experiences"]) == (0, 1, 1)
        assert got["recent"]["sources"] == 1
        assert got["recent"]["experiences_created"] == 1
        assert got["recent"]["claims_created"] == 1
        assert got["auto_extract"] == "on"

        # 같은 파일을 다시 넣어도 두 번 뽑지 않는다
        await upload(c, "cv.txt", RESUME)
        await parse_all(db, blobs, gw)
        assert len(await runs(db, owner)) == 1
    # 다시 불러도 같은 읽기에는 걸지 않는다
    assert await auto_extract(db, gw, owner.workspace_id, up["parse_run_id"], allowed=True) is None


async def test_setting_off_means_nothing_until_turned_on(db, blobs, people):
    owner = people["owner"]
    model = FakeModel(reply_for(RESUME))
    gw = gateway(db, model)
    async with client_for(db, blobs, people, gw) as c:
        r = await c.put(
            "/core/workspace-settings", json={"auto_extract": False}, headers=auth("owner")
        )
        assert r.status_code == 200 and r.json()["auto_extract"] is False
        r = await c.get("/core/ingest/capabilities", headers=auth("owner"))
        assert r.json()["auto_extract"] == "off"
        await upload(c, "cv.txt", RESUME)
        await parse_all(db, blobs, gw)
        assert await runs(db, owner) == []
        # 다른 설정을 바꿔도 켜고 끈 값은 그대로
        r = await c.put(
            "/core/workspace-settings", json={"timezone": "Asia/Seoul"}, headers=auth("owner")
        )
        assert r.json()["auto_extract"] is False
        # 받은 자료함에는 "추출 전"으로 남는다 (자료 화면에서 사람이 뽑는다)
        r = await c.get("/core/sources", params={"attention": "true"}, headers=auth("owner"))
        assert [x["attention"] for x in r.json()] == ["not_extracted"]
    assert len(model.completions) == 0


async def test_no_model_or_egress_or_permission_means_nothing(db, blobs, people):
    owner, editor = people["owner"], people["editor"]
    # AI 연결 없음 (게이트웨이 없음)
    async with client_for(db, blobs, people, None) as c:
        r = await c.get("/core/ingest/capabilities", headers=auth("owner"))
        assert r.json()["auto_extract"] == "no_model"
        await upload(c, "a.txt", RESUME + " 하나")
    await parse_all(db, blobs, None)
    assert await runs(db, owner) == []

    # 연결이 private을 받지 않음
    model = FakeModel(reply_for(RESUME))
    public_only = gateway(db, model, classes='"public"')
    async with client_for(db, blobs, people, public_only) as c:
        r = await c.get("/core/ingest/capabilities", headers=auth("owner"))
        assert r.json()["auto_extract"] == "egress_denied"
        await upload(c, "b.txt", RESUME + " 둘")
    await parse_all(db, blobs, public_only)
    assert await runs(db, owner) == []

    # 경험 뽑기 권한(egress)이 없는 사람이 넣음
    gw = gateway(db, model)
    up = await sources.upload_source(
        db,
        blobs,
        editor,
        filename="c.txt",
        data=(RESUME + " 셋").encode(),
        declared_mime="text/plain",
        max_bytes=1 << 20,
        auto_extract=False,
    )
    await parse_all(db, blobs, gw)
    assert await runs(db, owner) == []
    assert len(model.completions) == 0
    # 설정을 켜 둔 채로 연결이 생겨도, 권한 표시가 없는 읽기는 그대로
    assert await auto_extract(db, gw, owner.workspace_id, up.parse_run_id) is None


async def test_proof_reference_and_unconfirmed_are_never_extracted(db, blobs, people):
    owner = people["owner"]
    model = FakeModel(reply_for(RESUME))
    gw = gateway(db, model, classes='"public", "private", "restricted"')
    async with client_for(db, blobs, people, gw) as c:
        proof = await upload(c, "자격증.txt", PROOF, purpose="proof")
        ref = await upload(c, "후기.txt", REFERENCE, purpose="reference")
        unsure = await upload(c, "메모.txt", RESUME, purpose="experience", purpose_pending="true")
        await parse_all(db, blobs, gw)
        for up in (proof, ref, unsure):
            assert (
                await auto_extract(db, gw, owner.workspace_id, up["parse_run_id"], allowed=True)
                is None
            )
        assert await runs(db, owner) == []
        assert len(model.completions) == 0

        # 사람이 내 경험 자료로 확인하면 그때 정리한다 (권한·설정·연결이 맞을 때)
        r = await c.patch(
            f"/core/sources/{unsure['source_id']}",
            json={"purpose": "experience", "data_class": "private"},
            headers=auth("owner"),
        )
        assert r.status_code == 200, r.text
        assert r.json()["extraction_run_id"] is not None
        await extract_all(db, gw)
    assert len(model.completions) == 1
    # 증빙·참고 글은 모델에 가지 않았다
    sent = model.completions[0].messages[0].content
    assert "ZEBRA" not in sent and "플라밍고" not in sent


async def test_settings_off_also_skips_on_confirm(db, blobs, people):
    owner = people["owner"]
    model = FakeModel(reply_for(RESUME))
    gw = gateway(db, model)
    await update_settings(db, owner, auto_extract=False)
    async with client_for(db, blobs, people, gw) as c:
        unsure = await upload(c, "메모.txt", RESUME, purpose_pending="true")
        await parse_all(db, blobs, gw)
        r = await c.patch(
            f"/core/sources/{unsure['source_id']}",
            json={"purpose": "experience", "data_class": "private"},
            headers=auth("owner"),
        )
        assert r.json()["extraction_run_id"] is None
        # 끈 상태에서도 사람이 직접 뽑을 수 있다 (자료 화면의 "경험 뽑기")
        r = await c.post(
            f"/core/parse-runs/{unsure['parse_run_id']}/extractions", headers=auth("owner")
        )
        assert r.status_code == 202
    assert len(await runs(db, owner)) == 1

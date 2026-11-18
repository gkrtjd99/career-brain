"""인재상 맞춤 확인 (판정 모델 TypeSafe Jev, 참고 의견).

모든 자료는 합성이다. Jev는 각본대로 답하는 가짜 JudgmentClient로 게이트웨이를 지나 부른다 (장부·비용 확인).
어댑터는 httpx MockTransport로 요청 모양과 오류 해석만 본다 (밖으로 나가지 않는다).
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from career_core.adapters.typesafe import TypeSafeClient
from career_core.db import ModelCall
from career_core.db.models import CompanyFact, CompanyPage
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.llm import JudgmentCompletion, ModelCallError, TokenUsage, UsagePart
from career_core.llm.config import ConfigError, parse_models_config, route_problem
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.ports import ActorContext
from career_core.usecases import applications as app_uc
from career_core.usecases import companies as company_uc
from career_core.usecases import postings as postings_uc
from career_core.usecases import talent_fit as uc
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import select

pytestmark = pytest.mark.anyio

T0 = datetime.now(UTC)

JEV_CONFIG = """
[endpoints.jev]
preset = "typesafe"
data_classes = ["private"]

[routes]
talent_fit = "jev/jev-1.14.0"
"""
ESSAY = (
    "배포가 세 번 실패했을 때 원인을 하나씩 지워 가며 다시 시도했고, 결국 빌드 시간을 절반으로 줄였습니다. "
    "저는 소통을 잘하는 사람입니다."
)


class FakeResolver:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def env(db):
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    tokens = {
        "owner": ActorContext(a, ws_a, scopes_for_role(Role.OWNER)),
        "editor": ActorContext(a, ws_a, scopes_for_role(Role.EDITOR)),
        "other": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
    }
    return {**tokens, "tokens": tokens}


class FakeJev:
    """질문 이름(s{줄}_{문단} · c{줄} · o{문단})과 state를 보고 Noul로 답하는 가짜 판정 모델. 요청을 기록한다."""

    def __init__(self):
        self.requests = []

    async def judge(self, model, state, questions):
        self.requests.append((model, state, questions))
        paras = state["essay"]["paragraphs"]
        answers = {}
        for name, q in questions.items():
            trait = q["instructions"].get("trait", "")
            if name.startswith("s"):
                j = int(name.split("_")[1])
                p = 0.9 if "도전" in trait and "다시 시도" in paras[j] else 0.05
            elif name.startswith("c"):
                p = 0.85 if "소통" in trait and any("소통" in x for x in paras) else 0.1
            else:
                p = 0.9
            answers[name] = {"type": "noul", "noul": p}
        answers["junk"] = "not a dict"
        return JudgmentCompletion(
            answers={k: v for k, v in answers.items() if isinstance(v, dict)},
            model="jev-1.14.0",
            parts=(UsagePart("jev-1.14.0", TokenUsage(input_tokens=2000)),),
        )


def jev_gateway(db, client, *, key="k"):
    return ModelGateway(
        db,
        parse_models_config(JEV_CONFIG),
        PriceBook.load(),
        client_factory=lambda ep, k: client,
        env={"TYPESAFE_API_KEY": key} if key else {},
    )


async def application_with_essay(db, owner, essay=ESSAY):
    fields = {
        "title": "백엔드 (합성)",
        "company": "가상테크",
        "talent": ["- 실패를 두려워하지 않고 도전하는 사람,", "소통하는 사람."],
        "questions": [{"prompt": "지원 동기 (700자)", "char_limit": 700}],
    }
    [r] = await postings_uc.save_urls(db, owner, ["https://jobs.example.test/t/1"])
    await postings_uc.confirm_posting(db, owner, r.posting_id, overrides=fields)
    aid = await app_uc.create_application(db, owner, r.posting_id)
    [doc] = (await app_uc.get_application(db, owner, aid)).documents
    if essay:
        await app_uc.save_revision(
            db, owner, aid, doc.id, base_revision=0, operations=[{"op": "insert", "text": essay}]
        )
    return r.posting_id, aid, doc


# ---------------------------------------------------------------- 규칙


def test_trait_cleaning_and_levels():
    assert uc.clean_trait("- 실패를 두려워하지 않고 도전하는 사람,") == (
        "실패를 두려워하지 않고 도전하는 사람"
    )
    assert uc.clean_trait("1) 정직한 사람.") == "정직한 사람"
    assert uc.clean_trait("① 주인의식") == "주인의식"
    assert uc.clean_trait("자신의 일에 자부심을 갖고 전문성을 쌓아가는 사람 과") == (
        "자신의 일에 자부심을 갖고 전문성을 쌓아가는 사람"
    )
    assert uc.is_trait_line("동료와 함께 문제를 푸는 태도를 중요하게 생각합니다")
    assert uc.is_trait_line("주인의식")  # 짧은 가치 낱말
    assert uc.is_trait_line("We look for people who own problems end to end")
    assert not uc.is_trait_line("우리는 새로운 기술과 시장에 끊임없이 도전하는 혁신기업 입니다")
    assert not uc.is_trait_line("그런 분들과 함께 미래를 만들어가고 싶습니다")
    assert not uc.is_trait_line("함께 미래를 만들어가고 싶습니다")
    assert uc.is_headline("[세 번 실패한 배포, 네 번째에 성공시켰습니다]")
    assert not uc.is_headline("배포가 세 번 실패했습니다 [참고]")


def test_levels_from_noul_probabilities():
    # 경계: 0.7 이상 예, 0.3 아래 아니오, 그 사이는 갈림
    assert uc.level_of(0.7, 0.9) == "shown"  # 한 일로 보이면 말로 내세운 것보다 앞선다
    assert uc.level_of(0.69, 0.7) == "claimed"
    assert uc.level_of(0.5, 0.1) == "unclear"
    assert uc.level_of(0.1, 0.4) == "unclear"
    assert uc.level_of(0.29, 0.29) == "absent"
    assert uc.level_of(0.1, None) == "absent"

    traits = [uc.Trait("도전", "posting", True), uc.Trait("책임", "posting", True)]
    q = uc.build_questions(traits, 3)
    assert sorted(q) == sorted(
        [f"s{i}_{j}" for i in range(2) for j in range(3)] + ["c0", "c1", "o0", "o1", "o2"]
    )
    assert all(v["type"] == "noul" for v in q.values())
    assert "`essay.paragraphs[2]`" in q["s1_2"]["instructions"]["question"]
    assert q["s1_2"]["instructions"]["trait"] == "책임"
    assert set(q["s0_0"]["criteria"]) == {"true", "false"}

    doc = uuid4()
    ids = ["h", "a", "b"]

    def n(p):
        return {"type": "noul", "noul": p}

    answers = {
        # 도전: 제목 줄(h)이 가장 높아도 본문 문단(a)이 먼저, 경계 아래(b)는 근거가 아니다
        "s0_0": n(0.95),
        "s0_1": n(0.8),
        "s0_2": n(0.5),
        "c0": n(0.2),
        # 책임: b가 "보여 준다"고 해도 남이 한 일(o2 낮음)이면 그만큼만 센다
        "s1_0": n(0.1),
        "s1_1": n(0.2),
        "s1_2": n(0.9),
        "c1": n(0.8),
        "o0": n(0.9),
        "o1": n(0.9),
        "o2": n(0.04),
    }
    shown, claimed = uc.read_fits(answers, traits, ids, doc, headlines=frozenset({"h"}))
    assert (shown.level, shown.shown, shown.said) == ("shown", 0.9, 0.2)  # o0 0.9로 깎임
    assert shown.evidence == ["a", "h"]
    assert (claimed.level, claimed.shown, claimed.evidence) == ("claimed", 0.2, [])
    # 답이 없거나 모양이 다르면 그 줄은 판정 없음
    bad = {"s0_0": {"noul": True}, "s0_1": {"noul": "0.9"}, "s0_2": None}
    assert uc.read_fits(bad, traits[:1], ids, doc) == [None]
    # 확률은 0~1로 자른다, own이 없으면 깎지 않는다
    [fit] = uc.read_fits({"s0_0": n(1.4)}, traits[:1], ["a"], doc)
    assert (fit.level, fit.shown, fit.said, fit.evidence) == ("shown", 1.0, None, ["a"])

    # 문단이 많으면 이웃 문단을 묶는다 (근거는 묶음 첫 블록)
    blocks = [(str(k), f"문단{k}") for k in range(5)]
    assert uc.group_paragraphs(blocks, 5) == blocks
    assert uc.group_paragraphs(blocks, 2) == [("0", "문단0\n문단1\n문단2"), ("3", "문단3\n문단4")]


def test_typesafe_routes_only_judgment_and_back():
    cfg = parse_models_config(JEV_CONFIG)
    ep = cfg.endpoints["jev"]
    assert (ep.kind, ep.base_url, ep.api_key_env) == (
        "typesafe",
        "https://api.typesafe.ai/v1",
        "TYPESAFE_API_KEY",
    )
    assert route_problem("talent_fit", ep) is None
    assert route_problem("default", ep) == "judgment_only"
    with pytest.raises(ConfigError, match="judgment"):
        parse_models_config(JEV_CONFIG + 'default = "jev/jev-1.14.0"\n')
    with pytest.raises(ConfigError, match="typesafe"):
        parse_models_config(
            '[endpoints.p]\npreset = "cliproxyapi"\n[routes]\ntalent_fit = "p/gpt"\n'
        )
    prices = PriceBook.load()
    assert prices.lookup("typesafe", "jev-1.14.0").rates.output == 0
    assert prices.lookup("typesafe", "jev-latest") is not None


async def test_typesafe_adapter_request_and_errors():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        if body["model"] == "bad-key":
            return httpx.Response(401, json={"detail": "no"})
        if body["model"] == "busy":
            return httpx.Response(429, json={"detail": "slow down"})
        return httpx.Response(
            200,
            json={
                "model": "jev-1.14.0",
                "answers": {"t0": {"type": "score", "score": 1.5, "confidence": 0.7}},
                "usage": {"input_tokens": 1234, "output_tokens": 3},
            },
        )

    ep = parse_models_config(JEV_CONFIG).endpoints["jev"]
    client = TypeSafeClient(ep, "sk-test", transport=httpx.MockTransport(handler))
    q = {"t0": {"type": "score", "instructions": "x", "criteria": ["a", "b"]}}
    got = await client.judge("jev-latest", {"essay": "합성"}, q)
    assert got.answers["t0"]["score"] == 1.5 and got.model == "jev-1.14.0"
    assert got.usage.input_tokens == 1234
    [req] = seen
    assert str(req.url) == "https://api.typesafe.ai/v1/systemone"
    assert req.headers["Authorization"] == "Bearer sk-test"
    assert json.loads(req.content) == {
        "model": "jev-latest",
        "state": {"essay": "합성"},
        "questions": q,
    }
    for model, code in (("bad-key", "unauthorized"), ("busy", "rate_limited")):
        with pytest.raises(ModelCallError) as exc:
            await client.judge(model, {}, q)
        assert exc.value.code == code
    with pytest.raises(ModelCallError):
        from career_core.llm import ModelRequest

        await client.complete("jev-1.14.0", ModelRequest(messages=()))


# ---------------------------------------------------------------- 지원 문서


async def test_talent_fit_judges_each_trait_and_is_advisory(db, env):
    owner = env["owner"]
    pid, aid, doc = await application_with_essay(db, owner)
    # 회사 인재상: 확인한 것 하나, 확인 전 후보 하나, 공고와 같은 글 하나 (한 번만 묻는다)
    async with db.workspace_session(owner.workspace_id) as s:
        from career_core.db import JobPosting

        cid = (await s.get(JobPosting, pid)).company_id
    assert cid is not None  # 공고를 저장하면 회사에 이어진다
    text = "주인의식: 맡은 일을 끝까지 책임집니다. 소통하는 사람."
    page = await company_uc.import_paste(db, None, owner, cid, text, title="합성 소개")
    await company_uc.add_fact(
        db, owner, cid, page_id=page.id, kind="values", body="주인의식", quote="주인의식"
    )
    cand = await company_uc.add_fact(
        db,
        owner,
        cid,
        page_id=page.id,
        kind="talent",
        body="끝까지 책임지는 사람",
        quote="맡은 일을 끝까지 책임집니다",
    )
    await company_uc.add_fact(
        db,
        owner,
        cid,
        page_id=page.id,
        kind="talent",
        body="소통하는 사람",
        quote="소통하는 사람",
    )
    await company_uc.add_fact(
        db, owner, cid, page_id=page.id, kind="business", body="합성 사업", quote="주인의식"
    )
    async with db.workspace_session(owner.workspace_id) as s:
        (await s.get(CompanyFact, cand)).status = "candidate"
        # 공고 글에서 뽑은 후보는 공고 줄을 고쳐 쓴 것이라 빠진다
        posting_page = CompanyPage(
            id=uuid4(),
            workspace_id=owner.workspace_id,
            company_id=cid,
            origin="posting",
            url="https://jobs.example.test/t/1",
            status="done",
            text="도전을 즐기는 인재",
            fetched_at=T0,
            requested_by=owner.subject_id,
        )
        s.add(posting_page)
        await s.flush()
        s.add(
            CompanyFact(
                workspace_id=owner.workspace_id,
                company_id=cid,
                page_id=posting_page.id,
                kind="talent",
                body="도전을 즐기는 인재",
                quote="도전을 즐기는 인재",
                quote_start=0,
                quote_end=9,
                fetched_at=T0,
                status="candidate",
                origin="model",
                created_by=owner.subject_id,
            )
        )

    # 연결이 없으면 묻지 않는다 (default로 넘어가지 않는다)
    res = await uc.judge_talent_fit(db, None, owner, aid, document_id=doc.id)
    assert (res.judge.status, res.judge.note) == ("unavailable", "no_route")
    assert [t.text for t in res.traits] == [
        "실패를 두려워하지 않고 도전하는 사람",
        "소통하는 사람",
        "주인의식",
        "끝까지 책임지는 사람",
    ]
    assert [(t.source, t.confirmed) for t in res.traits] == [
        ("posting", True),
        ("posting", True),
        ("company", True),
        ("company", False),
    ]
    assert all(t.best is None for t in res.traits)

    client = FakeJev()
    res = await uc.judge_talent_fit(db, jev_gateway(db, client, key=None), owner, aid)
    assert res.judge.note == "api_key_missing" and client.requests == []
    gw = jev_gateway(db, client)
    res = await uc.judge_talent_fit(db, gw, env["editor"], aid)
    assert res.judge.note == "egress_scope_required" and client.requests == []

    res = await uc.judge_talent_fit(db, gw, owner, aid, document_id=doc.id)
    assert res.judge.status == "ok" and res.judge.calls == 1 and res.basis == "judge_advisory"
    assert res.judge.model == "jev-1.14.0"
    assert res.judge.cost_usd == f"{2000 * 0.042 / 1_000_000:.6f}"
    by = {t.text: t for t in res.traits}
    challenge = by["실패를 두려워하지 않고 도전하는 사람"]
    assert challenge.best == "shown" and challenge.fits[0].shown == 0.9
    [(model, state, questions)] = client.requests
    paras = state["essay"]["paragraphs"]
    assert model == "jev-1.14.0" and "\n\n".join(paras).replace("\n\n", " ") == ESSAY
    # 근거는 "다시 시도"가 있는 문단 (블록 ID), 화면에서 그 문단으로 이동한다
    assert len(challenge.fits[0].evidence) == 1
    assert by["소통하는 사람"].best == "claimed"  # 말로만
    assert by["주인의식"].best == "absent"
    assert state["company"] == "가상테크" and state["question"].startswith("지원 동기")
    assert len(questions) == 4 * len(paras) + 4 + len(paras)
    async with db.workspace_session(owner.workspace_id) as s:
        calls = (await s.execute(select(ModelCall.purpose, ModelCall.status))).all()
    assert calls == [("talent_fit", "ok")]  # 키·범위가 없을 때는 부르기 전에 멈춘다
    # 다른 공간에서는 보이지 않는다
    with pytest.raises(app_uc.NotFound):
        await uc.judge_talent_fit(db, gw, env["other"], aid)

    deps = CoreDeps(identity=FakeResolver(env["tokens"]), db=db, models=gw)
    app = FastAPI()
    app.include_router(build_router(deps))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        r = await c.post(f"/core/applications/{aid}/talent-fit", headers=auth("editor"))
        assert r.status_code == 403  # 문서를 밖으로 보내므로 egress 범위
        r = await c.post(
            f"/core/applications/{aid}/talent-fit",
            json={"document_id": str(doc.id)},
            headers=auth("owner"),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["judge"]["status"] == "ok" and body["basis"] == "judge_advisory"
        fit = body["traits"][0]["fits"][0]
        assert fit["level"] == "shown" and fit["shown"] == 0.9 and len(fit["evidence"]) == 1
        assert fit["said"] == 0.1
        r = await c.post(f"/core/applications/{uuid4()}/talent-fit", headers=auth("owner"))
        assert r.status_code == 404


async def test_talent_fit_without_traits_or_text(db, env):
    owner = env["owner"]
    _, aid, doc = await application_with_essay(db, owner, essay=None)
    client = FakeJev()
    gw = jev_gateway(db, client)
    res = await uc.judge_talent_fit(db, gw, owner, aid, document_id=doc.id)
    assert (res.judge.status, res.judge.note) == ("unavailable", "empty_document")
    assert len(res.traits) == 2 and client.requests == []

    [r] = await postings_uc.save_urls(db, owner, ["https://jobs.example.test/t/2"])
    fields = {"title": "합성", "company": "다른회사", "questions": [{"prompt": "자기소개"}]}
    await postings_uc.confirm_posting(db, owner, r.posting_id, overrides=fields)
    aid2 = await app_uc.create_application(db, owner, r.posting_id)
    res = await uc.judge_talent_fit(db, gw, owner, aid2)
    assert (res.judge.note, res.traits) == ("no_traits", []) and client.requests == []


async def test_talent_fit_reports_jev_errors(db, env):
    owner = env["owner"]
    _, aid, _doc = await application_with_essay(db, owner)

    class Busy:
        async def judge(self, model, state, questions):
            raise ModelCallError("rate_limited")

    res = await uc.judge_talent_fit(db, jev_gateway(db, Busy()), owner, aid)
    assert (res.judge.status, res.judge.note) == ("error", "rate_limited")
    assert all(t.best is None for t in res.traits)
    async with db.workspace_session(owner.workspace_id) as s:
        row = await s.scalar(select(ModelCall).where(ModelCall.purpose == "talent_fit"))
    assert (row.status, row.error_code) == ("error", "rate_limited")

"""일괄 실행 예상 비용: 입력 글자 수로 잰 예상(``input_chars``)과 호출 1회 평균(``past_runs``)으로 돌아가기.

모든 자료는 합성이다.
"""

import json
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from career_core.db.base import utcnow
from career_core.db.models import Run
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import run_handlers, run_until_idle
from career_core.llm import ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.ports import ActorContext
from career_core.usecases import brain
from career_core.usecases import run_estimates as est_uc
from career_core.usecases import runs as run_uc
from career_core.usecases.runs import RunDeps
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI

pytestmark = pytest.mark.anyio

WF = "interview_question_batch"
CONFIG = parse_models_config("""
[endpoints.claude]
preset = "anthropic"
data_classes = ["public", "private"]
[routes]
default = "claude/claude-opus-5-5"
""")


class QuestionLLM:
    async def complete(self, model, request):
        qs = [{"focus": "metric", "question": "합성 질문?"}]
        return ModelCompletion(
            text=json.dumps({"questions": qs}, ensure_ascii=False),
            model=model,
            stop_reason="end_turn",
            parts=(UsagePart(model, TokenUsage(input_tokens=400, output_tokens=100)),),
        )


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


@pytest.fixture
async def env(db):
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    return {
        "owner": ActorContext(a, ws_a, scopes_for_role(Role.OWNER)),
        "other": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
    }


# ---------------------------------------------------------------- 순수 계산


def test_fit_recovers_fixed_and_per_char_cost():
    a, b = Decimal("0.002"), Decimal("0.00001")
    points = [(n, x, a * n + b * x) for n, x in ((1, 100), (2, 1000), (3, 600), (5, 5000))]
    fa, fb = est_uc.fit_cost(points)
    assert abs(fa - a) < Decimal("1e-12") and abs(fb - b) < Decimal("1e-15")


def test_fit_gives_up_when_it_cannot_separate_or_goes_negative():
    assert est_uc.fit_cost([(2, 400, Decimal("0.01"))]) is None  # 실행 하나
    # 호출당 글자 수가 모두 같다 (200자/호출): 고정분과 글자당을 가를 수 없다
    assert est_uc.fit_cost([(1, 200, Decimal("0.01")), (3, 600, Decimal("0.03"))]) is None
    # 긴 입력이 오히려 싸다 → 글자당이 음수
    assert est_uc.fit_cost([(1, 100, Decimal("0.02")), (1, 5000, Decimal("0.001"))]) is None


def test_processed_chars_counts_only_finished_units():
    assert est_uc.processed_chars({"chars": [10, 20, 30], "next": 2}) == 30
    assert est_uc.processed_chars({"chars": [10, 20], "next": 9}) == 30
    for bad in ({}, {"next": 1}, {"chars": "x", "next": 1}, {"chars": [1, True], "next": 2}, None):
        assert est_uc.processed_chars(bad) is None


# ---------------------------------------------------------------- usecase


async def seed(db, actor, rows):
    async with db.workspace_session(actor.workspace_id) as s:
        for cost, calls, position, unknown in rows:
            s.add(
                Run(
                    id=uuid4(),
                    workspace_id=actor.workspace_id,
                    workflow=WF,
                    status="done",
                    input={},
                    position=position,
                    cost_usd=cost,
                    model_calls=calls,
                    cost_unknown=unknown,
                    actor_scopes=[],
                    created_by=actor.subject_id,
                    finished_at=utcnow(),
                )
            )


def cost(calls, chars):
    return str(Decimal("0.002") * calls + Decimal("0.00001") * chars)


async def test_estimate_scales_by_input_size_when_past_runs_recorded_chars(db, env):
    owner = env["owner"]
    await seed(
        db,
        owner,
        [
            (cost(1, 100), 1, {"chars": [100], "next": 1}, False),
            (cost(2, 1000), 2, {"chars": [400, 600], "next": 2}, False),
            # 중간에 끝난 실행: 처리한 첫 단위만 (300자, 호출 1번)
            (cost(1, 300), 1, {"chars": [300, 9000], "next": 1}, False),
            # c0023 전 실행: 글자 수 없음 → 호출 평균에만 들어간다
            ("0.050", 1, {}, True),
        ],
    )
    est = await est_uc.estimate_run(db, owner, WF, 99, sizes=[500, 1500])
    assert est.basis == "input_chars"
    assert (est.units, est.calls, est.chars) == (2, 2, 2000)
    assert (est.fixed_per_call_usd, est.per_1k_chars_usd) == ("0.002000", "0.010000")
    assert est.estimate_usd == "0.024000"  # 0.002 × 2 + 0.00001 × 2000
    assert (est.based_on_runs, est.based_on_calls, est.based_on_chars) == (3, 4, 1400)
    assert est.cost_unknown is False  # 근거 실행들만 본다
    # 호출 1회 평균도 같이 (모든 지난 실행)
    assert est.per_call_usd == "0.014400"  # (0.003 + 0.014 + 0.005 + 0.05) / 5

    # 입력이 없으면 호출 1회 평균
    plain = await est_uc.estimate_run(db, owner, WF, 2)
    assert (plain.basis, plain.estimate_usd, plain.chars, plain.based_on_runs) == (
        "past_runs",
        "0.028800",
        None,
        4,
    )
    assert plain.cost_unknown is True and plain.fixed_per_call_usd is None
    # 다른 공간의 실행은 섞이지 않는다
    other = await est_uc.estimate_run(db, env["other"], WF, 1, sizes=[500])
    assert (other.basis, other.estimate_usd, other.chars) == ("past_runs", None, 500)


async def test_estimate_falls_back_to_per_call_average_with_too_few_sized_runs(db, env):
    owner = env["owner"]
    await seed(
        db,
        owner,
        [(cost(2, 800), 2, {"chars": [400, 400], "next": 2}, False), ("0.010", 2, {}, False)],
    )
    est = await est_uc.estimate_run(db, owner, WF, 1, sizes=[5000])
    assert (est.basis, est.chars, est.units) == ("past_runs", 5000, 1)
    assert est.estimate_usd == est.per_call_usd == "0.005500"  # (0.012 + 0.010) / 4 × 1


# ---------------------------------------------------------------- HTTP


async def test_post_estimate_counts_the_input_and_the_run_records_the_same(db, env):
    owner = env["owner"]
    gw = ModelGateway(
        db,
        CONFIG,
        PriceBook.load(),
        client_factory=lambda ep, k: QuestionLLM(),
        env={"ANTHROPIC_API_KEY": "k"},
    )
    deps = CoreDeps(identity=Tokens({"o": owner, "x": env["other"]}), db=db, models=gw)
    app = FastAPI()
    app.include_router(build_router(deps), prefix="/api")
    e1 = await brain.create_experience(db, owner, title="주문 서비스", organization="가상커머스")
    await brain.add_claim(db, owner, e1, statement="주문 API 지연을 줄였다 (합성)", kind="result")
    e2 = await brain.create_experience(db, owner, title="정산")
    size1 = len("주문 서비스") + len("가상커머스") + len("주문 API 지연을 줄였다 (합성)")
    url = f"/api/core/runs/workflows/{WF}/estimate"
    h = {"Authorization": "Bearer o"}
    inp = {"experience_ids": [str(e1), str(e2)], "count": 2}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        res = await client.post(url, json={"input": inp}, headers=h)
        assert res.status_code == 200, res.text
        body = res.json()
        assert (body["units"], body["calls"], body["chars"], body["basis"]) == (
            2,
            2,
            size1 + len("정산"),
            "past_runs",
        )
        assert body["estimate_usd"] is None  # 지난 실행 없음
        # 잘못된 입력 422, 없는(다른 공간의) 경험 404, 모델을 안 쓰는 workflow 404
        assert (await client.post(url, json={"input": {}}, headers=h)).status_code == 422
        res = await client.post(url, json={"input": inp}, headers={"Authorization": "Bearer x"})
        assert res.status_code == 404
        res = await client.post(
            "/api/core/runs/workflows/save_job_urls/estimate",
            json={"input": {"urls": ["https://e.test"]}},
            headers=h,
        )
        assert res.status_code == 404

        view = await run_uc.start_run(RunDeps(db=db, gateway=gw), owner, WF, inp)
        await run_until_idle(db, run_handlers(RunDeps(db=db, gateway=gw)))
        done = await run_uc.get_run(db, owner, view.id)
        assert done.status == "done"
        assert done.position["chars"] == [size1, len("정산")]

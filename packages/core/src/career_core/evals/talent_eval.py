"""인재상 맞춤 판정 평가: 합성 자기소개서 8개 × 인재상 4줄 = 32건 (``talent_eval.json``).

두 설계를 같은 자료로 잰다.

- ``score``: 예전 설계. 인재상 줄마다 글 전체에 Score 하나 (안 보임 · 말로만 · 한 일로 드러남), 가장 큰 확률의 단계.
- ``paragraph``: 지금 설계 (``usecases.talent_fit``). (줄 × 문단) Noul "한 일로 보여 주는가" + 줄마다 Noul
  "말로 내세우는가", 코드가 합친다. 근거 문단이 맞는지도 센다.

판정 모델은 ``models.toml``의 ``routes.talent_fit`` 연결로 직접 부른다 (DB·장부 없음). 과금된다 (32건 전부 $0.01 아래).

    uv run python -m career_core.evals.talent_eval --models models.toml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from importlib import resources
from typing import Any
from uuid import UUID

from career_core.llm.config import TALENT_FIT_PURPOSE, load_models_config
from career_core.llm.gateway import default_client_factory
from career_core.llm.pricing import PriceBook
from career_core.usecases import talent_fit as tf

_DOC = UUID(int=0)

# 예전 설계 (비교용): 글 전체에 Score 하나, 단계 0 안 보임 · 1 말로만 · 2 드러남
SCORE_LEVELS = [
    "Nothing in the essay relates to this trait, or the essay shows the opposite.",
    (
        "The essay names or claims the trait, repeats the trait's words, or touches it only vaguely, "
        "without a concrete thing the applicant did."
    ),
    (
        "The essay describes something the applicant actually did, with a concrete situation or "
        "result, that clearly shows this trait even without naming it."
    ),
]
_SCORE_NAMES = ("absent", "claimed", "shown")


def load_fixture() -> dict[str, Any]:
    return json.loads(resources.files(__package__).joinpath("talent_eval.json").read_text("utf-8"))


def score_questions(traits: list[str]) -> dict[str, dict]:
    return {
        f"t{i}": {
            "type": "score",
            "instructions": {
                "task": "How clearly does `essay`, the applicant's answer to `question`, show that "
                "the applicant has this trait the company looks for?",
                "trait": t,
            },
            "criteria": SCORE_LEVELS,
        }
        for i, t in enumerate(traits)
    }


def score_level(answer: Any) -> str:
    probs = (answer or {}).get("probabilities") or {}
    vals = [float(probs.get(str(i), 0)) for i in range(3)]
    top = max(range(3), key=lambda i: vals[i])
    return _SCORE_NAMES[top] if vals[top] >= 0.5 else "unclear"


@dataclass
class Tally:
    n: int = 0
    correct: int = 0
    false_shown: int = 0  # 정답이 shown이 아닌데 shown (후하게 본 것)
    missed_shown: int = 0  # 정답이 shown인데 shown이 아님
    evidence_hit: int = 0  # shown을 맞힌 것 중 근거 문단이 정답 문단에 있는 것
    shown_hits: int = 0
    input_tokens: int = 0
    confusion: Counter = field(default_factory=Counter)
    misses: list[str] = field(default_factory=list)

    def add(self, key: str, gold: str, got: str) -> None:
        self.n += 1
        self.confusion[(gold, got)] += 1
        if gold == got:
            self.correct += 1
        else:
            self.misses.append(f"{key}: {gold} → {got}")
        if got == "shown" and gold != "shown":
            self.false_shown += 1
        if gold == "shown" and got != "shown":
            self.missed_shown += 1


async def run(models: str, designs: list[str]) -> dict[str, Tally]:
    config = load_models_config(models)
    route = config.routes.get(TALENT_FIT_PURPOSE)
    if route is None:
        raise SystemExit("models.toml에 routes.talent_fit이 없습니다")
    endpoint = config.endpoints[route.endpoint]
    key = os.environ.get(endpoint.api_key_env or "")
    client = default_client_factory(endpoint, key)
    fx = load_fixture()
    names = list(fx["traits"])
    traits = [tf.Trait(fx["traits"][k], "posting", True) for k in names]
    out = {d: Tally() for d in designs}
    for essay in fx["essays"]:
        paras = essay["paragraphs"]
        base = {"company": "가상테크", "question": essay["question"]}
        if "score" in designs:
            state = {**base, "essay": "\n\n".join(paras)}
            got = await client.judge(route.model, state, score_questions([t.text for t in traits]))
            out["score"].input_tokens += got.usage.input_tokens
            for i, k in enumerate(names):
                out["score"].add(
                    f"{essay['id']}/{k}", essay["gold"][k], score_level(got.answers.get(f"t{i}"))
                )
        if "paragraph" in designs:
            state = {**base, "essay": {"paragraphs": paras}}
            got = await client.judge(route.model, state, tf.build_questions(traits, len(paras)))
            t = out["paragraph"]
            t.input_tokens += got.usage.input_tokens
            ids = [str(j) for j in range(len(paras))]
            fits = tf.read_fits(got.answers, traits, ids, _DOC)
            for k, fit in zip(names, fits, strict=True):
                level = fit.level if fit else "unclear"
                t.add(f"{essay['id']}/{k}", essay["gold"][k], level)
                if level == "shown" and essay["gold"][k] == "shown" and fit:
                    t.shown_hits += 1
                    want = {str(j) for j in essay.get("evidence", {}).get(k, [])}
                    if want & set(fit.evidence):
                        t.evidence_hit += 1
                    else:
                        t.misses.append(f"{essay['id']}/{k}: 근거 {sorted(want)} → {fit.evidence}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", default="models.toml")
    ap.add_argument("--design", choices=["score", "paragraph", "both"], default="both")
    args = ap.parse_args()
    designs = ["score", "paragraph"] if args.design == "both" else [args.design]
    res = asyncio.run(run(args.models, designs))
    config = load_models_config(args.models)
    route = config.routes[TALENT_FIT_PURPOSE]
    price = PriceBook.load().lookup(
        config.endpoints[route.endpoint].pricing_provider or "", route.model
    )
    for name, t in res.items():
        usd = float(price.rates.input) * t.input_tokens / 1_000_000 if price else None
        print(
            f"\n== {name}: {t.correct}/{t.n} 맞음 · 후하게 본 것 {t.false_shown} · 놓친 드러남 {t.missed_shown}"
        )
        if name == "paragraph":
            print(f"   근거 문단 맞음 {t.evidence_hit}/{t.shown_hits}")
        print(f"   입력 토큰 {t.input_tokens}" + (f" · ${usd:.5f}" if usd is not None else ""))
        for m in t.misses:
            print("   틀림", m)


if __name__ == "__main__":
    main()

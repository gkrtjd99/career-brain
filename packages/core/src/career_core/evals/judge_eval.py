"""판정 모델 평가: 합성 자료로 기준선(단어 일치)과 판정 모델의 정확도를 잰다.

- 주장 판정 24문항: "이 주장이 이 요건을 보여 주는가" (학습·부정·다른 표현·같은 낱말 다른 뜻·지시문 포함)
- 가시성 판정 16문항(문서 4개): "이 문서만 읽었을 때 요건 근거가 보이는가"

사용자 DB를 건드리지 않는다. 판정 모델은 게이트웨이(`routes.judge_claim`, `routes.judge_visibility`)로만
부르고, 호출 장부는 임시 DB에 남긴 뒤 지운다. 라우트가 없으면 기준선만 출력한다.

    uv run python -m career_core.evals.judge_eval --models models.toml
    uv run python -m career_core.evals.judge_eval --baseline-only

(임시 DB를 만들 수 있는 관리자 접속: `CAREER_TEST_ADMIN_URL`, 기본은 로컬 docker compose)
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

from career_core.domain.documents import (
    BlockInput,
    CheckContext,
    key_terms,
    matched_terms,
    requirement_visibility,
)
from career_core.usecases import judges


def load_fixture() -> dict[str, Any]:
    return json.loads(resources.files(__package__).joinpath("judge_eval.json").read_text("utf-8"))


@dataclass
class Score:
    """이진 정확도 (yes ↔ 나머지). 판정 모델의 unclear·누락은 "아니오"로 센다."""

    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    unclear: int = 0
    missing: int = 0
    wrong: list[str] = field(default_factory=list)

    def add(self, case_id: str, predicted: bool, label: bool) -> None:
        if predicted and label:
            self.tp += 1
        elif predicted:
            self.fp += 1
            self.wrong.append(case_id)
        elif label:
            self.fn += 1
            self.wrong.append(case_id)
        else:
            self.tn += 1

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    @property
    def correct(self) -> int:
        return self.tp + self.tn

    def __str__(self) -> str:
        extra = f" unclear {self.unclear}" if self.unclear else ""
        extra += f" missing {self.missing}" if self.missing else ""
        return f"accuracy {self.correct}/{self.total}  (yes→no {self.fn}, no→yes {self.fp}){extra}"


@dataclass
class Report:
    scores: dict[str, dict[str, Score]]  # {"claim"|"visibility": {"baseline"|"judge": Score}}
    runs: list[judges.JudgeRun] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return sum(r.calls for r in self.runs)

    @property
    def cost_usd(self) -> float | None:
        costs = [r.cost_usd for r in self.runs if r.calls]
        if any(c is None for c in costs):
            return None
        return sum(float(c) for c in costs if c is not None)


def baseline_claim(requirement: str, claim: str) -> bool:
    """지금 기준선: 요건 핵심어가 주장 문장에 있는가 (기술명 별칭 포함, `domain.documents`)."""
    return bool(matched_terms(claim, key_terms(requirement)))


_SHOWN = {"met_visible", "overclaim_risk"}


def baseline_visibility(requirement: str, document: str) -> bool:
    """지금 기준선: 요건 가시성 검사에서 문서에 요건 낱말이 보이는가."""
    ctx = CheckContext(requirements=(("requirements", requirement),))
    [row] = requirement_visibility(ctx, [BlockInput("b1", document)])
    return row["verdict"] in _SHOWN


async def evaluate(fx: dict[str, Any], call_model: judges.ModelCaller | None) -> Report:
    """기준선은 항상, 판정 모델은 `call_model`이 있을 때만. 합성 자료라 등급은 public."""
    scores: dict[str, dict[str, Score]] = {
        "claim": {"baseline": Score()},
        "visibility": {"baseline": Score()},
    }
    runs: list[judges.JudgeRun] = []
    for c in fx["claims"]:
        scores["claim"]["baseline"].add(
            c["id"], baseline_claim(c["requirement"], c["claim"]), c["label"] == "yes"
        )
    for d in fx["documents"]:
        for r in d["requirements"]:
            scores["visibility"]["baseline"].add(
                r["id"], baseline_visibility(r["text"], d["text"]), r["label"] == "yes"
            )
    if call_model is None:
        return Report(scores, runs)

    classes = frozenset({"public"})
    judged = scores["claim"].setdefault("judge", Score())
    # 같은 요건 문장은 한 번에 묻는다 (호출 수를 줄인다)
    groups: dict[str, list[dict[str, Any]]] = {}
    for c in fx["claims"]:
        groups.setdefault(c["requirement"], []).append(c)
    for requirement, cases in groups.items():
        verdicts, run = await judges.judge_claims(
            call_model, requirement, [(c["id"], c["claim"]) for c in cases], data_classes=classes
        )
        runs.append(run)
        for c in cases:
            v = verdicts.get(c["id"])
            judged.missing += v is None
            judged.unclear += v is not None and v.verdict == "unclear"
            judged.add(c["id"], v is not None and v.verdict == "yes", c["label"] == "yes")
    judged = scores["visibility"].setdefault("judge", Score())
    for d in fx["documents"]:
        verdicts, run = await judges.judge_visibility(
            call_model,
            [(r["id"], r["text"]) for r in d["requirements"]],
            d["text"],
            data_classes=classes,
        )
        runs.append(run)
        for r in d["requirements"]:
            v = verdicts.get(r["id"])
            judged.missing += v is None
            judged.unclear += v is not None and v.verdict == "unclear"
            judged.add(r["id"], v is not None and v.verdict == "yes", r["label"] == "yes")
    return Report(scores, runs)


def format_report(report: Report) -> list[str]:
    lines = []
    for task, by in report.scores.items():
        for name, score in by.items():
            wrong = f"  wrong: {','.join(score.wrong)}" if score.wrong else ""
            lines.append(f"{task:10} {name:8} {score}{wrong}")
    if report.runs:
        models = sorted({r.model for r in report.runs if r.model})
        bases = sorted({r.cost_basis for r in report.runs if r.cost_basis})
        notes = sorted({r.note for r in report.runs if r.note})
        cost = report.cost_usd
        lines.append(
            f"judge calls {report.calls}  models {','.join(models) or '-'}  "
            f"cost_usd {'unknown' if cost is None else f'{cost:.6f}'} ({','.join(bases) or '-'})"
            + (f"  notes {','.join(notes)}" if notes else "")
        )
    return lines


async def _main(models_path: str | None, baseline_only: bool) -> None:
    fx = load_fixture()
    if baseline_only:
        for line in format_report(await evaluate(fx, None)):
            print(line)
        return
    from career_core.db import Database
    from career_core.domain.workspace import Role, scopes_for_role
    from career_core.llm.config import load_models_config
    from career_core.llm.gateway import ModelGateway
    from career_core.llm.pricing import PriceBook
    from career_core.ports import ActorContext
    from career_core.testing import migrated_database
    from career_core.usecases.workspaces import create_workspace

    config = load_models_config(models_path)
    missing = [
        p
        for p in (judges.JUDGE_CLAIM_PURPOSE, judges.JUDGE_VISIBILITY_PURPOSE)
        if p not in config.routes
    ]
    if missing:
        print(f"no judge route ({', '.join(missing)}): baseline only")
        for line in format_report(await evaluate(fx, None)):
            print(line)
        return
    with migrated_database() as urls:
        db = Database(urls.app)
        try:
            ws, subject = await create_workspace(db, name="eval", owner_display_name="eval")
            actor = ActorContext(subject, ws, scopes_for_role(Role.OWNER))
            gateway = ModelGateway(db, config, PriceBook.load(*config.price_files))
            report = await evaluate(fx, judges.gateway_caller(gateway, actor))
            for line in format_report(report):
                print(line)
        finally:
            await db.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", default="models.toml")
    parser.add_argument("--baseline-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(_main(args.models, args.baseline_only))


if __name__ == "__main__":
    main()

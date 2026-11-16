"""일괄 실행 예상 비용 (웹에서 시작하기 전에 보여 주는 참고값, c0023).

가격표로 미리 계산하지 않는다: 문단·경험 글의 길이와 모델 응답 길이를 시작 전에는 모른다. 대신 **이 공간에서
같은 workflow를 돌린 지난 실행**의 장부 기준 비용(``runs.cost_usd`` / ``runs.model_calls``)으로 호출 1회
평균을 내고, 이번에 계획한 호출 수를 곱한다.

- 지난 실행이 없으면 예상 없음(``None``). 화면은 호출 수만 보여 준다.
- 지난 실행에 단가 미상 호출이 있으면 ``cost_unknown``. 평균은 아는 비용만으로 낸다 (하한).
- 구독 프록시의 정가 환산액은 실지출이 아니므로 들어가지 않는다 (``runs.cost_usd`` 규칙 그대로).
- 예상은 약속이 아니다. 월 예산·단가 미상 거부는 실행 때 게이트웨이가 그대로 지킨다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from career_core.db import Database
from career_core.db.models import Run
from career_core.domain.runs import RunRuleError
from career_core.ports import ActorContext

# 계획 단위 하나(문단·경험)당 모델 호출 수
CALLS_PER_UNIT = {"suggest_document_batch": 1, "interview_question_batch": 1}
HISTORY_RUNS = 20
MAX_UNITS = 1000


@dataclass(frozen=True, slots=True)
class RunEstimate:
    workflow: str
    units: int
    calls: int
    # 호출 1회 평균 실지출 (지난 실행 기준). 지난 실행이 없으면 None
    per_call_usd: str | None
    estimate_usd: str | None
    based_on_runs: int
    based_on_calls: int
    cost_unknown: bool
    basis: str = "past_runs"


def _usd(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


async def estimate_run(db: Database, actor: ActorContext, workflow: str, units: int) -> RunEstimate:
    per_unit = CALLS_PER_UNIT.get(workflow)
    if per_unit is None:
        raise RunRuleError("unknown_workflow")
    if units < 1 or units > MAX_UNITS:
        raise RunRuleError("invalid_input")
    calls = units * per_unit
    async with db.workspace_session(actor.workspace_id) as s:
        rows = (
            await s.execute(
                select(Run.cost_usd, Run.model_calls, Run.cost_unknown)
                .where(Run.workflow == workflow, Run.model_calls > 0)
                .order_by(Run.created_at.desc())
                .limit(HISTORY_RUNS)
            )
        ).all()
    past_calls = sum(int(r.model_calls) for r in rows)
    if not past_calls:
        return RunEstimate(workflow, units, calls, None, None, 0, 0, False)
    spent = sum((Decimal(r.cost_usd or 0) for r in rows), Decimal(0))
    per_call = spent / past_calls
    return RunEstimate(
        workflow,
        units,
        calls,
        _usd(per_call),
        _usd(per_call * calls),
        len(rows),
        past_calls,
        any(r.cost_unknown for r in rows),
    )


__all__ = ["CALLS_PER_UNIT", "RunEstimate", "estimate_run"]

"""일괄 실행 예상 비용 (웹에서 시작하기 전에 보여 주는 참고값, c0023).

가격표로 미리 계산하지 않는다: 모델 응답 길이를 시작 전에는 모른다. 대신 **이 공간에서 같은 workflow를 돌린
지난 실행 20개**의 장부 기준 비용(``runs.cost_usd``, ``runs.model_calls``)을 쓴다. 근거(``basis``)는 둘 중 하나다:

- ``input_chars`` (입력을 같이 보낸 경우, ``POST …/estimate``): 지난 실행마다 (호출 수, 처리한 입력 글자 수, 비용)
  으로 ``비용 ≈ a × 호출 수 + b × 글자 수``를 최소제곱으로 맞춘다 (a = 호출마다 드는 고정분: 시스템 지시·문맥·
  응답, b = 글자당). 이번 예상 = a × 이번 호출 수 + b × 이번 입력 글자 수. 글자 수는 workflow의
  ``input_sizes``가 세고, 실행의 계획 단계가 같은 값을 ``position.chars``(단위마다)에 남긴다. 처리한 글자 수 =
  ``chars[:next]`` (실패·취소로 중간에 끝난 실행은 처리한 만큼만).
- ``past_runs`` (그 밖): 호출 1회 평균(비용 합 ÷ 호출 합) × 이번 호출 수. 아래 경우 여기로 돌아온다:
  입력이 없다, 글자 수를 남긴 지난 실행이 2개보다 적다, 지난 실행들의 "호출당 글자 수"가 거의 같아 a·b를 가를 수
  없다, 맞춘 a나 b가 음수다.

- 지난 실행이 없으면 예상 없음(``None``). 화면은 호출 수만 보여 준다.
- 지난 실행에 단가 미상 호출이 있으면 ``cost_unknown``. 예상은 아는 비용만으로 낸다 (하한).
- 구독 프록시의 정가 환산액은 실지출이 아니므로 들어가지 않는다 (``runs.cost_usd`` 규칙 그대로).
- 예상은 약속이 아니다. 월 예산·단가 미상 거부는 실행 때 게이트웨이가 그대로 지킨다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select

from career_core.db import Database
from career_core.db.models import Run
from career_core.domain.runs import RunRuleError
from career_core.ports import ActorContext

# 계획 단위 하나(문단·경험)당 모델 호출 수
CALLS_PER_UNIT = {
    "suggest_document_batch": 1,
    "interview_question_batch": 1,
    # 문항마다 한 번 (분량을 넘으면 한 번 더 줄여 달라고 한다)
    "draft_application": 1,
}
HISTORY_RUNS = 20
MAX_UNITS = 1000
MIN_SIZED_RUNS = 2
# 지난 실행들의 호출당 글자 수가 이 비율보다 덜 퍼져 있으면 고정분·글자당을 가르지 않는다
MIN_SPREAD = 0.05

BASIS_PER_CALL = "past_runs"
BASIS_INPUT_CHARS = "input_chars"


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
    basis: str = BASIS_PER_CALL
    # 이번 입력 글자 수 (입력을 보냈을 때)
    chars: int | None = None
    # basis = input_chars일 때: 맞춘 호출당 고정분·1천 자당 비용, 근거 실행들이 처리한 글자 수
    fixed_per_call_usd: str | None = None
    per_1k_chars_usd: str | None = None
    based_on_chars: int = 0


def _usd(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def processed_chars(position: Any) -> int | None:
    """실행이 처리한 입력 글자 수 (``position.chars[:next]``). 글자 수를 남기지 않은 실행이면 None."""
    if not isinstance(position, dict):
        return None
    chars, nxt = position.get("chars"), position.get("next")
    if not isinstance(chars, list) or isinstance(nxt, bool) or not isinstance(nxt, int):
        return None
    if not all(isinstance(c, int) and not isinstance(c, bool) and c >= 0 for c in chars):
        return None
    return sum(chars[: max(0, nxt)])


def fit_cost(points: Sequence[tuple[int, int, Decimal]]) -> tuple[Decimal, Decimal] | None:
    """(호출 수, 글자 수, 비용) 점들로 ``비용 ≈ a × 호출 + b × 글자`` 최소제곱 (절편 없음). 못 가르면 None."""
    if len(points) < MIN_SIZED_RUNS:
        return None
    cc = sum(float(n) * n for n, _, _ in points)
    xx = sum(float(x) * x for _, x, _ in points)
    cx = sum(float(n) * x for n, x, _ in points)
    cy = sum(float(n) * float(y) for n, _, y in points)
    xy = sum(float(x) * float(y) for _, x, y in points)
    det = cc * xx - cx * cx
    # det / (cc·xx) = 1 - cos²(호출 벡터, 글자 벡터): 호출당 글자 수가 모두 같으면 0
    if cc <= 0 or xx <= 0 or det / (cc * xx) < MIN_SPREAD**2:
        return None
    a = (cy * xx - cx * xy) / det
    b = (cc * xy - cx * cy) / det
    if a < 0 or b < 0:
        return None
    return Decimal(repr(a)), Decimal(repr(b))


async def estimate_run(
    db: Database,
    actor: ActorContext,
    workflow: str,
    units: int,
    *,
    sizes: Sequence[int] | None = None,
) -> RunEstimate:
    """``sizes``: 이번 입력의 단위별 글자 수 (workflow ``input_sizes``). 주면 ``units``는 그 길이다."""
    per_unit = CALLS_PER_UNIT.get(workflow)
    if per_unit is None:
        raise RunRuleError("unknown_workflow")
    if sizes is not None:
        units = len(sizes)
    if units < 1 or units > MAX_UNITS:
        raise RunRuleError("invalid_input")
    calls = units * per_unit
    chars = sum(sizes) if sizes is not None else None
    async with db.workspace_session(actor.workspace_id) as s:
        rows = (
            await s.execute(
                select(Run.cost_usd, Run.model_calls, Run.cost_unknown, Run.position)
                .where(Run.workflow == workflow, Run.model_calls > 0)
                .order_by(Run.created_at.desc())
                .limit(HISTORY_RUNS)
            )
        ).all()
    past_calls = sum(int(r.model_calls) for r in rows)
    if not past_calls:
        return RunEstimate(workflow, units, calls, None, None, 0, 0, False, chars=chars)
    spent = sum((Decimal(r.cost_usd or 0) for r in rows), Decimal(0))
    per_call = spent / past_calls
    unknown = any(r.cost_unknown for r in rows)

    if chars is not None:
        sized = [
            (int(r.model_calls), x, Decimal(r.cost_usd or 0), bool(r.cost_unknown))
            for r in rows
            if (x := processed_chars(r.position)) is not None and x > 0
        ]
        fit = fit_cost([(n, x, y) for n, x, y, _ in sized])
        if fit is not None:
            a, b = fit
            return RunEstimate(
                workflow,
                units,
                calls,
                _usd(per_call),
                _usd(a * calls + b * chars),
                len(sized),
                sum(n for n, _, _, _ in sized),
                any(u for *_, u in sized),
                BASIS_INPUT_CHARS,
                chars,
                _usd(a),
                _usd(b * 1000),
                sum(x for _, x, _, _ in sized),
            )
    return RunEstimate(
        workflow,
        units,
        calls,
        _usd(per_call),
        _usd(per_call * calls),
        len(rows),
        past_calls,
        unknown,
        chars=chars,
    )


__all__ = [
    "BASIS_INPUT_CHARS",
    "BASIS_PER_CALL",
    "CALLS_PER_UNIT",
    "RunEstimate",
    "estimate_run",
    "fit_cost",
    "processed_chars",
]

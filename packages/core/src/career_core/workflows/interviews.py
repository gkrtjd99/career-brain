"""AI 인터뷰 질문 묶음 workflow ``interview_question_batch`` (c0016).

경험마다 한 단계: 그 경험의 인터뷰 세션을 하나 만들고(템플릿 질문 포함) AI 보충 질문을 요청한다.
``suggest_document_batch``와 같은 방식이다.

- 모델 호출은 ``StepContext.model``을 거친다 → 비용이 실행에 쌓이고 장부 행에 실행·단계가 붙는다.
- 세션 멱등 키 ``ctx.effect_key("session:<경험 ID>")``, 질문 요청 멱등 키 ``ctx.effect_key("questions:<경험 ID>")``.
  단계 도중 worker가 죽어 같은 경험을 다시 해도 세션·질문이 늘지 않고, 이미 저장한 요청(질문 0개 포함)은
  모델을 다시 부르지 않는다.
- AI는 질문만 만든다. 답·주장은 사용자가 쓴다 (브레인에 쓰지 않는다). restricted 근거의 주장은 보내지 않는다
  (``interviews.add_ai_questions``의 규칙 그대로).
- 실행 기록(position·result)에는 경험·세션 ID와 개수만 남긴다. 질문 글은 인터뷰 세션에서 본다.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from career_core.db import Experience
from career_core.domain.brain import ClaimRuleError
from career_core.domain.runs import RunRuleError
from career_core.usecases import interviews as interview_uc
from career_core.usecases.brain import NotFound
from career_core.usecases.runs import StepContext, StepFailed, StepResult, Workflow

MAX_BATCH_EXPERIENCES = 20
# 세션을 만드는 쓰기 권한 (시작 권한 interview.ai는 egress)
WRITE_ACTION = "brain.write"


def _validate(raw: dict[str, Any]) -> dict[str, Any]:
    ids = raw.get("experience_ids")
    if not isinstance(ids, list) or not ids or len(ids) > MAX_BATCH_EXPERIENCES:
        raise RunRuleError("invalid_input")
    try:
        experiences = list(dict.fromkeys(str(UUID(str(i))) for i in ids))
    except ValueError as exc:
        raise RunRuleError("invalid_input") from exc
    count = raw.get("count", 3)
    if isinstance(count, bool) or not isinstance(count, int):
        raise RunRuleError("invalid_input")
    return {
        "experience_ids": experiences,
        "count": max(1, min(count, interview_uc.MAX_AI_QUESTIONS)),
    }


async def _plan(ctx: StepContext) -> StepResult:
    """대상 경험이 이 공간에 있는지 확인한다 (ID만 위치에 남긴다)."""
    if not ctx.deps.permissions.allows(ctx.actor, WRITE_ACTION):
        raise StepFailed("forbidden")
    ids = ctx.input["experience_ids"]
    async with ctx.deps.db.workspace_session(ctx.actor.workspace_id) as s:
        for eid in ids:
            if await s.get(Experience, UUID(eid)) is None:
                raise StepFailed("target_not_found")
    return StepResult(position={"experiences": ids, "next": 0, "sessions": [], "questions": 0})


async def _ask_next(ctx: StepContext) -> StepResult:
    ids: list[str] = ctx.position["experiences"]
    i = int(ctx.position.get("next", 0))
    if i >= len(ids):
        return StepResult(done=True, result=_result(ctx.position))
    eid = ids[i]
    try:
        session = await interview_uc.start_interview(
            ctx.deps.db,
            ctx.actor,
            UUID(eid),
            idempotency_key=ctx.effect_key(f"session:{eid}"),
        )
        created = await interview_uc.add_ai_questions(
            ctx.deps.db,
            None,
            ctx.actor,
            session,
            count=ctx.input["count"],
            idempotency_key=ctx.effect_key(f"questions:{eid}"),
            call_model=ctx.model,
            run_id=ctx.run_id,
        )
    except NotFound as exc:
        raise StepFailed("target_not_found") from exc
    except ClaimRuleError as exc:
        raise StepFailed(exc.code) from exc
    sessions = list(ctx.position.get("sessions", []))
    if str(session) not in sessions:
        sessions.append(str(session))
    position = {
        "experiences": ids,
        "next": i + 1,
        "sessions": sessions,
        "questions": int(ctx.position.get("questions", 0)) + len(created),
    }
    if i + 1 >= len(ids):
        return StepResult(position=position, done=True, result=_result(position))
    return StepResult(position=position, repeat=True)


def _result(position: dict[str, Any]) -> dict[str, Any]:
    return {
        "experiences": len(position.get("experiences", [])),
        "sessions": list(position.get("sessions", [])),
        "questions": position.get("questions", 0),
    }


INTERVIEW_QUESTION_BATCH = Workflow(
    name="interview_question_batch",
    action="interview.ai",
    steps=(_plan, _ask_next),
    validate=_validate,
    description=(
        "Start an interview per experience and ask AI follow-up questions (questions only; the "
        "user answers). Cost accrues to the run; resuming does not duplicate sessions or questions."
    ),
)

__all__ = ["INTERVIEW_QUESTION_BATCH"]

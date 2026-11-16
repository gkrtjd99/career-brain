"""문서 수정안 일괄 workflow ``suggest_document_batch`` (c0012).

문단 하나씩 수정안을 요청한다. 모델 호출은 ``StepContext.model``을 거치므로 비용이 실행에 쌓인다.
각 문단 요청의 멱등 키는 ``ctx.effect_key("block:<id>")``다. 단계 도중에 worker가 죽어 같은 문단을 다시
해도, 이미 저장한 수정안이 있으면 모델을 부르지 않고 그것을 쓴다 — 재개해도 수정안이 늘지 않는다.

실행 기록(position·result)에는 문단 ID와 개수만 남긴다. 수정안 본문은 문서의 수정안 목록에서 본다.
모델이 되물은 질문은 개수만 남는다 (질문 글을 보려면 문서 화면에서 문단별로 다시 요청한다).

수정안은 초안이다. 적용은 사람이 문서 화면에서 한다 (이 workflow는 문서·브레인에 쓰지 않는다).
시작 뒤 문서가 바뀌면(base_revision 불일치) 남은 문단은 하지 않고 ``revision_conflict``로 끝난다.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from career_core.domain.documents import DocumentRuleError, RevisionConflict
from career_core.domain.runs import RunRuleError
from career_core.usecases import applications as app_uc
from career_core.usecases.brain import NotFound
from career_core.usecases.runs import StepContext, StepFailed, StepResult, Workflow

MAX_BATCH_BLOCKS = 50


def _validate(raw: dict[str, Any]) -> dict[str, Any]:
    try:
        application_id = str(UUID(str(raw.get("application_id"))))
        document_id = str(UUID(str(raw.get("document_id"))))
    except ValueError as exc:
        raise RunRuleError("invalid_input") from exc
    base = raw.get("base_revision")
    if isinstance(base, bool) or not isinstance(base, int) or base < 1:
        raise RunRuleError("invalid_input")
    block_ids = raw.get("block_ids")
    if block_ids is not None and (
        not isinstance(block_ids, list)
        or len(block_ids) > MAX_BATCH_BLOCKS
        or not all(isinstance(b, str) and 0 < len(b) <= 100 for b in block_ids)
    ):
        raise RunRuleError("invalid_input")
    instruction = raw.get("instruction")
    if instruction is not None and not isinstance(instruction, str):
        raise RunRuleError("invalid_input")
    return {
        "application_id": application_id,
        "document_id": document_id,
        "base_revision": base,
        "block_ids": list(dict.fromkeys(block_ids)) if block_ids else None,
        "instruction": (instruction or "").strip()[: app_uc.MAX_INSTRUCTION_CHARS] or None,
    }


async def _plan(ctx: StepContext) -> StepResult:
    """대상 문단 목록을 정한다 (ID만 위치에 남긴다)."""
    try:
        view = await app_uc.get_document(
            ctx.deps.db,
            ctx.actor,
            UUID(ctx.input["application_id"]),
            UUID(ctx.input["document_id"]),
        )
    except NotFound as exc:
        raise StepFailed("target_not_found") from exc
    if not view.editable:
        raise StepFailed("application_locked")
    if view.current_revision != ctx.input["base_revision"]:
        raise StepFailed("revision_conflict")
    ids = [b.id for b in view.blocks]
    wanted = ctx.input.get("block_ids")
    if wanted:
        if set(wanted) - set(ids):
            raise StepFailed("unknown_block")
        ids = [i for i in ids if i in set(wanted)]
    ids = ids[:MAX_BATCH_BLOCKS]
    if not ids:
        raise StepFailed("nothing_to_suggest")
    return StepResult(
        position={"blocks": ids, "next": 0, "proposed": 0, "rejected": 0, "questions": 0}
    )


async def _suggest_next(ctx: StepContext) -> StepResult:
    ids: list[str] = ctx.position["blocks"]
    i = int(ctx.position.get("next", 0))
    if i >= len(ids):
        return StepResult(done=True, result=_result(ctx.position))
    block_id = ids[i]
    try:
        res = await app_uc.suggest_edits(
            ctx.deps.db,
            None,
            ctx.actor,
            UUID(ctx.input["application_id"]),
            UUID(ctx.input["document_id"]),
            base_revision=ctx.input["base_revision"],
            block_ids=[block_id],
            instruction=ctx.input.get("instruction"),
            idempotency_key=ctx.effect_key(f"block:{block_id}"),
            call_model=ctx.model,
        )
    except RevisionConflict as exc:
        raise StepFailed("revision_conflict") from exc
    except DocumentRuleError as exc:
        raise StepFailed(exc.code) from exc
    except NotFound as exc:
        raise StepFailed("target_not_found") from exc
    position = {
        "blocks": ids,
        "next": i + 1,
        "proposed": ctx.position.get("proposed", 0)
        + sum(1 for s in res.suggestions if s.status == "proposed"),
        "rejected": ctx.position.get("rejected", 0)
        + sum(1 for s in res.suggestions if s.status == "rejected"),
        # 질문 글은 실행 기록에 남기지 않는다 (원문 없음 규칙). 개수만
        "questions": ctx.position.get("questions", 0) + len(res.questions),
    }
    if i + 1 >= len(ids):
        return StepResult(position=position, done=True, result=_result(position))
    return StepResult(position=position, repeat=True)


def _result(position: dict[str, Any]) -> dict[str, Any]:
    return {
        "blocks": len(position.get("blocks", [])),
        "proposed": position.get("proposed", 0),
        "rejected": position.get("rejected", 0),
        "questions": position.get("questions", 0),
    }


SUGGEST_DOCUMENT_BATCH = Workflow(
    name="suggest_document_batch",
    action="documents.ai",
    steps=(_plan, _suggest_next),
    validate=_validate,
    description=(
        "Request AI edit suggestions paragraph by paragraph (drafts only; a human applies them). "
        "Cost accrues to the run; resuming does not duplicate suggestions."
    ),
)

__all__ = ["SUGGEST_DOCUMENT_BATCH"]

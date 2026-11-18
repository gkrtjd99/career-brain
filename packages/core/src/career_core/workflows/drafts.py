"""지원서 초안 workflow ``draft_application`` (plan §22, c0051).

첫 단계가 쓸 문항을 정하고(글이 없는 문항, ``overwrite``면 고른 문항 모두) 문항마다 한 단계씩 초안을 쓴다
(``usecases.application_draft.draft_document``).

- 모델 호출은 ``StepContext.model``을 거친다 → 비용이 실행에 쌓이고 장부 행에 실행·단계가 붙는다.
- 문항마다 revision 멱등 키 ``ctx.effect_key("doc:<문서 ID>")``: 단계 도중 worker가 죽어 같은 문항을 다시 해도
  이미 쓴 초안이 있으면 모델을 부르지 않는다.
- 계획할 때의 revision 번호(``position.base``)와 다르면(그 사이 사람이 저장) 그 문항은 건너뛴다 (``changed``).
- 같은 경험을 두 문항의 주 경험으로 쓰지 않으려고, 앞 문항에서 쓴 주 경험을 ``position.avoid``에 쌓는다.
- 실행 기록(position·result)에는 문서·경험 ID와 개수만 남긴다. 글은 문서의 revision에서 본다.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from career_core.db import Database
from career_core.domain.documents import DocumentRuleError
from career_core.domain.runs import RunRuleError
from career_core.ports import ActorContext
from career_core.usecases import application_draft as draft_uc
from career_core.usecases.brain import NotFound
from career_core.usecases.runs import StepContext, StepFailed, StepResult, Workflow

MAX_DOCUMENTS = 30
# 문서에 글을 쓰는 권한 (시작 권한 documents.ai는 egress)
WRITE_ACTION = "applications.write"


def _validate(raw: dict[str, Any]) -> dict[str, Any]:
    try:
        application_id = str(UUID(str(raw.get("application_id"))))
    except ValueError as exc:
        raise RunRuleError("invalid_input") from exc
    ids = raw.get("document_ids")
    if ids is not None:
        if not isinstance(ids, list) or len(ids) > MAX_DOCUMENTS:
            raise RunRuleError("invalid_input")
        try:
            ids = list(dict.fromkeys(str(UUID(str(i))) for i in ids))
        except ValueError as exc:
            raise RunRuleError("invalid_input") from exc
    overwrite = raw.get("overwrite", False)
    if not isinstance(overwrite, bool):
        raise RunRuleError("invalid_input")
    instruction = raw.get("instruction")
    if instruction is not None and not isinstance(instruction, str):
        raise RunRuleError("invalid_input")
    return {
        "application_id": application_id,
        "document_ids": ids or None,
        "overwrite": overwrite,
        "instruction": (instruction or "").strip()[: draft_uc.MAX_INSTRUCTION_CHARS] or None,
    }


async def _plan_for(db: Database, actor: ActorContext, inp: dict[str, Any]) -> draft_uc.DraftPlan:
    return await draft_uc.plan_draft(
        db,
        actor,
        UUID(inp["application_id"]),
        document_ids=inp.get("document_ids"),
        overwrite=inp.get("overwrite", False),
    )


async def input_sizes(db: Database, actor: ActorContext, inp: dict[str, Any]) -> list[int]:
    """시작 전 예상 비용용: 문항마다 문항 글 + 준비 문서 후보의 사실 글 (계획 단계가 남길 ``chars``와 같다)."""
    try:
        plan = await _plan_for(db, actor, inp)
    except (NotFound, DocumentRuleError) as exc:
        raise RunRuleError("invalid_input") from exc
    if not plan.documents:
        raise RunRuleError("nothing_to_draft")
    return plan.sizes


async def _plan(ctx: StepContext) -> StepResult:
    if not ctx.deps.permissions.allows(ctx.actor, WRITE_ACTION):
        raise StepFailed("forbidden")
    try:
        plan = await _plan_for(ctx.deps.db, ctx.actor, ctx.input)
    except NotFound as exc:
        raise StepFailed("target_not_found") from exc
    except DocumentRuleError as exc:
        raise StepFailed(exc.code) from exc
    if not plan.documents:
        raise StepFailed("nothing_to_draft")
    return StepResult(
        position={
            "documents": plan.documents,
            "base": plan.base,
            "chars": plan.sizes,
            "avoid": plan.avoid,
            "next": 0,
            "results": [],
            "written": 0,
            "skipped": 0,
            "unconfirmed": 0,
        }
    )


async def _draft_next(ctx: StepContext) -> StepResult:
    ids: list[str] = ctx.position["documents"]
    i = int(ctx.position.get("next", 0))
    if i >= len(ids):
        return StepResult(done=True, result=_result(ctx.position))
    doc_id = ids[i]
    avoid = list(ctx.position.get("avoid", []))
    try:
        out = await draft_uc.draft_document(
            ctx.deps.db,
            ctx.deps.gateway,
            ctx.actor,
            UUID(ctx.input["application_id"]),
            UUID(doc_id),
            call_model=ctx.model,
            overwrite=ctx.input.get("overwrite", False),
            base_revision=ctx.position.get("base", {}).get(doc_id),
            instruction=ctx.input.get("instruction"),
            avoid=avoid,
            idempotency_key=ctx.effect_key(f"doc:{doc_id}"),
        )
    except NotFound as exc:
        raise StepFailed("target_not_found") from exc
    except DocumentRuleError as exc:
        raise StepFailed(exc.code) from exc
    if out.main_experience_id and out.main_experience_id not in avoid:
        avoid.append(out.main_experience_id)
    written = out.status == draft_uc.WRITTEN
    position = {
        **ctx.position,
        "next": i + 1,
        "avoid": avoid,
        "results": [
            *ctx.position.get("results", []),
            {
                "document_id": doc_id,
                "status": out.status,
                "revision_no": out.revision_no,
                "unconfirmed": out.unconfirmed_sentences,
                "experiences": out.experience_ids,
                "dropped_sentences": out.dropped_sentences,
                "trimmed_sentences": out.trimmed_sentences,
            },
        ],
        "written": ctx.position.get("written", 0) + (1 if written else 0),
        "skipped": ctx.position.get("skipped", 0) + (0 if written else 1),
        "unconfirmed": ctx.position.get("unconfirmed", 0) + out.unconfirmed_sentences,
    }
    if i + 1 >= len(ids):
        return StepResult(position=position, done=True, result=_result(position))
    return StepResult(position=position, repeat=True)


def _result(position: dict[str, Any]) -> dict[str, Any]:
    return {
        "documents": len(position.get("documents", [])),
        "written": position.get("written", 0),
        "skipped": position.get("skipped", 0),
        "unconfirmed": position.get("unconfirmed", 0),
        "results": position.get("results", []),
    }


DRAFT_APPLICATION = Workflow(
    name=draft_uc.DRAFT_WORKFLOW,
    action="documents.ai",
    steps=(_plan, _draft_next),
    validate=_validate,
    input_sizes=input_sizes,
    description=(
        "Draft application answers question by question from the user's own facts "
        "(confirmed first; unconfirmed ones are marked). Never overwrites written text unless asked."
    ),
)

__all__ = ["DRAFT_APPLICATION"]

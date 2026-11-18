"""기본 실행 workflow (O08). 각 단계는 HTTP·MCP와 같은 usecase를 부른다.

단계는 재개될 수 있다. 단계 도중 worker가 죽으면 같은 단계를 처음부터 다시 하므로
부수효과는 멱등이어야 한다:

- ``save_job_urls``: URL 하나씩 저장한다. 같은 공고(정규화 URL)는 기존 항목을 돌려주므로 다시 해도
  공고가 늘지 않는다 (§17.3).
- ``submit_application``: 제출 승인을 요청하고(멱등 키 = 실행 ID) 사람이 결정할 때까지 멈춘다.
  승인되면 그 승인으로 제출한다. 이미 제출됐으면 그 제출본을 결과로 쓴다.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from career_core.domain.documents import DocumentRuleError
from career_core.domain.runs import ApprovalError, ApprovalStatus, RunRuleError
from career_core.usecases import applications as app_uc
from career_core.usecases import approvals as approval_uc
from career_core.usecases import postings as posting_uc
from career_core.usecases.brain import NotFound
from career_core.usecases.runs import StepContext, StepFailed, StepResult, Workflow

MAX_URLS = 50
MAX_URL_CHARS = 2000


# ---------------------------------------------------------------- save_job_urls


def _validate_urls(raw: dict[str, Any]) -> dict[str, Any]:
    urls = raw.get("urls")
    if not isinstance(urls, list) or not urls or len(urls) > MAX_URLS:
        raise RunRuleError("invalid_input")
    if not all(isinstance(u, str) and 0 < len(u.strip()) <= MAX_URL_CHARS for u in urls):
        raise RunRuleError("invalid_input")
    return {"urls": [u.strip() for u in urls]}


async def _save_next_url(ctx: StepContext) -> StepResult:
    urls: list[str] = ctx.input["urls"]
    i = int(ctx.position.get("next", 0))
    results = list(ctx.position.get("results", []))
    if i >= len(urls):
        return StepResult(done=True, result={"saved": results})
    [r] = await posting_uc.save_urls(ctx.deps.db, ctx.actor, [urls[i]])
    results.append(
        {
            "url": r.url,
            "posting_id": str(r.posting_id) if r.posting_id else None,
            "deduplicated": r.deduplicated,
            "error": r.error,
        }
    )
    position = {"next": i + 1, "results": results}
    if i + 1 >= len(urls):
        return StepResult(position=position, done=True, result={"saved": results})
    return StepResult(position=position, repeat=True)


SAVE_JOB_URLS = Workflow(
    name="save_job_urls",
    action="jobs.write",
    steps=(_save_next_url,),
    validate=_validate_urls,
    description="Save job posting URLs one by one (duplicates return the existing posting).",
)


# ---------------------------------------------------------------- submit_application


def _validate_submit(raw: dict[str, Any]) -> dict[str, Any]:
    try:
        application_id = str(UUID(str(raw.get("application_id"))))
    except ValueError as exc:
        raise RunRuleError("invalid_input") from exc
    try:
        payload = app_uc.submission_payload(
            {
                "revisions": raw.get("revisions"),
                "note": raw.get("note"),
                # c0012: 민감 정보 경고를 확인한 지문 (있을 때만)
                **({"sensitive_ack": raw["sensitive_ack"]} if raw.get("sensitive_ack") else {}),
            }
        )
    except ApprovalError as exc:
        raise RunRuleError("invalid_input") from exc
    return {"application_id": application_id, **payload}


async def _request_submit_approval(ctx: StepContext) -> StepResult:
    try:
        view = await approval_uc.request_approval(
            ctx.deps.db,
            ctx.actor,
            action=app_uc.SUBMIT_ACTION,
            target_id=UUID(ctx.input["application_id"]),
            payload={
                "revisions": ctx.input["revisions"],
                "note": ctx.input["note"],
                **(
                    {"sensitive_ack": ctx.input["sensitive_ack"]}
                    if ctx.input.get("sensitive_ack")
                    else {}
                ),
            },
            reason="submit_application run",
            idempotency_key=ctx.effect_key("approval"),
            run_id=ctx.run_id,
            permissions=ctx.deps.permissions,
        )
    except NotFound as exc:
        raise StepFailed("target_not_found") from exc
    except ApprovalError as exc:
        raise StepFailed(exc.code) from exc
    if view.status == ApprovalStatus.APPROVED:
        # 재개 후 이미 결정된 승인 (멱등 키로 같은 요청을 돌려받음)
        return StepResult(position={"approval_id": str(view.id)})
    if view.status != ApprovalStatus.PENDING:
        raise StepFailed(f"approval_{view.status}")
    return StepResult(position={"approval_id": str(view.id)}, wait_approval=view.id)


async def _submit_with_approval(ctx: StepContext) -> StepResult:
    approval_id = UUID(ctx.position["approval_id"])
    application_id = UUID(ctx.input["application_id"])
    view = await approval_uc.get_approval(ctx.deps.db, ctx.actor, approval_id)
    if view.status == ApprovalStatus.USED and view.used_result_id is not None:
        # 앞선 시도가 제출까지 하고 위치 저장 전에 멈췄다: 같은 제출본을 결과로 쓴다
        return StepResult(done=True, result={"submission_id": str(view.used_result_id)})
    if view.status != ApprovalStatus.APPROVED:
        raise StepFailed(f"approval_{view.status}")
    try:
        sub = await app_uc.submit_application(
            ctx.deps.db,
            ctx.actor,
            application_id,
            revisions={UUID(k): v for k, v in ctx.input["revisions"].items()},
            note=ctx.input["note"] or None,
            approval_id=approval_id,
            sensitive_ack=ctx.input.get("sensitive_ack"),
        )
    except ApprovalError as exc:
        raise StepFailed(exc.code) from exc
    except DocumentRuleError as exc:
        raise StepFailed(exc.code) from exc
    except NotFound as exc:
        raise StepFailed("target_not_found") from exc
    return StepResult(done=True, result={"submission_id": str(sub.id)})


SUBMIT_APPLICATION = Workflow(
    name="submit_application",
    action="applications.write",
    steps=(_request_submit_approval, _submit_with_approval),
    validate=_validate_submit,
    description="Request a human approval for a submission, wait for it, then submit.",
)


WORKFLOWS: dict[str, Workflow] = {w.name: w for w in (SAVE_JOB_URLS, SUBMIT_APPLICATION)}

# c0012 문서 수정안 일괄 (workflows/documents.py)
from career_core.workflows.documents import SUGGEST_DOCUMENT_BATCH

WORKFLOWS[SUGGEST_DOCUMENT_BATCH.name] = SUGGEST_DOCUMENT_BATCH

# c0016 AI 인터뷰 질문 묶음 (workflows/interviews.py)
from career_core.workflows.interviews import INTERVIEW_QUESTION_BATCH

WORKFLOWS[INTERVIEW_QUESTION_BATCH.name] = INTERVIEW_QUESTION_BATCH

# c0051 지원서 초안 (workflows/drafts.py, plan §22)
from career_core.workflows.drafts import DRAFT_APPLICATION

WORKFLOWS[DRAFT_APPLICATION.name] = DRAFT_APPLICATION

__all__ = ["SAVE_JOB_URLS", "SUBMIT_APPLICATION", "WORKFLOWS"]

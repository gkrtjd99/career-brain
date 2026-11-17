"""이력서 줄 다듬기 (O17, plan §19.5): 문서 수정안과 같은 경로 (용도 ``doc_suggest``).

모델로 보내는 것은 줄 글과 그 줄의 확인한 사실(restricted 원본 근거는 빼고)뿐이다. 프로필(연락처·생년월일·주소·
병역)은 이 모듈이 읽지 않는다 — 모델 경로는 restricted 프로필을 읽지 않는다는 규칙(``test_brain_me``)과 같다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from career_core.db import Database, Experience
from career_core.db.models import Application
from career_core.domain.documents import normalize_text
from career_core.domain.resume import ResumeConflict, ResumeRuleError, iter_lines
from career_core.llm import ChatMessage, ModelRequest
from career_core.llm.gateway import ModelGateway
from career_core.ports import ActorContext
from career_core.usecases import applications as app_uc
from career_core.usecases import resumes
from career_core.usecases.data_classes import claim_data_classes, restricted_claims
from career_core.usecases.extraction import parse_model_json

MAX_SUGGEST_LINES = resumes.MAX_SUGGEST_LINES
MAX_INSTRUCTION_CHARS = resumes.MAX_INSTRUCTION_CHARS


@dataclass(frozen=True, slots=True)
class LineSuggestion:
    line_id: str
    original_text: str
    suggested_text: str
    reason: str | None
    status: str  # proposed | rejected
    reject_reasons: list[dict]


@dataclass(frozen=True, slots=True)
class LineSuggestResult:
    revision: int
    suggestions: list[LineSuggestion]
    questions: list[str]
    dropped: int
    model_call_id: UUID | None


async def suggest_lines(
    db: Database,
    gateway: ModelGateway | None,
    actor: ActorContext,
    version_id: UUID,
    *,
    base_revision: int,
    line_ids: Sequence[str] | None = None,
    instruction: str | None = None,
) -> LineSuggestResult:
    """사람이 요청한 줄의 최소 수정안 (용도 ``doc_suggest``, 문서 수정안과 같은 지시문·같은 사실 대조).

    근거 사실에 없는 수치·역할·조직·기술을 넣은 수정안은 ``rejected``(사유와 함께). restricted 원본의 근거는
    보내지 않는다. 수정안은 저장하지 않는다 — 사람이 적용(``update_line``, origin ai)해야 새 revision이 된다."""
    async with db.workspace_session(actor.workspace_id) as s:
        v = await resumes._version(s, version_id)
        if base_revision != v.current_revision:
            raise ResumeConflict(v.current_revision)
        rev, content = await resumes._content(s, v)
        if rev is None:
            raise ResumeRuleError("nothing_to_suggest")
        lines = {ln["id"]: (it, ln) for _, it, ln in iter_lines(content)}
        wanted = list(dict.fromkeys(line_ids or lines))[:MAX_SUGGEST_LINES]
        if set(wanted) - set(lines):
            raise ResumeRuleError("unknown_line")
        app = await s.get(Application, v.application_id) if v.application_id else None
        blocks = [
            {
                "id": lid,
                "text": lines[lid][1]["text"],
                "claim_ids": [lines[lid][1]["claim_id"]] if lines[lid][1].get("claim_id") else [],
            }
            for lid in wanted
        ]
        inputs = {b.id: b for b in await app_uc._block_inputs(s, blocks)}
        ctx, _ = await app_uc._check_context(s, app)
        orgs = (await s.execute(select(Experience.organization))).scalars()
        known_orgs = sorted({o for o in orgs if o} | set(ctx.other_companies) | {ctx.company or ""})
        sent = {c.id for b in inputs.values() for c in b.claims if c.confirmed}
        blocked = await restricted_claims(s, sent)
        data_classes = await claim_data_classes(s, sent - blocked)
    evidence = {
        bid: [c.text for c in b.claims if c.confirmed and c.id not in blocked]
        for bid, b in inputs.items()
    }
    data = {
        "document": "resume",
        "company": ctx.company,
        "job_title": v.target_role or ctx.job_title,
        "paragraphs": [
            {"block_id": b["id"], "text": b["text"], "evidence": evidence[b["id"]]} for b in blocks
        ],
        "user_request": (instruction or "").strip()[:MAX_INSTRUCTION_CHARS] or None,
    }
    if gateway is None:
        raise ResumeRuleError("models_not_configured")
    request = ModelRequest(
        messages=(ChatMessage("user", f"<data>\n{json.dumps(data, ensure_ascii=False)}\n</data>"),),
        system=app_uc.SUGGEST_SYSTEM_PROMPT,
        max_output_tokens=3000,
        data_classes=data_classes,
    )
    result = await gateway.run(actor, app_uc.SUGGEST_PURPOSE, request)
    try:
        parsed = app_uc._SuggestOut.model_validate(parse_model_json(result.text))
    except (ValueError, ValidationError) as exc:
        raise ResumeRuleError("invalid_model_output") from exc
    by_id = {b["id"]: b for b in blocks}
    out: list[LineSuggestion] = []
    dropped = 0
    seen: set[str] = set()
    for item in parsed.suggestions[: MAX_SUGGEST_LINES * 2]:
        block = by_id.get(item.block_id)
        text = " ".join(normalize_text(item.text).split())
        if block is None or item.block_id in seen or not text or text == block["text"]:
            dropped += 1
            continue
        seen.add(item.block_id)
        reasons = app_uc.review_suggestion(
            block["text"], text, evidence[block["id"]], known_orgs, ctx.aliases
        )
        out.append(
            LineSuggestion(
                line_id=block["id"],
                original_text=block["text"],
                suggested_text=text,
                reason=(item.reason or "").strip() or None,
                status="rejected" if reasons else "proposed",
                reject_reasons=reasons,
            )
        )
    questions = [q.strip()[:500] for q in parsed.questions if q and q.strip()][:10]
    return LineSuggestResult(base_revision, out, questions, dropped, UUID(result.call_id))


__all__ = ["LineSuggestResult", "LineSuggestion", "suggest_lines"]

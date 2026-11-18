"""공간 공용 설정 경로 (c0021): 표시 시간대·기술명 별칭. c0052: 넣은 자료 바로 정리(``auto_extract``).

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from career_core.domain.korean import MAX_ALIAS_ENTRIES
from career_core.ports import ActorContext
from career_core.usecases import workspace_settings as uc


class AliasEntryIO(BaseModel):
    term: str
    aliases: list[str] = Field(default_factory=list)


class WorkspaceSettingsIn(BaseModel):
    # 빠진 칸은 그대로 둔다. timezone: null → 기본값(Asia/Seoul)으로, tech_aliases: [] → 사용자 별칭 비우기
    timezone: str | None = Field(default=None, max_length=64)
    tech_aliases: list[AliasEntryIO] | None = Field(default=None, max_length=MAX_ALIAS_ENTRIES)
    # c0052: 넣은 내 경험 자료를 바로 AI로 정리 (null은 바꾸지 않음)
    auto_extract: bool | None = None


class WorkspaceSettingsOut(BaseModel):
    timezone: str
    timezone_is_default: bool
    today: date
    day_ends_at: datetime
    tech_aliases: list[AliasEntryIO]
    builtin_aliases: list[AliasEntryIO]
    auto_extract: bool


def add_settings_routes(router: APIRouter, deps, authorize) -> None:
    reader = Annotated[ActorContext, Depends(authorize("settings.read"))]
    writer = Annotated[ActorContext, Depends(authorize("settings.write"))]

    def _out(view: uc.WorkspaceSettingsView) -> WorkspaceSettingsOut:
        return WorkspaceSettingsOut.model_validate(view, from_attributes=True)

    @router.get("/workspace-settings", response_model=WorkspaceSettingsOut)
    async def get_workspace_settings(actor: reader) -> WorkspaceSettingsOut:
        return _out(await uc.get_settings(deps.db, actor))

    @router.put("/workspace-settings", response_model=WorkspaceSettingsOut)
    async def put_workspace_settings(
        actor: writer, body: WorkspaceSettingsIn
    ) -> WorkspaceSettingsOut:
        sent = body.model_fields_set
        changes: dict[str, Any] = {}
        if "timezone" in sent:
            changes["timezone"] = body.timezone
        if "tech_aliases" in sent:
            changes["tech_aliases"] = [e.model_dump() for e in body.tech_aliases or []]
        if "auto_extract" in sent and body.auto_extract is not None:
            changes["auto_extract"] = body.auto_extract
        try:
            view = await uc.update_settings(deps.db, actor, **changes)
        except uc.SettingsError as exc:
            raise HTTPException(status_code=422, detail=exc.code) from exc
        return _out(view)

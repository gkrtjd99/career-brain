"""공간 공용 설정 (c0017): 표시 시간대와 기술명 별칭.

- 표시 시간대: 모든 화면의 날짜 표시, 학습 복습 마감(그 시간대의 하루 끝), 시험 D-day.
  저장하지 않았으면 기본 `Asia/Seoul`. 예전 학습 설정(`/core/study/settings`)도 이 값을 읽고 쓴다.
- 기술명 별칭: 기본 표(`domain.korean.TECH_ALIASES`) 위에 합친다. 문서 검사(요건 가시성·사실 변경)와
  학습 요건 빈도 묶기가 같은 표를 쓴다.

설정은 공간 단위 한 행이다. 없으면 기본값으로 본다 (읽기만 해서는 행을 만들지 않는다).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from career_core.db import Database, WorkspaceSettings
from career_core.db.base import utcnow
from career_core.domain.korean import (
    TECH_ALIASES,
    AliasEntry,
    AliasError,
    TechAliases,
    merge_aliases,
    parse_alias_entries,
)
from career_core.domain.study import DEFAULT_TIMEZONE, StudyRuleError, day_end, local_date, zone
from career_core.ports import ActorContext

_UNSET: Any = object()


class SettingsError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def _row(s: AsyncSession, *, lock: bool = False) -> WorkspaceSettings | None:
    q = select(WorkspaceSettings)
    if lock:
        q = q.with_for_update()
    return await s.scalar(q)


def _entries(row: WorkspaceSettings | None) -> tuple[AliasEntry, ...]:
    """저장된 별칭. 저장 때 검사했으므로 여기서 틀리면 빈 표로 본다 (검사를 멈추지 않는다)."""
    if row is None:
        return ()
    try:
        return parse_alias_entries(row.tech_aliases)
    except AliasError:
        return ()


# ---------------------------------------------------------------- 다른 usecase가 쓰는 조회


async def workspace_timezone_name(s: AsyncSession) -> str | None:
    """저장한 표시 시간대 이름. 없으면 None (기본값은 부르는 쪽이 정한다)."""
    return await s.scalar(select(WorkspaceSettings.timezone))


async def workspace_zone(s: AsyncSession, override: str | None = None) -> ZoneInfo:
    """요청 값 → 공간 설정 → 기본값(Asia/Seoul). 모르는 이름이면 StudyRuleError(invalid_timezone)."""
    if override:
        return zone(override)
    return zone(await workspace_timezone_name(s) or DEFAULT_TIMEZONE)


async def workspace_aliases(s: AsyncSession) -> TechAliases:
    """기본 표 + 공간 사용자 별칭."""
    return merge_aliases(_entries(await _row(s)))


# ---------------------------------------------------------------- 설정 화면


@dataclass(frozen=True, slots=True)
class WorkspaceSettingsView:
    timezone: str
    timezone_is_default: bool  # 저장한 시간대가 없어 기본값을 쓰는 중
    today: date  # 그 시간대의 오늘
    day_ends_at: datetime  # 오늘 끝 (다음 날 0시, UTC)
    tech_aliases: list[dict[str, Any]]  # 사용자 항목 [{term, aliases}]
    builtin_aliases: list[dict[str, Any]]  # 기본 표 (읽기 전용)


def _builtin() -> list[dict[str, Any]]:
    return [{"term": k, "aliases": list(v)} for k, v in TECH_ALIASES.items()]


def _view(row: WorkspaceSettings | None, now: datetime) -> WorkspaceSettingsView:
    name = (row.timezone if row else None) or None
    tz = zone(name or DEFAULT_TIMEZONE)
    return WorkspaceSettingsView(
        timezone=name or DEFAULT_TIMEZONE,
        timezone_is_default=name is None,
        today=local_date(now, tz),
        day_ends_at=day_end(now, tz),
        tech_aliases=[e.as_dict() for e in _entries(row)],
        builtin_aliases=_builtin(),
    )


async def get_settings(
    db: Database, actor: ActorContext, *, now: datetime | None = None
) -> WorkspaceSettingsView:
    async with db.workspace_session(actor.workspace_id) as s:
        row = await _row(s)
    return _view(row, now or utcnow())


async def update_settings(
    db: Database,
    actor: ActorContext,
    *,
    timezone: str | None = _UNSET,
    tech_aliases: list[dict[str, Any]] | None = _UNSET,
    now: datetime | None = None,
) -> WorkspaceSettingsView:
    """준 값만 바꾼다. `timezone=None`은 기본값으로 되돌리기, `tech_aliases=[]`는 사용자 별칭 비우기."""
    changes: dict[str, Any] = {}
    if timezone is not _UNSET:
        name = " ".join((timezone or "").split()) or None
        if name is not None:
            try:
                zone(name)  # 모르는 시간대는 저장하지 않는다
            except StudyRuleError as exc:
                raise SettingsError(exc.code) from exc
        changes["timezone"] = name
    if tech_aliases is not _UNSET:
        try:
            entries = parse_alias_entries(tech_aliases or [])
        except AliasError as exc:
            raise SettingsError(exc.code) from exc
        changes["tech_aliases"] = [e.as_dict() for e in entries]
    async with db.workspace_session(actor.workspace_id) as s:
        row = await _row(s, lock=True)
        if row is None:
            row = WorkspaceSettings(
                id=uuid4(),
                workspace_id=actor.workspace_id,
                timezone=None,
                tech_aliases=[],
                updated_by=actor.subject_id,
            )
            s.add(row)
        for key, value in changes.items():
            setattr(row, key, value)
        row.updated_by = actor.subject_id
        await s.flush()
        view = _view(row, now or utcnow())
    return view


__all__ = [
    "SettingsError",
    "WorkspaceSettingsView",
    "get_settings",
    "update_settings",
    "workspace_aliases",
    "workspace_timezone_name",
    "workspace_zone",
]

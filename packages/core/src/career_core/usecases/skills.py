"""기술 보기 (plan §20 "나 › 기술"): 따로 적는 표가 아니라 브레인에서 모은 것이다.

- 모으는 곳: 경험 태그(``experiences.tags``), 확인한 사실 중 "사용한 기술"(``claims.kind = skill``, 확인된 것만)의
  문장 속 기술명, 자격·이수 기록 중 자격증·어학(확인 전 후보 제외)의 이름.
- 같은 기술은 기술명 별칭 표로 묶는다 (``workspace_settings.tech_aliases`` + 기본 표, c0021 — 문서 검사·학습과 같은
  표). 새 표를 만들지 않는다: "합치기"는 그 표에 사용자 항목을 더하고, "나누기"는 사용자 항목에서 뺀다.
- 기술마다: 쓴 경험(기간), 자격, 마지막으로 쓴 해(진행 중이면 올해), 처음 쓴 해, 수(경험 + 자격). 숙련도 칸은 두지
  않는다 — 근거 없는 자기 평가가 되기 쉽다.
- 모델을 부르지 않는다.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select

from career_core.db.models import Claim, Credential, Experience
from career_core.db.session import Database
from career_core.domain.brain import CONFIRMED_STATUSES
from career_core.domain.experience_match import skill_tags
from career_core.domain.korean import (
    MAX_ALIAS_CHARS,
    TechAliases,
    found_spellings,
    parse_alias_entries,
)
from career_core.ports import ActorContext
from career_core.usecases import workspace_settings as settings_uc
from career_core.usecases.workspace_settings import SettingsError, workspace_aliases

ACTIVE = "active"
# 기술명이 하나도 안 보이는 짧은 "사용한 기술" 문장은 그 문장을 기술 이름으로 본다 ("Figma 프로토타이핑" 등은 낱말로 잡힌다)
SHORT_SKILL_STATEMENT = 30
SKILL_CREDENTIAL_KINDS = ("license", "language")


class SkillRuleError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SkillExperience:
    id: UUID
    title: str
    kind: str
    started_on: date | None
    ended_on: date | None
    ongoing: bool
    via: list[str]  # tag | claim


@dataclass(frozen=True, slots=True)
class SkillCredential:
    id: UUID
    name: str
    kind: str
    acquired_on: date | None


@dataclass(frozen=True, slots=True)
class SkillView:
    key: str  # 비교용 대표 표기 (소문자)
    name: str  # 화면 이름
    spellings: list[str]  # 자료에 나온 표기 (합친 이름 포함)
    merged: list[str]  # 사용자가 이 기술로 합친 표기 (나누기 대상)
    count: int  # 경험 수 + 자격 수
    claims: int  # 이 기술이 나온 확인한 사실 수
    first_year: int | None
    last_year: int | None
    experiences: list[SkillExperience]
    credentials: list[SkillCredential]


@dataclass
class _Group:
    spellings: Counter = field(default_factory=Counter)
    exps: dict[UUID, set[str]] = field(default_factory=dict)
    creds: dict[UUID, SkillCredential] = field(default_factory=dict)
    claims: set[UUID] = field(default_factory=set)


def _key(term: str) -> str:
    return " ".join((term or "").split()).lower()


def _mentions(text: str, spelling: str) -> bool:
    if spelling.isascii():
        pattern = rf"(?<![A-Za-z0-9]){re.escape(spelling)}(?![A-Za-z0-9])"
    else:
        pattern = rf"(?<![가-힣]){re.escape(spelling)}"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _claim_terms(
    statement: str, aliases: TechAliases, extra: dict[str, str]
) -> list[tuple[str, str]]:
    """문장 속 기술명 → [(대표 표기, 원문 표기)]. 표에 있는 기술 + 태그로 쓴 이름(``extra``: key → 표기)."""
    out: list[tuple[str, str]] = []
    for canon in aliases.table:
        found = found_spellings(statement, canon, aliases)
        if found:
            out.append((canon, found[0]))
    seen = {c for c, _ in out}
    for key, spelling in extra.items():
        if key not in seen and _mentions(statement, spelling):
            out.append((key, spelling))
    return out


def _year_range(
    exps: list[SkillExperience], creds: list[SkillCredential], today: date
) -> tuple[int | None, int | None]:
    firsts: list[int] = []
    lasts: list[int] = []
    for e in exps:
        if e.started_on:
            firsts.append(e.started_on.year)
        if e.ongoing:
            lasts.append(today.year)
        elif e.ended_on or e.started_on:
            lasts.append((e.ended_on or e.started_on).year)
    for c in creds:
        if c.acquired_on:
            firsts.append(c.acquired_on.year)
            lasts.append(c.acquired_on.year)
    return (min(firsts) if firsts else None, max(lasts) if lasts else None)


async def skills_overview(
    db: Database, actor: ActorContext, *, today: date | None = None
) -> list[SkillView]:
    today = today or date.today()
    async with db.workspace_session(actor.workspace_id) as s:
        aliases = await workspace_aliases(s)
        exps = (
            await s.execute(
                select(
                    Experience.id,
                    Experience.title,
                    Experience.kind,
                    Experience.started_on,
                    Experience.ended_on,
                    Experience.ongoing,
                    Experience.tags,
                ).where(Experience.status == ACTIVE)
            )
        ).all()
        claims = (
            await s.execute(
                select(Claim.id, Claim.experience_id, Claim.statement)
                .join(Experience, Experience.id == Claim.experience_id)
                .where(
                    Claim.kind == "skill",
                    Claim.status.in_([str(c) for c in CONFIRMED_STATUSES]),
                    Experience.status == ACTIVE,
                )
            )
        ).all()
        creds = (
            await s.execute(
                select(
                    Credential.id, Credential.name, Credential.kind, Credential.acquired_on
                ).where(
                    Credential.kind.in_(SKILL_CREDENTIAL_KINDS),
                    Credential.status != "extracted",
                )
            )
        ).all()

    groups: dict[str, _Group] = {}
    exp_info = {e.id: e for e in exps}

    def add(canon: str, spelling: str) -> _Group:
        g = groups.setdefault(canon, _Group())
        g.spellings[" ".join(spelling.split())] += 1
        return g

    tag_names: dict[str, str] = {}
    # 전공·학위·자격증 이름·경험 제목(프로젝트 이름)인 태그는 기술로 세지 않는다 (옛 추출이 단 태그 포함)
    titles = [e.title for e in exps]
    for e in exps:
        for tag in skill_tags(e.tags or [], titles=titles):
            canon = aliases.canonical(tag)
            add(canon, tag).exps.setdefault(e.id, set()).add("tag")
            if canon not in aliases.table:
                tag_names.setdefault(canon, tag)
    for c in claims:
        terms = _claim_terms(c.statement, aliases, tag_names)
        if not terms and len(c.statement.strip()) <= SHORT_SKILL_STATEMENT:
            terms = [(aliases.canonical(c.statement), c.statement.strip())]
        for canon, spelling in terms:
            g = add(canon, spelling)
            g.exps.setdefault(c.experience_id, set()).add("claim")
            g.claims.add(c.id)
    for c in creds:
        canon = aliases.canonical(c.name)
        add(canon, c.name).creds[c.id] = SkillCredential(c.id, c.name, c.kind, c.acquired_on)

    user = {e.key: e for e in aliases.entries}
    out: list[SkillView] = []
    for canon, g in groups.items():
        entry = user.get(canon)
        if entry is not None:
            name = entry.term
        else:
            # 가장 많이 쓴 표기. 같으면 기준 이름(별칭 표의 키)과 같은 표기, 그다음 가나다순 (조회 순서에 기대지 않는다)
            name = min(
                g.spellings.items(), key=lambda kv: (-kv[1], kv[0].casefold() != canon, kv[0])
            )[0]
        rows = []
        for exp_id, via in g.exps.items():
            e = exp_info.get(exp_id)
            if e is None:
                continue
            rows.append(
                SkillExperience(
                    e.id, e.title, e.kind, e.started_on, e.ended_on, e.ongoing, sorted(via)
                )
            )
        rows.sort(key=lambda x: (x.ongoing, x.ended_on or x.started_on or date.min), reverse=True)
        cred_rows = sorted(g.creds.values(), key=lambda x: x.acquired_on or date.min, reverse=True)
        first, last = _year_range(rows, cred_rows, today)
        out.append(
            SkillView(
                key=canon,
                name=name,
                spellings=sorted(g.spellings, key=lambda sp: (-g.spellings[sp], sp)),
                merged=list(entry.aliases) if entry else [],
                count=len(rows) + len(cred_rows),
                claims=len(g.claims),
                first_year=first,
                last_year=last,
                experiences=rows,
                credentials=cred_rows,
            )
        )
    out.sort(key=lambda v: (-v.count, -(v.last_year or 0), v.name.casefold()))
    return out


# ---------------------------------------------------------------- 합치기·나누기 (기술명 별칭 표)


def _clean(name: str) -> str:
    s = " ".join((name or "").split())
    if not s or len(s) > MAX_ALIAS_CHARS or "\n" in s:
        raise SkillRuleError("invalid_skill_name")
    return s


async def _entries(db: Database, actor: ActorContext) -> tuple[list[dict[str, Any]], TechAliases]:
    view = await settings_uc.get_settings(db, actor)
    async with db.workspace_session(actor.workspace_id) as s:
        aliases = await workspace_aliases(s)
    return [dict(e) for e in view.tech_aliases], aliases


async def _save(db: Database, actor: ActorContext, entries: list[dict[str, Any]]) -> None:
    try:
        parse_alias_entries(entries)
        await settings_uc.update_settings(db, actor, tech_aliases=entries)
    except SettingsError as exc:
        raise SkillRuleError(exc.code) from exc


async def merge_skills(db: Database, actor: ActorContext, into: str, names: list[str]) -> str:
    """``names``를 ``into``와 같은 기술로 본다. 대표 이름(화면 이름)을 돌려준다.

    합친 이름이 다른 사용자 항목의 대표였으면 그 항목의 별칭도 함께 옮긴다. 기본 표의 기술명도 합칠 수 있다
    (그 기술은 기본 표에서 이 기술의 별칭이 된다)."""
    target = _clean(into)
    merging = [_clean(n) for n in names]
    if not merging:
        raise SkillRuleError("nothing_to_merge")
    entries, aliases = await _entries(db, actor)
    key = aliases.canonical(target)
    term = target if _key(target) == key else aliases.display(key)
    mine = next((e for e in entries if _key(e["term"]) == key), None)
    if mine is None:
        mine = {"term": term, "aliases": []}
        entries.append(mine)
    moving: list[str] = []
    for n in merging:
        if aliases.canonical(n) == key:
            continue  # 이미 같은 기술
        moving.append(n)
        nk = _key(n)
        for e in list(entries):
            if e is mine:
                continue
            if _key(e["term"]) == nk:
                moving.extend(e["aliases"])
                entries.remove(e)
            else:
                e["aliases"] = [a for a in e["aliases"] if _key(a) != nk]
    have = {_key(a) for a in mine["aliases"]} | {key}
    for n in moving:
        if _key(n) not in have:
            mine["aliases"].append(n)
            have.add(_key(n))
    await _save(db, actor, entries)
    return mine["term"]


async def split_skill(db: Database, actor: ActorContext, name: str) -> None:
    """사용자가 합친 표기 하나를 따로 둔다. 사용자 항목의 대표 이름이면 그 항목을 지운다 (기본 표는 그대로)."""
    target = _key(_clean(name))
    entries, _ = await _entries(db, actor)
    changed = False
    kept: list[dict[str, Any]] = []
    for e in entries:
        if _key(e["term"]) == target:
            changed = True
            continue
        aliases = [a for a in e["aliases"] if _key(a) != target]
        changed = changed or len(aliases) != len(e["aliases"])
        kept.append({"term": e["term"], "aliases": aliases})
    if not changed:
        raise SkillRuleError("not_merged")
    await _save(db, actor, kept)


__all__ = [
    "SkillCredential",
    "SkillExperience",
    "SkillRuleError",
    "SkillView",
    "merge_skills",
    "skills_overview",
    "split_skill",
]

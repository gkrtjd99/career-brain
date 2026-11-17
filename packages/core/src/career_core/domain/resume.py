"""이력서 판본 본문 (O17, plan §19.5). 순수 코드: 본문 JSON 모양·수정 연산·칸 복사본·블라인드·쪽 수 어림.

이력서는 빈 칸을 새로 채우는 편집기가 아니라 브레인에서 나온다.

- 본문(``resume_revisions.content``)은 구역 목록이다: ``{"v": 1, "sections": [구역, …]}``.
  구역 ``{"kind", "title": null|글, "visible": bool, "items": [항목, …]}`` — 종류마다 하나, 목록 순서가 출력 순서.
- 항목 ``{"id", "ref": {"type", "id"} | null, "fields": {…}, "basis": 해시|null, "lines": [줄, …], "hidden"}``.
  ``ref``는 브레인 기록(경험·학력·자격·기술·프로필)이고 ``fields``는 **채울 때의 값 복사본**이다. 기록이 바뀌어도
  본문은 그대로이고, 읽을 때 지금 값의 해시와 ``basis``를 비교해 "오래됨"을 보인다("다시 채우기"는 사람이 누른다).
  기록에서 온 칸은 판본에서 고치지 않는다 — 브레인 기록을 고치고 다시 채운다. ``ref``가 없는 항목은 사람이 쓴 항목.
- 줄 ``{"id", "text", "claim_id": ID|null, "basis": 해시|null, "origin": "claim"|"user"|"ai"}``. 확인한 사실에서
  고른 줄은 그 사실을 근거로 계속 가리킨다. 사실의 문장이 바뀌면 "오래됨", 확인이 풀리면 "근거 사라짐"으로 보이고
  줄 글은 저절로 바뀌지 않는다. ``claim_id``가 없는 줄은 "근거 없음"이다 (직접 쓴 줄도 허용한다).
- 모델은 글을 쓰지 않는다. 줄 다듬기는 문서 수정안 경로(§11)로 사람이 요청할 때만이고, 적용한 줄은 ``origin = ai``.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from career_core.domain.documents import normalize_text

CONTENT_VERSION = 1


class ResumeRuleError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ResumeConflict(ResumeRuleError):
    """base_revision이 최신이 아니다. 덮어쓰지 않는다 (HTTP 409)."""

    def __init__(self, current: int) -> None:
        super().__init__("revision_conflict")
        self.current = current


# ---------------------------------------------------------------- 판본·구역

VERSION_KINDS = ("master", "role", "posting")
# 위 판본으로 고를 수 있는 종류 (마스터 → 직무 → 공고)
PARENT_KINDS: dict[str, tuple[str, ...]] = {
    "master": (),
    "role": ("master",),
    "posting": ("master", "role"),
}
TEMPLATES = ("one_column", "two_column", "korean_table")
REVISION_ORIGINS = ("user", "agent", "copy", "ai")
LINE_ORIGINS = ("claim", "user", "ai")

SECTION_KINDS = (
    "profile",
    "work",
    "education",
    "project",
    "skills",
    "credentials",
    "awards",
    "publications",
    "activities",
)
SECTION_TITLE: dict[str, str] = {
    "profile": "프로필",
    "work": "경력",
    "education": "학력·교육",
    "project": "프로젝트",
    "skills": "기술",
    "credentials": "자격·수료",
    "awards": "수상",
    "publications": "논문·발표",
    "activities": "대외활동",
}
# 비어 있으면 체크리스트에 뜨는 구역. 경력은 프로젝트가 있으면 채운 것으로 본다 (신입)
REQUIRED_SECTIONS = ("profile", "work", "education")
REF_TYPES = ("experience", "education", "credential", "skill", "profile")
# 구역마다 받는 항목 (None = 사람이 쓴 항목)
ALLOWED_REFS: dict[str, frozenset[str | None]] = {
    "profile": frozenset({"profile"}),
    "work": frozenset({"experience", None}),
    "project": frozenset({"experience", None}),
    "activities": frozenset({"experience", None}),
    "publications": frozenset({"experience", None}),
    "education": frozenset({"education", "experience", None}),
    "skills": frozenset({"skill", None}),
    "credentials": frozenset({"credential", None}),
    "awards": frozenset({"credential", None}),
}
# 줄(성과·내용)을 두지 않는 구역
NO_LINES = frozenset({"profile", "skills"})
# 경험 종류 → 채울 구역 (브레인에서 한 번에 채우기)
EXPERIENCE_SECTION: dict[str, str] = {
    "work": "work",
    "project": "project",
    "education": "education",
    "activity": "activities",
    "other": "activities",
}
# 자격 종류 → 구역
CREDENTIAL_SECTION: dict[str, str] = {
    "license": "credentials",
    "language": "credentials",
    "course": "credentials",
    "other": "credentials",
    "degree": "credentials",
    "award": "awards",
}

MAX_TITLE = 200
MAX_TARGET_ROLE = 200
MAX_ITEMS = 100
MAX_LINES = 30
MAX_LINE_CHARS = 500
MAX_CONTENT_CHARS = 60_000
MAX_OPERATIONS = 100
MAX_SECTION_TITLE = 40
MANUAL_FIELDS: dict[str, int] = {
    "title": 200,
    "organization": 200,
    "role": 200,
    "period_text": 100,
    "detail": 500,
}
PROFILE_SHOW = ("photo", "birth_date", "address", "name_en", "military")
# 새 프로필 항목의 기본 표시 (생년월일·주소·병역은 기본으로 보이지 않는다)
PROFILE_SHOW_DEFAULT = {
    "photo": True,
    "birth_date": False,
    "address": False,
    "name_en": False,
    "military": False,
}

_ID = re.compile(r"^[a-z][a-z0-9]{6,24}$")


def new_id(prefix: str) -> str:
    return prefix + uuid4().hex[:12]


def _clean_id(value: Any, prefix: str) -> str:
    if value is None:
        return new_id(prefix)
    if not isinstance(value, str) or not _ID.match(value) or not value.startswith(prefix):
        raise ResumeRuleError("invalid_id")
    return value


def clean_title(value: Any, *, limit: int = MAX_TITLE, code: str = "title_required") -> str:
    if not isinstance(value, str):
        raise ResumeRuleError(code)
    text = " ".join(normalize_text(value).split())
    if not text:
        raise ResumeRuleError(code)
    if len(text) > limit:
        raise ResumeRuleError("title_too_long")
    return text


def clean_optional(value: Any, limit: int, code: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ResumeRuleError(code)
    text = " ".join(normalize_text(value).split())
    if len(text) > limit:
        raise ResumeRuleError(code)
    return text or None


def line_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ResumeRuleError("invalid_text")
    text = " ".join(normalize_text(value).split())
    if not text:
        raise ResumeRuleError("empty_line")
    if len(text) > MAX_LINE_CHARS:
        raise ResumeRuleError("line_too_long")
    return text


def empty_content() -> dict[str, Any]:
    return {
        "v": CONTENT_VERSION,
        "sections": [
            {"kind": k, "title": None, "visible": True, "items": []} for k in SECTION_KINDS
        ],
    }


def _canon(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(content: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canon(content).encode("utf-8")).hexdigest()


def basis_hash(fields: Mapping[str, Any]) -> str:
    """기록 값의 해시 (채울 때 저장하고 읽을 때 지금 값과 비교한다). ID 칸(``*_id``)은 넣지 않는다 — 새 ID로
    복원한 사본에서도 같은 값이면 같은 해시여야 하기 때문이다 (사진을 바꾼 것은 "오래됨"으로 보지 않는다)."""
    plain = {k: v for k, v in fields.items() if not k.endswith("_id")}
    return hashlib.sha256(_canon(plain).encode("utf-8")).hexdigest()[:16]


def claim_basis(statement: str, experience_id: str) -> str:
    """사실 문장의 해시. 어느 경험의 사실인지는 줄을 넣을 때 따로 본다 (ID는 해시에 넣지 않는다)."""
    del experience_id
    return basis_hash({"statement": statement})


# ---------------------------------------------------------------- 기록 → 칸 복사본


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def experience_fields(e: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "title": e["title"],
        "organization": e.get("organization"),
        "role": e.get("role"),
        "kind": e.get("kind") or "other",
        "started_on": _iso(e.get("started_on")),
        "ended_on": _iso(e.get("ended_on")),
        "ongoing": bool(e.get("ongoing")),
        "period_text": e.get("period_text"),
        "tags": list(e.get("tags") or []),
    }


def education_fields(e: Mapping[str, Any]) -> dict[str, Any]:
    def num(v: Any) -> str | None:
        return None if v is None else f"{float(v):g}"

    return {
        "school": e["school"],
        "campus": e.get("campus"),
        "major": e.get("major"),
        "extra_major": e.get("extra_major"),
        "extra_major_kind": e.get("extra_major_kind"),
        "degree": e["degree"],
        "status": e["status"],
        "transferred": bool(e.get("transferred")),
        "entered_on": _iso(e.get("entered_on")),
        "graduated_on": _iso(e.get("graduated_on")),
        "gpa": num(e.get("gpa")),
        "gpa_scale": num(e.get("gpa_scale")),
    }


def credential_fields(c: Mapping[str, Any]) -> dict[str, Any]:
    # 번호는 넣지 않는다 (화면에서도 가리는 값)
    return {
        "kind": c["kind"],
        "name": c["name"],
        "issuer": c.get("issuer"),
        "acquired_on": _iso(c.get("acquired_on")),
        "expires_on": _iso(c.get("expires_on")),
        "started_on": _iso(c.get("started_on")),
        "ended_on": _iso(c.get("ended_on")),
        "score": c.get("score"),
        "hours": c.get("hours"),
    }


def skill_fields(name: str) -> dict[str, Any]:
    return {"name": name}


MILITARY_LABEL = {
    "served": "군필",
    "serving": "복무 중",
    "not_served": "미필",
    "exempt": "면제",
    "not_applicable": "비대상",
}


def profile_fields(p: Mapping[str, Any], photo_id: str | None) -> dict[str, Any]:
    """프로필 복사본. 병역은 한 줄 요약만 (보훈·장애·희망 조건은 넣지 않는다)."""
    military = None
    if p.get("military_status"):
        parts = [MILITARY_LABEL.get(p["military_status"], p["military_status"])]
        parts += [v for v in (p.get("military_branch"), p.get("military_rank")) if v]
        span = period_text(
            {
                "started_on": _iso(p.get("military_started_on")),
                "ended_on": _iso(p.get("military_ended_on")),
            }
        )
        if span:
            parts.append(span)
        military = " · ".join(parts)
    return {
        "name": p.get("name"),
        "name_en": p.get("name_en"),
        "headline": p.get("headline"),
        "email": p.get("email"),
        "phone": p.get("phone"),
        "address": p.get("address"),
        "links": [
            {"label": lk.get("label"), "url": lk.get("url")}
            for lk in (p.get("links") or [])
            if isinstance(lk, Mapping) and lk.get("url")
        ],
        "birth_date": _iso(p.get("birth_date")),
        "photo_id": photo_id,
        "military": military,
    }


# 연락처로 보는 칸 ("오래된 연락처" 검사)
CONTACT_FIELDS = ("email", "phone", "address")


# ---------------------------------------------------------------- 표시 글

DEGREE_LABEL = {
    "high_school": "고등학교",
    "associate": "전문학사",
    "bachelor": "학사",
    "master": "석사",
    "doctor": "박사",
}
EDU_STATUS_LABEL = {
    "enrolled": "재학",
    "on_leave": "휴학",
    "expected": "졸업 예정",
    "graduated": "졸업",
    "completed": "수료",
    "dropped": "중퇴",
}
CREDENTIAL_KIND_LABEL = {
    "license": "자격증",
    "language": "어학",
    "course": "수료",
    "award": "수상",
    "degree": "학력",
    "other": "기타",
}


def month(value: str | None) -> str | None:
    """``YYYY-MM-DD`` → ``YYYY.MM``"""
    if not value:
        return None
    return f"{value[:4]}.{value[5:7]}"


def day(value: str | None) -> str | None:
    if not value:
        return None
    return f"{value[:4]}.{value[5:7]}.{value[8:10]}"


def period_text(f: Mapping[str, Any]) -> str | None:
    start = month(f.get("started_on") or f.get("entered_on"))
    end = month(f.get("ended_on") or f.get("graduated_on"))
    if f.get("ongoing") and start:
        return f"{start} – 진행 중"
    if start and end:
        return start if start == end else f"{start} – {end}"
    if start:
        return f"{start} –"
    if end:
        return f"– {end}"
    return f.get("period_text") or None


# ---------------------------------------------------------------- 본문 검사 (저장 전)


def _section(content: Mapping[str, Any], kind: str) -> dict[str, Any]:
    for s in content["sections"]:
        if s["kind"] == kind:
            return s
    raise ResumeRuleError("unknown_section")


def iter_items(content: Mapping[str, Any]):
    for s in content["sections"]:
        for it in s["items"]:
            yield s, it


def iter_lines(content: Mapping[str, Any]):
    for s, it in iter_items(content):
        for ln in it.get("lines") or []:
            yield s, it, ln


def check_content(content: Any) -> dict[str, Any]:
    """저장할 본문의 모양을 확인한다 (연산 결과·복원). 고치지 않고 틀리면 거부한다."""
    if not isinstance(content, dict) or content.get("v") != CONTENT_VERSION:
        raise ResumeRuleError("invalid_content")
    sections = content.get("sections")
    if not isinstance(sections, list) or sorted(s.get("kind") for s in sections) != sorted(
        SECTION_KINDS
    ):
        raise ResumeRuleError("invalid_content")
    item_ids: set[str] = set()
    line_ids: set[str] = set()
    refs: set[tuple[str, str]] = set()
    chars = 0
    for s in sections:
        if not isinstance(s.get("items"), list) or len(s["items"]) > MAX_ITEMS:
            raise ResumeRuleError("too_many_items")
        if s["kind"] == "profile" and len(s["items"]) > 1:
            raise ResumeRuleError("one_profile")
        for it in s["items"]:
            if it["id"] in item_ids:
                raise ResumeRuleError("invalid_content")
            item_ids.add(it["id"])
            ref = it.get("ref")
            rtype = ref["type"] if ref else None
            if rtype not in ALLOWED_REFS[s["kind"]]:
                raise ResumeRuleError("ref_not_allowed")
            if ref:
                key = (ref["type"], ref["id"])
                if key in refs:
                    raise ResumeRuleError("already_added")
                refs.add(key)
            lines = it.get("lines") or []
            if len(lines) > MAX_LINES:
                raise ResumeRuleError("too_many_lines")
            if lines and s["kind"] in NO_LINES:
                raise ResumeRuleError("lines_not_allowed")
            for ln in lines:
                if ln["id"] in line_ids:
                    raise ResumeRuleError("invalid_content")
                line_ids.add(ln["id"])
                if ln.get("origin") not in LINE_ORIGINS:
                    raise ResumeRuleError("invalid_content")
                chars += len(ln["text"])
            chars += sum(len(str(v)) for v in (it.get("fields") or {}).values() if v)
    if chars > MAX_CONTENT_CHARS:
        raise ResumeRuleError("content_too_long")
    return content


# ---------------------------------------------------------------- 수정 연산


@dataclass(frozen=True, slots=True)
class ClaimInfo:
    id: str
    statement: str
    confirmed: bool
    experience_id: str

    @property
    def basis(self) -> str:
        return claim_basis(self.statement, self.experience_id)


@dataclass(slots=True)
class Lookup:
    """연산이 쓰는 브레인 값 (usecase가 DB에서 미리 읽어 둔다).

    ``records``: (종류, ID) → 지금 칸 값. 없으면 그 기록이 이 공간에 없다.
    ``claims``: 주장 ID → 정보. ``family``: 경험 ID → 그 경험과 바로 아래 경험 ID들 (성과 줄로 고를 수 있는 범위).
    """

    records: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    claims: dict[str, ClaimInfo] = field(default_factory=dict)
    family: dict[str, set[str]] = field(default_factory=dict)


def _find_item(content: dict[str, Any], item_id: Any) -> tuple[dict, dict]:
    for s in content["sections"]:
        for it in s["items"]:
            if it["id"] == item_id:
                return s, it
    raise ResumeRuleError("unknown_item")


def _find_line(content: dict[str, Any], line_id: Any) -> tuple[dict, dict, dict]:
    for s, it, ln in iter_lines(content):
        if ln["id"] == line_id:
            return s, it, ln
    raise ResumeRuleError("unknown_line")


def _index(value: Any, size: int) -> int:
    if value is None:
        return size
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= size:
        raise ResumeRuleError("invalid_index")
    return value


def _manual_fields(value: Any, *, partial: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - set(MANUAL_FIELDS):
        raise ResumeRuleError("invalid_fields")
    out = dict(partial or {})
    for k, limit in MANUAL_FIELDS.items():
        if k in value:
            out[k] = clean_optional(value[k], limit, "invalid_fields")
    if not out.get("title"):
        raise ResumeRuleError("title_required")
    return out


def _claim_line(info: ClaimInfo, line_id: Any = None) -> dict[str, Any]:
    return {
        "id": _clean_id(line_id, "l"),
        "text": line_text(info.statement),
        "claim_id": info.id,
        "basis": info.basis,
        "origin": "claim",
    }


def _check_claim(lookup: Lookup, item: dict[str, Any], claim_id: Any) -> ClaimInfo:
    try:
        cid = str(UUID(str(claim_id)))
    except ValueError as exc:
        raise ResumeRuleError("unknown_claim") from exc
    info = lookup.claims.get(cid)
    if info is None:
        raise ResumeRuleError("unknown_claim")
    if not info.confirmed:
        raise ResumeRuleError("claim_not_confirmed")
    ref = item.get("ref")
    if not ref or ref["type"] != "experience":
        raise ResumeRuleError("claim_needs_experience")
    if info.experience_id not in lookup.family.get(ref["id"], {ref["id"]}):
        raise ResumeRuleError("claim_not_in_experience")
    return info


def apply_operations(
    content: Mapping[str, Any],
    operations: Sequence[Mapping[str, Any]],
    lookup: Lookup,
    *,
    line_origin: str = "user",
) -> dict[str, Any]:
    """연산을 차례로 적용한 새 본문. 원본은 바꾸지 않는다. ``line_origin``: 사람이 쓴 줄은 user, 에이전트는 ai.

    - ``add_item {section, ref?: {type, id}, fields?, claim_ids?, index?, item_id?}``: ref가 있으면 그 기록의 지금 값을
      복사해 채운다(``fields``는 받지 않는다). ``claim_ids``는 그 경험의 확인한 사실 → 줄. ref가 없으면 사람이 쓴 항목.
    - ``update_item {item_id, fields?, show?, hidden?}``: ``fields``는 사람이 쓴 항목만, ``show``는 프로필만.
    - ``remove_item {item_id}`` · ``move_item {item_id, index, section?}`` · ``order_items {section, item_ids}``
    - ``refill_item {item_id}``: 기록의 지금 값으로 다시 채운다. 줄은 그대로.
    - ``add_line {item_id, claim_id?, text?, index?, line_id?}``: claim_id면 그 사실 문장(글을 주면 그 글, 근거는 유지).
    - ``update_line {line_id, text, origin?}`` · ``remove_line`` · ``move_line {line_id, index}``
    - ``refresh_line {line_id}``: 근거 사실의 지금 문장으로 바꾼다 (확인한 사실일 때만).
    - ``move_section {section, index}`` · ``set_section {section, visible?, title?}``
    """
    if not operations:
        raise ResumeRuleError("no_operations")
    if len(operations) > MAX_OPERATIONS:
        raise ResumeRuleError("too_many_operations")
    out: dict[str, Any] = json.loads(json.dumps(content))
    for op in operations:
        if not isinstance(op, Mapping):
            raise ResumeRuleError("unknown_operation")
        kind = op.get("op")
        if kind == "add_item":
            sec = _section(out, op.get("section"))
            ref = op.get("ref")
            if ref is not None:
                if not isinstance(ref, Mapping) or ref.get("type") not in REF_TYPES:
                    raise ResumeRuleError("invalid_ref")
                rtype, rid = ref["type"], str(ref.get("id") or "")
                if rtype not in ALLOWED_REFS[sec["kind"]]:
                    raise ResumeRuleError("ref_not_allowed")
                fields = lookup.records.get((rtype, rid))
                if fields is None:
                    raise ResumeRuleError("unknown_record")
                if op.get("fields") is not None:
                    raise ResumeRuleError("ref_fields_locked")
                item: dict[str, Any] = {
                    "id": _clean_id(op.get("item_id"), "i"),
                    "ref": {"type": rtype, "id": rid},
                    "fields": dict(fields),
                    "basis": basis_hash(fields),
                    "lines": [],
                    "hidden": False,
                }
                if rtype == "profile":
                    item["show"] = dict(PROFILE_SHOW_DEFAULT)
                    if not fields.get("photo_id"):
                        item["show"]["photo"] = False
            else:
                if None not in ALLOWED_REFS[sec["kind"]]:
                    raise ResumeRuleError("ref_required")
                item = {
                    "id": _clean_id(op.get("item_id"), "i"),
                    "ref": None,
                    "fields": _manual_fields(op.get("fields")),
                    "basis": None,
                    "lines": [],
                    "hidden": False,
                }
            for cid in op.get("claim_ids") or []:
                if sec["kind"] in NO_LINES:
                    raise ResumeRuleError("lines_not_allowed")
                info = _check_claim(lookup, item, cid)
                if any(ln["claim_id"] == info.id for ln in item["lines"]):
                    continue
                item["lines"].append(_claim_line(info))
            sec["items"].insert(_index(op.get("index"), len(sec["items"])), item)
        elif kind == "update_item":
            sec, item = _find_item(out, op.get("item_id"))
            if op.get("fields") is not None:
                if item.get("ref"):
                    raise ResumeRuleError("ref_fields_locked")
                item["fields"] = _manual_fields(op["fields"], partial=item["fields"])
            if op.get("show") is not None:
                show = op["show"]
                if sec["kind"] != "profile" or not isinstance(show, Mapping):
                    raise ResumeRuleError("invalid_show")
                if set(show) - set(PROFILE_SHOW) or not all(
                    isinstance(v, bool) for v in show.values()
                ):
                    raise ResumeRuleError("invalid_show")
                item["show"] = {**item.get("show", PROFILE_SHOW_DEFAULT), **show}
            if op.get("hidden") is not None:
                if not isinstance(op["hidden"], bool):
                    raise ResumeRuleError("invalid_hidden")
                item["hidden"] = op["hidden"]
        elif kind == "remove_item":
            sec, item = _find_item(out, op.get("item_id"))
            sec["items"].remove(item)
        elif kind == "move_item":
            sec, item = _find_item(out, op.get("item_id"))
            target = _section(out, op["section"]) if op.get("section") else sec
            rtype = item["ref"]["type"] if item.get("ref") else None
            if rtype not in ALLOWED_REFS[target["kind"]]:
                raise ResumeRuleError("ref_not_allowed")
            if item.get("lines") and target["kind"] in NO_LINES:
                raise ResumeRuleError("lines_not_allowed")
            sec["items"].remove(item)
            target["items"].insert(_index(op.get("index"), len(target["items"])), item)
        elif kind == "order_items":
            sec = _section(out, op.get("section"))
            ids = op.get("item_ids")
            if not isinstance(ids, list):
                raise ResumeRuleError("invalid_order")
            by_id = {it["id"]: it for it in sec["items"]}
            first = [by_id[i] for i in dict.fromkeys(ids) if i in by_id]
            rest = [it for it in sec["items"] if it["id"] not in {x["id"] for x in first}]
            sec["items"] = first + rest
        elif kind == "refill_item":
            _, item = _find_item(out, op.get("item_id"))
            ref = item.get("ref")
            if not ref:
                raise ResumeRuleError("not_from_brain")
            fields = lookup.records.get((ref["type"], ref["id"]))
            if fields is None:
                raise ResumeRuleError("unknown_record")
            item["fields"] = dict(fields)
            item["basis"] = basis_hash(fields)
            if ref["type"] == "profile" and not fields.get("photo_id"):
                item["show"] = {**item.get("show", PROFILE_SHOW_DEFAULT), "photo": False}
        elif kind == "add_line":
            sec, item = _find_item(out, op.get("item_id"))
            if sec["kind"] in NO_LINES:
                raise ResumeRuleError("lines_not_allowed")
            if len(item["lines"]) >= MAX_LINES:
                raise ResumeRuleError("too_many_lines")
            if op.get("claim_id") is not None:
                info = _check_claim(lookup, item, op["claim_id"])
                if any(ln["claim_id"] == info.id for ln in item["lines"]):
                    raise ResumeRuleError("claim_already_used")
                line = _claim_line(info, op.get("line_id"))
                if op.get("text") is not None:
                    text = line_text(op["text"])
                    if text != line["text"]:
                        line = {**line, "text": text, "origin": line_origin}
            else:
                line = {
                    "id": _clean_id(op.get("line_id"), "l"),
                    "text": line_text(op.get("text")),
                    "claim_id": None,
                    "basis": None,
                    "origin": line_origin,
                }
            item["lines"].insert(_index(op.get("index"), len(item["lines"])), line)
        elif kind == "update_line":
            _, _, line = _find_line(out, op.get("line_id"))
            text = line_text(op.get("text"))
            origin = op.get("origin") or line_origin
            if origin not in ("user", "ai"):
                raise ResumeRuleError("invalid_origin")
            if line_origin == "ai":
                origin = "ai"  # 에이전트가 고친 줄은 늘 ai
            if text != line["text"]:
                line["text"] = text
                line["origin"] = origin
        elif kind == "remove_line":
            _, item, line = _find_line(out, op.get("line_id"))
            item["lines"].remove(line)
        elif kind == "move_line":
            _, item, line = _find_line(out, op.get("line_id"))
            item["lines"].remove(line)
            item["lines"].insert(_index(op.get("index"), len(item["lines"])), line)
        elif kind == "refresh_line":
            _, item, line = _find_line(out, op.get("line_id"))
            if not line.get("claim_id"):
                raise ResumeRuleError("no_evidence")
            info = lookup.claims.get(line["claim_id"])
            if info is None or not info.confirmed:
                raise ResumeRuleError("claim_not_confirmed")
            line.update(text=line_text(info.statement), basis=info.basis, origin="claim")
        elif kind == "move_section":
            sec = _section(out, op.get("section"))
            out["sections"].remove(sec)
            out["sections"].insert(_index(op.get("index"), len(out["sections"])), sec)
        elif kind == "set_section":
            sec = _section(out, op.get("section"))
            if op.get("visible") is not None:
                if not isinstance(op["visible"], bool):
                    raise ResumeRuleError("invalid_visible")
                sec["visible"] = op["visible"]
            if "title" in op:
                sec["title"] = clean_optional(op["title"], MAX_SECTION_TITLE, "invalid_title")
        else:
            raise ResumeRuleError("unknown_operation")
    return check_content(out)


def operation_refs(
    content: Mapping[str, Any], operations: Sequence[Mapping[str, Any]]
) -> tuple[set[tuple[str, str]], set[str]]:
    """연산이 읽을 기록·주장 (usecase가 DB에서 미리 읽는다): add_item의 ref·claim_ids, refill_item의 ref,
    add_line의 claim_id, refresh_line의 줄 근거. 연산 안에서 만든 항목도 볼 수 있게 본문 전체의 ref도 넣는다."""
    records: set[tuple[str, str]] = set()
    claims: set[str] = set()
    for _, it in iter_items(content):
        if it.get("ref"):
            records.add((it["ref"]["type"], it["ref"]["id"]))
    for _, _, ln in iter_lines(content):
        if ln.get("claim_id"):
            claims.add(ln["claim_id"])
    for op in operations:
        if not isinstance(op, Mapping):
            continue
        ref = op.get("ref")
        if isinstance(ref, Mapping) and ref.get("type") in REF_TYPES and ref.get("id"):
            records.add((ref["type"], str(ref["id"])))
        for cid in [*(op.get("claim_ids") or []), op.get("claim_id")]:
            if cid:
                claims.add(str(cid))
    return records, claims


# ---------------------------------------------------------------- 읽을 때 상태


def item_state(item: Mapping[str, Any], current: Mapping[str, Any] | None) -> str:
    """manual | ok | stale(기록이 바뀜) | missing(기록이 없음)."""
    if not item.get("ref"):
        return "manual"
    if current is None:
        return "missing"
    return "ok" if basis_hash(current) == item.get("basis") else "stale"


def line_state(line: Mapping[str, Any], info: ClaimInfo | None) -> str:
    """none(근거 없음) | ok | stale(사실 문장이 바뀜) | broken(확인이 풀렸거나 없음)."""
    if not line.get("claim_id"):
        return "none"
    if info is None or not info.confirmed:
        return "broken"
    return "ok" if info.basis == line.get("basis") else "stale"


# ---------------------------------------------------------------- 블라인드

# 블라인드 판본에서 줄 글에 보이면 경고하는 말 (성별·가족·나이). 가리지는 않는다 — 사람이 고친다.
BLIND_WORDS = (
    "성별",
    "남성",
    "여성",
    "가족",
    "부모",
    "아버지",
    "어머니",
    "형제",
    "자매",
    "결혼",
    "기혼",
    "미혼",
    "혼인",
    "생년월일",
    "년생",
    "나이",
    "출신",
)
BLIND_MASK = "○○"


def mentions_blind(text: str | None) -> bool:
    return bool(text) and "블라인드" in text


def birth_strings(birth: str | None) -> list[str]:
    """생년월일 ``YYYY-MM-DD``의 흔한 쓰기와 "OOOO년생" (줄 글에서 찾아 가린다)."""
    if not birth:
        return []
    y, m, d = birth[:4], birth[5:7], birth[8:10]
    return [
        f"{y}-{m}-{d}",
        f"{y}.{m}.{d}",
        f"{y}. {int(m)}. {int(d)}",
        f"{y}/{m}/{d}",
        f"{y}년 {int(m)}월 {int(d)}일",
        f"{y}{m}{d}",
        f"{y}년생",
        f"{y[2:]}년생",
    ]


def redact(text: str, names: Sequence[str]) -> tuple[str, int]:
    """``names``(학교 이름·생년월일 쓰기)를 ○○로. 긴 이름부터."""
    n = 0
    for name in sorted({x for x in names if x and len(x) >= 2}, key=len, reverse=True):
        if name in text:
            n += text.count(name)
            text = text.replace(name, BLIND_MASK)
    return text, n


def blind_words(text: str) -> list[str]:
    return [w for w in BLIND_WORDS if w in text]


# ---------------------------------------------------------------- 출력용 모양


@dataclass(frozen=True, slots=True)
class RenderItem:
    """출력 한 항목. 표 양식의 칸: 기간 | 주 칸 | 둘째 칸 | 셋째 칸 | 줄."""

    period: str | None
    primary: str
    secondary: str | None = None
    tertiary: str | None = None
    lines: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    # 경험 항목의 경험 ID (포트폴리오 항목과 맞추기). 출력에는 쓰지 않는다.
    ref_id: str | None = None
    # 함께 내보낸 포트폴리오 항목으로 가는 상대 링크 (정적 HTML 묶음에서만, 그 밖에는 None)
    href: str | None = None


@dataclass(frozen=True, slots=True)
class RenderSection:
    kind: str
    title: str
    items: tuple[RenderItem, ...]


@dataclass(frozen=True, slots=True)
class RenderProfile:
    name: str | None
    name_en: str | None = None
    headline: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    links: tuple[tuple[str | None, str], ...] = ()
    birth_date: str | None = None
    military: str | None = None
    # (mime, 바이트). 블라인드·표시 끔·파일 없음이면 None
    photo: tuple[str, bytes] | None = None


@dataclass(frozen=True, slots=True)
class RenderDoc:
    title: str
    template: str
    blind: bool
    profile: RenderProfile | None
    sections: tuple[RenderSection, ...]
    target_role: str | None = None


def _degree_line(f: Mapping[str, Any]) -> str:
    parts = [DEGREE_LABEL.get(f.get("degree") or "", f.get("degree") or "")]
    status = EDU_STATUS_LABEL.get(f.get("status") or "")
    if status:
        parts.append(status)
    if f.get("transferred"):
        parts.append("편입")
    return " · ".join(p for p in parts if p)


def _major(f: Mapping[str, Any]) -> str | None:
    major = f.get("major")
    extra = f.get("extra_major")
    if extra:
        kind = {"double": "복수전공", "minor": "부전공"}.get(f.get("extra_major_kind") or "", "")
        major = f"{major or ''} · {extra}{f' ({kind})' if kind else ''}".strip(" ·")
    return major or None


def _gpa(f: Mapping[str, Any]) -> str | None:
    if f.get("gpa") and f.get("gpa_scale"):
        return f"학점 {f['gpa']}/{f['gpa_scale']}"
    return None


def _title_role(f: Mapping[str, Any]) -> str | None:
    """둘째 줄 "제목 · 역할". 조직이 없으면 제목이 첫 줄이라 역할만. 제목과 역할이 같으면 한 번만
    ("백엔드 엔지니어 · 백엔드 엔지니어" → "백엔드 엔지니어"), 첫 줄과 같은 글도 되풀이하지 않는다."""
    primary = f.get("organization") or f.get("title") or ""
    parts: list[str] = []
    for v in (f.get("title") if f.get("organization") else None, f.get("role")):
        v = (v or "").strip()
        if not v:
            continue
        key = " ".join(v.split()).casefold()
        if key == " ".join(primary.split()).casefold() or key in (
            " ".join(p.split()).casefold() for p in parts
        ):
            continue
        parts.append(v)
    return " · ".join(parts) or None


def render_item(
    kind: str,
    item: Mapping[str, Any],
    *,
    blind: bool,
) -> RenderItem | None:
    """구역 종류·항목 → 출력 칸. 블라인드면 학교·캠퍼스, 학력 자격의 이름·발급 기관을 뺀다."""
    f = item.get("fields") or {}
    rtype = item["ref"]["type"] if item.get("ref") else None
    lines = tuple(ln["text"] for ln in item.get("lines") or [])
    if rtype == "experience":
        return RenderItem(
            period=period_text(f),
            primary=f.get("organization") or f.get("title") or "",
            secondary=_title_role(f),
            lines=lines,
            tags=tuple(f.get("tags") or ()),
            ref_id=item["ref"]["id"],
        )
    if rtype == "education":
        school = None if blind else " ".join(v for v in (f.get("school"), f.get("campus")) if v)
        return RenderItem(
            period=period_text(f),
            primary=school or _degree_line(f),
            secondary=_major(f),
            tertiary=" · ".join(v for v in ((_degree_line(f) if school else None), _gpa(f)) if v)
            or None,
            lines=lines,
        )
    if rtype == "credential":
        degree = f.get("kind") == "degree"
        name = f.get("name") or ""
        issuer = f.get("issuer")
        if blind and degree:
            name, issuer = CREDENTIAL_KIND_LABEL["degree"], None
        when = day(f.get("acquired_on")) or period_text(f)
        extra = [f.get("score")] if f.get("score") else []
        if f.get("hours"):
            extra.append(f"{f['hours']}시간")
        return RenderItem(
            period=when,
            primary=name,
            secondary=issuer,
            tertiary=" · ".join(extra) or None,
            lines=lines,
        )
    if rtype == "skill":
        return RenderItem(period=None, primary=f.get("name") or "")
    # 사람이 쓴 항목
    return RenderItem(
        period=f.get("period_text"),
        primary=f.get("organization") or f.get("title") or "",
        secondary=_title_role(f),
        tertiary=f.get("detail"),
        lines=lines,
    )


def build_render(
    *,
    title: str,
    template: str,
    blind: bool,
    content: Mapping[str, Any],
    photo_bytes: Callable[[str], tuple[str, bytes] | None] | None = None,
    redact_names: Sequence[str] = (),
    target_role: str | None = None,
) -> tuple[RenderDoc, int]:
    """본문 → 출력 모양. 숨긴 구역·항목은 뺀다. 블라인드면 사진·생년월일·주소·학교·학력 자격 이름을 빼고
    줄 글의 학교 이름·생년월일 쓰기(``redact_names``)를 ○○로 가린다. 가린 수를 같이 준다."""
    profile: RenderProfile | None = None
    sections: list[RenderSection] = []
    masked = 0
    names = list(redact_names) if blind else []

    def clean(text: str) -> str:
        nonlocal masked
        if not names:
            return text
        text, n = redact(text, names)
        masked += n
        return text

    for sec in content["sections"]:
        if not sec.get("visible", True):
            continue
        items = [it for it in sec["items"] if not it.get("hidden")]
        if sec["kind"] == "profile":
            if not items:
                continue
            f = items[0].get("fields") or {}
            show = {**PROFILE_SHOW_DEFAULT, **(items[0].get("show") or {})}
            photo = None
            if show["photo"] and not blind and f.get("photo_id") and photo_bytes is not None:
                photo = photo_bytes(f["photo_id"])
            profile = RenderProfile(
                name=f.get("name"),
                name_en=f.get("name_en") if show["name_en"] else None,
                headline=clean(f["headline"]) if f.get("headline") else None,
                email=f.get("email"),
                phone=f.get("phone"),
                address=None if blind or not show["address"] else f.get("address"),
                links=tuple((lk.get("label"), lk["url"]) for lk in f.get("links") or []),
                birth_date=None if blind or not show["birth_date"] else day(f.get("birth_date")),
                military=f.get("military") if show["military"] else None,
                photo=photo,
            )
            continue
        out: list[RenderItem] = []
        for it in items:
            r = render_item(sec["kind"], it, blind=blind)
            if r is None:
                continue
            if names:
                r = RenderItem(
                    period=r.period,
                    primary=clean(r.primary),
                    secondary=clean(r.secondary) if r.secondary else None,
                    tertiary=clean(r.tertiary) if r.tertiary else None,
                    lines=tuple(clean(x) for x in r.lines),
                    tags=r.tags,
                    ref_id=r.ref_id,
                )
            out.append(r)
        if out:
            sections.append(
                RenderSection(
                    sec["kind"], sec.get("title") or SECTION_TITLE[sec["kind"]], tuple(out)
                )
            )
    return (
        RenderDoc(
            title=title,
            template=template,
            blind=blind,
            profile=profile,
            sections=tuple(sections),
            target_role=target_role,
        ),
        masked,
    )


# 쪽 수 어림 (서버 검사·MCP용). 화면은 미리보기에서 실제로 잰 쪽 수를 쓴다 (``PAGINATE_JS``의 ``data-pages``).
#
# 인쇄용 HTML(``domain.resume_export._CSS``)과 같은 치수로 블록 높이를 mm로 어림하고, 같은 쪽 나눔 규칙(블록이
# 넘치면 다음 쪽, 제목·항목 머리는 다음 블록과 함께)으로 쪽을 센다. 글자 폭은 글꼴을 모르므로 대략이다
# (한글 0.88em, 영문·숫자 0.53em, 공백 0.28em, 낱말 단위 줄 바꿈 ``word-break: keep-all``). 2026-11-18에
# Chrome 인쇄와 견주어 맞췄다 (docs/architecture/web.md 이력서). 그래도 어림이라 API는 ``pages_estimated: true``를 준다.
_PAGE_H = 297.0 - 16 - 18  # mm, .page 위아래 여백을 뺀 높이
_PAGE_W = 210.0 - 17 * 2
_PT = 25.4 / 72
_BODY_LINE = 10 * 1.55 * _PT  # 본문 10pt, 줄 높이 1.55
_B = 1.2  # .b padding-bottom


def _em(ch: str) -> float:
    if ch == " ":
        return 0.28
    if ord(ch) >= 0x1100 and unicodedata.east_asian_width(ch) in ("W", "F"):
        return 0.88
    return 0.53


def _lines(text: str, width_mm: float, size_pt: float = 10.0) -> int:
    """낱말 단위로 줄을 채운 줄 수 (긴 낱말은 글자 단위로 꺾인다: overflow-wrap: anywhere)."""
    if not text:
        return 0
    em = size_pt * _PT
    room = width_mm / em
    lines, used = 1, 0.0
    for word in text.split(" "):
        w = sum(_em(c) for c in word)
        gap = 0.28 if used else 0.0
        if used + gap + w <= room:
            used += gap + w
            continue
        if used:
            lines += 1
        while w > room:  # 아주 긴 낱말
            lines += 1
            w -= room
        used = w
    return lines


def _paginate(blocks: list[tuple[float, bool]]) -> int:
    """``PAGINATE_JS``와 같은 규칙: 넘치는 블록은 다음 쪽으로, 바로 앞의 keep 블록(제목·항목 머리)도 같이.
    쪽에 그 블록들뿐이면 넘쳐도 그 쪽에 둔다."""
    pages = 1
    page: list[tuple[float, bool]] = []
    for h, keep in blocks:
        page.append((h, keep))
        if sum(x for x, _ in page) <= _PAGE_H + 0.3:
            continue
        carry = [page[-1]]
        for prev in reversed(page[:-1]):
            if not prev[1]:
                break
            carry.insert(0, prev)
        if len(carry) == len(page):
            continue
        pages += 1
        page = carry
    return pages


def _blocks_one(doc: RenderDoc) -> list[tuple[float, bool]]:
    out: list[tuple[float, bool]] = []
    p = doc.profile
    if p is not None:
        text_w = _PAGE_W - (36 if p.photo else 0)
        h = 20 * 1.25 * _PT
        if p.headline:
            h += 1.5 + _lines(p.headline, text_w, 11) * 11 * 1.55 * _PT
        contact = [v for v in (p.email, p.phone, p.address, p.birth_date, p.military) if v]
        contact += [f"{lab} {url}" if lab else url for lab, url in p.links]
        if contact:
            h += 2 + _lines(" · ".join(contact), text_w) * _BODY_LINE
        out.append((max(h, 40.0 if p.photo else 0.0) + 5 + _B, False))
    for sec in doc.sections:
        out.append((4 + 1.5 + 11.5 * 1.55 * _PT + 0.3 + 2 + _B, True))
        if sec.kind == "skills":
            text = " · ".join(it.primary for it in sec.items if it.primary)
            out.append((_lines(text, _PAGE_W) * 10 * 1.7 * _PT + _B, False))
            continue
        for it in sec.items:
            h = 1.5 + 10.5 * 1.55 * _PT
            sub = " · ".join(v for v in (it.secondary, it.tertiary) if v)
            h += _lines(sub, _PAGE_W) * _BODY_LINE
            if it.tags and sec.kind == "project":
                h += _lines(", ".join(it.tags), _PAGE_W, 9) * 9 * 1.55 * _PT
            out.append((h + _B, bool(it.lines)))
            out.extend((_lines(ln, _PAGE_W - 4) * _BODY_LINE + _B, False) for ln in it.lines)
    return out


def _blocks_two(doc: RenderDoc) -> list[tuple[float, bool]]:
    out = _blocks_one(RenderDoc(doc.title, doc.template, doc.blind, doc.profile, ()))
    cell = _PAGE_W - 30 - 5
    for sec in doc.sections:
        if sec.kind == "skills":
            text = " · ".join(it.primary for it in sec.items if it.primary)
            out.append((5 + 2 + _lines(text, cell) * 10 * 1.7 * _PT + _B, False))
            continue
        for n, it in enumerate(sec.items):
            h = (5.3 if n == 0 else 3.7) + 10.5 * 1.55 * _PT
            sub = " · ".join(v for v in (it.secondary, it.tertiary) if v)
            h += _lines(sub, cell) * _BODY_LINE
            if it.tags and sec.kind == "project":
                h += _lines(", ".join(it.tags), cell, 9) * 9 * 1.55 * _PT
            h += sum(_lines(ln, cell - 4) * _BODY_LINE + 0.4 for ln in it.lines)
            out.append((h + _B, False))
    return out


def _blocks_table(doc: RenderDoc) -> list[tuple[float, bool]]:
    out: list[tuple[float, bool]] = [(20 * 1.55 * _PT + 8 + _B, False)]
    p = doc.profile
    if p is not None:
        rows = 3 + sum(bool(v) for v in (p.birth_date, p.address, p.military, p.links))
        out.append((rows * (_BODY_LINE + 3.4) + 5 + _B, False))
        if p.headline:
            out.append((_lines(p.headline, _PAGE_W, 11) * 11 * 1.55 * _PT + _B, False))
    cols = (27 - 4, 44 - 4, 38 - 4, _PAGE_W - 27 - 44 - 38 - 4)
    for sec in doc.sections:
        head = 3 + 1.5 + 11 * 1.55 * _PT
        if sec.kind == "skills":
            out.append((head + _B, True))
            text = " · ".join(it.primary for it in sec.items if it.primary)
            out.append((_lines(text, _PAGE_W - 4) * _BODY_LINE + 2.8 + _B, False))
            continue
        out.append((head + _BODY_LINE + 2.8 + _B, True))
        for it in sec.items:
            last = ([it.tertiary] if it.tertiary else []) + list(it.lines)
            n = max(
                _lines(it.period or "", cols[0]),
                _lines(it.primary, cols[1]),
                _lines(it.secondary or "", cols[2]),
                sum(_lines(x, cols[3]) for x in last),
                1,
            )
            out.append((n * _BODY_LINE + 2.8 + 0.6 * max(0, len(last) - 1) + _B, False))
    return out


def estimate_pages(doc: RenderDoc) -> int:
    if doc.template == "two_column":
        blocks = _blocks_two(doc)
    elif doc.template == "korean_table":
        blocks = _blocks_table(doc)
    else:
        blocks = _blocks_one(doc)
    return _paginate(blocks) if blocks else 1


__all__ = [
    "ALLOWED_REFS",
    "CONTENT_VERSION",
    "PARENT_KINDS",
    "REQUIRED_SECTIONS",
    "SECTION_KINDS",
    "SECTION_TITLE",
    "TEMPLATES",
    "VERSION_KINDS",
    "ClaimInfo",
    "Lookup",
    "RenderDoc",
    "RenderItem",
    "RenderProfile",
    "RenderSection",
    "ResumeConflict",
    "ResumeRuleError",
    "apply_operations",
    "build_render",
    "check_content",
    "content_hash",
    "empty_content",
    "estimate_pages",
    "item_state",
    "line_state",
]

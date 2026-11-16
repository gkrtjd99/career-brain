"""한국어 표현 규칙 (문서 검사용). 형태소 분석기 없이 정규식·사전으로만 한다.

- 수 표현: "두 배" → 2, "세 명" → 3, "절반" → 50, "3천" → 3000, "1만 2천 명" → 12000, "삼십 명" → 30.
  고유어 수(한·두·세…)와 한자어 수(일·이·삼…십·백·천)는 뒤에 단위 명사(명·배·개·번…)가 올 때만 수로 본다.
  "세계", "열정", "백엔드", "개발한 번역기"처럼 수 글자로 시작하는 낱말을 잘못 잡지 않기 위해서다.
- 기술명 한영 병기: "파이썬/Python", "자바스크립트/JavaScript", "쿠버네티스/K8s"를 같은 기술로 본다.
  기본 표(`TECH_ALIASES`) 위에 공간 설정의 사용자 별칭이 합쳐진다 (`merge_aliases`, `use_aliases`).

일부러 작고 단순하다. 놓치는 표현이 있고("수백 명", "두어 번"), 그런 표현은 숫자로 세지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

# --- 수 표현 -------------------------------------------------------------------------

_NATIVE_TENS = {
    "열": 10,
    "스물": 20,
    "스무": 20,
    "서른": 30,
    "마흔": 40,
    "쉰": 50,
    "예순": 60,
    "일흔": 70,
    "여든": 80,
    "아흔": 90,
}
# "한"(1)은 "한 번 더", "한 팀에서"처럼 수가 아닌 뜻으로 너무 흔해 혼자서는 수로 보지 않는다 ("열한"은 11).
_NATIVE_UNITS = {
    "한": 1,
    "두": 2,
    "세": 3,
    "네": 4,
    "다섯": 5,
    "여섯": 6,
    "일곱": 7,
    "여덟": 8,
    "아홉": 9,
}
_SINO_DIGITS = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_SMALL_UNITS = {"십": 10, "백": 100, "천": 1000}
_BIG_UNITS = {"만": 10_000, "억": 100_000_000, "조": 1_000_000_000_000}

# 수 뒤에 오는 단위 명사. 긴 것부터 맞춘다.
COUNTERS = tuple(
    sorted(
        [
            "배",
            "명",
            "개",
            "번",
            "회",
            "곳",
            "가지",
            "차례",
            "달",
            "개월",
            "해",
            "년",
            "시간",
            "주",
            "주일",
            "팀",
            "건",
            "대",
            "사람",
            "분",
            "단계",
            "종",
            "권",
            "장",
            "편",
            "층",
            "개국",
            "개사",
            "퍼센트",
            "프로",
            "%",
        ],
        key=len,
        reverse=True,
    )
)
# 단위 명사 바로 뒤에 올 수 있는 글자: 조사 첫 글자. 그 밖의 한글이 붙으면 다른 낱말이다 ("번역", "명함").
_AFTER = "이가을를은는의에로으과와도만씩째까부께보처쯤나랑하"
_COUNTER_RE = "|".join(re.escape(c) for c in COUNTERS)
_END = rf"(?=$|[^가-힣]|[{_AFTER}])"

_NATIVE_RE = re.compile(
    rf"(?<![가-힣\d.])((?:{'|'.join(_NATIVE_TENS)})?(?:{'|'.join(_NATIVE_UNITS)})?)\s?({_COUNTER_RE}){_END}"
)
_SINO_WORD = "[일이삼사오육칠팔구십백천만억조]+"
_SINO_RE = re.compile(rf"(?<![가-힣\d.])({_SINO_WORD})\s?({_COUNTER_RE}){_END}")
# 아라비아 숫자 + 한자어 큰 단위: "3천", "1.5만", "1만 2천", "2억"
_DIGIT_UNIT_RE = re.compile(r"(?<![\d.])((?:\d+(?:\.\d+)?\s?(?:[십백천억조]|만(?!큼))+\s?)+)")
_HALF_RE = re.compile(r"절반")


def _format(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def _combine(tokens: list[Decimal | str]) -> Decimal | None:
    """[수, 단위, 수, 단위 …] → 값. 단위는 십·백·천(작은)과 만·억·조(큰)."""
    total = Decimal(0)
    section = Decimal(0)
    current: Decimal | None = None
    seen_unit = False
    for tok in tokens:
        if isinstance(tok, Decimal):
            if current is not None:
                return None  # 수가 연달아 나오면 해석하지 않는다
            current = tok
        elif tok in _SMALL_UNITS:
            section += (current if current is not None else Decimal(1)) * _SMALL_UNITS[tok]
            current = None
            seen_unit = True
        elif tok in _BIG_UNITS:
            section += current if current is not None else Decimal(0)
            if section == 0:
                section = Decimal(1)
            total += section * _BIG_UNITS[tok]
            section = Decimal(0)
            current = None
            seen_unit = True
    total += section + (current if current is not None else Decimal(0))
    return total if seen_unit or tokens else None


def _sino_value(word: str) -> Decimal | None:
    tokens: list[Decimal | str] = []
    for ch in word:
        if ch in _SINO_DIGITS:
            tokens.append(Decimal(_SINO_DIGITS[ch]))
        else:
            tokens.append(ch)
    return _combine(tokens)


def _digit_unit_value(expr: str) -> Decimal | None:
    tokens: list[Decimal | str] = []
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)|([십백천만억조])", expr):
        if num:
            try:
                tokens.append(Decimal(num))
            except InvalidOperation:
                return None
        else:
            tokens.append(unit)
    if not any(isinstance(t, str) for t in tokens):
        return None
    return _combine(tokens)


def korean_numbers(text: str) -> tuple[set[str], list[tuple[int, int]]]:
    """한국어 수 표현의 값과 그 위치. 위치는 아라비아 숫자를 따로 셀 때 빼기 위해 돌려준다."""
    values: set[str] = set()
    spans: list[tuple[int, int]] = []
    t = text or ""
    for m in _DIGIT_UNIT_RE.finditer(t):
        v = _digit_unit_value(m.group(1))
        if v is not None:
            values.add(_format(v))
            spans.append(m.span(1))
    for m in _NATIVE_RE.finditer(t):
        word = m.group(1)
        if not word:
            continue
        tens = next((v for k, v in _NATIVE_TENS.items() if word.startswith(k)), 0)
        rest = word
        for k in sorted(_NATIVE_TENS, key=len, reverse=True):
            if word.startswith(k):
                rest = word[len(k) :]
                break
        unit = _NATIVE_UNITS.get(rest, 0) if rest else 0
        if (rest and not unit) or word == "한":
            continue
        values.add(str(tens + unit))
        spans.append(m.span(1))
    for m in _SINO_RE.finditer(t):
        word = m.group(1)
        # 한 글자 일·이·사·오는 "일", "이"(지시어), "사"(회사) 같은 낱말과 겹친다. 단위 글자가 있어야 수로 본다.
        if len(word) == 1 and word in _SINO_DIGITS:
            continue
        v = _sino_value(word)
        if v is not None and v > 0:
            values.add(_format(v))
            spans.append(m.span(1))
    for m in _HALF_RE.finditer(t):
        values.add("50")
        spans.append(m.span())
    return values, spans


# --- 기술명 한영 병기 ------------------------------------------------------------------

# 대표 표기(소문자 영문) → 같은 기술로 보는 다른 표기. 한글 표기와 흔한 약어만.
# 공간 설정(`workspace_settings.tech_aliases`, c0017)의 사용자 항목이 이 기본 표 위에 합쳐진다 (`merge_aliases`).
TECH_ALIASES: dict[str, tuple[str, ...]] = {
    "python": ("파이썬",),
    "javascript": ("자바스크립트", "js"),
    "typescript": ("타입스크립트", "ts"),
    "java": ("자바",),
    "kotlin": ("코틀린",),
    "swift": ("스위프트",),
    "go": ("golang", "고랭"),
    "rust": ("러스트",),
    "kubernetes": ("쿠버네티스", "k8s"),
    "docker": ("도커",),
    "postgresql": ("포스트그레스", "포스트그레스큐엘", "postgres"),
    "mysql": ("마이에스큐엘",),
    "redis": ("레디스",),
    "kafka": ("카프카",),
    "elasticsearch": ("엘라스틱서치",),
    "react": ("리액트", "react.js", "reactjs"),
    "node.js": ("nodejs", "노드js"),
    "spring": ("스프링",),
    "django": ("장고",),
    "linux": ("리눅스",),
    "terraform": ("테라폼",),
    "jenkins": ("젠킨스",),
    "tensorflow": ("텐서플로", "텐서플로우"),
    "pytorch": ("파이토치",),
    "android": ("안드로이드",),
    "aws": ("아마존 웹 서비스",),
}

# 사용자 항목 한도 (공간 설정 검사)
MAX_ALIAS_ENTRIES = 200
MAX_ALIASES_PER_ENTRY = 20
MAX_ALIAS_CHARS = 40


class AliasError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _spelling_key(value: str) -> str:
    """비교용 표기: 공백 정리, 영문은 소문자. 한글은 그대로."""
    return " ".join((value or "").split()).lower()


@dataclass(frozen=True, slots=True)
class AliasEntry:
    """사용자 별칭 한 줄: 대표 표기(화면 이름 그대로) + 같은 기술로 볼 다른 표기들."""

    term: str
    aliases: tuple[str, ...]

    @property
    def key(self) -> str:
        return _spelling_key(self.term)

    def as_dict(self) -> dict[str, Any]:
        return {"term": self.term, "aliases": list(self.aliases)}


def parse_alias_entries(raw: Any) -> tuple[AliasEntry, ...]:
    """설정 값(JSON 배열 [{term, aliases}]) 검사·정리. 잘못된 값은 `AliasError(code)`.

    - 대표 표기·별칭은 1~40자, 줄 바꿈 없음. 항목 200개, 항목당 별칭 20개까지.
    - 같은 표기가 두 항목에 나오면 `alias_conflict` (어느 기술인지 정할 수 없다).
    - 별칭이 자기 대표 표기와 같으면 빼고, 중복 별칭은 하나로 합친다.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise AliasError("invalid_aliases")
    if len(raw) > MAX_ALIAS_ENTRIES:
        raise AliasError("too_many_aliases")
    out: list[AliasEntry] = []
    owner: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise AliasError("invalid_aliases")
        term = " ".join(str(item.get("term") or "").split())
        names = item.get("aliases") or []
        if not term or len(term) > MAX_ALIAS_CHARS or not isinstance(names, list):
            raise AliasError("invalid_alias_term")
        if len(names) > MAX_ALIASES_PER_ENTRY:
            raise AliasError("too_many_aliases")
        key = _spelling_key(term)
        aliases: list[str] = []
        for n in names:
            a = " ".join(str(n or "").split())
            if not a or len(a) > MAX_ALIAS_CHARS:
                raise AliasError("invalid_alias")
            k = _spelling_key(a)
            if k == key or k in (_spelling_key(x) for x in aliases):
                continue
            aliases.append(a)
        for k in (key, *(_spelling_key(a) for a in aliases)):
            if owner.setdefault(k, key) != key:
                raise AliasError("alias_conflict")
        out.append(AliasEntry(term, tuple(aliases)))
    return tuple(out)


class TechAliases:
    """기술명 별칭 표: 대표 표기(소문자) → 다른 표기들. 기본 표에 사용자 항목을 합친 결과.

    사용자 항목이 이긴다: 사용자가 쓴 표기는 그 항목의 기술로만 본다 (기본 표에서 다른 기술의
    별칭이었으면 거기서 뺀다). 기본 표의 기술에 별칭을 더하려면 같은 대표 표기로 항목을 만든다.
    """

    __slots__ = ("_hangul_longer", "_to_canon", "entries", "table")

    def __init__(
        self, table: dict[str, tuple[str, ...]], entries: tuple[AliasEntry, ...] = ()
    ) -> None:
        self.table = table
        self.entries = entries
        self._to_canon: dict[str, str] = {}
        for canon, aliases in table.items():
            self._to_canon[canon] = canon
            for a in aliases:
                self._to_canon[_spelling_key(a)] = canon
        # "자바"가 "자바스크립트" 안에만 있을 때를 거르는 데 쓰는 더 긴 한글 표기들
        self._hangul_longer = tuple(k for k in self._to_canon if len(k) > 1 and not k.isascii())

    def canonical(self, term: str) -> str:
        low = _spelling_key(term)
        return self._to_canon.get(low, low)

    def spellings(self, term: str) -> tuple[str, ...]:
        canon = self.canonical(term)
        return (canon, *self.table.get(canon, ()))


def merge_aliases(entries: tuple[AliasEntry, ...] | None) -> TechAliases:
    """기본 표 + 사용자 항목. 항목이 없으면 기본 표 그대로."""
    if not entries:
        return BUILTIN_ALIASES
    claimed = {_spelling_key(s) for e in entries for s in (e.term, *e.aliases)}
    table: dict[str, tuple[str, ...]] = {}
    for canon, aliases in TECH_ALIASES.items():
        kept = tuple(a for a in aliases if _spelling_key(a) not in claimed)
        if canon in claimed and canon not in {e.key for e in entries}:
            # 사용자가 다른 기술의 별칭으로 쓴 대표 표기: 그 기술은 남기되 이 표기로는 찾지 않는다
            continue
        table[canon] = kept
    for e in entries:
        base = table.get(e.key, ())
        extra = tuple(
            a for a in e.aliases if _spelling_key(a) not in {_spelling_key(b) for b in base}
        )
        table[e.key] = (*base, *extra)
    return TechAliases(table, entries)


BUILTIN_ALIASES = TechAliases(dict(TECH_ALIASES))

# 지금 검사에 쓰는 별칭 표. 공간 설정을 읽은 usecase가 `use_aliases`로 정한다 (기본: 기본 표).
_ACTIVE: ContextVar[TechAliases] = ContextVar("career_tech_aliases", default=BUILTIN_ALIASES)


def current_aliases() -> TechAliases:
    return _ACTIVE.get()


@contextmanager
def use_aliases(aliases: TechAliases | None) -> Iterator[TechAliases]:
    """이 블록 안의 기술명 비교에 `aliases`를 쓴다 (None이면 바꾸지 않는다)."""
    if aliases is None:
        yield _ACTIVE.get()
        return
    token = _ACTIVE.set(aliases)
    try:
        yield aliases
    finally:
        _ACTIVE.reset(token)


def canonical_tech(term: str, aliases: TechAliases | None = None) -> str:
    """대표 표기. 사전에 없으면 소문자 그대로."""
    return (aliases or _ACTIVE.get()).canonical(term)


def tech_spellings(term: str, aliases: TechAliases | None = None) -> tuple[str, ...]:
    """같은 기술의 모든 표기 (대표 표기 먼저)."""
    return (aliases or _ACTIVE.get()).spellings(term)


def _has_spelling(text: str, spelling: str) -> bool:
    if spelling.isascii():
        pattern = rf"(?<![A-Za-z0-9]){re.escape(spelling)}(?![A-Za-z0-9])"
        return re.search(pattern, text, re.IGNORECASE) is not None
    # 한글 표기: 앞에 다른 한글이 붙지 않아야 한다 ("자바스크립트" 안의 "자바"는 별도로 거른다)
    for m in re.finditer(re.escape(spelling), text, re.IGNORECASE):
        before = text[m.start() - 1] if m.start() else ""
        if before and "가" <= before <= "힣":
            continue
        return True
    return False


def found_spellings(text: str, term: str, aliases: TechAliases | None = None) -> list[str]:
    """본문에서 찾은 이 기술의 표기들 (원문 그대로). 없으면 빈 목록."""
    table = aliases or _ACTIVE.get()
    out = []
    t = text or ""
    for spelling in table.spellings(term):
        if not _has_spelling(t, spelling):
            continue
        # "자바"가 "자바스크립트"의 일부일 때만 있으면 자바로 보지 않는다
        if not spelling.isascii():
            low = _spelling_key(spelling)
            covering = [a for a in table._hangul_longer if a != low and low in a]
            rest = t
            for a in covering:
                rest = re.sub(re.escape(a), " ", rest, flags=re.IGNORECASE)
            if not _has_spelling(rest, spelling):
                continue
        m = re.search(re.escape(spelling), t, re.IGNORECASE)
        out.append(m.group(0) if m else spelling)
    return out


def korean_tech_terms(text: str, aliases: TechAliases | None = None) -> list[str]:
    """한글(영문이 아닌 글자)로 쓴 기술명을 대표 표기로. ("파이썬으로 이전" → ["python"])"""
    table = aliases or _ACTIVE.get()
    return [
        canon
        for canon in table.table
        if any(not s.isascii() for s in found_spellings(text, canon, table))
    ]


__all__ = [
    "BUILTIN_ALIASES",
    "COUNTERS",
    "TECH_ALIASES",
    "AliasEntry",
    "AliasError",
    "TechAliases",
    "canonical_tech",
    "current_aliases",
    "found_spellings",
    "korean_numbers",
    "korean_tech_terms",
    "merge_aliases",
    "parse_alias_entries",
    "tech_spellings",
    "use_aliases",
]

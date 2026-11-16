"""한국어 표현 규칙 (문서 검사·검색용). 기본은 형태소 분석기 없이 정규식·사전으로만 한다.

- 수 표현: "두 배" → 2, "세 명" → 3, "절반" → 50, "3천" → 3000, "1만 2천 명" → 12000, "삼십 명" → 30.
  고유어 수(한·두·세…)와 한자어 수(일·이·삼…십·백·천)는 뒤에 단위 명사(명·배·개·번…)가 올 때만 수로 본다.
  "세계", "열정", "백엔드", "개발한 번역기"처럼 수 글자로 시작하는 낱말을 잘못 잡지 않기 위해서다.
- 기술명 한영 병기: "파이썬/Python", "자바스크립트/JavaScript", "쿠버네티스/K8s"를 같은 기술로 본다.
  기본 표(`TECH_ALIASES`) 위에 공간 설정의 사용자 별칭이 합쳐진다 (`merge_aliases`, `use_aliases`).

- 내용어 뽑기(`content_words`): 기본 `RuleAnalyzer`는 조사만 뗀다. 선택 extra `korean`(kiwipiepy)을 깔고
  `CAREER_KOREAN_ANALYZER=kiwi`를 주면 `KiwiAnalyzer`가 어미·복합명사·띄어쓰기 차이까지 다룬다.
  설치가 없으면 규칙 기반으로 돌아간다. 사실 변경·요건 가시성·빈도 묶음·키워드 검색이 이 함수를 쓴다.
  kiwi 모델(~0.42GB)은 `CAREER_KOREAN_IDLE_SECONDS`(기본 300초) 동안 안 쓰면 내리고 다음에 다시 올린다.

일부러 작고 단순하다. 놓치는 표현이 있고("수백 명", "두어 번"), 그런 표현은 숫자로 세지 않는다.
"""

from __future__ import annotations

import gc
import os
import re
import threading
import time
from collections import OrderedDict
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
# 공간 설정(`workspace_settings.tech_aliases`, c0021)의 사용자 항목이 이 기본 표 위에 합쳐진다 (`merge_aliases`).
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
    # 2026-11-17 넓힘: 공고·이력서에 흔한 도구 표기. 한글 표기는 뒤 글자를 보지 않으므로(조사가 붙는다)
    # 흔한 낱말의 앞부분이 될 수 있는 표기는 넣지 않는다 ("깃"→깃발, "뷰"→뷰어, "스칼라"=scalar,
    # "바벨"→바벨탑, "사이프러스"=나무, "주피터"=행성).
    "prometheus": ("프로메테우스",),
    "grafana": ("그라파나",),
    "kibana": ("키바나",),
    "jira": ("지라",),
    "confluence": ("컨플루언스",),
    "slack": ("슬랙",),
    "notion": ("노션",),
    "figma": ("피그마",),
    "github": ("깃허브", "깃헙"),
    "gitlab": ("깃랩",),
    "airflow": ("에어플로우", "에어플로"),
    "spark": ("스파크",),
    "hadoop": ("하둡",),
    "mongodb": ("몽고디비", "몽고db", "mongo"),
    "nginx": ("엔진엑스",),
    "graphql": ("그래프큐엘",),
    "angular": ("앵귤러",),
    "flutter": ("플러터",),
    "rabbitmq": ("래빗엠큐",),
    "ansible": ("앤서블",),
    "helm": ("헬름",),
    "istio": ("이스티오",),
    "datadog": ("데이터독",),
    "sentry": ("센트리",),
    "tableau": ("태블로",),
    "bigquery": ("빅쿼리",),
    "azure": ("애저",),
    "pandas": ("판다스",),
    "numpy": ("넘파이",),
    "gradle": ("그래들",),
    "maven": ("메이븐",),
    "webpack": ("웹팩",),
    "firebase": ("파이어베이스",),
    "selenium": ("셀레니움",),
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

    def lookup(self, term: str) -> str | None:
        """표에 있는 표기면 대표 표기, 없으면 None."""
        return self._to_canon.get(_spelling_key(term))

    def display(self, canon: str) -> str:
        """화면 이름: 사용자 항목이면 쓴 그대로("FastAPI"), 기본 표면 대표 표기."""
        for e in self.entries:
            if e.key == canon:
                return e.term
        return canon

    def phrases(self) -> tuple[str, ...]:
        """여러 낱말로 된 표기들 ("아마존 웹 서비스", 사용자 항목 "fast api")."""
        return tuple(k for k in self._to_canon if " " in k)

    def longer_hangul(self, spelling: str) -> tuple[str, ...]:
        """이 한글 표기로 시작하는 더 긴 표기 ("자바" → "자바스크립트"). 그 안의 일치는 이 기술이 아니다."""
        low = _spelling_key(spelling)
        return tuple(k for k in self._hangul_longer if k != low and k.startswith(low))

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


# --- 내용어 뽑기: 규칙(기본) 또는 선택 설치 형태소 분석기 ------------------------------------

# 낱말 끝에서 떼는 조사·어미 조각 (긴 것부터). 떼고 남은 말이 두 글자 이상일 때만 뗀다.
PARTICLES = tuple(
    sorted(
        [
            "으로서",
            "으로써",
            "에서는",
            "에서의",
            "에서",
            "으로",
            "에게",
            "까지",
            "부터",
            "과의",
            "와의",
            "이나",
            "이며",
            "이고",
            "하는",
            "하고",
            "하여",
            "해서",
            "했고",
            "한",
            "을",
            "를",
            "이",
            "가",
            "은",
            "는",
            "에",
            "의",
            "와",
            "과",
            "로",
            "도",
            "만",
        ],
        key=len,
        reverse=True,
    )
)
_HANGUL_WORD = re.compile(r"[가-힣]{2,}")
_HANGUL_ONLY = re.compile(r"[가-힣]+")


def strip_particle(word: str, particles: tuple[str, ...] = PARTICLES) -> str:
    """낱말 끝의 조사·어미 하나를 뗀다 ("트래픽을" → "트래픽"). 남는 말이 두 글자 미만이면 그대로."""
    for p in particles:
        if word.endswith(p) and len(word) - len(p) >= 2:
            return word[: -len(p)]
    return word


class RuleAnalyzer:
    """규칙 기반 (기본): 한글 낱말마다 끝의 조사·어미 하나를 뗀다. 복합 명사는 나누지 않는다."""

    name = "rule"
    morphological = False

    def content_spans(self, text: str) -> list[tuple[str, int, int]]:
        """(내용어, 시작, 끝) — 글 안의 글자 위치."""
        out = []
        for m in _HANGUL_WORD.finditer(text or ""):
            w = strip_particle(m.group())
            out.append((w, m.start(), m.start() + len(w)))
        return out

    def content_words(self, text: str) -> list[str]:
        return [strip_particle(w) for w in _HANGUL_WORD.findall(text or "")]

    def tokens(self, text: str) -> list[tuple[str, str]]:
        """(형태, 품사). 규칙 기반은 품사를 모른다: 내용어마다 ("낱말", "W")."""
        return [(w, "W") for w in self.content_words(text)]


# Kiwi 품사: 일반·고유 명사, 어근. 명사 파생 접미사(-화·-기·-자·-가 등)는 앞 명사에 붙이고,
# 형용사 파생(-하다, XSA: "공정한", "다양한")이 붙은 명사는 내용어로 보지 않는다.
_KIWI_NOUN_TAGS = frozenset({"NNG", "NNP", "XR"})
# 사전을 줄여 올린다: 오타·여러 낱말 사전 없이 RSS ~0.58GB → ~0.41GB (합성 평가 결과 같음, search.md)
KIWI_OPTIONS: dict[str, Any] = {"load_typo_dict": False, "load_multi_dict": False}
# 한동안 안 쓰면 모델을 내린다 (로컬 임베딩 `CAREER_EMBED_IDLE_SECONDS`와 같은 방식). 0이면 계속 들고 있다.
IDLE_ENV = "CAREER_KOREAN_IDLE_SECONDS"
IDLE_UNLOAD_SECONDS = 300.0


def idle_seconds(env: dict[str, str] | None = None) -> float:
    """`CAREER_KOREAN_IDLE_SECONDS` (기본 300초). 0 이하면 내리지 않는다. 잘못된 값은 기본값."""
    raw = (env if env is not None else os.environ).get(IDLE_ENV, "").strip()
    try:
        return max(0.0, float(raw)) if raw else IDLE_UNLOAD_SECONDS
    except ValueError:
        return IDLE_UNLOAD_SECONDS


class KiwiAnalyzer:
    """선택 설치 `korean` (kiwipiepy, Apache-2.0). 처음 쓸 때 모델을 올린다 (~0.4GB RSS, ~0.5초).

    명사·어근만 남긴다. 복합 명사는 나뉜다 ("결제시스템" → 결제, 시스템).
    같은 글을 여러 번 묻는 검사(요건마다 문서 전체)를 위해 최근 결과를 기억한다.
    `idle` 초 동안 안 쓰면 모델과 기억을 내리고, 다음에 쓸 때 다시 올린다 (`CAREER_KOREAN_IDLE_SECONDS`).
    `factory`는 시험용: `tokenize(text)`가 있는 객체를 만드는 함수 (주면 kiwipiepy를 import하지 않는다).
    """

    name = "kiwi"
    morphological = True

    def __init__(
        self,
        *,
        factory: Any = None,
        idle: float | None = None,
        clock: Any = None,
        **options: Any,
    ) -> None:
        if factory is None:
            from kiwipiepy import Kiwi  # 선택 의존성: 없으면 ImportError

            opts = {**KIWI_OPTIONS, **options}

            def factory() -> Any:
                return Kiwi(num_workers=-1, **opts)

        self._factory = factory
        self._idle = idle_seconds() if idle is None else max(0.0, float(idle))
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._kiwi: Any = None
        self._last_used = 0.0
        self._timer: threading.Timer | None = None
        self._cache: OrderedDict[str, tuple[tuple[str, str, int, int], ...]] = OrderedDict()
        self.loads = 0  # 모델을 올린 횟수 (내린 뒤 다시 올리면 늘어난다)

    @property
    def loaded(self) -> bool:
        return self._kiwi is not None

    def _drop_locked(self) -> None:
        self._kiwi = None
        self._cache.clear()

    def unload(self) -> None:
        """모델과 기억을 내린다. 다음 분석에서 다시 올린다."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._drop_locked()
        gc.collect()

    def unload_if_idle(self) -> bool:
        """마지막 사용 뒤 `idle` 초가 지났으면 내린다. 내렸으면 True. (타이머가 부른다)"""
        with self._lock:
            self._timer = None
            if self._kiwi is None or self._idle <= 0:
                return False
            remaining = self._last_used + self._idle - self._clock()
            if remaining > 0:
                self._schedule_locked(remaining)
                return False
            self._drop_locked()
        gc.collect()
        return True

    def _schedule_locked(self, delay: float) -> None:
        # 부를 때마다 타이머를 새로 만들지 않는다: 하나만 두고, 울렸을 때 남은 시간만큼 다시 건다
        if self._idle <= 0 or self._timer is not None:
            return
        self._timer = threading.Timer(delay, self.unload_if_idle)
        self._timer.daemon = True
        self._timer.start()

    def _analyze(self, text: str) -> tuple[tuple[str, str, int, int], ...]:
        """(형태, 품사, 시작, 끝). 글자 위치는 원문 기준."""
        key = text or ""
        with self._lock:
            hit = self._cache.get(key)
            if hit is None:
                if self._kiwi is None:
                    self._kiwi = self._factory()
                    self.loads += 1
                hit = tuple(
                    (t.form, t.tag, t.start, t.start + t.len) for t in self._kiwi.tokenize(key)
                )
                self._cache[key] = hit
                if len(self._cache) > 512:
                    self._cache.popitem(last=False)
            else:
                self._cache.move_to_end(key)
            self._last_used = self._clock()
            self._schedule_locked(self._idle)
        return hit

    def tokens(self, text: str) -> list[tuple[str, str]]:
        return [(form, tag) for form, tag, _, _ in self._analyze(text)]

    def content_spans(self, text: str) -> list[tuple[str, int, int]]:
        """(내용어, 시작, 끝). 파생 접미사를 붙인 낱말은 끝이 접미사 끝이다."""
        out: list[list[Any]] = []
        prev_noun = False
        for form, tag, start, end in self._analyze(text):
            if tag in _KIWI_NOUN_TAGS and _HANGUL_ONLY.fullmatch(form):
                out.append([form, start, end])
                prev_noun = True
                continue
            if prev_noun and tag == "XSN" and _HANGUL_ONLY.fullmatch(form):
                out[-1][0] += form
                out[-1][2] = end
                continue
            if prev_noun and tag == "XSA":
                out.pop()
            prev_noun = False
        return [(w, a, b) for w, a, b in out]

    def content_words(self, text: str) -> list[str]:
        return [w for w, _, _ in self.content_spans(text)]


ANALYZERS = ("rule", "kiwi")
_RULE = RuleAnalyzer()
_analyzer_cache: dict[str, RuleAnalyzer | KiwiAnalyzer] = {}
_analyzer_lock = threading.Lock()
_ANALYZER: ContextVar[RuleAnalyzer | KiwiAnalyzer | None] = ContextVar(
    "career_korean_analyzer", default=None
)


def load_analyzer(name: str | None) -> RuleAnalyzer | KiwiAnalyzer:
    """이름으로 분석기를 고른다. `kiwi`인데 설치되지 않았으면 규칙 기반으로 (검사를 멈추지 않는다)."""
    if (name or "rule").strip().lower() != "kiwi":
        return _RULE
    with _analyzer_lock:
        if "kiwi" not in _analyzer_cache:
            try:
                _analyzer_cache["kiwi"] = KiwiAnalyzer()
            except ImportError:
                _analyzer_cache["kiwi"] = _RULE
        return _analyzer_cache["kiwi"]


def korean_analyzer() -> RuleAnalyzer | KiwiAnalyzer:
    """지금 쓰는 분석기: `use_analyzer` 블록 → `CAREER_KOREAN_ANALYZER`(rule|kiwi) → 규칙 기반."""
    active = _ANALYZER.get()
    if active is not None:
        return active
    return load_analyzer(os.environ.get("CAREER_KOREAN_ANALYZER"))


@contextmanager
def use_analyzer(analyzer: RuleAnalyzer | KiwiAnalyzer | None) -> Iterator[None]:
    """이 블록 안의 내용어 뽑기에 `analyzer`를 쓴다 (평가·시험용)."""
    token = _ANALYZER.set(analyzer)
    try:
        yield
    finally:
        _ANALYZER.reset(token)


def content_words(text: str) -> list[str]:
    """한글 내용어 (원문 순서, 중복 포함). 불용어는 부르는 쪽이 뺀다."""
    return korean_analyzer().content_words(text)


def contains_phrase(text: str, phrase: str) -> bool:
    """글에 이 한글 낱말·구가 있는가. 여러 낱말 구는 띄어쓰기를 보지 않는다 ("테스트 코드" = "테스트코드").

    형태소 분석기가 있으면 내용어 순서로 맞춘다 ("공정한"의 "공정"은 낱말 "공정"이 아니다).
    """
    t, p = text or "", " ".join((phrase or "").split())
    if not p:
        return False
    analyzer = korean_analyzer()
    if analyzer.morphological:
        want = analyzer.content_words(p)
        have = analyzer.content_words(t)
        if want:
            n = len(want)
            return any(have[i : i + n] == want for i in range(len(have) - n + 1))
    if " " not in p:
        return p in t  # 한 낱말은 그대로 (공백을 지우면 "정보 안내"에서 "보안"이 나온다)
    return p.replace(" ", "") in t.replace(" ", "")


__all__ = [
    "ANALYZERS",
    "BUILTIN_ALIASES",
    "COUNTERS",
    "PARTICLES",
    "TECH_ALIASES",
    "AliasEntry",
    "AliasError",
    "KiwiAnalyzer",
    "RuleAnalyzer",
    "TechAliases",
    "canonical_tech",
    "contains_phrase",
    "content_words",
    "current_aliases",
    "found_spellings",
    "idle_seconds",
    "korean_analyzer",
    "korean_numbers",
    "korean_tech_terms",
    "load_analyzer",
    "merge_aliases",
    "parse_alias_entries",
    "strip_particle",
    "tech_spellings",
    "use_aliases",
    "use_analyzer",
]

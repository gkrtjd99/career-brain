"""한국어 표현 규칙 (문서 검사용). 형태소 분석기 없이 정규식·사전으로만 한다.

- 수 표현: "두 배" → 2, "세 명" → 3, "절반" → 50, "3천" → 3000, "1만 2천 명" → 12000, "삼십 명" → 30.
  고유어 수(한·두·세…)와 한자어 수(일·이·삼…십·백·천)는 뒤에 단위 명사(명·배·개·번…)가 올 때만 수로 본다.
  "세계", "열정", "백엔드", "개발한 번역기"처럼 수 글자로 시작하는 낱말을 잘못 잡지 않기 위해서다.
- 기술명 한영 병기: "파이썬/Python", "자바스크립트/JavaScript", "쿠버네티스/K8s"를 같은 기술로 본다.

일부러 작고 단순하다. 놓치는 표현이 있고("수백 명", "두어 번"), 그런 표현은 숫자로 세지 않는다.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

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
_ALIAS_TO_CANON: dict[str, str] = {}
for _canon, _aliases in TECH_ALIASES.items():
    _ALIAS_TO_CANON[_canon] = _canon
    for _a in _aliases:
        _ALIAS_TO_CANON[_a.lower()] = _canon


def canonical_tech(term: str) -> str:
    """대표 표기. 사전에 없으면 소문자 그대로."""
    low = (term or "").lower()
    return _ALIAS_TO_CANON.get(low, low)


def tech_spellings(term: str) -> tuple[str, ...]:
    """같은 기술의 모든 표기 (대표 표기 먼저)."""
    canon = canonical_tech(term)
    return (canon, *TECH_ALIASES.get(canon, ()))


def _has_spelling(text: str, spelling: str) -> bool:
    if spelling.isascii():
        pattern = rf"(?<![A-Za-z0-9]){re.escape(spelling)}(?![A-Za-z0-9])"
        return re.search(pattern, text, re.IGNORECASE) is not None
    # 한글 표기: 앞에 다른 한글이 붙지 않아야 한다 ("자바스크립트" 안의 "자바"는 별도로 거른다)
    for m in re.finditer(re.escape(spelling), text):
        before = text[m.start() - 1] if m.start() else ""
        if before and "가" <= before <= "힣":
            continue
        return True
    return False


def found_spellings(text: str, term: str) -> list[str]:
    """본문에서 찾은 이 기술의 표기들 (원문 그대로). 없으면 빈 목록."""
    out = []
    t = text or ""
    longer = [a for a in _ALIAS_TO_CANON if len(a) > 1]
    for spelling in tech_spellings(term):
        if not _has_spelling(t, spelling):
            continue
        # "자바"가 "자바스크립트"의 일부일 때만 있으면 자바로 보지 않는다
        if not spelling.isascii():
            covering = [a for a in longer if a != spelling and spelling in a and not a.isascii()]
            rest = t
            for a in covering:
                rest = rest.replace(a, " ")
            if not _has_spelling(rest, spelling):
                continue
        m = re.search(re.escape(spelling), t, re.IGNORECASE)
        out.append(m.group(0) if m else spelling)
    return out


def korean_tech_terms(text: str) -> list[str]:
    """한글로 쓴 기술명을 대표 표기로. ("파이썬으로 이전" → ["python"])"""
    return [
        canon
        for canon in TECH_ALIASES
        if any(not s.isascii() for s in found_spellings(text, canon))
    ]


__all__ = [
    "COUNTERS",
    "TECH_ALIASES",
    "canonical_tech",
    "found_spellings",
    "korean_numbers",
    "korean_tech_terms",
    "tech_spellings",
]

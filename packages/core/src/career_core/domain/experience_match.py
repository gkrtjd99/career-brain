"""같은 경험 찾기·같은 문장 찾기·인용이 주장을 받치는지·경험이 아닌 줄 가리기 (2026-11-18 사용 점검).

모델을 부르지 않는 규칙이다. 일부러 보수적이다: 확실할 때만 "같은 경험"(``SAME``)으로 보고, 애매하면
"같은 경험일 수 있음"(``MAYBE``)으로 새로 만들어 사람이 합치게 한다.

- 조직: 대소문자·공백·문장 부호·법인 표시(주식회사·(주)·㈜·Inc.·Corp.·Co., Ltd. 등)를 빼고 비교한다.
  회사 목록의 별칭(``companies.aliases``)을 주면 그 표기도 같은 조직으로 본다 (한글·영문 이름).
- 제목·역할: 정규화한 글이 같거나, 낱말 Jaccard·두 글자 Dice 중 큰 값. 제목↔역할도 서로 견준다
  ("소프트웨어 엔지니어"가 한 자료에선 제목, 다른 자료에선 역할).
- 기간: 둘 다 날짜가 있으면 겹쳐야 한다. 한쪽이라도 없으면 모른다 (막지 않는다).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from career_core.domain.korean import content_words, current_aliases, korean_numbers

SAME = "same"
MAYBE = "maybe"

SAME_TITLE = 0.8
MAYBE_TITLE = 0.4

_LEGAL = re.compile(
    r"주식회사|유한회사|유한책임회사|\(주\)|㈜|\(유\)|"
    r"\b(?:inc|incorporated|corp|corporation|co|company|ltd|limited|llc|gmbh|plc)\b\.?",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^0-9a-z가-힣]+")
_TOKEN = re.compile(r"[0-9a-z]+|[가-힣]+")


def _nfkc(value: str | None) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold()


def norm_org(value: str | None) -> str:
    """조직 비교 키: 법인 표시·공백·문장 부호를 뺀 소문자."""
    return _NON_WORD.sub("", _LEGAL.sub(" ", _nfkc(value)))


def norm_text(value: str | None) -> str:
    """제목·문장 비교 키: 공백·문장 부호를 뺀 소문자."""
    return _NON_WORD.sub("", _nfkc(value))


def _tokens(value: str | None) -> set[str]:
    return set(_TOKEN.findall(_nfkc(value)))


def _bigrams(key: str) -> set[str]:
    return {key[i : i + 2] for i in range(len(key) - 1)} if len(key) > 1 else {key} - {""}


def similarity(a: str | None, b: str | None) -> float:
    """0~1. 정규화한 글이 같으면 1, 한쪽이 다른 쪽을 품으면(3자 이상) 0.85, 아니면 낱말 Jaccard·두 글자 Dice."""
    ka, kb = norm_text(a), norm_text(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    short, long_ = sorted((ka, kb), key=len)
    if len(short) >= 3 and short in long_:
        return 0.85
    ta, tb = _tokens(a), _tokens(b)
    jac = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    ba, bb = _bigrams(ka), _bigrams(kb)
    dice = 2 * len(ba & bb) / (len(ba) + len(bb)) if ba and bb else 0.0
    return max(jac, dice)


@dataclass(frozen=True, slots=True)
class ExperienceKey:
    """비교에 쓰는 경험 칸."""

    id: UUID | None
    title: str
    organization: str | None = None
    role: str | None = None
    started_on: date | None = None
    ended_on: date | None = None
    ongoing: bool = False


def org_relation(a: str | None, b: str | None, aliases: Mapping[str, str] | None = None) -> str:
    """``same`` | ``unknown``(한쪽이 비었다) | ``different``. ``aliases``: 정규화 표기 → 회사 키."""
    ka, kb = norm_org(a), norm_org(b)
    if not ka or not kb:
        return "unknown"
    if aliases:
        ka, kb = aliases.get(ka, ka), aliases.get(kb, kb)
    if ka == kb:
        return "same"
    short, long_ = sorted((ka, kb), key=len)
    if len(short) >= 2 and long_.startswith(short):
        return "same"  # "바람물류" ↔ "바람물류가상", "acme" ↔ "acmekorea"
    return "different"


def periods_overlap(a: ExperienceKey, b: ExperienceKey, today: date | None = None) -> bool | None:
    if a.started_on is None or b.started_on is None:
        return None
    far = today or date(2200, 1, 1)
    a_end = a.ended_on or (far if a.ongoing else a.started_on)
    b_end = b.ended_on or (far if b.ongoing else b.started_on)
    return a.started_on <= b_end and b.started_on <= a_end


def title_similarity(a: ExperienceKey, b: ExperienceKey) -> float:
    pairs = [(a.title, b.title)]
    if a.role:
        pairs.append((a.role, b.title))
    if b.role:
        pairs.append((a.title, b.role))
    best = max(similarity(x, y) for x, y in pairs)
    # 제목이 직함(역할과 같은 글)인 경력끼리는 역할까지 같아야 같은 일로 본다
    return best


def match_level(
    cand: ExperienceKey, other: ExperienceKey, aliases: Mapping[str, str] | None = None
) -> tuple[str | None, float]:
    """(``same`` | ``maybe`` | None, 점수)."""
    org = org_relation(cand.organization, other.organization, aliases)
    if org == "different":
        return None, 0.0
    sim = title_similarity(cand, other)
    overlap = periods_overlap(cand, other)
    if org == "same":
        if sim >= SAME_TITLE and overlap is not False:
            return SAME, sim + (0.1 if overlap else 0.0)
        if sim >= MAYBE_TITLE:
            return MAYBE, sim
        return None, sim
    # 조직을 모른다: 제목이 거의 같고 기간이 겹칠 때만 같은 경험, 기간을 모르면 "같을 수 있음"
    if sim >= 0.95 and overlap is True:
        return SAME, sim
    if sim >= SAME_TITLE and overlap is not False:
        return MAYBE, sim
    return None, sim


def best_match(
    cand: ExperienceKey,
    existing: Iterable[ExperienceKey],
    aliases: Mapping[str, str] | None = None,
) -> tuple[str | None, ExperienceKey | None]:
    """가장 잘 맞는 기존 경험. ``same``이 하나라도 있으면 그중 점수가 가장 높은 것."""
    best: tuple[int, float, ExperienceKey] | None = None
    for other in existing:
        level, score = match_level(cand, other, aliases)
        if level is None:
            continue
        rank = 2 if level == SAME else 1
        if best is None or (rank, score) > (best[0], best[1]):
            best = (rank, score, other)
    if best is None:
        return None, None
    return (SAME if best[0] == 2 else MAYBE), best[2]


def alias_map(companies: Iterable[tuple[str, Sequence[str]]]) -> dict[str, str]:
    """회사 (이름, 별칭들) → 정규화 표기 → 회사 대표 키."""
    out: dict[str, str] = {}
    for name, aliases in companies:
        key = norm_org(name)
        if not key:
            continue
        for spelling in (name, *aliases):
            k = norm_org(spelling)
            if k:
                out.setdefault(k, key)
    return out


# ---------------------------------------------------------------- 같은 문장


def _numbers(text: str) -> set[str]:
    values, spans = korean_numbers(text or "")
    rest = list(text or "")
    for s, e in spans:
        rest[s:e] = [" "] * (e - s)
    digits = {n.lstrip("0") or "0" for n in re.findall(r"\d+(?:\.\d+)?", "".join(rest))}
    return values | digits


def same_statement(a: str, b: str) -> bool:
    """같은 사실을 적은 문장인가: 정규화한 글이 같거나, 두 글자 Dice ≥ 0.85이면서 숫자가 같다."""
    ka, kb = norm_text(a), norm_text(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    if _numbers(a) != _numbers(b):
        return False
    ba, bb = _bigrams(ka), _bigrams(kb)
    return 2 * len(ba & bb) / (len(ba) + len(bb)) >= 0.85


# ---------------------------------------------------------------- 인용이 주장을 받치는가

QUOTE_OK = "ok"
QUOTE_WEAK = "weak"
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9.+#-]{1,}")
_STOP = frozenset(
    {"경험", "업무", "담당", "진행", "통해", "위해", "대한", "관련", "사용", "그리고"}
)


def _key_words(text: str) -> set[str]:
    words = {w for w in content_words(text) if len(w) >= 2 and w not in _STOP}
    words |= {w.casefold() for w in _LATIN.findall(text or "")}
    return words


def quote_support(statement: str, quote: str) -> str:
    """인용이 주장을 받치는가 (``ok`` | ``weak``). 원문에 있는지는 추출이 이미 확인했다.

    - 주장에 있는 숫자가 인용에 없으면 약함 (숫자는 지어내기 쉬운 곳이다).
    - 주장의 핵심 낱말(한글 내용어·영문 낱말) 중 인용에 나오는 것이 없거나 3분의 1 미만이면 약함.
      한글은 붙여 쓰기 차이가 있어 인용 글을 띄어쓰기 없이도 본다.
    """
    if not (statement or "").strip() or not (quote or "").strip():
        return QUOTE_WEAK
    if _numbers(statement) - _numbers(quote):
        return QUOTE_WEAK
    words = _key_words(statement)
    if not words:
        return QUOTE_OK
    plain = norm_text(quote)
    low = (quote or "").casefold()
    hit = sum(1 for w in words if w.casefold() in low or norm_text(w) in plain)
    if hit == 0 or hit * 3 < len(words):
        return QUOTE_WEAK
    return QUOTE_OK


# ---------------------------------------------------------------- 경험이 아닌 줄

SCHOOL = re.compile(
    r"(대학교|대학원|대학|고등학교|중학교|학교|university|college|institute of technology|"
    r"high school|graduate school)",
    re.IGNORECASE,
)
DEGREE_WORDS = re.compile(
    r"(학사|석사|박사|전문학사|졸업|재학|휴학|수료|학점|전공|학과|bachelor|master|ph\.?d|"
    r"b\.?s\.?|m\.?s\.?|gpa)",
    re.IGNORECASE,
)
CERTIFICATE = re.compile(
    r"(기사|산업기사|기능사|기능장|기술사|자격증|자격|면허|certificate|certification|certified|"
    r"toeic|toefl|teps|opic|ielts|jlpt|jpt|hsk|sqld|sqlp|adsp|adp|aws certified|정보처리)",
    re.IGNORECASE,
)
LANGUAGE_TEST = re.compile(
    r"(toeic|toefl|teps|opic|ielts|jlpt|jpt|hsk|토익|토플|오픽)", re.IGNORECASE
)
SKILL_HEADINGS = frozenset(
    norm_text(x)
    for x in (
        "기술",
        "기술 스택",
        "보유 기술",
        "사용 기술",
        "스킬",
        "역량",
        "기술 역량",
        "skills",
        "skill",
        "tech stack",
        "technologies",
        "tools",
        "도구",
    )
)


def looks_like_school(*values: str | None) -> bool:
    return any(v and SCHOOL.search(v) for v in values)


def looks_like_certificate(name: str | None) -> bool:
    """자격증 이름처럼 보이는가 ("정보처리기사", "SQLD", "TOEIC 900"). 경력 직함("기술사업팀")은 뺀다."""
    n = " ".join((name or "").split())
    if not n or len(n) > 40:
        return False
    return CERTIFICATE.search(n) is not None and not re.search(r"(팀|부서|센터|본부|회사)", n)


def is_skill_heading(title: str | None) -> bool:
    return norm_text(title) in SKILL_HEADINGS


# ---------------------------------------------------------------- 기술 태그로 보지 않는 낱말

_MAJOR = re.compile(r"(학과|학부|전공|공학|과학|경영학|경제학|학$|학사|석사|박사)$")


def non_skill_tag(tag: str, *, titles: Iterable[str | None] = ()) -> bool:
    """전공·학위·자격증 이름·학교·경험 제목(프로젝트 이름)이면 기술 태그가 아니다."""
    t = " ".join((tag or "").split())
    if not t:
        return True
    if current_aliases().lookup(t) is not None:
        return False  # 별칭 표에 있는 기술명은 늘 기술이다
    key = norm_text(t)
    if _MAJOR.search(t) or SCHOOL.search(t) or looks_like_certificate(t):
        return True
    for title in titles:
        k = norm_text(title)
        if k and (k == key or (len(key) >= 4 and key in k and not t.isascii())):
            return True
    return False


def skill_tags(tags: Iterable[str], *, titles: Iterable[str | None] = ()) -> list[str]:
    titles = list(titles)
    return [t for t in tags if not non_skill_tag(t, titles=titles)]

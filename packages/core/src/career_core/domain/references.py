"""참고 자료 보관함 규칙 (O14, plan §19.2). 참고 칸: 다른 사람의 글이고 내 경험이 아니다.

- 종류: 합격 자기소개서·다른 사람 자기소개서·다른 사람 이력서·면접 후기·직무 글·기타.
- 개인정보: 남의 글 속 주민등록번호·전화번호·이메일·이름(``이름:`` 같은 표시 뒤)·학교 이름을 찾아 가리자고
  제안한다. 가리면 ``○○○``로 바꾼다 (학교는 ``○○○대학교``처럼 끝말을 남긴다). 찾는 규칙은 단순한 정규식이다 —
  놓치는 곳이 있으니 사람이 글을 보고 고른다.
- 비슷한 문장: 내 문서 문장이 참고 글 문장과 많이 겹치면 짚는다. 모델 없이 글자 3-gram 겹침으로 정한다
  (같은 입력이면 같은 결과). 점수 = 내 문장 3-gram 중 참고 문장에도 있는 비율.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from career_core.domain.documents import split_sentences
from career_core.domain.sensitive import _EMAIL, _LANDLINE, _MOBILE, _RRN


class ReferenceKind(StrEnum):
    ACCEPTED_ESSAY = "accepted_essay"  # 합격 자기소개서
    ESSAY = "essay"  # 다른 사람 자기소개서
    RESUME = "resume"  # 다른 사람 이력서
    INTERVIEW_REVIEW = "interview_review"  # 면접 후기
    JOB_ARTICLE = "job_article"  # 직무 글
    OTHER = "other"


REFERENCE_KINDS = tuple(k.value for k in ReferenceKind)


class NoteStatus(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"


class ReferenceRuleError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


MAX_TEXT = 200_000
MAX_TITLE = 300
MAX_ROLE = 100
MAX_SOURCE_NOTE = 1000
MAX_QUESTION = 1000
MAX_NOTE_ITEM = 200
MAX_NOTE_ITEMS = 20
MIN_YEAR, MAX_YEAR = 1950, 2100
MASK = "○○○"


def reference_kind(value: str | None) -> str:
    try:
        return ReferenceKind(value or "").value
    except ValueError as exc:
        raise ReferenceRuleError("invalid_kind") from exc


def _one_line(value: str | None, limit: int, code: str) -> str | None:
    s = " ".join(unicodedata.normalize("NFKC", value or "").split())
    if not s:
        return None
    if len(s) > limit:
        raise ReferenceRuleError(code)
    return s


def clean_title(value: str | None) -> str:
    s = _one_line(value, MAX_TITLE, "title_too_long")
    if not s:
        raise ReferenceRuleError("title_required")
    return s


def clean_role(value: str | None) -> str | None:
    return _one_line(value, MAX_ROLE, "role_too_long")


def clean_source_note(value: str | None) -> str | None:
    s = (value or "").strip()
    if not s:
        return None
    if len(s) > MAX_SOURCE_NOTE:
        raise ReferenceRuleError("source_note_too_long")
    return s


def clean_year(value: int | None) -> int | None:
    if value is None:
        return None
    if not MIN_YEAR <= value <= MAX_YEAR:
        raise ReferenceRuleError("invalid_year")
    return value


def clean_text(value: str | None) -> str:
    """줄바꿈은 남기고 줄 끝 공백과 3줄 넘는 빈 줄만 줄인다."""
    s = unicodedata.normalize("NFC", (value or "").replace("\r\n", "\n").replace("\r", "\n"))
    s = "\n".join(line.rstrip() for line in s.split("\n"))
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    if not s:
        raise ReferenceRuleError("text_required")
    if len(s) > MAX_TEXT:
        raise ReferenceRuleError("text_too_long")
    return s


def clean_items(values: list[str] | None, code: str) -> list[str]:
    """메모 칸 목록: 한 줄로, 빈 것·같은 것 빼기, 항목 200자·20개 이하."""
    out: list[str] = []
    seen: set[str] = set()
    for v in values or []:
        s = _one_line(v, MAX_NOTE_ITEM, f"{code}_item_too_long")
        if s and s.casefold() not in seen:
            seen.add(s.casefold())
            out.append(s)
    if len(out) > MAX_NOTE_ITEMS:
        raise ReferenceRuleError(f"too_many_{code}")
    return out


def clean_question(value: str | None) -> str | None:
    return _one_line(value, MAX_QUESTION, "question_too_long")


# ---------------------------------------------------------------- 개인정보 (남의 글)

# "이름: 홍길동", "성명 : 김 철 수"는 아니고 붙여 쓴 2~4글자, 영문 "Name: Jane Doe"
_LABELED_NAME = re.compile(
    r"(?P<label>(?:이\s?름|성\s?명|작성자|지원자|글쓴이|본명|Name)\s*[:：]\s*)"
    r"(?P<value>[가-힣]{2,4}(?![가-힣])|[A-Z][a-z]+(?:\s[A-Z][a-z]+){1,2})",
    re.IGNORECASE,
)
# 학교 이름: 붙여 쓴 고유 이름 + 학교 끝말. "○○" 같이 이미 가린 글은 한글이 아니라 다시 잡히지 않는다
_SCHOOL = re.compile(
    r"(?<![가-힣])(?P<value>[가-힣]{2,15})"
    r"(?P<suffix>대학교|대학원|여자고등학교|고등학교|중학교|초등학교|외국어고|과학고)"
)
# 끝말 앞 글자가 이런 낱말뿐이면 학교 이름이 아니다 ("우리대학교"는 남겨도 된다)
_NOT_SCHOOL_NAMES = frozenset({"우리", "같은", "다른", "모든", "지방", "명문", "해당", "소재"})

PII_KINDS = ("resident_registration_number", "phone_number", "email_address", "name", "school")


@dataclass(frozen=True, slots=True)
class PiiHit:
    kind: str
    start: int  # 가릴 곳 [start, end)
    end: int
    value: str  # 찾은 글 (사용자 본인의 공간 안에서만 보여 준다)
    replacement: str


def pii_hits(text: str) -> list[PiiHit]:
    """남의 글 속 식별 정보. 겹치면 앞의(먼저 찾은 종류의) 것만 남긴다."""
    t = text or ""
    found: list[PiiHit] = []
    for m in _RRN.finditer(t):
        found.append(PiiHit("resident_registration_number", *m.span(), m.group(0), MASK))
    for pattern in (_MOBILE, _LANDLINE):
        for m in pattern.finditer(t):
            found.append(PiiHit("phone_number", *m.span(), m.group(0), MASK))
    for m in _EMAIL.finditer(t):
        found.append(PiiHit("email_address", *m.span(), m.group(0), MASK))
    for m in _LABELED_NAME.finditer(t):
        found.append(PiiHit("name", *m.span("value"), m.group("value"), MASK))
    for m in _SCHOOL.finditer(t):
        if m.group("value") in _NOT_SCHOOL_NAMES:
            continue
        found.append(PiiHit("school", m.start(), m.end(), m.group(0), MASK + m.group("suffix")))
    out: list[PiiHit] = []
    taken: list[tuple[int, int]] = []
    for h in found:
        if any(h.start < b and a < h.end for a, b in taken):
            continue
        taken.append((h.start, h.end))
        out.append(h)
    return sorted(out, key=lambda h: h.start)


def mask_pii(text: str, hits: list[PiiHit] | None = None) -> tuple[str, int]:
    """찾은 곳을 ``○○○``로 바꾼 글과 바꾼 수."""
    hits = pii_hits(text) if hits is None else hits
    out = text
    for h in sorted(hits, key=lambda h: h.start, reverse=True):
        out = out[: h.start] + h.replacement + out[h.end :]
    return out, len(hits)


# ---------------------------------------------------------------- 비슷한 문장 (모델 없이)

N = 3
MIN_CHARS = 15  # 정규화한 글자 수가 이보다 짧은 문장은 보지 않는다 (짧은 인사말·제목이 걸리지 않게)
SIMILAR_AT = 0.6  # 내 문장 3-gram의 60% 이상이 한 참고 문장에 있으면 "비슷함"
MAX_CORPUS_SENTENCES = 20_000
# 이보다 많은 참고 문장에 나오는 3-gram("습니다" 같은 끝말)은 후보 찾기에 쓰지 않는다 (점수에는 쓴다)
COMMON_GRAM = 200
TOP_CANDIDATES = 8

_NOT_WORD = re.compile(r"[\W_]+", re.UNICODE)


def normalize_for_compare(text: str) -> str:
    return _NOT_WORD.sub("", unicodedata.normalize("NFKC", text or "").casefold())


def grams(text: str) -> frozenset[str]:
    s = normalize_for_compare(text)
    if len(s) < MIN_CHARS:
        return frozenset()
    return frozenset(s[i : i + N] for i in range(len(s) - N + 1))


def similarity(mine: str, reference: str) -> float:
    """내 문장 3-gram 중 참고 문장에도 있는 비율 (0~1). 짧은 문장은 0."""
    a, b = grams(mine), grams(reference)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a)


@dataclass(frozen=True, slots=True)
class SimilarMatch:
    reference_id: str | None  # 참고 글 (reference_items) — 글이 참고 원본에만 있으면 None
    source_id: str | None  # 참고 원본 (sources, 용도 reference)
    title: str
    score: float
    sentence: str  # 참고 문장 (검사 결과에 싣지 않는다. 시험·디버깅용)


class ReferenceCorpus:
    """공간의 참고 글 문장 모음. 문서 검사에서 문장마다 가장 비슷한 참고 문장을 찾는다."""

    def __init__(self, texts: list[tuple[str | None, str | None, str, str]]) -> None:
        """``texts``: (참고 글 ID, 원본 ID, 제목, 글). 문장은 앞에서부터 ``MAX_CORPUS_SENTENCES``개까지."""
        self._rows: list[tuple[str | None, str | None, str, str, frozenset[str]]] = []
        for ref_id, source_id, title, text in texts:
            for sentence in split_sentences(text):
                g = grams(sentence)
                if g:
                    self._rows.append((ref_id, source_id, title, sentence, g))
                if len(self._rows) >= MAX_CORPUS_SENTENCES:
                    break
            if len(self._rows) >= MAX_CORPUS_SENTENCES:
                break
        self._index: dict[str, list[int]] = {}
        for i, row in enumerate(self._rows):
            for g in row[4]:
                self._index.setdefault(g, []).append(i)

    def __len__(self) -> int:
        return len(self._rows)

    def best(self, sentence: str) -> SimilarMatch | None:
        """``SIMILAR_AT`` 이상인 가장 비슷한 참고 문장. 없으면 None."""
        mine = grams(sentence)
        if not mine or not self._rows:
            return None
        votes: Counter[int] = Counter()
        for g in mine:
            posting = self._index.get(g)
            if posting and len(posting) <= COMMON_GRAM:
                votes.update(posting)
        best: SimilarMatch | None = None
        for i, _ in votes.most_common(TOP_CANDIDATES):
            ref_id, source_id, title, ref_sentence, g = self._rows[i]
            score = len(mine & g) / len(mine)
            if score >= SIMILAR_AT and (best is None or score > best.score):
                best = SimilarMatch(ref_id, source_id, title, round(score, 2), ref_sentence)
        return best

    def copies(self, item: str) -> bool:
        """메모 한 줄이 참고 글 문장을 그대로 옮긴 것인지 (모델 후보에서 원문 문장을 거른다)."""
        return self.best(item) is not None


__all__ = [
    "MASK",
    "PII_KINDS",
    "REFERENCE_KINDS",
    "SIMILAR_AT",
    "NoteStatus",
    "PiiHit",
    "ReferenceCorpus",
    "ReferenceKind",
    "ReferenceRuleError",
    "SimilarMatch",
    "grams",
    "mask_pii",
    "pii_hits",
    "similarity",
]

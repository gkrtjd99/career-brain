"""지원 문서 규칙 (O07). 모두 순수 코드다 — 모델 없이, 같은 입력에 같은 결과.

- 글자·바이트 수는 문항 설정(공백·줄바꿈·인코딩)에 따라 코드가 센다. 모델이 센 값은 쓰지 않는다.
- 품질 검사는 경고다. 판단과 수정은 사용자가 한다.
  범용 문장(회사 바꿔치기)·상투어·다른 회사명, 근거 없는 경험·수치, 요건 가시성, 심사 시스템 조작.
- AI 수정안이 원문·연결된 근거에 없는 수치·역할·조직·기술명을 넣으면 거부한다.
  숨김 문자·심사 모델 대상 지시문도 수정안에 있으면 거부한다 (시스템이 만들지 않는다).

규칙은 일부러 단순하다(정규식·단어 일치). 놓치는 경우가 있고, 과하게 잡는 경우도 있다.
그래서 결과는 "확인해 볼 문장" 목록이지 자동 판정이 아니다.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from career_core.domain.korean import (
    TechAliases,
    canonical_tech,
    content_words,
    found_spellings,
    korean_analyzer,
    korean_numbers,
    korean_tech_terms,
    use_aliases,
)


class DocumentRuleError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RevisionConflict(DocumentRuleError):
    """base_revision이 최신이 아니다. 덮어쓰지 않는다.

    current: 문서의 최신 revision 번호. stale: 제출 때 기대와 다른 문서들.
    """

    def __init__(self, current: int | None = None, stale: list[dict] | None = None) -> None:
        super().__init__("revision_conflict")
        self.current = current
        self.stale = stale or []


class ApplicationStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    PASSED = "passed"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


# 사람이 바꿀 수 있는 상태. `submitted`는 제출(submit)로만 된다.
STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    ApplicationStatus.DRAFT: frozenset({ApplicationStatus.WITHDRAWN}),
    ApplicationStatus.SUBMITTED: frozenset(
        {ApplicationStatus.PASSED, ApplicationStatus.REJECTED, ApplicationStatus.WITHDRAWN}
    ),
    ApplicationStatus.PASSED: frozenset({ApplicationStatus.REJECTED, ApplicationStatus.WITHDRAWN}),
    ApplicationStatus.REJECTED: frozenset(),
    ApplicationStatus.WITHDRAWN: frozenset(),
}


class DocumentKind(StrEnum):
    QUESTION = "question"  # 공고 자기소개서 문항 하나
    RESUME = "resume"
    COVER_LETTER = "cover_letter"
    PORTFOLIO = "portfolio"
    OTHER = "other"


class BlockOrigin(StrEnum):
    USER = "user"
    AI = "ai"  # AI 수정안을 적용한 문단. 사용자가 다시 고치면 user가 된다


class SuggestionStatus(StrEnum):
    PROPOSED = "proposed"
    REJECTED = "rejected"  # 사실을 바꾸거나 조작 문구가 있어 코드가 거부했다
    APPLIED = "applied"
    DISMISSED = "dismissed"


MAX_BLOCKS = 200
MAX_BLOCK_CHARS = 10_000
MAX_DOCUMENT_CHARS = 60_000
MAX_CLAIMS_PER_BLOCK = 20
MAX_SPANS_PER_BLOCK = 50


# --- 본문 블록과 수정 연산 -------------------------------------------------------------
#
# 문서 본문은 문단(블록) 목록이다: {"id", "text", "origin": user|ai, "claim_ids": [...]}.
# 제출 문자열은 블록을 줄바꿈 하나로 잇는다.
#
# 문장 단위 근거(c0012, 선택): "spans": [{"start", "end", "claim_ids"}] — 블록 글 안의 위치(코드 포인트,
# NFC 정리 후). spans가 있으면 검사는 문장마다 그 문장에 걸친 span의 근거만 본다. 없으면 예전처럼 문단 단위.
# span의 주장은 블록 claim_ids에도 합쳐 둔다(문단 단위만 아는 화면·제출본도 근거 목록을 그대로 본다).
# spans가 없는 블록에는 키를 두지 않는다 — 예전 revision과 해시가 같다.


def new_block_id() -> str:
    return "b" + uuid4().hex[:12]


def _block_text(value: Any) -> str:
    if not isinstance(value, str):
        raise DocumentRuleError("invalid_text")
    text = normalize_text(value).strip()
    if not text:
        raise DocumentRuleError("empty_block")
    if len(text) > MAX_BLOCK_CHARS:
        raise DocumentRuleError("block_too_long")
    return text


def _claim_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise DocumentRuleError("invalid_claim_ids")
    try:
        out = list(dict.fromkeys(str(UUID(str(v))) for v in value))
    except ValueError as exc:
        raise DocumentRuleError("invalid_claim_ids") from exc
    if len(out) > MAX_CLAIMS_PER_BLOCK:
        raise DocumentRuleError("too_many_claims")
    return out


def _spans(value: Any, text: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_SPANS_PER_BLOCK:
        raise DocumentRuleError("invalid_spans")
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise DocumentRuleError("invalid_spans")
        start, end = item.get("start"), item.get("end")
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or isinstance(start, bool)
            or isinstance(end, bool)
            or not 0 <= start < end <= len(text)
        ):
            raise DocumentRuleError("invalid_spans")
        claims = _claim_ids(item.get("claim_ids"))
        if not claims:
            raise DocumentRuleError("invalid_spans")
        out.append({"start": start, "end": end, "claim_ids": claims})
    out.sort(key=lambda x: (x["start"], x["end"]))
    return out


def _reanchor(old_text: str, new_text: str, spans: Sequence[dict[str, Any]]) -> list[dict]:
    """글이 바뀌면 span이 가리키던 글을 새 글에서 다시 찾는다. 못 찾으면 그 span은 뺀다."""
    out = []
    for sp in spans:
        piece = old_text[sp["start"] : sp["end"]]
        i = new_text.find(piece) if piece else -1
        if i >= 0:
            out.append({**sp, "start": i, "end": i + len(piece)})
    return out


def _finish_block(block: dict[str, Any]) -> dict[str, Any]:
    """span 주장을 블록 claim_ids에 합치고, 빈 spans 키는 지운다."""
    spans = block.get("spans") or []
    out = {k: v for k, v in block.items() if k != "spans"}
    if spans:
        out["spans"] = spans
        span_claims = [c for sp in spans for c in sp["claim_ids"]]
        merged = list(dict.fromkeys([*out.get("claim_ids", []), *span_claims]))
        if len(merged) > MAX_CLAIMS_PER_BLOCK:
            raise DocumentRuleError("too_many_claims")
        out["claim_ids"] = merged
    return out


def apply_operations(
    blocks: Sequence[dict[str, Any]], operations: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """수정 연산을 적용한 새 블록 목록. 원본은 바꾸지 않는다.

    - replace {block_id, text, claim_ids?}: 글이 바뀌면 origin은 user
    - insert {text, index?, claim_ids?}: index가 없으면 끝에
    - delete {block_id}
    - link {block_id, claim_ids}: 근거 연결을 이 목록으로 바꾼다
    - set_blocks {blocks: [{id?, text, claim_ids?, spans?}]}: 전체 교체. 같은 id·같은 글이면 origin 유지
    - link_spans {block_id, spans: [{start, end, claim_ids}]}: 문장 단위 근거를 이 목록으로 바꾼다

    replace·insert·set_blocks도 spans를 받는다. 주지 않았는데 글이 바뀌면 옛 span을 새 글에서 다시 찾는다.
    """
    out = [{**b, "claim_ids": list(b.get("claim_ids") or [])} for b in blocks]

    def find(block_id: Any) -> int:
        for i, b in enumerate(out):
            if b["id"] == block_id:
                return i
        raise DocumentRuleError("unknown_block")

    if not operations:
        raise DocumentRuleError("no_operations")
    for op in operations:
        kind = op.get("op")
        if kind == "replace":
            i = find(op.get("block_id"))
            text = _block_text(op.get("text"))
            old = out[i]
            if text != old["text"]:
                out[i] = {**old, "text": text, "origin": BlockOrigin.USER.value}
                out[i]["spans"] = _reanchor(old["text"], text, old.get("spans") or [])
            if op.get("claim_ids") is not None:
                out[i]["claim_ids"] = _claim_ids(op["claim_ids"])
            if op.get("spans") is not None:
                out[i]["spans"] = _spans(op["spans"], text)
        elif kind == "insert":
            index = op.get("index")
            if index is None:
                index = len(out)
            if not isinstance(index, int) or not 0 <= index <= len(out):
                raise DocumentRuleError("invalid_index")
            text = _block_text(op.get("text"))
            out.insert(
                index,
                {
                    "id": new_block_id(),
                    "text": text,
                    "origin": BlockOrigin.USER.value,
                    "claim_ids": _claim_ids(op.get("claim_ids")),
                    "spans": _spans(op.get("spans"), text),
                },
            )
        elif kind == "delete":
            out.pop(find(op.get("block_id")))
        elif kind == "link":
            i = find(op.get("block_id"))
            out[i]["claim_ids"] = _claim_ids(op.get("claim_ids"))
        elif kind == "link_spans":
            i = find(op.get("block_id"))
            out[i]["spans"] = _spans(op.get("spans"), out[i]["text"])
        elif kind == "set_blocks":
            items = op.get("blocks")
            if not isinstance(items, list):
                raise DocumentRuleError("invalid_blocks")
            old = {b["id"]: b for b in out}
            new: list[dict[str, Any]] = []
            seen: set[str] = set()
            for item in items:
                if not isinstance(item, dict):
                    raise DocumentRuleError("invalid_blocks")
                text = _block_text(item.get("text"))
                prev = old.get(item.get("id"))
                if prev is None or prev["id"] in seen:
                    block_id, origin = new_block_id(), BlockOrigin.USER.value
                    spans: list[dict] = []
                else:
                    block_id = prev["id"]
                    origin = prev["origin"] if prev["text"] == text else BlockOrigin.USER.value
                    spans = _reanchor(prev["text"], text, prev.get("spans") or [])
                if item.get("spans") is not None:
                    spans = _spans(item["spans"], text)
                seen.add(block_id)
                new.append(
                    {
                        "id": block_id,
                        "text": text,
                        "origin": origin,
                        "claim_ids": _claim_ids(item.get("claim_ids")),
                        "spans": spans,
                    }
                )
            out = new
        else:
            raise DocumentRuleError("unknown_operation")
    out = [_finish_block(b) for b in out]
    if len(out) > MAX_BLOCKS:
        raise DocumentRuleError("too_many_blocks")
    if sum(len(b["text"]) for b in out) > MAX_DOCUMENT_CHARS:
        raise DocumentRuleError("document_too_long")
    return out


def document_text(blocks: Sequence[dict[str, Any]]) -> str:
    return "\n".join(b["text"] for b in blocks)


def ai_ratio(blocks: Sequence[dict[str, Any]]) -> float:
    """AI 수정안을 적용한 문단의 글자 비율 (§11 사용자 작성분과 AI 수정분의 비율)."""
    total = sum(len(b["text"]) for b in blocks)
    if not total:
        return 0.0
    ai = sum(len(b["text"]) for b in blocks if b.get("origin") == BlockOrigin.AI)
    return round(ai / total, 4)


# --- 글자·바이트 수 ------------------------------------------------------------------

SPACES_RULES = ("include", "exclude")
# one: 줄바꿈 1글자(\n) / two: 2글자(\r\n로 보내는 양식) / none: 세지 않음
NEWLINE_RULES = ("one", "two", "none")
ENCODINGS = ("utf-8", "cp949", "euc-kr")


@dataclass(frozen=True, slots=True)
class CountRule:
    spaces: str = "include"
    newlines: str = "one"
    encoding: str = "utf-8"

    @classmethod
    def parse(cls, data: dict[str, Any] | None) -> CountRule:
        data = data or {}
        unknown = set(data) - {"spaces", "newlines", "encoding"}
        rule = cls(
            spaces=data.get("spaces", "include"),
            newlines=data.get("newlines", "one"),
            encoding=str(data.get("encoding", "utf-8")).lower(),
        )
        if (
            unknown
            or rule.spaces not in SPACES_RULES
            or rule.newlines not in NEWLINE_RULES
            or rule.encoding not in ENCODINGS
        ):
            raise DocumentRuleError("invalid_count_rule")
        return rule

    def as_dict(self) -> dict[str, str]:
        return {"spaces": self.spaces, "newlines": self.newlines, "encoding": self.encoding}


@dataclass(frozen=True, slots=True)
class TextCounts:
    chars: int  # 규칙(공백 포함/제외)에 따른 글자 수 — 제한과 비교하는 값
    chars_with_spaces: int
    chars_without_spaces: int
    bytes: int  # 규칙 인코딩·줄바꿈 기준, 공백 포함
    newlines: int
    rule: dict[str, str]
    char_limit: int | None = None
    byte_limit: int | None = None
    over_char_limit: bool = False
    over_byte_limit: bool = False
    unencodable: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "chars": self.chars,
            "chars_with_spaces": self.chars_with_spaces,
            "chars_without_spaces": self.chars_without_spaces,
            "bytes": self.bytes,
            "newlines": self.newlines,
            "rule": self.rule,
            "char_limit": self.char_limit,
            "byte_limit": self.byte_limit,
            "over_char_limit": self.over_char_limit,
            "over_byte_limit": self.over_byte_limit,
            "unencodable": self.unencodable,
        }


def normalize_text(text: str) -> str:
    """저장 전 정리: NFC(맥 입력의 풀어쓴 자모가 두세 글자로 세지지 않게), 줄바꿈은 \\n.

    보이지 않는 문자는 지우지 않는다 — 검사가 경고해야 하므로 사용자가 쓴 그대로 둔다.
    """
    return unicodedata.normalize("NFC", text or "").replace("\r\n", "\n").replace("\r", "\n")


def count_text(
    text: str,
    rule: CountRule | None = None,
    *,
    char_limit: int | None = None,
    byte_limit: int | None = None,
) -> TextCounts:
    rule = rule or CountRule()
    t = normalize_text(text)
    newlines = t.count("\n")
    body = t.replace("\n", "")
    weight = {"one": 1, "two": 2, "none": 0}[rule.newlines]
    with_spaces = len(body) + newlines * weight
    without_spaces = len(re.sub(r"\s", "", body))  # 공백 제외는 줄바꿈도 뺀다
    unencodable = sorted({ch for ch in body if not _encodable(ch, rule.encoding)})
    # 인코딩에 없는 문자는 양식마다 처리가 달라 1바이트(?)로 센다. 따로 목록으로 알린다.
    nbytes = len(body.encode(rule.encoding, errors="replace")) + newlines * weight
    chars = with_spaces if rule.spaces == "include" else without_spaces
    return TextCounts(
        chars=chars,
        chars_with_spaces=with_spaces,
        chars_without_spaces=without_spaces,
        bytes=nbytes,
        newlines=newlines,
        rule=rule.as_dict(),
        char_limit=char_limit,
        byte_limit=byte_limit,
        over_char_limit=char_limit is not None and chars > char_limit,
        over_byte_limit=byte_limit is not None and nbytes > byte_limit,
        unencodable=unencodable,
    )


def _encodable(ch: str, encoding: str) -> bool:
    try:
        ch.encode(encoding)
    except UnicodeEncodeError:
        return False
    return True


_LIMIT_CHARS = re.compile(r"(\d[\d,]*)\s*자")
_LIMIT_BYTES = re.compile(r"(\d[\d,]*)\s*(?:byte|bytes|바이트)", re.IGNORECASE)


def rule_from_prompt(
    prompt: str, char_limit: int | None = None
) -> tuple[CountRule, int | None, int | None]:
    """문항 문구에서 세는 기준을 읽는다. (규칙, 글자 제한, 바이트 제한)

    "공백 제외" → 공백 제외. "1000byte" → 바이트 제한. "한글 2byte"류 → cp949.
    알 수 없으면 공백 포함·줄바꿈 1글자·UTF-8 (사용자가 문서 설정에서 고친다).
    """
    p = prompt or ""
    spaces = "exclude" if re.search(r"(공백|띄어쓰기)\s*(제외|미포함)", p) else "include"
    byte_limit = None
    encoding = "utf-8"
    if m := _LIMIT_BYTES.search(p):
        byte_limit = int(m.group(1).replace(",", ""))
        if re.search(r"(한글|국문)[^\d]{0,10}2\s*(byte|바이트)", p, re.IGNORECASE):
            encoding = "cp949"
    if char_limit is None and (m := _LIMIT_CHARS.search(p)):
        char_limit = int(m.group(1).replace(",", ""))
    return CountRule(spaces=spaces, encoding=encoding), char_limit, byte_limit


# --- 문장·단어 ---------------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n+")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9+#]*(?:[.\-][A-Za-z0-9+#]+)*")
# 한글 낱말의 조사·어미 떼기는 domain.korean.content_words (규칙 기반 기본, 선택 설치 형태소 분석기).
# 요건 문장에 흔하지만 무엇을 요구하는지는 말해 주지 않는 말.
KO_STOPWORDS = frozenset(
    [
        "경험",
        "이해",
        "능력",
        "가능자",
        "가능",
        "이상",
        "우대",
        "사항",
        "보유",
        "보유자",
        "활용",
        "사용",
        "관련",
        "업무",
        "지식",
        "또는",
        "있는",
        "있으신",
        "신입",
        "경력",
        "개발",
        "역량",
        "기반",
        "기본",
        "대한",
        "통한",
        "위한",
        "갖춘",
        "원활",
        "경우",
        "해당",
        "전공",
        "학력",
        "무관",
        "다양",
        "다양한",
        "프로젝트",
        "수행",
        "참여",
        "지원",
        "담당",
        "서비스",
        "분야",
        "기술",
        "환경",
        "실무",
        "구축",
        "운영",
        "설계",
        "구현",
        "개선",
        "관리",
        "이상의",
        "분들",
        "하신",
        "있으며",
        "자격",
        "요건",
    ]
)
LATIN_STOPWORDS = frozenset(
    {"or", "and", "the", "of", "with", "in", "on", "for", "to", "a", "an", "etc", "e.g", "i", "my"}
)


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text or "") if s.strip()]


def sentence_ranges(text: str) -> list[tuple[int, int, str]]:
    """문장과 블록 글 안의 위치 [start, end). split_sentences와 같은 문장을 같은 순서로."""
    out = []
    pos = 0
    t = text or ""
    for m in [*_SENTENCE_SPLIT.finditer(t), None]:
        end = m.start() if m else len(t)
        piece = t[pos:end]
        stripped = piece.strip()
        if stripped:
            start = pos + (len(piece) - len(piece.lstrip()))
            out.append((start, start + len(stripped), stripped))
        if m:
            pos = m.end()
    return out


def latin_terms(text: str) -> list[str]:
    out = []
    for t in _LATIN.findall(text or ""):
        for part in t.split("/"):
            low = part.lower()
            if low and low not in LATIN_STOPWORDS:
                out.append(low)
    return out


def korean_terms(text: str) -> list[str]:
    out = []
    for w in content_words(text):
        if len(w) >= 2 and w not in KO_STOPWORDS:
            out.append(w)
    return out


@dataclass(frozen=True, slots=True)
class Terms:
    """요건 하나를 판정할 단어들. latin이 있으면 그중 하나(“Python 또는 Go”), 한국어만 있으면 두 개 이상."""

    words: tuple[str, ...]
    latin: bool

    @property
    def needed(self) -> int:
        if not self.words:
            return 0
        return 1 if self.latin else min(2, len(self.words))


def tech_terms(text: str) -> list[str]:
    """영문 기술명 + 한글로 쓴 기술명, 대표 표기로 ("파이썬" → python, "K8s" → kubernetes)."""
    return list(
        dict.fromkeys([*(canonical_tech(t) for t in latin_terms(text)), *korean_tech_terms(text)])
    )


def key_terms(requirement: str) -> Terms:
    latin = tech_terms(requirement)
    if latin:
        return Terms(tuple(latin), True)
    return Terms(tuple(dict.fromkeys(korean_terms(requirement))), False)


def _has_latin(text: str, term: str) -> bool:
    return (
        re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text, re.IGNORECASE)
        is not None
    )


def matched_terms(text: str, terms: Terms) -> list[str]:
    """요건 단어 중 본문에 있는 것. 판정 기준(needed)을 못 넘으면 빈 목록.

    한국어 낱말은 규칙 기반이면 부분 문자열("트래픽" ⊂ "트래픽을"), 형태소 분석기(선택 설치)가 있으면
    본문의 내용어 집합으로 본다 ("영업"은 "영업일"에, "인사"는 "인사말"에 걸리지 않는다).
    """
    if not terms.words:
        return []
    # 기술명은 한영 표기를 같이 본다 ("Python" 요건 ↔ 문서의 "파이썬")
    if terms.latin:
        hits = [w for w in terms.words if found_spellings(text, w)]
    elif korean_analyzer().morphological:
        found = set(content_words(text))
        hits = [w for w in terms.words if w in found]
    else:
        hits = [w for w in terms.words if w in text]
    return hits if len(hits) >= terms.needed else []


_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


def number_parts(text: str) -> set[str]:
    """글에 있는 숫자 조각. "1,200" → 1200, "2021.03" → 2021, 3 (앞자리 0 제거).

    날짜 표기가 달라도("2021년 3월" / "2021.03") 같은 숫자로 본다.
    한국어 수 표현도 숫자로 본다: "두 배" → 2, "세 명" → 3, "절반" → 50, "3천 명" → 3000 (domain.korean).
    """
    t = _THOUSANDS.sub("", text or "")
    values, spans = korean_numbers(t)
    for start, end in sorted(spans, reverse=True):
        t = t[:start] + " " + t[end:]
    found = {n for v in values for n in re.findall(r"\d+", v)}
    found |= set(re.findall(r"\d+", t))
    return {n.lstrip("0") or "0" for n in found}


_ACTION = re.compile(
    r"(개발|구축|설계|운영|개선|담당|주도|참여|도입|달성|분석|해결|진행|수행|기획|리드|배포|"
    r"자동화|최적화|이전|전환|작성|구현|줄였|줄이|늘렸|늘리|높였|낮췄|만들|이끌|맡았|맡아|관리|"
    r"출시|런칭|발표|수상|단축|절감|확보|유치)"
)
_PAST = re.compile(r"(았|었|였|했|렸|켰|웠|냈|쳤|졌|겼|췄|됐)")
_EN_ACTION = re.compile(
    r"\b(built|led|developed|designed|reduced|increased|improved|launched|managed|"
    r"implemented|migrated|created|owned|shipped|cut|grew)\b",
    re.IGNORECASE,
)
LEAD_TERMS = (
    "주도",
    "리드",
    "총괄",
    "이끌",
    "이끈",
    "책임자",
    "팀장",
    "리더",
    "단독",
    "혼자",
    "주관",
    "lead",
    "led",
    "head",
)


def is_experience_statement(sentence: str) -> bool:
    """본인이 한 일을 말하는 문장 (행동 + 과거형). '지원하게 되었습니다' 같은 문장은 아니다."""
    return bool(
        (_ACTION.search(sentence) and _PAST.search(sentence)) or _EN_ACTION.search(sentence)
    )


# 형태소 분석기가 있을 때 한국어 역할 표현을 형태로 본다: 명사(주도·리더…), 동사 어간(이끌다), 부사(혼자).
# "리더십", "리드타임", "단독주택"처럼 다른 낱말 속의 글자는 역할로 보지 않는다.
_LEAD_MORPHS = {
    ("이끌", "VV"): "이끌",
    ("혼자", "MAG"): "혼자",
    ("혼자", "NNG"): "혼자",
}


def lead_terms(text: str) -> set[str]:
    low = (text or "").lower()
    out = set()
    analyzer = korean_analyzer()
    if analyzer.morphological:
        words = set(analyzer.content_words(text))
        tokens = set(analyzer.tokens(text))
        for t in LEAD_TERMS:
            if not t.isascii() and t in words:
                out.add(t)
        out |= {v for k, v in _LEAD_MORPHS.items() if k in tokens}
    for t in LEAD_TERMS:
        if t.isascii():
            if re.search(rf"\b{t}\b", low):
                out.add(t)
        elif not analyzer.morphological and t in low:
            out.add(t)
    return out


_ORG_SUFFIX = re.compile(
    r"[가-힣A-Za-z0-9]{1,20}(?:전자|은행|그룹|재단|대학교|연구소|연구원|코리아|랩스|테크|컴퍼니|"
    r"증권|보험|카드|생명|공사|공단|주식회사|㈜)"
)


def organization_names(text: str) -> set[str]:
    names = set(_ORG_SUFFIX.findall(text or ""))
    names |= {
        m.strip() for m in re.findall(r"(?:주식회사|㈜|\(주\))\s*([가-힣A-Za-z0-9]{2,20})", text)
    }
    return {n for n in names if len(n) >= 2}


# --- 심사 시스템 조작 (§17.19) ---------------------------------------------------------

# 보이지 않는 문자: soft hyphen, 결합 문자, 한글 채움, 폭 없는 공백·결합자, 방향 제어, BOM, 태그 문자.
_INVISIBLE_RANGES = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x115F, 0x1160),
    (0x180E, 0x180E),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x2064),
    (0x2066, 0x2069),
    (0x3164, 0x3164),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xE0000, 0xE007F),
)
INVISIBLE_CHARS = re.compile(
    "[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in _INVISIBLE_RANGES) + "]"
)
HIDDEN_MARKUP = re.compile(
    r"(color\s*:\s*(#f{3}(?:f{3})?\b|white\b|rgba?\(\s*255\s*,\s*255\s*,\s*255)"
    r"|font-size\s*:\s*0(?:\.0+)?(?:px|pt|em|rem)?\s*[;\"'}]"
    r"|display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?:\.0+)?\s*[;\"'}]"
    r"|<\s*(?:span|div|font|p)\b[^>]*\bstyle\s*="
    r"|<\s*font\b[^>]*color\s*=\s*[\"']?(?:#?f{3}(?:f{3})?|white)"
    r"|\\color\{white\})",
    re.IGNORECASE,
)
SCREENING_INSTRUCTIONS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        (
            r"ignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier|other)\s+"
            r"(?:instructions?|prompts?|rules|criteria)"
        ),
        r"disregard\s+(?:all\s+|the\s+|any\s+)?(?:previous|prior|above|earlier|other)",
        (
            r"(?:you\s+are|act\s+as)\s+(?:an?\s+)?(?:ai|assistant|language\s+model|recruit\w*|"
            r"screen\w*|hiring\s+manager)"
        ),
        (
            r"(?:rank|score|rate|recommend|shortlist|select)\s+(?:this|the)\s+"
            r"(?:candidate|applicant|resume|cv)\s+(?:as|first|highest|top)"
        ),
        (
            r"(?:this|the)\s+(?:candidate|applicant)\s+is\s+(?:the\s+best|highly\s+qualified|"
            r"a\s+perfect|an?\s+ideal)"
        ),
        r"(?:note\s+to|attention|message\s+for)\s+(?:the\s+)?(?:ai|llm|gpt|model|ats|screener)",
        (
            r"(?:이전|앞의|위의|기존|다른)\s*(?:의\s*)?(?:모든\s*)?(?:지시|지침|명령|프롬프트|평가\s*기준)"
            r"\s*(?:사항)?(?:을|를|은|는)?\s*(?:모두\s*)?(?:무시|잊)"
        ),
        (
            r"(?:ai|인공지능|모델|심사\s*(?:모델|시스템|프로그램)|채용\s*(?:시스템|프로그램|ai)|"
            r"평가자|ats|gpt|llm)\s*(?:은|는|에게|께|이|가)?\s*[^.\n]{0,30}"
            r"(?:합격|최고\s*점수|만점|1순위|최우선|추천)\s*(?:시켜|하세요|하십시오|해\s*주|하라|해라|처리하)"
        ),
        (
            r"이\s*지원자(?:를|는|가)\s*[^.\n]{0,20}(?:합격|최고|만점|1순위)\s*(?:시켜|하세요|하십시오|"
            r"해\s*주|처리|으로\s*평가|로\s*평가)"
        ),
    )
)

STUFFING_MIN_COUNT = 5
STUFFING_MIN_SHARE = 0.05
KEYWORD_LIST_MIN_ITEMS = 6


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    message: str
    block_id: str | None = None
    sentence: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    severity: str = "warning"

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "block_id": self.block_id,
            "sentence": self.sentence,
            "detail": self.detail,
        }


def ats_findings(
    text: str,
    *,
    block_id: str | None = None,
    unsupported_terms: Iterable[str] = (),
    repetition: bool = True,
) -> list[Finding]:
    """숨김 문자·숨김 서식·심사 모델 대상 지시문·키워드 반복. 경고만 한다."""
    out: list[Finding] = []
    invisible = sorted({f"U+{ord(c):04X}" for c in INVISIBLE_CHARS.findall(text or "")})
    if invisible:
        out.append(
            Finding(
                "hidden_characters",
                "보이지 않는 문자가 있습니다. 화면에 안 보이는 글은 심사 조작으로 볼 수 있어 지우는 것이 좋습니다.",
                block_id,
                detail={"characters": invisible},
            )
        )
    if m := HIDDEN_MARKUP.search(text or ""):
        out.append(
            Finding(
                "hidden_text_markup",
                "흰 글씨·크기 0·숨김 같은 서식이 있습니다. 숨긴 글은 넣지 않습니다.",
                block_id,
                detail={"match": m.group(0)[:80]},
            )
        )
    for sentence in split_sentences(text):
        if any(p.search(sentence) for p in SCREENING_INSTRUCTIONS):
            out.append(
                Finding(
                    "screening_instruction",
                    "심사 프로그램·모델에게 지시하는 문장으로 보입니다. 지원 문서에 넣지 않습니다.",
                    block_id,
                    sentence,
                )
            )
    words = [*latin_terms(text), *korean_terms(text)] if repetition else []
    if words:
        for word, n in Counter(words).most_common(3):
            if n >= STUFFING_MIN_COUNT and n / len(words) >= STUFFING_MIN_SHARE:
                out.append(
                    Finding(
                        "keyword_repetition",
                        f"'{word}'가 {n}번 반복됩니다. 같은 단어를 채워 넣은 것처럼 보일 수 있습니다.",
                        block_id,
                        detail={"term": word, "count": n},
                    )
                )
    unsupported = {t.lower() for t in unsupported_terms}
    for line in (text or "").split("\n"):
        items = [i.strip() for i in re.split(r"[,/·;|]", line) if i.strip()]
        if len(items) < KEYWORD_LIST_MIN_ITEMS or re.search(r"(다|요)\s*[.!]?\s*$", line):
            continue
        listed = {t for i in items for t in latin_terms(i) + korean_terms(i)}
        bad = sorted(listed & unsupported)
        if len(bad) >= 3:
            out.append(
                Finding(
                    "keyword_list_unsupported",
                    "브레인에 근거가 없는 공고 키워드가 목록으로 나열되어 있습니다. "
                    "실제로 쓴 기술만 남깁니다.",
                    block_id,
                    line.strip()[:200],
                    detail={"terms": bad},
                )
            )
    return out


# --- 사실 변경 (§17.17) ----------------------------------------------------------------


def fact_changes(
    original: str,
    suggested: str,
    evidence_texts: Sequence[str] = (),
    known_organizations: Iterable[str] = (),
    *,
    aliases: TechAliases | None = None,
) -> list[dict[str, Any]]:
    """`_fact_changes`를 공간 별칭 표(`aliases`, 없으면 지금 표)로."""
    with use_aliases(aliases):
        return _fact_changes(original, suggested, evidence_texts, known_organizations)


def _fact_changes(
    original: str,
    suggested: str,
    evidence_texts: Sequence[str] = (),
    known_organizations: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """수정안이 원문·연결 근거에 없는 사실을 넣었는지. 비어 있으면 통과.

    - number: 원문·근거에 없는 숫자 (수치 확대, 날짜 변경)
    - role: 원문·근거에 없는 주도·총괄 같은 역할 표현 (참여 → 주도)
    - organization: 원문·근거에 없는 조직명
    - new_term: 원문·근거에 없는 영문 고유명사·기술명 (보유하지 않은 기술의 키워드 삽입)
    """
    sources = "\n".join([original, *evidence_texts])
    reasons: list[dict[str, Any]] = []
    new_numbers = sorted(number_parts(suggested) - number_parts(sources))
    if new_numbers:
        reasons.append({"code": "number", "values": new_numbers})
    new_lead = sorted(lead_terms(suggested) - lead_terms(sources))
    if new_lead:
        reasons.append({"code": "role", "values": new_lead})
    orgs = {o for o in known_organizations if o and len(o) >= 2 and o in suggested}
    orgs |= organization_names(suggested)
    new_orgs = sorted(o for o in orgs if o not in sources)
    if new_orgs:
        reasons.append({"code": "organization", "values": new_orgs})
    # 같은 기술의 다른 표기("파이썬" → "Python")는 새 기술이 아니다
    src_latin = set(latin_terms(sources)) | set(tech_terms(sources))
    new_terms = sorted(
        t
        for t in set(latin_terms(suggested))
        if t not in src_latin and canonical_tech(t) not in src_latin
    )
    if new_terms:
        reasons.append({"code": "new_term", "values": new_terms})
    return reasons


# --- 문서 검사 ---------------------------------------------------------------------

DEFAULT_CLICHES = (
    "열정",
    "최선을 다",
    "책임감",
    "성실",
    "도전 정신",
    "도전정신",
    "소통 능력",
    "소통능력",
    "글로벌 인재",
    "인재가 되겠",
    "일원이 되",
    "시너지",
    "어릴 때부터",
    "어릴 적부터",
    "꿈꿔",
    "비전에 공감",
    "무한한 가능성",
    "기여하고 싶",
    "성장하고 싶",
    "함께 성장",
    "많은 것을 배",
    "귀사",
    "뽑아 주신다면",
    "뽑아주신다면",
)
TARGET_WORDS = ("귀사", "당사", "회사", "기업", "이곳", "이 회사")


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    """브레인의 주장. text는 주장 문장 + 경험 정보(제목·조직·역할·기간)."""

    id: str
    text: str
    confirmed: bool
    contribution: str = "unknown"


@dataclass(frozen=True, slots=True)
class SpanInput:
    """문장 단위 근거: 블록 글의 [start, end)에 연결된 주장."""

    start: int
    end: int
    claims: tuple[EvidenceClaim, ...] = ()


@dataclass(frozen=True, slots=True)
class BlockInput:
    id: str
    text: str
    origin: str = BlockOrigin.USER
    claims: tuple[EvidenceClaim, ...] = ()
    # 있으면 문장마다 그 문장에 걸친 span의 근거만 본다. 없으면 문단(claims) 단위.
    spans: tuple[SpanInput, ...] = ()

    def sentence_claims(self, start: int, end: int) -> tuple[EvidenceClaim, ...]:
        if not self.spans:
            return self.claims
        out: dict[str, EvidenceClaim] = {}
        for sp in self.spans:
            if sp.start < end and sp.end > start:
                for c in sp.claims:
                    out.setdefault(c.id, c)
        return tuple(out.values())


@dataclass(frozen=True, slots=True)
class CheckContext:
    company: str | None = None
    job_title: str | None = None
    # 공고 요건: (kind, 문장). kind는 requirements | preferred
    requirements: tuple[tuple[str, str], ...] = ()
    # 담당 업무 같은 공고 고유 내용. 회사 바꿔치기 시험에서 "구체적"으로 본다
    posting_texts: tuple[str, ...] = ()
    other_companies: tuple[str, ...] = ()
    cliches: tuple[str, ...] = DEFAULT_CLICHES
    # 공간의 확인된 주장 전체. 요건 가시성의 "브레인 근거"
    brain: tuple[EvidenceClaim, ...] = ()
    # 기술명 별칭 표 (기본 표 + 공간 설정, c0021). None이면 기본 표
    aliases: TechAliases | None = None


def _posting_terms(ctx: CheckContext) -> set[str]:
    out: set[str] = set()
    for text in [*(t for _, t in ctx.requirements), *ctx.posting_texts]:
        out |= set(latin_terms(text)) | set(korean_terms(text))
    return out


def _is_specific(
    sentence: str, ctx: CheckContext, posting_terms: set[str], claim_text: str
) -> bool:
    s = sentence
    for name in (ctx.company, ctx.job_title):
        if name:
            s = s.replace(name, " ")
    if re.search(r"\d", s) or latin_terms(s) or re.search(r"[\"“'‘「『]", s):
        return True
    words = set(korean_terms(s))
    if words & posting_terms:
        return True
    return bool(claim_text and words & set(korean_terms(claim_text)))


def _mentions_target(sentence: str, ctx: CheckContext) -> bool:
    names = [n for n in (ctx.company, ctx.job_title) if n]
    return any(n in sentence for n in names) or any(w in sentence for w in TARGET_WORDS)


def requirement_visibility(ctx: CheckContext, blocks: Sequence[BlockInput]) -> list[dict[str, Any]]:
    """`_requirement_visibility`를 문맥의 별칭 표(`ctx.aliases`)로."""
    with use_aliases(ctx.aliases):
        return _requirement_visibility(ctx, blocks)


def _requirement_visibility(
    ctx: CheckContext, blocks: Sequence[BlockInput]
) -> list[dict[str, Any]]:
    """공고 요건 × 문서 × 브레인 (§11 요건 가시성 검사).

    | 브레인 근거 | 문서에 보임 | 판정 |
    | 있음 | 보임 | met_visible | / | 있음 | 안 보임 | missing | /
    | 없음 | 주장함 | overclaim_risk | / | 없음 | 없음 | unmet |
    판정 값은 "근거가 문서에 보이는가"이지 합격 가능성이 아니다.
    """
    text = "\n".join(b.text for b in blocks)
    out = []
    for kind, req in ctx.requirements:
        terms = key_terms(req)
        if not terms.words:
            out.append(
                {
                    "kind": kind,
                    "requirement": req,
                    "terms": [],
                    "verdict": "not_checkable",
                    "brain_claim_ids": [],
                    "document_terms": [],
                    "linked": False,
                    "suggestion": "단어로 판정할 수 없는 요건입니다. 직접 확인합니다.",
                }
            )
            continue
        brain_ids = [c.id for c in ctx.brain if c.confirmed and matched_terms(c.text, terms)]
        doc_terms = matched_terms(text, terms)
        # 기술명 표기가 공고와 다름 ("Python" 요건, 문서엔 "파이썬"만): 실제 쓴 기술이면 공고 표기 병기를 제안
        spelling = []
        if terms.latin:
            for w in doc_terms:
                posted = found_spellings(req, w)
                written = found_spellings(text, w)
                if posted and not {p.lower() for p in posted} & {x.lower() for x in written}:
                    spelling.append({"term": w, "posting": posted[0], "document": written})
        linked = any(
            matched_terms(b.text, terms)
            and any(c.confirmed and matched_terms(c.text, terms) for c in b.claims)
            for b in blocks
        )
        if brain_ids and doc_terms:
            verdict, tip = "met_visible", "유지합니다."
            if spelling:
                names = ", ".join(f"'{x['posting']}'" for x in spelling)
                tip = f"유지합니다. 공고 표기({names})를 같이 적으면 사람·기계 모두 찾기 쉽습니다."
        elif brain_ids:
            verdict, tip = "missing", "브레인에 근거가 있습니다. 해당 경험을 문서에 드러냅니다."
        elif doc_terms:
            verdict, tip = (
                "overclaim_risk",
                "브레인에 근거가 없습니다. 근거를 추가하거나 문장을 뺍니다.",
            )
        else:
            verdict, tip = "unmet", "보완 학습이나 지원 재검토를 고려합니다."
        out.append(
            {
                "kind": kind,
                "requirement": req,
                "terms": list(terms.words),
                "verdict": verdict,
                "brain_claim_ids": brain_ids[:20],
                "document_terms": doc_terms,
                "linked": linked,
                "spelling_differs": spelling,
                "suggestion": tip,
            }
        )
    return out


def check_document(ctx: CheckContext, blocks: Sequence[BlockInput]) -> dict[str, Any]:
    """`_check_document`를 문맥의 별칭 표(`ctx.aliases`)로."""
    with use_aliases(ctx.aliases):
        return _check_document(ctx, blocks)


def _check_document(ctx: CheckContext, blocks: Sequence[BlockInput]) -> dict[str, Any]:
    """문서 한 벌 검사. {"findings": [...], "requirements": [...], "summary": {code: n}}"""
    findings: list[Finding] = []
    posting_terms = _posting_terms(ctx)
    brain_text = "\n".join(c.text for c in ctx.brain if c.confirmed)
    unsupported_terms: set[str] = set()
    for _, req in ctx.requirements:
        terms = key_terms(req)
        if terms.latin and not matched_terms(brain_text, terms):
            unsupported_terms |= set(terms.words)
    own_orgs_text = brain_text
    others = [
        o
        for o in ctx.other_companies
        if o
        and len(o) >= 2
        and o != ctx.company
        and o not in own_orgs_text
        and not (ctx.company and o in ctx.company)
    ]
    for b in blocks:
        confirmed = [c for c in b.claims if c.confirmed]
        claim_text = "\n".join(c.text for c in confirmed)
        if b.claims and not confirmed:
            findings.append(
                Finding(
                    "evidence_unconfirmed",
                    "연결한 주장이 아직 확인되지 않았습니다. 확인 전에는 근거로 보지 않습니다.",
                    b.id,
                    detail={"claim_ids": [c.id for c in b.claims]},
                )
            )
        sentences = sentence_ranges(b.text)
        specific_block = False
        level = "sentence" if b.spans else "paragraph"
        block_confirmed, block_claim_text = confirmed, claim_text
        for start, end, s in sentences:
            if b.spans:
                # 문장 단위 근거: 이 문장에 걸친 span의 확인된 주장만
                confirmed = [c for c in b.sentence_claims(start, end) if c.confirmed]
                claim_text = "\n".join(c.text for c in confirmed)
            specific = _is_specific(s, ctx, posting_terms, block_claim_text)
            specific_block = specific_block or specific
            if _mentions_target(s, ctx) and not specific:
                findings.append(
                    Finding(
                        "generic_company_swap",
                        "회사명·직무명을 다른 것으로 바꿔도 그대로 성립하는 문장입니다.",
                        b.id,
                        s,
                    )
                )
            hits = [c for c in ctx.cliches if c and c in s]
            if hits:
                findings.append(
                    Finding(
                        "cliche",
                        "상투적인 표현입니다. 실제 행동·결과로 바꿀 수 있는지 봅니다.",
                        b.id,
                        s,
                        {"phrases": hits},
                        severity="info",
                    )
                )
            named = [o for o in others if o in s]
            if named:
                findings.append(
                    Finding(
                        "other_company_name",
                        "다른 회사 이름이 남아 있습니다. 이전 지원 문서에서 옮긴 문장인지 확인합니다.",
                        b.id,
                        s,
                        {"companies": named},
                    )
                )
            nums = number_parts(s)
            experience = is_experience_statement(s)
            if (nums or experience) and not confirmed:
                findings.append(
                    Finding(
                        "unsupported_statement",
                        "경험·수치를 말하지만 연결된 브레인 근거가 없습니다. "
                        "근거(확인된 주장)를 연결하거나 문장을 고칩니다.",
                        b.id,
                        s,
                        {
                            "kinds": [
                                k for k, v in (("experience", experience), ("number", nums)) if v
                            ],
                            # sentence: 문장 단위 근거(spans)로 판정 / paragraph: 문단 근거로 판정
                            "evidence_level": level,
                        },
                    )
                )
                continue
            if nums and confirmed:
                missing = sorted(nums - number_parts(claim_text))
                if missing:
                    findings.append(
                        Finding(
                            "number_not_in_evidence",
                            "연결한 근거에 없는 숫자가 있습니다.",
                            b.id,
                            s,
                            {"numbers": missing},
                        )
                    )
            if confirmed:
                lead = lead_terms(s) - lead_terms(claim_text)
                if lead:
                    findings.append(
                        Finding(
                            "role_not_in_evidence",
                            "근거에는 없는 주도·총괄 같은 역할 표현이 있습니다. 실제 역할과 맞는지 봅니다.",
                            b.id,
                            s,
                            {"terms": sorted(lead)},
                        )
                    )
        if sentences and not specific_block and not block_confirmed:
            findings.append(
                Finding(
                    "paragraph_not_specific",
                    "고유명사·수치·결정 이유·본인 행동이 없는 문단입니다.",
                    b.id,
                    severity="info",
                )
            )
        findings.extend(
            ats_findings(
                b.text, block_id=b.id, unsupported_terms=unsupported_terms, repetition=False
            )
        )
    # 키워드 반복은 문서 전체로 본다.
    findings.extend(
        f for f in ats_findings("\n".join(b.text for b in blocks)) if f.code == "keyword_repetition"
    )
    summary = Counter(f.code for f in findings)
    return {
        "findings": [f.as_dict() for f in findings],
        "requirements": requirement_visibility(ctx, blocks),
        "summary": dict(sorted(summary.items())),
    }

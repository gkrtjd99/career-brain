"""자격·이수 기록 (c0034, plan §20)과 증빙 글에서 항목 뽑기. 순수 코드.

뽑기는 **로컬 규칙만** 쓴다. 증빙은 자료 등급이 늘 ``restricted``라 모델·외부 OCR로 보내지 않는다. 사진 파일은
로컬 파서가 글을 읽지 않으므로(``adapters.parsers.ImageParser``) 뽑을 것이 없고 사람이 칸을 채운다.

규칙:

- 채운 칸마다 원문 위치(블록 순번·시작·끝)가 있다. 위치를 댈 수 없는 값은 채우지 않는다.
- 날짜는 날까지 적힌 것만 읽는다 (``2024.05.26``, ``2024-05-26``, ``2024년 5월 26일``, ``May 26, 2024``).
  달까지만 적혔으면 비운다.
- 만료일은 원문에 "유효기간·만료일·Valid until"이 적혀 있을 때만. 어학 성적이 "보통 2년"이어도 계산하지 않는다.
- 주민등록번호로 보이는 값은 어떤 칸에도 넣지 않는다 (``has_resident_number``로 따로 표시만 한다).
- 서류 종류(자격증·어학·수료·수상·학력·재직/경력)는 글에 나오는 낱말로 정한다. 이름을 찾지 못하면 후보를
  만들지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from career_core.domain.sensitive import _RRN, sensitive_findings


class CredentialKind(StrEnum):
    LICENSE = "license"  # 자격증
    LANGUAGE = "language"  # 어학
    COURSE = "course"  # 수료
    AWARD = "award"  # 수상
    DEGREE = "degree"  # 학력
    OTHER = "other"  # 재직·경력 증명 등


class CredentialStatus(StrEnum):
    EXTRACTED = "extracted"  # 증빙 파일에서 뽑음 (사람이 확인 전)
    SELF_CONFIRMED = "self_confirmed"  # 사람이 확인 (증빙 없음)
    EVIDENCE_SUPPORTED = "evidence_supported"  # 사람이 확인 + 증빙 파일 연결


class CredentialRuleError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


MAX_NAME = 200
MAX_ISSUER = 200
MAX_NUMBER = 100
MAX_SCORE = 100
MAX_DETAIL = 2000
MAX_HOURS = 100_000

# 칸 이름 (``field_spans``의 키). detail은 뽑지 않는다 (사람이 쓰는 칸).
TEXT_FIELDS = ("name", "issuer", "number", "score")
DATE_FIELDS = ("acquired_on", "expires_on", "started_on", "ended_on")
SPAN_FIELDS = (*TEXT_FIELDS, *DATE_FIELDS, "hours")


def credential_kind(value: str) -> CredentialKind:
    try:
        return CredentialKind(value)
    except ValueError as exc:
        raise CredentialRuleError("invalid_kind") from exc


def mask_number(number: str | None) -> str | None:
    """끝 4자리만 보인다 (4자 이하면 모두 가린다). 길이는 그대로 드러내지 않게 앞은 ``•`` 네 개로."""
    if not number:
        return number
    tail = number[-4:] if len(number) > 4 else ""
    return "••••" + tail


def expiry_state(expires_on: date | None, today: date, soon_days: int = 90) -> str | None:
    """``expired`` (지남) | ``soon`` (``soon_days`` 안) | None."""
    if expires_on is None:
        return None
    if expires_on < today:
        return "expired"
    if (expires_on - today).days <= soon_days:
        return "soon"
    return None


# ---------------------------------------------------------------- 뽑기 결과


@dataclass(frozen=True, slots=True)
class TextBlock:
    ordinal: int
    text: str
    page: int | None = None


@dataclass(frozen=True, slots=True)
class FieldSpan:
    """원문 위치: 파싱 실행의 블록 순번과 그 블록 글 안의 [start, end)."""

    block: int
    start: int
    end: int
    page: int | None = None

    def as_dict(self) -> dict:
        out = {"block": self.block, "start": self.start, "end": self.end}
        if self.page is not None:
            out["page"] = self.page
        return out


@dataclass(frozen=True, slots=True)
class FieldValue:
    value: str | int | date
    span: FieldSpan


@dataclass(frozen=True, slots=True)
class CredentialDraft:
    kind: CredentialKind
    fields: dict[str, FieldValue] = field(default_factory=dict)

    def value(self, name: str):
        f = self.fields.get(name)
        return f.value if f else None


# ---------------------------------------------------------------- 글 줄


@dataclass(frozen=True, slots=True)
class _Line:
    block: TextBlock
    start: int  # 블록 글 안에서 줄의 시작
    text: str


def _lines(blocks: Sequence[TextBlock]) -> list[_Line]:
    out: list[_Line] = []
    for b in blocks:
        for m in re.finditer(r"[^\n]+", b.text):
            if m.group(0).strip():
                out.append(_Line(b, m.start(), m.group(0)))
    return out


# 낱말 앞이 한글·영문이 아닐 때만 이름표로 본다 ("주민등록번호"의 "등록번호"는 이름표가 아니다)
_PRE = r"(?<![가-힣A-Za-z])"
_SEP = r"[\s:：|]*"


def _label(pattern: str) -> re.Pattern[str]:
    return re.compile(_PRE + r"(?:" + pattern + r")" + r"(?![가-힣])" + _SEP, re.IGNORECASE)


# ---------------------------------------------------------------- 값 모양

_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
    )
}
_DATE_NUM = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*(?:[./-]|년)\s*(0?[1-9]|1[0-2])\s*(?:[./-]|월)\s*"
    r"(0?[1-9]|[12]\d|3[01])(?!\d)(?:\s*일|\s*\.)?"
)
_DATE_EN_MDY = re.compile(
    r"(?<![A-Za-z])(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+"
    r"(0?[1-9]|[12]\d|3[01]),?\s+((?:19|20)\d{2})(?!\d)",
    re.IGNORECASE,
)
_DATE_EN_DMY = re.compile(
    r"(?<!\d)(0?[1-9]|[12]\d|3[01])\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"[a-z]*\.?,?\s+((?:19|20)\d{2})(?!\d)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _Found:
    start: int  # 줄 안 위치
    end: int
    value: str | int | date


def _dates(text: str) -> list[_Found]:
    out: list[_Found] = []
    for m in _DATE_NUM.finditer(text):
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        out.append(_Found(m.start(), m.end(), d))
    for rx, order in ((_DATE_EN_MDY, "mdy"), (_DATE_EN_DMY, "dmy")):
        for m in rx.finditer(text):
            if order == "mdy":
                mon, day, year = m.group(1), m.group(2), m.group(3)
            else:
                day, mon, year = m.group(1), m.group(2), m.group(3)
            try:
                d = date(int(year), _MONTHS[mon[:3].lower()], int(day))
            except (ValueError, KeyError):
                continue
            out.append(_Found(m.start(), m.end(), d))
    out.sort(key=lambda f: f.start)
    return out


# 주민등록번호 모양 (domain.sensitive와 같은 규칙): 어떤 칸에도 넣지 않는다
_RRN_LIKE = _RRN
# 이름표가 이런 것이면 그 줄에서 값을 읽지 않는다
_PERSONAL_LABEL = re.compile(
    r"주민|생년|생\s*일|전화|휴대|연락처|주\s*소|성\s*명|이\s*름|e-?mail", re.IGNORECASE
)


def _cut_text(tail: str) -> tuple[int, int] | None:
    """이름표 뒤 글에서 글 값의 범위: ``|``·탭·두 칸 공백·다음 이름표 앞에서 끊고 끝 문장부호를 뺀다."""
    m = re.match(r"\s*", tail)
    start = m.end() if m else 0
    end = len(tail)
    for stop in (re.compile(r"\s*\|"), re.compile(r"\t| {2,}"), _ANY_LABEL):
        hit = stop.search(tail, start + 1)
        if hit and hit.start() < end:
            end = hit.start()
    value = tail[start:end]
    stripped = value.rstrip(" .,;·)(]")
    # 여는 괄호만 남고 닫는 괄호를 뺀 경우는 되돌린다 ("(주)에이크미")
    if stripped.count("(") > stripped.count(")") and value[len(stripped) :].startswith(")"):
        stripped = value[: len(stripped) + 1]
    end = start + len(stripped)
    if end <= start:
        return None
    return start, end


# ---------------------------------------------------------------- 서류 종류

_TEST_NAME = re.compile(
    r"TOEIC\s*Speaking(?:\s*(?:&|and)\s*Writing)?|TOEIC\s*Writing|TOEIC|토익\s*스피킹|토익|"
    r"OPIc|오픽|TOEFL(?:\s*iBT)?|토플|TEPS|텝스|IELTS|JLPT|JPT|新?HSK|G-?TELP|지텔프",
    re.IGNORECASE,
)
_KIND_WORDS: tuple[tuple[CredentialKind, re.Pattern[str]], ...] = (
    (
        CredentialKind.DEGREE,
        re.compile(r"졸업\s*(?:예정\s*)?증명서|학위\s*증명서|학위를\s*수여|졸업하였"),
    ),
    (CredentialKind.OTHER, re.compile(r"재직\s*증명서|경력\s*증명서|재직하고\s*있|근무하였")),
    (
        CredentialKind.COURSE,
        re.compile(r"수료증|수료\s*증명|이수증|수료하였|이수하였|(?:과정|교육)을\s*(?:수료|이수)"),
    ),
    (CredentialKind.AWARD, re.compile(r"상\s*장|표\s*창\s*장|수상하였|시상합니다|입상하였")),
    (CredentialKind.LANGUAGE, _TEST_NAME),
    (
        CredentialKind.LICENSE,
        re.compile(
            r"자격증|자격\s*번호|자격\s*종목|국가\s*기술\s*자격|국가\s*공인|민간\s*자격|"
            r"자격을\s*취득|시험에\s*(?:최종\s*)?합격"
        ),
    ),
)
# 이름을 못 찾았을 때 쓰는 서류 제목 낱말 (그 낱말의 위치가 원문 위치)
_TITLE_WORDS: dict[CredentialKind, re.Pattern[str]] = {
    CredentialKind.COURSE: re.compile(r"수료증|이수증"),
    CredentialKind.DEGREE: re.compile(r"졸업\s*(?:예정\s*)?증명서|학위\s*증명서"),
    CredentialKind.OTHER: re.compile(r"재직\s*증명서|경력\s*증명서"),
    CredentialKind.AWARD: re.compile(r"표\s*창\s*장|상\s*장"),
}


def detect_kind(text: str) -> CredentialKind | None:
    for kind, rx in _KIND_WORDS:
        if rx.search(text):
            return kind
    return None


# ---------------------------------------------------------------- 이름표

_L_NAME = {
    CredentialKind.LICENSE: _label(
        r"자격\s*종목|종목\s*및\s*등급|자격\s*명|자격\s*등급|자격\s*종류|종\s*목\s*명|종\s*목"
    ),
    CredentialKind.COURSE: _label(
        r"과\s*정\s*명|교육\s*과정\s*명|교육\s*과정|교\s*육\s*명|프로그램\s*명|과\s*목\s*명|연수\s*명|과\s*정"
    ),
    CredentialKind.AWARD: _label(r"대\s*회\s*명|공모전\s*명|행\s*사\s*명|경진\s*대회\s*명"),
    CredentialKind.DEGREE: _label(r"학\s*위\s*명|학\s*위\s*종\s*류|학\s*위|전\s*공|학\s*과"),
}
_L_AWARD_GRADE = _label(r"수\s*상\s*명|상\s*명|훈\s*격|수\s*상\s*내\s*역")
_AWARD_WORD = re.compile(
    r"(?<![가-힣])(?:최우수상|우수상|장려상|대상|금상|은상|동상|특별상|입선|공로상)(?![가-힣])"
)
_LICENSE_SENTENCE = re.compile(
    r"([가-힣A-Za-z0-9]{2,30}(?:\s?[1-3]급)?)\s*(?:자격|시험)(?:에|을|를|의)?\s*(?:최종\s*)?(?:합격|취득)"
)
_LICENSE_STOP = frozenset({"위", "국가기술", "국가", "해당", "상기", "다음", "본", "이"})

_L_ISSUER = _label(
    r"발급\s*기관|발행\s*기관|시행\s*기관|교육\s*기관|주관\s*기관|수여\s*기관|운영\s*기관|기\s*관\s*명|"
    r"학\s*교\s*명|회\s*사\s*명|법\s*인\s*명|상\s*호|주\s*최|주\s*관|Issued\s*by|Issuer"
)
# 서명 줄: "한국산업인력공단 이사장", "○○대학교 총장", "주식회사 에이크미 대표이사 홍길동 (인)"
_SIGNATURE = re.compile(
    r"^\s*(?P<v>[가-힣A-Za-z0-9()·&.\- ]{2,40}?)\s+"
    r"(?:이사장|총장|원장|회장|대표이사|대표|위원장|센터장|학장|교육감|장관|청장|교육원장)"
    r"(?:\s+[가-힣]{2,4})?(?:\s*(?:\(인\)|\[인\]|직인|인))?\s*$"
)
KNOWN_ISSUERS = (
    "한국산업인력공단",
    "대한상공회의소",
    "한국데이터산업진흥원",
    "한국인터넷진흥원",
    "한국정보통신진흥협회",
    "한국생산성본부",
    "국사편찬위원회",
    "금융투자협회",
    "한국금융연수원",
    "한국TOEIC위원회",
    "한국토익위원회",
    "ETS",
    "YBM",
    "ACTFL",
    "일본국제교류기금",
    "일본국제교육지원협회",
    "HSK한국사무국",
    "British Council",
)
_KNOWN_ISSUER = re.compile(
    r"(?<![가-힣A-Za-z])(?:" + "|".join(re.escape(x) for x in KNOWN_ISSUERS) + r")(?![A-Za-z])"
)

_L_NUMBER = _label(
    r"자격\s*번호|자격증\s*번호|등록\s*번호|증명서?\s*번호|발급\s*번호|관리\s*번호|인증\s*번호|문서\s*번호|"
    r"성적표\s*번호|수료\s*번호|증서\s*번호|수험\s*번호|Registration\s*(?:No\.?|Number)|"
    r"Certificate\s*(?:No\.?|Number)|Cert\.?\s*No\.?"
)
_NUMBER_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]{2,40}")
_NUMBER_JE = re.compile(r"(?<![가-힣])제\s*([A-Za-z0-9][A-Za-z0-9\-]{2,30})\s*호(?![가-힣])")

_L_ACQUIRED = {
    CredentialKind.LICENSE: _label(
        r"(?:최종\s*)?합격\s*(?:연월일|년월일|일자|일)|(?:자격\s*)?취득\s*(?:연월일|년월일|일자|일)"
    ),
    CredentialKind.LANGUAGE: _label(
        r"시험\s*(?:일자|일|날짜)|응시\s*(?:일자|일)|시행\s*(?:일자|일)|Test\s*Date|Exam\s*Date|Date\s*of\s*Test"
    ),
    CredentialKind.COURSE: _label(r"수료\s*(?:연월일|일자|일)|이수\s*(?:일자|일)"),
    CredentialKind.AWARD: _label(r"수상\s*(?:일자|일)|시상\s*(?:일자|일)|수여\s*(?:일자|일)"),
    CredentialKind.DEGREE: _label(r"졸업\s*(?:연월일|년월일|일자|일)|학위\s*수여\s*(?:일자|일)"),
}
_L_EXPIRES = _label(
    r"(?:성적\s*|자격\s*)?유효\s*기간|유효\s*기한|만료\s*(?:일자|일)|갱신\s*기한|"
    r"Valid\s*(?:Until|Through|Thru)|Expir(?:y|ation)\s*Date"
)
_L_PERIOD = {
    CredentialKind.COURSE: _label(
        r"교육\s*기간|과정\s*기간|연수\s*기간|훈련\s*기간|수강\s*기간|기\s*간"
    ),
    CredentialKind.DEGREE: _label(r"재학\s*기간|수학\s*기간"),
    CredentialKind.OTHER: _label(r"재직\s*기간|근무\s*기간|경력\s*기간|기\s*간"),
}
_L_START = _label(r"입학\s*(?:연월일|년월일|일자|일)|입사\s*(?:일자|일)")
_L_END = _label(r"퇴사\s*(?:일자|일)")
_RANGE_SEP = re.compile(r"\s*(?:~|∼|〜|–|—|-|부터)\s*")
_ONGOING = re.compile(r"\s*(?:~|∼|〜|–|—|-|부터)\s*(?:현재|재직\s*중|재학\s*중)")

_L_HOURS = _label(r"(?:총\s*)?(?:교육|이수|수료|학습|연수|훈련)\s*시간|총\s*시간")
_HOURS_VALUE = re.compile(r"(?:총\s*)?(\d{1,5})\s*시간")
_HOURS_ANY = re.compile(r"(?<![가-힣\d])총\s*(\d{1,5})\s*시간")

_L_TOTAL = _label(r"Total\s*Score|Total|총\s*점|합\s*계|총\s*득\s*점")
_L_SCORE = _label(r"점\s*수|성\s*적|Score")
_L_GRADE = _label(r"등\s*급|급\s*수|레\s*벨|Level|Grade|Rating|Result")
_NUM_SCORE = re.compile(r"\d{1,3}(?:\.\d)?(?!\d)")
_GRADE = re.compile(
    r"(?:AL|IH|IM\s?[1-3]|IM|IL|NH|NM|NL|N[1-5]|[1-9]\s*급|Level\s*[1-8]|Lv\.?\s*[1-8]|"
    r"Band\s*\d(?:\.5)?|[A-C][12])(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_INLINE_GRADE = re.compile(
    r"(?:JLPT|JPT|新?HSK|OPIc|오픽)\s*[:：]?\s*(N[1-5]|[1-6]\s*급|AL|IH|IM\s?[1-3]|IM|IL|NH|NM|NL)"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# 글 칸 값을 끊는 "다음 이름표" (모든 이름표를 합친 것)
_ANY_LABEL = re.compile(
    r"\s(?="
    + "|".join(
        p.pattern.removeprefix(_PRE).removesuffix(_SEP)
        for p in (
            *_L_NAME.values(),
            _L_AWARD_GRADE,
            _L_ISSUER,
            _L_NUMBER,
            *_L_ACQUIRED.values(),
            _L_EXPIRES,
            *_L_PERIOD.values(),
            _L_START,
            _L_END,
            _L_HOURS,
            _L_TOTAL,
            _L_SCORE,
            _L_GRADE,
        )
    )
    + r"|성\s*명|생년월일|주민)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------- 찾기


def _value_after(lines: list[_Line], label: re.Pattern[str], parse) -> Iterator[FieldValue]:
    """이름표 뒤(같은 줄, 비었으면 다음 줄)의 값. ``parse(tail) -> _Found | None`` (tail 안 위치)."""
    for i, line in enumerate(lines):
        for m in label.finditer(line.text):
            before = line.text[: m.start()]
            if _PERSONAL_LABEL.search(before[-6:]):
                continue
            tail = line.text[m.end() :]
            target, base = line, line.start + m.end()
            if not tail.strip() and i + 1 < len(lines):
                target = lines[i + 1]
                tail, base = target.text, target.start
            found = parse(tail)
            if found is None:
                continue
            yield FieldValue(
                found.value,
                FieldSpan(
                    target.block.ordinal, base + found.start, base + found.end, target.block.page
                ),
            )


def _first(it: Iterable[FieldValue]) -> FieldValue | None:
    return next(iter(it), None)


def _text_parser(min_len: int = 2, max_len: int = 100):
    def parse(tail: str) -> _Found | None:
        cut = _cut_text(tail)
        if cut is None:
            return None
        start, end = cut
        value = tail[start:end]
        if not (min_len <= len(value) <= max_len) or _dates(value) or _RRN_LIKE.search(value):
            return None
        if not re.search(r"[가-힣A-Za-z]", value):
            return None
        return _Found(start, end, value)

    return parse


def _number_parser(tail: str) -> _Found | None:
    m = re.match(r"\s*(?:제\s*)?", tail)
    m2 = _NUMBER_VALUE.match(tail, m.end() if m else 0)
    if not m2:
        return None
    value = m2.group(0).rstrip("-")
    if not re.search(r"\d", value) or _RRN_LIKE.search(value) or _dates(value):
        return None
    return _Found(m2.start(), m2.start() + len(value), value)


def _date_parser(which: str = "first"):
    def parse(tail: str) -> _Found | None:
        cut = _until_next_field(tail)
        found = _dates(cut)
        if not found:
            return None
        # 이름표 바로 뒤에 날짜가 와야 한다 (다른 글이 끼면 그 줄의 다른 칸일 수 있다)
        if which == "first":
            f = found[0]
            if cut[: f.start].strip(" ()[]:：-"):
                return None
            return f
        return found[-1]  # 기간(시작 ~ 끝)의 끝

    return parse


def _until_next_field(tail: str) -> str:
    """같은 줄의 다음 칸(``|`` 또는 다음 이름표) 앞까지."""
    cut = re.split(r"\s*\|", tail, maxsplit=1)[0]
    hit = _ANY_LABEL.search(cut, 1)
    return cut[: hit.start()] if hit else cut


def _period(tail: str) -> tuple[_Found, _Found | None, bool] | None:
    """``DATE ~ DATE`` | ``DATE ~ 현재`` → (시작, 끝|None, 진행 중)."""
    cut = _until_next_field(tail)
    found = _dates(cut)
    if not found or cut[: found[0].start].strip(" ()[]:："):
        return None
    first = found[0]
    rest = cut[first.end :]
    if _ONGOING.match(rest):
        return first, None, True
    if len(found) >= 2 and _RANGE_SEP.fullmatch(cut[first.end : found[1].start]):
        return first, found[1], False
    return None


def _span(line: _Line, start: int, end: int) -> FieldSpan:
    return FieldSpan(line.block.ordinal, line.start + start, line.start + end, line.block.page)


def _search(lines: list[_Line], rx: re.Pattern[str], group: int = 0) -> FieldValue | None:
    for line in lines:
        m = rx.search(line.text)
        if m:
            return FieldValue(m.group(group), _span(line, m.start(group), m.end(group)))
    return None


def _clean(fv: FieldValue | None) -> FieldValue | None:
    """주민등록번호 모양이 섞인 값은 버린다."""
    if fv is None:
        return None
    if isinstance(fv.value, str) and _RRN_LIKE.search(fv.value):
        return None
    return fv


def extract_credential(blocks: Sequence[TextBlock]) -> CredentialDraft | None:
    """증빙 글(블록들)에서 자격·이수 후보 하나. 서류 종류나 이름을 알 수 없으면 None."""
    lines = _lines(blocks)
    if not lines:
        return None
    joined = "\n".join(line.text for line in lines)
    kind = detect_kind(joined)
    if kind is None:
        return None
    fields: dict[str, FieldValue] = {}

    def put(name: str, fv: FieldValue | None) -> None:
        fv = _clean(fv)
        if fv is not None and name not in fields:
            fields[name] = fv

    text = _text_parser()

    # 이름
    if kind == CredentialKind.LANGUAGE:
        put("name", _search(lines, _TEST_NAME))
    elif kind == CredentialKind.AWARD:
        put("name", _first(_value_after(lines, _L_NAME[kind], text)))
        grade = _first(_value_after(lines, _L_AWARD_GRADE, text))
        if "name" in fields:
            put("score", grade or _search(lines, _AWARD_WORD))
        else:
            put("name", grade or _search(lines, _AWARD_WORD))
    elif kind in _L_NAME:
        put("name", _first(_value_after(lines, _L_NAME[kind], text)))
    if "name" not in fields and kind == CredentialKind.LICENSE:
        for line in lines:
            for m in _LICENSE_SENTENCE.finditer(line.text):
                word = m.group(1).strip()
                if word in _LICENSE_STOP or word.endswith(("법", "의하여")):
                    continue
                put("name", FieldValue(word, _span(line, m.start(1), m.start(1) + len(word))))
                break
            if "name" in fields:
                break
    if "name" not in fields and kind in _TITLE_WORDS:
        put("name", _search(lines, _TITLE_WORDS[kind]))
    if "name" not in fields:
        return None

    # 발급 기관
    put("issuer", _first(_value_after(lines, _L_ISSUER, _text_parser(2, MAX_ISSUER))))
    if "issuer" not in fields:
        for line in lines:
            m = _SIGNATURE.match(line.text)
            if m and re.search(r"[가-힣A-Za-z]{2}", m.group("v")):
                v = m.group("v").strip()
                start = line.text.index(v, m.start("v"))
                put("issuer", FieldValue(v, _span(line, start, start + len(v))))
                break
    if "issuer" not in fields:
        put("issuer", _search(lines, _KNOWN_ISSUER))

    # 번호
    put("number", _first(_value_after(lines, _L_NUMBER, _number_parser)))
    if "number" not in fields:
        put("number", _search(lines, _NUMBER_JE, 1))

    # 날짜
    if kind in _L_ACQUIRED:
        put("acquired_on", _first(_value_after(lines, _L_ACQUIRED[kind], _date_parser())))
    put("expires_on", _first(_value_after(lines, _L_EXPIRES, _date_parser("last"))))
    if kind in _L_PERIOD:
        for line in lines:
            done = False
            for m in _L_PERIOD[kind].finditer(line.text):
                got = _period(line.text[m.end() :])
                if got is None:
                    continue
                first, last, _ongoing = got
                base = m.end()
                put(
                    "started_on",
                    FieldValue(first.value, _span(line, base + first.start, base + first.end)),
                )
                if last is not None:
                    put(
                        "ended_on",
                        FieldValue(last.value, _span(line, base + last.start, base + last.end)),
                    )
                done = True
                break
            if done:
                break
    put("started_on", _first(_value_after(lines, _L_START, _date_parser())))
    put("ended_on", _first(_value_after(lines, _L_END, _date_parser())))

    # 점수·등급 (어학), 시간 (수료)
    if kind == CredentialKind.LANGUAGE:
        put("score", _first(_value_after(lines, _L_TOTAL, _match_at_start(_NUM_SCORE))))
        put("score", _first(_value_after(lines, _L_GRADE, _match_at_start(_GRADE))))
        put("score", _search(lines, _INLINE_GRADE, 1))
        put("score", _first(_value_after(lines, _L_SCORE, _match_at_start(_NUM_SCORE))))
    if kind == CredentialKind.COURSE:
        hours = _first(_value_after(lines, _L_HOURS, _match_at_start(_HOURS_VALUE, 1)))
        hours = hours or _search(lines, _HOURS_ANY, 1)
        if hours is not None:
            n = int(hours.value)
            if 1 <= n <= MAX_HOURS:
                fields["hours"] = FieldValue(n, hours.span)

    # 날짜 순서가 맞지 않으면 그 칸을 버린다 (DB CHECK와 같은 규칙)
    a, e = fields.get("acquired_on"), fields.get("expires_on")
    if a and e and e.value < a.value:
        fields.pop("expires_on")
    s, n = fields.get("started_on"), fields.get("ended_on")
    if s and n and n.value < s.value:
        fields.pop("ended_on")
    return CredentialDraft(kind, fields)


def _match_at_start(rx: re.Pattern[str], group: int = 0):
    def parse(tail: str) -> _Found | None:
        lead = re.match(r"\s*", tail)
        m = rx.match(tail, lead.end() if lead else 0)
        if not m:
            return None
        return _Found(m.start(group), m.end(group), m.group(group))

    return parse


def has_resident_number(blocks: Iterable[TextBlock]) -> bool:
    """주민등록번호로 보이는 값이 있나 (§11 민감 정보 검사와 같은 규칙). 값은 돌려주지 않는다."""
    return any(
        f.code == "resident_registration_number" for b in blocks for f in sensitive_findings(b.text)
    )


__all__ = [
    "DATE_FIELDS",
    "KNOWN_ISSUERS",
    "SPAN_FIELDS",
    "TEXT_FIELDS",
    "CredentialDraft",
    "CredentialKind",
    "CredentialRuleError",
    "CredentialStatus",
    "FieldSpan",
    "FieldValue",
    "TextBlock",
    "credential_kind",
    "detect_kind",
    "expiry_state",
    "extract_credential",
    "has_resident_number",
    "mask_number",
]

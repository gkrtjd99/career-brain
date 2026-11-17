"""브레인에 넣기: 넣은 것이 무엇인지 고른다 (plan §9, §20). 순수 코드, 모델을 부르지 않는다.

넣은 것은 증빙일 수 있으므로 무엇인지 정하려고 외부 모델에 보내지 않는다. 파일 이름·형식·앞부분 글(이 기기에서
읽은 글 포함)과 주소만 본다.

- 파일·글 → ``experience``(내 경험 자료) | ``proof``(증빙 서류) | ``reference``(참고 자료)
- 주소 → ``posting``(공고) | ``company``(회사 페이지)
- 확신(``confidence``): ``high``면 바로 보낸다. ``low``면 받은 자료함에서 "확인 필요"로 기다린다
  (제안한 것을 미리 골라 둔다). 확신이 없을 때는 외부로 덜 나가는 쪽(증빙)을 먼저 본다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from career_core.domain.sensitive import _RRN
from career_core.domain.sources import (
    SUGGEST_TEXT_HEAD,
    SourcePurpose,
    suggest_purpose,
)

Confidence = Literal["high", "low"]
HIGH: Confidence = "high"
LOW: Confidence = "low"

# 서류 자체임을 말하는 문장·제목 (이력서 본문에는 잘 안 나온다)
_PROOF_STRONG = (
    "증명서",
    "증명합니다",
    "증명함",
    "확인합니다",
    "위 사람은",
    "위의 사람은",
    "위와 같이",
    "수료하였음",
    "수료하였습니다",
    "이수하였음",
    "합격하였음",
    "취득하였음",
    "재직하고 있음",
    "재직하였음",
    "졸업하였음",
    "수료증",
    "합격증",
    "합격증명",
    "성적증명",
    "성적표",
    "재직증명",
    "경력증명",
    "졸업증명",
    "재학증명",
    "자격증 번호",
    "자격번호",
    "자격증번호",
    "발급번호",
    "증명번호",
    "발급일자",
    "score report",
    "official score",
    "certificate of",
    "this is to certify",
    "has successfully completed",
    "certificate no",
)
# 서류 이름일 수도, 이력서의 한 줄일 수도 있는 낱말: 짧은 글(서류 한 장)에서만 증빙 쪽으로 본다
_PROOF_WEAK = (
    "자격증",
    "toeic",
    "토익",
    "opic",
    "오픽",
    "toefl",
    "teps",
    "ielts",
    "jlpt",
    "hsk",
    "수료",
    "합격",
    "성적",
    "점수",
    "listening",
    "reading",
    "certificate",
)
SHORT_DOC_CHARS = 900  # 서류 한 장 정도의 글

# 남의 글
_REFERENCE_TEXT = (
    "합격 자소서",
    "합격자소서",
    "합격 자기소개서",
    "합격자기소개서",
    "자소서 모음",
    "자기소개서 모음",
    "면접 후기",
    "면접후기",
    "합격 후기",
    "합격후기",
    "전형 후기",
    "선배의",
    "합격자의",
    "출처:",
)
_REFERENCE_WEAK = ("후기", "예시", "샘플", "sample", "합격자")

# 내 경험 자료: 파일 이름
_EXPERIENCE_NAME = (
    "이력서",
    "경력기술서",
    "경력 기술서",
    "포트폴리오",
    "회고",
    "자기소개서",
    "자소서",
    "프로젝트",
    "업무 정리",
    "업무정리",
    "resume",
    "résumé",
    "cv",
    "portfolio",
    "retro",
    "retrospective",
    "project",
)
# 1인칭 서술 (내가 한 일)
_FIRST_PERSON = re.compile(
    r"(저는|제가|나는|내가|저희 팀|담당했|맡았|맡아|기여했|개발했|설계했|구축했|이끌었|개선했|줄였|높였|"
    r"\bI (?:led|built|designed|developed|owned|shipped|improved|reduced)\b|\bmy role\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FileGuess:
    purpose: SourcePurpose
    confidence: Confidence
    # 이유: 걸린 낱말(소문자), ``resident_number``(주민등록번호 모양), ``image``(사진, 글 없음),
    # ``first_person``(내가 한 일을 적은 글), ``default``(걸린 것 없음)
    reason: str
    resident_number: bool = False


def _norm(value: str | None) -> str:
    return " ".join((value or "").casefold().replace("_", " ").replace("-", " ").split())


def _hit(haystack: str, words: tuple[str, ...]) -> str | None:
    return next((w for w in words if w in haystack), None)


def _name_hit(name: str, words: tuple[str, ...]) -> str | None:
    """파일 이름의 낱말. 짧은 영어 낱말(cv)은 낱말 경계에서만."""
    for w in words:
        if len(w) <= 3 and w.isascii():
            if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", name):
                return w
        elif w in name:
            return w
    return None


def has_resident_number(text: str | None) -> bool:
    return bool(text) and bool(_RRN.search(text))


def classify_file(
    filename: str | None, mime: str | None = None, text_head: str | None = None
) -> FileGuess:
    """파일·붙여넣은 글 하나. 순서 (앞의 것이 이긴다):

    1. 글에 주민등록번호 모양 → 증빙 (확신). 남의 글이어도 이 기기 밖으로 보내지 않는 쪽.
    2. 파일 이름의 증빙·참고 낱말 (``domain.sources.suggest_purpose``와 같은 낱말) → 확신.
    3. 글의 서류 문장("증명합니다", "위 사람은", "score report" …) → 증빙 (확신).
    4. 글의 남의 글 낱말("합격 자소서", "면접 후기" …) → 참고 (확신).
    5. 파일 이름의 내 경험 낱말(이력서·포트폴리오·회고 …) → 내 경험 (확신).
    6. 짧은 글(서류 한 장)에 자격·어학 낱말 → 증빙 (확인 필요).
    7. 1인칭 서술이 둘 이상 → 내 경험 (확신), 하나 → 내 경험 (확인 필요).
    8. 글 없는 사진 → 증빙 (확인 필요, ``image``). 글이 있는 사진·문서는 남의 글 약한 낱말 → 참고 (확인 필요).
    9. 나머지 → 내 경험 (확인 필요).
    """
    head = (text_head or "")[: SUGGEST_TEXT_HEAD * 2]
    rrn = has_resident_number(head)
    if rrn:
        return FileGuess(SourcePurpose.PROOF, HIGH, "resident_number", True)

    name = _norm(filename)
    by_name = suggest_purpose(filename, None, None)  # 이름만 (사진·본문 규칙은 아래에서)
    if by_name.matched:
        return FileGuess(by_name.purpose, HIGH, by_name.matched)

    body = _norm(head)
    if hit := _hit(body, _PROOF_STRONG):
        return FileGuess(SourcePurpose.PROOF, HIGH, hit)
    if hit := _hit(body, _REFERENCE_TEXT):
        return FileGuess(SourcePurpose.REFERENCE, HIGH, hit)
    if hit := _name_hit(name, _EXPERIENCE_NAME):
        return FileGuess(SourcePurpose.EXPERIENCE, HIGH, hit)

    short = 0 < len(body) <= SHORT_DOC_CHARS
    first_person = len(_FIRST_PERSON.findall(head))
    if short and first_person == 0 and (hit := _hit(body, _PROOF_WEAK)):
        return FileGuess(SourcePurpose.PROOF, LOW, hit)
    if first_person >= 2:
        return FileGuess(SourcePurpose.EXPERIENCE, HIGH, "first_person")
    if first_person == 1:
        return FileGuess(SourcePurpose.EXPERIENCE, LOW, "first_person")
    if not body and (mime or "").casefold().startswith("image/"):
        return FileGuess(SourcePurpose.PROOF, LOW, "image")
    if hit := _hit(body, _REFERENCE_WEAK):
        return FileGuess(SourcePurpose.REFERENCE, LOW, hit)
    return FileGuess(SourcePurpose.EXPERIENCE, LOW, "default")


# ---------------------------------------------------------------- 주소

UrlKind = Literal["posting", "company"]

# 공고 사이트·채용 관리 도구 (주소만 봐도 공고). 앞의 점은 하위 도메인 포함
_JOB_HOSTS = (
    "saramin.co.kr",
    "jobkorea.co.kr",
    "wanted.co.kr",
    "jumpit.co.kr",
    "jumpit.saramin.co.kr",
    "rememberapp.co.kr",
    "career.rememberapp.co.kr",
    "programmers.co.kr",
    "catch.co.kr",
    "incruit.com",
    "jobplanet.co.kr",
    "linkareer.com",
    "superookie.com",
    "rocketpunch.com",
    "work.go.kr",
    "worknet.go.kr",
    "alio.go.kr",
    "job.alio.go.kr",
    "greenhouse.io",
    "lever.co",
    "myworkdayjobs.com",
    "myworkdaysite.com",
    "ashbyhq.com",
    "smartrecruiters.com",
    "recruitee.com",
    "workable.com",
    "bamboohr.com",
    "teamtailor.com",
    "personio.de",
    "personio.com",
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "greetinghr.com",
    "ninehire.com",
    "recruiter.co.kr",
    "careerlink.co.kr",
    "flex.team",
)
# 목록·검색 페이지는 공고 한 건이 아니다 (공고 사이트의 첫 화면·회사 소개)
_JOB_HOST_NOT_POSTING = re.compile(
    r"^/(?:$|search|company(?:-info)?/?(?:\d+/?)?$|companies|about|login|signup)", re.IGNORECASE
)
_POSTING_PATH = re.compile(
    r"/(?:recruit(?:ment)?s?|jobs?|careers?|career|positions?|openings?|postings?|vacanc(?:y|ies)|"
    r"apply|job-?detail|jobdetail|opening|o|wd|채용|공고|채용공고)/[^/?#]+",
    re.IGNORECASE,
)
_POSTING_QUERY = (
    "rec_idx",
    "gi_no",
    "jobid",
    "job_id",
    "gh_jid",
    "posting_id",
    "jobno",
    "recruit_no",
)
_COMPANY_PATH = re.compile(
    r"^/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:about(?:-us)?|company|companies|corporate|ir|investors?|"
    r"investor-relations|culture|people|team|story|values|vision|mission|introduce|intro|overview|"
    r"who-we-are|our-story|esg|sustainability|news(?:room)?|press|회사소개|기업정보|인재상)(?:/|$|\.)",
    re.IGNORECASE,
)
_CAREERS_LANDING = re.compile(
    r"^/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:careers?|recruit|jobs?)/?$", re.IGNORECASE
)
_HOST_NOISE = re.compile(r"^(?:www|careers?|recruit|jobs?|about|ir|corp|m)\.")


@dataclass(frozen=True, slots=True)
class UrlGuess:
    kind: UrlKind
    confidence: Confidence
    reason: str  # job_host | posting_path | posting_query | company_path | careers_landing | home | default
    host: str  # 소문자, www. 뺌
    company_hint: str | None = None  # 회사 주소면 이름 후보 (호스트의 첫 이름)


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def company_hint_of(host: str) -> str | None:
    """``careers.acme.co.kr`` → ``acme``. 공고 사이트·IP면 None."""
    if (
        not host
        or re.fullmatch(r"[\d.:]+", host)
        or any(_host_matches(host, h) for h in _JOB_HOSTS)
    ):
        return None
    h = host
    while _HOST_NOISE.match(h):
        h = _HOST_NOISE.sub("", h, count=1)
    parts = h.split(".")
    # 국가 도메인의 2단계 (co.kr, or.kr, com.au …)
    if len(parts) >= 3 and parts[-2] in ("co", "or", "ac", "go", "ne", "com", "org", "net"):
        parts = parts[:-2]
    elif len(parts) >= 2:
        parts = parts[:-1]
    return parts[-1] if parts and parts[-1] else None


def normalize_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold()
    return host.removeprefix("www.")


def classify_url(url: str) -> UrlGuess:
    """주소 하나. 순서:

    1. 공고 사이트·채용 관리 도구 호스트 → 공고 (확신). 그 사이트의 첫 화면·검색·회사 소개는 공고가 아니다.
    2. 공고 한 건의 경로(``/recruit/…``, ``/jobs/…``, ``/careers/…``)나 질의(``rec_idx``) → 공고 (확신).
    3. 회사 소개 경로(``/about``, ``/company``, ``/ir``, ``/culture`` …) → 회사 페이지 (확신).
    4. 채용 첫 화면(``/careers``)·홈 → 회사 페이지 (확인 필요).
    5. 나머지 → 공고 (확인 필요): 하나씩 붙여 넣는 주소는 대개 공고다.
    """
    parts = urlsplit(url.strip())
    host = normalize_host(url)
    path = parts.path or "/"
    query = {k.casefold() for k in parse_qs(parts.query)}
    hint = company_hint_of(host)
    on_job_host = any(_host_matches(host, h) for h in _JOB_HOSTS)
    if on_job_host:
        if _JOB_HOST_NOT_POSTING.match(path) and not (query & set(_POSTING_QUERY)):
            return UrlGuess("posting", LOW, "job_host_listing", host, None)
        return UrlGuess("posting", HIGH, "job_host", host, None)
    if query & set(_POSTING_QUERY):
        return UrlGuess("posting", HIGH, "posting_query", host, hint)
    if _POSTING_PATH.search(path):
        return UrlGuess("posting", HIGH, "posting_path", host, hint)
    if _COMPANY_PATH.match(path):
        return UrlGuess("company", HIGH, "company_path", host, hint)
    if _CAREERS_LANDING.match(path):
        return UrlGuess("company", LOW, "careers_landing", host, hint)
    if path in ("", "/"):
        return UrlGuess("company", LOW, "home", host, hint)
    return UrlGuess("posting", LOW, "default", host, hint)


# ---------------------------------------------------------------- 읽은 글 (text layer)

# 이 기기(브라우저)에서 읽은 글을 원본에 붙일 때의 상한
MAX_LAYER_BLOCKS = 2000
MAX_BLOCK_CHARS = 20_000
MAX_LAYER_CHARS = 200_000
MAX_LAYER_PAGES = 300  # adapters.parsers.MAX_PDF_PAGES와 같다
MAX_BBOX = 100_000
MAX_ENGINE_CHARS = 100
# 글을 붙일 수 있는 형식: 글자를 이 서버가 못 읽는 것 (사진, 스캔 PDF)
TEXT_LAYER_MIMES = frozenset({"image/png", "image/jpeg", "application/pdf"})
RESIDENT_MASK = "******-*******"


class TextLayerError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class LayerBlock:
    page: int
    text: str
    bbox: tuple[int, int, int, int] | None = None


@dataclass(frozen=True, slots=True)
class TextLayer:
    """읽은 글 한 벌. ``engine``은 읽은 도구 이름(예: ``tesseract.js 7``), ``masked``는 보내기 전에 가린
    주민등록번호 수 (브라우저가 센 것)."""

    engine: str
    blocks: tuple[LayerBlock, ...]
    pages_total: int = 1
    masked: int = 0


def mask_resident_numbers(text: str) -> tuple[str, int]:
    """주민등록번호 모양을 ``******-*******``로 가린다. (가린 글, 가린 수)."""
    n = 0

    def sub(_m) -> str:
        nonlocal n
        n += 1
        return RESIDENT_MASK

    return _RRN.sub(sub, text), n


def check_text_layer(layer: TextLayer, *, mime: str) -> TextLayer:
    """상한·쪽 번호·위치를 검사하고, 남은 주민등록번호 모양을 한 번 더 가린 것을 돌려준다.

    사진은 1쪽. 위치는 음이 아닌 정수 네 개(x0 ≤ x1, y0 ≤ y1)만 — 없는 좌표는 만들지 않는다."""
    if mime not in TEXT_LAYER_MIMES:
        raise TextLayerError("text_layer_not_needed")
    engine = " ".join((layer.engine or "").split())[:MAX_ENGINE_CHARS]
    if not engine:
        raise TextLayerError("engine_required")
    pages_total = layer.pages_total or 1
    if mime.startswith("image/") and pages_total != 1:
        raise TextLayerError("invalid_page")
    if not (1 <= pages_total <= MAX_LAYER_PAGES):
        raise TextLayerError("too_many_pages")
    if len(layer.blocks) > MAX_LAYER_BLOCKS:
        raise TextLayerError("too_many_blocks")
    if not (0 <= layer.masked <= MAX_LAYER_BLOCKS):
        raise TextLayerError("invalid_masked")
    out: list[LayerBlock] = []
    total = 0
    masked = layer.masked
    for b in layer.blocks:
        if not (1 <= b.page <= pages_total):
            raise TextLayerError("invalid_page")
        text = b.text.replace("\r\n", "\n").replace("\x00", "").strip()
        if len(text) > MAX_BLOCK_CHARS:
            raise TextLayerError("block_too_long")
        if not text:
            continue
        text, n = mask_resident_numbers(text)
        masked += n
        total += len(text)
        if total > MAX_LAYER_CHARS:
            raise TextLayerError("text_too_long")
        bbox = None
        if b.bbox is not None:
            if len(b.bbox) != 4 or not all(
                isinstance(v, int) and 0 <= v <= MAX_BBOX for v in b.bbox
            ):
                raise TextLayerError("invalid_bbox")
            x0, y0, x1, y1 = b.bbox
            if x1 < x0 or y1 < y0:
                raise TextLayerError("invalid_bbox")
            bbox = (x0, y0, x1, y1)
        out.append(LayerBlock(b.page, text, bbox))
    if not out:
        raise TextLayerError("empty_text_layer")
    return TextLayer(engine, tuple(out), pages_total, masked)


__all__ = [
    "HIGH",
    "LOW",
    "MAX_LAYER_CHARS",
    "SHORT_DOC_CHARS",
    "TEXT_LAYER_MIMES",
    "FileGuess",
    "LayerBlock",
    "TextLayer",
    "TextLayerError",
    "UrlGuess",
    "check_text_layer",
    "classify_file",
    "classify_url",
    "company_hint_of",
    "has_resident_number",
    "mask_resident_numbers",
    "normalize_host",
]

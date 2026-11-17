"""Markdown으로 내보내기 (plan §21 백업·유지보수): 노트 앱(Obsidian 등)에서 폴더째 여는 읽기 전용 묶음(zip).

백업 묶음(``usecases.export``, tar.gz)과 목적이 다르다. 이 묶음은 **한 방향**이다: 사람이 읽고 노트 앱에서
링크를 따라가는 용도이고, 다시 가져올 수 없다 (형식·ID·해시가 없다). 복원은 백업 묶음으로만 한다.

폴더 (이름은 한국어 고정, 파일 이름은 ``safe_name``으로 macOS·Windows·Linux에서 모두 쓸 수 있게)::

    README.md                    무엇인지, 내보낸 때, 수, "다시 가져올 수 없습니다"
    나/프로필.md                  기본 정보·링크 (병역·보훈·장애·희망 조건·생년월일·주소는 ``sensitive``일 때만)
    나/학력.md, 나/자격·어학·수상.md, 나/기술.md
    경험/<경험>.md                YAML 머리말(kind·org·role·period·tags·parent) + 확인한 사실과 근거 인용
    내 글/기본 문서/, 내 글/공통 답변/, 내 글/지난 제출본/
    지원/<지원>.md                공고 요약·상태·전형·문서의 마지막 저장 글
    참고/회사/, 참고/참고 글/      머리말 ``purpose: reference`` + "근거로 쓰지 않습니다"
    포트폴리오/<항목>.md           O18: '공개 가능' 항목만. 문제·내 역할·한 일·결과, 링크, 공개 가능 이미지의 설명만
    자료/목록.md                  올린 자료 목록 (증빙 원본·증빙에서 읽은 글은 넣지 않는다)
    자료/원본/                    ``files``일 때만: 증빙이 아닌 원본 파일

규칙:

- 확인한 사실(``self_confirmed``·``evidence_supported``)만 넣는다. 확인 전 후보(``extracted``)는 ``candidates``일 때
  "확인 전" 칸에 따로 적는다. 보관·이의 제기한 사실은 넣지 않는다.
- 증빙(``purpose = proof``)은 제목·날짜만 목록에 적는다. 원본 바이트·읽은 글(블록·OCR)은 어떤 옵션으로도 넣지
  않는다. 근거 인용의 원본이 나중에 증빙으로 바뀌었거나 용도 확인 전이면 인용을 빼고 제목만 적는다.
- 주민등록번호 모양은 어디서든 가린다 (``domain.sensitive``와 같은 모양). 자격 번호는 ``sensitive``가 아니면
  끝 4자리만 (화면과 같음).
- 파일 이름 충돌은 같은 폴더 안에서 대소문자를 무시해 찾고, (이름, 만든 때, ID) 순서로 정해 뒤의 것에
  `` (2)``·`` (3)``을 붙인다. 같은 자료면 같은 이름이 나온다.
- 링크는 Obsidian 위키 링크 ``[[폴더/이름|보이는 제목]]``이다 (경로를 적어 같은 이름이 다른 폴더에 있어도 맞게 간다).
- 소유자(``workspace.export``)만. 앱 계정(RLS) + 공간 문맥 안에서만 읽는다. 한 번 내보낼 때마다
  ``export_runs``에 ``kind = 'markdown'`` 한 줄 (c0046) — "마지막 백업"에는 세지 않는다.
- 임시 파일에 쓰고, 풀어 쓴 크기 합이 ``max_bytes``(기본 ``CAREER_MARKDOWN_EXPORT_MAX_BYTES``, 512 MiB)를 넘으면
  ``too_large``로 멈춘다. 원본 파일은 하나씩 읽는다.
- 포트폴리오는 공개 규칙이다 (``domain.portfolio.visible_in(…, "public")``): '나만'·'지원서에만' 항목은 없고,
  보이는 항목에서도 '공개 가능' 이미지의 설명만 적는다. 이미지 파일은 넣지 않는다.
- 모델을 부르지 않는다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import unicodedata
import zipfile
from collections.abc import Iterable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import insert, select

from career_core.db.models import (
    AnswerAsset,
    AnswerAssetClaim,
    Application,
    ApplicationDocument,
    ApplicationStage,
    ApplicationSubmission,
    Claim,
    ClaimEvidence,
    Company,
    CompanyFact,
    CompanyPage,
    Credential,
    DocumentBlock,
    DocumentRevision,
    Education,
    Experience,
    ExportRun,
    InterviewAnswer,
    InterviewQuestion,
    JobPosting,
    ParseRun,
    Profile,
    ReferenceItem,
    ReferenceNote,
    Source,
    SourceSpan,
    SourceVersion,
    Workspace,
)
from career_core.db.session import Database
from career_core.domain.brain import CONFIRMED_STATUSES, ClaimStatus
from career_core.domain.credentials import mask_number
from career_core.domain.experiences import month_text
from career_core.domain.sensitive import _RRN
from career_core.domain.study import StudyRuleError
from career_core.domain.workspace import ScopePermissionPolicy
from career_core.ports import ActorContext, BlobStore, PermissionPolicy
from career_core.usecases.export import ExportError

MARKDOWN_FORMAT_VERSION = 1
MAX_BYTES_ENV = "CAREER_MARKDOWN_EXPORT_MAX_BYTES"
DEFAULT_MAX_BYTES = 512 << 20
# 파일 이름 한 칸의 최대 UTF-8 바이트 (확장자·" (2)" 앞). 대부분의 파일 시스템 한도 255바이트 안에 넉넉히.
MAX_NAME_BYTES = 120
FALLBACK_NAME = "제목 없음"

ME = "나"
EXPERIENCES = "경험"
WRITING = "내 글"
BASE_DOCS = f"{WRITING}/기본 문서"
ANSWERS = f"{WRITING}/공통 답변"
SUBMISSIONS = f"{WRITING}/지난 제출본"
APPLICATIONS = "지원"
COMPANIES = "참고/회사"
REFERENCES = "참고/참고 글"
SOURCES = "자료"
PORTFOLIO = "포트폴리오"
ORIGINALS = f"{SOURCES}/원본"

NO_IMPORT = "이 폴더는 다시 가져올 수 없습니다. 백업은 연결·관리의 백업을 쓰세요."
REFERENCE_LINE = "참고 자료입니다. 근거로 쓰지 않습니다."

EXPERIENCE_KIND = {
    "work": "경력",
    "project": "프로젝트",
    "education": "교육",
    "activity": "대외활동",
    "other": "기타",
}
CLAIM_KIND = {
    "responsibility": "맡은 일",
    "action": "직접 한 일",
    "result": "결과",
    "decision": "선택과 이유",
    "difficulty": "어려움과 해결",
    "skill": "기술",
    "other": "기타",
}
CONTRIBUTION = {"personal": "본인", "team": "팀", "unknown": "기여 미상"}
APP_STATUS = {
    "draft": "준비 중",
    "submitted": "제출함",
    "passed": "통과",
    "rejected": "불합격",
    "withdrawn": "철회",
}
STAGE_KIND = {
    "document": "서류",
    "test": "테스트",
    "assignment": "과제",
    "interview": "면접",
    "final": "최종",
    "other": "기타",
}
STAGE_OUTCOME = {
    "pending": "결과 전",
    "passed": "통과",
    "failed": "탈락",
    "withdrawn": "포기",
    "cancelled": "취소됨",
}
DOC_KIND = {
    "question": "자기소개서 문항",
    "resume": "이력서",
    "cover_letter": "자기소개서",
    "portfolio": "포트폴리오",
    "other": "기타 문서",
}
ANSWER_KIND = {
    "intro": "1분 자기소개",
    "motivation": "지원 동기",
    "personality": "성격 장단점",
    "conflict": "갈등 경험",
    "failure": "실패 경험",
    "aspiration": "입사 후 포부",
    "other": "기타",
}
CREDENTIAL_KIND = {
    "license": "자격증",
    "language": "어학",
    "course": "수료",
    "award": "수상",
    "degree": "학력",
    "other": "기타",
}
DEGREE = {
    "high_school": "고등학교",
    "associate": "전문학사",
    "bachelor": "학사",
    "master": "석사",
    "doctor": "박사",
}
EDUCATION_STATUS = {
    "enrolled": "재학",
    "on_leave": "휴학",
    "expected": "졸업예정",
    "graduated": "졸업",
    "completed": "수료",
    "dropped": "중퇴",
}
EXTRA_MAJOR = {"double": "복수전공", "minor": "부전공"}
MILITARY_STATUS = {
    "served": "군필",
    "not_served": "미필",
    "exempt": "면제",
    "not_applicable": "비대상",
    "serving": "복무 중",
}
EMPLOYMENT_TYPE = {"regular": "정규직", "contract": "계약직", "intern": "인턴"}
REFERENCE_KIND = {
    "accepted_essay": "합격 자기소개서",
    "essay": "자기소개서",
    "resume": "이력서",
    "interview_review": "면접 후기",
    "job_article": "직무 글",
    "other": "기타",
}
FACT_KIND = {
    "talent": "인재상",
    "values": "핵심 가치",
    "business": "주요 사업",
    "product": "제품·서비스",
    "finance": "재무",
    "culture": "문화",
    "news": "소식",
    "hiring": "채용 절차",
    "other": "기타",
}
PURPOSE = {"experience": "내 경험 자료", "proof": "증빙 서류", "reference": "참고 자료"}
POSTING_LISTS = (
    ("responsibilities", "주요 업무"),
    ("requirements", "자격 요건"),
    ("preferred", "우대 사항"),
    ("process", "전형 절차"),
)


@dataclass(frozen=True, slots=True)
class MarkdownOptions:
    """``sensitive``: 민감 정보 포함 (프로필의 생년월일·주소·병역·보훈·장애·희망 조건, 자격 번호 전체, 등급
    restricted인 참고 글·원본). ``candidates``: 확인 전 사실·회사 항목·참고 메모를 "확인 전"에.
    ``files``: 증빙이 아닌 원본 파일 (증빙은 늘 뺀다)."""

    sensitive: bool = False
    candidates: bool = False
    files: bool = False


@dataclass(frozen=True, slots=True)
class MarkdownReport:
    path: Path
    sha256: str
    bytes: int
    notes: int  # .md 파일 수
    files: int  # 넣은 원본 파일 수
    counts: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------- 이름·글


# Windows에서 쓸 수 없는 글자 + 노트 앱 링크에 쓰는 글자(# ^ [ ] |). 경로 구분자도 여기서 막힌다.
_FORBIDDEN = re.compile(r'[<>:"/\\|?*#^\[\]]')
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff-\ufeff]")
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def _cut_bytes(value: str, limit: int) -> str:
    data = value.encode("utf-8")
    if len(data) <= limit:
        return value
    return data[:limit].decode("utf-8", "ignore")


def safe_name(
    title: str | None, *, fallback: str = FALLBACK_NAME, max_bytes: int = MAX_NAME_BYTES
) -> str:
    """파일·폴더 이름 한 칸. NFC, 금지 글자 → ``-``, 제어 문자 → 공백, 공백 정리, 앞뒤 공백·점 빼기,
    UTF-8 ``max_bytes``로 자르기, Windows 예약 이름 앞에 ``_``. 남는 것이 없으면 ``fallback``."""
    s = unicodedata.normalize("NFC", title or "")
    s = _CONTROL.sub(" ", s)
    s = _FORBIDDEN.sub("-", s)
    s = " ".join(s.split()).strip(" .")
    s = _cut_bytes(s, max_bytes).rstrip(" .")
    if not s:
        s = fallback
    if s.split(".", 1)[0].strip().upper() in _WINDOWS_RESERVED:
        s = "_" + s
    return s


def assign_names(items: Iterable[tuple[Any, str | None, str]]) -> dict[Any, str]:
    """(키, 제목, 정렬 키) → 키별 파일 이름(확장자 없이). 한 폴더 안에서 대소문자를 무시해 겹치지 않게,
    (이름, 정렬 키) 순서로 앞의 것이 그대로, 뒤의 것이 `` (2)``부터 받는다."""
    rows = sorted(
        ((safe_name(title), order, key) for key, title, order in items),
        key=lambda r: (r[0].casefold(), r[0], r[1]),
    )
    used: set[str] = set()
    out: dict[Any, str] = {}
    for base, _, key in rows:
        name, n = base, 1
        while name.casefold() in used:
            n += 1
            name = f"{base} ({n})"
        used.add(name.casefold())
        out[key] = name
    return out


def mask_resident_numbers(text: str) -> str:
    """주민등록번호 모양의 뒤 6자리를 가린다 (``900101-1******``)."""
    return _RRN.sub(lambda m: f"{m.group(1)}-{m.group(2)}******", text)


def _clean(text: str | None) -> str:
    return mask_resident_numbers(unicodedata.normalize("NFC", text or ""))


def _inline(text: str | None) -> str:
    """목록 한 줄에 넣는 글: 줄바꿈을 공백으로, 링크로 읽히지 않게 ``[[``를 막는다."""
    return " ".join(_clean(text).split()).replace("[[", "\\[\\[").replace("]]", "\\]\\]")


def _block(text: str | None) -> str:
    """본문 글은 그대로 (주민등록번호 모양만 가림). 끝 공백 줄만 정리."""
    return _clean(text).replace("\r\n", "\n").strip("\n")


def _alias(text: str) -> str:
    return " ".join(re.sub(r"[\[\]|]", " ", _clean(text)).split())


def link(folder: str, name: str, title: str | None = None) -> str:
    alias = _alias(title or "")
    if alias and alias != name:
        return f"[[{folder}/{name}|{alias}]]"
    return f"[[{folder}/{name}]]"


def frontmatter(pairs: Sequence[tuple[str, Any]]) -> str:
    """YAML 머리말. 값은 JSON으로 쓴다 (JSON은 YAML의 부분집합: 따옴표·콜론·줄바꿈이 안전하다). 빈 값은 뺀다."""
    lines = ["---"]
    for key, value in pairs:
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, str):
            value = _clean(value)
        elif isinstance(value, list):
            value = [_clean(v) if isinstance(v, str) else v for v in value]
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _period(start: date | None, end: date | None, ongoing: bool, raw: str | None) -> str | None:
    if start or end:
        tail = "진행 중" if ongoing else (month_text(end) or "")
        return f"{month_text(start) or ''} – {tail}".strip(" –") or None
    return " ".join((raw or "").split()) or None


# 날짜만 적는 시각(제출·결정·가져온 때)은 공간 시간대의 날짜로 적는다 (파일 이름에도 쓰인다).
# ``export_markdown``이 내보내는 동안 정한다. 정하지 않았으면 UTC.
_ZONE: ContextVar[tzinfo] = ContextVar("markdown_zone", default=UTC)


def _day(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        at = value if value.tzinfo else value.replace(tzinfo=UTC)
        return at.astimezone(_ZONE.get()).date().isoformat()
    return value.isoformat()


def _bullets(pairs: Sequence[tuple[str, Any]]) -> list[str]:
    return [f"- {label}: {_inline(str(v))}" for label, v in pairs if v not in (None, "", [])]


def _ext(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower().lstrip(".")
    suffix = re.sub(r"[^a-z0-9]", "", suffix)[:10]
    return f".{suffix}" if suffix else ""


def _max_bytes() -> int:
    raw = os.environ.get(MAX_BYTES_ENV, "").strip()
    try:
        return int(raw) if raw else DEFAULT_MAX_BYTES
    except ValueError:
        return DEFAULT_MAX_BYTES


# ---------------------------------------------------------------- zip


class _Writer:
    """zip에 한 파일씩 (스레드에서). 풀어 쓴 크기 합이 한도를 넘으면 ``too_large``."""

    def __init__(self, zf: zipfile.ZipFile, when: datetime, max_bytes: int) -> None:
        self.zf = zf
        self.when = (when.year, when.month, when.day, when.hour, when.minute, when.second)
        self.max_bytes = max_bytes
        self.total = 0
        self.notes = 0
        self.files = 0
        self.paths: set[str] = set()

    def reserve(self, size: int) -> None:
        if self.total + size > self.max_bytes:
            raise ExportError("too_large", f"over {self.max_bytes} bytes")
        self.total += size

    async def put(self, path: str, data: bytes) -> None:
        path = unicodedata.normalize("NFC", path)
        if path.casefold() in self.paths:  # 이름 배정이 막는다. 여기는 마지막 확인
            raise ExportError("name_collision", path)
        self.reserve(len(data))
        self.paths.add(path.casefold())
        info = zipfile.ZipInfo(path, date_time=self.when)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        await asyncio.to_thread(self.zf.writestr, info, data)

    async def note(self, path: str, text: str) -> None:
        await self.put(f"{path}.md", (text.rstrip("\n") + "\n").encode("utf-8"))
        self.notes += 1


# ---------------------------------------------------------------- 모으기


@dataclass(slots=True)
class _Evidence:
    quote: str | None = None
    source_title: str | None = None
    page: int | None = None
    withheld: bool = False  # 원본이 증빙·용도 확인 전이라 인용을 뺐다
    question: str | None = None
    answer: str | None = None


def _evidence_line(ev: _Evidence) -> str:
    if ev.answer is not None:
        q = f" (질문: {_inline(ev.question)})" if ev.question else ""
        return f"  - 인터뷰 답: “{_inline(ev.answer)}”{q}"
    where = _inline(ev.source_title) or "원본"
    if ev.page is not None:
        where += f", {ev.page}쪽"
    if ev.withheld:
        return f"  - 근거: {where} (증빙 자료라 인용은 넣지 않았습니다)"
    return f"  - 근거: “{_inline(ev.quote)}” — {where}"


def _claim_line(c: Claim) -> str:
    bits = [CLAIM_KIND.get(c.kind, c.kind), CONTRIBUTION.get(c.contribution, c.contribution)]
    return f"- {_inline(c.statement)} · {' · '.join(bits)}"


async def export_markdown(
    db: Database,
    actor: ActorContext,
    out: str | os.PathLike[str],
    *,
    blobs: BlobStore | None = None,
    options: MarkdownOptions | None = None,
    via: str = "cli",
    permissions: PermissionPolicy | None = None,
    max_bytes: int | None = None,
    now: datetime | None = None,
) -> MarkdownReport:
    """actor의 공간을 ``out``(zip)에 Markdown 노트로 쓴다. 소유자만. ``options.files``에는 ``blobs``가 있어야 한다."""
    if not (permissions or ScopePermissionPolicy()).allows(actor, "workspace.export"):
        raise ExportError("forbidden")
    opts = options or MarkdownOptions()
    if opts.files and blobs is None:
        raise ExportError("files_unavailable")
    ws = actor.workspace_id
    out = Path(out)
    limit = _max_bytes() if max_bytes is None else max_bytes
    now = now or datetime.now(UTC)

    from career_core.usecases import skills as skills_uc
    from career_core.usecases.workspace_settings import workspace_zone

    async with db.workspace_session(ws) as s:
        try:
            zone = await workspace_zone(s)
        except StudyRuleError:  # 잘못 저장된 시간대 이름이면 UTC로 (내보내기를 막지 않는다)
            zone = UTC
        data = await _load(s, opts)
    skills = await skills_uc.skills_overview(db, actor, today=now.astimezone(zone).date())
    local_now = now.astimezone(zone)

    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(f".{out.name}.{uuid4().hex}.part")
    try:
        zf = await asyncio.to_thread(zipfile.ZipFile, partial, "w", zipfile.ZIP_DEFLATED)
        try:
            w = _Writer(zf, local_now, limit)
            token = _ZONE.set(zone)
            try:
                counts = await _write_all(w, data, skills, opts, blobs, ws, local_now)
            finally:
                _ZONE.reset(token)
        finally:
            await asyncio.to_thread(zf.close)
        os.replace(partial, out)
    finally:
        partial.unlink(missing_ok=True)

    digest, size = await asyncio.to_thread(_digest, out)
    async with db.workspace_session(ws) as s:
        await s.execute(
            insert(ExportRun).values(
                id=uuid4(),
                workspace_id=ws,
                kind="markdown",
                via=via,
                created_by=actor.subject_id,
                format_version=MARKDOWN_FORMAT_VERSION,
                archive_sha256=digest,
                archive_bytes=size,
                rows_total=w.notes,
                blobs_total=w.files,
                created_at=datetime.now(UTC),
            )
        )
    return MarkdownReport(out, digest, size, w.notes, w.files, counts)


def _digest(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


@dataclass(slots=True)
class _Data:
    workspace_name: str = ""
    profile: Profile | None = None
    education: list[Education] = field(default_factory=list)
    credentials: list[Credential] = field(default_factory=list)
    experiences: list[Experience] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    evidence: dict[UUID, list[_Evidence]] = field(default_factory=dict)
    base_docs: list[tuple[ApplicationDocument, str]] = field(default_factory=list)
    app_docs: dict[UUID, list[tuple[ApplicationDocument, str]]] = field(default_factory=dict)
    answers: list[AnswerAsset] = field(default_factory=list)
    answer_claims: dict[UUID, list[UUID]] = field(default_factory=dict)
    submissions: list[ApplicationSubmission] = field(default_factory=list)
    applications: list[Application] = field(default_factory=list)
    postings: dict[UUID, JobPosting] = field(default_factory=dict)
    stages: dict[UUID, list[ApplicationStage]] = field(default_factory=dict)
    companies: list[Company] = field(default_factory=list)
    facts: dict[UUID, list[CompanyFact]] = field(default_factory=dict)
    pages: dict[UUID, list[CompanyPage]] = field(default_factory=dict)
    references: list[ReferenceItem] = field(default_factory=list)
    notes: dict[UUID, list[ReferenceNote]] = field(default_factory=dict)
    sources: list[Source] = field(default_factory=list)
    versions: dict[UUID, SourceVersion] = field(default_factory=dict)
    # O18: 공개 가능 항목만 (``portfolios.markdown_items``)
    portfolio: list[dict[str, Any]] = field(default_factory=list)


async def _all(s, stmt) -> list:
    return list((await s.execute(stmt)).scalars().all())


async def _latest_texts(s, docs: list[ApplicationDocument]) -> dict[UUID, str]:
    """문서마다 마지막으로 저장한 글 (current_revision). 저장한 적 없으면 빈 글."""
    if not docs:
        return {}
    rows = (
        await s.execute(
            select(
                DocumentRevision.document_id,
                DocumentRevision.revision_no,
                DocumentRevision.blocks,
            ).where(DocumentRevision.document_id.in_([d.id for d in docs]))
        )
    ).all()
    current = {d.id: d.current_revision for d in docs}
    out: dict[UUID, str] = {}
    for doc_id, no, blocks in rows:
        if no == current.get(doc_id):
            out[doc_id] = "\n".join(str(b.get("text", "")) for b in blocks or [])
    return out


async def _load(s, opts: MarkdownOptions) -> _Data:
    d = _Data()
    d.workspace_name = await s.scalar(select(Workspace.name)) or ""
    d.profile = await s.scalar(select(Profile).limit(1))
    d.education = await _all(
        s, select(Education).order_by(Education.entered_on.desc().nulls_last(), Education.id)
    )
    cred_q = select(Credential).order_by(
        Credential.kind, Credential.acquired_on.desc().nulls_last(), Credential.name, Credential.id
    )
    if not opts.candidates:
        cred_q = cred_q.where(Credential.status != "extracted")
    d.credentials = await _all(s, cred_q)
    d.experiences = await _all(
        s, select(Experience).where(Experience.status == "active").order_by(Experience.id)
    )
    statuses = {str(x) for x in CONFIRMED_STATUSES}
    if opts.candidates:
        statuses.add(str(ClaimStatus.EXTRACTED))
    d.claims = await _all(
        s,
        select(Claim)
        .where(Claim.status.in_(sorted(statuses)))
        .order_by(Claim.created_at, Claim.id),
    )
    claim_ids = [c.id for c in d.claims]
    if claim_ids:
        span_rows = (
            await s.execute(
                select(
                    ClaimEvidence.claim_id,
                    SourceSpan.quote,
                    DocumentBlock.page,
                    Source.title,
                    Source.purpose,
                    Source.purpose_pending,
                )
                .join(SourceSpan, SourceSpan.id == ClaimEvidence.source_span_id)
                .join(DocumentBlock, DocumentBlock.id == SourceSpan.document_block_id)
                .join(ParseRun, ParseRun.id == DocumentBlock.parse_run_id)
                .join(SourceVersion, SourceVersion.id == ParseRun.source_version_id)
                .join(Source, Source.id == SourceVersion.source_id)
                .where(ClaimEvidence.claim_id.in_(claim_ids))
                .order_by(ClaimEvidence.created_at, ClaimEvidence.id)
            )
        ).all()
        for claim_id, quote, page, title, purpose, pending in span_rows:
            withheld = purpose != "experience" or bool(pending)
            d.evidence.setdefault(claim_id, []).append(
                _Evidence(
                    quote=None if withheld else quote,
                    source_title=title,
                    page=page,
                    withheld=withheld,
                )
            )
        answer_rows = (
            await s.execute(
                select(ClaimEvidence.claim_id, InterviewAnswer.answer, InterviewQuestion.prompt)
                .join(InterviewAnswer, InterviewAnswer.id == ClaimEvidence.interview_answer_id)
                .join(InterviewQuestion, InterviewQuestion.id == InterviewAnswer.question_id)
                .where(ClaimEvidence.claim_id.in_(claim_ids))
                .order_by(ClaimEvidence.created_at, ClaimEvidence.id)
            )
        ).all()
        for claim_id, answer, prompt in answer_rows:
            d.evidence.setdefault(claim_id, []).append(_Evidence(question=prompt, answer=answer))

    docs = await _all(
        s,
        select(ApplicationDocument).order_by(
            ApplicationDocument.question_index.nulls_last(),
            ApplicationDocument.created_at,
            ApplicationDocument.id,
        ),
    )
    texts = await _latest_texts(s, docs)
    for doc in docs:
        entry = (doc, texts.get(doc.id, ""))
        if doc.application_id is None:
            d.base_docs.append(entry)
        else:
            d.app_docs.setdefault(doc.application_id, []).append(entry)

    d.answers = await _all(s, select(AnswerAsset).order_by(AnswerAsset.id))
    rows = (
        await s.execute(
            select(AnswerAssetClaim.answer_asset_id, AnswerAssetClaim.claim_id).order_by(
                AnswerAssetClaim.created_at, AnswerAssetClaim.id
            )
        )
    ).all()
    for answer_id, claim_id in rows:
        d.answer_claims.setdefault(answer_id, []).append(claim_id)

    d.applications = await _all(s, select(Application).order_by(Application.id))
    d.postings = {p.id: p for p in await _all(s, select(JobPosting))}
    d.submissions = await _all(
        s,
        select(ApplicationSubmission).order_by(
            ApplicationSubmission.submitted_at, ApplicationSubmission.id
        ),
    )
    for st in await _all(
        s, select(ApplicationStage).order_by(ApplicationStage.position, ApplicationStage.id)
    ):
        d.stages.setdefault(st.application_id, []).append(st)

    d.companies = await _all(s, select(Company).order_by(Company.id))
    fact_q = select(CompanyFact).order_by(CompanyFact.kind, CompanyFact.created_at, CompanyFact.id)
    if not opts.candidates:
        fact_q = fact_q.where(CompanyFact.status == "confirmed")
    for f in await _all(s, fact_q):
        d.facts.setdefault(f.company_id, []).append(f)
    page_rows = (
        await s.execute(
            select(CompanyPage)
            .where(CompanyPage.status == "done")
            .order_by(CompanyPage.created_at, CompanyPage.id)
        )
    ).scalars()
    for p in page_rows:
        d.pages.setdefault(p.company_id, []).append(p)
    d.references = await _all(s, select(ReferenceItem).order_by(ReferenceItem.id))
    note_q = select(ReferenceNote).order_by(ReferenceNote.created_at, ReferenceNote.id)
    if not opts.candidates:
        note_q = note_q.where(ReferenceNote.status == "confirmed")
    for n in await _all(s, note_q):
        d.notes.setdefault(n.reference_id, []).append(n)

    d.sources = await _all(s, select(Source).order_by(Source.created_at, Source.id))
    for v in await _all(
        s, select(SourceVersion).order_by(SourceVersion.created_at, SourceVersion.id)
    ):
        d.versions[v.source_id] = v  # 마지막 판
    from career_core.usecases.portfolios import markdown_items

    d.portfolio = await markdown_items(s)
    return d


# ---------------------------------------------------------------- 쓰기


def _posting_fields(p: JobPosting | None) -> dict[str, Any]:
    return dict((p.fields or {}) if p else {})


def _deadline(value: Any) -> str | None:
    if not value:
        return None
    if value == "always":
        return "상시채용"
    return str(value).replace("T", " ")


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v.get("prompt", "")) if isinstance(v, dict) else str(v) for v in value if v]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _order_key(row: Any) -> str:
    at = getattr(row, "created_at", None) or getattr(row, "submitted_at", None)
    return f"{at.isoformat() if at else ''}|{row.id}"


async def _write_all(
    w: _Writer,
    d: _Data,
    skills: list,
    opts: MarkdownOptions,
    blobs: BlobStore | None,
    ws: UUID,
    now: datetime,
) -> dict[str, int]:
    exp_names = assign_names((e.id, e.title, _order_key(e)) for e in d.experiences)
    exps = {e.id: e for e in d.experiences}

    def exp_link(exp_id: UUID | None) -> str | None:
        if exp_id is None or exp_id not in exp_names:
            return None
        return link(EXPERIENCES, exp_names[exp_id], exps[exp_id].title)

    company_names = assign_names((c.id, c.name, _order_key(c)) for c in d.companies)
    companies = {c.id: c for c in d.companies}

    def company_link(company_id: UUID | None) -> str | None:
        if company_id is None or company_id not in company_names:
            return None
        return link(COMPANIES, company_names[company_id], companies[company_id].name)

    def app_title(a: Application) -> str:
        return a.title or _posting_fields(d.postings.get(a.posting_id)).get("title") or ""

    app_names = assign_names((a.id, app_title(a), _order_key(a)) for a in d.applications)
    apps = {a.id: a for a in d.applications}

    def sub_title(sub: ApplicationSubmission) -> str:
        fields = (sub.posting or {}).get("fields") or {}
        app = apps.get(sub.application_id)
        name = (
            app_title(app)
            if app
            else " ".join(str(x) for x in (fields.get("company"), fields.get("title")) if x)
        )
        return f"{_day(sub.submitted_at)} {name}"

    sub_names = assign_names((s.id, sub_title(s), _order_key(s)) for s in d.submissions)
    subs_by_app: dict[UUID, list[ApplicationSubmission]] = {}
    for sub in d.submissions:
        subs_by_app.setdefault(sub.application_id, []).append(sub)

    claims_by_exp: dict[UUID, list[Claim]] = {}
    claims = {c.id: c for c in d.claims}
    for c in d.claims:
        claims_by_exp.setdefault(c.experience_id, []).append(c)
    confirmed = [c for c in d.claims if c.status in CONFIRMED_STATUSES]

    await _write_me(w, d, skills, opts, exp_link)
    await _write_experiences(w, d, exp_names, claims_by_exp, exp_link)
    n_docs = await _write_writing(
        w,
        d,
        claims,
        sub_names,
        sub_title,
        exp_link,
        lambda a: link(APPLICATIONS, app_names[a], app_title(apps[a])) if a in app_names else None,
    )
    await _write_applications(
        w, d, app_names, app_title, sub_names, sub_title, subs_by_app, company_link
    )
    await _write_references(w, d, opts, company_names, company_link, app_names, app_title)
    n_files = await _write_sources(w, d, opts, blobs, ws)
    await _write_portfolio(w, d, claims, exp_link)

    counts = {
        "experiences": len(d.experiences),
        "claims": len(confirmed),
        "applications": len(d.applications),
        "documents": n_docs,
        "references": len(d.companies) + len(d.references),
        "sources": len(d.sources),
        "files": n_files,
        "portfolio": len(d.portfolio),
    }
    await w.note("README", _readme(d, counts, opts, now))
    return counts


def _readme(d: _Data, counts: dict[str, int], opts: MarkdownOptions, now: datetime) -> str:
    yes = {True: "넣음", False: "넣지 않음"}
    lines = [
        "# 커리어 브레인 노트",
        "",
        "career-brain에서 내보낸 읽기 전용 Markdown 묶음입니다. Obsidian 같은 노트 앱에서 이 폴더를 열 수 있습니다.",
        "",
        f"**{NO_IMPORT}**",
        "",
        *_bullets(
            [
                ("공간", d.workspace_name),
                ("내보낸 때", now.strftime("%Y-%m-%d %H:%M")),
            ]
        ),
        (
            f"- 경험 {counts['experiences']}개 · 확인한 사실 {counts['claims']}개 · "
            f"지원 {counts['applications']}개 · 내 글 {counts['documents']}개 · "
            f"참고 {counts['references']}개 · 자료 {counts['sources']}개"
        ),
        (
            f"- 민감 정보: {yes[opts.sensitive]} · 확인 전 사실: {yes[opts.candidates]} · "
            f"원본 파일: {yes[opts.files]} ({counts['files']}개)"
        ),
        "",
        "## 폴더",
        "",
        f"- {ME}/ — 프로필·학력·자격·기술",
        f"- {EXPERIENCES}/ — 경험마다 한 파일. 확인한 사실과 그 근거",
        f"- {WRITING}/ — 기본 문서·공통 답변·지난 제출본",
        f"- {APPLICATIONS}/ — 지원마다 한 파일. 공고 요약·전형·문서",
        "- 참고/ — 회사 조사와 참고 글. 근거로 쓰지 않습니다.",
        f"- {SOURCES}/ — 올린 자료 목록. 증빙 서류의 원본과 읽은 글은 넣지 않습니다.",
        *(
            [
                (
                    f"- {PORTFOLIO}/ — '공개 가능' 포트폴리오 항목 {counts['portfolio']}개. "
                    "'나만'·'지원서에만' 항목과 이미지 파일은 넣지 않습니다."
                )
            ]
            if counts["portfolio"]
            else []
        ),
        "",
        "확인한 사실만 근거로 적었습니다. 주민등록번호로 보이는 값은 가렸습니다.",
    ]
    return "\n".join(lines)


async def _write_me(w: _Writer, d: _Data, skills: list, opts: MarkdownOptions, exp_link) -> None:
    p = d.profile
    lines = ["# 프로필", ""]
    if p is None:
        lines.append("적은 프로필이 없습니다.")
    else:
        lines += _bullets(
            [
                ("이름", p.name),
                ("영문 이름", p.name_en),
                ("한 줄 소개", p.headline),
                ("이메일", p.email),
                ("전화", p.phone),
            ]
        )
        links = [x for x in p.links or [] if isinstance(x, dict) and x.get("url")]
        if links:
            lines += ["", "## 링크", ""]
            for x in links:
                label = _alias(str(x.get("label") or "")) or "링크"
                url = str(x["url"]).strip()
                if re.match(r"https?://\S+$", url):
                    lines.append(f"- [{label}](<{url}>)")
                else:  # 웹 주소가 아니면 링크로 만들지 않는다
                    lines.append(f"- {label}: {_inline(url)}")
        if opts.sensitive:
            military = [
                ("병역", MILITARY_STATUS.get(p.military_status or "", p.military_status)),
                ("군별", p.military_branch),
                ("계급", p.military_rank),
                (
                    "복무 기간",
                    _period(p.military_started_on, p.military_ended_on, False, None),
                ),
                ("면제 사유", p.military_exemption_reason),
            ]
            flags = [
                ("보훈 대상", None if p.veteran is None else ("예" if p.veteran else "아니오")),
                ("장애", None if p.disability is None else ("예" if p.disability else "아니오")),
            ]
            wishes = [
                ("희망 직무", ", ".join(p.desired_roles or [])),
                ("희망 지역", ", ".join(p.desired_locations or [])),
                ("희망 연봉", p.desired_salary),
                ("입사 가능 시기", p.available_from),
                (
                    "고용 형태",
                    ", ".join(EMPLOYMENT_TYPE.get(t, t) for t in p.employment_types or []),
                ),
            ]
            lines += ["", "## 민감 정보", ""]
            lines += _bullets([("생년월일", _day(p.birth_date)), ("주소", p.address)])
            lines += _bullets(military) + _bullets(flags) + _bullets(wishes)
        else:
            lines += [
                "",
                "생년월일·주소·병역·보훈·장애·희망 조건은 넣지 않았습니다 (민감 정보 포함을 고르면 넣습니다).",
            ]
    await w.note(f"{ME}/프로필", "\n".join(lines))

    lines = ["# 학력", ""]
    if not d.education:
        lines.append("적은 학력이 없습니다.")
    for e in d.education:
        head = " ".join(x for x in (e.school, e.campus) if x)
        lines += [f"## {_inline(head)}", ""]
        extra = (
            f"{e.extra_major} ({EXTRA_MAJOR.get(e.extra_major_kind or '', '')})".replace(" ()", "")
            if e.extra_major
            else None
        )
        gpa = f"{e.gpa} / {e.gpa_scale}" if e.gpa is not None and e.gpa_scale is not None else None
        lines += _bullets(
            [
                ("학위", DEGREE.get(e.degree, e.degree)),
                (
                    "상태",
                    EDUCATION_STATUS.get(e.status, e.status) + (" · 편입" if e.transferred else ""),
                ),
                ("전공", e.major),
                ("다른 전공", extra),
                ("기간", _period(e.entered_on, e.graduated_on, False, None)),
                ("학점", gpa),
                ("메모", e.note),
            ]
        )
        if exp_link(e.experience_id):
            lines.append(f"- 경험: {exp_link(e.experience_id)}")
        lines.append("")
    await w.note(f"{ME}/학력", "\n".join(lines))

    lines = ["# 자격·어학·수상", ""]
    if not d.credentials:
        lines.append("적은 자격·이수 기록이 없습니다.")
    by_kind: dict[str, list[Credential]] = {}
    for c in d.credentials:
        by_kind.setdefault(c.kind, []).append(c)
    for kind, label in CREDENTIAL_KIND.items():
        rows = by_kind.get(kind) or []
        if not rows:
            continue
        lines += [f"## {label}", ""]
        for c in rows:
            state = " (확인 전)" if c.status == "extracted" else ""
            lines.append(f"### {_inline(c.name)}{state}")
            lines.append("")
            lines += _bullets(
                [
                    ("발급", c.issuer),
                    ("번호", c.number if opts.sensitive else mask_number(c.number)),
                    ("취득", _day(c.acquired_on)),
                    ("만료", _day(c.expires_on)),
                    ("기간", _period(c.started_on, c.ended_on, False, None)),
                    ("점수·등급", c.score),
                    ("시간", f"{c.hours}시간" if c.hours else None),
                    ("메모", c.detail),
                    ("증빙", "증빙 서류와 이어 둠" if c.status == "evidence_supported" else None),
                ]
            )
            if exp_link(c.experience_id):
                lines.append(f"- 경험: {exp_link(c.experience_id)}")
            lines.append("")
    await w.note(f"{ME}/자격·어학·수상", "\n".join(lines))

    lines = [
        "# 기술",
        "",
        '경험 태그·확인한 "사용한 기술"·자격에서 모았습니다.',
        "",
    ]
    if not skills:
        lines.append("모은 기술이 없습니다.")
    for sk in skills:
        years = (
            f"{sk.first_year}–{sk.last_year}"
            if sk.first_year and sk.last_year and sk.first_year != sk.last_year
            else str(sk.last_year or sk.first_year or "")
        )
        lines += [f"## {_inline(sk.name)}", ""]
        meta = [
            x
            for x in (years, f"경험 {len(sk.experiences)}개", f"자격 {len(sk.credentials)}개")
            if x
        ]
        lines.append(" · ".join(meta))
        lines.append("")
        for e in sk.experiences:
            target = exp_link(e.id)
            if target:
                lines.append(f"- {target}")
        for c in sk.credentials:
            lines.append(f"- 자격: {_inline(c.name)}")
        lines.append("")
    await w.note(f"{ME}/기술", "\n".join(lines))


async def _write_experiences(w, d: _Data, names, claims_by_exp, exp_link) -> None:
    children: dict[UUID, list[Experience]] = {}
    for e in d.experiences:
        if e.parent_id:
            children.setdefault(e.parent_id, []).append(e)
    for e in sorted(d.experiences, key=lambda x: names[x.id].casefold()):
        claims = claims_by_exp.get(e.id, [])
        confirmed = [c for c in claims if c.status in CONFIRMED_STATUSES]
        pending = [c for c in claims if c.status == ClaimStatus.EXTRACTED]
        head = frontmatter(
            [
                ("kind", EXPERIENCE_KIND.get(e.kind, e.kind)),
                ("org", e.organization),
                ("role", e.role),
                ("period", _period(e.started_on, e.ended_on, e.ongoing, e.period_text)),
                ("tags", list(e.tags or [])),
                ("parent", exp_link(e.parent_id)),
            ]
        )
        lines = [head, f"# {_inline(e.title)}", ""]
        if exp_link(e.parent_id):
            lines += [f"상위 경험: {exp_link(e.parent_id)}", ""]
        lines += ["## 확인한 사실", ""]
        if not confirmed:
            lines.append("아직 확인한 사실이 없습니다.")
        for c in confirmed:
            lines.append(_claim_line(c))
            lines += [_evidence_line(ev) for ev in d.evidence.get(c.id, [])]
        kids = sorted(children.get(e.id, []), key=lambda x: (x.started_on or date.max, names[x.id]))
        if kids:
            lines += ["", "## 안의 경험", ""]
            lines += [f"- {exp_link(k.id)}" for k in kids]
        if pending:
            lines += [
                "",
                "## 확인 전",
                "",
                "아직 확인하지 않은 후보입니다. 근거로 쓰지 않습니다.",
                "",
            ]
            for c in pending:
                lines.append(_claim_line(c))
                lines += [_evidence_line(ev) for ev in d.evidence.get(c.id, [])]
        await w.note(f"{EXPERIENCES}/{names[e.id]}", "\n".join(lines))


_LINK_KIND = {"repo": "저장소", "demo": "데모", "article": "글", "video": "영상"}


async def _write_portfolio(w, d: _Data, claims: dict[UUID, Claim], exp_link) -> None:
    """포트폴리오/<항목>.md (공개 가능 항목만). 근거가 이어진 줄은 그대로, 직접 쓴 줄은 "(근거 없음)"."""
    rows = d.portfolio
    if not rows:
        return
    names = assign_names((r["row"].id, r["row"].title, _order_key(r["row"])) for r in rows)

    def line(ln: dict) -> str:
        cid = ln.get("claim_id")
        mark = ""
        if not cid:
            mark = " (근거 없음)"
        else:
            try:
                c = claims.get(UUID(cid))
            except ValueError:
                c = None
            if c is None or c.status not in CONFIRMED_STATUSES:
                mark = " (근거 사라짐)"
        return f"- {_inline(ln.get('text'))}{mark}"

    for r in rows:
        it, c = r["row"], r["content"]
        head = frontmatter(
            [
                ("experience", exp_link(it.experience_id)),
                ("tags", list(it.tags or [])),
                ("visibility", "공개 가능"),
            ]
        )
        lines = [head, f"# {_inline(it.title)}", ""]
        if exp_link(it.experience_id):
            lines += [f"경험: {exp_link(it.experience_id)}", ""]
        for lk in it.links or []:
            label = lk.get("label") or _LINK_KIND.get(lk.get("kind"), "링크")
            lines.append(f"- {_inline(label)}: <{_inline(lk.get('url'))}>")
        if it.links:
            lines.append("")
        for title, key in (("문제", "problem"), ("내 역할", "role")):
            if c.get(key):
                lines += [f"## {title}", "", _block(c[key]) + " (직접 쓴 글)", ""]
        for title, key in (("한 일", "actions"), ("결과", "results")):
            if c.get(key):
                lines += [f"## {title}", "", *(line(ln) for ln in c[key]), ""]
        caps = [img.caption for img in r["images"] if img.caption]
        if caps:
            lines += ["## 이미지", "", *(f"- {_inline(x)}" for x in caps), ""]
        await w.note(f"{PORTFOLIO}/{names[it.id]}", "\n".join(lines))


def _submission_docs(sub: ApplicationSubmission) -> list[dict]:
    return [x for x in sub.documents or [] if isinstance(x, dict)]


def _submitted_text(doc: dict) -> str:
    return doc.get("text") or "\n".join(
        str(b.get("text", "")) for b in doc.get("blocks") or [] if isinstance(b, dict)
    )


async def _write_writing(w, d: _Data, claims, sub_names, sub_title, exp_link, app_link) -> int:
    n = 0
    base_names = assign_names((doc.id, doc.title, _order_key(doc)) for doc, _ in d.base_docs)
    for doc, text in d.base_docs:
        head = frontmatter([("kind", DOC_KIND.get(doc.kind, doc.kind))])
        lines = [head, f"# {_inline(doc.title)}", ""]
        if doc.question_prompt:
            lines += [f"문항: {_inline(doc.question_prompt)}", ""]
        lines.append(_block(text) or "아직 쓴 글이 없습니다.")
        await w.note(f"{BASE_DOCS}/{base_names[doc.id]}", "\n".join(lines))
        n += 1

    answer_names = assign_names((a.id, a.title, _order_key(a)) for a in d.answers)
    for a in d.answers:
        head = frontmatter([("kind", ANSWER_KIND.get(a.kind, a.kind))])
        lines = [head, f"# {_inline(a.title)}", "", _block(a.body) or "아직 쓴 글이 없습니다."]
        refs = [claims[c] for c in d.answer_claims.get(a.id, []) if c in claims]
        refs = [c for c in refs if c.status in CONFIRMED_STATUSES]
        if refs:
            lines += ["", "## 근거로 이은 확인한 사실", ""]
            for c in refs:
                where = exp_link(c.experience_id)
                lines.append(f"- {_inline(c.statement)}" + (f" — {where}" if where else ""))
        await w.note(f"{ANSWERS}/{answer_names[a.id]}", "\n".join(lines))
        n += 1

    for sub in d.submissions:
        fields = (sub.posting or {}).get("fields") or {}
        head = frontmatter(
            [
                ("company", fields.get("company")),
                ("role", fields.get("title")),
                ("submitted", _day(sub.submitted_at)),
                ("application", app_link(sub.application_id)),
            ]
        )
        lines = [head, f"# {_inline(sub_title(sub))}", "", "제출할 때 고정한 글 그대로입니다.", ""]
        if sub.note:
            lines += [f"메모: {_inline(sub.note)}", ""]
        for doc in _submission_docs(sub):
            lines += [
                f"## {_inline(doc.get('title') or DOC_KIND.get(doc.get('kind') or '', '문서'))}",
                "",
            ]
            if doc.get("question_prompt"):
                lines += [f"문항: {_inline(doc.get('question_prompt'))}", ""]
            lines += [_block(_submitted_text(doc)) or "(빈 글)", ""]
        await w.note(f"{SUBMISSIONS}/{sub_names[sub.id]}", "\n".join(lines))
        n += 1
    return n


async def _write_applications(
    w, d: _Data, names, app_title, sub_names, sub_title, subs_by_app, company_link
) -> None:
    for a in d.applications:
        posting = d.postings.get(a.posting_id)
        fields = _posting_fields(posting)
        stages = d.stages.get(a.id, [])
        last = stages[-1] if stages else None
        stage = (
            f"{last.name or STAGE_KIND.get(last.kind, last.kind)} · {STAGE_OUTCOME.get(last.outcome, last.outcome)}"
            if last
            else None
        )
        url = posting.canonical_url if posting else None
        head = frontmatter(
            [
                ("company", fields.get("company")),
                ("role", fields.get("title")),
                ("deadline", _deadline(fields.get("deadline"))),
                ("link", url),
                ("status", APP_STATUS.get(a.status, a.status)),
                ("stage", stage),
                ("submitted", _day(a.submitted_at)),
            ]
        )
        lines = [head, f"# {_inline(app_title(a))}", "", "## 공고", ""]
        lines += _bullets(
            [
                ("회사", fields.get("company")),
                ("공고명", fields.get("title")),
                ("근무지", fields.get("location")),
                ("고용 형태", fields.get("employment_type")),
                ("경력 조건", fields.get("career_level")),
                ("마감", _deadline(fields.get("deadline"))),
            ]
        )
        if url:
            lines.append(f"- 링크: <{url}>")
        if posting and company_link(posting.company_id):
            lines.append(f"- 회사 조사: {company_link(posting.company_id)}")
        for key, label in POSTING_LISTS:
            items = _list(fields.get(key))
            if items:
                lines += ["", f"### {label}", ""] + [f"- {_inline(x)}" for x in items]
        lines += ["", "## 상태", "", f"- {APP_STATUS.get(a.status, a.status)}"]
        if a.result_note:
            lines.append(f"- 메모: {_inline(a.result_note)}")
        if stages:
            lines += ["", "### 전형", ""]
            for st in stages:
                when = _day(st.decided_at or st.scheduled_at)
                bits = [
                    _inline(st.name) or STAGE_KIND.get(st.kind, st.kind),
                    STAGE_OUTCOME.get(st.outcome, st.outcome),
                ]
                if when:
                    bits.append(when)
                lines.append("- " + " · ".join(bits))
        docs = d.app_docs.get(a.id, [])
        lines += ["", "## 문서", ""]
        if not docs:
            lines.append("아직 만든 문서가 없습니다.")
        for doc, text in docs:
            lines += [f"### {_inline(doc.title)}", ""]
            if doc.question_prompt:
                lines += [f"문항: {_inline(doc.question_prompt)}", ""]
            lines += [_block(text) or "아직 쓴 글이 없습니다.", ""]
        subs = subs_by_app.get(a.id, [])
        if subs:
            lines += ["## 제출본", ""]
            lines += [f"- {link(SUBMISSIONS, sub_names[s.id], sub_title(s))}" for s in subs]
        await w.note(f"{APPLICATIONS}/{names[a.id]}", "\n".join(lines))


async def _write_references(
    w, d: _Data, opts, company_names, company_link, app_names, app_title
) -> None:
    apps_by_company: dict[UUID, list[Application]] = {}
    for a in d.applications:
        p = d.postings.get(a.posting_id)
        if p and p.company_id:
            apps_by_company.setdefault(p.company_id, []).append(a)
    for c in d.companies:
        head = frontmatter(
            [("purpose", "reference"), ("aliases", list(c.aliases or [])), ("homepage", c.homepage)]
        )
        lines = [head, f"# {_inline(c.name)}", "", REFERENCE_LINE, ""]
        if c.homepage:
            lines += [f"- 홈페이지: <{c.homepage}>", ""]
        facts = d.facts.get(c.id, [])
        confirmed = [f for f in facts if f.status == "confirmed"]
        pending = [f for f in facts if f.status != "confirmed"]
        lines += ["## 확인한 항목", ""]
        if not confirmed:
            lines.append("확인한 항목이 없습니다.")
        lines += _fact_lines(confirmed)
        if pending:
            lines += ["", "## 확인 전", ""] + _fact_lines(pending)
        pages = d.pages.get(c.id, [])
        if pages:
            lines += ["", "## 가져온 원문", ""]
            for p in pages:
                title = _inline(p.title) or (p.final_url or p.url or "붙여넣은 글")
                when = _day(p.fetched_at)
                url = p.final_url or p.url
                lines.append(
                    f"- {title}" + (f" <{url}>" if url else "") + (f" · {when}" if when else "")
                )
        apps = apps_by_company.get(c.id, [])
        if apps:
            lines += ["", "## 이 회사에 낸 지원", ""]
            lines += [f"- {link(APPLICATIONS, app_names[a.id], app_title(a))}" for a in apps]
        await w.note(f"{COMPANIES}/{company_names[c.id]}", "\n".join(lines))

    ref_names = assign_names((r.id, r.title, _order_key(r)) for r in d.references)
    for r in d.references:
        head = frontmatter(
            [
                ("purpose", "reference"),
                ("kind", REFERENCE_KIND.get(r.kind, r.kind)),
                ("company", company_link(r.company_id)),
                ("role", r.role),
                ("year", r.year),
            ]
        )
        lines = [head, f"# {_inline(r.title)}", "", REFERENCE_LINE, ""]
        if r.source_note:
            lines += [f"- 출처: {_inline(r.source_note)}", ""]
        notes = d.notes.get(r.id, [])
        for title, rows in (
            ("구조 메모", [n for n in notes if n.status == "confirmed"]),
            ("확인 전 메모", [n for n in notes if n.status != "confirmed"]),
        ):
            if not rows:
                continue
            lines += [f"## {title}", ""]
            for n in rows:
                if n.question:
                    lines.append(f"- 문항: {_inline(n.question)}")
                for label, items in (
                    ("구성", n.structure),
                    ("소재", n.materials),
                    ("강조 역량", n.competencies),
                ):
                    if items:
                        lines.append(f"  - {label}: {_inline(', '.join(items))}")
            lines.append("")
        lines += ["## 글", ""]
        if r.data_class == "restricted" and not opts.sensitive:
            lines.append(
                "이 기기 밖으로 보내지 않는 글이라 넣지 않았습니다 (민감 정보 포함을 고르면 넣습니다)."
            )
        else:
            lines.append(_block(r.text) or "(빈 글)")
        await w.note(f"{REFERENCES}/{ref_names[r.id]}", "\n".join(lines))


def _fact_lines(facts: list[CompanyFact]) -> list[str]:
    out: list[str] = []
    for f in facts:
        bits = [FACT_KIND.get(f.kind, f.kind)]
        if f.as_of:
            precision = f.as_of_precision or "day"
            bits.append(
                f"기준 {f.as_of.year}"
                if precision == "year"
                else f"기준 {f.as_of:%Y-%m}"
                if precision == "month"
                else f"기준 {f.as_of.isoformat()}"
            )
        out.append(f"- {_inline(f.body)} · {' · '.join(bits)}")
        where = f" — <{f.source_url}>" if f.source_url else ""
        out.append(f"  - 인용: “{_inline(f.quote)}”{where}")
    return out


def _file_allowed(src: Source, opts: MarkdownOptions) -> bool:
    """원본 파일을 넣을 수 있나. 증빙·용도 확인 전은 늘 빼고, 등급 restricted는 민감 정보를 골랐을 때만."""
    if src.purpose == "proof" or src.purpose_pending:
        return False
    return src.data_class != "restricted" or opts.sensitive


async def _write_sources(w, d: _Data, opts, blobs: BlobStore | None, ws: UUID) -> int:
    file_names: dict[UUID, str] = {}
    chosen = [
        src for src in d.sources if opts.files and _file_allowed(src, opts) and src.id in d.versions
    ]
    if chosen:
        stems = assign_names(
            (
                src.id,
                Path(d.versions[src.id].original_filename or src.title).stem or src.title,
                _order_key(src),
            )
            for src in chosen
        )
        # 이름(확장자 앞)이 이미 겹치지 않으므로 확장자를 붙여도 겹치지 않는다
        file_names = {k: v + _ext(d.versions[k].original_filename) for k, v in stems.items()}
    included = 0
    lines = [
        "# 자료",
        "",
        "올린 자료 목록입니다. 증빙 서류의 원본과 거기서 읽은 글은 넣지 않습니다.",
        "",
    ]
    if not d.sources:
        lines.append("올린 자료가 없습니다.")
    for src in d.sources:
        purpose = PURPOSE.get(src.purpose, src.purpose)
        if src.purpose_pending:
            purpose += " (용도 확인 전)"
        line = f"- {_inline(src.title) or '제목 없음'} · {purpose} · {_day(src.created_at)}"
        name = file_names.get(src.id)
        if name and blobs is not None:
            ver = d.versions[src.id]
            if w.total + ver.size_bytes > w.max_bytes:
                raise ExportError("too_large", f"over {w.max_bytes} bytes")
            data = await blobs.get(ws, ver.blob_key)
            if hashlib.sha256(data).hexdigest() != ver.blob_key:
                raise ExportError("blob_changed", ver.blob_key[:12])
            await w.put(f"{ORIGINALS}/{name}", data)
            w.files += 1
            included += 1
            del data
            line += f" · [[{ORIGINALS}/{name}|원본]]"
        lines.append(line)
    await w.note(f"{SOURCES}/목록", "\n".join(lines))
    return included


__all__: Sequence[str] = (
    "DEFAULT_MAX_BYTES",
    "MarkdownOptions",
    "MarkdownReport",
    "assign_names",
    "export_markdown",
    "frontmatter",
    "link",
    "mask_resident_numbers",
    "safe_name",
)

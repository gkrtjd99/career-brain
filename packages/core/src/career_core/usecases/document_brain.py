"""지원 문서 ↔ 브레인 (2026-11-19).

이미 써 둔 자기소개서는 그 자체가 경험 자료다. 그런데 지원 문서에만 있으면 브레인은 비어 있고, 검사는 문단마다
"근거 없는 경험·수치"만 낸다. 두 방향을 잇는다.

- ``send_to_brain``: 저장한 글(현재 판)을 Markdown 원본(내 경험 자료, private)으로 브레인에 넣는다. 읽기가 끝나면
  평소처럼 경험·주장 후보를 뽑는다 (권한이 있을 때, ``auto_extract``). 같은 글을 다시 보내면 처음 원본을 쓴다
  (내용 해시). 제한(restricted) 근거가 연결된 문단은 넣지 않는다.
- ``brain_matches``: 문단마다 "이 문단 글 안에 원문 인용이 그대로 들어 있는 주장". 그 인용이 지원 문서에서 왔든
  따로 넣은 자기소개서 파일에서 왔든 같다 (글이 같으면 같은 이야기다). 확인한 주장만 연결 후보로 주고, 확인 전
  후보는 수만 센다 (확인은 브레인에서). 이미 그 문단에 연결된 주장은 뺀다. 연결은 웹이 ``link`` 연산으로 한다
  (사람이 누른다, 자동으로 고치지 않는다).
- 인용은 내 경험 자료(``purpose = experience``)·restricted가 아닌 원본에서 온 것만 본다. 짧은 인용
  (``MIN_QUOTE_CHARS`` 아래)은 우연히 겹칠 수 있어 보지 않는다.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from career_core.db import (
    Claim,
    ClaimEvidence,
    Database,
    DocumentBlock,
    Experience,
    JobPosting,
    ParseRun,
    Source,
    SourceVersion,
)
from career_core.db.models import SourceSpan
from career_core.domain.brain import CONFIRMED_STATUSES, ClaimStatus
from career_core.domain.postings import FieldName
from career_core.domain.sources import DataClass, SourcePurpose
from career_core.ports import ActorContext, BlobStore
from career_core.uploads import UploadRejected
from career_core.usecases.data_classes import restricted_claims
from career_core.usecases.sources import UploadResult, upload_source

MIN_QUOTE_CHARS = 12
MAX_SPANS = 20_000
MAX_MATCHES_PER_BLOCK = 8
_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


@dataclass(frozen=True, slots=True)
class Rendered:
    title: str
    filename: str
    data: bytes
    paragraphs: int
    withheld: int


async def _load(s, application_id: UUID | None, document_id: UUID):
    from career_core.usecases import applications as app_uc

    app, doc = await app_uc._get_doc(s, application_id, document_id)
    rev = await app_uc._revision(s, doc.id, doc.current_revision)
    blocks = list(rev.blocks) if rev else []
    company = None
    if app is not None and app.posting_id is not None:
        posting = await s.get(JobPosting, app.posting_id)
        value = (posting.fields or {}).get(FieldName.COMPANY) if posting else None
        company = value if isinstance(value, str) and value.strip() else None
    return app, doc, blocks, company


def render(doc_title: str, question: str | None, company: str | None, texts: list[str]) -> Rendered:
    """브레인 원본으로 넣을 Markdown. 첫 줄은 제목(회사 · 문항), 문단은 빈 줄로 나눈다."""
    head = " · ".join(x for x in (company, question or doc_title) if x)
    title = (f"{head} (지원서)" if head else "지원서 글")[:300]
    body = "\n\n".join(texts)
    text = f"# {title}\n\n{body}\n"
    safe = "".join(ch if ch.isalnum() or ch in " -_.()" else " " for ch in title)
    return Rendered(
        title,
        (" ".join(safe.split())[:80] or "지원서") + ".md",
        text.encode("utf-8"),
        len(texts),
        0,
    )


async def _rendered(db: Database, actor: ActorContext, application_id, document_id) -> Rendered:
    async with db.workspace_session(actor.workspace_id) as s:
        _app, doc, blocks, company = await _load(s, application_id, document_id)
        linked = {c for b in blocks for c in b.get("claim_ids") or []}
        blocked = {str(i) for i in await restricted_claims(s, {UUID(c) for c in linked})}
    texts, withheld = [], 0
    for b in blocks:
        if set(b.get("claim_ids") or []) & blocked:
            withheld += 1
            continue
        if (b.get("text") or "").strip():
            texts.append(b["text"].strip())
    r = render(doc.title, doc.question_prompt, company, texts)
    return Rendered(r.title, r.filename, r.data, r.paragraphs, withheld)


async def send_to_brain(
    db: Database,
    blobs: BlobStore,
    actor: ActorContext,
    application_id: UUID | None,
    document_id: UUID,
    *,
    max_bytes: int,
    auto_extract: bool,
) -> tuple[UploadResult, Rendered]:
    r = await _rendered(db, actor, application_id, document_id)
    if not r.paragraphs:
        raise UploadRejected("empty", 400)
    result = await upload_source(
        db,
        blobs,
        actor,
        filename=r.filename,
        data=r.data,
        declared_mime="text/markdown",
        max_bytes=max_bytes,
        title=r.title,
        data_class=DataClass.PRIVATE,
        purpose=SourcePurpose.EXPERIENCE,
        auto_extract=auto_extract,
    )
    return result, r


@dataclass(frozen=True, slots=True)
class ClaimMatch:
    claim_id: UUID
    statement: str
    kind: str
    experience_id: UUID
    experience_title: str


@dataclass(frozen=True, slots=True)
class BlockMatch:
    block_id: str
    confirmed: list[ClaimMatch]
    pending: int  # 확인 전 후보 수 (브레인에서 확인하면 연결할 수 있다)


@dataclass(frozen=True, slots=True)
class BrainMatches:
    document_id: UUID
    # 지금 글을 그대로 보낸 원본이 브레인에 있는가 (같은 내용 해시)
    in_brain: bool
    source_id: UUID | None
    # 확인 전 후보가 가장 많이 나온 원본 (다른 파일로 넣었어도): 브레인에서 확인하러 갈 곳
    review_source_id: UUID | None
    blocks: list[BlockMatch]
    confirmed_total: int
    pending_total: int


async def brain_matches(
    db: Database, actor: ActorContext, application_id: UUID | None, document_id: UUID
) -> BrainMatches:
    r = await _rendered(db, actor, application_id, document_id)
    digest = hashlib.sha256(r.data).hexdigest()
    async with db.workspace_session(actor.workspace_id) as s:
        _app, doc, blocks, _company = await _load(s, application_id, document_id)
        source_id = await s.scalar(
            select(SourceVersion.source_id).where(SourceVersion.sha256 == digest).limit(1)
        )
        rows = (
            await s.execute(
                select(SourceSpan.quote, Claim, Experience, Source.id)
                .join(ClaimEvidence, ClaimEvidence.source_span_id == SourceSpan.id)
                .join(Claim, Claim.id == ClaimEvidence.claim_id)
                .join(Experience, Experience.id == Claim.experience_id)
                .join(DocumentBlock, DocumentBlock.id == SourceSpan.document_block_id)
                .join(ParseRun, ParseRun.id == DocumentBlock.parse_run_id)
                .join(SourceVersion, SourceVersion.id == ParseRun.source_version_id)
                .join(Source, Source.id == SourceVersion.source_id)
                .where(
                    Source.purpose == SourcePurpose.EXPERIENCE.value,
                    Source.data_class != DataClass.RESTRICTED.value,
                    Claim.status.in_(
                        [*(x.value for x in CONFIRMED_STATUSES), ClaimStatus.EXTRACTED.value]
                    ),
                    Experience.merged_into_id.is_(None),
                )
                .limit(MAX_SPANS)
            )
        ).all()
    quotes = [(_norm(q), c, e, sid) for q, c, e, sid in rows if len(_norm(q)) >= MIN_QUOTE_CHARS]
    pending_sources: Counter[UUID] = Counter()
    out: list[BlockMatch] = []
    for b in blocks:
        text = _norm(b.get("text") or "")
        if not text:
            continue
        have = set(b.get("claim_ids") or [])
        confirmed: dict[UUID, ClaimMatch] = {}
        pending: set[UUID] = set()
        for q, c, e, sid in quotes:
            if q not in text or str(c.id) in have:
                continue
            if c.status in CONFIRMED_STATUSES:
                confirmed.setdefault(c.id, ClaimMatch(c.id, c.statement, c.kind, e.id, e.title))
            elif c.id not in pending:
                pending.add(c.id)
                pending_sources[sid] += 1
        if confirmed or pending:
            out.append(
                BlockMatch(b["id"], list(confirmed.values())[:MAX_MATCHES_PER_BLOCK], len(pending))
            )
    return BrainMatches(
        doc.id,
        source_id is not None,
        source_id,
        pending_sources.most_common(1)[0][0] if pending_sources else None,
        out,
        sum(len(m.confirmed) for m in out),
        sum(m.pending for m in out),
    )


__all__ = [
    "MIN_QUOTE_CHARS",
    "BlockMatch",
    "BrainMatches",
    "ClaimMatch",
    "Rendered",
    "brain_matches",
    "render",
    "send_to_brain",
]

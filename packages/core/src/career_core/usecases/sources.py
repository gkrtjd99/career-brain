"""원본 업로드·용도. 같은 공간의 같은 바이트는 한 번만 저장·처리한다.

용도(c0032, plan §20): 올릴 때 고른다 (기본 ``experience``). 증빙(``proof``)은 자료 등급을 늘
``restricted``로 둔다. 나중에 바꿀 수 있다 (``change_purpose``):

- → ``experience``: 읽기가 끝났으면 경험 추출을 큐에 넣는다 (추출 권한이 있을 때).
- ``experience`` → 증빙·참고: 이 원본에서 뽑은 확인 전 후보(``extracted`` 주장, 남은 주장이 없는 AI 경험)는
  ``retired``로 닫고 대기 중인 추출은 실패(``purpose_changed``)로 닫는다. 이 원본을 근거로 단 확인된 주장이
  하나라도 있으면 바꾸지 않는다 (``source_has_confirmed_claims``) — 사실이 된 주장의 근거를 몰래 빼지 않는다.

확인 대기(c0039, 브레인에 넣기): 넣을 때 용도를 확신하지 못하면 ``purpose_pending``으로 올리고 등급을
``restricted``로 둔다 (증빙일 수 있다). 사람이 용도를 고르거나 그대로 확인하면(``change_purpose``) 풀린다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from career_core.db.base import utcnow
from career_core.db.models import (
    Claim,
    ClaimEvidence,
    DocumentBlock,
    Experience,
    ExtractionRun,
    Job,
    ParseRun,
    ReferenceItem,
    Source,
    SourceSpan,
    SourceVersion,
)
from career_core.db.session import Database
from career_core.domain.brain import (
    CONFIRMED_STATUSES,
    ClaimOrigin,
    ClaimRuleError,
    ClaimStatus,
    ExtractionStatus,
)
from career_core.domain.sources import (
    PIPELINE_VERSION,
    PURPOSE_DATA_CLASS,
    DataClass,
    ParseStatus,
    SourcePurpose,
    source_purpose,
)
from career_core.ports import ActorContext, BlobStore
from career_core.uploads import DOCX_MIME, UploadRejected, clean_filename, resolve_mime
from career_core.usecases.index_queue import request_index

PARSE_JOB = "parse"
# c0029 ``parse_runs.hidden_text``: 숨김 서식 글을 블록에 넣는다 / 뺀다
HIDDEN_TEXT_CHOICES = ("keep", "exclude")
# ``exclude``를 따르는 형식 (나머지는 ``keep``으로 저장한다)
HIDDEN_TEXT_EXCLUDABLE = frozenset({DOCX_MIME})


@dataclass(frozen=True, slots=True)
class UploadResult:
    source_id: UUID
    version_id: UUID
    parse_run_id: UUID | None
    deduplicated: bool
    hidden_text: str = "keep"  # 그 파싱 실행이 실제로 쓰는 값 (같은 파일이면 처음 올린 때의 값)
    # 원본의 용도·등급. 같은 파일을 다시 올리면 처음 원본의 용도 그대로다 (바꾸려면 change_purpose).
    purpose: str = SourcePurpose.EXPERIENCE.value
    data_class: str = DataClass.PRIVATE.value
    # c0039: 용도 확인을 기다림, 이 기기에서 읽은 글을 붙인 블록 수
    purpose_pending: bool = False
    text_blocks: int = 0


_STRICTNESS = {DataClass.PUBLIC: 0, DataClass.PRIVATE: 1, DataClass.RESTRICTED: 2}


async def _existing(
    db: Database, workspace_id: UUID, sha256: str, data_class: DataClass
) -> UploadResult | None:
    async with db.workspace_session(workspace_id) as s:
        version = await s.scalar(select(SourceVersion).where(SourceVersion.sha256 == sha256))
        if version is None:
            return None
        # 같은 파일을 더 엄격한 등급으로 다시 올리면 등급을 올린다 (낮추지는 않는다).
        source = await s.get(Source, version.source_id)
        if _STRICTNESS[data_class] > _STRICTNESS[DataClass(source.data_class)]:
            source.data_class = data_class.value
        run = (
            await s.execute(
                select(ParseRun.id, ParseRun.hidden_text).where(
                    ParseRun.source_version_id == version.id,
                    ParseRun.pipeline_version == PIPELINE_VERSION,
                )
            )
        ).first()
        return UploadResult(
            version.source_id,
            version.id,
            run[0] if run else None,
            deduplicated=True,
            hidden_text=run[1] if run else "keep",
            purpose=source.purpose,
            data_class=source.data_class,
            purpose_pending=bool(source.purpose_pending),
        )


MAX_PURPOSE_HINT = 200


async def upload_source(
    db: Database,
    blobs: BlobStore,
    actor: ActorContext,
    *,
    filename: str | None,
    data: bytes,
    declared_mime: str | None,
    max_bytes: int,
    title: str | None = None,
    data_class: str = DataClass.PRIVATE,
    hidden_text: str = "keep",
    purpose: str | None = SourcePurpose.EXPERIENCE,
    purpose_pending: bool = False,
    purpose_hint: str | None = None,
    text_layer=None,
) -> UploadResult:
    """``hidden_text``: ``exclude``면 숨김 서식 글을 원본 블록에서 뺀다 (DOCX만, 다른 형식은 ``keep``).
    같은 파일을 다시 올리면 처음 파싱의 값을 그대로 돌려준다 (다시 파싱하지 않는다).

    ``purpose``: experience(기본) | proof | reference. 증빙은 고른 등급과 상관없이 ``restricted``다.
    같은 파일을 증빙으로 다시 올리면 등급만 올린다 (용도는 처음 원본 그대로, 결과의 ``purpose``).

    c0039: ``purpose_pending``이면 받은 자료함에서 사람의 확인을 기다리고 등급은 ``restricted``다.
    ``text_layer``(``domain.ingest.TextLayer``)는 이 기기에서 읽은 글: 사진·스캔 PDF에만, 같은 트랜잭션에서
    붙인다 (worker가 읽기를 끝낼 때 그 쪽을 못 읽은 쪽으로 보지 않는다). 같은 파일이면 ``ingest.attach_text_layer``."""
    if not data:
        raise UploadRejected("empty", 400)
    if len(data) > max_bytes:
        raise UploadRejected("too_large", 413)
    try:
        dc = DataClass(data_class)
    except ValueError as exc:
        raise UploadRejected("invalid_data_class", 422) from exc
    try:
        pp = source_purpose(purpose)
    except ValueError as exc:
        raise UploadRejected("invalid_purpose", 422) from exc
    dc = PURPOSE_DATA_CLASS.get(pp, dc)
    if purpose_pending:
        dc = DataClass.RESTRICTED
    hint = " ".join((purpose_hint or "").split())[:MAX_PURPOSE_HINT] or None
    if hidden_text not in HIDDEN_TEXT_CHOICES:
        raise UploadRejected("invalid_hidden_text", 422)
    name = clean_filename(filename)
    mime = resolve_mime(data, declared_mime, name)
    if mime not in HIDDEN_TEXT_EXCLUDABLE:
        hidden_text = "keep"
    sha256 = hashlib.sha256(data).hexdigest()
    ws = actor.workspace_id
    text_blocks = 0

    if text_layer is not None:
        from career_core.domain.ingest import TextLayerError, check_text_layer

        try:
            text_layer = check_text_layer(text_layer, mime=mime)
        except TextLayerError as exc:
            raise UploadRejected(exc.code, 422) from exc

    if found := await _existing(db, ws, sha256, dc):
        return found

    # 파일 쓰기는 DB 트랜잭션 밖에서. 내용 주소라 중복 쓰기는 무해하다.
    key = await blobs.put(ws, sha256, data, mime)
    source = Source(
        id=uuid4(),
        workspace_id=ws,
        title=(title or name)[:300],
        data_class=dc,
        purpose=pp.value,
        created_by=actor.subject_id,
        purpose_pending=bool(purpose_pending),
        purpose_hint=hint,
    )
    version = SourceVersion(
        id=uuid4(),
        workspace_id=ws,
        source_id=source.id,
        sha256=sha256,
        size_bytes=len(data),
        mime_type=mime,
        original_filename=name,
        blob_key=key,
    )
    run = ParseRun(
        id=uuid4(),
        workspace_id=ws,
        source_version_id=version.id,
        pipeline_version=PIPELINE_VERSION,
        status=ParseStatus.PENDING,
        requested_by=actor.subject_id,
        hidden_text=hidden_text,
    )
    try:
        async with db.workspace_session(ws) as s:
            # 복합 FK는 ORM 관계로 선언하지 않았으므로 순서대로 flush한다.
            for row in (
                source,
                version,
                run,
                Job(workspace_id=ws, kind=PARSE_JOB, target_id=run.id),
            ):
                s.add(row)
                await s.flush()
            if text_layer is not None:
                from career_core.usecases.ingest import store_with_new_run

                text_blocks = await store_with_new_run(s, ws, run, text_layer, mime)
    except IntegrityError:
        # 동시에 같은 파일이 올라온 경우: 먼저 커밋된 쪽을 돌려준다.
        if found := await _existing(db, ws, sha256, dc):
            return found
        raise
    return UploadResult(
        source.id,
        version.id,
        run.id,
        deduplicated=False,
        hidden_text=hidden_text,
        purpose=pp.value,
        data_class=dc.value,
        purpose_pending=bool(purpose_pending),
        text_blocks=text_blocks,
    )


# ---------------------------------------------------------------- 목록

# 받은 자료함에 올리는 이유 (앞의 것이 더 급하다). None = 다 처리됨 (기록 쪽에서 찾아 들어간다).
ATTENTION_ORDER = (
    "purpose_unconfirmed",  # c0039: 넣을 때 용도를 확신하지 못함 (확인 필요)
    "parse_failed",  # 읽기 실패
    "extraction_failed",  # 경험 추출 실패
    "processing",  # 읽는 중 (외부 OCR 대기 포함)
    "extracting",  # 경험 추출 중
    "pending_claims",  # 확인할 주장 후보가 남음
    "parse_partial",  # 일부 쪽만 읽음
    "extraction_partial",  # 일부 블록만 모델에 보냄
    "not_extracted",  # 내 경험 자료인데 아직 추출을 요청하지 않음
)


def attention_of(
    purpose: str,
    parse_status: str | None,
    extraction_status: str | None,
    pending_claims: int,
    purpose_pending: bool = False,
) -> str | None:
    """받은 자료함에 둘 이유 하나. 증빙·참고는 읽기 상태만 본다 (추출하지 않는다)."""
    found: set[str] = set()
    if purpose_pending:
        found.add("purpose_unconfirmed")
    if parse_status is None or parse_status in (ParseStatus.PENDING, ParseStatus.WAITING_PROVIDER):
        found.add("processing")
    elif parse_status == ParseStatus.FAILED:
        found.add("parse_failed")
    elif parse_status == ParseStatus.PARTIAL:
        found.add("parse_partial")
    if purpose == SourcePurpose.EXPERIENCE and parse_status in (
        ParseStatus.COMPLETE,
        ParseStatus.PARTIAL,
    ):
        if extraction_status is None:
            found.add("not_extracted")
        elif extraction_status == ExtractionStatus.PENDING:
            found.add("extracting")
        elif extraction_status == ExtractionStatus.FAILED:
            found.add("extraction_failed")
        elif extraction_status == ExtractionStatus.PARTIAL:
            found.add("extraction_partial")
    if pending_claims:
        found.add("pending_claims")
    return next((a for a in ATTENTION_ORDER if a in found), None)


@dataclass(frozen=True, slots=True)
class SourceListItem:
    id: UUID
    title: str
    data_class: str
    created_at: datetime
    versions: int
    latest_version_id: UUID | None
    original_filename: str | None
    mime_type: str | None
    size_bytes: int | None
    # 최신 버전의 최신 파이프라인 읽기 결과
    parse_run_id: UUID | None
    parse_status: str | None
    pages_total: int | None
    missing_pages: list[int]
    parse_error: str | None
    # 그 읽기 결과로 요청한 마지막 경험 추출
    extraction_run_id: UUID | None
    extraction_status: str | None
    claims_created: int | None
    # c0032
    purpose: str = SourcePurpose.EXPERIENCE.value
    # 이 원본에서 뽑았고 아직 확인하지 않은 주장 후보 수 (활성 경험만)
    pending_claims: int = 0
    # 받은 자료함에 둘 이유 (``ATTENTION_ORDER``), 다 처리됐으면 None
    attention: str | None = None
    # c0039: 용도 확인을 기다림, 제안의 이유, 읽은 길(local | device | model | provider)
    purpose_pending: bool = False
    purpose_hint: str | None = None
    read_by: str | None = None


def _source_runs(source_ids):
    """원본 → 그 원본의 추출 실행 (모든 버전·파이프라인)."""
    return (
        select(SourceVersion.source_id, ExtractionRun.id)
        .join(ParseRun, ParseRun.source_version_id == SourceVersion.id)
        .join(ExtractionRun, ExtractionRun.parse_run_id == ParseRun.id)
        .where(SourceVersion.source_id.in_(source_ids))
    )


async def _pending_counts(s, source_ids: list[UUID]) -> dict[UUID, int]:
    if not source_ids:
        return {}
    runs = _source_runs(source_ids).subquery()
    rows = await s.execute(
        select(runs.c.source_id, func.count(Claim.id))
        .join(Claim, Claim.extraction_run_id == runs.c.id)
        .join(Experience, Experience.id == Claim.experience_id)
        .where(Claim.status == ClaimStatus.EXTRACTED, Experience.status == "active")
        .group_by(runs.c.source_id)
    )
    return dict(rows.all())


async def list_sources(
    db: Database,
    actor: ActorContext,
    *,
    limit: int = 100,
    purpose: str | None = None,
    attention: bool = False,
) -> list[SourceListItem]:
    """올린 원본 목록 (최근 것부터)과 각 원본 최신 버전의 읽기·추출 상태.

    ``purpose``: 그 용도만. ``attention``: 받은 자료함 — 처리 중·실패·일부만 처리·확인할 후보가 있는 원본만
    (상한은 거른 뒤에 건다. 오래된 실패도 빠지지 않게)."""
    limit = max(1, min(limit, 500))
    if purpose is not None:
        purpose = source_purpose(purpose).value
    async with db.workspace_session(actor.workspace_id) as s:
        stmt = select(Source).order_by(Source.created_at.desc(), Source.id)
        if purpose is not None:
            stmt = stmt.where(Source.purpose == purpose)
        if not attention:
            stmt = stmt.limit(limit)
        sources = (await s.execute(stmt)).scalars().all()
        ids = [x.id for x in sources]
        versions: dict[UUID, list[SourceVersion]] = {}
        if ids:
            for v in (
                await s.execute(
                    select(SourceVersion)
                    .where(SourceVersion.source_id.in_(ids))
                    .order_by(SourceVersion.created_at.desc(), SourceVersion.id)
                )
            ).scalars():
                versions.setdefault(v.source_id, []).append(v)
        latest_ids = [vs[0].id for vs in versions.values()]
        runs: dict[UUID, ParseRun] = {}
        if latest_ids:
            for r in (
                await s.execute(
                    select(ParseRun)
                    .where(ParseRun.source_version_id.in_(latest_ids))
                    .order_by(ParseRun.pipeline_version, ParseRun.created_at)
                )
            ).scalars():
                runs[r.source_version_id] = r  # 마지막(가장 높은 파이프라인)이 남는다
        run_ids = [r.id for r in runs.values()]
        extractions: dict[UUID, ExtractionRun] = {}
        if run_ids:
            for e in (
                await s.execute(
                    select(ExtractionRun)
                    .where(ExtractionRun.parse_run_id.in_(run_ids))
                    .order_by(ExtractionRun.created_at)
                )
            ).scalars():
                extractions[e.parse_run_id] = e
        pending = await _pending_counts(s, ids)
    out = []
    for src in sources:
        vs = versions.get(src.id, [])
        v = vs[0] if vs else None
        r = runs.get(v.id) if v else None
        e = extractions.get(r.id) if r else None
        n = pending.get(src.id, 0)
        why = attention_of(
            src.purpose,
            r.status if r else None,
            e.status if e else None,
            n,
            bool(src.purpose_pending),
        )
        if attention and why is None:
            continue
        out.append(
            SourceListItem(
                id=src.id,
                title=src.title,
                data_class=src.data_class,
                created_at=src.created_at,
                versions=len(vs),
                latest_version_id=v.id if v else None,
                original_filename=v.original_filename if v else None,
                mime_type=v.mime_type if v else None,
                size_bytes=v.size_bytes if v else None,
                parse_run_id=r.id if r else None,
                parse_status=r.status if r else None,
                pages_total=r.pages_total if r else None,
                missing_pages=list(r.missing_pages or []) if r else [],
                parse_error=r.error_code if r else None,
                extraction_run_id=e.id if e else None,
                extraction_status=e.status if e else None,
                claims_created=e.claims_created if e else None,
                purpose=src.purpose,
                pending_claims=n,
                attention=why,
                purpose_pending=bool(src.purpose_pending),
                purpose_hint=src.purpose_hint,
                read_by=read_by(r),
            )
        )
        if len(out) >= limit:
            break
    return out


def read_by(run: ParseRun | None) -> str | None:
    """읽은 길: ``device``(이 기기의 OCR) | ``model``(연결된 비전 모델) | ``provider``(외부 파서) |
    ``local``(이 서버가 글자를 읽음). 아직 읽지 않았으면 None."""
    if run is None:
        return None
    if run.text_layer and run.text_layer.get("origin") in ("device", "model"):
        return run.text_layer["origin"]
    if run.provider_job_id:
        return "provider"
    if run.status in (ParseStatus.COMPLETE, ParseStatus.PARTIAL):
        return "local"
    return None


# ---------------------------------------------------------------- 용도


class SourceNotFound(LookupError):
    pass


def _confirmed_citing(source_id: UUID):
    """이 원본(모든 버전)의 원문 위치를 근거로 단 확인된 주장."""
    return (
        select(func.count(func.distinct(Claim.id)))
        .select_from(Claim)
        .join(ClaimEvidence, ClaimEvidence.claim_id == Claim.id)
        .join(SourceSpan, SourceSpan.id == ClaimEvidence.source_span_id)
        .join(DocumentBlock, DocumentBlock.id == SourceSpan.document_block_id)
        .join(ParseRun, ParseRun.id == DocumentBlock.parse_run_id)
        .join(SourceVersion, SourceVersion.id == ParseRun.source_version_id)
        .where(
            SourceVersion.source_id == source_id,
            Claim.status.in_([c.value for c in CONFIRMED_STATUSES]),
        )
    )


@dataclass(frozen=True, slots=True)
class SourceClaimCounts:
    pending: int  # 이 원본에서 뽑은 확인 전 후보
    confirmed: int  # 이 원본을 근거로 단 확인된 주장


async def source_claim_counts(
    db: Database, actor: ActorContext, source_id: UUID
) -> SourceClaimCounts:
    async with db.workspace_session(actor.workspace_id) as s:
        pending = (await _pending_counts(s, [source_id])).get(source_id, 0)
        confirmed = await s.scalar(_confirmed_citing(source_id)) or 0
    return SourceClaimCounts(pending, confirmed)


@dataclass(frozen=True, slots=True)
class PurposeChange:
    source_id: UUID
    purpose: str
    data_class: str
    changed: bool
    # experience → 증빙·참고: 닫은 주장 후보·AI 경험·대기 중이던 추출 수
    discarded_claims: int = 0
    discarded_experiences: int = 0
    cancelled_extractions: int = 0
    # → experience: 큐에 넣은(또는 이미 대기 중인) 추출. 읽기가 안 끝났거나 권한이 없으면 None
    extraction_run_id: UUID | None = None


async def change_purpose(
    db: Database,
    actor: ActorContext,
    source_id: UUID,
    purpose: str,
    *,
    data_class: str | None = None,
    can_extract: bool = False,
) -> PurposeChange:
    """용도(와 선택으로 자료 등급)를 바꾼다. 규칙은 모듈 설명.

    ``data_class``: 함께 바꿀 등급. 증빙은 ``restricted``만 (다른 값은 ``proof_requires_restricted``).
    증빙에서 다른 용도로 바꿀 때 등급을 주지 않으면 ``restricted``를 그대로 둔다 (낮추는 것은 사람이 고른다).
    ``can_extract``: → experience일 때 추출을 큐에 넣을지 (``brain.extract`` 권한, HTTP가 판단).
    """
    try:
        target = source_purpose(purpose)
    except ValueError as exc:
        raise ClaimRuleError("invalid_purpose") from exc
    try:
        want_dc = DataClass(data_class) if data_class is not None else None
    except ValueError as exc:
        raise ClaimRuleError("invalid_data_class") from exc
    forced = PURPOSE_DATA_CLASS.get(target)
    if forced is not None and want_dc is not None and want_dc != forced:
        raise ClaimRuleError("proof_requires_restricted")

    ws = actor.workspace_id
    discarded_claims = discarded_exps = cancelled = 0
    parse_run_id: UUID | None = None
    async with db.workspace_session(ws) as s:
        src = await s.get(Source, source_id, with_for_update=True)
        if src is None:
            raise SourceNotFound(source_id)
        before = SourcePurpose(src.purpose)
        new_dc = forced or want_dc or DataClass(src.data_class)
        was_pending = bool(src.purpose_pending)
        if was_pending and actor.is_human:
            # c0039: 사람이 용도를 확인했다 (그대로여도). 확인 대기 동안의 restricted는 고른 등급으로
            src.purpose_pending = False
        elif was_pending:
            new_dc = DataClass.RESTRICTED  # 에이전트는 확인하지 못한다: 대기 중엔 restricted 그대로
        confirmed = was_pending and not src.purpose_pending
        if before == target and new_dc == src.data_class and not confirmed:
            return PurposeChange(src.id, src.purpose, src.data_class, changed=False)

        if before == SourcePurpose.REFERENCE and target != SourcePurpose.REFERENCE:
            # c0035: 참고 글(reference_items)이 쓰는 원본은 참고로 둔다. 남의 글이 내 경험 추출로 흘러가지 않게
            used = await s.scalar(
                select(func.count())
                .select_from(ReferenceItem)
                .where(ReferenceItem.source_id == source_id)
            )
            if used:
                raise ClaimRuleError("source_has_reference_items")
        if target != SourcePurpose.EXPERIENCE and before == SourcePurpose.EXPERIENCE:
            if await s.scalar(_confirmed_citing(source_id)):
                raise ClaimRuleError("source_has_confirmed_claims")
            run_ids = [r for _, r in (await s.execute(_source_runs([source_id]))).all()]
            if run_ids:
                # 대기 중인 추출을 먼저 닫는다 (실행 중인 worker는 저장 전에 상태를 다시 보고 멈춘다).
                cancelled = (
                    await s.execute(
                        update(ExtractionRun)
                        .where(
                            ExtractionRun.id.in_(run_ids),
                            ExtractionRun.status == ExtractionStatus.PENDING,
                        )
                        .values(
                            status=ExtractionStatus.FAILED.value,
                            error_code="purpose_changed",
                            updated_at=utcnow(),
                        )
                    )
                ).rowcount
                discarded_claims = (
                    await s.execute(
                        update(Claim)
                        .where(
                            Claim.extraction_run_id.in_(run_ids),
                            Claim.status == ClaimStatus.EXTRACTED,
                        )
                        .values(status=ClaimStatus.RETIRED.value, updated_at=utcnow())
                    )
                ).rowcount
                # 이 원본에서 AI가 만든 경험 중 남은 주장도, 아래 경험도 없는 것
                live_claim = (
                    select(Claim.id)
                    .where(
                        Claim.experience_id == Experience.id, Claim.status != ClaimStatus.RETIRED
                    )
                    .exists()
                )
                exps = (
                    await s.execute(
                        select(Experience).where(
                            Experience.extraction_run_id.in_(run_ids),
                            Experience.origin == ClaimOrigin.AI_EXTRACTION,
                            Experience.status == "active",
                            ~live_claim,
                        )
                    )
                ).scalars()
                for exp in exps:
                    has_child = await s.scalar(
                        select(func.count()).where(
                            Experience.parent_id == exp.id, Experience.status == "active"
                        )
                    )
                    if has_child:
                        continue
                    exp.status = "retired"
                    exp.updated_at = utcnow()
                    discarded_exps += 1

        src.purpose = target.value
        src.data_class = new_dc.value
        if (
            target == SourcePurpose.EXPERIENCE
            and (before != SourcePurpose.EXPERIENCE or (was_pending and not src.purpose_pending))
            and can_extract
        ):
            parse_run_id = await s.scalar(
                select(ParseRun.id)
                .join(SourceVersion, SourceVersion.id == ParseRun.source_version_id)
                .where(
                    SourceVersion.source_id == source_id,
                    ParseRun.status.in_([ParseStatus.COMPLETE, ParseStatus.PARTIAL]),
                )
                .order_by(
                    SourceVersion.created_at.desc(),
                    ParseRun.pipeline_version.desc(),
                    ParseRun.created_at.desc(),
                )
                .limit(1)
            )
        # 색인 대상이 바뀐다 (증빙은 색인하지 않는다, 참고는 색인한다)
        await request_index(s, ws)
        result = PurposeChange(
            src.id,
            src.purpose,
            src.data_class,
            changed=True,
            discarded_claims=discarded_claims,
            discarded_experiences=discarded_exps,
            cancelled_extractions=cancelled,
        )

    if parse_run_id is not None:
        from career_core.usecases.extraction import request_extraction

        ext_id = await request_extraction(db, actor, parse_run_id)
        result = replace(result, extraction_run_id=ext_id)
    return result

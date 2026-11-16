"""O11 완료 기준: 빈 환경에서 복원 후 핵심 데이터 수·해시 확인.

고정 회귀 시험 1(다른 공간은 내보내기에 없다), 12(제출본 불변), 13(복원된 원본·관계·문서 연결).
모든 자료는 합성이다.
"""

import contextlib
import hashlib
import io
import json
import re
import secrets
import tarfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import Base, Database, JobPosting, JobPostingVersion, ModelCall
from career_core.db.migrate import upgrade
from career_core.db.models import ApplicationSubmission, DocumentSuggestion, Workspace
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import parsing_handlers, run_until_idle
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext
from career_core.testing import admin_url, migrated_database, postgres_available
from career_core.usecases import application_stages as stages_uc
from career_core.usecases import applications as apps_uc
from career_core.usecases import brain, interviews
from career_core.usecases import documents as docs_uc
from career_core.usecases import postings as postings_uc
from career_core.usecases import study as study_uc
from career_core.usecases.export import (
    EXCLUDED_TABLES,
    FORMAT_VERSION,
    TOMBSTONE_TABLES,
    ArchiveError,
    Column,
    ExportError,
    ForeignKey,
    PlanError,
    Table,
    _row_order,
    build_plan,
    export_workspace,
    read_catalog,
    restore_workspace,
)
from career_core.usecases.maintenance import maintenance_info
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.sources import upload_source
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
RESUME = (
    "Acme Corp (synthetic), Backend Engineer, 2021.03 - 2023.02\n\n"
    "Built the order service in FastAPI and cut p95 latency from 800ms to 200ms."
)
FIELDS = {
    "title": "백엔드 개발자 (합성)",
    "company": "가상회사",
    "requirements": ["Python 경험", "SQL 이해"],
    "preferred": ["AWS 운영 경험"],
    "process": ["서류 - 코딩테스트 - 면접"],
    "questions": [{"prompt": "지원 동기를 작성해 주세요. (500자 이내)", "char_limit": 500}],
}
SCREENSHOT = b"\x89PNG\r\n\x1a\n synthetic screenshot bytes"


def owner_of(ws, actor):
    return ActorContext(actor, ws, scopes_for_role(Role.OWNER))


async def build_workspace(db, blobs, owner, *, tag):
    """브레인·원본·공고(버전·스크린샷)·지원(수정안·제출본)·학습·모델 장부를 고루 만든다."""
    ws = owner.workspace_id
    up = await upload_source(
        db,
        blobs,
        owner,
        filename=f"cv-{tag}.txt",
        data=f"{RESUME}\n\n{tag}".encode(),
        declared_mime="text/plain",
        max_bytes=1 << 20,
    )
    deps = ParsingDeps(
        db=db, blobs=blobs, egress=ProviderEgressPolicy(), local_parsers=default_local_parsers()
    )
    await run_until_idle(db, parsing_handlers(deps))
    async with db.workspace_session(ws) as s:
        block_id = await s.scalar(
            text("SELECT id FROM core.document_blocks WHERE parse_run_id = :r ORDER BY ordinal"),
            {"r": up.parse_run_id},
        )
    span = await brain.create_span(db, owner, block_id, 0, 9)
    exp = await brain.create_experience(
        db, owner, title=f"주문 서비스 개선 {tag}", organization="가상커머스", role="백엔드"
    )
    claim = await brain.add_claim(
        db,
        owner,
        exp,
        statement=f"주문 API p95 지연을 줄였다 {tag}",
        kind="result",
        contribution="personal",
        span_ids=[span],
    )
    session = await interviews.start_interview(db, owner, exp)
    view = await interviews.get_interview(db, owner, session)
    await interviews.answer_question(
        db,
        owner,
        view.questions[0].id,
        answer="결제 배치를 Python으로 옮겼다 (합성)",
        kind="action",
        contribution="team",
        experience_id=exp,
    )
    async with db.workspace_session(ws) as s:
        s.add(
            ModelCall(
                workspace_id=ws,
                actor_id=owner.subject_id,
                purpose="extract",
                endpoint="proxy",
                requested_model="m",
                status="ok",
                input_tokens=500,
                output_tokens=200,
                billing="metered",
                cost_usd=Decimal("0.0012345678"),
                cost_basis="estimated",
                price_refs=["synthetic:price"],
            )
        )

    # 공고: 버전 + 스크린샷 원본 → 확정 (공고 ↔ 확정 버전 FK가 순환한다)
    key = await blobs.put(ws, hashlib.sha256(SCREENSHOT).hexdigest(), SCREENSHOT, "image/png")
    url = f"https://jobs.example.test/{tag}/1"
    async with db.workspace_session(ws) as s:
        p = JobPosting(
            workspace_id=ws,
            canonical_url=url,
            source_url=url,
            status="draft",
            created_by=owner.subject_id,
        )
        s.add(p)
        await s.flush()
        s.add(
            JobPostingVersion(
                workspace_id=ws,
                posting_id=p.id,
                version_no=1,
                final_url=url,
                method="manual_text",
                adapter="text",
                content_hash=uuid4().hex,
                text='합성 공고 본문 — 줄바꿈\n과 "따옴표" 포함',
                extracted={
                    "title": {"value": FIELDS["title"], "evidence": {"kind": "user", "quote": None}}
                },
                missing=[],
                changed_fields=[],
                screenshot_key=key,
                created_by=owner.subject_id,
            )
        )
    posting = p.id
    await postings_uc.confirm_posting(db, owner, posting, overrides=FIELDS)

    # 지원: revision 1 → AI 수정안(기준 revision 1) → 적용(revision 2, 수정안을 가리킴)
    app = await apps_uc.create_application(db, owner, posting)
    doc = (await apps_uc.get_application(db, owner, app)).documents[0].id
    await apps_uc.save_revision(
        db,
        owner,
        app,
        doc,
        base_revision=0,
        operations=[
            {"op": "insert", "text": "주문 API 지연을 줄였습니다.", "claim_ids": [str(claim)]}
        ],
    )
    current = await apps_uc.get_document(db, owner, app, doc)
    async with db.workspace_session(ws) as s:
        r1 = await s.scalar(
            text("SELECT id FROM core.document_revisions WHERE document_id = :d"), {"d": doc}
        )
        sug = DocumentSuggestion(
            workspace_id=ws,
            document_id=doc,
            base_revision_id=r1,
            block_id=current.blocks[0].id,
            original_text=current.blocks[0].text,
            suggested_text="주문 API 지연을 800ms에서 200ms로 줄였습니다.",
            reason="수치 근거 (합성)",
            status="proposed",
            created_by=owner.subject_id,
        )
        s.add(sug)
    await apps_uc.apply_suggestion(db, owner, app, doc, sug.id, base_revision=1)
    # c0016: 수정안·인터뷰 질문 요청 기록, 실행 태그가 붙은 장부 행 (장부 → 실행 FK)
    await _add_c0016_rows(db, owner, doc, r1, session, sug.block_id)
    # c0012: 공용 문서 → 지원 문서로 복사 (복사본 → 원본 문서·revision FK가 순환한다), 문서 설정
    shared = await docs_uc.create_shared_document(db, owner, kind="resume", title=f"기본 {tag}")
    await apps_uc.save_revision(
        db,
        owner,
        None,
        shared,
        base_revision=0,
        operations=[{"op": "insert", "text": "공용 이력서 문단", "claim_ids": [str(claim)]}],
    )
    copy = await docs_uc.copy_to_application(db, owner, app, shared)
    await docs_uc.update_settings(db, owner, cliches=["합성상투어"])
    submission = await apps_uc.submit_application(
        db, owner, app, revisions={doc: 2, copy: 1}, note="합성"
    )
    await stages_uc.add_stage(db, owner, app, kind="document", outcome="passed")

    # 학습: 요건 ↔ 주장, 계획, 기록, 응시, 오답, 복습, 경험 후보
    await study_uc.link_claim(
        db, owner, posting, field="requirements", text="Python 경험", claim_id=claim
    )
    plan = await study_uc.plan_from_posting(db, owner, posting)
    item = plan.items_created[0]
    await study_uc.log_session(db, owner, item, minutes=45, started_at=T0, notes="합성 메모")
    attempt = await study_uc.record_attempt(
        db, owner, study_item_id=item, result="failed", score=70, max_score=100, taken_at=T0
    )
    mistake = await study_uc.add_mistake(
        db, owner, attempt_id=attempt, question="합성 문제: 구간 합", reason="time", now=T0
    )
    await study_uc.review_mistake(db, owner, mistake, "good", now=T0 + timedelta(days=1))
    await study_uc.propose_claim(
        db, owner, item, experience_id=exp, statement=f"SQL 학습 결과 {tag}", attempt_id=attempt
    )
    return {
        "source": up.source_id,
        "experience": exp,
        "claim": claim,
        "span": span,
        "posting": posting,
        "application": app,
        "document": doc,
        "submission": submission,
        "shared": shared,
        "copy": copy,
        "item": item,
        "screenshot": key,
    }


async def _add_c0016_rows(db, owner, doc, revision_id, session_id, block_id):
    from career_core.db.models import (
        DocumentSuggestionRequest,
        InterviewQuestion,
        InterviewQuestionRequest,
        Run,
    )

    ws = owner.workspace_id
    async with db.workspace_session(ws) as s:
        run = Run(
            workspace_id=ws,
            workflow="suggest_document_batch",
            status="done",
            input={},
            position={},
            finished_at=datetime.now(UTC),
            created_by=owner.subject_id,
        )
        s.add(run)
        await s.flush()
        s.add(
            ModelCall(
                workspace_id=ws,
                actor_id=owner.subject_id,
                purpose="doc_suggest",
                endpoint="proxy",
                requested_model="m",
                status="ok",
                billing="metered",
                cost_usd=Decimal("0.0001"),
                cost_basis="metered",
                run_id=run.id,
                run_step=1,
            )
        )
        s.add(
            DocumentSuggestionRequest(
                workspace_id=ws,
                document_id=doc,
                base_revision_id=revision_id,
                block_ids=[block_id],
                questions=["합성 질문: 지연을 어떻게 쟀나요?"],
                proposed=1,
                idempotency_key=f"run:{run.id}:block:{block_id}",
                run_id=run.id,
                created_by=owner.subject_id,
            )
        )
        req = InterviewQuestionRequest(
            workspace_id=ws,
            session_id=session_id,
            requested=1,
            created=1,
            created_by=owner.subject_id,
        )
        s.add(req)
        await s.flush()
        s.add(
            InterviewQuestion(
                workspace_id=ws,
                session_id=session_id,
                ordinal=99,
                focus="metric",
                prompt="합성 AI 질문: 수치는 어떻게 쟀나요?",
                origin="ai",
                request_id=req.id,
            )
        )


@pytest.fixture
async def env(db, tmp_path):
    blobs = LocalBlobStore(tmp_path / "blobs")
    ws_a, a = await create_workspace(db, name="A (합성)", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B (합성)", owner_display_name="bob")
    owner, other = owner_of(ws_a, a), owner_of(ws_b, b)
    data_a = await build_workspace(db, blobs, owner, tag="tag-alpha")
    data_b = await build_workspace(db, blobs, other, tag="tag-bravo")
    return {"blobs": blobs, "owner": owner, "other": other, "a": data_a, "b": data_b}


def members(path):
    with tarfile.open(path, "r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers()}


def rewrite(path, out, change):
    """묶음의 파일 하나를 바꿔 다시 쓴다 (변조 시험용)."""
    files = members(path)
    change(files)
    with tarfile.open(out, "w:gz") as tar:
        for name in ["manifest.json", *sorted(n for n in files if n != "manifest.json")]:
            info = tarfile.TarInfo(name)
            info.size = len(files[name])
            tar.addfile(info, io.BytesIO(files[name]))
    return out


# ---------------------------------------------------------------- 계획


async def test_every_workspace_table_is_exported_or_excluded(db):
    """새 표(O08 등)가 생겨도 빠짐없이 분류된다: 내보내거나, 이유를 적고 뺀다."""
    async with db.workspace_session(uuid4()) as s:
        tables = await read_catalog(await s.connection())
    plan = build_plan(tables)
    exported = {t.name for t in plan.tables}
    catalog_ws = {t.name for t in tables if any(c.name == "workspace_id" for c in t.columns)}
    assert catalog_ws <= exported | set(EXCLUDED_TABLES)
    assert exported & set(EXCLUDED_TABLES) == set()
    for table in Base.metadata.sorted_tables:
        if "workspace_id" in table.c:
            name = f"{table.schema}.{table.name}"
            assert name in exported or name in EXCLUDED_TABLES, name
    assert {"core.workspaces", "core.actors", "core.memberships"} <= exported
    assert "core.alembic_version" in plan.skipped
    assert not any(t.startswith("personal.") for t in exported)
    # FK 순서: 부모가 먼저
    pos = {t.name: i for i, t in enumerate(plan.tables)}
    for t in plan.tables:
        for fk in t.foreign_keys:
            if not fk.deferrable:
                assert pos[fk.ref_table] < pos[t.name], (t.name, fk.ref_table)
    # 순환 FK(공고 ↔ 확정 버전, revision ↔ 수정안, 실행 ↔ 승인, 문서 ↔ 복사 출처)는 한쪽이
    # DEFERRABLE이라 표 순서가 정해진다
    assert {fk.name for fk in plan.deferred} == {
        "fk_job_postings_confirmed_version",
        "fk_document_revisions_suggestion",
        "fk_runs_approval_id",
        # c0012: 복사본 → 원본 문서(같은 표), 복사본 → 원본 revision(revision → 문서 순환)
        "fk_application_documents_source_document",
        "fk_application_documents_source_revision",
    }
    assert plan.groups == ()


def _t(name, fks=()):
    """합성 표: (칸, 부모 표, 미룰 수 있음) 목록으로 공간 복합 FK를 만든다."""
    cols = ["id", "workspace_id", *(c for c, _, _ in fks)]
    return Table(
        name=name,
        columns=tuple(Column(c, "uuid", c in ("id", "workspace_id")) for c in cols),
        primary_key=("id",),
        foreign_keys=tuple(
            ForeignKey(f"fk_{c}", name, ("workspace_id", c), ref, ("workspace_id", "id"), d)
            for c, ref, d in fks
        ),
        scope="workspace",
    )


def test_plan_order_and_unsupported_references():
    tables = [_t("core.b", [("a_id", "core.a", False)]), _t("core.a")]
    assert [t.name for t in build_plan(tables, {}).tables] == ["core.a", "core.b"]
    with pytest.raises(PlanError, match="not exported"):
        build_plan(tables, {"core.a": "test"})


def test_cyclic_tables_are_inserted_row_by_row():
    """미룰 수 없는 FK로 표끼리 순환하면 행 단위로 부모 먼저 넣는다. 행까지 순환하면 거부."""
    plan = build_plan(
        [_t("core.p", [("c_id", "core.c", False)]), _t("core.c", [("p_id", "core.p", False)])], {}
    )
    assert plan.groups == (frozenset({"core.c", "core.p"}),)
    ws = str(uuid4())
    p1, c1, p2 = (str(uuid4()) for _ in range(3))
    lines = {
        "core.p": [
            json.dumps({"id": p1, "workspace_id": ws, "c_id": None}),
            json.dumps({"id": p2, "workspace_id": ws, "c_id": c1}),
        ],
        "core.c": [json.dumps({"id": c1, "workspace_id": ws, "p_id": p1})],
    }
    steps = _row_order(plan, plan.groups[0], lines)
    assert [(name, [json.loads(x)["id"] for x in rows]) for name, rows in steps] == [
        ("core.p", [p1]),
        ("core.c", [c1]),
        ("core.p", [p2]),
    ]
    lines["core.p"][0] = json.dumps({"id": p1, "workspace_id": ws, "c_id": c1})
    with pytest.raises(PlanError, match="cycle"):
        _row_order(plan, plan.groups[0], lines)
    # 한쪽을 DEFERRABLE로 두면 순환 묶음이 없어진다
    plan = build_plan(
        [_t("core.p", [("c_id", "core.c", True)]), _t("core.c", [("p_id", "core.p", False)])], {}
    )
    assert plan.groups == () and [t.name for t in plan.tables] == ["core.p", "core.c"]


# ---------------------------------------------------------------- 내보내기·복원


async def test_export_restore_roundtrip(db, env, tmp_path):
    owner, other, a, b = env["owner"], env["other"], env["a"], env["b"]
    out = tmp_path / "a.tar.gz"
    editor = ActorContext(owner.subject_id, owner.workspace_id, scopes_for_role(Role.EDITOR))
    with pytest.raises(ExportError, match="forbidden"):
        await export_workspace(db, env["blobs"], editor, out)
    report = await export_workspace(db, env["blobs"], owner, out)
    assert report.sha256 == hashlib.sha256(out.read_bytes()).hexdigest()
    files = members(out)
    manifest = json.loads(files["manifest.json"])
    assert manifest["workspace"]["id"] == str(owner.workspace_id)
    assert manifest["migration_heads"] and manifest["format_version"] == FORMAT_VERSION == 2
    assert set(manifest["tombstone_tables"]) == TOMBSTONE_TABLES
    assert "core.embeddings" in manifest["excluded"] and "personal.*" in manifest["excluded"]
    rows = {t["name"]: t["rows"] for t in manifest["tables"]}
    for name in (
        "core.sources",
        "core.source_versions",
        "core.document_blocks",
        "core.source_spans",
        "core.claims",
        "core.claim_evidence",
        "core.interview_answers",
        "core.model_calls",
        "core.job_postings",
        "core.job_posting_versions",
        "core.applications",
        "core.document_revisions",
        "core.document_claims",
        "core.document_suggestions",
        "core.application_submissions",
        "core.application_stages",
        "core.document_settings",
        "core.document_suggestion_requests",
        "core.interview_question_requests",
        "core.runs",
        "core.study_requirement_claims",
        "core.study_items",
        "core.study_sessions",
        "core.study_attempts",
        "core.study_mistakes",
        "core.study_reviews",
        "core.study_claim_candidates",
    ):
        assert rows[name] > 0, name
    assert rows["core.workspaces"] == 1 and rows["core.actors"] == 1
    # 지원 문서 2 + 공용 문서 1 + 복사본 1
    assert rows["core.document_revisions"] == 4
    assert {f"blobs/{bl['key']}" for bl in manifest["blobs"]} == {
        n for n in files if n.startswith("blobs/")
    }
    assert a["screenshot"] in {bl["key"] for bl in manifest["blobs"]}

    # §17.1: 다른 공간의 자료는 어떤 형태로도 들어 있지 않다
    blob = b"".join(files.values())
    for foreign in (other.workspace_id, other.subject_id, b["claim"], b["posting"], b["source"]):
        assert str(foreign).encode() not in blob
    assert b"tag-bravo" not in blob
    for name, data in files.items():
        if name.startswith("tables/") and name != "tables/core.actors.jsonl":
            for line in data.decode().splitlines():
                row = json.loads(line)
                assert str(owner.workspace_id) in (row.get("workspace_id"), row.get("id"))
    # 키·토큰은 없다
    assert b"cbp_" not in blob and b"token_hash" not in blob

    async with db.workspace_session(owner.workspace_id) as s:
        before_sub = await s.get(ApplicationSubmission, a["submission"].id)
        before_sub = (before_sub.documents, before_sub.content_hash, before_sub.posting)

    with migrated_database() as target_urls:
        target = Database(target_urls.app, poolclass=NullPool)
        target_blobs = LocalBlobStore(tmp_path / "restored-blobs")
        try:
            # 시험 삼아: 모두 확인하고 아무것도 쓰지 않는다
            dry = await restore_workspace(target, target_blobs, out, dry_run=True)
            assert dry.dry_run and dry.rows_total == report.rows_total
            async with target.workspace_session(owner.workspace_id) as s:
                assert await s.scalar(select(func.count()).select_from(Workspace)) == 0
            assert not (tmp_path / "restored-blobs").exists()

            restored = await restore_workspace(target, target_blobs, out)
            assert restored.workspace_id == owner.workspace_id
            assert restored.owners == [owner.subject_id]
            assert {t.name: (t.rows, t.sha256) for t in restored.tables} == {
                t["name"]: (t["rows"], t["sha256"]) for t in manifest["tables"]
            }
            assert restored.blobs == len(manifest["blobs"])
            await assert_relations(target, target_blobs, owner, a, before_sub)

            # 같은 대상에 다시 복원하지 않는다
            with pytest.raises(ArchiveError, match="target_not_empty"):
                await restore_workspace(target, target_blobs, out)

            # 복원한 공간을 다시 내보내면 같은 내용이다 (내보내기 기록 표만 늘어난다)
            again = await export_workspace(target, target_blobs, owner, tmp_path / "again.tar.gz")
            again_tables = {t["name"]: t for t in again.manifest["tables"]}
            for t in manifest["tables"]:
                if t["name"] != "core.export_runs":
                    assert again_tables[t["name"]]["sha256"] == t["sha256"], t["name"]
            info = await maintenance_info(target, owner.workspace_id, target_blobs)
            assert info.last_restore is not None and info.last_restore.kind == "restore"
            assert info.last_export.archive_sha256 == again.sha256
            assert info.blobs_referenced == len(manifest["blobs"]) == info.blob_files
            # 작업 큐·벡터는 따라오지 않는다 (재색인 job은 CLI가 복원 뒤에 넣는다)
            assert info.excluded == {
                "core.jobs": 0,
                "core.embeddings": 0,
                "core.oauth_identities": 0,
                "core.oauth_tokens": 0,
                "core.maintenance_results": 0,
                "core.restore_jobs": 0,
            }
        finally:
            await target.dispose()


async def assert_relations(target, blobs, owner, a, before_sub):
    """§17.13: 복원된 공간의 원본·관계·문서가 연결되어 있다."""
    exp = await brain.get_experience(target, owner, a["experience"])
    claim = next(c for c in exp.claims if c.id == a["claim"])
    assert claim.status in ("self_confirmed", "evidence_supported")
    span = await brain.get_span(target, owner, a["span"])
    assert span.source_id == a["source"] and span.quote == span.block_text[0:9]
    async with target.workspace_session(owner.workspace_id) as s:
        version = (
            await s.execute(
                text("SELECT sha256, blob_key FROM core.source_versions WHERE source_id = :s"),
                {"s": a["source"]},
            )
        ).one()
        posting = await s.get(JobPosting, a["posting"])
        assert posting.status == "saved" and posting.confirmed_version_id is not None
        shot = await s.scalar(
            select(JobPostingVersion.screenshot_key).where(
                JobPostingVersion.id == posting.confirmed_version_id
            )
        )
    data = await blobs.get(owner.workspace_id, version.blob_key)
    assert hashlib.sha256(data).hexdigest() == version.sha256 and b"tag-alpha" in data
    assert await blobs.get(owner.workspace_id, shot) == SCREENSHOT

    doc = await apps_uc.get_document(target, owner, a["application"], a["document"])
    assert doc.current_revision == 2 and doc.blocks[0].text.startswith("주문 API 지연을 800ms")
    assert str(a["claim"]) in doc.blocks[0].claim_ids
    [sug] = await apps_uc.list_suggestions(target, owner, a["application"], a["document"])
    assert sug.status == "applied"
    # 복사본은 출처(공용 문서·revision)를 그대로 가리킨다
    copy = await apps_uc.get_document(target, owner, a["application"], a["copy"])
    assert copy.source["document_id"] == str(a["shared"]) and copy.source["revision_no"] == 1
    [stage] = await stages_uc.list_stages(target, owner, a["application"])
    assert (stage.outcome, stage.submission_id) == ("passed", a["submission"].id)
    assert (await docs_uc.get_settings(target, owner)).cliches == ["합성상투어"]
    # §17.12: 제출본은 그대로이고 여전히 고칠 수 없다
    frozen = await apps_uc.get_submission(target, owner, a["application"])
    assert (frozen.documents, frozen.content_hash, frozen.posting) == before_sub
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with target.workspace_session(owner.workspace_id) as s:
            await s.execute(update(ApplicationSubmission).values(note="고침"))

    item = await study_uc.get_item(target, owner, a["item"])
    assert item.total_minutes == 45 and item.attempts[0].result == "failed"
    assert item.claim_candidates and item.requirement is not None
    hits = await brain.list_experiences(target, owner)
    assert {h.id for h in hits} == {a["experience"]}


async def test_tampered_or_incompatible_archive_is_rejected(db, env, tmp_path):
    owner = env["owner"]
    out = tmp_path / "a.tar.gz"
    await export_workspace(db, env["blobs"], owner, out)

    def edit_claims(files):
        name = "tables/core.claims.jsonl"
        files[name] = files[name].replace(b"tag-alpha", b"tag-zulu")

    def bump_version(files):
        m = json.loads(files["manifest.json"])
        m["format_version"] = 99
        files["manifest.json"] = json.dumps(m).encode()

    def forge_counts(files):
        # 해시까지 맞춰 고쳐도 다른 공간으로 행을 옮길 수는 없다 (RLS가 막는다)
        m = json.loads(files["manifest.json"])
        name = "tables/core.claims.jsonl"
        lines = files[name].decode().splitlines()
        row = json.loads(lines[0])
        row["workspace_id"] = str(env["other"].workspace_id)
        lines.append(json.dumps({**row, "id": str(uuid4())}))
        files[name] = ("\n".join(lines) + "\n").encode()
        for t in m["tables"]:
            if t["name"] == "core.claims":
                t["rows"] += 1
                t["sha256"] = hashlib.sha256(files[name]).hexdigest()
        files["manifest.json"] = json.dumps(m).encode()

    def drop_blob(files):
        files.pop(next(n for n in files if n.startswith("blobs/")))

    def extra_member(files):
        files["../escape.txt"] = b"x"

    cases = [
        (edit_claims, "hash_mismatch"),
        (bump_version, "version_mismatch"),
        (drop_blob, "missing_member"),
        (extra_member, "unexpected_member"),
    ]
    with migrated_database() as target_urls:
        target = Database(target_urls.app, poolclass=NullPool)
        target_blobs = LocalBlobStore(tmp_path / "t-blobs")
        try:
            for i, (change, code) in enumerate(cases):
                bad = rewrite(out, tmp_path / f"bad{i}.tar.gz", change)
                with pytest.raises(ArchiveError, match=code):
                    await restore_workspace(target, target_blobs, bad)
            with pytest.raises(ArchiveError, match="version_mismatch"):
                await restore_workspace(target, target_blobs, out, expected_heads=["c0001"])
            (tmp_path / "junk.tar.gz").write_bytes(b"not a tar")
            with pytest.raises(ArchiveError, match="not_an_archive"):
                await restore_workspace(target, target_blobs, tmp_path / "junk.tar.gz")
            forged = rewrite(out, tmp_path / "forged.tar.gz", forge_counts)
            with pytest.raises(Exception, match="row-level security"):
                await restore_workspace(target, target_blobs, forged)
            # 실패한 복원은 아무것도 남기지 않는다
            async with target.workspace_session(owner.workspace_id) as s:
                assert await s.scalar(select(func.count()).select_from(Workspace)) == 0
            assert not (tmp_path / "t-blobs").exists()
        finally:
            await target.dispose()


# ---------------------------------------------------------------- HTTP·유지보수


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


async def test_maintenance_http(db, env):
    owner = env["owner"]
    editor = ActorContext(owner.subject_id, owner.workspace_id, scopes_for_role(Role.EDITOR))
    async with db.workspace_session(owner.workspace_id) as s:
        s.add(
            JobPosting(
                workspace_id=owner.workspace_id,
                canonical_url="https://jobs.example.test/old-draft",
                source_url="https://jobs.example.test/old-draft",
                status="draft",
                created_by=owner.subject_id,
                created_at=datetime.now(UTC) - timedelta(days=8),
            )
        )
    app = FastAPI()
    app.include_router(
        build_router(
            CoreDeps(identity=Tokens({"owner": owner, "editor": editor}), db=db, blobs=env["blobs"])
        )
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as client:

        def h(token):
            return {"Authorization": f"Bearer {token}"}

        for path in ("/core/maintenance", "/core/maintenance/export"):
            assert (await client.get(path, headers=h("editor"))).status_code == 403
            assert (await client.get(path)).status_code == 401
        r = await client.post("/core/maintenance/purge-drafts", headers=h("editor"))
        assert r.status_code == 403

        info = (await client.get("/core/maintenance", headers=h("owner"))).json()
        assert info["drafts_purgeable"] == 1 and info["last_export"] is None
        assert info["tables"]["core.claims"] >= 2 and info["blobs"]["referenced"] == 2
        assert info["blobs"]["files"] == 2 and info["blobs"]["bytes"] > 0

        r = await client.get("/core/maintenance/export", headers=h("owner"))
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/gzip"
        assert "attachment" in r.headers["content-disposition"]
        exported_sha = r.headers["x-archive-sha256"]
        assert hashlib.sha256(r.content).hexdigest() == exported_sha
        with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
            manifest = json.loads(tar.extractfile("manifest.json").read())
        assert manifest["workspace"]["id"] == str(owner.workspace_id)

        r = await client.post("/core/maintenance/purge-drafts", headers=h("owner"))
        assert r.json() == {"deleted": 1}
        info = (await client.get("/core/maintenance", headers=h("owner"))).json()
        assert info["drafts_purgeable"] == 0
        assert info["last_export"]["via"] == "http"
        assert info["last_export"]["archive_sha256"] == exported_sha

        # 정리한 초안은 삭제 기록에 남고, 삭제 기록만 따로 받을 수 있다
        assert (
            await client.get("/core/maintenance/deletions", headers=h("editor"))
        ).status_code == 403
        r = await client.get("/core/maintenance/deletions", headers=h("owner"))
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
        head, *rows = (json.loads(x) for x in r.text.splitlines())
        assert head["format"] == "career-brain-deletions"
        assert [x["target_table"] for x in rows] == ["core.job_postings"]
        r = await client.get(
            "/core/maintenance/export", params={"recipient": "age1bad"}, headers=h("owner")
        )
        assert r.status_code == 400 and r.json()["detail"] == "invalid_recipient"


# ---------------------------------------------------------------- 새 ID·암호화·대기 작업·삭제 기록


async def test_restore_with_new_ids_into_the_same_install(db, env, tmp_path):
    """같은 설치에 사본을 만든다: 모든 ID가 새로 생기고 FK·참조가 같이 바뀐다. 원래 공간은 그대로."""
    from career_core.db.models import Approval

    owner, a = env["owner"], env["a"]
    # 지금 대상 버전 그대로인 열린 승인. 대상 버전 해시에 문서 ID가 들어 있으므로 새 ID로 가져오면 맞지 않는다.
    async with db.workspace_session(owner.workspace_id) as s:
        version = await s.scalar(
            text("SELECT core.application_target_version(:a)"), {"a": a["application"]}
        )
        s.add(
            Approval(
                workspace_id=owner.workspace_id,
                action="applications.submit",
                target_kind="application",
                target_id=a["application"],
                target_version=version,
                payload_hash="0" * 64,
                requested_by=owner.subject_id,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
    out = tmp_path / "a.tar.gz"
    report = await export_workspace(db, env["blobs"], owner, out)
    copy = await restore_workspace(db, env["blobs"], out, new_ids=True)
    # 넣는 동안은 trigger가 꺼져 있고(묶음 그대로 확인), 확인 뒤 c0019 함수로 다시 계산해 지금 닫는다
    async with db.workspace_session(copy.workspace_id) as s:
        rows = (await s.execute(select(Approval.status, Approval.invalid_reason))).all()
    assert [tuple(r) for r in rows] == [("invalidated", "target_changed")]
    assert copy.approvals_invalidated == 1
    async with db.workspace_session(owner.workspace_id) as s:
        assert (await s.execute(select(Approval.status))).scalars().all() == ["pending"]
    assert copy.workspace_id != owner.workspace_id
    assert copy.source_workspace_id == owner.workspace_id
    assert copy.rows_total == report.rows_total and copy.blobs == report.blobs
    [new_owner] = copy.owners
    assert new_owner != owner.subject_id
    actor = owner_of(copy.workspace_id, new_owner)
    titles = [e.title for e in await brain.list_experiences(db, actor)]
    assert titles == [e.title for e in await brain.list_experiences(db, owner)]
    assert a["experience"] not in {e.id for e in await brain.list_experiences(db, actor)}
    # 원래 공간은 바뀌지 않았다
    assert await brain.get_experience(db, owner, a["experience"])

    # 사본을 다시 내보내면 옛 ID가 하나도 없다 (FK 없는 참조까지 바뀌었다)
    again = tmp_path / "copy.tar.gz"
    await export_workspace(db, env["blobs"], actor, again)
    data = b"".join(v for k, v in members(again).items() if k.startswith("tables/"))
    for old in (owner.workspace_id, owner.subject_id, a["experience"], a["claim"], a["posting"]):
        assert str(old).encode() not in data
    assert str(copy.workspace_id).encode() in data
    # 같은 묶음을 또 가져와도 충돌하지 않는다 (매번 새 ID)
    second = await restore_workspace(db, env["blobs"], out, new_ids=True, dry_run=True)
    assert second.workspace_id not in (owner.workspace_id, copy.workspace_id)


async def test_encrypted_archive_roundtrip(db, env, tmp_path, monkeypatch):
    pyrage = pytest.importorskip("pyrage")
    from career_core.adapters.age_crypt import is_encrypted

    monkeypatch.setenv("CAREER_AGE_BACKEND", "pyrage")
    key, wrong = pyrage.x25519.Identity.generate(), pyrage.x25519.Identity.generate()
    (tmp_path / "key.txt").write_text(f"# synthetic test key\n{key}\n")
    (tmp_path / "wrong.txt").write_text(f"{wrong}\n")
    out = tmp_path / "a.tar.gz.age"
    with pytest.raises(ExportError, match="invalid_recipient"):
        await export_workspace(db, env["blobs"], env["owner"], out, recipients=["age1nope"])
    report = await export_workspace(
        db, env["blobs"], env["owner"], out, recipients=[str(key.to_public())]
    )
    assert report.encrypted and is_encrypted(out)
    assert b"tag-alpha" not in out.read_bytes()
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".part")]  # 평문 임시 파일 없음
    with pytest.raises(ArchiveError, match="encrypted_archive"):
        await restore_workspace(db, env["blobs"], out, new_ids=True, dry_run=True)
    with pytest.raises(ArchiveError, match="decrypt_failed"):
        await restore_workspace(
            db, env["blobs"], out, new_ids=True, dry_run=True, identities=[tmp_path / "wrong.txt"]
        )
    checked = await restore_workspace(
        db, env["blobs"], out, new_ids=True, dry_run=True, identities=[tmp_path / "key.txt"]
    )
    assert checked.encrypted and checked.archive_sha256 == report.sha256
    assert checked.rows_total == report.rows_total


async def test_pending_work_is_requeued_and_later_deletions_are_replayed(db, tmp_path):
    from career_core.db.models import Deletion
    from career_core.usecases.export import export_deletions
    from career_core.usecases.maintenance import purge_drafts

    blobs = LocalBlobStore(tmp_path / "blobs")
    ws, me = await create_workspace(db, name="대기 작업 (합성)", owner_display_name="carol")
    owner = owner_of(ws, me)
    await upload_source(
        db,
        blobs,
        owner,
        filename="queued.txt",
        data=b"synthetic note waiting for the parser",
        declared_mime="text/plain",
        max_bytes=1 << 20,
    )
    draft = JobPosting(
        workspace_id=ws,
        canonical_url=f"https://jobs.example.test/draft-{uuid4().hex[:8]}",
        source_url="https://jobs.example.test/draft",
        status="draft",
        created_by=me,
        created_at=datetime.now(UTC) - timedelta(days=30),
    )
    async with db.workspace_session(ws) as s:
        s.add(draft)
    out = tmp_path / "before.tar.gz"
    report = await export_workspace(db, blobs, owner, out)
    assert report.pending_work == 1
    [work] = report.manifest["pending_work"]
    assert (work["kind"], work["target_table"]) == ("parse", "core.parse_runs")

    # 내보낸 뒤 지운 자료는 삭제 기록(trigger)에 남는다
    assert await purge_drafts(db, ws) == 1
    async with db.workspace_session(ws) as s:
        [tomb] = (await s.execute(select(Deletion))).scalars().all()
    assert (tomb.target_table, tomb.target_id) == ("core.job_postings", draft.id)
    log = tmp_path / "deletions.jsonl"
    assert await export_deletions(db, owner, log) == 1
    head, row = (json.loads(x) for x in log.read_text().splitlines())
    assert head["workspace_id"] == str(ws) and row["target_id"] == str(draft.id)

    restored = await restore_workspace(db, blobs, out, new_ids=True, deletion_logs=[log])
    assert restored.requeued == {"parse": 1}
    assert (restored.deletions_applied, restored.deletions_skipped) == (1, 0)
    new_ws = restored.workspace_id
    async with db.workspace_session(new_ws) as s:
        assert await s.scalar(select(func.count()).select_from(JobPosting)) == 0
        [tomb] = (await s.execute(select(Deletion))).scalars().all()
        assert tomb.deleted_at < datetime.now(UTC) and tomb.target_table == "core.job_postings"
        jobs = (
            await s.execute(
                text(
                    "SELECT j.kind FROM core.jobs j JOIN core.parse_runs r ON r.id = j.target_id "
                    "WHERE j.workspace_id = :ws AND j.status = 'queued'"
                ),
                {"ws": str(new_ws)},
            )
        ).scalars()
        assert list(jobs) == ["parse"]

    # 다른 공간의 삭제 기록은 받지 않는다
    other_ws, other = await create_workspace(db, name="다른 (합성)", owner_display_name="dan")
    wrong = tmp_path / "other.jsonl"
    await export_deletions(db, owner_of(other_ws, other), wrong)
    with pytest.raises(ArchiveError, match="deletions_workspace_mismatch"):
        await restore_workspace(db, blobs, out, new_ids=True, deletion_logs=[wrong])


# ---------------------------------------------------------------- 기존 공간에 합치기 (c0018)


async def table_counts(db, ws):
    async with db.workspace_session(ws) as s:
        tables = await read_catalog(await s.connection())
        plan = build_plan(tables)
        out = {}
        for t in plan.tables:
            if t.scope == "workspace":
                out[t.name] = await s.scalar(
                    text(f"SELECT count(*) FROM {t.sql} WHERE workspace_id = :ws"), {"ws": ws}
                )
    return out


def added(report):
    return {t.name: t.added for t in report.tables if t.added}


async def _scalar(db, ws, sql, **params):
    async with db.workspace_session(ws) as s:
        return await s.scalar(text(sql), params)


async def test_merge_old_backup_into_the_same_workspace(db, env, tmp_path):
    """지난 묶음을 같은 공간에 합치면 그 뒤 지운 행만 돌아온다 (나머지는 같은 ID로 겹친다)."""
    from career_core.usecases.export import export_deletions, merge_workspace

    owner, a, blobs = env["owner"], env["a"], env["blobs"]
    ws = owner.workspace_id
    out = tmp_path / "a.tar.gz"
    await export_workspace(db, blobs, owner, out)
    # 내보낸 뒤: 전형 단계를 지우고, 요건↔주장 연결을 끊고, 새 경험을 더한다
    [stage] = await stages_uc.list_stages(db, owner, a["application"])
    await stages_uc.delete_stage(db, owner, a["application"], stage.id)
    req = await _scalar(db, ws, "SELECT requirement_id FROM core.study_requirement_claims")
    await study_uc.unlink_claim(db, owner, req, a["claim"])
    later = await brain.create_experience(db, owner, title="내보낸 뒤 경험 (합성)")
    before = await table_counts(db, ws)

    dry = await merge_workspace(db, blobs, owner, out, dry_run=True)
    assert dry.dry_run and dry.workspace_id == dry.source_workspace_id == ws
    assert added(dry) == {"core.application_stages": 1, "core.study_requirement_claims": 1}
    claims = next(t for t in dry.tables if t.name == "core.claims")
    assert claims.added == 0 and claims.skipped_by == {"id": claims.archive_rows}
    assert dry.actors_mapped == 0  # 같은 사람(같은 ID, 이 공간 구성원)
    assert {"core.workspaces", "core.actors", "core.memberships"} <= set(dry.not_merged)
    assert await table_counts(db, ws) == before  # dry-run은 아무것도 쓰지 않는다

    merged = await merge_workspace(db, blobs, owner, out)
    assert added(merged) == added(dry) and merged.rows_added == 2
    [back] = await stages_uc.list_stages(db, owner, a["application"])
    assert back.id != stage.id and (back.kind, back.outcome) == (stage.kind, stage.outcome)
    assert back.submission_id == stage.submission_id  # 기존 제출본을 가리킨다
    item = await study_uc.get_item(db, owner, a["item"])
    assert item.requirement is not None
    assert later in {e.id for e in await brain.list_experiences(db, owner)}
    info = await maintenance_info(db, ws, blobs)
    assert info.last_restore.kind == "merge" and info.last_restore.rows_total == 2

    # 다시 합쳐도 늘지 않는다: 연결은 UNIQUE(요건, 주장)로, UNIQUE가 없는 단계는 같은 내용으로 겹친다
    again = await merge_workspace(db, blobs, owner, out, dry_run=True)
    assert again.rows_added == 0
    stages = next(t for t in again.tables if t.name == "core.application_stages")
    assert stages.skipped_by == {"content": 1}

    # 삭제 기록을 같이 주면 합친 행에만 다시 적용된다 (그 사이 지운 단계·연결은 다시 지워진다)
    await stages_uc.delete_stage(db, owner, a["application"], back.id)
    await study_uc.unlink_claim(db, owner, req, a["claim"])
    log = tmp_path / "deletions.jsonl"
    await export_deletions(db, owner, log)
    replay = await merge_workspace(db, blobs, owner, out, deletion_logs=[log])
    assert replay.rows_added == 2 and replay.deletions_applied == 2
    assert await stages_uc.list_stages(db, owner, a["application"]) == []
    assert replay.skipped_deletions == []


async def test_merge_into_another_workspace_with_duplicates(db, env, tmp_path):
    """다른 공간에 합친다: 같은 파일(sha256)·같은 공고 주소는 기존 행으로, 나머지는 새 ID로. FK가 이어진다."""
    from career_core.usecases.export import merge_workspace

    owner, other, a, blobs = env["owner"], env["other"], env["a"], env["blobs"]
    ws_b = other.workspace_id
    # B에도 A와 같은 원본 파일과 같은 공고 주소가 있다
    dup = await upload_source(
        db,
        blobs,
        other,
        filename="same-file.txt",
        data=f"{RESUME}\n\ntag-alpha".encode(),
        declared_mime="text/plain",
        max_bytes=1 << 20,
    )
    deps = ParsingDeps(
        db=db, blobs=blobs, egress=ProviderEgressPolicy(), local_parsers=default_local_parsers()
    )
    await run_until_idle(db, parsing_handlers(deps))
    url = "https://jobs.example.test/tag-alpha/1"
    async with db.workspace_session(ws_b) as s:
        s.add(
            JobPosting(
                workspace_id=ws_b,
                canonical_url=url,
                source_url=url,
                status="draft",
                created_by=other.subject_id,
            )
        )
    out = tmp_path / "a.tar.gz"
    await export_workspace(db, blobs, owner, out)
    a_claims = await _rows(db, owner.workspace_id, "core.claims")
    b_before = await table_counts(db, ws_b)
    b_exps = {e.id: e.title for e in await brain.list_experiences(db, other)}

    report = await merge_workspace(db, blobs, other, out)
    t = {x.name: x for x in report.tables}
    assert report.workspace_id == ws_b and report.source_workspace_id == owner.workspace_id
    assert t["core.sources"].skipped_by == {"core.source_versions.sha256": 1}
    assert t["core.source_versions"].added == 0 and t["core.parse_runs"].added == 0
    assert t["core.document_blocks"].skipped_by == {
        "parse_run_id+ordinal": t["core.document_blocks"].archive_rows
    }
    assert t["core.source_spans"].added == 1  # B의 같은 블록 위에 새 범위
    [(key, n)] = t["core.job_postings"].skipped_by.items()
    assert "canonical_url" in key and n == 1 and t["core.job_postings"].added == 0
    assert t["core.job_posting_versions"].added == 1
    assert t["core.document_settings"].added == 0  # 공간당 1행: B의 설정 그대로
    for name in ("core.experiences", "core.applications", "core.application_submissions"):
        assert t[name].added == t[name].archive_rows > 0, name
    assert report.actors_mapped == 1 and report.blobs == 1  # 스크린샷만 (원본 파일은 B에 있다)
    after = await table_counts(db, ws_b)
    for name, n in after.items():
        # export_runs: 합치기 기록 1행 (묶음의 백업 기록은 넣지 않는다)
        assert n == b_before[name] + t[name].added + (name == "core.export_runs"), name

    # 기존 행은 그대로, 합친 행은 B의 기존 원본·공고를 가리킨다
    exps = {e.id: e.title for e in await brain.list_experiences(db, other)}
    assert b_exps.items() <= exps.items() and len(exps) == len(b_exps) + 1
    [merged_exp] = [i for i in exps if i not in b_exps]
    assert exps[merged_exp] == "주문 서비스 개선 tag-alpha"
    view = await brain.get_experience(db, other, merged_exp)
    claim = next(c for c in view.claims if c.statement.startswith("주문 API p95"))
    span_id = await _scalar(
        db, ws_b, "SELECT source_span_id FROM core.claim_evidence WHERE claim_id = :c", c=claim.id
    )
    span = await brain.get_span(db, other, span_id)
    assert span.source_id == dup.source_id
    async with db.workspace_session(ws_b) as s:
        posting = await s.scalar(select(JobPosting).where(JobPosting.canonical_url == url))
        assert posting.status == "draft" and posting.confirmed_version_id is None  # 고치지 않았다
        app_posting = await s.scalar(
            text(
                "SELECT a.posting_id FROM core.applications a JOIN core.job_posting_versions v "
                "ON v.id = a.posting_version_id AND v.posting_id = a.posting_id "
                "WHERE a.posting_id = :p"
            ),
            {"p": posting.id},
        )
        assert app_posting == posting.id
        shot = await s.scalar(
            select(JobPostingVersion.screenshot_key).where(
                JobPostingVersion.posting_id == posting.id
            )
        )
    assert await blobs.get(ws_b, shot) == SCREENSHOT
    assert await _rows(db, owner.workspace_id, "core.claims") == a_claims  # A는 그대로

    # 합친 B를 내보내면 A의 ID·사람이 하나도 없고, 빈 DB에 그대로 복원된다 (FK·해시가 맞다)
    out_b = tmp_path / "b.tar.gz"
    exported = await export_workspace(db, blobs, other, out_b)
    data = b"".join(v for k, v in members(out_b).items() if k.startswith("tables/"))
    for old in (owner.workspace_id, owner.subject_id, a["experience"], a["claim"], a["posting"]):
        assert str(old).encode() not in data
    with migrated_database() as target_urls:
        target = Database(target_urls.app, poolclass=NullPool)
        try:
            checked = await restore_workspace(
                target, LocalBlobStore(tmp_path / "t"), out_b, dry_run=True
            )
            assert checked.rows_total == exported.rows_total
        finally:
            await target.dispose()


async def _rows(db, ws, table):
    async with db.workspace_session(ws) as s:
        return sorted((await s.execute(text(f"SELECT to_jsonb(t)::text FROM {table} t"))).scalars())


async def test_merge_policies_permissions_and_rollback(db, env, tmp_path, monkeypatch):
    from career_core.usecases import export as export_mod
    from career_core.usecases.export import merge_workspace

    owner, other, blobs = env["owner"], env["other"], env["blobs"]
    ws_b = other.workspace_id
    out = tmp_path / "a.tar.gz"
    await export_workspace(db, blobs, owner, out)
    before = await table_counts(db, ws_b)
    editor = ActorContext(other.subject_id, ws_b, scopes_for_role(Role.EDITOR))
    with pytest.raises(ExportError, match="forbidden"):
        await merge_workspace(db, blobs, editor, out)
    with pytest.raises(ExportError, match="invalid_policy"):
        await merge_workspace(db, blobs, other, out, on_duplicate="overwrite")
    # fail: 겹치는 행이 있으면 아무것도 쓰지 않는다 (같은 공간에 합치면 모두 겹친다)
    with pytest.raises(ArchiveError, match="duplicates_found"):
        await merge_workspace(db, blobs, owner, out, on_duplicate="fail")
    # B와 A가 겹치는 행은 document_settings(공간당 1행)뿐이다
    with pytest.raises(ArchiveError, match="duplicates_found: core.document_settings 1$"):
        await merge_workspace(db, blobs, other, out, on_duplicate="fail")

    # 한 행이 CHECK를 어기면 (해시까지 맞춘 묶음) 전체가 되돌려진다
    def bad_claim(files):
        m = json.loads(files["manifest.json"])
        name = "tables/core.claims.jsonl"
        lines = files[name].decode().splitlines()
        row = json.loads(lines[-1])
        row["kind"] = "bogus"
        lines[-1] = json.dumps(row, ensure_ascii=False)
        files[name] = ("\n".join(lines) + "\n").encode()
        for e in m["tables"]:
            if e["name"] == "core.claims":
                e["sha256"] = hashlib.sha256(files[name]).hexdigest()
        files["manifest.json"] = json.dumps(m).encode()

    bad = rewrite(out, tmp_path / "bad.tar.gz", bad_claim)
    with pytest.raises(ArchiveError, match="merge_conflict.*ck_claims_kind"):
        await merge_workspace(db, blobs, other, bad)
    assert await table_counts(db, ws_b) == before

    # 넣는 동안 기존 행이 바뀌면(추가만 해야 하는데) 전후 해시가 맞지 않아 되돌린다
    real = export_mod._insert_all

    async def sneaky(conn, plan, lines_of, inserted=None):
        await real(conn, plan, lines_of, inserted)
        await conn.execute(text("UPDATE core.experiences SET title = title || ' (고침)'"))

    monkeypatch.setattr(export_mod, "_insert_all", sneaky)
    with pytest.raises(ArchiveError, match="verify_failed: core.experiences"):
        await merge_workspace(db, blobs, other, out)
    monkeypatch.undo()
    assert await table_counts(db, ws_b) == before
    assert not any("(고침)" in e.title for e in await brain.list_experiences(db, other))
    blob_dir = tmp_path / "blobs"
    files_before = sorted(str(p) for p in blob_dir.rglob("*") if p.is_file())

    # dry-run은 원본도 쓰지 않는다
    dry = await merge_workspace(db, blobs, other, out, dry_run=True)
    assert dry.rows_added > 0 and dry.blobs == 2  # 원본 파일 + 스크린샷
    assert sorted(str(p) for p in blob_dir.rglob("*") if p.is_file()) == files_before
    assert await table_counts(db, ws_b) == before


# ---------------------------------------------------------------- 옛 형식 묶음


async def test_older_format_archives_are_upgraded(db, env, tmp_path, monkeypatch):
    from career_core.usecases.export import (
        UPGRADES,
        FormatUpgrade,
        merge_workspace,
        readable_versions,
    )

    owner, blobs = env["owner"], env["blobs"]
    out = tmp_path / "a.tar.gz"
    report = await export_workspace(db, blobs, owner, out)
    assert readable_versions() == [1, 2] and set(UPGRADES) == {1}

    # v1 (c0018 전 형식): tombstone_tables가 없다. 표 내용은 같다 → 1 → 2 단계는 표를 바꾸지 않는다
    def as_v1(files):
        m = json.loads(files["manifest.json"])
        m["format_version"] = 1
        del m["tombstone_tables"]
        files["manifest.json"] = json.dumps(m).encode()

    v1 = rewrite(out, tmp_path / "v1.tar.gz", as_v1)
    restored = await restore_workspace(db, blobs, v1, new_ids=True, dry_run=True)
    assert restored.format_upgraded_from == 1 and restored.rows_total == report.rows_total
    assert "archive format 1 was upgraded to 2" in restored.notes

    # 합성 v0: 공간 정보가 맨 위 칸이고 주장 칸 이름이 달랐다고 가정한 형식. 단계를 등록해야 읽는다.
    def as_v0(files):
        m = json.loads(files["manifest.json"])
        ws = m.pop("workspace")
        m.update(format_version=0, workspace_id=ws["id"], workspace_name=ws["name"])
        del m["tombstone_tables"]
        name = "tables/core.claims.jsonl"
        lines = []
        for line in files[name].decode().splitlines():
            row = json.loads(line)
            row["text"] = row.pop("statement")
            lines.append(json.dumps(row, ensure_ascii=False))
        files[name] = ("\n".join(lines) + "\n").encode()
        for e in m["tables"]:
            if e["name"] == "core.claims":
                e["sha256"] = hashlib.sha256(files[name]).hexdigest()
        files["manifest.json"] = json.dumps(m).encode()

    v0 = rewrite(out, tmp_path / "v0.tar.gz", as_v0)
    with pytest.raises(ArchiveError, match="version_mismatch: format 0, this install reads 1, 2"):
        await restore_workspace(db, blobs, v0, new_ids=True, dry_run=True)

    def upgrade_v0(manifest, arc):
        manifest["workspace"] = {
            "id": manifest.pop("workspace_id"),
            "name": manifest.pop("workspace_name"),
        }

        def rename(row):
            row["statement"] = row.pop("text")
            return row

        arc.rewrite("core.claims", rename)

    monkeypatch.setitem(UPGRADES, 0, FormatUpgrade(0, "합성 v0 → v1", upgrade_v0))
    assert readable_versions() == [0, 1, 2]
    restored = await restore_workspace(db, blobs, v0, new_ids=True, dry_run=True)
    assert restored.format_upgraded_from == 0 and restored.rows_total == report.rows_total
    # 합치기도 같은 변환을 거친다 (같은 공간에 합치면 모두 겹친다 = 내용이 그대로 읽혔다)
    merged = await merge_workspace(db, blobs, owner, v0, dry_run=True)
    assert merged.format_upgraded_from == 0 and merged.rows_added == 0
    # 단계가 표 파일을 잃으면 거부
    monkeypatch.setitem(
        UPGRADES, 0, FormatUpgrade(0, "깨진 단계", lambda m, arc: arc.path("core.claims").unlink())
    )
    with pytest.raises(ArchiveError, match="upgrade_failed"):
        await restore_workspace(db, blobs, v0, new_ids=True, dry_run=True)


# ---------------------------------------------------------------- 삭제 기록 (c0018)


async def test_deletion_log_covers_user_deleted_tables(db, owner_engine):
    """c0018: 사용자가 직접 지우는 표의 삭제를 남기고, FK CASCADE로 같이 지워진 행은 남기지 않는다."""
    from career_core.db.models import Deletion

    with owner_engine.connect() as conn:
        triggered = set(
            conn.execute(
                text(
                    "SELECT tgrelid::regclass::text FROM pg_trigger "
                    "WHERE tgname = 'record_deletion' AND NOT tgisinternal"
                )
            ).scalars()
        )
        check = conn.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_deletions_target_table'"
            )
        )
        defs = dict(
            conn.execute(
                text(
                    "SELECT tgrelid::regclass::text, pg_get_triggerdef(oid) FROM pg_trigger "
                    "WHERE tgname = 'record_deletion' AND NOT tgisinternal"
                )
            ).all()
        )
        cascades = conn.execute(
            text(
                "SELECT conrelid::regclass::text, confrelid::regclass::text, "
                "ARRAY(SELECT a.attname FROM unnest(conkey) k JOIN pg_attribute a "
                "ON a.attrelid = conrelid AND a.attnum = k) FROM pg_constraint "
                "WHERE contype = 'f' AND confdeltype = 'c' AND confrelid <> 'core.workspaces'::regclass"
            )
        ).all()
    assert triggered == set(TOMBSTONE_TABLES)
    assert all(f"'{t}'" in check for t in TOMBSTONE_TABLES)
    # trigger 인자(칸, CASCADE 부모)가 DB의 ON DELETE CASCADE FK와 같다 (새 FK가 생기면 c0018 표도 고친다)
    for table, definition in defs.items():
        args = re.findall(r"'([^']*)'", definition.split("record_deletion(", 1)[1])
        want = {
            (next(c for c in cols if c != "workspace_id"), parent)
            for child, parent, cols in cascades
            if child == table
        }
        assert set(zip(args[::2], args[1::2], strict=True)) == want, table

    ws, me = await create_workspace(db, name="삭제 기록 (합성)", owner_display_name="erin")
    owner = owner_of(ws, me)
    exp = await brain.create_experience(db, owner, title="지울 경험 (합성)")
    kept = await brain.add_claim(
        db, owner, exp, statement="같이 지워질 주장 (합성)", kind="action", contribution="personal"
    )
    first = await brain.add_claim(
        db, owner, exp, statement="먼저 지울 주장 (합성)", kind="skill", contribution="personal"
    )
    async with db.workspace_session(ws) as s:
        await s.execute(text("DELETE FROM core.claims WHERE id = :c"), {"c": first})
    async with db.workspace_session(ws) as s:
        await s.execute(text("DELETE FROM core.experiences WHERE id = :e"), {"e": exp})
        assert await s.scalar(select(func.count()).select_from(text("core.claims"))) == 0
    async with db.workspace_session(ws) as s:
        rows = (await s.execute(select(Deletion.target_table, Deletion.target_id))).all()
    # 직접 지운 주장·경험만. 경험과 같이(CASCADE) 지워진 주장은 남지 않는다.
    assert sorted(rows) == sorted([("core.claims", first), ("core.experiences", exp)])
    assert kept not in {r.target_id for r in rows}


async def test_skipped_deletions_report_reasons(db, env, tmp_path):
    """지울 수 없는 대상(RESTRICT FK가 가리킴)은 건너뛰고, 그 이유와 가리키는 표를 보고한다."""
    from career_core.usecases.export import merge_workspace

    owner, a, blobs = env["owner"], env["a"], env["blobs"]
    out = tmp_path / "a.tar.gz"
    await export_workspace(db, blobs, owner, out)
    log = tmp_path / "synthetic-deletions.jsonl"
    at = datetime.now(UTC).isoformat()
    lines = [
        {
            "format": "career-brain-deletions",
            "format_version": 1,
            "workspace_id": str(owner.workspace_id),
            "created_at": at,
        },
        # 문서 revision이 주장을 가리킨다 (document_claims → claims, NO ACTION)
        {"target_table": "core.claims", "target_id": str(a["claim"]), "deleted_at": at},
        # 묶음에 없는 대상은 조용히 넘어간다
        {"target_table": "core.experiences", "target_id": str(uuid4()), "deleted_at": at},
    ]
    log.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    report = await restore_workspace(
        db, blobs, out, new_ids=True, dry_run=True, deletion_logs=[log]
    )
    assert report.deletions_applied == 0 and report.deletions_skipped == 1
    [skip] = report.skipped_deletions
    assert (skip.target_table, skip.target_id, skip.reason) == (
        "core.claims",
        str(a["claim"]),
        "still_referenced",
    )
    assert skip.detail.startswith("core.document_claims (")

    # 합치기: 원래 있던 행에 대한 삭제 기록은 적용하지 않고 kept_existing으로 알린다
    merged = await merge_workspace(db, blobs, owner, out, dry_run=True, deletion_logs=[log])
    assert [(d.target_id, d.reason) for d in merged.skipped_deletions] == [
        (str(a["claim"]), "kept_existing")
    ]


# ---------------------------------------------------------------- 웹 복원 (c0018)


async def wait_job(client, job_id, headers):
    import asyncio

    for _ in range(400):
        r = await client.get(f"/core/maintenance/restore/{job_id}", headers=headers)
        assert r.status_code == 200, r.text
        if r.json()["status"] not in ("verifying", "applying"):
            return r.json()
        await asyncio.sleep(0.02)
    raise AssertionError("restore job did not finish")


async def test_web_restore_upload_verify_apply(db, env, tmp_path, monkeypatch):
    """올리기 → 확인(dry-run, 배경) → 합치기. 소유자만, 임시 폴더만 쓰고 지운다, 상한은 이 경로만."""
    import tempfile

    from career_core.interfaces.http import restore as restore_mod

    owner, other, blobs = env["owner"], env["other"], env["blobs"]
    uploads = tmp_path / "tmp"
    uploads.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(uploads))
    out = tmp_path / "a.tar.gz"
    await export_workspace(db, blobs, owner, out)
    data = out.read_bytes()
    editor = ActorContext(other.subject_id, other.workspace_id, scopes_for_role(Role.EDITOR))
    app = FastAPI()
    deps = CoreDeps(
        identity=Tokens({"b": other, "editor": editor, "a": owner}),
        db=db,
        blobs=blobs,
        max_restore_bytes=1 << 26,
    )
    app.include_router(build_router(deps))
    raw = {"Authorization": "Bearer b", "Content-Type": "application/octet-stream"}
    h = {"Authorization": "Bearer b"}
    before = await table_counts(db, other.workspace_id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as client:
        path = "/core/maintenance/restore"
        bad = {**raw, "Authorization": "Bearer editor"}
        assert (await client.post(path, content=data, headers=bad)).status_code == 403
        assert (await client.post(path, content=data)).status_code == 401
        r = await client.post(path, content=data, headers={**h, "Content-Type": "text/plain"})
        assert r.status_code == 415
        info = (await client.get("/core/maintenance", headers=h)).json()
        assert info["restore_available"] and info["max_restore_bytes"] == 1 << 26

        r = await client.post(path, content=data, headers={**raw, "X-Filename": "a.tar.gz"})
        assert r.status_code == 201, r.text
        job = r.json()
        assert job["status"] == "uploaded" and job["bytes"] == len(data) and not job["encrypted"]
        assert job["sha256"] == hashlib.sha256(data).hexdigest() and job["filename"] == "a.tar.gz"
        [job_dir] = list(uploads.iterdir())
        assert job_dir.name.startswith("career-restore-upload-")
        jid = job["id"]
        apply_body = {"on_duplicate": "skip", "archive_sha256": job["sha256"]}
        r = await client.post(f"{path}/{jid}/apply", json=apply_body, headers=h)
        assert r.status_code == 409 and r.json()["detail"] == "not_verified"

        r = await client.post(f"{path}/{jid}/verify", json={"on_duplicate": "skip"}, headers=h)
        assert r.status_code == 202 and r.json()["status"] == "verifying"
        job = await wait_job(client, jid, h)
        assert job["status"] == "verified", job
        rep = job["report"]
        assert rep["dry_run"] and rep["rows_added"] > 0 and rep["actors_mapped"] == 1
        assert rep["workspace_id"] == str(other.workspace_id)
        assert rep["source_workspace_id"] == str(owner.workspace_id)
        settings = next(t for t in rep["tables"] if t["name"] == "core.document_settings")
        assert settings["skipped"] == 1 and settings["added"] == 0
        assert await table_counts(db, other.workspace_id) == before  # 확인은 쓰지 않는다

        wrong = {**apply_body, "archive_sha256": "0" * 64}
        r = await client.post(f"{path}/{jid}/apply", json=wrong, headers=h)
        assert r.json()["detail"] == "archive_changed"
        r = await client.post(
            f"{path}/{jid}/apply", json={**apply_body, "on_duplicate": "fail"}, headers=h
        )
        assert r.json()["detail"] == "policy_changed"
        r = await client.post(f"{path}/{jid}/apply", json=apply_body, headers=h)
        assert r.status_code == 202
        job = await wait_job(client, jid, h)
        assert job["status"] == "done" and not job["report"]["dry_run"]
        assert job["report"]["rows_added"] == rep["rows_added"]
        assert list(uploads.iterdir()) == []  # 합친 뒤 올린 파일은 지운다
        titles = {e.title for e in await brain.list_experiences(db, other)}
        assert "주문 서비스 개선 tag-alpha" in titles
        info = (await client.get("/core/maintenance", headers=h)).json()
        assert info["last_restore"]["kind"] == "merge" and info["last_restore"]["via"] == "http"

        # 편집자는 볼 수 없고, 다른 공간의 소유자에게는 없는 작업이다
        assert (
            await client.get(f"{path}/{jid}", headers={"Authorization": "Bearer editor"})
        ).status_code == 403
        assert (
            await client.get(f"{path}/{jid}", headers={"Authorization": "Bearer a"})
        ).status_code == 404

        # 삭제 기록 올리기: 형식이 틀리면 400, 맞으면 줄 수. 같은 공간의 새 업로드는 옛 작업을 바꾼다.
        r = await client.post(path, content=data, headers=raw)
        jid2 = r.json()["id"]
        assert (await client.get(f"{path}/{jid}", headers=h)).status_code == 404
        r = await client.post(f"{path}/{jid2}/deletions", content=b"junk\n", headers=raw)
        assert r.status_code == 400 and r.json()["detail"] == "invalid_deletions"
        from career_core.usecases.export import export_deletions

        log = tmp_path / "deletions.jsonl"
        await export_deletions(db, owner, log)
        r = await client.post(f"{path}/{jid2}/deletions", content=log.read_bytes(), headers=raw)
        assert r.status_code == 200 and r.json()["deletions"] == 0
        # 잘못된 age 신원은 작업을 시작하기 전에 거부
        r = await client.post(f"{path}/{jid2}/verify", json={"identity": "not a key"}, headers=h)
        assert r.status_code == 400 and r.json()["detail"] == "invalid_identity"
        assert (await client.get(f"{path}/{jid2}", headers=h)).json()["status"] == "uploaded"
        r = await client.delete(f"{path}/{jid2}", headers=h)
        assert r.status_code == 204 and list(uploads.iterdir()) == []

        # 처리 함수도 상한을 센다 (미들웨어가 없을 때)
        small = FastAPI()
        small.include_router(
            build_router(
                CoreDeps(identity=Tokens({"b": other}), db=db, blobs=blobs, max_restore_bytes=1000)
            )
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(small), base_url="http://t"
    ) as client:
        r = await client.post("/core/maintenance/restore", content=data, headers=raw)
        assert r.status_code == 413

        async def chunks():
            for i in range(0, len(data), 512):
                yield data[i : i + 512]

        r = await client.post("/core/maintenance/restore", content=chunks(), headers=raw)
        assert r.status_code == 413
    assert list(uploads.iterdir()) == []
    assert restore_mod.RESTORE_UPLOAD_PATH == "/core/maintenance/restore"


async def test_web_restore_encrypted_archive(db, env, tmp_path, monkeypatch):
    pyrage = pytest.importorskip("pyrage")
    monkeypatch.setenv("CAREER_AGE_BACKEND", "pyrage")
    owner, other, blobs = env["owner"], env["other"], env["blobs"]
    key = pyrage.x25519.Identity.generate()
    out = tmp_path / "a.tar.gz.age"
    await export_workspace(db, blobs, owner, out, recipients=[str(key.to_public())])
    app = FastAPI()
    app.include_router(build_router(CoreDeps(identity=Tokens({"b": other}), db=db, blobs=blobs)))
    h = {"Authorization": "Bearer b"}
    path = "/core/maintenance/restore"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as client:
        r = await client.post(
            path,
            content=out.read_bytes(),
            headers={**h, "Content-Type": "application/octet-stream"},
        )
        job = r.json()
        assert job["encrypted"]
        r = await client.post(f"{path}/{job['id']}/verify", json={}, headers=h)
        assert r.status_code == 400 and r.json()["detail"] == "encrypted_archive"
        identity = f"# synthetic test key\n{key}\n"
        r = await client.post(f"{path}/{job['id']}/verify", json={"identity": identity}, headers=h)
        assert r.status_code == 202
        done = await wait_job(client, job["id"], h)
        assert done["status"] == "verified" and done["report"]["encrypted"]
        # 신원 파일은 남지 않는다
        assert not [p for p in tmp_path.rglob("identity-*")]
        body = {"archive_sha256": job["sha256"], "identity": identity}
        r = await client.post(f"{path}/{job['id']}/apply", json=body, headers=h)
        done = await wait_job(client, job["id"], h)
        assert done["status"] == "done" and done["report"]["rows_added"] > 0


async def test_body_size_limit_override_only_for_the_restore_path():
    from career_core.interfaces.http.limits import BodySizeLimit
    from fastapi import Request

    app = FastAPI()

    @app.post("/api/core/maintenance/restore")
    @app.post("/api/core/sources")
    async def up(request: Request):
        return {"n": len(await request.body())}

    limited = BodySizeLimit(
        app, max_bytes=1000, overrides={("POST", "/api/core/maintenance/restore"): 5000}
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(limited), base_url="http://t") as c:
        assert (
            await c.post("/api/core/maintenance/restore", content=b"x" * 4000)
        ).status_code == 200
        assert (
            await c.post("/api/core/maintenance/restore", content=b"x" * 6000)
        ).status_code == 413
        assert (await c.post("/api/core/sources", content=b"x" * 4000)).status_code == 413
        # 다른 경로(접두어만 같은 경로 포함)는 넓히지 않는다
        r = await c.post("/api/core/maintenance/restore/x/deletions", content=b"x" * 4000)
        assert r.status_code == 413


# ---------------------------------------------------------------- 옛 migration head 변환 (c0024)


def _core_chain():
    """core migration revision (오래된 것 먼저)."""
    from career_core.usecases.export import _parents, core_migration_heads

    parents = _parents()
    [cur] = core_migration_heads()
    chain = []
    while cur is not None:
        chain.append(cur)
        cur = parents[cur]
    return chain[::-1]


@contextlib.contextmanager
def _empty_database():
    """migration을 하나씩 올려 볼 빈 DB (소유자 URL)."""
    admin = admin_url()
    name = f"career_test_{secrets.token_hex(4)}"
    eng = create_engine(admin, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield admin.set(database=name)
    finally:
        with eng.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        eng.dispose()


async def _export_shape(url):
    """내보내는 표 → [(칸, 타입)] (그 시점 DB의 카탈로그)."""
    target = Database(url, poolclass=NullPool)
    try:
        async with target.engine.connect() as conn:
            plan = build_plan(await read_catalog(conn))
    finally:
        await target.dispose()
    return {t.name: [(c.name, c.type) for c in t.columns] for t in plan.tables}


def _synthetic_archive(root, shape, heads):
    """모양만 있는 합성 묶음: 표마다 모든 칸이 null인 행 하나."""
    from career_core.usecases.export import _Archive, _table_file

    (root / "tables").mkdir(parents=True)
    (root / "blobs").mkdir()
    tables = []
    for name, cols in shape.items():
        (root / "tables" / f"{name}.jsonl").write_text(
            json.dumps({c: None for c, _ in cols}) + "\n"
        )
        tables.append(
            {
                "name": name,
                "file": _table_file(name),
                "rows": 1,
                "sha256": "",
                "columns": [list(c) for c in cols],
            }
        )
    manifest = {"tables": tables, "migration_heads": list(heads), "blobs": []}
    return _Archive(manifest, root / "tables", root / "blobs", [], "", 0)


async def test_schema_steps_cover_every_migration(tmp_path):
    """SCHEMA_FLOOR 다음의 모든 core migration에 변환 단계 또는 '바뀜 없음' 표시가 있고, 그것이 맞다.

    migration을 하나씩 올리며 내보내는 표의 모양(표·칸·타입)을 앞뒤로 비교한다:
    - ``no_data_change``인데 모양이 바뀌었으면 실패 → ``@schema_step``을 등록하라는 뜻.
    - 단계는 앞 모양의 합성 묶음(칸이 모두 null인 행)에 적용해 뒤 모양의 표·칸이 되는지 본다.
    새 migration이 내보내는 표를 바꾸면 이 시험이 잡는다 (docs/operations/backup.md "migration 단계 더하기").
    """
    from career_core.usecases.export import SCHEMA_FLOOR, SCHEMA_STEPS

    chain = _core_chain()
    after = chain[chain.index(SCHEMA_FLOOR) + 1 :]
    missing = [r for r in after if r not in SCHEMA_STEPS]
    assert not missing, f"register schema_step or no_data_change for: {missing}"
    assert set(SCHEMA_STEPS) <= set(after), "steps for revisions that are not in the chain"
    if not postgres_available(admin_url()):
        pytest.skip("PostgreSQL not reachable")
    with _empty_database() as url:
        upgrade(url, revision=SCHEMA_FLOOR)
        before = await _export_shape(url)
        for rev in after:
            upgrade(url, revision=rev)
            now = await _export_shape(url)
            step = SCHEMA_STEPS[rev]
            if step.apply is None:
                changed = sorted(n for n in set(before) | set(now) if before.get(n) != now.get(n))
                assert not changed, f"{rev} changes exported tables {changed}: add a schema_step"
            else:
                arc = _synthetic_archive(tmp_path / rev, before, [chain[chain.index(rev) - 1]])
                step.apply(arc)
                got = {e["name"]: e for e in arc.manifest["tables"]}
                assert set(got) == set(now), (rev, sorted(set(got) ^ set(now)))
                for name, cols in now.items():
                    if name in arc.touched:
                        for row in arc.iter_rows(name):
                            assert set(row) == {c for c, _ in cols}, (rev, name)
                    else:
                        assert [tuple(c) for c in got[name]["columns"]] == cols, (rev, name)
            before = now


async def test_archive_from_an_older_migration_head_is_converted(db, tmp_path, monkeypatch):
    """c0020 head에서 만든 진짜 묶음(study_settings)이 지금 설치(workspace_settings)로 들어간다."""
    from career_core.testing import TEST_APP_LOGIN, TEST_APP_PASSWORD
    from career_core.usecases import export as export_mod
    from career_core.usecases.export import SCHEMA_STEPS, SchemaStep, merge_workspace

    blobs = LocalBlobStore(tmp_path / "blobs")
    out = tmp_path / "c0020.tar.gz"
    with _empty_database() as url:
        upgrade(url, revision="c0020")
        old = Database(
            url.set(username=TEST_APP_LOGIN, password=TEST_APP_PASSWORD), poolclass=NullPool
        )
        try:
            ws, me = await create_workspace(old, name="옛 설치 (합성)", owner_display_name="olga")
            owner = owner_of(ws, me)
            await brain.create_experience(old, owner, title="옛 head 경험 (합성)")
            async with old.workspace_session(ws) as s:
                await s.execute(
                    text(
                        "INSERT INTO core.study_settings (id, workspace_id, timezone, updated_by) "
                        "VALUES (:i, :w, 'Europe/Paris', :a)"
                    ),
                    {"i": uuid4(), "w": ws, "a": me},
                )
            # 그 시점 코드처럼 head를 c0020으로 적는다 (표는 DB 카탈로그에서 읽으므로 그대로 옛 모양)
            monkeypatch.setattr(export_mod, "core_migration_heads", lambda: ["c0020"])
            report = await export_workspace(old, blobs, owner, out)
            monkeypatch.undo()
        finally:
            await old.dispose()
    names = {t["name"] for t in report.manifest["tables"]}
    assert "core.study_settings" in names and "core.workspace_settings" not in names

    restored = await restore_workspace(db, blobs, out, new_ids=True)
    assert restored.schema_converted_from == ["c0020"]
    assert any("were converted" in n for n in restored.notes)
    assert restored.rows_total == report.rows_total
    copy = owner_of(restored.workspace_id, restored.owners[0])
    async with db.workspace_session(copy.workspace_id) as s:
        row = (
            await s.execute(text("SELECT timezone, tech_aliases FROM core.workspace_settings"))
        ).one()
    assert (row.timezone, row.tech_aliases) == ("Europe/Paris", [])
    titles = [e.title for e in await brain.list_experiences(db, copy)]
    assert titles == ["옛 head 경험 (합성)"]
    # 같은 묶음을 그 공간에 합치면 모두 겹친다 (변환한 행이 DB 출력과 같은 모양)
    merged = await merge_workspace(db, blobs, copy, out, dry_run=True)
    assert merged.schema_converted_from == ["c0020"] and merged.rows_added == 0

    # 단계가 없는 revision을 지나야 하는 묶음, 이 설치보다 새 묶음은 거부
    def head(value):
        def change(files):
            m = json.loads(files["manifest.json"])
            m["migration_heads"] = [value]
            files["manifest.json"] = json.dumps(m).encode()

        return change

    floor = rewrite(out, tmp_path / "c0016.tar.gz", head("c0016"))
    with pytest.raises(ArchiveError, match="no conversion step for c0018"):
        await restore_workspace(db, blobs, floor, new_ids=True, dry_run=True)
    future = rewrite(out, tmp_path / "c9999.tar.gz", head("c9999"))
    with pytest.raises(ArchiveError, match="version_mismatch"):
        await restore_workspace(db, blobs, future, new_ids=True, dry_run=True)
    # 단계를 '바뀜 없음'으로 잘못 적으면 표 구성이 달라 거부 (반쯤 들어가지 않는다)
    monkeypatch.setitem(SCHEMA_STEPS, "c0021", SchemaStep("c0021", "잘못된 표시", None))
    with pytest.raises(ArchiveError, match="schema_mismatch: tables differ"):
        await restore_workspace(db, blobs, out, new_ids=True, dry_run=True)


# ---------------------------------------------------------------- 합치기 내용 키 (c0024)


async def test_merge_keys_cover_keyless_tables(db):
    """합치는 표마다 겹침을 찾을 키가 있다: NULL 없는 UNIQUE, 내용 키, 자식 키, 또는 '내용만' 이유.

    내용 키의 칸은 그 표에 있고, 칸 목록 키는 NOT NULL 칸만 쓴다 (NULL이면 키가 걸리지 않는다).
    새 표를 더하면 여기서 분류를 요구한다.
    """
    from career_core.usecases.export import (
        CHILD_MERGE_KEYS,
        CONTENT_ONLY_TABLES,
        EXTRA_MERGE_KEYS,
        MERGE_SKIPPED_TABLES,
        SameExcept,
    )

    async with db.workspace_session(uuid4()) as s:
        plan = build_plan(await read_catalog(await s.connection()))
    merged = {
        t.name: t
        for t in plan.tables
        if t.scope == "workspace" and t.name not in MERGE_SKIPPED_TABLES
    }
    assert set(EXTRA_MERGE_KEYS) | set(CONTENT_ONLY_TABLES) | set(CHILD_MERGE_KEYS) <= set(merged)
    assert not set(EXTRA_MERGE_KEYS) & set(CONTENT_ONLY_TABLES)
    unclassified = []
    for name, t in merged.items():
        cols = {c.name: c for c in t.columns}
        for key in EXTRA_MERGE_KEYS.get(name, ()):
            if isinstance(key, SameExcept):
                assert set(key.ignore) <= set(cols), (name, set(key.ignore) - set(cols))
                assert not {"id", "workspace_id", "created_at"} & set(key.ignore), name
            else:
                assert all(cols[c].not_null for c in key), (name, key)
        strong = any(
            "id" not in key and all(cols[c].not_null for c in key) and set(key) != {"workspace_id"}
            for _, key in t.unique_keys
        ) or ("workspace_id",) in [k for _, k in t.unique_keys]
        if not (
            strong
            or name in EXTRA_MERGE_KEYS
            or name in CHILD_MERGE_KEYS
            or name in CONTENT_ONLY_TABLES
        ):
            unclassified.append(name)
    assert not unclassified, f"add EXTRA_MERGE_KEYS or CONTENT_ONLY_TABLES for {unclassified}"


async def test_merge_recognises_rows_changed_after_export(db, env, tmp_path):
    """A를 B에 합친 뒤 A에서 상태·메모·시각만 고치고 다시 합쳐도 늘지 않는다. 정체 칸을 고친 행은 더한다."""
    from career_core.usecases.export import merge_workspace

    owner, other, blobs = env["owner"], env["other"], env["blobs"]
    ws = owner.workspace_id
    first = tmp_path / "first.tar.gz"
    await export_workspace(db, blobs, owner, first)
    assert (await merge_workspace(db, blobs, other, first)).rows_added > 0
    edits = [
        "UPDATE core.experiences SET updated_at = now()",
        "UPDATE core.claims SET updated_at = now()",
        "UPDATE core.applications SET result_note = '합성 결과 메모', updated_at = now()",
        "UPDATE core.application_stages SET note = '합성 메모 고침', updated_at = now()",
        "UPDATE core.study_items SET notes = '합성 메모 고침', updated_at = now()",
        "UPDATE core.study_sessions SET notes = '합성 메모 고침'",
        "UPDATE core.study_mistakes SET repetitions = repetitions + 1, updated_at = now()",
    ]
    async with db.workspace_session(ws) as s:
        for sql in edits:
            await s.execute(text(sql))
    second = tmp_path / "second.tar.gz"
    await export_workspace(db, blobs, owner, second)
    again = await merge_workspace(db, blobs, other, second, dry_run=True)
    t = {x.name: x for x in again.tables}
    for name in (
        "core.experiences",
        "core.claims",
        "core.applications",
        "core.application_stages",
        "core.study_items",
        "core.study_sessions",
        "core.study_mistakes",
    ):
        assert t[name].added == 0, (name, t[name].skipped_by)
        assert any(k.startswith("same_except:") for k in t[name].skipped_by), name
    assert again.rows_added == 0

    # 문장(정체 칸)을 고친 주장은 다른 주장으로 더한다 (다른 행을 잘못 합쳐 잃지 않게)
    async with db.workspace_session(ws) as s:
        await s.execute(
            text("UPDATE core.claims SET statement = statement || ' (고침)' WHERE id = :c"),
            {"c": env["a"]["claim"]},
        )
    third = tmp_path / "third.tar.gz"
    await export_workspace(db, blobs, owner, third)
    changed = await merge_workspace(db, blobs, other, third, dry_run=True)
    assert {x.name: x.added for x in changed.tables}["core.claims"] == 1


# ---------------------------------------------------------------- 합친 뒤 승인 다시 확인 (c0024)


async def test_merge_rechecks_open_approvals(db, env, tmp_path):
    """합치기가 지원의 문서를 되살리면 그 지원의 열린 승인은 대상 버전이 달라져 같은 트랜잭션에서 닫힌다."""
    from career_core.db.models import Approval
    from career_core.usecases.export import merge_workspace

    owner, a, blobs = env["owner"], env["a"], env["blobs"]
    ws, app = owner.workspace_id, a["application"]
    out = tmp_path / "a.tar.gz"
    await export_workspace(db, blobs, owner, out)
    # 내보낸 뒤 지원 문서(복사본) 하나를 지우고, 지금 상태로 제출 승인을 요청한다
    async with db.workspace_session(ws) as s:
        await s.execute(
            text("DELETE FROM core.application_documents WHERE id = :d"), {"d": a["copy"]}
        )
        version = await s.scalar(text("SELECT core.application_target_version(:a)"), {"a": app})
        approval = Approval(
            workspace_id=ws,
            action="applications.submit",
            target_kind="application",
            target_id=app,
            target_version=version,
            payload_hash="0" * 64,
            requested_by=owner.subject_id,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        s.add(approval)

    async def state():
        async with db.workspace_session(ws) as s:
            row = await s.get(Approval, approval.id, populate_existing=True)
            return row.status, row.invalid_reason

    dry = await merge_workspace(db, blobs, owner, out, dry_run=True)
    assert dry.approvals_invalidated == 1 and await state() == ("pending", None)
    merged = await merge_workspace(db, blobs, owner, out)
    assert {x.name: x.added for x in merged.tables}["core.application_documents"] == 1
    assert merged.approvals_invalidated == 1
    assert await state() == ("invalidated", "target_changed")
    # 바뀐 것이 없으면 닫지 않는다: 지금 버전으로 다시 요청하고 같은 묶음을 또 합친다
    async with db.workspace_session(ws) as s:
        version = await s.scalar(text("SELECT core.application_target_version(:a)"), {"a": app})
        fresh = Approval(
            workspace_id=ws,
            action="applications.submit",
            target_kind="application",
            target_id=app,
            target_version=version,
            payload_hash="1" * 64,
            requested_by=owner.subject_id,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        s.add(fresh)
    again = await merge_workspace(db, blobs, owner, out)
    assert again.rows_added == 0 and again.approvals_invalidated == 0
    async with db.workspace_session(ws) as s:
        assert (await s.get(Approval, fresh.id)).status == "pending"


# ---------------------------------------------------------------- 웹 복원 작업 상태 DB (c0024)


async def test_web_restore_jobs_are_durable_across_processes(db, env, tmp_path):
    """작업 상태는 DB, 파일은 공유 폴더: 다른 프로세스가 이어 받고, 재시작 뒤 끊긴 작업은 interrupted로 닫힌다."""
    import os
    import time

    from career_core.db.models import RestoreJob

    owner, other, blobs = env["owner"], env["other"], env["blobs"]
    shared = tmp_path / "shared"
    out = tmp_path / "a.tar.gz"
    await export_workspace(db, blobs, owner, out)
    data = out.read_bytes()

    def process():
        app = FastAPI()
        app.include_router(
            build_router(
                CoreDeps(identity=Tokens({"b": other}), db=db, blobs=blobs, restore_dir=str(shared))
            )
        )
        return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t")

    h = {"Authorization": "Bearer b"}
    raw = {**h, "Content-Type": "application/octet-stream"}
    path = "/core/maintenance/restore"
    async with process() as one, process() as two:
        assert (await one.get(path, headers=h)).status_code == 404
        job = (await one.post(path, content=data, headers=raw)).json()
        jid = job["id"]
        assert [p.name for p in shared.iterdir()] == [f"career-restore-upload-{jid}"]
        # 다른 프로세스가 같은 작업을 본다 (지금 작업 조회 포함)
        assert (await two.get(f"{path}/{jid}", headers=h)).json()["sha256"] == job["sha256"]
        assert (await two.get(path, headers=h)).json()["id"] == jid
        # 다른 프로세스가 확인을 돌리고, 처음 프로세스가 결과를 읽는다
        r = await two.post(f"{path}/{jid}/verify", json={"on_duplicate": "skip"}, headers=h)
        assert r.status_code == 202
        r = await one.post(f"{path}/{jid}/verify", json={"on_duplicate": "skip"}, headers=h)
        assert r.status_code == 409 and r.json()["detail"] == "restore_busy"
        done = await wait_job(one, jid, h)
        assert done["status"] == "verified" and done["report"]["rows_added"] > 0

    # 합치는 중에 프로세스가 죽었다: 상태는 applying, 생존 신호는 오래전
    async with db.workspace_session(other.workspace_id) as s:
        await s.execute(
            update(RestoreJob)
            .where(RestoreJob.id == jid)
            .values(
                status="applying",
                phase="insert",
                runner="pid0-gone",
                heartbeat_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
    async with process() as three:  # 재시작
        job = (await three.get(f"{path}/{jid}", headers=h)).json()
        assert (job["status"], job["error"]) == ("failed", "interrupted")
        assert "insert" in job["error_detail"]
        # 올린 파일은 남아 있어 같은 작업을 다시 확인하고 합칠 수 있다
        r = await three.post(f"{path}/{jid}/verify", json={"on_duplicate": "skip"}, headers=h)
        assert r.status_code == 202
        job = await wait_job(three, jid, h)
        assert job["status"] == "verified"
        body = {"on_duplicate": "skip", "archive_sha256": job["sha256"]}
        assert (await three.post(f"{path}/{jid}/apply", json=body, headers=h)).status_code == 202
        job = await wait_job(three, jid, h)
        assert job["status"] == "done" and job["report"]["rows_added"] > 0
        assert list(shared.iterdir()) == []  # 합친 뒤 지운다

        # 오래 손대지 않은 작업은 읽을 때 지워진다 (행과 폴더)
        job = (await three.post(path, content=data, headers=raw)).json()
        async with db.workspace_session(other.workspace_id) as s:
            await s.execute(
                update(RestoreJob)
                .where(RestoreJob.id == job["id"])
                .values(updated_at=datetime.now(UTC) - timedelta(hours=2))
            )
        assert (await three.get(f"{path}/{job['id']}", headers=h)).status_code == 404
        assert list(shared.iterdir()) == []
        async with db.workspace_session(other.workspace_id) as s:
            assert await s.get(RestoreJob, UUID(job["id"])) is None

    # 다른 프로세스가 남긴 옛 폴더는 시작할 때 지운다 (폴더 시각 기준)
    stale = shared / f"career-restore-upload-{uuid4()}"
    stale.mkdir()
    old = time.time() - 7200
    os.utime(stale, (old, old))
    fresh = shared / f"career-restore-upload-{uuid4()}"
    fresh.mkdir()
    async with process():
        pass
    assert sorted(shared.iterdir()) == [fresh]
    # 올린 파일이 지워진 작업은 410 upload_missing (다시 올리라고)
    async with process() as four:
        job = (await four.post(path, content=data, headers=raw)).json()
        import shutil

        shutil.rmtree(shared / f"career-restore-upload-{job['id']}")
        r = await four.post(f"{path}/{job['id']}/verify", json={}, headers=h)
        assert r.status_code == 410 and r.json()["detail"] == "upload_missing"
        job = (await four.get(f"{path}/{job['id']}", headers=h)).json()
        assert (job["status"], job["error"]) == ("failed", "upload_missing")

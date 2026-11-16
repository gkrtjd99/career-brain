"""O11 완료 기준: 빈 환경에서 복원 후 핵심 데이터 수·해시 확인.

고정 회귀 시험 1(다른 공간은 내보내기에 없다), 12(제출본 불변), 13(복원된 원본·관계·문서 연결).
모든 자료는 합성이다.
"""

import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import Base, Database, JobPosting, JobPostingVersion, ModelCall
from career_core.db.models import ApplicationSubmission, DocumentSuggestion, Workspace
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import parsing_handlers, run_until_idle
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext
from career_core.testing import migrated_database
from career_core.usecases import application_stages as stages_uc
from career_core.usecases import applications as apps_uc
from career_core.usecases import brain, interviews
from career_core.usecases import documents as docs_uc
from career_core.usecases import postings as postings_uc
from career_core.usecases import study as study_uc
from career_core.usecases.export import (
    EXCLUDED_TABLES,
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
from sqlalchemy import func, select, text, update
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
    assert manifest["migration_heads"] and manifest["format_version"] == 1
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
    # 열린 승인: 복원이 문서 행을 넣어도 (c0014 무효화 trigger) 묶음의 상태 그대로 남는다
    async with db.workspace_session(owner.workspace_id) as s:
        s.add(
            Approval(
                workspace_id=owner.workspace_id,
                action="applications.submit",
                target_kind="application",
                target_id=a["application"],
                target_version="0" * 64,
                payload_hash="0" * 64,
                requested_by=owner.subject_id,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
    out = tmp_path / "a.tar.gz"
    report = await export_workspace(db, env["blobs"], owner, out)
    copy = await restore_workspace(db, env["blobs"], out, new_ids=True)
    async with db.workspace_session(copy.workspace_id) as s:
        statuses = (await s.execute(select(Approval.status))).scalars().all()
    assert statuses == ["pending"]
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

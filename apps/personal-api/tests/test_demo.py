"""`personal-api demo`: 합성 데모 공간 (모델·네트워크 호출 없음, 다시 실행하면 새 공간)."""

import urllib.request

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.web_fetch import BrowserFetcher, HttpFetcher
from career_core.db import (
    Claim,
    ClaimEvidence,
    DocumentBlock,
    Experience,
    InterviewAnswer,
    JobPosting,
    JobPostingVersion,
    SourceSpan,
    StudyAssessment,
    StudyAttempt,
    StudyItem,
    StudyMistake,
    StudyRequirement,
    StudyRequirementClaim,
    StudySession,
)
from career_core.db.models import (
    Application,
    ApplicationDocument,
    AuditEvent,
    DocumentClaim,
    DocumentRevision,
    JobFetchRun,
    ModelCall,
    ParseRun,
    Source,
)
from career_core.llm.gateway import ModelGateway
from career_core.usecases import applications as app_uc
from career_core.usecases import study as study_uc
from personal_api import cli
from personal_api.demo import seed_demo
from personal_api.identity import PersonalTokenResolver, list_tokens
from personal_api.settings import Settings
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio


@pytest.fixture
def no_egress(monkeypatch):
    """모델·네트워크 경로를 부르면 바로 실패하게 한다."""

    def boom(*_a, **_k):
        raise AssertionError("demo must not call a model or the network")

    async def aboom(*_a, **_k):
        boom()

    monkeypatch.setattr(ModelGateway, "run", aboom)
    monkeypatch.setattr(ModelGateway, "embed", aboom)
    monkeypatch.setattr(HttpFetcher, "fetch", aboom)
    monkeypatch.setattr(HttpFetcher, "fetch_raw", aboom)
    monkeypatch.setattr(BrowserFetcher, "fetch", aboom)
    monkeypatch.setattr(httpx.AsyncClient, "send", aboom)
    monkeypatch.setattr(httpx.Client, "send", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)


async def _count(s, model, *where) -> int:
    return await s.scalar(select(func.count()).select_from(model).where(*where))


async def test_demo_fills_every_area_without_model_or_network(db, tmp_path, no_egress):
    result = await seed_demo(db, LocalBlobStore(str(tmp_path)))
    actor = await PersonalTokenResolver(db).resolve(result.token)
    assert actor.workspace_id == result.workspace_id and actor.has_scope("approve")
    assert result.workspace_name == "데모"

    async with db.workspace_session(result.workspace_id) as s:
        # 브레인: 원본 3개, 모두 파싱 완료, 인용 근거
        assert await _count(s, Source) == 3
        statuses = (await s.execute(select(ParseRun.status))).scalars().all()
        assert sorted(statuses) == ["complete"] * 3
        assert await _count(s, DocumentBlock) >= 10
        assert await _count(s, SourceSpan) == 8
        assert await _count(s, Experience) == 4
        # 경력 하나 아래 프로젝트 둘 (한 단계), 기간은 달 단위
        assert await _count(s, Experience, Experience.parent_id.is_not(None)) == 2
        assert await _count(s, Experience, Experience.started_on.is_(None)) == 0
        by_status = dict(
            (await s.execute(select(Claim.status, func.count()).group_by(Claim.status))).all()
        )
        assert by_status == {"evidence_supported": 6, "self_confirmed": 1, "extracted": 3}
        assert await _count(s, ClaimEvidence, ClaimEvidence.kind == "source_span") == 8
        assert await _count(s, InterviewAnswer) == 2
        # 공고: 저장 1, 확인 대기 1. 가져오기는 오프라인 실패 → 붙여넣은 본문 버전
        postings = dict(
            (
                await s.execute(select(JobPosting.status, func.count()).group_by(JobPosting.status))
            ).all()
        )
        assert postings == {"saved": 1, "draft": 1}
        assert await _count(s, JobPostingVersion, JobPostingVersion.method == "manual_text") == 2
        assert await _count(s, JobPostingVersion, JobPostingVersion.model_call_id.isnot(None)) == 0
        fetches = (await s.execute(select(JobFetchRun.status, JobFetchRun.error_code))).all()
        assert sorted(fetches) == [
            ("done", None),
            ("done", None),
            ("failed", "needs_manual_check"),
            ("failed", "needs_manual_check"),
        ]
        # 지원·문서
        assert await _count(s, Application) == 1
        assert await _count(s, ApplicationDocument) == 3  # 문항 2 + 공용 이력서
        assert await _count(s, DocumentRevision) == 2
        assert await _count(s, DocumentClaim) >= 5
        # 학습
        assert await _count(s, StudyRequirement) == 3
        assert await _count(s, StudyRequirementClaim) == 1
        assert await _count(s, StudyItem) == 2
        assert await _count(s, StudyAssessment) == 1
        assert await _count(s, StudySession) == 1
        assert await _count(s, StudyAttempt) == 1
        assert await _count(s, StudyMistake) == 2
        # 모델 호출 장부는 비어 있고, 감사 기록 한 줄
        assert await _count(s, ModelCall) == 0
        assert await _count(s, AuditEvent, AuditEvent.event == "demo_seed") == 1
        app_id = await s.scalar(select(Application.id))
        posting_id = await s.scalar(select(JobPosting.id).where(JobPosting.status == "saved"))

    # 문서 검사 탭: 글자 수, 근거 연결, 요건 가시성, 상투어 경고
    checks = await app_uc.application_checks(db, actor, app_id)
    first = next(d for d in checks["documents"] if d["revision"] == 1)
    assert 0 < first["counts"]["chars"] <= 700
    assert checks["requirements"]
    doc_check = await app_uc.validate_document(db, actor, app_id, first["document_id"])
    codes = {f["code"] for f in doc_check["findings"]}
    assert codes, doc_check["findings"]
    # 학습: 요건 하나는 경험으로 채워졌고, 복습은 내일 첫 일정
    coverage = await study_uc.posting_coverage(db, actor, posting_id)
    assert "evidenced" in {r.coverage for r in coverage.requirements}
    assert await study_uc.due_reviews(db, actor) == []
    assert len(await study_uc.list_mistakes(db, actor)) == 2


async def test_second_run_creates_another_workspace(db, tmp_path, no_egress):
    blobs = LocalBlobStore(str(tmp_path))
    one = await seed_demo(db, blobs)
    two = await seed_demo(db, blobs, name="데모 2")
    assert one.workspace_id != two.workspace_id and one.token != two.token
    a = await PersonalTokenResolver(db).resolve(one.token)
    b = await PersonalTokenResolver(db).resolve(two.token)
    assert (a.workspace_id, b.workspace_id) == (one.workspace_id, two.workspace_id)
    for r in (one, two):
        async with db.workspace_session(r.workspace_id) as s:
            assert await _count(s, Source) == 3
            assert await _count(s, Claim) == 10


async def test_cli_prints_one_token_and_nothing_secret(pg, db, tmp_path, no_egress, capsys):
    settings = Settings(database_url=pg.app.render_as_string(False), blob_dir=str(tmp_path))
    assert await cli._demo(settings, "  가상 데모  ") == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    tokens = [line for line in lines if line.startswith("cbp_")]
    assert len(tokens) == 1 and out.count("cbp_") == 1
    assert lines[lines.index(tokens[0]) - 1].endswith("store it safely):")
    assert pg.app.password not in out and "postgresql" not in out
    actor = await PersonalTokenResolver(db).resolve(tokens[0])
    assert actor is not None
    [row] = await list_tokens(db, workspace_id=actor.workspace_id)
    assert row.label == "demo"
    assert "(가상 데모)" in out  # 이름은 공백을 정리해 쓴다

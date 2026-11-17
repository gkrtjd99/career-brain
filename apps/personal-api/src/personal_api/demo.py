"""`personal-api demo`: 모델 키 없이 화면을 둘러볼 수 있는 합성 데모 공간.

새 공간 + owner + API 토큰을 만들고, 주요 화면마다 볼거리가 생기게 가상의 한국어 자료를 채운다.
모든 쓰기는 웹·MCP와 같은 usecase를 거친다 (직접 INSERT 없음: RLS·불변 규칙이 그대로 걸린다).

- 모델 호출 없음: gateway를 만들지 않는다. 공고 항목 추출·AI 질문·AI 수정안은 쓰지 않는다.
- 네트워크 없음: 공고는 붙여넣은 본문 + 사람이 확인한 항목이다. 공고 주소는 문서용 주소
  (RFC 5737 TEST-NET-1, ``192.0.2.0/24``)라 다른 worker가 먼저 집어도 내부 주소 차단에서 멈춘다
  (DNS 조회도 없다). 이 명령 안의 가져오기는 오프라인 fetcher로 "직접 붙여넣기 필요"로 끝낸다.
- 다시 실행하면 데모 공간이 하나 더 생긴다. 앱 계정은 다른 공간을 볼 수 없어(RLS) "이미 있음"을
  확인할 수 없고, 공간마다 토큰이 따로라 서로 섞이지 않는다.

이름·회사·수치는 모두 지어낸 것이다 (회사 이름에 "(가상 회사)"를 붙였다).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from career_core.adapters.audit import DbAuditSink
from career_core.adapters.parsers import default_local_parsers
from career_core.adapters.web_fetch import FetchFailure
from career_core.db import Database, DocumentBlock, Job
from career_core.domain.postings import FetchError
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext, BlobStore
from career_core.usecases import applications as app_uc
from career_core.usecases import brain as brain_uc
from career_core.usecases import documents as doc_uc
from career_core.usecases import interviews as interview_uc
from career_core.usecases import postings as posting_uc
from career_core.usecases import study as study_uc
from career_core.usecases.parsing import ParsingDeps, process_parse_run
from career_core.usecases.postings import FETCH_JOB, PostingDeps
from career_core.usecases.sources import PARSE_JOB, upload_source
from career_core.usecases.workspaces import create_workspace
from sqlalchemy import select

from personal_api.identity import PersonalTokenResolver, issue_token

DEFAULT_NAME = "데모"
DEFAULT_DISPLAY_NAME = "데모 사용자"
TOKEN_LABEL = "demo"
# 다른 worker가 같은 작업을 처리 중이면 끝날 때까지 기다리는 상한 (초)
SETTLE_TIMEOUT = 60.0

# ---------------------------------------------------------------- 합성 원본

RESUME = """\
# 이력서 (데모용 가상 자료)

이 문서는 career-brain 데모를 위해 지어낸 자료입니다. 실제 인물·회사와 관계없습니다.

## 요약

백엔드 개발 4년 차. 주문·정산처럼 돈이 오가는 시스템을 Python과 PostgreSQL로 만들고 운영했습니다.

## 경력: 가상커머스 (가상 회사) · 백엔드 개발자 · 2022.03 – 현재

주문 정산 배치를 단계별로 다시 설계하고 실패한 단계부터 재시작할 수 있게 바꿨습니다.

사내 상품 검색 API에 PostgreSQL pg_trgm 색인을 도입해 응답 시간 p95를 1.8초에서 0.4초로 줄였습니다.

신규 입사자 3명의 온보딩 문서를 만들고 코드 리뷰 기준을 정리했습니다.

## 기술

Python, FastAPI, PostgreSQL, Redis, Docker
"""

RETRO = """\
# 정산 배치 개편 회고 (데모용 가상 자료)

## 배경

가상커머스(가상 회사)의 일일 정산 배치는 하루 주문 약 12만 건을 한 번에 처리했고, 중간에 실패하면 처음부터 다시 돌려야 했습니다. 처리 시간은 평균 4시간이었습니다.

## 내가 한 일

배치를 주문 수집, 금액 계산, 지급 파일 생성의 세 단계로 나누고, 단계마다 완료 지점을 PostgreSQL 테이블에 기록해 실패한 단계부터 다시 시작할 수 있게 했습니다.

금액 계산 단계는 판매자 단위로 나눠 병렬로 처리했습니다. 같은 판매자를 두 작업이 동시에 잡지 않도록 SKIP LOCKED 행 잠금을 썼습니다.

## 결과

처리 시간이 평균 4시간에서 50분으로 줄었습니다.

정산 담당 팀과 함께 검증 리포트를 만든 뒤 정산 오류 문의가 월 30건 안팎에서 5건 안팎으로 줄었습니다. 이 수치는 팀 전체의 결과입니다.

## 고민했던 선택

메시지 큐를 새로 도입하는 안도 검토했지만, 운영 인원이 적어 이미 쓰고 있던 PostgreSQL만으로 작업 상태를 관리하는 쪽을 골랐습니다.
"""

SIDE = """\
사이드 프로젝트 노트: 스터디 일정 알림 봇 (데모용 가상 자료)

동네 스터디 모임(가상) 8명이 쓰는 일정 알림 봇을 혼자 만들었다. FastAPI와 SQLite로 시작했고 매주 바뀐 일정을 메신저로 알려 준다.

알림이 두 번 가는 문제가 있었다. 재시도할 때 같은 메시지를 다시 보내지 않도록 알림마다 멱등 키를 저장해서 해결했다.

다음에는 테스트를 먼저 쓰고 싶다. 이번에는 배포한 뒤에 버그를 찾은 일이 많았다.
"""

SOURCES = (
    ("resume", "데모 이력서 (가상)", "demo-resume.md", "text/markdown", RESUME),
    ("retro", "정산 배치 개편 회고 (가상)", "demo-settlement-retro.md", "text/markdown", RETRO),
    ("side", "사이드 프로젝트 노트 (가상)", "demo-side-project.txt", "text/plain", SIDE),
)

# ---------------------------------------------------------------- 공고 (붙여넣은 본문)

# RFC 5737 문서용 주소: 실제로 가져오지 않는다 (내부 주소 차단, DNS 조회 없음)
POSTING_A_URL = "https://192.0.2.10/demo/jobs/backend-settlement"
POSTING_B_URL = "https://192.0.2.20/demo/jobs/data-platform"

POSTING_A_TEXT = """\
[가상핀테크 (가상 회사)] 백엔드 엔지니어 (정산 플랫폼)

데모용 가상 공고입니다. 실제 회사·채용과 관계없습니다.

근무지: 서울 / 고용 형태: 정규직 / 경력 3년 이상

주요 업무
- 정산·지급 배치 설계와 운영
- 결제 데이터 정합성 검증 도구 개발

자격 요건
- Python 또는 Java 기반 백엔드 개발 경력 3년 이상
- PostgreSQL 등 관계형 데이터베이스 설계·튜닝 경험
- 대용량 배치 처리 경험

우대 사항
- Kafka 등 메시지 큐 운영 경험
- 결제·정산 도메인 경험

전형 절차
서류 전형 - 코딩테스트 - 기술 면접 - 최종 면접

자기소개서 문항
1. 지원 직무와 관련해 가장 자신 있는 경험을 구체적으로 적어 주세요. (공백 포함 700자 이내)
2. 입사 후 이루고 싶은 목표를 적어 주세요. (공백 포함 500자 이내)
"""

POSTING_B_TEXT = """\
[가상물류 (가상 회사)] 데이터 플랫폼 엔지니어

데모용 가상 공고입니다. 실제 회사·채용과 관계없습니다.

자격 요건
- 데이터 파이프라인 개발 경력 2년 이상
- SQL과 Python에 능숙하신 분

우대 사항
- Airflow 등 워크플로 도구 운영 경험

전형 절차
서류 전형 - 과제 전형 - 면접
"""

QUESTION_1 = (
    "지원 직무와 관련해 가장 자신 있는 경험을 구체적으로 적어 주세요. (공백 포함 700자 이내)"
)
QUESTION_2 = "입사 후 이루고 싶은 목표를 적어 주세요. (공백 포함 500자 이내)"
REQ_BATCH = "대용량 배치 처리 경험"
REQ_DB = "PostgreSQL 등 관계형 데이터베이스 설계·튜닝 경험"
PREF_KAFKA = "Kafka 등 메시지 큐 운영 경험"
PROCESS = "서류 전형 - 코딩테스트 - 기술 면접 - 최종 면접"


def posting_a_fields(deadline: str) -> dict:
    """사람이 공고 본문을 보고 확인한 항목 (확정 화면의 직접 입력과 같은 경로, 근거는 `user`)."""
    return {
        "title": "백엔드 엔지니어 (정산 플랫폼)",
        "company": "가상핀테크 (가상 회사)",
        "location": "서울",
        "employment_type": "정규직",
        "career_level": "경력 3년 이상",
        "deadline": deadline,
        "responsibilities": ["정산·지급 배치 설계와 운영", "결제 데이터 정합성 검증 도구 개발"],
        "requirements": ["Python 또는 Java 기반 백엔드 개발 경력 3년 이상", REQ_DB, REQ_BATCH],
        "preferred": [PREF_KAFKA, "결제·정산 도메인 경험"],
        "process": [PROCESS],
        "questions": [
            {"prompt": QUESTION_1, "char_limit": 700},
            {"prompt": QUESTION_2, "char_limit": 500},
        ],
    }


# ---------------------------------------------------------------- 결과


@dataclass(slots=True)
class DemoResult:
    workspace_id: UUID
    workspace_name: str
    token: str = field(repr=False)  # 출력은 CLI가 한 번만 한다
    counts: dict[str, int] = field(default_factory=dict)


class _OfflineFetcher:
    """데모 공고는 가져오지 않는다: 직접 붙여넣기로 넘어가게 하는 실패를 돌려준다 (네트워크 없음)."""

    async def fetch(self, url: str) -> FetchFailure:
        return FetchFailure(FetchError.NEEDS_MANUAL_CHECK.value, detail="demo_offline")


async def _settle(db: Database, ws: UUID, kind: str, target_id: UUID) -> None:
    """다른 worker(serve 내장 등)가 같은 작업을 처리하는 중이면 끝날 때까지 기다린다."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SETTLE_TIMEOUT
    while True:
        async with db.workspace_session(ws) as s:
            running = await s.scalar(
                select(Job.id).where(
                    Job.kind == kind, Job.target_id == target_id, Job.status == "running"
                )
            )
        if running is None or loop.time() > deadline:
            return
        await asyncio.sleep(0.2)


async def _blocks(db: Database, ws: UUID, parse_run_id: UUID) -> list[DocumentBlock]:
    async with db.workspace_session(ws) as s:
        return list(
            (
                await s.execute(
                    select(DocumentBlock)
                    .where(DocumentBlock.parse_run_id == parse_run_id)
                    .order_by(DocumentBlock.ordinal)
                )
            ).scalars()
        )


class _Spans:
    """원본별 블록에서 인용을 찾아 근거 위치(source span)를 만든다 (웹의 "문장 골라 근거로"와 같다)."""

    def __init__(self, db: Database, actor: ActorContext, blocks: dict[str, list]) -> None:
        self.db, self.actor, self.blocks = db, actor, blocks

    async def __call__(self, source: str, quote: str) -> UUID:
        for b in self.blocks[source]:
            start = b.text.find(quote)
            if start >= 0:
                return await brain_uc.create_span(
                    self.db, self.actor, b.id, start, start + len(quote)
                )
        raise LookupError(f"demo quote not found in {source}: {quote[:30]}")


async def _sources(
    db: Database, blobs: BlobStore, actor: ActorContext, max_bytes: int
) -> dict[str, list]:
    """업로드 → 파싱(이 프로세스에서 바로, 로컬 텍스트 추출만) → 블록."""
    deps = ParsingDeps(
        db=db,
        blobs=blobs,
        egress=ProviderEgressPolicy({}),  # 외부 파서 없음
        local_parsers=default_local_parsers(),
    )
    out: dict[str, list] = {}
    for key, title, filename, mime, body in SOURCES:
        up = await upload_source(
            db,
            blobs,
            actor,
            filename=filename,
            data=body.encode("utf-8"),
            declared_mime=mime,
            max_bytes=max_bytes,
            title=title,
        )
        await process_parse_run(deps, actor.workspace_id, up.parse_run_id)
        await _settle(db, actor.workspace_id, PARSE_JOB, up.parse_run_id)
        out[key] = await _blocks(db, actor.workspace_id, up.parse_run_id)
        if not out[key]:
            raise RuntimeError(f"demo source {filename} produced no blocks")
    return out


async def _brain(db: Database, actor: ActorContext, span) -> dict[str, UUID]:
    """경험 3개, 주장(확인됨 + 확인 대기), 인터뷰 답. 반환: 뒤에서 쓰는 주장 ID."""
    claims: dict[str, UUID] = {}
    settle = await brain_uc.create_experience(
        db,
        actor,
        title="주문 정산 배치 개편",
        organization="가상커머스 (가상 회사)",
        role="백엔드 개발자",
        period_text="2023.03 – 2024.08",
    )
    search = await brain_uc.create_experience(
        db,
        actor,
        title="사내 상품 검색 API 성능 개선",
        organization="가상커머스 (가상 회사)",
        role="백엔드 개발자",
        period_text="2022.06 – 2022.12",
    )
    side = await brain_uc.create_experience(
        db,
        actor,
        title="사이드 프로젝트: 스터디 일정 알림 봇",
        organization="개인 프로젝트",
        role="1인 개발",
        period_text="2024.10 – 2025.01",
    )

    async def claim(exp, key, statement, kind, contribution, source, quote) -> None:
        claims[key] = await brain_uc.add_claim(
            db,
            actor,
            exp,
            statement=statement,
            kind=kind,
            contribution=contribution,
            span_ids=[await span(source, quote)],
        )

    # 본인/팀 구분까지 정한 주장은 바로 "자료로 확인됨"이 된다 (owner = 확인 권한)
    await claim(
        settle,
        "settle_action",
        "일일 정산 배치를 세 단계로 나누고 완료 지점을 PostgreSQL 테이블에 기록해 실패한 단계부터 "
        "재시작되게 했다.",
        "action",
        "personal",
        "retro",
        "배치를 주문 수집, 금액 계산, 지급 파일 생성의 세 단계로 나누고",
    )
    await claim(
        settle,
        "settle_result",
        "정산 배치 처리 시간을 평균 4시간에서 50분으로 줄였다.",
        "result",
        "personal",
        "retro",
        "처리 시간이 평균 4시간에서 50분으로 줄었습니다.",
    )
    await claim(
        settle,
        "settle_team",
        "정산 담당 팀과 검증 리포트를 만들어 정산 오류 문의가 월 30건 안팎에서 5건 안팎으로 줄었다.",
        "result",
        "team",
        "retro",
        "정산 오류 문의가 월 30건 안팎에서 5건 안팎으로 줄었습니다",
    )
    await claim(
        search,
        "skill_python",
        "Python과 PostgreSQL로 주문·정산 시스템을 만들고 운영했다.",
        "skill",
        "personal",
        "resume",
        "Python과 PostgreSQL로 만들고 운영했습니다.",
    )
    await claim(
        search,
        "search_result",
        "상품 검색 API에 pg_trgm 색인을 도입해 응답 시간 p95를 1.8초에서 0.4초로 줄였다.",
        "result",
        "personal",
        "resume",
        "응답 시간 p95를 1.8초에서 0.4초로 줄였습니다.",
    )
    await claim(
        side,
        "side_action",
        "재시도 때 같은 알림을 다시 보내지 않도록 알림마다 멱등 키를 저장했다.",
        "difficulty",
        "personal",
        "side",
        "알림마다 멱등 키를 저장해서 해결했다.",
    )
    # 확인 대기: 본인 몫인지 팀 몫인지 아직 정하지 않았다 → "사람이 확인해야 사실"
    await claim(
        settle,
        "settle_decision",
        "메시지 큐 대신 기존 PostgreSQL로 작업 상태를 관리하기로 했다.",
        "decision",
        "unknown",
        "retro",
        "이미 쓰고 있던 PostgreSQL만으로 작업 상태를 관리하는 쪽을 골랐습니다.",
    )
    await claim(
        search,
        "search_onboarding",
        "신규 입사자 3명의 온보딩 문서를 만들고 코드 리뷰 기준을 정리했다.",
        "action",
        "unknown",
        "resume",
        "신규 입사자 3명의 온보딩 문서를 만들고 코드 리뷰 기준을 정리했습니다.",
    )

    # 인터뷰: 템플릿 질문(모델 없음)에 사용자가 쓴 답. 답 원문이 그대로 근거가 된다.
    session = await interview_uc.start_interview(db, actor, settle)
    view = await interview_uc.get_interview(db, actor, session)
    by_focus = {q.focus: q.id for q in view.questions}
    answer = await interview_uc.answer_question(
        db,
        actor,
        by_focus["personal_contribution"],
        answer=(
            "단계 분리 설계와 재시작 지점 테이블은 제가 직접 설계하고 구현했습니다. "
            "지급 파일 형식 검토는 정산 담당 팀이 맡았습니다."
        ),
        statement="정산 배치의 단계 분리와 재시작 지점 테이블을 직접 설계·구현했다.",
        kind="action",
        contribution="personal",
    )
    claims["interview_personal"] = answer.claim_id
    answer = await interview_uc.answer_question(
        db,
        actor,
        by_focus["metric"],
        answer=(
            "처리 시간은 배치 로그의 시작·종료 시각으로 개편 전후 4주씩 비교했습니다. "
            "문의 건수는 고객센터 분류 기준으로 셌습니다."
        ),
        statement="처리 시간은 배치 로그로 개편 전후 4주씩 비교해 측정했다.",
        kind="result",
        contribution="unknown",  # 확인 대기로 남긴다
    )
    claims["interview_metric"] = answer.claim_id
    return claims


async def _postings(
    db: Database, blobs: BlobStore, actor: ActorContext, now: datetime
) -> tuple[UUID, UUID]:
    """공고 2개: 하나는 항목을 확인해 저장, 하나는 붙여넣은 본문만 있는 확인 대기."""
    deps = PostingDeps(db=db, fetcher=_OfflineFetcher(), gateway=None, browser=None, blobs=blobs)
    ids = []
    for url, text in ((POSTING_A_URL, POSTING_A_TEXT), (POSTING_B_URL, POSTING_B_TEXT)):
        [saved] = await posting_uc.save_urls(db, actor, [url])
        if saved.fetch_run_id is not None:
            # 웹에서 "가져오기 실패 → 본문 붙여넣기"와 같은 순서. 가져오기는 오프라인으로 끝낸다.
            await posting_uc.process_fetch(deps, actor.workspace_id, saved.fetch_run_id)
            await _settle(db, actor.workspace_id, FETCH_JOB, saved.fetch_run_id)
        await posting_uc.add_manual_text(deps, actor, saved.posting_id, text)
        ids.append(saved.posting_id)
    a, b = ids
    deadline = (now + timedelta(days=21)).date().isoformat()
    await posting_uc.confirm_posting(db, actor, a, overrides=posting_a_fields(deadline))
    return a, b


async def _documents(
    db: Database, actor: ActorContext, posting_id: UUID, claims: dict[str, UUID]
) -> tuple[UUID, UUID]:
    """공용 이력서 1개 + 지원 1개(문항 1에 근거 연결 문단, 문항 2는 빈 채로)."""

    def ids(*keys: str) -> list[str]:
        return [str(claims[k]) for k in keys]

    resume = await doc_uc.create_shared_document(db, actor, kind="resume", title="기본 이력서")
    await app_uc.save_revision(
        db,
        actor,
        None,
        resume,
        base_revision=0,
        operations=[
            {
                "op": "insert",
                "text": "가상커머스(가상 회사) 백엔드 개발자. 일일 정산 배치를 단계별로 다시 설계해 "
                "처리 시간을 평균 4시간에서 50분으로 줄였습니다.",
                "claim_ids": ids("settle_action", "settle_result"),
            },
            {
                "op": "insert",
                "text": "상품 검색 API에 pg_trgm 색인을 도입해 응답 시간 p95를 1.8초에서 0.4초로 "
                "줄였습니다.",
                "claim_ids": ids("search_result"),
            },
        ],
        reason="데모 초안",
    )

    application = await app_uc.create_application(db, actor, posting_id)
    view = await app_uc.get_application(db, actor, application)
    first = next(d for d in view.documents if d.question_index == 0)
    await app_uc.save_revision(
        db,
        actor,
        application,
        first.id,
        base_revision=0,
        operations=[
            {
                "op": "insert",
                "text": "가상커머스(가상 회사)에서 하루 12만 건을 처리하던 정산 배치를 세 단계로 "
                "나누고, 완료 지점을 PostgreSQL 테이블에 기록해 실패한 단계부터 다시 시작할 수 있게 "
                "재설계했습니다. 그 결과 처리 시간이 "
                "평균 4시간에서 50분으로 줄었습니다.",
                "claim_ids": ids("settle_action", "settle_result", "interview_personal"),
            },
            {
                "op": "insert",
                "text": "정산 담당 팀과 검증 리포트를 함께 만들어 정산 오류 문의를 월 30건 안팎에서 "
                "5건 안팎으로 줄이는 데 참여했습니다.",
                "claim_ids": ids("settle_team"),
            },
            {
                # 근거 없는 상투 문장: 검사 탭에서 경고가 어떻게 보이는지 보여 준다
                "op": "insert",
                "text": "저는 언제나 열정과 책임감으로 최선을 다하는 개발자입니다.",
            },
        ],
        reason="데모 초안",
    )
    return resume, application


async def _study(
    db: Database,
    actor: ActorContext,
    posting_id: UUID,
    claims: dict[str, UUID],
    now: datetime,
) -> dict[str, int]:
    """공고 요건 → 학습 항목·시험 → 학습 기록·모의 응시·오답 → 복습 일정."""
    # 이 요건은 경험으로 채워졌다 (사람이 직접 연결)
    await study_uc.link_claim(
        db,
        actor,
        posting_id,
        field="requirements",
        text=REQ_BATCH,
        claim_id=claims["settle_result"],
    )
    plan = await study_uc.plan_from_posting(
        db,
        actor,
        posting_id,
        selections=[("preferred", PREF_KAFKA), ("process", PROCESS)],
    )
    [kafka] = plan.items_created
    [coding] = plan.assessments_created  # "코딩테스트" 단계 (면접 단계는 만들지 않는다)
    await study_uc.update_assessment(
        db, actor, coding, scheduled_at=(now + timedelta(days=10)).replace(microsecond=0)
    )
    await study_uc.log_session(
        db,
        actor,
        kafka,
        minutes=50,
        started_at=now - timedelta(days=1),
        notes="파티션, 컨슈머 그룹, 오프셋 커밋 개념 정리. 정산 배치에 쓴다면 재처리를 어떻게 할지 메모.",
    )
    graph = await study_uc.create_item(
        db,
        actor,
        title="코딩테스트 대비: 그래프 탐색 (BFS·DFS)",
        kind="topic",
        assessment_id=coding,
    )
    attempt = await study_uc.record_attempt(
        db,
        actor,
        result="failed",
        assessment_id=coding,
        study_item_id=graph,
        score=2,
        max_score=4,
        taken_at=now - timedelta(days=2),
        minutes=90,
        notes="모의 코딩테스트 (직접 만든 연습 세트). 4문제 중 2문제 정답.",
    )
    await study_uc.add_mistake(
        db,
        actor,
        attempt_id=attempt,
        question="격자 미로에서 출발점부터 도착점까지 최단 거리 구하기",
        wrong_answer="DFS로 모든 경로를 탐색해 시간 초과",
        correct_answer="BFS로 거리 배열을 채우며 탐색",
        reason="concept",
        reason_note="가중치 없는 최단 거리는 BFS라는 점을 놓쳤다.",
    )
    await study_uc.add_mistake(
        db,
        actor,
        attempt_id=attempt,
        question="방문 표시를 큐에 넣을 때 할지 꺼낼 때 할지",
        wrong_answer="꺼낼 때 표시해 같은 칸이 여러 번 들어감",
        correct_answer="넣을 때 표시",
        reason="careless",
    )
    return {"study_items": 2, "assessments": 1, "attempts": 1, "mistakes": 2}


async def seed_demo(
    db: Database,
    blobs: BlobStore,
    *,
    name: str = DEFAULT_NAME,
    display_name: str = DEFAULT_DISPLAY_NAME,
    max_upload_bytes: int = 20 * 1024 * 1024,
    now: datetime | None = None,
) -> DemoResult:
    """새 데모 공간을 만들고 채운다. 다시 부르면 공간이 하나 더 생긴다 (기존 공간은 건드리지 않는다)."""
    name = " ".join((name or "").split()) or DEFAULT_NAME
    now = now or datetime.now(UTC)
    ws, owner = await create_workspace(db, name=name, owner_display_name=display_name)
    token = await issue_token(db, workspace_id=ws, actor_id=owner, label=TOKEN_LABEL)
    # 웹·MCP와 같은 권한으로 쓴다 (토큰 → owner 범위)
    actor = await PersonalTokenResolver(db).resolve(token)
    if actor is None:  # pragma: no cover - 방금 만든 토큰
        raise RuntimeError("demo token did not resolve")

    blocks = await _sources(db, blobs, actor, max_upload_bytes)
    claims = await _brain(db, actor, _Spans(db, actor, blocks))
    saved, _draft = await _postings(db, blobs, actor, now)
    await _documents(db, actor, saved, claims)
    study = await _study(db, actor, saved, claims, now)
    # 시스템 화면의 기록: 이 공간의 자료는 데모 명령이 만들었다 (본문·토큰은 남기지 않는다)
    await DbAuditSink(db).record(
        actor, "demo_seed", {"channel": "cli", "tool": "demo", "status": "ok"}
    )
    pending, _ = await brain_uc.list_pending_claims(db, actor)
    counts = {
        "sources": len(SOURCES),
        "experiences": 3,
        "claims": len(claims),
        "claims_pending": len(pending),
        "interview_answers": 2,
        "postings": 2,
        "postings_saved": 1,
        "applications": 1,
        "shared_documents": 1,
        **study,
    }
    return DemoResult(workspace_id=ws, workspace_name=name, token=token, counts=counts)

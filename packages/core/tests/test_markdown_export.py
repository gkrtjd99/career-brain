"""Markdown으로 내보내기 (plan §21, c0046): 노트 앱에서 여는 읽기 전용 zip.

- 폴더·파일 이름(한국어 폴더, 금지 글자·"..", 긴 이름, NFC, 대소문자만 다른 이름의 안정된 충돌 해결).
- 머리말(YAML, 값은 JSON)과 위키 링크가 모두 있는 파일을 가리킨다.
- 기본은 확인한 사실만, 민감 정보 없음, 원본 파일 없음. 고르면 확인 전·민감 정보·원본 파일이 들어간다.
- 증빙 원본 바이트와 증빙에서 읽은 글은 어떤 옵션에도 없다. 주민등록번호 모양은 가린다.
- 참고 칸은 ``purpose: reference``와 "근거로 쓰지 않습니다".
- 다른 공간 0건, 소유자만(편집자 403), 한도를 넘으면 413, ``export_runs``에 ``markdown`` 한 줄(백업으로 세지 않음).

모든 자료는 합성이다.
"""

import io
import json
import random
import re
import unicodedata
import zipfile
from datetime import date
from uuid import uuid4

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import DocumentBlock, JobPosting, JobPostingVersion
from career_core.db.models import CompanyFact, ExportRun, Source
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import parsing_handlers, run_until_idle
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext
from career_core.usecases import answers as answers_uc
from career_core.usecases import applications as app_uc
from career_core.usecases import brain
from career_core.usecases import companies as company_uc
from career_core.usecases import credentials as cred_uc
from career_core.usecases import education as education_uc
from career_core.usecases import postings as posting_uc
from career_core.usecases import profiles as profile_uc
from career_core.usecases import references as ref_uc
from career_core.usecases.export import ExportError
from career_core.usecases.maintenance import maintenance_info
from career_core.usecases.markdown_export import (
    MAX_NAME_BYTES,
    MarkdownOptions,
    assign_names,
    export_markdown,
    frontmatter,
    mask_resident_numbers,
    safe_name,
)
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.sources import upload_source
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import select, update

pytestmark = pytest.mark.anyio

PROOF_TEXT = "합성 졸업증명서 PROOFSECRET 성명 김합성 900101-1234567"
PROOF_BYTES = PROOF_TEXT.encode()
EXPERIENCE_TEXT = "주문 API 지연을 800ms에서 200ms로 줄였다"
RRN_TEXT = "담당자 확인번호 850505-2345678 로 결제 정산 배치를 만들었다"
REFERENCE_TEXT = "다른 사람의 합성 자기소개서 REFTEXT 본문입니다."
WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


# ---------------------------------------------------------------- 이름 (순수)


def test_safe_name_sanitizes_for_every_os():
    assert safe_name("A/B 테스트: 1차") == "A-B 테스트- 1차"
    assert safe_name("..") == "제목 없음"
    assert safe_name("../../etc/passwd") == "-..-etc-passwd"
    assert "/" not in safe_name("a/../b") and "\\" not in safe_name("a\\b")
    assert safe_name('그 "회사" <설명>?*|') == "그 -회사- -설명----"
    assert safe_name("링크 [[깨기]] #태그 ^블록") == "링크 --깨기-- -태그 -블록"
    assert safe_name("  끝에 점과 공백 . . ") == "끝에 점과 공백"
    assert safe_name(".숨김") == "숨김"
    assert safe_name("줄\n바꿈\t탭\x00널") == "줄 바꿈 탭 널"
    assert safe_name("CON") == "_CON" and safe_name("nul.txt") == "_nul.txt"
    assert safe_name("COM1 보고서") == "COM1 보고서"  # 예약 이름 자체가 아니면 그대로
    assert safe_name("") == "제목 없음" and safe_name(None) == "제목 없음"
    long = "아주긴제목" * 100
    cut = safe_name(long)
    assert len(cut.encode()) <= MAX_NAME_BYTES and long.startswith(cut)
    # 글자 중간에서 자르지 않는다 (UTF-8로 다시 읽힌다)
    assert cut.encode().decode() == cut
    nfd = unicodedata.normalize("NFD", "한글 경험")
    assert nfd != "한글 경험" and safe_name(nfd) == "한글 경험"
    zero_width = "보이지" + chr(0x200B) + "않는" + chr(0x202E) + "글자"
    assert safe_name(zero_width) == "보이지 않는 글자"


def test_assign_names_is_stable_and_case_insensitive():
    items = [
        ("b", "Alpha", "2026-01-02|b"),
        ("a", "alpha", "2026-01-01|a"),
        ("c", "ALPHA", "2026-01-03|c"),
        ("d", "Alpha (2)", "2026-01-04|d"),
        ("e", "다른 이름", "2026-01-05|e"),
        ("f", unicodedata.normalize("NFD", "다른 이름"), "2026-01-06|f"),
    ]
    first = assign_names(items)
    for _ in range(5):
        random.shuffle(items)
        assert assign_names(items) == first
    lowered = [v.casefold() for v in first.values()]
    assert len(set(lowered)) == len(lowered)
    assert first["e"] == "다른 이름" and first["f"] == "다른 이름 (2)"
    # 대소문자만 다르면 같은 이름: (이름, 정렬 키) 순서로 앞의 것이 그대로, 뒤의 것이 (2)·(3)
    # 원래 "Alpha (2)"인 항목은 이미 쓰인 이름을 피해 한 번 더 번호를 받는다
    assert (first["c"], first["b"], first["a"], first["d"]) == (
        "ALPHA",
        "Alpha (2)",
        "alpha (3)",
        "Alpha (2) (2)",
    )


def test_frontmatter_values_are_json_and_rrn_masked():
    text = frontmatter(
        [
            ("kind", "경력"),
            ("org", 'a: "b" #c'),
            ("tags", ["Python", "C#"]),
            ("empty", None),
            ("none", []),
            ("note", "번호 900101-1234567"),
        ]
    )
    lines = text.strip().split("\n")
    assert lines[0] == lines[-1] == "---"
    parsed = {k: json.loads(v) for k, v in (line.split(": ", 1) for line in lines[1:-1])}
    assert parsed == {
        "kind": "경력",
        "org": 'a: "b" #c',
        "tags": ["Python", "C#"],
        "note": "번호 900101-1******",
    }
    assert mask_resident_numbers("x 900101 1234567 y") == "x 900101-1****** y"


# ---------------------------------------------------------------- 합성 공간


class FakeResolver:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


async def saved_posting(db, actor, *, company, title, url, questions=()):
    async with db.workspace_session(actor.workspace_id) as s:
        p = JobPosting(
            id=uuid4(),
            workspace_id=actor.workspace_id,
            canonical_url=url,
            source_url=url,
            status="draft",
            created_by=actor.subject_id,
        )
        s.add(p)
        await s.flush()
        s.add(
            JobPostingVersion(
                id=uuid4(),
                workspace_id=actor.workspace_id,
                posting_id=p.id,
                version_no=1,
                final_url=url,
                method="manual_text",
                adapter="text",
                content_hash=uuid4().hex,
                text="합성 공고 본문",
                extracted={},
                missing=[],
                changed_fields=[],
                created_by=actor.subject_id,
            )
        )
    await posting_uc.confirm_posting(
        db,
        actor,
        p.id,
        overrides={
            "company": company,
            "title": title,
            "deadline": "2026-12-31",
            "requirements": ["Python 3년 이상"],
            "questions": list(questions),
        },
    )
    return p.id


async def put_source(db, blobs, actor, text, *, title, purpose="experience"):
    return await upload_source(
        db,
        blobs,
        actor,
        filename=f"{title}.txt",
        data=text.encode(),
        declared_mime="text/plain",
        max_bytes=1 << 20,
        title=title,
        purpose=purpose,
    )


async def span_for(db, actor, text):
    async with db.workspace_session(actor.workspace_id) as s:
        block = await s.scalar(select(DocumentBlock).where(DocumentBlock.text == text))
    return await brain.create_span(db, actor, block.id, 0, len(text))


@pytest.fixture
async def env(db, tmp_path):
    ws_a, a = await create_workspace(db, name="A (합성)", owner_display_name="amy")
    ws_b, b = await create_workspace(db, name="B (합성)", owner_display_name="ben")
    owner = ActorContext(a, ws_a, scopes_for_role(Role.OWNER))
    editor = ActorContext(a, ws_a, scopes_for_role(Role.EDITOR))
    other = ActorContext(b, ws_b, scopes_for_role(Role.OWNER))
    blobs = LocalBlobStore(tmp_path / "blobs")

    # 원본: 경험 자료 2개, 나중에 증빙으로 바뀐 자료 1개, 증빙 1개
    await put_source(db, blobs, owner, EXPERIENCE_TEXT, title="합성 경력 기술서")
    await put_source(db, blobs, owner, RRN_TEXT, title="정산 메모")
    later_proof = await put_source(
        db, blobs, owner, "나중에 증빙이 된 LATERPROOF 글", title="바뀐 자료"
    )
    await put_source(db, blobs, owner, PROOF_TEXT, title="합성 졸업증명서", purpose="proof")
    deps = ParsingDeps(
        db=db, blobs=blobs, egress=ProviderEgressPolicy(), local_parsers=default_local_parsers()
    )
    await run_until_idle(db, parsing_handlers(deps))

    parent = await brain.create_experience(
        db,
        owner,
        title="가상커머스 백엔드",
        organization="가상커머스",
        role="백엔드 개발자",
        kind="work",
        started_on="2021-03",
        ended_on="2023-02",
        tags=["Python", "FastAPI"],
    )
    child = await brain.create_experience(
        db,
        owner,
        title="A/B 테스트: 주문..개선",
        kind="project",
        parent_id=parent,
        started_on="2022-01",
        ongoing=False,
    )
    long_title = "아주 긴 경험 제목 " * 30
    await brain.create_experience(db, owner, title=long_title, kind="other")
    await brain.create_experience(db, owner, title="Alpha", kind="activity")
    await brain.create_experience(db, owner, title="alpha", kind="activity")
    await brain.create_experience(
        db, owner, title=unicodedata.normalize("NFD", "한글 이름"), kind="other"
    )

    confirmed = await brain.add_claim(
        db,
        owner,
        child,
        statement=EXPERIENCE_TEXT,
        kind="result",
        contribution="personal",
        span_ids=[await span_for(db, owner, EXPERIENCE_TEXT)],
    )
    rrn_claim = await brain.add_claim(
        db,
        owner,
        parent,
        statement="결제 정산 배치를 만들었다",
        kind="action",
        contribution="personal",
        span_ids=[await span_for(db, owner, RRN_TEXT)],
    )
    later_text = "나중에 증빙이 된 LATERPROOF 글"
    withheld = await brain.add_claim(
        db,
        owner,
        parent,
        statement="정산 오류를 줄였다",
        kind="result",
        contribution="team",
        span_ids=[await span_for(db, owner, later_text)],
    )
    self_only = await brain.add_claim(
        db,
        owner,
        parent,
        statement="Kubernetes 클러스터 운영",
        kind="skill",
        contribution="personal",
    )
    # 편집자가 쓴 주장은 확인 전(extracted)
    pending = await brain.add_claim(
        db, editor, parent, statement="CANDIDATE 후보 사실", kind="other", contribution="personal"
    )
    # 근거를 단 뒤 원본이 증빙으로 바뀌었다
    async with db.workspace_session(ws_a) as s:
        await s.execute(
            update(Source)
            .where(Source.id == later_proof.source_id)
            .values(purpose="proof", data_class="restricted")
        )

    await profile_uc.save_profile(
        db,
        owner,
        name="김합성",
        email="synthetic@example.test",
        phone="010-0000-0000",
        birth_date=date(1990, 1, 1),
        address="가상시 합성구 BIRTHADDR",
        links=[("GitHub", "https://example.test/synthetic")],
        headline="합성 백엔드 개발자",
    )
    await profile_uc.update_profile_details(
        db,
        owner,
        {
            "military_status": "served",
            "military_branch": "MILBRANCH",
            "desired_roles": ["DESIREDROLE"],
            "desired_salary": "SALARYWISH",
        },
    )
    await education_uc.create_education(
        db,
        owner,
        {
            "school": "가상대학교",
            "major": "컴퓨터공학",
            "degree": "bachelor",
            "status": "graduated",
            "entered_on": "2015-03",
            "graduated_on": "2021-02",
            "experience_id": parent,
        },
    )
    await cred_uc.create_credential(
        db,
        owner,
        {
            "kind": "license",
            "name": "정보처리기사",
            "number": "LIC-12345678",
            "acquired_on": date(2022, 6, 10),
        },
    )

    posting = await saved_posting(
        db,
        owner,
        company="(주)가상회사",
        title="백엔드 개발자",
        url="https://jobs.example.test/1",
        questions=[{"prompt": "지원 동기를 작성해 주세요.", "char_limit": 500}],
    )
    app_id = await app_uc.create_application(db, owner, posting)
    doc_id = (await app_uc.get_application(db, owner, app_id)).documents[0].id
    await app_uc.save_revision(
        db,
        owner,
        app_id,
        doc_id,
        base_revision=0,
        operations=[{"op": "insert", "text": "SUBMITTED 지원 동기 글입니다."}],
    )
    await app_uc.submit_application(db, owner, app_id, revisions={doc_id: 1})
    base_doc = await app_uc.add_document(db, owner, None, kind="resume", title="기본 이력서")
    await app_uc.save_revision(
        db,
        owner,
        None,
        base_doc,
        base_revision=0,
        operations=[{"op": "insert", "text": "BASEDOC 기본 문서 글"}],
    )
    await answers_uc.create_answer(
        db,
        owner,
        kind="intro",
        title="1분 자기소개",
        body="ANSWERBODY 안녕하세요.",
        claim_ids=[confirmed],
    )

    company = await company_uc.find_or_create_company(db, owner, name="가상전자", aliases=["가상"])
    page = await company_uc.import_paste(
        db,
        None,
        owner,
        company,
        "가상전자의 인재상은 도전하는 사람입니다. 2024년 매출액 1,200억 원.",
    )
    fact = await company_uc.add_fact(
        db,
        owner,
        company,
        page_id=page.id,
        kind="talent",
        body="도전하는 사람",
        quote="인재상은 도전하는 사람입니다",
    )
    async with db.workspace_session(ws_a) as s:
        row = await s.get(CompanyFact, fact)
        s.add(
            CompanyFact(
                id=uuid4(),
                workspace_id=ws_a,
                company_id=company,
                page_id=page.id,
                kind="culture",
                body="FACTCANDIDATE 도전 문화",
                quote=row.quote,
                quote_start=row.quote_start,
                quote_end=row.quote_end,
                fetched_at=row.fetched_at,
                status="candidate",
                origin="model",
                created_by=owner.subject_id,
            )
        )
    await ref_uc.create_reference(
        db,
        None,
        owner,
        kind="accepted_essay",
        title="합격 자소서: 가상전자/2024",
        text=REFERENCE_TEXT,
        company_id=company,
        year=2024,
    )

    # 다른 공간
    b_exp = await brain.create_experience(db, other, title="FOREIGN 다른 공간 경험")
    await brain.add_claim(
        db, other, b_exp, statement="FOREIGN 주장", kind="other", contribution="personal"
    )

    tokens = {"owner": owner, "editor": editor, "other": other}
    app = FastAPI()
    app.include_router(build_router(CoreDeps(identity=FakeResolver(tokens), db=db, blobs=blobs)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        yield {
            "c": c,
            "owner": owner,
            "editor": editor,
            "other": other,
            "blobs": blobs,
            "claims": {
                "confirmed": confirmed,
                "rrn": rrn_claim,
                "withheld": withheld,
                "self": self_only,
                "pending": pending,
            },
            "long_title": long_title,
        }


def read_zip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.testzip() is None
        return {i.filename: zf.read(i) for i in zf.infolist()}


def text_of(files: dict[str, bytes]) -> str:
    return "\n".join(v.decode("utf-8", "replace") for v in files.values())


def head_of(md: str) -> dict:
    assert md.startswith("---\n"), md[:80]
    body = md.split("---\n", 2)[1]
    return {k: json.loads(v) for k, v in (line.split(": ", 1) for line in body.strip().split("\n"))}


def check_links(files: dict[str, bytes]) -> int:
    names = set(files)
    n = 0
    for path, data in files.items():
        if not path.endswith(".md"):
            continue
        for target in WIKILINK.findall(data.decode()):
            assert target + ".md" in names or target in names, (path, target)
            n += 1
    return n


async def export(db, env, tmp_path, **opts):
    out = tmp_path / f"notes-{uuid4().hex[:6]}.zip"
    report = await export_markdown(
        db, env["owner"], out, blobs=env["blobs"], options=MarkdownOptions(**opts)
    )
    return report, read_zip(out.read_bytes())


# ---------------------------------------------------------------- 내용


async def test_default_export_layout_and_rules(db, env, tmp_path):
    report, files = await export(db, env, tmp_path)
    names = set(files)
    for path in (
        "README.md",
        "나/프로필.md",
        "나/학력.md",
        "나/자격·어학·수상.md",
        "나/기술.md",
        "경험/가상커머스 백엔드.md",
        "경험/A-B 테스트- 주문..개선.md",
        "내 글/기본 문서/기본 이력서.md",
        "내 글/공통 답변/1분 자기소개.md",
        "참고/회사/가상전자.md",
        "참고/참고 글/합격 자소서- 가상전자-2024.md",
        "자료/목록.md",
    ):
        assert path in names, (path, sorted(names))
    assert any(p.startswith("지원/") for p in names)
    assert any(p.startswith("내 글/지난 제출본/") for p in names)
    # 이름: NFC, 대소문자만 다른 이름은 겹치지 않음, 긴 이름은 잘림, 모든 칸이 안전
    assert "경험/한글 이름.md" in names
    assert {"경험/Alpha.md", "경험/alpha (2).md"} <= names
    assert len({p.casefold() for p in names}) == len(names)
    for p in names:
        assert unicodedata.normalize("NFC", p) == p
        for part in p.split("/"):
            assert part not in ("", ".", "..") and not part.endswith((" ", "."))
            assert len(part.encode()) <= MAX_NAME_BYTES + len(" (99).md")
            assert not re.search(r'[<>:"\\|?*]', part)
    long_name = next(p for p in names if p.startswith("경험/아주 긴 경험 제목"))
    assert len(long_name.encode()) < len(("경험/" + env["long_title"]).encode())

    readme = files["README.md"].decode()
    assert "이 폴더는 다시 가져올 수 없습니다. 백업은 연결·관리의 백업을 쓰세요." in readme
    assert "경험 6개" in readme and "확인한 사실 4개" in readme

    # 경험: 머리말, 부모 링크, 확인한 사실과 근거, 확인 전은 기본에 없음
    child = files["경험/A-B 테스트- 주문..개선.md"].decode()
    head = head_of(child)
    assert head["kind"] == "프로젝트" and head["period"] == "2022-01"
    assert head["parent"] == "[[경험/가상커머스 백엔드]]"
    assert EXPERIENCE_TEXT in child and "합성 경력 기술서" in child and "근거: “" in child
    parent = files["경험/가상커머스 백엔드.md"].decode()
    head = head_of(parent)
    assert head == {
        "kind": "경력",
        "org": "가상커머스",
        "role": "백엔드 개발자",
        "period": "2021-03 – 2023-02",
        "tags": ["Python", "FastAPI"],
    }
    assert "[[경험/A-B 테스트- 주문..개선|A/B 테스트: 주문..개선]]" in parent
    assert "Kubernetes 클러스터 운영 · 기술 · 본인" in parent
    assert "850505-2******" in parent and "2345678" not in text_of(files)
    # 원본이 증빙으로 바뀐 근거: 제목만, 인용 없음
    assert "바뀐 자료 (증빙 자료라 인용은 넣지 않았습니다)" in parent
    assert "LATERPROOF" not in text_of(files)
    assert "CANDIDATE" not in text_of(files) and "## 확인 전" not in parent
    assert "FACTCANDIDATE" not in text_of(files)

    # 나: 민감 정보 없음, 자격 번호는 끝 4자리
    profile = files["나/프로필.md"].decode()
    assert "김합성" in profile and "https://example.test/synthetic" in profile
    for secret in ("BIRTHADDR", "1990-01-01", "MILBRANCH", "DESIREDROLE", "SALARYWISH"):
        assert secret not in text_of(files), secret
    creds = files["나/자격·어학·수상.md"].decode()
    assert "정보처리기사" in creds and "LIC-12345678" not in creds and "••••5678" in creds
    assert "[[경험/가상커머스 백엔드]]" in files["나/학력.md"].decode()
    assert "Python" in files["나/기술.md"].decode()

    # 내 글·지원: 저장한 글 그대로, 링크가 이어진다
    sub_path = next(p for p in names if p.startswith("내 글/지난 제출본/"))
    sub = files[sub_path].decode()
    assert "SUBMITTED 지원 동기 글입니다." in sub and head_of(sub)["company"] == "(주)가상회사"
    app_path = next(p for p in names if p.startswith("지원/"))
    app = files[app_path].decode()
    head = head_of(app)
    assert head["company"] == "(주)가상회사" and head["deadline"] == "2026-12-31"
    assert head["link"] == "https://jobs.example.test/1" and head["status"] == "제출함"
    assert "SUBMITTED 지원 동기 글입니다." in app and "Python 3년 이상" in app
    assert f"[[{sub_path[:-3]}]]" in app
    assert "BASEDOC 기본 문서 글" in files["내 글/기본 문서/기본 이력서.md"].decode()
    answer = files["내 글/공통 답변/1분 자기소개.md"].decode()
    assert "ANSWERBODY" in answer and "[[경험/A-B 테스트- 주문..개선|" in answer

    # 참고: 표시와 줄
    for path in ("참고/회사/가상전자.md", "참고/참고 글/합격 자소서- 가상전자-2024.md"):
        md = files[path].decode()
        assert head_of(md)["purpose"] == "reference"
        assert "근거로 쓰지 않습니다" in md
    company = files["참고/회사/가상전자.md"].decode()
    assert "도전하는 사람" in company and head_of(company)["aliases"] == ["가상"]
    ref = files["참고/참고 글/합격 자소서- 가상전자-2024.md"].decode()
    assert REFERENCE_TEXT in ref and head_of(ref)["company"] == "[[참고/회사/가상전자]]"

    # 자료: 목록에 증빙 제목은 있고 바이트·글은 없다. 원본 폴더 없음
    listing = files["자료/목록.md"].decode()
    assert "합성 졸업증명서 · 증빙 서류" in listing and "합성 경력 기술서 · 내 경험 자료" in listing
    assert not any(p.startswith("자료/원본/") for p in names)
    everything = b"\n".join(files.values())
    assert b"PROOFSECRET" not in everything and PROOF_BYTES not in everything
    assert "1234567" not in text_of(files)

    # 다른 공간 0건, 링크는 모두 있는 파일
    assert "FOREIGN" not in text_of(files)
    assert check_links(files) >= 8
    assert report.notes == sum(p.endswith(".md") for p in names) and report.files == 0


async def test_options_add_candidates_sensitive_and_files_but_never_proofs(db, env, tmp_path):
    report, files = await export(db, env, tmp_path, sensitive=True, candidates=True, files=True)
    everything = text_of(files)
    parent = files["경험/가상커머스 백엔드.md"].decode()
    assert "## 확인 전" in parent and "CANDIDATE 후보 사실" in parent
    assert parent.index("## 확인 전") < parent.index("CANDIDATE")
    company = files["참고/회사/가상전자.md"].decode()
    assert "FACTCANDIDATE" in company and company.index("## 확인 전") < company.index(
        "FACTCANDIDATE"
    )
    profile = files["나/프로필.md"].decode()
    for value in ("BIRTHADDR", "1990-01-01", "MILBRANCH", "DESIREDROLE", "SALARYWISH", "군필"):
        assert value in profile, value
    assert "LIC-12345678" in files["나/자격·어학·수상.md"].decode()

    originals = {p: v for p, v in files.items() if p.startswith("자료/원본/")}
    assert set(originals) == {"자료/원본/합성 경력 기술서.txt", "자료/원본/정산 메모.txt"}
    assert originals["자료/원본/합성 경력 기술서.txt"] == EXPERIENCE_TEXT.encode()
    assert report.files == 2
    # 증빙(처음부터·나중에 바뀐 것)의 바이트·글은 어떤 옵션에도 없다
    raw = b"\n".join(files.values())
    assert b"PROOFSECRET" not in raw and b"LATERPROOF" not in raw
    assert "LATERPROOF" not in everything
    # 원본 파일은 사용자가 올린 바이트 그대로다 (가리는 것은 노트 글뿐)
    assert originals["자료/원본/정산 메모.txt"] == RRN_TEXT.encode()
    assert check_links(files) >= 10
    assert "FOREIGN" not in everything


async def test_export_is_deterministic_and_recorded_but_not_a_backup(db, env, tmp_path):
    _, first = await export(db, env, tmp_path)
    _, second = await export(db, env, tmp_path)
    assert sorted(first) == sorted(second)
    owner = env["owner"]
    async with db.workspace_session(owner.workspace_id) as s:
        runs = (await s.execute(select(ExportRun))).scalars().all()
    assert [r.kind for r in runs] == ["markdown", "markdown"]
    assert all(r.rows_total == sum(p.endswith(".md") for p in first) for r in runs)
    info = await maintenance_info(db, owner.workspace_id, env["blobs"])
    assert info.last_export is None  # Markdown 묶음은 백업이 아니다


async def test_permissions_isolation_and_size_cap(db, env, tmp_path, monkeypatch):
    c = env["c"]
    res = await c.get("/core/maintenance/export/markdown", headers=auth("editor"))
    assert res.status_code == 403
    with pytest.raises(ExportError) as exc:
        await export_markdown(db, env["editor"], tmp_path / "x.zip")
    assert exc.value.code == "forbidden" and not (tmp_path / "x.zip").exists()
    with pytest.raises(ExportError) as exc:
        await export_markdown(
            db, env["owner"], tmp_path / "y.zip", options=MarkdownOptions(files=True)
        )
    assert exc.value.code == "files_unavailable"

    res = await c.get(
        "/core/maintenance/export/markdown",
        params={"candidates": "true"},
        headers=auth("owner"),
    )
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/zip"
    assert "career-notes-" in res.headers["content-disposition"]
    assert res.headers["cache-control"] == "no-store" and len(res.headers["x-archive-sha256"]) == 64
    files = read_zip(res.content)
    assert "CANDIDATE 후보 사실" in files["경험/가상커머스 백엔드.md"].decode()

    # 다른 공간 소유자는 자기 공간만 받는다
    res = await c.get("/core/maintenance/export/markdown", headers=auth("other"))
    theirs = read_zip(res.content)
    assert "FOREIGN 다른 공간 경험" in text_of(theirs)
    assert not any("가상커머스" in p for p in theirs) and "김합성" not in text_of(theirs)

    monkeypatch.setenv("CAREER_MARKDOWN_EXPORT_MAX_BYTES", "2000")
    res = await c.get("/core/maintenance/export/markdown", headers=auth("owner"))
    assert (res.status_code, res.json()["detail"]) == (413, "too_large")
    with pytest.raises(ExportError) as exc:
        await export_markdown(db, env["owner"], tmp_path / "big.zip", max_bytes=2000)
    assert exc.value.code == "too_large"
    assert list(tmp_path.glob("*big.zip*")) == []  # 반쯤 쓴 파일을 남기지 않는다

"""2026-11-18 사용 점검에서 나온 문제들: 여러 원본의 같은 경험, Chrome 한국어 PDF, 경험이 아닌 줄, 합치기·지우기,
한 번에 확인, 약한 인용, 이력서 채우기·내보내기, 공고 문항 글. 모든 자료는 합성이다."""

import json
from datetime import UTC, date, datetime

import httpx
import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import PdfTextParser, default_local_parsers, rejoin_lines
from career_core.db import Claim, DocumentBlock, Experience
from career_core.db.models import Credential
from career_core.domain.brain import ClaimRuleError, ClaimStatus
from career_core.domain.documents import (
    CheckContext,
    EvidenceClaim,
    key_terms,
    requirement_visibility,
)
from career_core.domain.experience_match import (
    MAYBE,
    SAME,
    ExperienceKey,
    best_match,
    norm_org,
    quote_support,
    same_statement,
)
from career_core.domain.experiences import normalize_tags
from career_core.domain.postings import clean_list_item, clean_question
from career_core.domain.resume import (
    RenderDoc,
    RenderItem,
    RenderProfile,
    RenderSection,
    estimate_pages,
    render_item,
)
from career_core.domain.resume_export import render_resume, resume_filename
from career_core.domain.text_clean import clean_control, clean_inline
from career_core.domain.workspace import Role, scopes_for_role
from career_core.interfaces.http import CoreDeps, build_router
from career_core.jobs.worker import extraction_handlers, parsing_handlers, run_until_idle
from career_core.llm import ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext
from career_core.usecases import brain, experience_merge, resumes
from career_core.usecases.extraction import (
    ExtractionDeps,
    find_quote,
    get_extraction,
    list_suggestions,
    request_extraction,
)
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.sources import upload_source
from career_core.usecases.workspaces import create_workspace
from fastapi import FastAPI
from sqlalchemy import select

pytestmark = pytest.mark.anyio

CONFIG = parse_models_config("""
[endpoints.proxy]
preset = "cliproxyapi"
price_as = "openai"
data_classes = ["public", "private"]

[routes]
default = "proxy/gpt-6.1-sol"
""")
ENV = {"CLIPROXY_API_KEY": "k"}


class ScriptedClient:
    """정해진 응답을 차례로 돌려주는 모델 (실제 네트워크 없음)."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.calls = []

    async def complete(self, model, request):
        self.calls.append(request)
        text = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return ModelCompletion(
            text=text,
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, TokenUsage(input_tokens=500, output_tokens=200)),),
            request_id="req",
        )


def gateway(db, client):
    return ModelGateway(
        db, CONFIG, PriceBook.load(), client_factory=lambda ep, key: client, env=ENV
    )


@pytest.fixture
async def people(db):
    ws_a, a = await create_workspace(db, name="A", owner_display_name="alice")
    ws_b, b = await create_workspace(db, name="B", owner_display_name="bob")
    return {
        "owner": ActorContext(a, ws_a, scopes_for_role(Role.OWNER)),
        "editor": ActorContext(a, ws_a, scopes_for_role(Role.EDITOR)),
        "other": ActorContext(b, ws_b, scopes_for_role(Role.OWNER)),
    }


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blobs")


async def upload(db, blobs, actor, text, filename):
    up = await upload_source(
        db,
        blobs,
        actor,
        filename=filename,
        data=text.encode() if isinstance(text, str) else text,
        declared_mime="application/pdf" if filename.endswith(".pdf") else "text/plain",
        max_bytes=1 << 20,
        data_class="private",
    )
    deps = ParsingDeps(
        db=db, blobs=blobs, egress=ProviderEgressPolicy(), local_parsers=default_local_parsers()
    )
    await run_until_idle(db, parsing_handlers(deps))
    return up


def reply(**output):
    return "```json\n" + json.dumps(output, ensure_ascii=False) + "\n```"


async def extract(db, actor, parse_run_id, client):
    ext_id = await request_extraction(db, actor, parse_run_id)
    await run_until_idle(db, extraction_handlers(ExtractionDeps(db, gateway(db, client))))
    return await get_extraction(db, actor, ext_id)


async def rows(db, actor, model, *where):
    async with db.workspace_session(actor.workspace_id) as s:
        return (await s.execute(select(model).where(*where))).scalars().all()


# --- 합성 PDF: Chrome 인쇄처럼 띄어쓰기를 U+0001로 읽는 글꼴 ------------------------------------


def _hex(text: str) -> str:
    return "".join(f"{ord(c):04X}" for c in text)


def chrome_like_pdf(lines: list[str], *, space: str = "\x01") -> bytes:
    """1바이트 글꼴(Helvetica)에 ToUnicode 표를 붙여 글자마다 코드 하나를 주고 공백(0x20)은 ``space``로 적는다.
    pypdf는 Chrome(Skia) PDF처럼 띄어쓰기를 ``\\x01``로 읽는다. 줄은 ``T*``로 내린다 (꺾인 줄)."""
    chars = sorted({c for line in lines for c in line if c != " "})
    codes: dict[str, int] = {}
    nxt = 0x21
    for c in chars:
        codes[c] = nxt
        nxt = 0xA1 if nxt + 1 == 0x7F else nxt + 1
    assert nxt <= 0xFF, "too many distinct characters"
    cmap_lines = [f"<20> <{_hex(space)}>"] + [f"<{v:02X}> <{_hex(c)}>" for c, v in codes.items()]
    body = "\n".join(cmap_lines)
    cmap = (
        "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
        "1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
        f"{len(cmap_lines)} beginbfchar\n{body}\nendbfchar\n"
        "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
    ).encode()
    ops = ["BT", "/F1 11 Tf", "50 780 Td", "14 TL"]
    for line in lines:
        raw = "".join(f"{(0x20 if c == ' ' else codes[c]):02X}" for c in line)
        ops.append(f"<{raw}> Tj T*")
    ops.append("ET")
    content = "\n".join(ops).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 6 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Length %d >>\nstream\n" % len(cmap) + cmap + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


PDF_LINES = [
    "누리데이터(가상) 백엔드 엔지니어 2021.03 - 2024.08",
    "주문 정산 배치를 Python으로 다시 만들어 하루 정산 시간을 6시간에서 40분으",
    "로 줄였다.",
    "장애 대응 절차를 만들어 평균 복구 시간을 35분에서 12분으로 낮췄다.",
    "- Kafka 소비자 지연 경보",
]


def test_chrome_like_pdf_reproduces_control_spaces():
    import io

    from pypdf import PdfReader

    raw = PdfReader(io.BytesIO(chrome_like_pdf(PDF_LINES))).pages[0].extract_text()
    assert "\x01" in raw  # 고치기 전 모양: 띄어쓰기가 제어 문자로 읽힌다


def test_pdf_parser_cleans_controls_and_rejoins_broken_lines():
    result = PdfTextParser().parse(chrome_like_pdf(PDF_LINES))
    texts = [b.text for b in result.blocks]
    assert all(clean_control(t) == t and "\t" not in t for t in texts)
    assert len(texts) >= 3  # 쪽 하나가 블록 하나가 아니다
    joined = "\n".join(texts)
    assert "40분으로 줄였다." in joined  # 낱말 중간에서 꺾인 줄을 다시 붙였다
    assert "\n로 줄였다" not in joined
    assert any(t.startswith("- Kafka") for t in texts)  # 글머리표 줄은 앞 줄에 붙이지 않는다
    assert {b.page for b in result.blocks} == {1}


def test_rejoin_lines_keeps_sentences_and_headings_apart():
    text = (
        "경력\n"
        "누리데이터에서 주문 정산 배치를 다시 만들어 하루 정산 시간을 6시간에서 40분으\n"
        "로 줄였고 실패 재시도를 자동화했다.\n"
        "1. 다음 항목\n"
    )
    out = rejoin_lines(text)
    assert out[0] == "경력"
    assert "40분으로 줄였고" in out[1]
    assert out[-1] == "1. 다음 항목"


def test_clean_inline_and_find_quote_whitespace_fallback():
    assert clean_inline("주문\x01정산\t배치 ") == "주문 정산 배치"
    block = "하루 정산 시간을 6시간에서 40분으로 줄였다."
    # 모델이 붙여 이은 줄에 띄어쓰기를 더 넣거나 빼도 원문 위치를 찾는다
    start, end = find_quote(block, "40분으 로 줄였다")
    assert block[start:end] == "40분으로 줄였다"


async def test_parse_pipeline_stores_no_control_chars(db, blobs, people):
    owner = people["owner"]
    up = await upload(
        db, blobs, owner, "주문\x01정산\x02배치\x7f를 만들었다.\n\n둘째 문단", "a.txt"
    )
    blocks = await rows(db, owner, DocumentBlock, DocumentBlock.parse_run_id == up.parse_run_id)
    assert blocks and all(clean_control(b.text) == b.text for b in blocks)
    pdf = await upload(db, blobs, owner, chrome_like_pdf(PDF_LINES), "cv.pdf")
    blocks = await rows(db, owner, DocumentBlock, DocumentBlock.parse_run_id == pdf.parse_run_id)
    assert len(blocks) >= 3
    assert all("\x01" not in b.text for b in blocks)


# --- 같은 경험 맞추기 (규칙) --------------------------------------------------------------


def key(title, org=None, role=None, start=None, end=None, ongoing=False, id_=None):
    return ExperienceKey(id_, title, org, role, start, end, ongoing)


def test_experience_match_rules():
    assert norm_org("누리데이터 주식회사") == norm_org("(주)누리데이터") == norm_org("누리데이터㈜")
    base = key(
        "주문 정산 배치 재구축", "누리데이터(가상)", "백엔드", date(2021, 3, 1), date(2024, 8, 1)
    )
    same = key(
        "주문 정산 배치 재구축", "누리데이터 주식회사", None, date(2022, 1, 1), date(2022, 6, 1)
    )
    assert best_match(same, [base])[0] == SAME
    later = key("주문 정산 배치 재구축", "누리데이터", None, date(2025, 1, 1), date(2025, 6, 1))
    assert best_match(later, [base])[0] != SAME  # 기간이 겹치지 않으면 같은 경험이 아니다
    no_org = key("주문 정산 배치 재구축")
    assert best_match(no_org, [base])[0] == MAYBE  # 조직을 모르면 확실하지 않다
    other = key("모바일 앱 출시", "하늘페이(가상)")
    assert best_match(other, [base]) == (None, None)
    assert same_statement(
        "하루 정산 시간을 6시간에서 40분으로 줄였다.",
        "하루 정산 시간을 6시간에서 40분으로 줄였습니다",
    )
    assert not same_statement("정산 시간을 6시간에서 40분으로", "정산 시간을 6시간에서 30분으로")


def test_quote_support_flags_weak_quotes():
    statement = "하루 정산 시간을 6시간에서 40분으로 줄였다."
    assert quote_support(statement, "하루 정산 시간을 6시간에서 40분으로 단축") == "ok"
    assert quote_support(statement, "누리데이터(가상) 백엔드 엔지니어") == "weak"  # 숫자가 없다
    assert quote_support("Kafka 소비자 지연 경보를 만들었다", "- Kafka 소비자 지연 경보") == "ok"


def test_normalize_tags_keeps_only_skills():
    tags = normalize_tags(
        [
            "Python",
            "컴퓨터공학",
            "정보처리기사",
            "주문 정산 배치 재구축",
            "PostgreSQL",
            "한빛대학교",
        ],
        skills_only=True,
        titles=["주문 정산 배치 재구축"],
    )
    assert tags == ["Python", "PostgreSQL"]


# --- 여러 원본 추출: 같은 경험에 붙이기, 학력·자격·기술 줄 나누기 ------------------------------------

RESUME = (
    "누리데이터(가상) 백엔드 엔지니어 2021.03 - 2024.08\n\n"
    "주문 정산 배치를 Python과 PostgreSQL로 다시 만들어 하루 정산 시간을 6시간에서 40분으로 줄였다.\n\n"
    "한빛대학교(가상) 컴퓨터공학 학사 2015.03 - 2021.02\n\n"
    "정보처리기사 2020.11 취득\n\n"
    "기술: Python, PostgreSQL, Kafka, Redis"
)
COVER = (
    "누리데이터 주식회사에서 주문 정산 배치를 다시 만들었습니다.\n\n"
    "하루 정산 시간을 6시간에서 40분으로 줄였습니다.\n\n"
    "정산 실패 재시도를 자동화해 매달 이틀의 수작업을 없앴습니다."
)
STATEMENT = "하루 정산 시간을 6시간에서 40분으로 줄였다."


def resume_reply():
    return reply(
        experiences=[
            {
                "title": "주문 정산 배치 재구축",
                "organization": "누리데이터(가상)",
                "role": "백엔드 엔지니어",
                "period": "2021.03 - 2024.08",
                "kind": "work",
                "tags": ["Python", "PostgreSQL", "컴퓨터공학", "주문 정산 배치 재구축"],
                "claims": [
                    {
                        "kind": "result",
                        "statement": STATEMENT,
                        "contribution": "personal",
                        "quotes": [
                            {"block": 1, "text": "하루 정산 시간을 6시간에서 40분으로 줄였다"}
                        ],
                    }
                ],
            },
            {  # 학력을 경험으로 낸 경우: 학력 제안으로 돌린다
                "title": "컴퓨터공학 학사",
                "organization": "한빛대학교(가상)",
                "period": "2015.03 - 2021.02",
                "claims": [
                    {
                        "statement": "컴퓨터공학 학사를 받았다",
                        "quotes": [{"block": 2, "text": "한빛대학교(가상) 컴퓨터공학 학사"}],
                    }
                ],
            },
            {  # 자격증을 경험으로 낸 경우: 추출한 자격 후보로
                "title": "정보처리기사",
                "period": "2020.11",
                "claims": [
                    {
                        "statement": "정보처리기사를 취득했다",
                        "quotes": [{"block": 3, "text": "정보처리기사 2020.11 취득"}],
                    }
                ],
            },
            {  # 기술 목록 줄: 경험이 아니다
                "title": "기술",
                "tags": ["Python", "PostgreSQL", "Kafka", "Redis"],
                "claims": [
                    {
                        "kind": "skill",
                        "statement": "Python, PostgreSQL, Kafka, Redis를 쓴다",
                        "quotes": [{"block": 4, "text": "Python, PostgreSQL, Kafka, Redis"}],
                    }
                ],
            },
        ]
    )


def cover_reply():
    return reply(
        experiences=[
            {  # 같은 경험 (조직 표기가 다르다): 새로 만들지 않고 붙인다
                "title": "주문 정산 배치 재구축",
                "organization": "누리데이터 주식회사",
                "role": "백엔드 엔지니어",
                "claims": [
                    {  # 같은 사실: 새 주장 대신 근거만 더한다
                        "kind": "result",
                        "statement": "하루 정산 시간을 6시간에서 40분으로 줄였습니다.",
                        "contribution": "personal",
                        "quotes": [
                            {"block": 1, "text": "하루 정산 시간을 6시간에서 40분으로 줄였습니다"}
                        ],
                    },
                    {
                        "kind": "action",
                        "statement": "정산 실패 재시도를 자동화했다.",
                        "contribution": "personal",
                        "quotes": [{"block": 2, "text": "정산 실패 재시도를 자동화해"}],
                    },
                ],
            },
            {  # 조직·기간이 없어 확실하지 않다: 만들되 "같은 경험일 수 있음"
                "title": "주문 정산 배치",
                "claims": [
                    {
                        "kind": "action",
                        "statement": "정산 실패 재시도를 자동화했다.",
                        "contribution": "unknown",
                        "quotes": [{"block": 2, "text": "정산 실패 재시도를 자동화해"}],
                    },
                    {
                        "kind": "result",
                        "statement": "매달 이틀의 수작업을 없앴다.",
                        "contribution": "unknown",
                        "quotes": [{"block": 2, "text": "매달 이틀의 수작업을 없앴습니다"}],
                    },
                ],
            },
        ]
    )


async def two_sources(db, blobs, owner):
    client = ScriptedClient(resume_reply(), cover_reply())
    a = await upload(db, blobs, owner, RESUME, "resume.txt")
    first = await extract(db, owner, a.parse_run_id, client)
    b = await upload(db, blobs, owner, COVER, "cover.txt")
    second = await extract(db, owner, b.parse_run_id, client)
    exps = {e.title: e for e in await brain.list_experiences(db, owner)}
    return a, b, first, second, exps


async def test_multi_source_extraction_matches_and_routes(db, blobs, people):
    owner = people["owner"]
    a, _b, first, second, exps = await two_sources(db, blobs, owner)

    # 첫 원본: 경험 하나. 학력·자격·기술 줄은 경험이 되지 않는다
    assert first.status == "complete"
    assert first.experiences_created == 1
    assert set(exps) == {"주문 정산 배치 재구축", "주문 정산 배치"}
    main = exps["주문 정산 배치 재구축"]
    assert main.tags == ["Python", "PostgreSQL"]  # 전공·제목은 기술이 아니다
    sugg = first.suggestions
    assert [e["school"] for e in sugg["education"]] == ["한빛대학교(가상)"]
    assert sugg["education"][0]["major"] == "컴퓨터공학"
    assert sorted(sugg["skills"]) == ["Kafka", "Redis"]  # 경험 글에 없는 기술은 제안으로
    assert sugg["filtered"] == {"education": 1, "credentials": 1, "skills": 1}
    [cred] = await rows(db, owner, Credential)
    assert (cred.name, cred.status, cred.source_id, cred.acquired_on) == (
        "정보처리기사",
        "extracted",
        None,
        date(2020, 11, 1),
    )

    # 둘째 원본: 같은 경험에 붙이고, 같은 사실엔 근거만 더한다
    assert (second.experiences_matched, second.claims_attached, second.experiences_created) == (
        1,
        1,
        1,
    )
    full = await brain.get_experience(db, owner, main.id)
    by_statement = {c.statement: c for c in full.claims}
    assert set(by_statement) == {STATEMENT, "정산 실패 재시도를 자동화했다."}
    assert len(by_statement[STATEMENT].evidence) == 2  # 이력서 + 자기소개서
    maybe = exps["주문 정산 배치"]
    assert maybe.possible_duplicate_of == main.id
    assert maybe.possible_duplicate_title == "주문 정산 배치 재구축"
    assert second.suggestions["possible_duplicates"] == [
        {"experience_id": str(maybe.id), "possible_duplicate_of": str(main.id)}
    ]
    flagged = await brain.list_experiences(db, owner, brain.ExperienceFilter(duplicates=True))
    assert [e.id for e in flagged] == [maybe.id]

    # 학력 제안은 학력을 만들 때까지 남는다
    left = await list_suggestions(db, owner)
    assert [e["school"] for e in left["education"]] == ["한빛대학교(가상)"]
    assert left["education"][0]["source_id"] == str(a.source_id)
    assert sorted(left["skills"]) == ["Kafka", "Redis"]
    assert await list_suggestions(db, people["other"]) == {"education": [], "skills": []}


async def test_merge_moves_claims_and_rewrites_references(db, blobs, people):
    owner, editor = people["owner"], people["editor"]
    _, _, _, _, exps = await two_sources(db, blobs, owner)
    main, dup = exps["주문 정산 배치 재구축"], exps["주문 정산 배치"]
    with pytest.raises(ClaimRuleError, match="merge_requires_approve"):
        await experience_merge.merge_experiences(db, editor, main.id, [dup.id])
    with pytest.raises(ClaimRuleError, match="merge_self"):
        await experience_merge.merge_experiences(db, owner, main.id, [main.id])

    res = await experience_merge.merge_experiences(db, owner, main.id, [dup.id])
    assert res.merged == [dup.id]
    assert res.claims_folded == 1  # 같은 문장(추출 후보)은 근거만 옮기고 접는다
    assert res.claims_moved == 2  # 옮긴 행 (접은 것 포함)
    full = await brain.get_experience(db, owner, main.id)
    statements = sorted(c.statement for c in full.claims if c.status != ClaimStatus.RETIRED)
    assert statements == sorted(
        [STATEMENT, "정산 실패 재시도를 자동화했다.", "매달 이틀의 수작업을 없앴다."]
    )
    gone = await brain.get_experience(db, owner, dup.id)
    assert (gone.status, gone.merged_into_id) == ("retired", main.id)
    assert gone.merged_at is not None
    assert await brain.list_experiences(db, owner, brain.ExperienceFilter(duplicates=True)) == []
    with pytest.raises(ClaimRuleError, match="experience_not_active"):
        await experience_merge.merge_experiences(db, owner, main.id, [dup.id])


async def test_merged_reference_in_resume_resolves_to_target(db, blobs, people):
    owner = people["owner"]
    _, _, _, _, exps = await two_sources(db, blobs, owner)
    main, dup = exps["주문 정산 배치 재구축"], exps["주문 정산 배치"]
    claims = await rows(db, owner, Claim)
    for c in claims:
        if c.status == "extracted" and c.contribution != "unknown":
            await brain.confirm_claim(db, owner, c.id)
    # 이력서가 합치기 전의 경험을 가리킨다
    vid = await resumes.create_version(db, owner, kind="master")
    await resumes.save(
        db,
        owner,
        vid,
        base_revision=0,
        operations=[
            {"op": "add_item", "section": "work", "ref": {"type": "experience", "id": str(dup.id)}}
        ],
        reason="t",
    )
    await experience_merge.merge_experiences(db, owner, main.id, [dup.id])
    view = await resumes.get_version(db, owner, vid)
    [item] = [it for sec in view.content["sections"] for it in sec["items"] if it.get("ref")]
    assert item["state"] != "missing"  # 합친 경험의 값으로 읽는다
    # 한 번에 채우기는 합쳐 받은 경험을 다시 넣지 않는다
    await resumes.save(
        db, owner, vid, base_revision=1, operations=[{"op": "fill_all", "claims": True}], reason="t"
    )
    view = await resumes.get_version(db, owner, vid)
    refs = [
        it["ref"]["id"]
        for sec in view.content["sections"]
        for it in sec["items"]
        if it.get("ref") and it["ref"]["type"] == "experience"
    ]
    assert refs == [str(dup.id)]


async def test_fill_all_skips_possible_duplicates_and_repeated_lines(db, blobs, people):
    owner = people["owner"]
    _, _, _, _, exps = await two_sources(db, blobs, owner)
    for c in await rows(db, owner, Claim):
        if c.status == "extracted":
            await brain.confirm_claim(db, owner, c.id, contribution="personal")
    vid = await resumes.create_version(db, owner, kind="master", fill=True, fill_claims=True)
    view = await resumes.get_version(db, owner, vid)
    items = [
        it
        for sec in view.content["sections"]
        for it in sec["items"]
        if it.get("ref", {}).get("type") == "experience"
    ]
    # "같은 경험일 수 있음" 짝은 한 번만
    assert [it["ref"]["id"] for it in items] == [str(exps["주문 정산 배치 재구축"].id)]
    texts = [ln["text"] for it in items for ln in it.get("lines", [])]
    assert len(texts) == len(set(texts))


async def test_delete_experience_refuses_when_used(db, blobs, people):
    owner, editor = people["owner"], people["editor"]
    _, _, _, _, exps = await two_sources(db, blobs, owner)
    main, dup = exps["주문 정산 배치 재구축"], exps["주문 정산 배치"]
    [c] = [c for c in await rows(db, owner, Claim, Claim.experience_id == main.id)][:1]
    await brain.confirm_claim(db, owner, c.id)
    with pytest.raises(experience_merge.ExperienceInUse) as err:
        await experience_merge.delete_experience(db, owner, main.id)
    assert err.value.references == {"confirmed_claims": 1}
    with pytest.raises(ClaimRuleError, match="confirm_requires_approve"):
        await experience_merge.delete_experience(db, editor, main.id, include_confirmed=True)
    # 이력서가 쓰면 확인한 사실을 같이 지워도 거부한다
    vid = await resumes.create_version(db, owner, kind="master")
    await resumes.save(
        db,
        owner,
        vid,
        base_revision=0,
        operations=[
            {"op": "add_item", "section": "work", "ref": {"type": "experience", "id": str(main.id)}}
        ],
        reason="t",
    )
    with pytest.raises(experience_merge.ExperienceInUse) as err:
        await experience_merge.delete_experience(db, owner, main.id, include_confirmed=True)
    assert err.value.references == {"resumes": 1}

    res = await experience_merge.delete_experience(db, editor, dup.id)  # 확인 전 후보뿐
    assert res.claims_deleted == 2
    assert await rows(db, owner, Experience, Experience.id == dup.id) == []
    # 지운 짝을 가리키던 표시는 저절로 풀린다 (ON DELETE SET NULL)
    assert await brain.list_experiences(db, owner, brain.ExperienceFilter(duplicates=True)) == []


async def test_dismiss_duplicate_hint(db, blobs, people):
    owner = people["owner"]
    _, _, _, _, exps = await two_sources(db, blobs, owner)
    dup = exps["주문 정산 배치"]
    await experience_merge.dismiss_duplicate(db, owner, dup.id)
    view = await brain.get_experience(db, owner, dup.id)
    assert view.possible_duplicate_of is None


# --- 한 번에 확인 -------------------------------------------------------------------------


async def test_batch_confirm_rules(db, blobs, people):
    owner, editor = people["owner"], people["editor"]
    _a, b, _, _, exps = await two_sources(db, blobs, owner)
    dup = exps["주문 정산 배치"]
    with pytest.raises(ClaimRuleError, match="confirm_requires_approve"):
        await brain.confirm_claims(db, editor, contribution="personal", experience_id=dup.id)
    with pytest.raises(ClaimRuleError, match="choose_one_scope"):
        await brain.confirm_claims(
            db, owner, contribution="personal", experience_id=dup.id, source_id=b.source_id
        )
    # 적힌 대로: 구분이 "모름"이면 확인하지 않는다 (한 건 확인과 같은 규칙)
    res = await brain.confirm_claims(db, owner, contribution="as_is", experience_id=dup.id)
    assert res.confirmed == []
    assert [r for _, r in res.skipped] == ["contribution_required", "contribution_required"]
    # 원본 하나에서 나온 것 모두: 자기소개서에서 나온 후보 (다른 원본 것은 건드리지 않는다)
    res = await brain.confirm_claims(db, owner, contribution="team", source_id=b.source_id)
    # 자기소개서 근거가 있는 것 모두: 새 사실 3개 + 이력서 사실(같은 문장이라 근거만 더함)
    assert len(res.confirmed) == 4 and not res.truncated
    for c in await rows(db, owner, Claim, Claim.experience_id == dup.id):
        assert (c.status, c.contribution) == ("evidence_supported", "team")
    main_claims = await rows(
        db, owner, Claim, Claim.experience_id == exps["주문 정산 배치 재구축"].id
    )
    # 이력서에서 처음 나온 사실도 자기소개서 근거가 붙어 있으므로 같이 확인됐다
    assert {c.status for c in main_claims} == {"evidence_supported"}


class Tokens:
    def __init__(self, tokens):
        self.tokens = tokens

    async def resolve(self, credential):
        return self.tokens.get(credential)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


async def http_client(db, blobs, people):
    app = FastAPI()
    app.include_router(
        build_router(
            CoreDeps(
                identity=Tokens({"owner": people["owner"], "editor": people["editor"]}),
                db=db,
                blobs=blobs,
                max_upload_bytes=1 << 20,
            )
        )
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t")


async def test_http_merge_delete_batch_and_flags(db, blobs, people):
    owner = people["owner"]
    _, _, _, _, exps = await two_sources(db, blobs, owner)
    main, dup = exps["주문 정산 배치 재구축"], exps["주문 정산 배치"]
    async with await http_client(db, blobs, people) as c:
        r = await c.get("/core/experiences", params={"duplicates": "true"}, headers=auth("owner"))
        [row] = r.json()
        assert (row["id"], row["possible_duplicate_of"], row["possible_duplicate_title"]) == (
            str(dup.id),
            str(main.id),
            "주문 정산 배치 재구축",
        )
        r = await c.get(f"/core/experiences/{main.id}", headers=auth("owner"))
        evidence = [e for cl in r.json()["claims"] for e in cl["evidence"]]
        assert all("weak_quote" in e for e in evidence)
        r = await c.get("/core/extraction-suggestions", headers=auth("owner"))
        assert r.json()["education"][0]["school"] == "한빛대학교(가상)"

        r = await c.post(
            "/core/claims/confirm-batch",
            json={"contribution": "personal", "experience_id": str(main.id)},
            headers=auth("editor"),
        )
        assert r.status_code == 403
        r = await c.post(
            "/core/claims/confirm-batch",
            json={"contribution": "personal", "experience_id": str(main.id)},
            headers=auth("owner"),
        )
        assert r.status_code == 200, r.text
        assert len(r.json()["confirmed"]) == 2
        r = await c.post(
            "/core/claims/confirm-batch", json={"contribution": "personal"}, headers=auth("owner")
        )
        assert (r.status_code, r.json()["detail"]) == (422, "choose_one_scope")

        r = await c.delete(f"/core/experiences/{main.id}", headers=auth("owner"))
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "experience_in_use"
        assert r.json()["detail"]["references"] == {"confirmed_claims": 2}

        r = await c.post(
            f"/core/experiences/{main.id}/merge",
            json={"source_ids": [str(dup.id)]},
            headers=auth("editor"),
        )
        assert r.status_code == 403
        r = await c.post(
            f"/core/experiences/{main.id}/merge",
            json={"source_ids": [str(dup.id)]},
            headers=auth("owner"),
        )
        assert r.status_code == 200, r.text
        assert r.json()["merged"] == [str(dup.id)]
        r = await c.get(f"/core/experiences/{dup.id}", headers=auth("owner"))
        assert r.json()["merged_into_id"] == str(main.id)
        r = await c.delete(f"/core/experiences/{dup.id}/duplicate-hint", headers=auth("owner"))
        assert r.status_code == 204


# --- 이력서 ---------------------------------------------------------------------------


def test_resume_title_role_collapse_and_filename():
    item = {
        "ref": {"type": "experience", "id": "x"},
        "fields": {
            "title": "백엔드 엔지니어",
            "role": "백엔드 엔지니어",
            "organization": "누리데이터",
        },
    }
    rendered = render_item("work", item, blind=False)
    assert rendered.secondary == "백엔드 엔지니어"
    assert resume_filename("마스터", "html").endswith(".html")
    assert not resume_filename("마스터", "html").endswith(".pdf.html")
    assert resume_filename("마스터", "json").endswith(".json")


def test_resume_markdown_does_not_escape_period_digits():
    doc = RenderDoc(
        "t",
        "one_column",
        False,
        RenderProfile(name="한가람"),
        (
            RenderSection(
                "work",
                "경력",
                (
                    RenderItem(
                        period="2024.09 – 2025.02", primary="누리데이터", lines=("1. 첫 줄",)
                    ),
                ),
            ),
        ),
    )
    md = render_resume("md", doc).decode()
    assert "\\2024" not in md and "2024.09 – 2025.02" in md
    assert "1\\. 첫 줄" in md or "\\1. 첫 줄" in md or "1\\." in md  # 줄 앞 번호는 목록이 되지 않게


def test_estimate_pages_grows_with_content():
    def doc(n_items, n_lines):
        items = tuple(
            RenderItem(
                period="2021.03 – 2024.08",
                primary=f"누리데이터 {i}",
                lines=tuple(
                    "주문 정산 배치 시스템을 Python과 PostgreSQL로 다시 만들어 하루 정산 시간을 줄였다."
                    for _ in range(n_lines)
                ),
            )
            for i in range(n_items)
        )
        return RenderDoc("t", "one_column", False, None, (RenderSection("work", "경력", items),))

    assert estimate_pages(doc(1, 2)) == 1
    assert estimate_pages(doc(6, 6)) >= 2
    assert estimate_pages(doc(12, 6)) > estimate_pages(doc(6, 6))


# --- 공고 문항·요건 ------------------------------------------------------------------------


def test_clean_question_strips_markers_and_reads_limit():
    assert clean_question("- 지원 동기를 적어 주세요. (700자 이내)") == (
        "지원 동기를 적어 주세요.",
        700,
    )
    assert clean_question("1. 협업 경험을 적어 주세요 [최대 1,000자]") == (
        "협업 경험을 적어 주세요",
        1000,
    )
    # 세는 기준이 있는 괄호는 글에 남긴다 (문서 설정이 이 글에서 공백 제외를 읽는다)
    assert clean_question("2) 성장 과정 (공백 제외 500자)") == ("성장 과정 (공백 제외 500자)", 500)
    assert clean_question("2024년 목표는?") == ("2024년 목표는?", None)
    assert clean_list_item("- 정산 도메인 경험") == "정산 도메인 경험"
    assert clean_list_item("3년 이상 경력") == "3년 이상 경력"


def test_requirement_qualifier_words_do_not_block_match():
    assert key_terms("정산 도메인 경험").words == ("정산",)
    ctx = CheckContext(
        company="하늘페이(가상)",
        job_title="백엔드",
        requirements=(("preferred", "정산 도메인 경험"),),
        brain=(EvidenceClaim("c1", "주문 정산 배치를 다시 만들었다", confirmed=True),),
    )
    [row] = requirement_visibility(ctx, [])
    assert row["verdict"] == "missing"  # 브레인에 근거가 있다 (경험 없음이 아니다)


# --- 내보내기 날짜 ----------------------------------------------------------------------


def test_markdown_day_uses_workspace_zone():
    from zoneinfo import ZoneInfo

    from career_core.usecases import markdown_export as mx

    late = datetime(2026, 11, 17, 16, 30, tzinfo=UTC)  # 서울 11월 18일 01:30
    assert mx._day(late) == "2026-11-17"
    token = mx._ZONE.set(ZoneInfo("Asia/Seoul"))
    try:
        assert mx._day(late) == "2026-11-18"
    finally:
        mx._ZONE.reset(token)

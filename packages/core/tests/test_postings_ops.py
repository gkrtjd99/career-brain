"""공고 운영 백로그: 봇 차단 구분, 스캔 PDF → 쪽 이미지 → 비전, 그리팅 자기소개서 문항, CPU 작업 스레드.

모든 페이지·PDF·문항은 합성이다 (실제 페이지의 구조만 흉내 낸다).
"""

import json
import struct
import threading
import zlib

import httpx
import pytest
from career_core.adapters import pdf_render
from career_core.adapters.web_fetch import AddressBlocked, HttpFetcher, detect_bot_block
from career_core.db import ModelCall
from career_core.jobs.worker import posting_handlers, run_until_idle
from career_core.llm import ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.ports import ActorContext
from career_core.usecases import postings as uc
from career_core.usecases.workspaces import create_workspace
from sqlalchemy import select

pytestmark = pytest.mark.anyio

# Cloudflare 관리형 확인 화면을 흉내 낸 합성 본문 (실제 페이지를 복사하지 않았다)
CF_CHALLENGE = (
    "<!DOCTYPE html><html><head><style>body{text-align:center}</style></head><body>"
    "<noscript>Enable JavaScript and cookies to continue</noscript>"
    "<script>(function(){window._cf_chl_opt={cType:'managed'};var s=document.createElement('script');"
    "s.src='/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1';})();</script></body></html>"
)


def scanned_pdf(pages: int, size: tuple[int, int] = (612, 792)) -> bytes:
    """글자 없이 사각형만 그린 PDF (스캔본 흉내: 추출할 텍스트가 없다)."""
    w, h = size
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(pages))
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode(),
    ]
    for i in range(pages):
        content = f"0 g 72 72 {200 + i * 10} 100 re f".encode()
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}] /Contents {4 + 2 * i} 0 R >>".encode()
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def png_size(png: bytes) -> tuple[int, int]:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", png[16:24])


def _script_json(value) -> str:
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


GREETING_QUESTIONS = [
    {
        "questionnaireId": "q-source",
        "question": "이 공고를 처음 본 곳을 골라 주세요",
        "type": 400,
        "need": True,
        "description": None,
        "maxLength": None,
        "items": [],
        "optionItems": [{"id": "o1", "item": "합성 채널 A"}, {"id": "o2", "item": "합성 채널 B"}],
        "isDirectly": True,
    },
    {
        "questionnaireId": "q-motive",
        "question": "  지원 동기와 입사 후   하고 싶은 일을 적어 주세요. ",
        "type": 100,
        "need": True,
        "description": None,
        "maxLength": 1000,
        "items": [],
        "optionItems": [],
        "isDirectly": True,
    },
    {
        "questionnaireId": "q-yesno",
        "question": "합성 질문: 해외 근무가 가능한가요?",
        "type": 200,
        "need": True,
        "description": None,
        "maxLength": None,
        "items": [],
        "optionItems": [{"id": "y", "item": "예"}, {"id": "n", "item": "아니요"}],
        "isDirectly": False,
    },
    {
        "questionnaireId": "q-growth",
        "question": "가장 크게 성장한 경험을 적어 주세요",
        "type": 100,
        "need": False,
        "description": "공백 포함",
        "maxLength": None,
        "items": [],
        "optionItems": [],
        "isDirectly": True,
    },
]
GREETING_DETAIL = (
    "<h2>담당 업무</h2><ul><li>합성 주문 API 설계와 개발</li><li>합성 배치 작업 운영</li></ul>"
    "<h2>자격 요건</h2><ul><li>합성 서버 개발 경험 2년 이상</li><li>SQL 이해</li></ul>"
)
GREETING_PAGE = (
    "<html><head><meta property='og:title' content='합성 서버 개발자'></head><body>"
    "<div id='__next'><p>합성 서버 개발자</p></div>"
    "<script id='__NEXT_DATA__' type='application/json'>"
    + _script_json(
        {
            "props": {
                "pageProps": {
                    "dehydratedState": {
                        "queries": [
                            {
                                "queryKey": ["career", "getOpeningById", {"openingId": 7001}],
                                "state": {
                                    "data": {
                                        "success": True,
                                        "data": {
                                            "groupInfo": {"name": "가상문항 채용"},
                                            "openingsInfo": {
                                                "openingId": 7001,
                                                "title": "합성 서버 개발자",
                                                "detail": GREETING_DETAIL,
                                                "openDate": "2026-11-01T00:00:00Z",
                                            },
                                            "questionnairesInfo": GREETING_QUESTIONS,
                                            "jobPositionSetting": {"jobPositions": []},
                                        },
                                    }
                                },
                            }
                        ]
                    }
                }
            }
        }
    )
    + "</script></body></html>"
)

HOSTS = {
    "blocked.fake.test": "93.184.216.30",
    "limited.fake.test": "93.184.216.31",
    "scan.fake.test": "93.184.216.32",
    "fakeco.career.greetinghr.com": "93.184.216.33",
}


class Site:
    def __init__(self):
        self.requests: list[tuple[str, str, str, str]] = []
        self.routes = {
            ("blocked.fake.test", "/job"): (
                429,
                {"cf-mitigated": "challenge", "server": "cloudflare"},
                "text/html",
                CF_CHALLENGE,
            ),
            ("blocked.fake.test", "/forbidden"): (403, {}, "text/html", CF_CHALLENGE),
            ("limited.fake.test", "/job"): (
                429,
                {"retry-after": "30", "server": "cloudflare"},
                "text/plain",
                "Too Many Requests",
            ),
            ("scan.fake.test", "/scan.pdf"): (200, {}, "application/pdf", scanned_pdf(2)),
            ("scan.fake.test", "/long.pdf"): (200, {}, "application/pdf", scanned_pdf(6)),
            ("scan.fake.test", "/huge.pdf"): (
                200,
                {},
                "application/pdf",
                scanned_pdf(1, (5000, 3000)),
            ),
            ("fakeco.career.greetinghr.com", "/ko/o/7001"): (200, {}, "text/html", GREETING_PAGE),
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        self.requests.append(
            (
                host,
                request.url.path,
                request.headers.get("cookie", ""),
                request.headers["user-agent"],
            )
        )
        assert request.url.host == HOSTS[host]
        status, headers, ctype, body = self.routes[(host, request.url.path)]
        content = body if isinstance(body, bytes) else body.encode()
        return httpx.Response(status, content=content, headers={"content-type": ctype, **headers})


async def fake_resolve(host, port):
    ip = HOSTS.get(host)
    if ip is None:
        raise AddressBlocked(host)
    return ip


OCR_TEXT = "2026 합성 신입 채용\n가상스캔 주식회사\n접수 기간: 2026.11.20 ~ 2026.12.04\n담당 업무: 합성 시스템 개발"
REPLY = json.dumps(
    {
        "page_type": "posting",
        "fields": {
            "title": {"value": "2026 합성 신입 채용", "quote": "2026 합성 신입 채용"},
            "company": {"value": "가상스캔 주식회사", "quote": "가상스캔 주식회사"},
        },
    }
)


class Model:
    def __init__(self):
        self.requests = []

    async def complete(self, model, request):
        self.requests.append(request)
        return ModelCompletion(
            text=OCR_TEXT if request.images else REPLY,
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, TokenUsage(input_tokens=100, output_tokens=50)),),
        )


@pytest.fixture
async def env(db, tmp_path):
    from career_core.adapters.blobstore import LocalBlobStore

    ws, subject = await create_workspace(db, name="A", owner_display_name="alice")
    site, model = Site(), Model()

    def make(vision=True):
        routes = 'job_extract_image = "proxy/gpt-6.1-sol"\n' if vision else ""
        config = parse_models_config(
            '[endpoints.proxy]\npreset = "cliproxyapi"\nprice_as = "openai"\n'
            'data_classes = ["public"]\n[routes]\ndefault = "proxy/gpt-6.1-sol"\n' + routes
        )
        gw = ModelGateway(
            db,
            config,
            PriceBook.load(),
            client_factory=lambda ep, key: model,
            env={"CLIPROXY_API_KEY": "k"},
        )
        fetcher = HttpFetcher(
            transport=httpx.MockTransport(site.handler), resolver=fake_resolve, max_bytes=1 << 20
        )
        return uc.PostingDeps(db=db, fetcher=fetcher, gateway=gw, blobs=LocalBlobStore(tmp_path))

    return {"actor": ActorContext(subject, ws), "site": site, "model": model, "make": make}


async def fetch(db, env, url, **kw):
    deps = env["make"](**kw)
    [r] = await uc.save_urls(db, env["actor"], [url])
    await run_until_idle(db, posting_handlers(deps))
    return await uc.get_posting(db, env["actor"], r.posting_id), deps, r


# --- 봇 차단 ----------------------------------------------------------------------------


def test_detect_bot_block_rules():
    assert detect_bot_block(429, {"CF-Mitigated": "challenge"}, "") == "cloudflare"
    assert detect_bot_block(200, {"cf-mitigated": "challenge"}, "") == "cloudflare"
    assert detect_bot_block(403, {}, CF_CHALLENGE) == "cloudflare"
    assert detect_bot_block(405, {"x-amzn-waf-action": "captcha"}, "") == "aws_waf"
    assert detect_bot_block(403, {"x-datadome": "protected"}, "") == "datadome"
    assert (
        detect_bot_block(503, {}, "<script src='https://geo.captcha-delivery.com/x'>") == "datadome"
    )
    # 진짜 요청 한도: 확인 화면 표시가 없다
    assert detect_bot_block(429, {"retry-after": "30", "server": "cloudflare"}, "Too Many") is None
    assert detect_bot_block(200, {}, CF_CHALLENGE) is None  # 200 본문만으로는 판단하지 않는다
    assert detect_bot_block(404, {}, CF_CHALLENGE) is None


async def test_bot_challenge_is_reported_apart_from_rate_limits(db, env):
    blocked, _, _ = await fetch(db, env, "https://blocked.fake.test/job")
    forbidden, _, _ = await fetch(db, env, "https://blocked.fake.test/forbidden")
    limited, _, _ = await fetch(db, env, "https://limited.fake.test/job")
    run = blocked.runs[0]
    assert (run.status, run.error_code, run.http_status) == ("failed", "bot_blocked", 429)
    assert run.notes == ["bot_blocked_by:cloudflare"]
    assert forbidden.runs[0].error_code == "bot_blocked"
    assert (limited.runs[0].error_code, limited.runs[0].notes) == ("rate_limited", [])
    for view in (blocked, forbidden, limited):
        assert view.status == "draft" and "deadline" not in view.fields  # 마감으로 보지 않는다
    # 우회하지 않는다: 요청은 한 번씩, 쿠키 없음, 같은 User-Agent (브라우저 흉내 없음)
    paths = [(h, p) for h, p, _, _ in env["site"].requests]
    assert paths == [
        ("blocked.fake.test", "/job"),
        ("blocked.fake.test", "/forbidden"),
        ("limited.fake.test", "/job"),
    ]
    assert {c for _, _, c, _ in env["site"].requests} == {""}
    assert len({ua for _, _, _, ua in env["site"].requests}) == 1
    assert env["model"].requests == []


# --- 스캔 PDF → 쪽 이미지 → 비전 ------------------------------------------------------


def test_png_gray_is_a_valid_png():
    pixels = bytes(range(6)) + b"\x00\x00" + bytes(range(6, 12)) + b"\x00\x00"  # stride 8
    png = pdf_render.png_gray(6, 2, 8, pixels)
    assert png_size(png) == (6, 2)
    length = struct.unpack(">I", png[33:37])[0]
    assert png[37:41] == b"IDAT"
    raw = zlib.decompress(png[41 : 41 + length])
    assert raw == b"\x00" + bytes(range(6)) + b"\x00" + bytes(range(6, 12))


@pytest.mark.skipif(not pdf_render.renderer_available(), reason="pdf-render extra not installed")
async def test_scanned_pdf_is_read_through_the_vision_route(db, env):
    view, deps, r = await fetch(db, env, "https://scan.fake.test/scan.pdf")
    v = view.latest
    assert (v.method, v.adapter, v.has_screenshot) == ("http", "pdf", True)
    assert {k: x["value"] for k, x in v.extracted.items()}["company"] == "가상스캔 주식회사"
    assert view.runs[0].notes[:2] == ["pdf_rendered:2/2", "ocr_pdf_by_model"]
    ocr, extract = env["model"].requests
    assert [i.media_type for i in ocr.images] == ["image/png", "image/png"]
    for img in ocr.images:
        w, h = png_size(img.data)
        assert max(w, h) <= pdf_render.MAX_EDGE_PIXELS and w * h <= pdf_render.MAX_PAGE_PIXELS
    assert ocr.data_classes == frozenset({"public"}) == extract.data_classes
    async with db.workspace_session(env["actor"].workspace_id) as s:
        purposes = sorted((await s.execute(select(ModelCall.purpose))).scalars())
    assert purposes == ["job_extract", "job_extract_image"]
    # 같은 PDF를 다시 가져오면 그리지도, 모델을 부르지도 않는다
    await uc.refetch(db, env["actor"], r.posting_id)
    await run_until_idle(db, posting_handlers(deps))
    assert len(env["model"].requests) == 2
    assert (await uc.get_posting(db, env["actor"], r.posting_id)).runs[0].unchanged


@pytest.mark.skipif(not pdf_render.renderer_available(), reason="pdf-render extra not installed")
async def test_scanned_pdf_caps_pages_and_pixels(db, env):
    view, _, _ = await fetch(db, env, "https://scan.fake.test/long.pdf")
    assert view.runs[0].notes[0] == f"pdf_rendered:{pdf_render.MAX_RENDER_PAGES}/6"
    assert len(env["model"].requests[0].images) == pdf_render.MAX_RENDER_PAGES
    env["model"].requests.clear()
    await fetch(db, env, "https://scan.fake.test/huge.pdf")  # 5000×3000pt 쪽
    [img] = env["model"].requests[0].images
    w, h = png_size(img.data)
    assert w == pdf_render.MAX_EDGE_PIXELS and h < w and w * h <= pdf_render.MAX_PAGE_PIXELS


async def test_scanned_pdf_without_renderer_or_route_needs_manual_check(db, env, monkeypatch):
    def missing(*a, **kw):
        raise pdf_render.PdfRenderUnavailable

    monkeypatch.setattr(uc, "render_pages", missing)
    view, _, _ = await fetch(db, env, "https://scan.fake.test/scan.pdf")
    run = view.runs[0]
    assert (run.error_code, run.notes) == (
        "needs_manual_check",
        ["pdf_no_text", "pdf_render_missing"],
    )
    assert env["model"].requests == []


async def test_renderer_missing_is_a_clear_error(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_pdfium(name, *a, **kw):
        if name == "pypdfium2":
            raise ImportError(name)
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_pdfium)
    assert not pdf_render.renderer_available()
    with pytest.raises(pdf_render.PdfRenderUnavailable):
        pdf_render.render_pages(scanned_pdf(1))


@pytest.mark.skipif(not pdf_render.renderer_available(), reason="pdf-render extra not installed")
def test_render_rejects_broken_files():
    with pytest.raises(pdf_render.PdfRenderError):
        pdf_render.render_pages(b"%PDF-1.4\nnot really a pdf")


# --- 그리팅 자기소개서 문항 ----------------------------------------------------------------


async def test_greeting_questionnaires_become_question_fields(db, env):
    view, _, _ = await fetch(
        db, env, "https://fakeco.career.greetinghr.com/ko/o/7001", vision=False
    )
    v = view.latest
    assert v.adapter == "greeting"
    q = v.extracted["questions"]
    # 서술형(type 100)만, 원래 순서대로. 선택형(200·400)은 문항이 아니다. 글자 수가 없으면 비운다.
    assert q["value"] == [
        {"prompt": "지원 동기와 입사 후 하고 싶은 일을 적어 주세요.", "char_limit": 1000},
        {"prompt": "가장 크게 성장한 경험을 적어 주세요", "char_limit": None},
    ]
    assert q["evidence"] == {
        "kind": "structured",
        "ref": "greeting:opening.questionnairesInfo[1,3].question·maxLength",
    }
    assert v.extracted["company"]["value"] == "가상문항 채용"


def test_greeting_questions_ignore_bad_shapes():
    from career_core.adapters.ats import _greeting_questions

    data = {
        "questionnairesInfo": [
            None,
            {"type": 100, "question": "   "},
            {"type": 100, "question": "합성 문항", "maxLength": True},
            {"type": 100, "question": "합성 문항 2", "maxLength": -5},
            {"type": "100", "question": "문자열 type"},
        ]
    }
    assert _greeting_questions(data) == (
        [
            {"prompt": "합성 문항", "char_limit": None},
            {"prompt": "합성 문항 2", "char_limit": None},
        ],
        [2, 3],
    )
    assert _greeting_questions({"questionnairesInfo": None}) == ([], [])


# --- CPU 작업은 스레드에서 --------------------------------------------------------------


async def test_html_extraction_runs_off_the_event_loop(db, env, monkeypatch):
    loop_thread = threading.current_thread().name
    seen = []
    real = uc.extract_page

    def spy(html, url):
        seen.append(threading.current_thread().name)
        return real(html, url)

    monkeypatch.setattr(uc, "extract_page", spy)
    await fetch(db, env, "https://fakeco.career.greetinghr.com/ko/o/7001", vision=False)
    assert seen and all(name != loop_thread for name in seen)


# --- c0025: 긴 스캔 공고 쪽 고르기 · 비전 비용 확인 ------------------------------------------


def mixed_pdf(pattern: str) -> bytes:
    """쪽마다 ``x``(사각형을 그린 쪽) 또는 ``.``(빈 쪽). 글자는 없다 (스캔본 흉내)."""
    pages = len(pattern)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(pages))
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode(),
    ]
    for i, mark in enumerate(pattern):
        content = f"0 g 72 72 {200 + i * 10} 100 re f".encode() if mark == "x" else b""
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R >>".encode()
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


needs_renderer = pytest.mark.skipif(
    not pdf_render.renderer_available(), reason="pdf-render extra not installed"
)


def test_choose_pages_keeps_front_pages_and_the_last_one():
    ink = dict.fromkeys(range(10), 0.05)
    assert pdf_render.choose_pages(ink, 4) == ((0, 1, 2, 9), ())
    ink[1] = ink[9] = 0.0  # 빈 쪽은 뺀다: 마지막 글 있는 쪽이 끝쪽
    assert pdf_render.choose_pages(ink, 4) == ((0, 2, 3, 8), (1, 9))
    assert pdf_render.choose_pages({0: 0.1, 1: 0.0, 2: 0.2}, 4) == ((0, 2), (1,))
    # 모두 빈 쪽이면 앞쪽부터 (모델이 NO_TEXT로 답한다)
    assert pdf_render.choose_pages(dict.fromkeys(range(6), 0.0), 4)[0] == (0, 1, 2, 3)
    assert pdf_render.choose_pages(ink, 1) == ((0,), (1, 9))
    assert pdf_render.choose_pages({}, 4) == ((), ())


@needs_renderer
def test_pick_pages_measures_ink_on_real_pages():
    pick = pdf_render.pick_pages(mixed_pdf("x.xx.xxx"), max_pages=4)
    assert pick.pages_total == 8 and pick.blank == (1, 4)
    assert pick.pages == (0, 2, 3, 7)
    assert pick.ink[1] < pdf_render.BLANK_INK_RATIO <= pick.ink[0]
    rendered = pdf_render.render_pages(mixed_pdf("x.xx.xxx"), pages=pick.pages)
    assert rendered.pages == (0, 2, 3, 7) and len(rendered.sizes) == 4
    assert all(png_size(img) == size for img, size in zip(rendered.images, rendered.sizes))


def test_image_token_estimate_is_the_larger_provider_formula():
    assert uc.image_tokens(1236, 1600) == -(-1236 * 1600 // 750)  # Anthropic 쪽이 크다
    assert uc.image_tokens(0, 10) == 0
    assert uc.image_tokens(100, 100) == 85 + 170  # 작은 이미지는 OpenAI 기본+타일 하나


METERED = (
    '[endpoints.oa]\npreset = "openai"\ndata_classes = ["public"]\n'
    '[routes]\ndefault = "oa/gpt-6.1-sol"\njob_extract_image = "oa/gpt-6.1-sol"\n'
)


def metered_deps(db, env, tmp_path, threshold):
    from career_core.adapters.blobstore import LocalBlobStore

    gw = ModelGateway(
        db,
        parse_models_config(METERED),
        PriceBook.load(),
        client_factory=lambda ep, key: env["model"],
        env={"OPENAI_API_KEY": "k"},
    )
    fetcher = HttpFetcher(
        transport=httpx.MockTransport(env["site"].handler), resolver=fake_resolve, max_bytes=1 << 20
    )
    return uc.PostingDeps(
        db=db,
        fetcher=fetcher,
        gateway=gw,
        blobs=LocalBlobStore(tmp_path),
        vision_confirm_usd=threshold,
    )


async def test_gateway_estimate_cost_uses_the_price_book(db, env, tmp_path):
    from decimal import Decimal

    deps = metered_deps(db, env, tmp_path, None)
    est = deps.gateway.estimate_cost(
        "job_extract_image", input_tokens=100_000, output_tokens=10_000
    )
    assert (est.endpoint, est.model, est.metered) == ("oa", "gpt-6.1-sol", True)
    assert est.usd == Decimal("0.20") + Decimal("0.10")  # 입력 $2/1M, 출력 $10/1M
    # 긴 입력은 단가 구간을 따른다 (272,001 토큰부터 $4/$15)
    big = deps.gateway.estimate_cost("job_extract_image", input_tokens=300_000, output_tokens=0)
    assert big.usd == Decimal("1.20")
    sub = env["make"]().gateway.estimate_cost("job_extract_image", input_tokens=10, output_tokens=0)
    assert sub.billing == "subscription" and not sub.metered


@needs_renderer
async def test_expensive_scan_waits_for_confirmation_then_reads_once(db, env, tmp_path):
    from decimal import Decimal

    env["site"].routes[("scan.fake.test", "/long8.pdf")] = (
        200,
        {},
        "application/pdf",
        mixed_pdf("x.xx.xxx"),
    )
    deps = metered_deps(db, env, tmp_path, Decimal("0.01"))
    actor = env["actor"]
    [r] = await uc.save_urls(db, actor, ["https://scan.fake.test/long8.pdf"])
    await run_until_idle(db, posting_handlers(deps))
    view = await uc.get_posting(db, actor, r.posting_id)
    run = view.runs[0]
    # 비전을 부르기 전에 멈춘다: 모델 호출·장부·버전 없음
    assert run.status == "needs_confirmation" and env["model"].requests == []
    assert view.latest is None
    c = run.confirmation
    assert c.status == "pending" and c.pages == [1, 3, 4, 8] and c.pages_total == 8
    assert c.images == 4 and c.input_tokens > 4 * 2000
    assert Decimal(c.estimated_usd) < Decimal(c.max_usd) and Decimal(c.max_usd) > Decimal("0.01")
    assert c.threshold_usd == "0.010000" and c.model == "gpt-6.1-sol"
    assert "pdf_pages_read:1,3,4,8" in run.notes and "pdf_blank_pages:2" in run.notes
    summary = next(p for p in await uc.list_postings(db, actor) if p.id == r.posting_id)
    assert summary.last_fetch_status == "needs_confirmation"
    async with db.workspace_session(actor.workspace_id) as s:
        assert (await s.execute(select(ModelCall))).first() is None

    # AI 에이전트는 비용을 승인하지 않는다
    agent = ActorContext(actor.subject_id, actor.workspace_id, kind="agent")
    with pytest.raises(uc.PostingRuleError):
        await uc.decide_fetch_confirmation(db, agent, r.posting_id, run.id, accept=True)
    # 사람이 확인 → 다시 가져오기 → 같은 파일·같은 쪽이면 한 번만 읽는다
    new_run = await uc.decide_fetch_confirmation(db, actor, r.posting_id, run.id, accept=True)
    assert new_run is not None and new_run != run.id
    with pytest.raises(uc.PostingRuleError):  # 두 번 결정하지 않는다
        await uc.decide_fetch_confirmation(db, actor, r.posting_id, run.id, accept=True)
    await run_until_idle(db, posting_handlers(deps))
    view = await uc.get_posting(db, actor, r.posting_id)
    done = view.runs[0]
    assert done.id == new_run and done.status == "done" and view.latest.adapter == "pdf"
    assert "vision_confirmed" in done.notes and "pdf_rendered:4/8" in done.notes
    ocr = [q for q in env["model"].requests if q.images]
    assert len(ocr) == 1 and len(ocr[0].images) == 4
    assert view.runs[1].confirmation.status == "used"

    # 다른 파일(내용이 바뀜)은 확인을 다시 받는다 — 확인은 한 번만 쓴다
    env["site"].routes[("scan.fake.test", "/long8.pdf")] = (
        200,
        {},
        "application/pdf",
        mixed_pdf("xxxxxxxx"),
    )
    await uc.refetch(db, actor, r.posting_id)
    await run_until_idle(db, posting_handlers(deps))
    view = await uc.get_posting(db, actor, r.posting_id)
    assert view.runs[0].status == "needs_confirmation"
    assert view.runs[0].confirmation.pages == [1, 2, 3, 8]
    assert len([q for q in env["model"].requests if q.images]) == 1


@needs_renderer
async def test_declined_or_cheap_scans(db, env, tmp_path):
    from decimal import Decimal

    actor = env["actor"]
    deps = metered_deps(db, env, tmp_path, Decimal("0.01"))
    [r] = await uc.save_urls(db, actor, ["https://scan.fake.test/long.pdf"])
    await run_until_idle(db, posting_handlers(deps))
    run = (await uc.get_posting(db, actor, r.posting_id)).runs[0]
    assert run.status == "needs_confirmation"
    assert await uc.decide_fetch_confirmation(db, actor, r.posting_id, run.id, accept=False) is None
    run = (await uc.get_posting(db, actor, r.posting_id)).runs[0]
    assert (run.status, run.error_code) == ("failed", "needs_manual_check")
    assert run.notes[-1] == "vision_declined" and run.confirmation.status == "declined"
    assert env["model"].requests == []

    # 상한 아래면 묻지 않고 읽는다 (예상 비용은 메모에 남는다)
    cheap = metered_deps(db, env, tmp_path, Decimal(5))
    [r2] = await uc.save_urls(db, actor, ["https://scan.fake.test/scan.pdf"])
    await run_until_idle(db, posting_handlers(cheap))
    run = (await uc.get_posting(db, actor, r2.posting_id)).runs[0]
    assert run.status == "done" and run.confirmation is None
    assert any(n.startswith("vision_estimate_usd:0.0") for n in run.notes)
    assert "pdf_pages_read:1,2" in run.notes


@needs_renderer
async def test_subscription_endpoints_do_not_ask(db, env):
    # 구독 프록시는 쓴 만큼 돈이 나가지 않는다: 확인 없이 읽고 정가 환산 예상만 남긴다
    view, deps, _ = await fetch(db, env, "https://scan.fake.test/long.pdf")
    assert deps.vision_confirm_usd is not None
    assert view.runs[0].status == "done"
    assert any(n.startswith("vision_estimate_usd:") for n in view.runs[0].notes)


@needs_renderer
async def test_confirmation_routes(db, env, tmp_path):
    from decimal import Decimal

    from career_core.interfaces.http import CoreDeps, build_router
    from fastapi import FastAPI

    actor = env["actor"]
    deps = metered_deps(db, env, tmp_path, Decimal("0.01"))
    [r] = await uc.save_urls(db, actor, ["https://scan.fake.test/long.pdf"])
    await run_until_idle(db, posting_handlers(deps))
    run_id = (await uc.get_posting(db, actor, r.posting_id)).runs[0].id

    class Tokens:
        async def resolve(self, credential):
            from career_core.domain.workspace import Role, scopes_for_role

            if credential == "viewer":
                return ActorContext(actor.subject_id, actor.workspace_id, frozenset({"read"}))
            if credential == "owner":
                return ActorContext(
                    actor.subject_id, actor.workspace_id, scopes_for_role(Role.OWNER)
                )
            return None

    app = FastAPI()
    app.include_router(build_router(CoreDeps(identity=Tokens(), db=db)), prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://t") as c:
        base = f"/api/core/job-postings/{r.posting_id}"
        detail = (await c.get(base, headers={"Authorization": "Bearer viewer"})).json()
        conf = detail["runs"][0]["confirmation"]
        assert conf["status"] == "pending" and conf["max_usd"] and conf["pages"] == [1, 2, 3, 6]
        url = f"{base}/fetch-runs/{run_id}/confirm"
        assert (await c.post(url, headers={"Authorization": "Bearer viewer"})).status_code == 403
        res = await c.post(url, headers={"Authorization": "Bearer owner"})
        assert res.status_code == 202 and res.json()["status"] == "pending"
        again = await c.post(url, headers={"Authorization": "Bearer owner"})
        assert (again.status_code, again.json()["detail"]) == (409, "confirmation_not_pending")
        missing = f"{base}/fetch-runs/{r.posting_id}/decline"
        assert (await c.post(missing, headers={"Authorization": "Bearer owner"})).status_code == 404


# --- c0025: 그리팅 선택형 문항 ------------------------------------------------------------


async def test_greeting_choice_questions_become_a_separate_field(db, env):
    view, _, _ = await fetch(
        db, env, "https://fakeco.career.greetinghr.com/ko/o/7001", vision=False
    )
    v = view.latest
    choices = v.extracted["application_questions"]
    assert choices["value"] == [
        {
            "prompt": "이 공고를 처음 본 곳을 골라 주세요",
            "required": True,
            "options": ["합성 채널 A", "합성 채널 B"],
            "source_type": 400,
        },
        {
            "prompt": "합성 질문: 해외 근무가 가능한가요?",
            "required": True,
            "options": ["예", "아니요"],
            "source_type": 200,
        },
    ]
    assert choices["evidence"] == {
        "kind": "structured",
        "ref": "greeting:opening.questionnairesInfo[0,2].question·need·optionItems",
    }
    # 자기소개서 문항에는 섞이지 않는다
    assert next(q["prompt"] for q in v.extracted["questions"]["value"]).startswith("지원 동기")
    # 확정하면 그대로 저장되고, 사용자가 고칠 수도 있다 (모양 검사)
    await uc.confirm_posting(db, env["actor"], view.id)
    saved = await uc.get_posting(db, env["actor"], view.id)
    assert saved.fields["application_questions"][1]["options"] == ["예", "아니요"]
    with pytest.raises(uc.PostingRuleError):
        await uc.confirm_posting(
            db,
            env["actor"],
            view.id,
            overrides={"application_questions": [{"prompt": "x", "options": [1]}]},
        )
    await uc.confirm_posting(
        db,
        env["actor"],
        view.id,
        overrides={
            "application_questions": [{"prompt": " 합성  질문 ", "options": ["가", " 나 "]}]
        },
    )
    saved = await uc.get_posting(db, env["actor"], view.id)
    assert saved.fields["application_questions"] == [
        {"prompt": "합성 질문", "required": None, "options": ["가", "나"], "source_type": None}
    ]
    assert saved.evidence["application_questions"]["kind"] == "user"


def test_greeting_choices_ignore_bad_shapes():
    from career_core.adapters.ats import _greeting_choices

    data = {
        "questionnairesInfo": [
            {"type": 200, "question": "보기 없음", "optionItems": []},
            {"type": 400, "question": "  ", "optionItems": [{"item": "가"}]},
            {"type": 100, "question": "서술형", "optionItems": [{"item": "가"}]},
            {"type": True, "question": "불 type", "optionItems": [{"item": "가"}]},
            {
                "type": 200,
                "question": "합성 선택",
                "need": "yes",
                "optionItems": [{"item": " 가 "}, {"id": 3}, None, "나", {"item": ""}],
            },
            {"type": 400, "question": "items만", "items": [{"item": "다"}]},
        ]
    }
    assert _greeting_choices(data) == (
        [
            {"prompt": "합성 선택", "required": None, "options": ["가", "나"], "source_type": 200},
            {"prompt": "items만", "required": None, "options": ["다"], "source_type": 400},
        ],
        [4, 5],
    )


async def test_llm_never_fills_structured_only_fields(db, env):
    # 모델이 선택형 문항을 내도 받지 않는다 (구조화 데이터에서만)
    out, dropped = uc.verify_llm_fields(
        {"application_questions": [{"prompt": "합성", "options": ["가"]}]}, "합성 본문"
    )
    assert out == {} and dropped == 0


# --- c0025: 모르는 서비스의 봇 확인 화면 ------------------------------------------------------

UNKNOWN_CHALLENGE = (
    "<html><head><title>One moment</title></head><body>"
    "<script>document.cookie='vfy=1;path=/';setTimeout(function(){location.reload()},900);</script>"
    "</body></html>"
)
CAPTCHA_PAGE = (
    '<html><body><h1>Security check</h1><div class="g-recaptcha" data-sitekey="synthetic"></div>'
    '<script src="https://www.example.test/recaptcha/api.js"></script></body></html>'
)
HUMAN_CHECK_KO = (
    "<html><body><p>비정상적인 트래픽이 감지되었습니다. 자동 입력 방지 문자를 입력해 주세요.</p>"
    "<script src='/assets/guard.js'></script></body></html>"
)
SPA_SHELL = (
    "<html><head><script src='/static/app.js'></script></head><body><div id='root'></div>"
    "<noscript>You need to enable JavaScript to run this app.</noscript></body></html>"
)
NORMAL_WITH_CAPTCHA_SCRIPT = (
    "<html><body><h1>합성 백엔드 개발자</h1>"
    + "<p>합성 회사의 주문 시스템을 함께 만들 개발자를 찾습니다. 담당 업무와 자격 요건은 아래와 같습니다.</p>"
    * 4
    + "<script src='https://www.example.test/recaptcha/api.js'></script></body></html>"
)


def test_unknown_challenge_signs():
    from career_core.adapters.web_fetch import challenge_signs

    assert challenge_signs(429, UNKNOWN_CHALLENGE) == ("js_challenge", "tiny_body_script")
    assert challenge_signs(200, UNKNOWN_CHALLENGE) == ("js_challenge",)
    assert challenge_signs(403, CAPTCHA_PAGE)[0] == "captcha"
    assert challenge_signs(200, CAPTCHA_PAGE) == ("captcha",)
    assert "phrase" in challenge_signs(503, HUMAN_CHECK_KO)
    # 보통의 SPA 껍데기·캡차 스크립트만 있는 보통 페이지·진짜 요청 한도는 확인 화면이 아니다
    assert challenge_signs(200, SPA_SHELL) == ()
    assert challenge_signs(200, NORMAL_WITH_CAPTCHA_SCRIPT) == ()
    assert challenge_signs(429, "Too Many Requests") == ()
    assert challenge_signs(404, CAPTCHA_PAGE) == ()
    # "geolocation =" 같은 대입은 다시 불러오기가 아니다
    assert challenge_signs(200, "<script>document.cookie='a=1'; geolocation = 3;</script>") == ()


async def test_unknown_challenges_say_maybe_blocked_and_are_not_retried(db, env):
    site = env["site"]
    HOSTS["guard.fake.test"] = "93.184.216.40"
    site.routes.update(
        {
            ("guard.fake.test", "/a"): (429, {}, "text/html", UNKNOWN_CHALLENGE),
            ("guard.fake.test", "/b"): (200, {}, "text/html", UNKNOWN_CHALLENGE),
            ("guard.fake.test", "/c"): (403, {}, "text/html", CAPTCHA_PAGE),
            ("guard.fake.test", "/d"): (429, {"retry-after": "5"}, "text/plain", "slow down"),
        }
    )

    class NoBrowser:
        calls = 0

        async def fetch(self, url):
            NoBrowser.calls += 1
            raise AssertionError("확인 화면을 브라우저로 다시 열지 않는다")

    deps = env["make"](vision=False)
    deps = uc.PostingDeps(
        db=db, fetcher=deps.fetcher, gateway=deps.gateway, blobs=deps.blobs, browser=NoBrowser()
    )
    urls = [f"https://guard.fake.test/{p}" for p in "abcd"]
    results = await uc.save_urls(db, env["actor"], urls)
    await run_until_idle(db, posting_handlers(deps))
    runs = [(await uc.get_posting(db, env["actor"], r.posting_id)).runs[0] for r in results]
    assert [r.error_code for r in runs] == [
        "maybe_bot_blocked",
        "maybe_bot_blocked",
        "maybe_bot_blocked",
        "rate_limited",
    ]
    assert runs[0].notes == ["bot_block_signs:js_challenge,tiny_body_script"]
    assert runs[2].notes == ["bot_block_signs:captcha,tiny_body_script"]
    assert NoBrowser.calls == 0 and env["model"].requests == []
    # 요청은 URL마다 한 번 (다시 시도·우회 없음)
    paths = [p for h, p, _, _ in site.requests if h == "guard.fake.test"]
    assert sorted(paths) == ["/a", "/b", "/c", "/d"]

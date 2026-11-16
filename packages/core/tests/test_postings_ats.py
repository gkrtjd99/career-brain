"""공고 백로그: 채용 시스템 어댑터, PDF·이미지 공고, 추출 품질 (모든 페이지·JSON은 합성).

실제 회사 페이지를 커밋하지 않는다. 구조만 흉내 낸 가짜 데이터다 (회사·사람·주소 모두 가상).
"""

import json
from html import escape

import httpx
import pytest
from career_core.adapters import ats
from career_core.adapters.html_extract import extract_page
from career_core.adapters.web_fetch import AddressBlocked, HttpFetcher, sniff_binary
from career_core.db import ModelCall
from career_core.domain.postings import (
    ats_request_allowed,
    classify_heading,
)
from career_core.jobs.worker import posting_handlers, run_until_idle
from career_core.llm import ModelCompletion, TokenUsage, UsagePart
from career_core.llm.config import parse_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook
from career_core.ports import ActorContext
from career_core.testing import make_pdf
from career_core.usecases import postings as uc
from career_core.usecases.workspaces import create_workspace
from sqlalchemy import select

pytestmark = pytest.mark.anyio


def _script_json(value) -> str:
    """페이지에 박힌 JSON처럼 `<`를 이스케이프한다."""
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


# --- 합성 fixture ---------------------------------------------------------------------

GH_CONTENT = (
    "<p>가상 그린하우스는 합성 데이터로 만든 회사입니다.</p>"
    "<p><strong>What You'll Do</strong></p><ul><li>Build the fake payments API</li>"
    "<li>Operate batch jobs</li></ul>"
    "<p><strong>What you should have</strong></p><ul><li>3+ years of Python</li>"
    "<li><strong>SQL</strong> fluency</li></ul>"
    "<p>&nbsp;<strong>Bonus points</strong></p><ul><li>Kafka experience</li></ul>"
    "<p>We offer synthetic benefits to everyone.</p>"
)
GREENHOUSE_PAGE = (
    "<html><head><title>Job Application for Backend Engineer at 가상 그린하우스</title></head>"
    "<body><div id='app'><h1>Backend Engineer</h1><p>Apply now</p></div><script>"
    "window.__remixContext = "
    + _script_json(
        {
            "state": {
                "loaderData": {
                    "root": {"locale": "en"},
                    "routes/$url_token_.jobs_.$job_post_id": {
                        "jobPost": {
                            "title": "Backend Engineer",
                            "company_name": "가상 그린하우스",
                            "job_post_location": "Seoul, Korea",
                            "published_at": "2026-11-02T09:00:00-05:00",
                            "application_deadline": "2026-12-15",
                            "introduction": "<p>Synthetic intro paragraph.</p>",
                            "content": GH_CONTENT,
                            "conclusion": "<p>Synthetic closing paragraph.</p>",
                            "public_url": "https://job-boards.greenhouse.io/fakeboard/jobs/111",
                        },
                        "jobPostId": "111",
                    },
                }
            }
        }
    )
    + ";</script></body></html>"
)

GREETING_DETAIL = (
    "<h2><strong>담당 업무</strong></h2><ul><li>합성 주문 시스템 개발</li><li>배치 운영</li></ul>"
    "<h2><strong>자격 요건</strong></h2><p><strong>[경험]</strong></p>"
    "<ul><li>백엔드 개발 경력 3년 이상</li></ul><p><strong>[스킬]</strong></p>"
    "<ul><li>Python 또는 Kotlin</li></ul>"
    "<h2><strong>우대 사항</strong></h2><ul><li>대용량 트래픽 경험</li></ul>"
    "<h2><strong>합류 여정</strong></h2><ul><li>서류 → 과제 → 인터뷰 → 최종 합격</li></ul>"
    "<h2><strong>꼭 확인해주세요!</strong></h2><ul><li>합성 안내 문구입니다.</li></ul>"
)
GREETING_PAGE = (
    "<html><head><meta property='og:title' content='백엔드 개발자'></head><body>"
    "<div id='__next'><p>백엔드 개발자</p><p>마감기한 D-30</p></div>"
    "<script id='__NEXT_DATA__' type='application/json'>"
    + _script_json(
        {
            "props": {
                "pageProps": {
                    "dehydratedState": {
                        "queries": [
                            {"queryKey": ["publicCareer", "getCareerBaseInfo", "fakeco"]},
                            {
                                "queryKey": [
                                    "career",
                                    "getOpeningById",
                                    {"openingId": 9001, "workspaceId": 1},
                                ],
                                "state": {
                                    "data": {
                                        "success": True,
                                        "data": {
                                            "groupInfo": {"name": "가상그리팅 채용"},
                                            "openingsInfo": {
                                                "openingId": 9001,
                                                "title": "백엔드 개발자",
                                                "detail": GREETING_DETAIL,
                                                "openDate": "2026-11-01T00:00:00Z",
                                                "dueDate": "2026-11-30T14:59:59Z",
                                                "deadlineDDay": -30,
                                            },
                                            "jobPositionSetting": {
                                                "jobPositions": [
                                                    {
                                                        "workspacePlace": {
                                                            "location": "가상동",
                                                            "place": "서울특별시 가상구 합성로 1",
                                                        },
                                                        "jobPositionCareer": {
                                                            "careerFrom": 3,
                                                            "careerTo": None,
                                                            "careerType": "EXPERIENCED",
                                                        },
                                                        "jobPositionEmployment": {
                                                            "employmentType": "FULL_TIME_WORKER"
                                                        },
                                                    }
                                                ]
                                            },
                                        },
                                    }
                                },
                            },
                        ]
                    }
                }
            }
        }
    )
    + "</script></body></html>"
)

LEVER_ID = "0f0e0d0c-0b0a-4999-8888-777766665555"
LEVER_PAGE = (
    "<html><head><title>가상레버 - Data Engineer</title>"
    '<script type="application/ld+json">'
    + _script_json(
        {
            "@context": "http://schema.org",
            "@type": "JobPosting",
            "title": "Data Engineer",
            "hiringOrganization": {"@type": "Organization", "name": "가상레버"},
            "jobLocation": {"address": {"addressLocality": "Seoul", "addressRegion": None}},
            "employmentType": "Full-time",
            "datePosted": "2026-11-03",
            "description": "<p>Synthetic description.</p>",
        }
    )
    + "</script></head><body><div class='content'><h2>Data Engineer</h2>"
    + "<p>Synthetic Lever page body for a fake company.</p>" * 6
    + "</div></body></html>"
)
LEVER_API = {
    "id": LEVER_ID,
    "text": "Data Engineer",
    "categories": {
        "commitment": "Full-time",
        "location": "Seoul",
        "allLocations": ["Seoul", "Busan"],
        "team": "Data",
    },
    "createdAt": 1793664000000,  # 2026-11-03 UTC
    "description": "<div><p>We build synthetic pipelines.</p></div>",
    "lists": [
        {"text": "What You'll Do", "content": "<li>Own the fake data lake</li><li>Write ETL</li>"},
        {"text": "Requirements", "content": "<li>Spark or Flink</li><li>SQL</li>"},
        {"text": "Nice to have", "content": "<li>dbt</li>"},
        {"text": "Perks", "content": "<li>Synthetic snacks</li>"},
    ],
    "additional": "<div>Equal opportunity (synthetic).</div>",
    "hostedUrl": f"https://jobs.lever.co/fakeco/{LEVER_ID}",
}

WORKDAY_PATH = "job/Seoul-Korea/Backend-Engineer_R-0001"
WORKDAY_PAGE = (
    "<html><head><title>Backend Engineer</title>"
    '<script type="application/ld+json">'
    + _script_json(
        {
            "@type": "JobPosting",
            "title": "Backend Engineer",
            "hiringOrganization": {"name": "1000 가상워크데이 Korea"},
            "jobLocation": {"address": {"addressLocality": "Seoul, Korea"}},
            "datePosted": "2026-11-04",
            "employmentType": "FULL_TIME",
            # Workday JSON-LD 설명은 줄바꿈이 사라진 한 줄이다 (항목 인용이 어렵다)
            "description": "What you'll be doing: Build APIs Operate services "
            "What we need to see: Java Kubernetes " * 4,
        }
    )
    + "</script></head><body><div id='root'></div>"
    '<script>window.workday = window.workday || { tenant: "fakeco" };</script></body></html>'
)
WORKDAY_API = {
    "jobPostingInfo": {
        "title": "Backend Engineer",
        "jobDescription": "<p>Synthetic team description for a fake company.</p>"
        "<p><b>What you'll be doing:</b></p><ul><li>Build APIs</li><li>Operate services</li></ul>"
        "<p><b>What we need to see:</b></p><ul><li>Java</li><li>Kubernetes</li></ul>"
        "<p><b>Ways to stand out from the crowd:</b></p><ul><li>Go</li></ul>"
        "<p>Synthetic equal opportunity statement that is not a list item.</p>",
        "location": "Seoul, Korea",
        "additionalLocations": ["Busan, Korea"],
        "timeType": "Full time",
        "startDate": "2026-11-04",
        "jobReqId": "R-0001",
    },
    "hiringOrganization": {"name": "1000 가상워크데이 Korea"},
}

COMPANY_SITE_PAGE = (
    "<html><head><title>Careers - 가상회사</title>"
    '<script src="https://boards.greenhouse.io/embed/job_board/js?for=fakeboard"></script>'
    "</head><body><div id='grnhse_app'></div></body></html>"
)
GH_API = {
    "id": 222,
    "title": "Site Reliability Engineer",
    "company_name": "가상회사",
    "location": {"name": "Remote - Korea"},
    "first_published": "2026-11-05T10:00:00-05:00",
    "application_deadline": None,
    "content": escape(
        "<p>가상회사 is a synthetic company used only in tests.</p>"
        "<h3>Responsibilities</h3><ul><li>Keep fake services up</li></ul>"
        "<h3>Qualifications</h3><ul><li>Linux</li><li>Terraform</li></ul>"
    ),
}

WANTED_LIKE_PAGE = (
    "<html><head><title>[가상원티드] 백엔드 개발자 채용 공고 | 가상포털</title>"
    '<script type="application/ld+json">'
    + _script_json(
        {
            "@type": "JobPosting",
            "title": "백엔드 개발자",
            "hiringOrganization": {"name": "가상원티드"},
            "jobLocation": {
                "address": {
                    "addressRegion": "서울",
                    "addressLocality": "가상구",
                    "streetAddress": "서울 가상구 합성로 2",
                }
            },
            "experienceRequirements": ["경력"],
            "employmentType": "정규직",
            "datePosted": "2026-11-06",
            "validThrough": "2026-12-31",
            # 설명에는 주요 업무만 있다
            "description": "• 합성 주문 API 개발과 운영을 담당합니다. 대규모 트래픽 처리를 맡습니다.\n"
            "• 배치 작업 설계와 개선, 장애 대응 자동화를 함께 합니다.",
        }
    )
    + "</script></head><body><main><h2>포지션 상세</h2><h3>주요업무</h3>"
    "<p>• 합성 주문 API 개발과 운영을 담당합니다. 대규모 트래픽 처리를 맡습니다.</p>"
    "<p>• 배치 작업 설계와 개선, 장애 대응 자동화를 함께 합니다.</p>"
    "<h3>자격요건</h3><p>• Python 백엔드 개발 경험 3년 이상이신 분</p>"
    "<p>• 관계형 데이터베이스 설계 경험이 있으신 분</p>"
    "<h3>우대사항</h3><p>• Kafka 운영 경험이 있으신 분</p>"
    "<p>• 합성 공고 본문의 마지막 우대 항목입니다.</p></main></body></html>"
)

QUALITY_PAGE = (
    "<html><head><title>가상기업｜[2026 하반기] SW 개발 직무</title></head><body>"
    "<div class='entry-content'><p>[자격 요건]</p><p>C++ 개발 경험</p><p>Airflow 사용 경험 우대</p>"
    "<p>[우대 사항]</p><p>임베디드 경험</p><p>[근무지] 서울 가상구</p>"
    + "<p>합성 공고 본문입니다.</p>" * 10
    + "</div></body></html>"
)
QUALITY_REPLY = json.dumps(
    {
        "page_type": "posting",
        "fields": {
            "title": {"value": "SW 개발", "quote": "SW 개발 직무"},
            "company": {"value": "가상기업", "quote": "가상기업｜[2026 하반기] SW 개발 직무"},
            "requirements": [
                {"quote": "C++ 개발 경험"},
                {"quote": "Airflow 사용 경험 우대"},
                {"quote": "임베디드 경험"},
            ],
            "preferred": [
                {"quote": "Airflow 사용 경험 우대"},
                {"quote": "임베디드 경험"},
                {"quote": "임베디드 경험"},
            ],
        },
    }
)

PDF_LINES = [
    "Synthetic Posting - Fake Robotics Inc.",
    "Position: Robotics Software Engineer",
    "Requirements: ROS2 experience",
    "Apply by 2026-12-20",
]
PDF_REPLY = json.dumps(
    {
        "page_type": "posting",
        "fields": {
            "title": {
                "value": "Robotics Software Engineer",
                "quote": "Position: Robotics Software Engineer",
            },
            "company": {"value": "Fake Robotics Inc.", "quote": "Fake Robotics Inc."},
            "deadline": {"value": "2026-12-20", "quote": "Apply by 2026-12-20"},
            "requirements": [{"quote": "Requirements: ROS2 experience"}],
        },
    }
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # 머리만 PNG인 합성 바이트 (실제 이미지 아님)
OCR_TEXT = "가상포스터 주식회사\n2026 신입 공개채용\n모집 분야: 데이터 분석\n접수 기간: 2026.11.20 ~ 2026.12.04"
OCR_REPLY = json.dumps(
    {
        "page_type": "posting",
        "fields": {
            "title": {"value": "2026 신입 공개채용", "quote": "2026 신입 공개채용"},
            "company": {"value": "가상포스터 주식회사", "quote": "가상포스터 주식회사"},
            "deadline": {"value": "2026-12-04", "quote": "접수 기간: 2026.11.20 ~ 2026.12.04"},
        },
    }
)

HOSTS = {
    "job-boards.greenhouse.io": "93.184.216.1",
    "fakeco.career.greetinghr.com": "93.184.216.2",
    "jobs.lever.co": "93.184.216.3",
    "api.lever.co": "93.184.216.4",
    "fakeco.wd1.myworkdayjobs.com": "93.184.216.5",
    "careers.fake.test": "93.184.216.6",
    "boards-api.greenhouse.io": "93.184.216.7",
    "portal.fake.test": "93.184.216.8",
    "board.fake.test": "93.184.216.9",
    "pdf.fake.test": "93.184.216.10",
    "image.fake.test": "93.184.216.11",
    "internal.fake.test": "10.0.0.9",
}


class FakeSite:
    def __init__(self):
        self.requests: list[tuple[str, str, str, str]] = []
        self.lever_api_status = 200
        self.routes = {
            ("job-boards.greenhouse.io", "/fakeboard/jobs/111"): ("text/html", GREENHOUSE_PAGE),
            ("fakeco.career.greetinghr.com", "/ko/o/9001"): ("text/html", GREETING_PAGE),
            ("jobs.lever.co", f"/fakeco/{LEVER_ID}"): ("text/html", LEVER_PAGE),
            ("api.lever.co", f"/v0/postings/fakeco/{LEVER_ID}"): (
                "application/json",
                json.dumps(LEVER_API),
            ),
            ("fakeco.wd1.myworkdayjobs.com", f"/en-US/External/{WORKDAY_PATH}"): (
                "text/html",
                WORKDAY_PAGE,
            ),
            ("fakeco.wd1.myworkdayjobs.com", f"/wday/cxs/fakeco/External/{WORKDAY_PATH}"): (
                "application/json",
                json.dumps(WORKDAY_API),
            ),
            ("careers.fake.test", "/jobs/sre"): ("text/html", COMPANY_SITE_PAGE),
            ("boards-api.greenhouse.io", "/v1/boards/fakeboard/jobs/222"): (
                "application/json",
                json.dumps(GH_API),
            ),
            ("portal.fake.test", "/wd/1"): ("text/html", WANTED_LIKE_PAGE),
            ("board.fake.test", "/archives/1"): ("text/html", QUALITY_PAGE),
            ("pdf.fake.test", "/posting.pdf"): ("application/pdf", make_pdf(PDF_LINES)),
            ("pdf.fake.test", "/scan.pdf"): ("application/pdf", make_pdf(["", ""])),
            ("pdf.fake.test", "/download"): ("application/octet-stream", make_pdf(PDF_LINES)),
            ("pdf.fake.test", "/fake.pdf"): ("application/pdf", "<html>not a pdf</html>"),
            ("image.fake.test", "/poster.png"): ("image/png", PNG),
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        self.requests.append(
            (host, request.url.path, request.headers.get("cookie", ""), request.headers["accept"])
        )
        assert request.url.host == HOSTS[host]  # 검사한 IP로만 접속
        assert request.extensions.get("sni_hostname") == host
        if host == "api.lever.co" and self.lever_api_status != 200:
            return httpx.Response(self.lever_api_status, json={"ok": False})
        route = self.routes.get((host, request.url.path))
        if route is None:
            return httpx.Response(404, html="not found")
        ctype, body = route
        content = body if isinstance(body, bytes) else body.encode()
        return httpx.Response(200, content=content, headers={"content-type": ctype})


async def fake_resolve(host, port):
    ip = HOSTS.get(host)
    if ip is None:
        raise ConnectionError("dns_failed")
    if ip.startswith("10."):
        raise AddressBlocked(host)
    return ip


class ScriptedModel:
    """이미지가 붙은 요청에는 OCR 글을, 아니면 추출 JSON을 돌려준다. 요청을 기록한다."""

    def __init__(self, reply: str, ocr: str = OCR_TEXT):
        self.reply, self.ocr = reply, ocr
        self.requests = []

    async def complete(self, model, request):
        self.requests.append(request)
        text = self.ocr if request.images else self.reply
        return ModelCompletion(
            text=text,
            model=model,
            stop_reason="stop",
            parts=(UsagePart(model, TokenUsage(input_tokens=100, output_tokens=50)),),
        )


def _config(vision: bool):
    extra = 'job_extract_image = "proxy/gpt-6.1-sol"\n' if vision else ""
    return parse_models_config(
        '[endpoints.proxy]\npreset = "cliproxyapi"\nprice_as = "openai"\n'
        'data_classes = ["public"]\n[routes]\ndefault = "proxy/gpt-6.1-sol"\n' + extra
    )


@pytest.fixture
async def env(db, tmp_path):
    from career_core.adapters.blobstore import LocalBlobStore

    ws, subject = await create_workspace(db, name="A", owner_display_name="alice")
    site = FakeSite()
    model = ScriptedModel(QUALITY_REPLY)

    def make(vision=False, reply=None):
        if reply is not None:
            model.reply = reply
        gw = ModelGateway(
            db,
            _config(vision),
            PriceBook.load(),
            client_factory=lambda ep, key: model,
            env={"CLIPROXY_API_KEY": "k"},
        )
        fetcher = HttpFetcher(
            transport=httpx.MockTransport(site.handler), resolver=fake_resolve, max_bytes=1 << 20
        )
        return uc.PostingDeps(db=db, fetcher=fetcher, gateway=gw, blobs=LocalBlobStore(tmp_path))

    return {"actor": ActorContext(subject, ws), "site": site, "model": model, "make": make}


async def fetch_one(db, env, url, **kw):
    deps = env["make"](**kw)
    [r] = await uc.save_urls(db, env["actor"], [url])
    await run_until_idle(db, posting_handlers(deps))
    return await uc.get_posting(db, env["actor"], r.posting_id), deps, r


def values(view):
    return {k: v["value"] for k, v in view.latest.extracted.items()}


def refs(view):
    return {k: v["evidence"].get("ref") for k, v in view.latest.extracted.items()}


# --- 채용 시스템 어댑터 ---------------------------------------------------------------


async def test_greenhouse_hosted_page(db, env):
    view, _, _ = await fetch_one(db, env, "https://job-boards.greenhouse.io/fakeboard/jobs/111")
    v = view.latest
    assert v.adapter == "greenhouse" and v.method == "http"
    got = values(view)
    assert got["title"] == "Backend Engineer" and got["company"] == "가상 그린하우스"
    assert got["location"] == "Seoul, Korea"
    assert got["deadline"] == "2026-12-15"  # 날짜만이면 날짜만 (자정으로 바꾸지 않는다)
    assert got["posted_at"] == "2026-11-02T09:00:00-05:00"
    assert got["responsibilities"] == ["Build the fake payments API", "Operate batch jobs"]
    assert got["requirements"] == [
        "3+ years of Python",
        "SQL fluency",
    ]  # 항목 안 굵은 글씨는 제목 아님
    assert got["preferred"] == ["Kafka experience"]  # 목록 뒤 일반 문단(복지)은 섹션 밖
    assert refs(view)["title"] == "greenhouse:jobPost.title"
    assert refs(view)["requirements"] == "greenhouse:jobPost.content § What you should have"
    full = await uc.get_version(db, env["actor"], view.id, v.id)
    assert "Synthetic intro paragraph." in full.text and "Kafka experience" in full.text
    # 페이지 어댑터가 찾았으면 API를 부르지 않는다
    assert {h for h, *_ in env["site"].requests} == {"job-boards.greenhouse.io"}


async def test_greeting_page(db, env):
    view, _, _ = await fetch_one(db, env, "https://fakeco.career.greetinghr.com/ko/o/9001")
    assert view.latest.adapter == "greeting"
    got = values(view)
    assert got["title"] == "백엔드 개발자" and got["company"] == "가상그리팅 채용"
    assert got["deadline"] == "2026-11-30T14:59:59+00:00"
    assert got["location"] == "서울특별시 가상구 합성로 1"
    assert (got["employment_type"], got["career_level"]) == ("정규직", "경력 3년 이상")
    # "자격 요건" 아래 소제목([경험]·[스킬])은 같은 섹션
    assert got["requirements"] == ["백엔드 개발 경력 3년 이상", "Python 또는 Kotlin"]
    assert got["process"] == ["서류 → 과제 → 인터뷰 → 최종 합격"]
    listed = [item for k in uc.LIST_FIELDS if k in got for item in got[k]]
    assert "합성 안내 문구입니다." not in listed
    assert refs(view)["career_level"].startswith("greeting:opening.jobPositionSetting")
    full = await uc.get_version(db, env["actor"], view.id, view.latest.id)
    assert "D-30" not in full.text  # 매일 바뀌는 D-day는 본문 스냅샷에 넣지 않는다


async def test_lever_uses_public_api_and_json_ld_for_company(db, env):
    view, _, _ = await fetch_one(db, env, f"https://jobs.lever.co/fakeco/{LEVER_ID}")
    assert view.latest.adapter == "lever"
    got = values(view)
    assert got["title"] == "Data Engineer"
    assert got["company"] == "가상레버"  # API에는 없어 페이지 JSON-LD가 채운다
    assert refs(view)["company"] == "ld:JobPosting.hiringOrganization.name"
    assert got["location"] == "Seoul, Busan" and got["employment_type"] == "Full-time"
    assert got["posted_at"] == "2026-11-03"
    assert got["responsibilities"] == ["Own the fake data lake", "Write ETL"]
    assert got["requirements"] == ["Spark or Flink", "SQL"]
    assert got["preferred"] == ["dbt"]
    assert refs(view)["requirements"] == "lever:posting.lists[1]"
    api = [r for r in env["site"].requests if r[0] == "api.lever.co"]
    assert api and api[0][3] == "application/json"
    assert all(cookie == "" for _, _, cookie, _ in env["site"].requests)  # 쿠키 없음


async def test_lever_api_failure_falls_back_to_json_ld(db, env):
    env["site"].lever_api_status = 404
    view, _, _ = await fetch_one(db, env, f"https://jobs.lever.co/fakeco/{LEVER_ID}")
    assert view.latest.adapter == "json_ld"
    assert values(view)["company"] == "가상레버"
    assert "ats_api_failed:lever:source_unavailable" in view.runs[0].notes


async def test_workday_cxs_api_on_same_host(db, env):
    view, _, _ = await fetch_one(
        db, env, f"https://fakeco.wd1.myworkdayjobs.com/en-US/External/{WORKDAY_PATH}"
    )
    assert view.latest.adapter == "workday"
    got = values(view)
    assert got["location"] == "Seoul, Korea, Busan, Korea"
    assert (got["employment_type"], got["posted_at"]) == ("Full time", "2026-11-04")
    assert got["responsibilities"] == ["Build APIs", "Operate services"]
    assert got["requirements"] == ["Java", "Kubernetes"]
    assert got["preferred"] == ["Go"]
    full = await uc.get_version(db, env["actor"], view.id, view.latest.id)
    assert (
        "Build APIs\nOperate services" in full.text
    )  # JSON-LD 한 줄 설명 대신 줄이 살아 있는 본문
    hosts = [(h, p) for h, p, _, _ in env["site"].requests]
    assert ("fakeco.wd1.myworkdayjobs.com", f"/wday/cxs/fakeco/External/{WORKDAY_PATH}") in hosts


async def test_greenhouse_embed_on_company_site(db, env):
    view, _, _ = await fetch_one(db, env, "https://careers.fake.test/jobs/sre?gh_jid=222")
    assert view.latest.adapter == "greenhouse"
    got = values(view)
    assert (got["title"], got["company"]) == ("Site Reliability Engineer", "가상회사")
    assert got["location"] == "Remote - Korea"
    assert got["requirements"] == ["Linux", "Terraform"]
    assert refs(view)["title"] == "greenhouse_api:job.title"


def test_ats_request_rules():
    page = f"https://jobs.lever.co/fakeco/{LEVER_ID}"
    assert ats_request_allowed(page, "https://api.lever.co/v0/postings/fakeco/x")
    assert ats_request_allowed("https://a.myworkdayjobs.com/x", "https://a.myworkdayjobs.com/wday")
    for bad in (
        "http://api.lever.co/v0/postings/a/b",  # https만
        "https://api.lever.co:8443/v0/postings/a/b",  # 다른 포트
        "https://evil.fake.test/v0",  # 목록에 없는 다른 호스트
        "https://user:pw@api.lever.co/x",
        "https://b.myworkdayjobs.com/wday",  # 같은 사이트라도 다른 호스트
    ):
        assert not ats_request_allowed(page, bad), bad
    # 임베드 스크립트의 보드 이름으로 호스트·경로를 바꿀 수 없다
    p = extract_page(
        '<script src="https://boards.greenhouse.io/embed/job_board/js?for=../../x"></script>',
        "https://careers.fake.test/j?gh_jid=1",
    )
    assert ats.greenhouse_api_url(p, "https://careers.fake.test/j?gh_jid=1") is None
    assert ats.greenhouse_api_url(p, "https://careers.fake.test/j?gh_jid=abc") is None
    assert ats.lever_api_url(None, "https://jobs.lever.co/fakeco") is None  # 회사 목록은 공고 아님
    assert ats.workday_api_url(None, "https://a.wd1.myworkdayjobs.com/en-US/Ext") is None
    assert (
        ats.workday_api_url(None, "https://a.wd1.myworkdayjobs.com/Ext/job/Seoul/X_R1")
        == "https://a.wd1.myworkdayjobs.com/wday/cxs/a/Ext/job/Seoul/X_R1"
    )


async def test_ats_api_never_reaches_internal_hosts(db, env):
    """임베드가 가리키는 보드가 있어도 요청 호스트는 고정이고, DNS 검사도 같은 fetcher가 한다."""
    HOSTS["boards-api.greenhouse.io"] = "10.0.0.7"  # 내부 주소로 풀리는 상황
    try:
        view, _, _ = await fetch_one(db, env, "https://careers.fake.test/jobs/sre?gh_jid=222")
    finally:
        HOSTS["boards-api.greenhouse.io"] = "93.184.216.7"
    assert "ats_api_failed:greenhouse:blocked_address" in view.runs[0].notes
    assert "boards-api.greenhouse.io" not in {h for h, *_ in env["site"].requests}


async def test_json_ld_quirks_and_partial_description(db, env):
    view, _, _ = await fetch_one(db, env, "https://portal.fake.test/wd/1")
    v = view.latest
    assert v.adapter == "json_ld"
    got = values(view)
    assert got["location"] == "서울 가상구 합성로 2"  # 도로명에 이미 있는 지역을 겹쳐 쓰지 않는다
    assert got["career_level"] == "경력"
    assert got["deadline"] == "2026-12-31" and got["posted_at"] == "2026-11-06"
    # JSON-LD 설명에는 주요 업무만 있어 본문은 페이지에서 가져온다 (자격·우대까지)
    full = await uc.get_version(db, env["actor"], view.id, v.id)
    assert "Kafka 운영 경험이 있으신 분" in full.text


# --- 섹션 나누기 -----------------------------------------------------------------------


def test_sections_from_html_rules():
    html = (
        "<h3>[담당 업무]</h3><p>• 합성 API 개발</p><p>- 운영 자동화</p>"
        "<h3>회사 소개</h3><p>합성 회사 소개 문단</p>"
        "<p>자격요건</p><ul><li>Go 경험</li></ul><p>목록 뒤 문단</p>"
        "<p>Airflow 경험 우대</p>"
    )
    out = ats.sections_from_html(html, "x")
    assert out["responsibilities"]["value"] == ["합성 API 개발", "운영 자동화"]
    assert out["requirements"]["value"] == ["Go 경험"]
    assert "preferred" not in out  # "Airflow 경험 우대"는 항목 줄이지 제목이 아니다
    assert classify_heading("Airflow 경험 우대") is None
    assert classify_heading("우대 사항") == "preferred"
    assert classify_heading("Preferred Qualifications", certain=True) == "preferred"
    assert classify_heading("Basic Qualifications", certain=True) == "requirements"


# --- 추출 품질 ------------------------------------------------------------------------


async def test_llm_dedupes_lists_and_prefers_fuller_title(db, env):
    view, _, _ = await fetch_one(db, env, "https://board.fake.test/archives/1")
    got = values(view)
    # 페이지 제목 "가상기업｜[2026 하반기] SW 개발 직무"가 뒷받침하는 더 긴 제목
    assert got["title"] == "[2026 하반기] SW 개발 직무"
    assert view.latest.extracted["title"]["evidence"]["kind"] == "quote"
    # 두 목록에 같은 줄: 줄 자체의 "우대" → 우대, 그다음은 앞의 섹션 제목
    assert got["requirements"] == ["C++ 개발 경험"]
    assert got["preferred"] == ["Airflow 사용 경험 우대", "임베디드 경험"]
    assert "llm_deduped:3" in view.runs[0].notes


def test_fuller_title_keeps_quote_rules():
    text = "가상기업｜[2026 하반기] SW 개발 직무\n본문"
    entry = {"value": "SW 개발", "evidence": {"kind": "quote", "ref": "SW 개발"}}
    wider = uc.fuller_title(entry, text, "가상기업｜[2026 하반기] SW 개발 직무", "가상기업")
    assert wider["value"] == "[2026 하반기] SW 개발 직무"
    assert text[wider["evidence"]["start"] : wider["evidence"]["end"]] == wider["value"]
    # 본문에 없는 페이지 제목은 근거가 될 수 없다
    assert uc.fuller_title(entry, "본문만", "가상기업｜[2026 하반기] SW 개발 직무", None) == entry
    # 회사·꼬리말("채용 공고")만 다르면 넓히지 않는다
    same = {"value": "백엔드 개발자", "evidence": {}}
    assert (
        uc.fuller_title(
            same, "[가상] 백엔드 개발자 채용 공고", "[가상] 백엔드 개발자 채용 공고", "가상"
        )
        == same
    )


# --- PDF ----------------------------------------------------------------------------


async def test_pdf_posting_goes_through_text_path(db, env):
    view, _, _ = await fetch_one(db, env, "https://pdf.fake.test/posting.pdf", reply=PDF_REPLY)
    v = view.latest
    assert (v.method, v.adapter) == ("http", "pdf")
    got = values(view)
    assert got["title"] == "Robotics Software Engineer"
    assert got["deadline"] == "2026-12-20"
    assert v.extracted["deadline"]["evidence"]["kind"] == "quote"
    assert "pdf_pages:4/4" in view.runs[0].notes
    req = env["model"].requests[-1]
    assert req.data_classes == frozenset({"public"}) and not req.images


async def test_pdf_edge_cases(db, env):
    deps = env["make"](reply=PDF_REPLY)
    results = await uc.save_urls(
        db,
        env["actor"],
        [
            "https://pdf.fake.test/scan.pdf",
            "https://pdf.fake.test/download",
            "https://pdf.fake.test/fake.pdf",
        ],
    )
    await run_until_idle(db, posting_handlers(deps))
    views = [await uc.get_posting(db, env["actor"], r.posting_id) for r in results]
    scan, octet, fake = views
    assert (scan.runs[0].error_code, scan.runs[0].notes) == ("needs_manual_check", ["pdf_no_text"])
    assert octet.latest.adapter == "pdf"  # octet-stream이어도 파일 머리가 PDF면 받는다
    assert fake.runs[0].error_code == "unsupported_content"  # 선언과 내용이 다르면 거부
    assert "deadline" not in scan.fields and "deadline" not in fake.fields


async def test_binary_size_cap_still_applies():
    big = make_pdf(["x" * 50] * 30)

    def handler(request):
        return httpx.Response(200, content=big, headers={"content-type": "application/pdf"})

    fetcher = HttpFetcher(
        transport=httpx.MockTransport(handler), resolver=fake_resolve, max_bytes=len(big) - 1
    )
    result = await fetcher.fetch("https://pdf.fake.test/posting.pdf")
    assert result.code == "too_large"
    assert sniff_binary(PNG) == "image/png" and sniff_binary(b"<html>") is None


# --- 이미지 -------------------------------------------------------------------------


async def test_image_without_vision_route_needs_manual_check(db, env):
    view, _, _ = await fetch_one(db, env, "https://image.fake.test/poster.png", reply=OCR_REPLY)
    run = view.runs[0]
    assert (run.status, run.error_code) == ("failed", "needs_manual_check")
    assert run.notes == ["vision_route_missing"]
    assert env["model"].requests == []  # default 라우트로 넘어가지 않는다


async def test_image_with_vision_route(db, env):
    view, deps, r = await fetch_one(
        db, env, "https://image.fake.test/poster.png", vision=True, reply=OCR_REPLY
    )
    v = view.latest
    assert (v.method, v.adapter, v.has_screenshot) == ("http", "image", True)
    got = values(view)
    assert got["company"] == "가상포스터 주식회사"
    assert got["deadline"] == "2026-12-04"
    assert "ocr_by_model" in view.runs[0].notes
    ocr, extract = env["model"].requests
    assert ocr.images[0].media_type == "image/png" and ocr.images[0].data == PNG
    assert ocr.data_classes == frozenset({"public"}) and extract.data_classes == frozenset(
        {"public"}
    )
    async with db.workspace_session(env["actor"].workspace_id) as s:
        purposes = sorted((await s.execute(select(ModelCall.purpose))).scalars())
    assert purposes == ["job_extract", "job_extract_image"]
    # 같은 이미지를 다시 가져오면 모델을 부르지 않고 버전도 늘지 않는다
    await uc.refetch(db, env["actor"], r.posting_id)
    await run_until_idle(db, posting_handlers(deps))
    assert len(env["model"].requests) == 2
    assert await uc.count_versions(db, env["actor"], r.posting_id) == 1
    assert (await uc.get_posting(db, env["actor"], r.posting_id)).runs[0].unchanged


# --- 이미지 입력 모양 (제공자 어댑터) -----------------------------------------------------


async def test_provider_adapters_send_images():
    import anthropic
    import httpx2
    import openai
    from career_core.adapters.llm_anthropic import AnthropicModelClient
    from career_core.adapters.llm_openai import OpenAICompatibleClient
    from career_core.llm import ChatMessage, ImageInput, ModelRequest

    cfg = parse_models_config(
        '[endpoints.oa]\npreset="openai"\n[endpoints.cl]\npreset="anthropic"\n'
    )
    req = ModelRequest(
        messages=(ChatMessage("user", "read"),),
        data_classes=frozenset({"public"}),
        images=(ImageInput("image/png", PNG),),
    )
    seen = []

    def oa(request):
        seen.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 1,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "t"},
                    }
                ],
            },
        )

    def cl(request):
        seen.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "msg",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [{"type": "text", "text": "t"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    http = openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(oa))
    await OpenAICompatibleClient(cfg.endpoints["oa"], "sk", http_client=http).complete("m", req)
    http = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(cl))
    await AnthropicModelClient(cfg.endpoints["cl"], "sk", http_client=http).complete(
        "claude-haiku-5-5", req
    )
    oa_content, cl_content = seen[0]["messages"][-1]["content"], seen[1]["messages"][-1]["content"]
    assert oa_content[0] == {"type": "text", "text": "read"}
    assert oa_content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert cl_content[0]["type"] == "image" and cl_content[0]["source"]["media_type"] == "image/png"
    assert cl_content[-1] == {"type": "text", "text": "read"}

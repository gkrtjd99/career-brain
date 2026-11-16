"""공고 페이지 가져오기 (HTTP 우선, 필요할 때만 브라우저).

안전 규칙
- http/https만. URL 안의 계정 정보 거부.
- 매 요청(리다이렉트 포함)마다 DNS를 풀어 모든 주소가 공개 주소인지 확인하고, 확인한 IP로만 접속한다
  (DNS rebinding으로 검사 후 내부 주소로 바뀌는 것을 막는다). TLS는 원래 호스트 이름으로 검증한다.
- 쿠키·로그인 정보를 쓰지 않는다. 응답 크기·시간 상한.
- 브라우저(Playwright)는 선택 설치. 모든 하위 요청도 같은 주소 검사를 거친다.
- 봇 차단(Cloudflare 확인 화면 등)은 우회하지 않는다. ``rate_limited``(진짜 요청 한도)와 구분해
  ``bot_blocked``로 알리고 본문 직접 붙여넣기로 안내한다 (``detect_bot_block``).
- 모르는 서비스의 확인 화면(캡차·JS 확인 스크립트·빈 본문 + 스크립트)은 ``maybe_bot_blocked``
  ("막혔을 수 있음")로 알린다 (``challenge_signs``, c0025). 200 응답이어도 그런 화면이면 브라우저로 다시
  열지 않는다: 브라우저가 확인 스크립트를 실행하면 그게 우회다.
"""

from __future__ import annotations

import asyncio
import re
import socket
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from career_core.domain.postings import FetchError, is_public_address

USER_AGENT = "Mozilla/5.0 (compatible; career-brain self-hosted; +job-posting-saver)"
TEXT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "application/json",
    "application/ld+json",
)
# 본문 대신 바이트로 넘기는 형식 (PDF 공고, 포스터 이미지). 같은 크기 상한을 쓴다.
BINARY_TYPES = ("application/pdf", "image/png", "image/jpeg", "image/webp", "image/gif")
MAX_BYTES = 6 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True, slots=True)
class FetchedPage:
    url: str  # 요청한 URL
    final_url: str
    status: int
    content_type: str
    text: str
    method: str = "http"  # http | browser
    screenshot_png: bytes | None = None
    body: bytes | None = None  # BINARY_TYPES일 때만 원본 바이트 (text는 빈 문자열)


@dataclass(frozen=True, slots=True)
class FetchFailure:
    code: str
    status: int | None = None
    detail: str | None = None


class AddressBlocked(Exception):
    pass


async def resolve_public(host: str, port: int) -> str:
    """호스트의 모든 주소가 공개 주소일 때만 첫 주소를 돌려준다."""
    if is_public_address(host):
        return host
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ConnectionError("dns_failed") from exc
    addresses = sorted({info[4][0] for info in infos})
    if not addresses or not all(is_public_address(a) for a in addresses):
        raise AddressBlocked(host)
    return addresses[0]


# 봇 확인 화면의 표시 (소문자 본문 앞부분에서 찾는다). 자동 요청을 막는 화면이지 요청 한도가 아니다.
_CHALLENGE_MARKERS: tuple[tuple[str, str], ...] = (
    ("/cdn-cgi/challenge-platform/", "cloudflare"),
    ("_cf_chl_opt", "cloudflare"),
    ("cf-browser-verification", "cloudflare"),
    ("challenges.cloudflare.com", "cloudflare"),
    ("<title>just a moment...</title>", "cloudflare"),
    ("attention required! | cloudflare", "cloudflare"),
    ("_incapsula_resource", "imperva"),
    ("incapsula incident id", "imperva"),
    ("captcha-delivery.com", "datadome"),
    ("px-captcha", "perimeterx"),
    ("awswafintegration", "aws_waf"),
)
# 봇 확인 화면을 줄 수 있는 상태 코드 (200 확인 화면은 본문이 짧아 직접 확인으로 간다)
_CHALLENGE_STATUSES = frozenset({403, 429, 503})


def detect_bot_block(status: int, headers: Mapping[str, str] | None, body: str) -> str | None:
    """봇 차단이면 그 출처(예: ``cloudflare``), 아니면 None. 차단을 피하려고 다시 요청하지 않는다.

    1. 응답 머리: Cloudflare ``cf-mitigated: challenge``, AWS WAF ``x-amzn-waf-action``,
       DataDome ``x-datadome``(403). 상태 코드와 상관없이 확인 화면이다.
    2. 403·429·503 본문의 확인 화면 표시 (``_CHALLENGE_MARKERS``).
    ``Retry-After``만 있고 위 표시가 없는 429는 진짜 요청 한도(``rate_limited``)로 둔다.
    """
    h = {k.lower(): v for k, v in (headers or {}).items()}
    if h.get("cf-mitigated", "").strip().lower() == "challenge":
        return "cloudflare"
    if h.get("x-amzn-waf-action", "").strip().lower() in ("challenge", "captcha"):
        return "aws_waf"
    if status not in _CHALLENGE_STATUSES:
        return None
    if status == 403 and "x-datadome" in h:
        return "datadome"
    lowered = body[:60000].lower()
    for marker, source in _CHALLENGE_MARKERS:
        if marker in lowered:
            return source
    return None


# --- 모르는 서비스의 확인 화면 (c0025) ----------------------------------------------------
# 이 흔적들은 특정 서비스를 가리키지 않는다. 그래서 "막혔을 수 있음"(maybe_bot_blocked)으로만 말한다.
_CAPTCHA_WIDGETS = (
    'class="g-recaptcha',
    "class='g-recaptcha",
    'class="h-captcha',
    "class='h-captcha",
    'class="cf-turnstile',
    "class='cf-turnstile",
    'id="captcha',
    "id='captcha",
)
_CAPTCHA_SCRIPTS = (
    "recaptcha/api.js",
    "recaptcha/enterprise.js",
    "hcaptcha.com/1/api.js",
    "challenges.cloudflare.com/turnstile",
    "captcha",
)
# 사람 확인을 요구하는 문구 (소문자). "javascript를 켜라"만으로는 SPA와 구분되지 않아 쿠키까지 요구한다.
_CHALLENGE_PHRASES = (
    "checking your browser",
    "checking if the site connection is secure",
    "verify you are human",
    "verify that you are human",
    "verifying you are human",
    "are you a robot",
    "i'm not a robot",
    "unusual traffic",
    "automated requests",
    "automated access",
    "bot detection",
    "enable javascript and cookies",
    "javascript and cookies are required",
    "자동 입력 방지",
    "보안 문자",
    "로봇이 아닙니다",
    "자동화된 요청",
    "비정상적인 트래픽",
    "비정상적인 접근",
)
_COOKIE_SET = re.compile(r"document\.cookie\s*=")
_RELOAD = re.compile(
    r"\blocation\.(?:reload|replace)\s*\(|(?<![\w.])(?:window\.|document\.)?location(?:\.href)?\s*=(?!=)"
)
_CHALLENGE_SCRIPT = re.compile(
    r"<script[^>]*\bsrc=[\"'][^\"']*challenge[^\"']*[\"']|id=[\"']challenge-form[\"']"
)
_SCRIPT_BLOCK = re.compile(r"<(script|style|template)\b[^>]*>.*?</\1\s*>", re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
TINY_BODY_CHARS = 200


def _visible_chars(lowered: str) -> int:
    text = _TAG.sub(" ", _SCRIPT_BLOCK.sub(" ", lowered))
    return len(" ".join(text.split()))


def challenge_signs(status: int, body: str) -> tuple[str, ...]:
    """모르는 서비스의 봇 확인 화면 흔적. 없으면 빈 튜플. 판단만 하고 다시 요청하지 않는다.

    - ``captcha``: 캡차 위젯·스크립트. ``phrase``: 사람 확인 문구. ``js_challenge``: 쿠키를 심고 다시
      불러오는 스크립트, 또는 이름에 challenge가 든 스크립트·폼. ``tiny_body_script``: 보이는 글이 거의 없는데
      스크립트만 있다.
    - 403·429·503: 흔적 하나면 충분하다 (보통의 오류 화면에는 스크립트·캡차가 없다).
    - 200: 보이는 글이 거의 없고(``TINY_BODY_CHARS`` 미만) 위젯·문구·JS 확인 중 하나가 있을 때만
      (캡차 스크립트만 불러오는 보통 페이지·스크립트로 그리는 SPA와 구분하려고).
    """
    if status not in _CHALLENGE_STATUSES and status != 200:
        return ()
    lowered = body[:60000].lower()
    has_script = "<script" in lowered
    tiny = _visible_chars(lowered) < TINY_BODY_CHARS
    widget = any(m in lowered for m in _CAPTCHA_WIDGETS)
    signs: list[str] = []
    if widget or (status != 200 and any(m in lowered for m in _CAPTCHA_SCRIPTS)):
        signs.append("captcha")
    if any(p in lowered for p in _CHALLENGE_PHRASES):
        signs.append("phrase")
    if (_COOKIE_SET.search(lowered) and _RELOAD.search(lowered)) or _CHALLENGE_SCRIPT.search(
        lowered
    ):
        signs.append("js_challenge")
    if status == 200:
        return tuple(signs) if tiny and signs else ()
    if tiny and has_script:
        signs.append("tiny_body_script")
    return tuple(signs)


def _status_failure(
    status: int, body: str, headers: Mapping[str, str] | None = None, *, html: bool = False
) -> FetchFailure | None:
    """상태 코드·확인 화면 → 실패. ``html``: 200 응답도 확인 화면인지 본다 (HTML일 때만).

    순서: 알려진 봇 차단 → 로그인(401·407, 로그인 글이 있는 403) → 모르는 확인 화면(``maybe_bot_blocked``)
    → 429 ``rate_limited`` → 그 밖의 4xx·5xx.
    """
    bot = detect_bot_block(status, headers, body)
    if bot is not None:
        return FetchFailure(FetchError.BOT_BLOCKED, status, bot)
    if status in (401, 407):
        return FetchFailure(FetchError.LOGIN_REQUIRED, status)
    if status == 403:
        lowered = body[:20000].lower()
        if "login" in lowered or "로그인" in lowered or "sign in" in lowered:
            return FetchFailure(FetchError.LOGIN_REQUIRED, status)
    if status >= 400 or html:
        signs = challenge_signs(status, body)
        if signs:
            return FetchFailure(FetchError.MAYBE_BOT_BLOCKED, status, ",".join(signs))
    if status == 429:
        return FetchFailure(FetchError.RATE_LIMITED, status)
    if status >= 400:
        return FetchFailure(FetchError.SOURCE_UNAVAILABLE, status)
    return None


class HttpFetcher:
    """httpx 기반. 테스트에서는 `transport`와 `resolver`를 바꿔 끼운다."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver=resolve_public,
        max_bytes: int = MAX_BYTES,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._resolve = resolver
        self._max_bytes = max_bytes
        self._timeout = timeout

    async def _read_capped(self, resp: httpx.Response) -> bytes | FetchFailure | None:
        """압축을 직접 풀며 읽는다. 풀린 크기가 상한을 넘으면 None (압축 폭탄 방지).

        httpx의 자동 해제는 받은 조각 하나를 상한 없이 풀어 버린다.
        """
        if resp.is_stream_consumed:  # 이미 메모리에 풀려 있는 응답 (테스트용 등)
            data = resp.content
            return None if len(data) > self._max_bytes else data
        encoding = resp.headers.get("content-encoding", "").strip().lower()
        if encoding in ("", "identity"):
            decoder = None
        elif encoding in ("gzip", "x-gzip"):
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif encoding == "deflate":
            decoder = zlib.decompressobj(zlib.MAX_WBITS)
        else:
            return FetchFailure(FetchError.UNSUPPORTED_CONTENT, resp.status_code, encoding)
        out: list[bytes] = []
        size = wire = 0
        try:
            async for chunk in resp.aiter_raw():
                wire += len(chunk)
                if wire > self._max_bytes:
                    return None
                if decoder is None:
                    size += len(chunk)
                    if size > self._max_bytes:
                        return None
                    out.append(chunk)
                    continue
                data = chunk
                while data:
                    piece = decoder.decompress(data, self._max_bytes - size + 1)
                    size += len(piece)
                    if size > self._max_bytes:
                        return None
                    out.append(piece)
                    data = decoder.unconsumed_tail
            if decoder is not None:
                tail = decoder.flush()
                size += len(tail)
                if size > self._max_bytes:
                    return None
                out.append(tail)
        except zlib.error:
            return FetchFailure(FetchError.SOURCE_UNAVAILABLE, resp.status_code, "bad_encoding")
        return b"".join(out)

    async def fetch_raw(
        self, url: str, *, accept: str = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
    ) -> RawResponse | FetchFailure:
        """검사한 공개 IP로만 GET. 리다이렉트도 매번 다시 검사하며 직접 따라간다."""
        current = url
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=self._timeout,
            follow_redirects=False,
            trust_env=False,  # 프록시 환경변수·.netrc를 쓰지 않는다
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                parts = urlsplit(current)
                if parts.scheme not in ("http", "https") or not parts.hostname:
                    return FetchFailure(FetchError.INVALID_URL)
                if parts.username or parts.password:
                    return FetchFailure(FetchError.INVALID_URL)
                port = parts.port or (443 if parts.scheme == "https" else 80)
                try:
                    ip = await self._resolve(parts.hostname, port)
                except AddressBlocked:
                    return FetchFailure(FetchError.BLOCKED_ADDRESS, detail=parts.hostname)
                except ConnectionError:
                    return FetchFailure(FetchError.SOURCE_UNAVAILABLE, detail="dns_failed")
                shown = f"[{ip}]" if ":" in ip else ip
                pinned = urlunsplit(
                    (parts.scheme, f"{shown}:{port}", parts.path or "/", parts.query, "")
                )
                host_header = parts.hostname + (f":{parts.port}" if parts.port else "")
                try:
                    async with client.stream(
                        "GET",
                        pinned,
                        headers={
                            "Host": host_header,
                            "User-Agent": USER_AGENT,
                            "Accept": accept,
                            "Accept-Language": "ko,en;q=0.8",
                            # 직접 풀 수 있는 것만 받는다 (압축 해제 크기를 조각마다 제한하려고).
                            "Accept-Encoding": "gzip, deflate",
                        },
                        extensions={"sni_hostname": parts.hostname},
                    ) as resp:
                        if resp.status_code in (301, 302, 303, 307, 308):
                            location = resp.headers.get("location")
                            if not location:
                                return FetchFailure(FetchError.SOURCE_UNAVAILABLE, resp.status_code)
                            current = urljoin(current, location)
                            continue
                        declared = resp.headers.get("content-length")
                        if declared and declared.isdigit() and int(declared) > self._max_bytes:
                            return FetchFailure(FetchError.TOO_LARGE, resp.status_code)
                        body = await self._read_capped(resp)
                        if body is None:
                            return FetchFailure(FetchError.TOO_LARGE, resp.status_code)
                        if isinstance(body, FetchFailure):
                            return body
                        return RawResponse(
                            final_url=current,
                            status=resp.status_code,
                            content_type=resp.headers.get("content-type", ""),
                            charset=resp.charset_encoding,
                            body=body,
                            headers=_kept_headers(resp.headers),
                        )
                except httpx.TimeoutException:
                    return FetchFailure(FetchError.SOURCE_UNAVAILABLE, detail="timeout")
                except httpx.HTTPError as exc:
                    return FetchFailure(FetchError.SOURCE_UNAVAILABLE, detail=type(exc).__name__)
        return FetchFailure(FetchError.SOURCE_UNAVAILABLE, detail="too_many_redirects")

    async def fetch(
        self, url: str, *, accept: str = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
    ) -> FetchedPage | FetchFailure:
        raw = await self.fetch_raw(url, accept=accept)
        if isinstance(raw, FetchFailure):
            return raw
        text = raw.body.decode(raw.charset or "utf-8", errors="replace")
        ctype = raw.content_type.split(";")[0].strip().lower()
        is_html = ctype in ("", "text/html", "application/xhtml+xml")
        failure = _status_failure(raw.status, text, raw.headers, html=is_html)
        if failure:
            return failure
        if ctype in BINARY_TYPES or ctype in OCTET_TYPES:
            # 선언한 형식과 실제 파일 머리가 맞아야 한다 (octet-stream이면 머리로 판단).
            sniffed = sniff_binary(raw.body)
            if sniffed is None or (ctype in BINARY_TYPES and sniffed != ctype):
                return FetchFailure(FetchError.UNSUPPORTED_CONTENT, raw.status, ctype)
            return FetchedPage(url, raw.final_url, raw.status, sniffed, "", body=raw.body)
        if ctype and not ctype.startswith(TEXT_TYPES):
            return FetchFailure(FetchError.UNSUPPORTED_CONTENT, raw.status, ctype)
        return FetchedPage(url, raw.final_url, raw.status, ctype or "text/html", text)


OCTET_TYPES = ("application/octet-stream", "binary/octet-stream")


def sniff_binary(data: bytes) -> str | None:
    """파일 머리로 형식을 본다 (BINARY_TYPES 중 하나, 아니면 None)."""
    if b"%PDF-" in data[:1024]:  # PDF 머리는 첫 1KB 안 어디든 올 수 있다
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


# 봇 차단 판별에 쓰는 응답 머리만 남긴다 (쿠키 등은 버린다)
_KEPT_HEADERS = ("cf-mitigated", "x-amzn-waf-action", "x-datadome", "retry-after", "server")


def _kept_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {k: headers[k] for k in _KEPT_HEADERS if k in headers}


@dataclass(frozen=True, slots=True)
class RawResponse:
    final_url: str
    status: int
    content_type: str
    charset: str | None
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


SUBRESOURCE_MAX_BYTES = 4 * 1024 * 1024
MAX_SUBRESOURCES = 150
PAGE_BUDGET_BYTES = 24 * 1024 * 1024


@dataclass
class BrowserFetcher:
    """Playwright Chromium으로 렌더링한다 (선택 설치: `career-core[browser]` + `playwright install chromium`).

    Chromium이 직접 네트워크에 나가지 않는다: 모든 요청을 가로채 `HttpFetcher`(공개 주소 검사·IP 고정·
    리다이렉트마다 재검사·크기 상한)로 대신 가져와 응답을 채워 준다. 그래서 Chromium의 DNS 조회·
    리다이렉트·DNS rebinding으로 내부 주소에 닿지 못한다. GET 외 요청, 서비스 워커, WebSocket은 막는다.
    새 컨텍스트(쿠키·저장소 없음), 다운로드 금지.
    """

    timeout_ms: int = 25000
    screenshot: bool = True
    resolver: object = field(default=resolve_public)
    transport: object = None  # 테스트용 httpx transport

    async def fetch(self, url: str) -> FetchedPage | FetchFailure:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return FetchFailure(FetchError.NEEDS_MANUAL_CHECK, detail="browser_not_installed")
        main = HttpFetcher(transport=self.transport, resolver=self.resolver)
        sub = HttpFetcher(
            transport=self.transport, resolver=self.resolver, max_bytes=SUBRESOURCE_MAX_BYTES
        )
        state = {"count": 0, "bytes": 0, "main": None}

        async def guard(route) -> None:
            request = route.request
            parts = urlsplit(request.url)
            if parts.scheme in ("data", "blob"):
                await route.continue_()
                return
            if request.method != "GET" or parts.scheme not in ("http", "https"):
                await route.abort()
                return
            is_main = request.is_navigation_request() and request.frame.parent_frame is None
            if not is_main:
                state["count"] += 1
                if state["count"] > MAX_SUBRESOURCES or state["bytes"] > PAGE_BUDGET_BYTES:
                    await route.abort()
                    return
            raw = await (main if is_main else sub).fetch_raw(
                request.url, accept=request.headers.get("accept", "*/*")
            )
            if isinstance(raw, FetchFailure):
                if is_main:
                    state["main"] = raw
                await route.abort()
                return
            state["bytes"] += len(raw.body)
            headers = {"content-type": raw.content_type} if raw.content_type else {}
            await route.fulfill(status=raw.status, headers=headers, body=raw.body)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(args=["--disable-dev-shm-usage"])
            try:
                context = await browser.new_context(
                    user_agent=USER_AGENT,
                    accept_downloads=False,
                    java_script_enabled=True,
                    locale="ko-KR",
                    viewport={"width": 1280, "height": 1600},
                    service_workers="block",
                )
                await context.route("**/*", guard)
                if hasattr(context, "route_web_socket"):
                    await context.route_web_socket("**/*", lambda ws: ws.close())
                page = await context.new_page()
                try:
                    resp = await page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
                except Exception:  # noqa: BLE001 - 렌더링 실패는 수동 확인으로 넘긴다
                    if isinstance(state["main"], FetchFailure):
                        return state["main"]
                    return FetchFailure(FetchError.NEEDS_MANUAL_CHECK, detail="render_failed")
                if isinstance(state["main"], FetchFailure):
                    return state["main"]
                status = resp.status if resp else 200
                html = await page.content()
                kept = _kept_headers(await resp.all_headers()) if resp else {}
                failure = _status_failure(status, html, kept, html=True)
                if failure:
                    return failure
                shot = (
                    await page.screenshot(full_page=True, type="png") if self.screenshot else None
                )
                return FetchedPage(url, page.url, status, "text/html", html, "browser", shot)
            finally:
                await browser.close()

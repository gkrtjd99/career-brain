"""GitHub 저장소 읽기 (O18 포트폴리오 가져오기). 공개 REST API만, 토큰은 선택.

- 요청은 공고 가져오기와 같은 ``HttpFetcher``(공개 주소 검사·IP 고정·크기 상한·프록시 환경변수 무시)를 거치고,
  호스트는 ``api.github.com``·``raw.githubusercontent.com``만 허용한다 (리다이렉트 포함, ``ALLOWED_HOSTS``).
- 입력 주소는 ``https://github.com/<owner>/<repo>`` 모양만 받는다. 다른 호스트는 ``not_github``.
- 부르는 것은 둘: ``GET /repos/{owner}/{repo}``(주 언어·만든 날·마지막 push·공개 여부·별·포크),
  ``GET /repos/{owner}/{repo}/readme``(원문 그대로, ``application/vnd.github.raw+json``). 토큰 없이 공개 API 한도
  (시간당 60회) 안에서 쓴다. 403(남은 한도 0)·429는 ``rate_limited``와 다시 해도 되는 시각으로 알린다.
- 토큰은 비공개 저장소를 읽을 때만 ``Authorization``으로 보낸다. 토큰 값은 로그·예외·결과에 넣지 않는다.
- 별·포크 같은 숫자는 참고 정보다. 본인 기여의 근거로 쓰지 않는다 (기여는 사람이 확인한다).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from career_core.adapters.web_fetch import FetchFailure, HttpFetcher
from career_core.domain.postings import FetchError

API = "https://api.github.com"
ALLOWED_HOSTS = frozenset({"api.github.com", "raw.githubusercontent.com"})
WEB_HOSTS = frozenset({"github.com", "www.github.com"})
API_VERSION = "2022-11-28"
MAX_README_BYTES = 512 * 1024
MAX_TOKEN_CHARS = 400

_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_\-.]+$")


class GitHubError(Exception):
    """``code``만 밖으로 나간다 (토큰·응답 본문은 넣지 않는다). ``reset_at``: 한도가 풀리는 시각 (rate_limited)."""

    def __init__(self, code: str, *, status: int | None = None, reset_at: datetime | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.reset_at = reset_at


@dataclass(frozen=True, slots=True)
class RepoRef:
    owner: str
    repo: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"


@dataclass(frozen=True, slots=True)
class RepoInfo:
    full_name: str
    html_url: str
    private: bool
    language: str | None
    created_at: str | None
    pushed_at: str | None
    default_branch: str | None
    description: str | None
    stars: int
    forks: int
    readme: str | None  # 없으면 None


def parse_repo_url(value: str) -> RepoRef:
    """``https://github.com/owner/repo``(``.git``·뒤 경로·``www.`` 허용) → RepoRef. 다른 호스트는 ``not_github``."""
    raw = (value or "").strip()
    if not raw or len(raw) > 500 or any(c.isspace() for c in raw):
        raise GitHubError("invalid_url")
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise GitHubError("invalid_url") from exc
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        raise GitHubError("invalid_url")
    host = (parts.hostname or "").lower()
    if host not in WEB_HOSTS:
        raise GitHubError("not_github")
    if port not in (None, 443, 80):
        raise GitHubError("invalid_url")
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) < 2:
        raise GitHubError("invalid_url")
    owner, repo = segs[0], segs[1]
    repo = repo.removesuffix(".git")
    if not _OWNER.match(owner) or not _REPO.match(repo) or repo in (".", ".."):
        raise GitHubError("invalid_url")
    return RepoRef(owner, repo)


def check_token(value: str) -> str:
    t = (value or "").strip()
    if not t or len(t) > MAX_TOKEN_CHARS or not _TOKEN.match(t):
        raise GitHubError("invalid_token")
    return t


def _reset(headers: dict[str, str]) -> datetime | None:
    v = headers.get("x-ratelimit-reset", "")
    if v.isdigit():
        return datetime.fromtimestamp(int(v), UTC)
    return None


class GitHubClient:
    """테스트에서는 ``fetcher``(``HttpFetcher(transport=MockTransport, resolver=…)``)를 바꿔 끼운다."""

    def __init__(self, fetcher: HttpFetcher | None = None) -> None:
        self._fetcher = fetcher or HttpFetcher(max_bytes=MAX_README_BYTES * 2)

    def __repr__(self) -> str:
        return "GitHubClient()"

    async def _get(self, path: str, *, accept: str, token: str | None) -> tuple[int, bytes, dict]:
        headers = {"X-GitHub-Api-Version": API_VERSION}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        raw = await self._fetcher.fetch_raw(
            f"{API}{path}", accept=accept, headers=headers, allowed_hosts=ALLOWED_HOSTS
        )
        if isinstance(raw, FetchFailure):
            if raw.code == FetchError.TOO_LARGE:
                raise GitHubError("too_large")
            if raw.code in (FetchError.BLOCKED_ADDRESS, FetchError.INVALID_URL):
                raise GitHubError("blocked_address")
            raise GitHubError("github_unavailable")
        headers_in = {k.lower(): v for k, v in raw.headers.items()}
        status = raw.status
        if status == 429 or (status == 403 and headers_in.get("x-ratelimit-remaining") == "0"):
            raise GitHubError("rate_limited", status=status, reset_at=_reset(headers_in))
        if status == 401:
            raise GitHubError("token_invalid", status=status)
        return status, raw.body, headers_in

    async def repo(self, ref: RepoRef, *, token: str | None = None) -> RepoInfo:
        status, body, _ = await self._get(
            f"/repos/{ref.owner}/{ref.repo}", accept="application/vnd.github+json", token=token
        )
        if status == 404:
            raise GitHubError("repo_not_found", status=status)
        if status == 403:
            raise GitHubError("repo_forbidden", status=status)
        if status != 200:
            raise GitHubError("github_unavailable", status=status)
        try:
            data: dict[str, Any] = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubError("github_unavailable") from exc
        if not isinstance(data, dict) or not isinstance(data.get("full_name"), str):
            raise GitHubError("github_unavailable")
        readme = await self.readme(ref, token=token)

        def num(key: str) -> int:
            v = data.get(key)
            return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0

        def text(key: str, limit: int = 300) -> str | None:
            v = data.get(key)
            return v[:limit] if isinstance(v, str) and v else None

        full = data["full_name"][:200]
        return RepoInfo(
            full_name=full,
            html_url=f"https://github.com/{full}",
            private=bool(data.get("private")),
            language=text("language", 60),
            created_at=text("created_at", 40),
            pushed_at=text("pushed_at", 40),
            default_branch=text("default_branch", 100),
            description=text("description"),
            stars=num("stargazers_count"),
            forks=num("forks_count"),
            readme=readme,
        )

    async def readme(self, ref: RepoRef, *, token: str | None = None) -> str | None:
        status, body, _ = await self._get(
            f"/repos/{ref.owner}/{ref.repo}/readme",
            accept="application/vnd.github.raw+json",
            token=token,
        )
        if status == 404:
            return None
        if status != 200:
            raise GitHubError("github_unavailable", status=status)
        if len(body) > MAX_README_BYTES:
            raise GitHubError("readme_too_large")
        text = body.decode("utf-8", errors="replace").replace("\x00", "").strip()
        return text or None


__all__ = [
    "ALLOWED_HOSTS",
    "GitHubClient",
    "GitHubError",
    "RepoInfo",
    "RepoRef",
    "check_token",
    "parse_repo_url",
]

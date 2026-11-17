# Security

career-brain stores personal career data, so security reports are welcome.

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on this repository.

Include what you found, how to reproduce it (synthetic data only — never send real personal data, tokens or keys), and the version or commit you tested.
This is a personal open-source project with no dedicated security team; expect a reply on a best-effort basis.

## Scope

In scope: the code in this repository — workspace isolation (row-level security), API token and MCP/OAuth handling, file parsing and upload limits, posting URL fetching (internal address blocking), export/restore archives, and anything that could leak one workspace's data to another.

Out of scope: your own deployment choices (exposing the app without TLS, weak database passwords, sharing tokens), and vulnerabilities in third-party model providers.

## Design notes

How isolation and tokens work: [docs/architecture/database.md](docs/architecture/database.md), [docs/architecture/mcp.md](docs/architecture/mcp.md), deployment hardening: [docs/operations/install.md](docs/operations/install.md).

Portfolio GitHub import (c0049) only calls `api.github.com` and `raw.githubusercontent.com` (redirects included) through the same fetcher as posting import (public-address check, pinned IP, size cap). An optional token for private repositories is stored encrypted with the same `SecretBox` as model keys, shown as its last 4 characters only, sent only to `api.github.com`, never logged or returned, and left out of backups. Portfolio images are resized in the browser and re-checked and stripped of metadata on the server; they are never sent to models or MCP, and "private" items and images are absent from every export: [docs/architecture/database.md](docs/architecture/database.md#포트폴리오-c0049-o18).

The "send this page" bookmarklet (c0043) keeps no token on third-party pages, uses `postMessage` with a fixed target origin, and never renders received HTML: [docs/architecture/web.md](docs/architecture/web.md#보고-있는-페이지-보내기-capture-c0043).

---

보안 문제는 공개 이슈 대신 이 저장소의 **Security → Report a vulnerability**(비공개 신고)로 알려 주세요. 재현 자료는 합성 데이터로만 보내 주세요.

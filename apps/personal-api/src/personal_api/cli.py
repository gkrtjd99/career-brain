"""personal-api 운영 명령.

personal-api migrate     # core → personal 순서로 migration (소유자 계정)
personal-api bootstrap   # 공간 + owner + API 토큰 생성 (앱 계정), 토큰은 한 번만 출력
personal-api serve       # API + 내장 worker + 웹 정적 파일 (한 프로세스, 작은 서버용)
personal-api worker      # 백그라운드 작업 처리 (serve --no-worker 와 함께 따로 띄울 때)
personal-api models      # 엔드포인트·라우트·단가·키 설정 상태 (키 값은 출력하지 않음)
personal-api pricing     # 단가표 점검 (오래된 항목, 단가 없는 라우트)
personal-api usage       # 모델 사용량·비용 (CAREER_API_TOKEN 의 공간)
personal-api ping        # 라우트 하나에 짧은 합성 요청을 보내 설정을 확인 (과금됨)
personal-api mcp         # MCP stdio 서버 (CAREER_API_TOKEN 의 공간, 도구는 docs/architecture/mcp.md)
personal-api export --out FILE   # CAREER_API_TOKEN 공간을 묶음 파일(tar.gz)로 (소유자만)
personal-api restore FILE        # 빈 설치에 묶음을 복원하고 새 토큰 발급 (--dry-run: 검사만)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.request
from datetime import UTC, date, datetime, timedelta

from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.adapters.web_fetch import BrowserFetcher, HttpFetcher
from career_core.db import Database
from career_core.jobs.worker import (
    JobHandler,
    extraction_handlers,
    index_handlers,
    parsing_handlers,
    posting_handlers,
    run_handlers,
    run_until_idle,
)
from career_core.llm import ChatMessage, ModelCallError, ModelDenied, ModelRequest
from career_core.llm.config import BILLING_METERED
from career_core.llm.pricing_check import XAI_LANGUAGE_MODELS_URL, check_prices, compare_xai
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext
from career_core.usecases.extraction import ExtractionDeps
from career_core.usecases.indexing import IndexDeps
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.postings import PostingDeps
from career_core.usecases.runs import RunDeps
from career_core.usecases.usage import usage_summary
from career_core.usecases.workspaces import create_workspace

from personal_api.identity import PersonalTokenResolver, issue_token
from personal_api.models import build_gateway, load_config, load_prices
from personal_api.runner import run_worker, worker_poll_seconds
from personal_api.settings import Settings

TOKEN_ENV = "CAREER_API_TOKEN"


def build_handlers(settings: Settings, db: Database) -> dict[str, JobHandler]:
    """worker가 처리하는 작업 종류. `worker` 명령과 `serve`(내장 worker)가 같이 쓴다."""
    deps = ParsingDeps(
        db=db,
        blobs=LocalBlobStore(settings.blob_dir),
        egress=ProviderEgressPolicy({}),  # 외부 제공자 미설정 = 외부 전송 없음
        local_parsers=default_local_parsers(),
    )
    gateway = build_gateway(settings, db)
    return {
        **run_handlers(RunDeps(db=db, gateway=gateway)),
        **parsing_handlers(deps),
        **extraction_handlers(ExtractionDeps(db, gateway)),
        **index_handlers(IndexDeps(db, gateway)),
        **posting_handlers(
            PostingDeps(
                db=db,
                fetcher=HttpFetcher(),
                gateway=gateway,
                # 동적 페이지용 브라우저는 선택 설치. 없으면 직접 붙여넣기로 안내한다.
                browser=BrowserFetcher() if settings.browser_fetch else None,
                blobs=deps.blobs,
            )
        ),
    }


async def _worker(settings: Settings, *, once: bool) -> None:
    db = Database(settings.database_url)
    handlers = build_handlers(settings, db)
    try:
        if once:
            print(f"processed {await run_until_idle(db, handlers)} job(s)")
            return
        await run_worker(db, handlers, poll_seconds=worker_poll_seconds())
    finally:
        await db.dispose()


async def _bootstrap(settings: Settings, workspace_name: str, display_name: str) -> str:
    db = Database(settings.database_url)
    try:
        ws, actor = await create_workspace(db, name=workspace_name, owner_display_name=display_name)
        return await issue_token(db, workspace_id=ws, actor_id=actor, label="bootstrap")
    finally:
        await db.dispose()


async def _actor(db: Database) -> ActorContext:
    token = os.environ.get(TOKEN_ENV)
    if not token:
        sys.exit(f"set {TOKEN_ENV} to an API token from `personal-api bootstrap`")
    actor = await PersonalTokenResolver(db).resolve(token)
    if actor is None:
        sys.exit(f"{TOKEN_ENV} is not a valid token")
    return actor


def _models(settings: Settings) -> None:
    config = load_config(settings)
    prices = load_prices(config)
    if not config.endpoints:
        print(f"no endpoints configured ({settings.models_config}); all model calls are denied")
        return
    print("endpoints:")
    for ep in config.endpoints.values():
        key = (
            "-"
            if not ep.api_key_env
            else (f"{ep.api_key_env}={'set' if os.environ.get(ep.api_key_env) else 'MISSING'}")
        )
        classes = ",".join(sorted(ep.data_classes - {"restricted"})) or "none"
        print(f"  {ep.name:<12} {ep.kind:<9} {ep.billing:<12} key {key:<28} data {classes}")
    print("routes:")
    for purpose, route in sorted(config.routes.items()):
        ep = config.endpoints[route.endpoint]
        provider = ep.pricing_provider if ep.billing == BILLING_METERED else ep.price_as
        price = prices.lookup(provider, route.model) if provider else None
        shown = f"in {price.rates.input} / out {price.rates.output} USD/1M" if price else "no price"
        print(f"  {purpose:<12} {route.endpoint}/{route.model:<28} {shown}")
    if config.budget.monthly_usd is not None:
        print(f"monthly budget: {config.budget.monthly_usd} USD (metered endpoints only)")


def _fetch_xai() -> dict:
    """xAI 공식 단가 API. 키는 xAI에만 보낸다."""
    key = os.environ.get("XAI_API_KEY")
    if not key:
        sys.exit("set XAI_API_KEY to fetch xAI prices")
    req = urllib.request.Request(
        XAI_LANGUAGE_MODELS_URL,
        headers={"Authorization": f"Bearer {key}", "User-Agent": "career-brain-pricing-check"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _pricing(settings: Settings, max_age_days: int, fetch_xai: bool) -> int:
    config = load_config(settings)
    prices = load_prices(config)
    findings = check_prices(prices, config, today=date.today(), max_age_days=max_age_days)
    if fetch_xai:
        findings += compare_xai(_fetch_xai(), prices)
    for f in findings:
        print(f"{f.level:<5} {f.code:<20} {f.subject}  {f.detail}")
    if not findings:
        print("pricing ok")
    return 1 if any(f.level == "error" for f in findings) else 0


async def _usage(settings: Settings, days: int, group_by: str) -> None:
    db = Database(settings.database_url)
    try:
        actor = await _actor(db)
        now = datetime.now(UTC)
        s = await usage_summary(
            db,
            actor.workspace_id,
            since=now - timedelta(days=days),
            group_by=group_by,
            monthly_budget_usd=load_config(settings).budget.monthly_usd,
            now=now,
        )
    finally:
        await db.dispose()
    print(
        f"{group_by:<28} {'calls':>6} {'err':>4} {'deny':>4} {'in':>10} {'cached':>9} "
        f"{'out':>9} {'spent$':>11} {'equiv$':>10}"
    )
    for r in [*s.rows, s.total]:
        print(
            f"{r.key:<28} {r.calls:>6} {r.errors:>4} {r.denied:>4} {r.input_tokens:>10} "
            f"{r.cached_input_tokens:>9} {r.output_tokens:>9} {r.spent_usd:>11.4f} "
            f"{r.equivalent_usd:>10.4f}"
        )
    line = f"this month: {s.month_spent_usd:.4f} USD"
    if s.monthly_budget_usd is not None:
        line += f" of {s.monthly_budget_usd} (remaining {s.budget_remaining_usd:.4f})"
    print(line)
    for note in s.notes:
        print(f"note: {note}")


async def _ping(settings: Settings, purpose: str) -> int:
    db = Database(settings.database_url)
    try:
        actor = await _actor(db)
        gateway = build_gateway(settings, db)
        request = ModelRequest(
            messages=(ChatMessage("user", "Reply with the single word OK."),),
            max_output_tokens=256,
            data_classes=frozenset({"public"}),  # 합성 문장만 보낸다
        )
        try:
            result = await gateway.run(actor, purpose, request)
        except ModelDenied as exc:
            print(f"denied: {exc.code}")
            return 1
        except ModelCallError as exc:
            print(f"error: {exc.code}")
            return 1
    finally:
        await db.dispose()
    u = result.usage
    print(f"{result.endpoint}/{result.model} stop={result.stop_reason} text={result.text[:40]!r}")
    print(
        f"tokens in {u.input_tokens} cached {u.cached_input_tokens} out {u.output_tokens} "
        f"(reasoning {u.reasoning_tokens}) cost {result.cost_usd} USD [{result.cost_basis}]"
    )
    return 0


async def _reindex(settings: Settings) -> None:
    from career_core.usecases.index_queue import enqueue_index

    db = Database(settings.database_url)
    try:
        actor = await _actor(db)
        await enqueue_index(db, actor.workspace_id)
        space = build_gateway(settings, db).embedding_space()
    finally:
        await db.dispose()
    print(f"index job queued (space: {space or 'none — keyword search only'}); run the worker")


async def _search(settings: Settings, query: str, limit: int) -> None:
    from career_core.usecases.search import search

    db = Database(settings.database_url)
    try:
        actor = await _actor(db)
        result = await search(db, actor, query, gateway=build_gateway(settings, db), limit=limit)
    finally:
        await db.dispose()
    print(f"mode={result.mode} note={result.semantic_note or '-'}")
    for h in result.hits:
        where = h.experience_title or h.source_title or h.question or ""
        ranks = f"k{h.keyword_rank or '-'} s{h.semantic_rank or '-'}"
        print(f"{h.score:.4f} {ranks:<8} {h.kind:<6} {where[:24]:<24} {h.snippet[:90]}")


async def _mcp(settings: Settings) -> None:
    """stdio MCP 서버. 토큰은 메시지마다 다시 확인한다 (폐기하면 바로 막힌다)."""
    from career_core.interfaces.http import CoreDeps
    from career_core.interfaces.mcp import McpServer
    from career_core.interfaces.mcp.stdio import run_stdio

    token = os.environ.get(TOKEN_ENV)
    if not token:
        sys.exit(f"set {TOKEN_ENV} to an API token from `personal-api bootstrap`")
    db = Database(settings.database_url)
    try:
        resolver = PersonalTokenResolver(db)
        if await resolver.resolve(token) is None:
            sys.exit(f"{TOKEN_ENV} is not a valid token")

        deps = CoreDeps(identity=resolver, db=db, models=build_gateway(settings, db))
        server = McpServer(deps)
        await run_stdio(server, lambda: resolver.resolve(token))
    finally:
        await db.dispose()


async def _export(settings: Settings, out: str) -> int:
    from career_core.usecases.export import ExportError, export_workspace

    db = Database(settings.database_url)
    try:
        actor = await _actor(db)
        try:
            report = await export_workspace(db, LocalBlobStore(settings.blob_dir), actor, out)
        except ExportError as exc:
            print(f"export failed: {exc}")
            return 1
    finally:
        await db.dispose()
    print(f"wrote {report.path} ({report.bytes} bytes, sha256 {report.sha256})")
    print(f"workspace {report.workspace_id}: {report.rows_total} rows, {report.blobs} blob(s)")
    for t in report.tables:
        if t.rows:
            print(f"  {t.name:<36} {t.rows:>7}")
    for m in report.missing_blobs:
        print(f"warning: blob {m['key'][:12]}… {m['reason']} ({', '.join(m['refs'])})")
    print("the archive contains personal data: keep it encrypted, off this server")
    return 0


async def _restore(settings: Settings, path: str, *, dry_run: bool) -> int:
    from career_core.usecases.export import ExportError, restore_workspace
    from career_core.usecases.index_queue import enqueue_index

    db = Database(settings.database_url)
    try:
        try:
            report = await restore_workspace(
                db, LocalBlobStore(settings.blob_dir), path, dry_run=dry_run
            )
        except ExportError as exc:
            print(f"restore failed: {exc}")
            return 1
        print(
            f"{'checked' if dry_run else 'restored'} workspace {report.workspace_id} "
            f"({report.workspace_name}): {report.rows_total} rows, {report.blobs} blob(s), "
            "counts and hashes match"
        )
        for note in report.notes:
            print(f"note: {note}")
        if dry_run:
            print("dry run: nothing was written")
            return 0
        await enqueue_index(db, report.workspace_id)  # 검색 벡터는 묶음에 없다
        if report.owners:
            token = await issue_token(
                db, workspace_id=report.workspace_id, actor_id=report.owners[0], label="restore"
            )
            print("new API token for the restored owner (shown once, store it safely):")
            print(token)
        print("search index job queued; run the worker (or serve) to rebuild it")
    finally:
        await db.dispose()
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="personal-api")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply core + personal migrations")
    b = sub.add_parser("bootstrap", help="create a workspace, owner and API token (run once)")
    b.add_argument("--workspace-name", default="내 커리어")
    b.add_argument("--display-name", default="me")
    sv = sub.add_parser("serve", help="run the API (+ in-process worker and web UI)")
    sv.add_argument("--host", default=None, help="bind address (CAREER_HOST, default 127.0.0.1)")
    sv.add_argument("--port", type=int, default=None, help="port (CAREER_PORT, default 8000)")
    sv.add_argument(
        "--no-worker",
        action="store_true",
        help="do not run the job worker in this process (CAREER_EMBEDDED_WORKER=0)",
    )
    w = sub.add_parser("worker", help="process background jobs")
    w.add_argument("--once", action="store_true", help="process pending jobs and exit")
    sub.add_parser("models", help="show model endpoints, routes and price status")
    pr = sub.add_parser("pricing", help="check the price catalog against configured routes")
    pr.add_argument("--max-age-days", type=int, default=60)
    pr.add_argument("--fetch-xai", action="store_true", help="compare with xAI's pricing API")
    u = sub.add_parser("usage", help=f"model usage and cost for the {TOKEN_ENV} workspace")
    u.add_argument("--days", type=int, default=30)
    u.add_argument("--by", default="day", choices=["day", "model", "purpose", "endpoint", "status"])
    sub.add_parser("reindex", help="queue a search re-index (after changing the embed route)")
    se = sub.add_parser("search", help=f"search the {TOKEN_ENV} workspace brain")
    se.add_argument("query")
    se.add_argument("--limit", type=int, default=10)
    pg = sub.add_parser("ping", help="send a tiny synthetic request through a route (billed)")
    pg.add_argument("purpose", nargs="?", default="default")
    sub.add_parser("mcp", help=f"MCP server over stdio for the {TOKEN_ENV} workspace")
    ex = sub.add_parser("export", help=f"export the {TOKEN_ENV} workspace to a .tar.gz archive")
    ex.add_argument("--out", required=True, help="archive path (e.g. career-export.tar.gz)")
    rs = sub.add_parser("restore", help="restore an export archive into an empty install")
    rs.add_argument("file")
    rs.add_argument("--dry-run", action="store_true", help="check everything, write nothing")
    args = parser.parse_args(argv)
    settings = Settings.from_env()

    if args.cmd == "migrate":
        # alembic은 migrate 때만 올린다 (serve·worker 상주 메모리를 줄인다)
        from career_core.db.migrate import upgrade

        from personal_api.migrations import PERSONAL_CHAIN

        upgrade(settings.migration_database_url, [PERSONAL_CHAIN])
        print("migrations applied: core, personal")
    elif args.cmd == "bootstrap":
        token = asyncio.run(_bootstrap(settings, args.workspace_name, args.display_name))
        print("API token (shown once, store it safely):")
        print(token)
    elif args.cmd == "serve":
        from personal_api.serve import serve

        serve(settings, host=args.host, port=args.port, worker=False if args.no_worker else None)
    elif args.cmd == "worker":
        asyncio.run(_worker(settings, once=args.once))
    elif args.cmd == "models":
        _models(settings)
    elif args.cmd == "pricing":
        sys.exit(_pricing(settings, args.max_age_days, args.fetch_xai))
    elif args.cmd == "usage":
        asyncio.run(_usage(settings, args.days, args.by))
    elif args.cmd == "reindex":
        asyncio.run(_reindex(settings))
    elif args.cmd == "search":
        asyncio.run(_search(settings, args.query, args.limit))
    elif args.cmd == "ping":
        sys.exit(asyncio.run(_ping(settings, args.purpose)))
    elif args.cmd == "mcp":
        asyncio.run(_mcp(settings))
    elif args.cmd == "export":
        sys.exit(asyncio.run(_export(settings, args.out)))
    elif args.cmd == "restore":
        sys.exit(asyncio.run(_restore(settings, args.file, dry_run=args.dry_run)))


if __name__ == "__main__":
    main()

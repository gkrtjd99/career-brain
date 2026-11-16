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
personal-api restore FILE --merge  # CAREER_API_TOKEN 공간에 합치기 (--on-duplicate skip|fail)
personal-api oauth-link --subject SUB   # 외부 인가 서버의 사용자(sub)를 CAREER_API_TOKEN 소유자에 연결
personal-api token list|issue|revoke ID  # CAREER_API_TOKEN 공간의 API 토큰 (소유자만, 새 토큰은 한 번만 출력)
personal-api maintenance         # 모든 공간 주기 유지보수(초안 정리)를 지금 한 번 (serve·worker는 자동)
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

from personal_api.identity import (
    PersonalTokenResolver,
    issue_token,
    list_tokens,
    revoke_token,
)
from personal_api.models import build_gateway, load_config, load_prices
from personal_api.runner import (
    maintenance_interval_seconds,
    run_worker,
    worker_listen,
    worker_poll_seconds,
)
from personal_api.settings import Settings

TOKEN_ENV = "CAREER_API_TOKEN"
MAX_TOKEN_LABEL = 100


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
        listen = worker_listen()
        await run_worker(
            db,
            handlers,
            poll_seconds=worker_poll_seconds(listen),
            listen=listen,
            maintenance_seconds=maintenance_interval_seconds(),
        )
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
        server = McpServer(deps, channel="mcp-stdio")
        await run_stdio(server, lambda: resolver.resolve(token))
    finally:
        await db.dispose()


async def _export(settings: Settings, out: str, recipients: list[str] | None = None) -> int:
    from career_core.usecases.export import ExportError, export_workspace

    db = Database(settings.database_url)
    try:
        actor = await _actor(db)
        try:
            report = await export_workspace(
                db, LocalBlobStore(settings.blob_dir), actor, out, recipients=recipients or None
            )
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
    if report.pending_work:
        print(f"{report.pending_work} pending job(s) recorded; restore re-queues them")
    if report.encrypted:
        print("encrypted with age: keep the identity file off this server")
    else:
        print("the archive contains personal data: keep it encrypted, off this server")
    return 0


async def _export_deletions(settings: Settings, out: str) -> int:
    from career_core.usecases.export import ExportError, export_deletions

    db = Database(settings.database_url)
    try:
        actor = await _actor(db)
        try:
            n = await export_deletions(db, actor, out)
        except ExportError as exc:
            print(f"export failed: {exc}")
            return 1
    finally:
        await db.dispose()
    print(f"wrote {out} ({n} deletion record(s)); pass it to restore --deletions")
    return 0


async def _restore(
    settings: Settings,
    path: str,
    *,
    dry_run: bool,
    new_ids: bool = False,
    identities: list[str] | None = None,
    deletions: list[str] | None = None,
) -> int:
    from career_core.usecases.export import ExportError, restore_workspace
    from career_core.usecases.index_queue import enqueue_index

    db = Database(settings.database_url)
    try:
        try:
            report = await restore_workspace(
                db,
                LocalBlobStore(settings.blob_dir),
                path,
                dry_run=dry_run,
                new_ids=new_ids,
                identities=identities or None,
                deletion_logs=deletions or (),
            )
        except ExportError as exc:
            print(f"restore failed: {exc}")
            return 1
        print(
            f"{'checked' if dry_run else 'restored'} workspace {report.workspace_id} "
            f"({report.workspace_name}): {report.rows_total} rows, {report.blobs} blob(s), "
            "counts and hashes match"
        )
        if report.source_workspace_id:
            print(f"imported with new ids (source workspace {report.source_workspace_id})")
        _print_deletions(report)
        for kind, n in sorted(report.requeued.items()):
            print(f"re-queued {n} {kind} job(s)")
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


def _print_deletions(report) -> None:
    """다시 적용한 삭제 기록 수와, 건너뛴 기록마다 이유 (restore·merge 공통)."""
    if not (report.deletions_applied or report.deletions_skipped):
        return
    print(f"re-applied {report.deletions_applied} deletion(s), skipped {report.deletions_skipped}")
    for d in report.skipped_deletions:
        detail = f": {d.detail}" if d.detail else ""
        print(f"  skipped {d.target_table} {d.target_id} — {d.reason}{detail}")


async def _merge(
    settings: Settings,
    path: str,
    *,
    dry_run: bool,
    on_duplicate: str,
    identities: list[str] | None = None,
    deletions: list[str] | None = None,
) -> int:
    """CAREER_API_TOKEN 공간(자료가 있어도 됨)에 묶음을 합친다. 소유자만."""
    from career_core.usecases.export import ExportError, merge_workspace
    from career_core.usecases.index_queue import enqueue_index

    db = Database(settings.database_url)
    try:
        actor = await _actor(db)
        try:
            report = await merge_workspace(
                db,
                LocalBlobStore(settings.blob_dir),
                actor,
                path,
                on_duplicate=on_duplicate,
                dry_run=dry_run,
                identities=identities or None,
                deletion_logs=deletions or (),
            )
        except ExportError as exc:
            print(f"merge failed: {exc}")
            return 1
        print(
            f"{'checked' if dry_run else 'merged'} archive of {report.source_workspace_id} "
            f"({report.source_workspace_name}) into {report.workspace_id} "
            f"({report.workspace_name}): {report.rows_added} row(s) added, "
            f"{report.rows_skipped} duplicate(s) skipped, {report.blobs} blob(s); "
            "existing rows unchanged, added rows hash-checked"
        )
        for t in report.tables:
            if t.name in report.not_merged or not t.archive_rows:
                continue
            by = ", ".join(f"{k} {n}" for k, n in sorted(t.skipped_by.items()))
            print(f"  {t.name:<36} +{t.added:<6} skipped {t.skipped}{f' ({by})' if by else ''}")
        for name, reason in sorted(report.not_merged.items()):
            print(f"  {name:<36} not merged: {reason}")
        if report.actors_mapped:
            print(f"{report.actors_mapped} archive actor(s) mapped to you")
        _print_deletions(report)
        for kind, n in sorted(report.requeued.items()):
            print(f"re-queued {n} {kind} job(s)")
        for note in report.notes:
            print(f"note: {note}")
        if dry_run:
            print("dry run: nothing was written")
            return 0
        await enqueue_index(db, report.workspace_id)
        print("search index job queued; run the worker (or serve) to rebuild it")
    finally:
        await db.dispose()
    return 0


async def _oauth_link(
    settings: Settings, subject: str | None, issuer: str | None, revoke: str | None
) -> int:
    """원격 MCP OAuth: (issuer, sub) → 이 공간의 소유자. 인자 없이 부르면 연결 목록."""
    from uuid import UUID

    from career_core.usecases.oauth_identities import (
        OAuthLinkError,
        link_oauth_identity,
        list_oauth_identities,
        revoke_oauth_identity,
    )

    db = Database(settings.database_url)
    try:
        actor = await _actor(db)
        try:
            if revoke:
                ok = await revoke_oauth_identity(db, actor, UUID(revoke))
                print("revoked" if ok else "not found or already revoked")
                return 0 if ok else 1
            if subject:
                iss = issuer or settings.oauth_issuer
                if not iss:
                    sys.exit("set --issuer or CAREER_OAUTH_ISSUER")
                view = await link_oauth_identity(db, actor, issuer=iss, subject=subject)
                print(f"linked {view.issuer} sub={view.subject} → workspace {actor.workspace_id}")
                return 0
            for v in await list_oauth_identities(db, actor):
                state = "revoked" if v.revoked_at else "active"
                print(f"{v.id}  {state:<8} {v.issuer}  sub={v.subject}")
        except OAuthLinkError as exc:
            print(f"oauth-link failed: {exc.code}")
            return 1
    finally:
        await db.dispose()
    return 0


async def _token(
    settings: Settings, action: str, *, token_id: str | None = None, label: str | None = None
) -> int:
    """토큰 관리. 원문은 새로 발급한 토큰만 한 번 출력하고, 목록에는 ID·이름·상태만 보인다."""
    from uuid import UUID

    db = Database(settings.database_url)
    try:
        actor = await _actor(db)
        if not actor.has_scope("workspace-admin"):
            print("token commands need an owner token")
            return 1
        ws = actor.workspace_id
        current = os.environ[TOKEN_ENV]
        if action == "list":
            rows = await list_tokens(db, workspace_id=ws, current_token=current)
            print(f"  {'id':<36}  {'status':<18}  {'created':<16}  label")
            for r in rows:
                status = f"revoked {r.revoked_at:%Y-%m-%d}" if r.revoked_at else "active"
                mark = "*" if r.current else " "
                print(f"{mark} {r.id}  {status:<18}  {r.created_at:%Y-%m-%d %H:%M}  {r.label}")
            print("* = the token in CAREER_API_TOKEN")
            return 0
        if action == "issue":
            name = (label or "cli").strip()
            if not name or len(name) > MAX_TOKEN_LABEL:
                print(f"label must be 1..{MAX_TOKEN_LABEL} characters")
                return 1
            token = await issue_token(db, workspace_id=ws, actor_id=actor.subject_id, label=name)
            print("new API token (shown once, store it safely):")
            print(token)
            return 0
        # revoke
        try:
            target = UUID(token_id or "")
        except ValueError:
            print("revoke needs a token id from `personal-api token list`")
            return 1
        rows = await list_tokens(db, workspace_id=ws, current_token=current)
        row = next((r for r in rows if r.id == target), None)
        if row is not None and row.current:
            print(
                "refusing to revoke the token in CAREER_API_TOKEN: issue a new one, "
                "switch to it, then revoke this one"
            )
            return 1
        if row is None or not await revoke_token(db, workspace_id=ws, token_id=target):
            print("no active token with that id in this workspace")
            return 1
        print(f"revoked {target} ({row.label})")
        return 0
    finally:
        await db.dispose()


async def _maintenance(settings: Settings) -> int:
    from career_core.jobs.maintenance import run_maintenance

    db = Database(settings.database_url)
    try:
        report = await run_maintenance(db)
    finally:
        await db.dispose()
    if not report.ran:
        print("another worker is running maintenance right now; skipped")
        return 0
    counts = " ".join(f"{k}={v}" for k, v in report.counts.items())
    print(f"maintenance: {report.workspaces} workspace(s) {counts} errors={report.errors}")
    return 1 if report.errors else 0


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
    ex.add_argument(
        "--recipient", action="append", help="age public key (age1…); encrypts the archive"
    )
    ed = sub.add_parser("export-deletions", help="write the deletion log (small, export often)")
    ed.add_argument("--out", required=True)
    rs = sub.add_parser("restore", help="restore an export archive into an empty install")
    rs.add_argument("file")
    rs.add_argument("--dry-run", action="store_true", help="check everything, write nothing")
    rs.add_argument(
        "--new-ids", action="store_true", help="import as a new workspace with fresh ids"
    )
    rs.add_argument("--identity", action="append", help="age identity file for encrypted archives")
    rs.add_argument(
        "--deletions", action="append", help="deletion log(s) written after the archive"
    )
    rs.add_argument(
        "--merge",
        action="store_true",
        help=f"merge into the {TOKEN_ENV} workspace (may already have data; owner only)",
    )
    rs.add_argument(
        "--on-duplicate",
        choices=["skip", "fail"],
        default="skip",
        help="with --merge: keep the existing row (skip) or abort (fail) on duplicates",
    )
    ol = sub.add_parser("oauth-link", help="link an external OAuth subject to the token's owner")
    ol.add_argument("--subject", help="the 'sub' claim from your authorization server")
    ol.add_argument("--issuer", help="defaults to CAREER_OAUTH_ISSUER")
    ol.add_argument("--revoke", metavar="ID", help="revoke a link (id from the list)")
    tk = sub.add_parser("token", help=f"list, issue or revoke API tokens ({TOKEN_ENV}, owner only)")
    tk_sub = tk.add_subparsers(dest="token_cmd", required=True)
    tk_sub.add_parser("list", help="token ids, labels and status (never the tokens)")
    ti = tk_sub.add_parser("issue", help="issue a new owner token (printed once)")
    ti.add_argument("--label", default="cli")
    tr = tk_sub.add_parser("revoke", help="revoke a token by id")
    tr.add_argument("id")
    sub.add_parser("maintenance", help="run periodic maintenance (draft purge) for all workspaces")
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
        sys.exit(asyncio.run(_export(settings, args.out, args.recipient)))
    elif args.cmd == "export-deletions":
        sys.exit(asyncio.run(_export_deletions(settings, args.out)))
    elif args.cmd == "restore" and args.merge:
        sys.exit(
            asyncio.run(
                _merge(
                    settings,
                    args.file,
                    dry_run=args.dry_run,
                    on_duplicate=args.on_duplicate,
                    identities=args.identity,
                    deletions=args.deletions,
                )
            )
        )
    elif args.cmd == "restore":
        sys.exit(
            asyncio.run(
                _restore(
                    settings,
                    args.file,
                    dry_run=args.dry_run,
                    new_ids=args.new_ids,
                    identities=args.identity,
                    deletions=args.deletions,
                )
            )
        )
    elif args.cmd == "oauth-link":
        sys.exit(asyncio.run(_oauth_link(settings, args.subject, args.issuer, args.revoke)))
    elif args.cmd == "token":
        sys.exit(
            asyncio.run(
                _token(
                    settings,
                    args.token_cmd,
                    token_id=getattr(args, "id", None),
                    label=getattr(args, "label", None),
                )
            )
        )
    elif args.cmd == "maintenance":
        sys.exit(asyncio.run(_maintenance(settings)))


if __name__ == "__main__":
    main()

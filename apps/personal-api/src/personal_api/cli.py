"""personal-api 운영 명령.

personal-api migrate     # core → personal 순서로 migration (소유자 계정)
personal-api bootstrap   # 공간 + owner + API 토큰 생성 (앱 계정), 토큰은 한 번만 출력
"""

from __future__ import annotations

import argparse
import asyncio

from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import Database
from career_core.db.migrate import upgrade
from career_core.jobs.worker import parsing_handlers, run_once, run_until_idle
from career_core.policies import ProviderEgressPolicy
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.workspaces import create_workspace

from personal_api.identity import issue_token
from personal_api.migrations import PERSONAL_CHAIN
from personal_api.settings import Settings


async def _worker(settings: Settings, *, once: bool) -> None:
    db = Database(settings.database_url)
    deps = ParsingDeps(
        db=db,
        blobs=LocalBlobStore(settings.blob_dir),
        egress=ProviderEgressPolicy({}),  # 외부 제공자 미설정 = 외부 전송 없음
        local_parsers=default_local_parsers(),
    )
    handlers = parsing_handlers(deps)
    try:
        if once:
            print(f"processed {await run_until_idle(db, handlers)} job(s)")
            return
        while True:
            if not await run_once(db, handlers):
                await asyncio.sleep(1)
    finally:
        await db.dispose()


async def _bootstrap(settings: Settings, workspace_name: str, display_name: str) -> str:
    db = Database(settings.database_url)
    try:
        ws, actor = await create_workspace(db, name=workspace_name, owner_display_name=display_name)
        return await issue_token(db, workspace_id=ws, actor_id=actor, label="bootstrap")
    finally:
        await db.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="personal-api")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply core + personal migrations")
    b = sub.add_parser("bootstrap", help="create a workspace, owner and API token (run once)")
    b.add_argument("--workspace-name", default="내 커리어")
    b.add_argument("--display-name", default="me")
    w = sub.add_parser("worker", help="process background jobs")
    w.add_argument("--once", action="store_true", help="process pending jobs and exit")
    args = parser.parse_args(argv)
    settings = Settings.from_env()

    if args.cmd == "migrate":
        upgrade(settings.migration_database_url, [PERSONAL_CHAIN])
        print("migrations applied: core, personal")
    elif args.cmd == "bootstrap":
        token = asyncio.run(_bootstrap(settings, args.workspace_name, args.display_name))
        print("API token (shown once, store it safely):")
        print(token)
    elif args.cmd == "worker":
        asyncio.run(_worker(settings, once=args.once))


if __name__ == "__main__":
    main()

"""personal-api 운영 명령.

personal-api migrate     # core → personal 순서로 migration (소유자 계정)
personal-api bootstrap   # 공간 + owner + API 토큰 생성 (앱 계정), 토큰은 한 번만 출력
"""

from __future__ import annotations

import argparse
import asyncio

from career_core.db import Database
from career_core.db.migrate import upgrade
from career_core.usecases.workspaces import create_workspace

from personal_api.identity import issue_token
from personal_api.migrations import PERSONAL_CHAIN
from personal_api.settings import Settings


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
    args = parser.parse_args(argv)
    settings = Settings.from_env()

    if args.cmd == "migrate":
        upgrade(settings.migration_database_url, [PERSONAL_CHAIN])
        print("migrations applied: core, personal")
    elif args.cmd == "bootstrap":
        token = asyncio.run(_bootstrap(settings, args.workspace_name, args.display_name))
        print("API token (shown once, store it safely):")
        print(token)


if __name__ == "__main__":
    main()

"""personal-api export / restore: 빈 설치에 복원하고 새 토큰으로 같은 공간에 들어간다 (합성 자료)."""

import pytest
from career_core.adapters.blobstore import LocalBlobStore
from career_core.db import Database
from career_core.testing import migrated_database
from career_core.usecases import brain
from career_core.usecases.sources import upload_source
from personal_api import cli
from personal_api.identity import PersonalTokenResolver
from personal_api.migrations import PERSONAL_CHAIN
from personal_api.settings import Settings
from sqlalchemy import text
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.anyio


async def test_export_then_restore_into_empty_install(pg, db, tmp_path, monkeypatch, capsys):
    source = Settings(database_url=pg.app.render_as_string(False), blob_dir=str(tmp_path / "a"))
    token = await cli._bootstrap(source, "합성 공간", "me")
    actor = await PersonalTokenResolver(db).resolve(token)
    await upload_source(
        db,
        LocalBlobStore(source.blob_dir),
        actor,
        filename="note.txt",
        data=b"synthetic note for backup",
        declared_mime="text/plain",
        max_bytes=1 << 20,
    )
    exp = await brain.create_experience(db, actor, title="합성 경험")
    monkeypatch.setenv(cli.TOKEN_ENV, token)
    out = tmp_path / "backup.tar.gz"
    assert await cli._export(source, str(out)) == 0
    assert "wrote" in capsys.readouterr().out

    with migrated_database([PERSONAL_CHAIN]) as urls:
        target = Settings(
            database_url=urls.app.render_as_string(False), blob_dir=str(tmp_path / "b")
        )
        assert await cli._restore(target, str(out), dry_run=True) == 0
        assert "dry run" in capsys.readouterr().out
        assert await cli._restore(target, str(out), dry_run=False) == 0
        printed = capsys.readouterr().out
        new_token = printed.split("store it safely):\n", 1)[1].split("\n", 1)[0]
        assert new_token.startswith("cbp_") and new_token != token
        tdb = Database(urls.app, poolclass=NullPool)
        try:
            restored = await PersonalTokenResolver(tdb).resolve(new_token)
            assert restored.workspace_id == actor.workspace_id
            assert restored.subject_id == actor.subject_id
            # 옛 토큰은 따라오지 않는다
            assert await PersonalTokenResolver(tdb).resolve(token) is None
            assert (await brain.get_experience(tdb, restored, exp)).title == "합성 경험"
            async with tdb.workspace_session(restored.workspace_id) as s:
                kinds = (await s.execute(text("SELECT kind FROM core.jobs"))).scalars().all()
            assert kinds == ["embed"]  # 검색 벡터는 다시 만든다
        finally:
            await tdb.dispose()
        assert await cli._restore(target, str(out), dry_run=False) == 1
        assert "target_not_empty" in capsys.readouterr().out

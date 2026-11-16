"""personal-api export / restore: 빈 설치에 복원하고 새 토큰으로 같은 공간에 들어간다 (합성 자료)."""

import json
from datetime import UTC, datetime

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
            # 검색 벡터는 다시 만들고, 내보낼 때 대기 중이던 파싱도 다시 넣는다
            assert sorted(kinds) == ["embed", "parse"]
        finally:
            await tdb.dispose()
        assert await cli._restore(target, str(out), dry_run=False) == 1
        assert "target_not_empty" in capsys.readouterr().out


async def test_restore_merge_into_the_token_workspace(pg, db, tmp_path, monkeypatch, capsys):
    """restore --merge: 자료가 있는 공간에 합친다. 건너뛴 삭제 기록은 이유와 함께 출력한다."""
    settings = Settings(database_url=pg.app.render_as_string(False), blob_dir=str(tmp_path / "a"))
    token = await cli._bootstrap(settings, "합칠 공간 (합성)", "me")
    actor = await PersonalTokenResolver(db).resolve(token)
    exp = await brain.create_experience(db, actor, title="지웠다가 되살릴 경험 (합성)")
    kept = await brain.create_experience(db, actor, title="남아 있는 경험 (합성)")
    monkeypatch.setenv(cli.TOKEN_ENV, token)
    out = tmp_path / "backup.tar.gz"
    assert await cli._export(settings, str(out)) == 0
    async with db.workspace_session(actor.workspace_id) as s:
        await s.execute(text("DELETE FROM core.experiences WHERE id = :e"), {"e": exp})
    capsys.readouterr()

    assert await cli._merge(settings, str(out), dry_run=True, on_duplicate="skip") == 0
    printed = capsys.readouterr().out
    assert "checked archive" in printed and "1 row(s) added" in printed
    assert "core.experiences" in printed and "dry run" in printed
    assert await cli._merge(settings, str(out), dry_run=True, on_duplicate="fail") == 1
    assert "duplicates_found" in capsys.readouterr().out

    # 삭제 기록: 되살린 경험에는 다시 적용, 원래 있던 경험(합성 기록)은 이유와 함께 건너뜀
    log = tmp_path / "deletions.jsonl"
    assert await cli._export_deletions(settings, str(log)) == 0
    at = datetime.now(UTC).isoformat()
    row = {"target_table": "core.experiences", "target_id": str(kept), "deleted_at": at}
    with log.open("a") as f:
        f.write(json.dumps(row) + "\n")
    capsys.readouterr()
    merged = await cli._merge(
        settings, str(out), dry_run=False, on_duplicate="skip", deletions=[str(log)]
    )
    assert merged == 0
    printed = capsys.readouterr().out
    assert "merged archive" in printed and "re-applied 1 deletion(s), skipped 1" in printed
    assert f"skipped core.experiences {kept} — kept_existing" in printed
    titles = {e.title for e in await brain.list_experiences(db, actor)}
    assert titles == {"남아 있는 경험 (합성)"}

"""공간 내보내기·복원 (O11).

묶음 파일 (tar.gz, 형식 버전 ``FORMAT_VERSION``)::

    manifest.json                     형식·migration head·공간·표마다 행 수·sha256·칸 목록·원본 목록
    tables/<schema>.<table>.jsonl     한 줄 = 한 행 (PostgreSQL ``to_jsonb``, UTC), 기본 키 순서
    blobs/<sha256>                    행이 참조하는 원본 파일 (내용 주소)

- 표는 코드 목록이 아니라 **DB 카탈로그**에서 찾는다. ``workspace_id`` 칸이 있는 표는 모두 들어가고,
  빼는 표는 ``EXCLUDED_TABLES``에 이유와 함께 적는다. 새 migration이 표를 더해도 코드 수정 없이 따라온다.
- 넣는 순서는 FK로 정한다. 복원은 INSERT만 한다 (추가만 되는 표도 그대로 지켜진다).
  표끼리 FK가 순환하면(공고 ↔ 확정 버전, revision ↔ 수정안) 순환의 한 FK를 ``DEFERRABLE``로 두어
  (c0011) 트랜잭션 끝에서 검사하고, 그래도 남는 표 순환은 행 단위 순서(부모 행 먼저)로 넣는다.
  새 표가 미룰 수 없는 행 순환을 만들면 계획 단계에서 오류로 알린다.
- 내보내기·복원 모두 앱 계정(RLS 적용)으로, 그 공간 문맥 안에서만 읽고 쓴다.
  다른 공간의 행은 WHERE와 RLS 두 겹으로 빠지고, 복원 때 다른 공간 ID가 섞인 행은 RLS가 거부한다.
- 해시는 묶음의 무결성 검사다(우연한 손상·부분 수정). 서명이 아니므로 출처 인증은 하지 않는다.
"""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import io
import json
import os
import re
import tarfile
import tempfile
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncConnection

from career_core import __version__
from career_core.db.models import ExportRun
from career_core.db.rls import WORKSPACE_SETTING
from career_core.db.session import Database
from career_core.domain.workspace import ScopePermissionPolicy
from career_core.ports import ActorContext, BlobStore, PermissionPolicy

FORMAT = "career-brain-workspace-export"
FORMAT_VERSION = 1
SCHEMAS = ("core",)
MANIFEST = "manifest.json"

# 공간 자료지만 묶음에 넣지 않는 표. 새로 빼는 표는 이유를 같이 적는다.
EXCLUDED_TABLES: dict[str, str] = {
    "core.jobs": "작업 큐(일시 상태, 대상 ID뿐). 복원 후 필요한 작업은 다시 만든다",
    "core.embeddings": "검색 벡터. 모델·차원에 따라 다르고 원문에서 다시 만든다 (복원 후 re-index)",
}
# SCHEMAS 밖(personal.api_tokens의 토큰 해시, 서비스 platform의 계정·결제)은 처음부터 읽지 않는다.
OUT_OF_SCOPE = {
    "personal.*": "로그인 토큰(해시)·세션. 복원한 공간에는 새 토큰을 발급한다",
    "platform.*": "서비스 계정·결제 (career-service 소유)",
}

# workspace_id가 없는 공간 범위 표.
_ROOT = "root"  # core.workspaces: id = 공간
_MEMBERS = "members"  # core.actors: 이 공간 membership이 있는 actor (여러 공간이 같이 쓸 수 있음)
_WORKSPACE = "workspace"
_SPECIAL_SCOPES = {"core.workspaces": _ROOT, "core.actors": _MEMBERS}

# 원본 파일(BlobStore key)을 담는 칸. 이름 규칙(blob_key, *_blob_key) + 명시 목록.
BLOB_COLUMNS = frozenset({("core.job_posting_versions", "screenshot_key")})

_BLOB_KEY = re.compile(r"[0-9a-f]{64}")
_TABLE_FILE = re.compile(r"tables/([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)\.jsonl")
_BLOB_FILE = re.compile(r"blobs/([0-9a-f]{64})")
_BATCH = 500


class ExportError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class PlanError(ExportError):
    def __init__(self, detail: str) -> None:
        super().__init__("unsupported_schema", detail)


class ArchiveError(ExportError):
    """묶음이 손상됐거나, 형식·버전이 맞지 않거나, 대상이 비어 있지 않다."""


# ---------------------------------------------------------------- 카탈로그·계획


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    type: str
    not_null: bool


@dataclass(frozen=True, slots=True)
class ForeignKey:
    name: str
    table: str
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]
    deferrable: bool = False  # 트랜잭션 끝에서 검사할 수 있다 → 넣는 순서에서 뺀다


@dataclass(frozen=True, slots=True)
class Table:
    name: str  # schema.table
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKey, ...]
    scope: str | None  # workspace | root | members | None(공간 범위 없음)

    @property
    def sql(self) -> str:
        schema, table = self.name.split(".", 1)
        return f'"{schema}"."{table}"'

    @property
    def blob_columns(self) -> tuple[str, ...]:
        return tuple(
            c.name
            for c in self.columns
            if c.name == "blob_key"
            or c.name.endswith("_blob_key")
            or (self.name, c.name) in BLOB_COLUMNS
        )


@dataclass(frozen=True, slots=True)
class ExportPlan:
    tables: tuple[Table, ...]  # 넣는 순서
    groups: tuple[frozenset[str], ...]  # FK가 순환해 행 단위 순서로 넣는 표 묶음
    deferred: tuple[ForeignKey, ...]  # 트랜잭션 끝에서 검사하는 FK (순서 계산에서 뺀다)
    excluded: dict[str, str]
    skipped: dict[str, str]  # 공간 범위가 없는 표 (alembic_version 등)

    def table(self, name: str) -> Table:
        return next(t for t in self.tables if t.name == name)

    def group_of(self, name: str) -> frozenset[str] | None:
        return next((g for g in self.groups if name in g), None)


_CATALOG_TABLES = """
SELECT c.oid, n.nspname || '.' || c.relname AS name
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p') AND n.nspname = ANY(:schemas)
"""
_CATALOG_COLUMNS = """
SELECT a.attrelid AS oid, a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type,
       a.attnotnull AS not_null
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p') AND n.nspname = ANY(:schemas) AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attrelid, a.attnum
"""
_CATALOG_CONSTRAINTS = """
SELECT con.conname AS name, con.contype AS kind, con.conrelid AS oid,
       con.condeferrable AS deferrable, rn.nspname || '.' || rc.relname AS ref_table,
       ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(n, i)
             JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n
             ORDER BY k.i) AS cols,
       ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(n, i)
             JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.n
             ORDER BY k.i) AS ref_cols
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_class rc ON rc.oid = con.confrelid
LEFT JOIN pg_namespace rn ON rn.oid = rc.relnamespace
WHERE con.contype IN ('p', 'f') AND n.nspname = ANY(:schemas)
ORDER BY con.conname
"""


async def read_catalog(conn: AsyncConnection, schemas: Sequence[str] = SCHEMAS) -> list[Table]:
    """표·칸·기본 키·FK (pg_catalog, 앱 계정으로도 읽힌다)."""
    params = {"schemas": list(schemas)}
    tables = (await conn.execute(text(_CATALOG_TABLES), params)).all()
    columns: dict[int, list[Column]] = {}
    for r in (await conn.execute(text(_CATALOG_COLUMNS), params)).all():
        columns.setdefault(r.oid, []).append(Column(r.name, r.type, r.not_null))
    pks: dict[int, tuple[str, ...]] = {}
    fks: dict[int, list[ForeignKey]] = {}
    names = {t.oid: t.name for t in tables}
    for r in (await conn.execute(text(_CATALOG_CONSTRAINTS), params)).all():
        if r.kind == "p":
            pks[r.oid] = tuple(r.cols)
        else:
            fks.setdefault(r.oid, []).append(
                ForeignKey(
                    r.name,
                    names[r.oid],
                    tuple(r.cols),
                    r.ref_table,
                    tuple(r.ref_cols),
                    bool(r.deferrable),
                )
            )
    out = []
    for t in tables:
        cols = tuple(columns.get(t.oid, ()))
        scope = _SPECIAL_SCOPES.get(t.name)
        if scope is None and any(c.name == "workspace_id" for c in cols):
            scope = _WORKSPACE
        out.append(
            Table(
                name=t.name,
                columns=cols,
                primary_key=pks.get(t.oid, ()),
                foreign_keys=tuple(fks.get(t.oid, ())),
                scope=scope,
            )
        )
    return sorted(out, key=lambda t: t.name)


def build_plan(tables: Sequence[Table], excluded: dict[str, str] | None = None) -> ExportPlan:
    """내보낼 표와 넣는 순서.

    DEFERRABLE FK는 트랜잭션 끝에서 검사하므로 순서 계산에서 뺀다. 그래도 표끼리 순환하면
    그 묶음은 행 단위 순서로 넣는다 (``_row_order``).
    """
    excluded = EXCLUDED_TABLES if excluded is None else excluded
    present = {t.name for t in tables}
    chosen = {t.name: t for t in tables if t.scope is not None and t.name not in excluded}
    skipped = {t.name: "공간 범위 없음" for t in tables if t.scope is None}
    edges: dict[str, set[str]] = {name: set() for name in chosen}
    deferred: list[ForeignKey] = []
    for t in chosen.values():
        if not t.primary_key:
            raise PlanError(f"{t.name} has no primary key")
        for fk in t.foreign_keys:
            if fk.ref_table not in chosen:
                raise PlanError(
                    f"{t.name}.{fk.name} references {fk.ref_table}, which is not exported"
                )
            if fk.deferrable:
                deferred.append(fk)
            else:
                edges[t.name].add(fk.ref_table)

    order: list[Table] = []
    groups: list[frozenset[str]] = []
    for comp in _components(sorted(chosen), edges):
        members = sorted(comp)
        order.extend(chosen[n] for n in members)
        if len(comp) > 1 or members[0] in edges[members[0]]:
            groups.append(frozenset(comp))
    return ExportPlan(
        tables=tuple(order),
        groups=tuple(groups),
        deferred=tuple(deferred),
        excluded={k: v for k, v in excluded.items() if k in present},
        skipped=skipped,
    )


def _components(nodes: list[str], edges: dict[str, set[str]]) -> list[set[str]]:
    """강한 연결 요소 (Tarjan). 간선은 자식 → 부모라서 부모 쪽 요소가 먼저 나온다."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    out: list[set[str]] = []
    counter = 0

    def visit(v: str) -> None:
        nonlocal counter
        index[v] = low[v] = counter
        counter += 1
        stack.append(v)
        on_stack.add(v)
        for w in sorted(edges[v]):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = set()
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.add(w)
                if w == v:
                    break
            out.append(comp)

    for v in nodes:
        if v not in index:
            visit(v)
    return out


def core_migration_heads() -> list[str]:
    """이 코드가 아는 core migration head. 묶음과 대상이 같아야 복원한다."""
    from alembic.script import ScriptDirectory

    from career_core.db.migrate import CORE_CHAIN

    return sorted(ScriptDirectory(str(CORE_CHAIN.script_location)).get_heads())


# ---------------------------------------------------------------- 공통 SQL


def scope_sql(table: Table) -> str:
    if table.scope == _ROOT:
        return "t.id = CAST(:ws AS uuid)"
    if table.scope == _MEMBERS:
        return (
            't.id IN (SELECT m.actor_id FROM "core"."memberships" m '
            "WHERE m.workspace_id = CAST(:ws AS uuid))"
        )
    return "t.workspace_id = CAST(:ws AS uuid)"


def _rows_sql(table: Table) -> str:
    order = ", ".join(f't."{c}"' for c in table.primary_key)
    return (
        f"SELECT to_jsonb(t)::text AS j FROM {table.sql} AS t "
        f"WHERE {scope_sql(table)} ORDER BY {order}"
    )


async def _enter_workspace(conn: AsyncConnection, workspace_id: UUID) -> None:
    """공간 문맥 + 출력 형식 고정 (해시가 서버 설정에 흔들리지 않게). 모두 트랜잭션 로컬."""
    await conn.execute(
        text("SELECT set_config(:key, :ws, true)"),
        {"key": WORKSPACE_SETTING, "ws": str(workspace_id)},
    )
    for stmt in (
        "SET LOCAL TIME ZONE 'UTC'",
        "SET LOCAL extra_float_digits = 1",
        "SET LOCAL bytea_output = 'hex'",
    ):
        await conn.execute(text(stmt))


async def _table_digest(conn: AsyncConnection, table: Table, ws: UUID) -> tuple[int, str]:
    h = hashlib.sha256()
    n = 0
    result = await conn.stream(text(_rows_sql(table)), {"ws": str(ws)})
    async for row in result:
        h.update(row.j.encode() + b"\n")
        n += 1
    return n, h.hexdigest()


def _columns_signature(table: Table) -> list[list[str]]:
    return [[c.name, c.type] for c in table.columns]


# ---------------------------------------------------------------- 내보내기


@dataclass(frozen=True, slots=True)
class TableReport:
    name: str
    rows: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ExportReport:
    path: Path
    workspace_id: UUID
    sha256: str
    bytes: int
    tables: list[TableReport]
    blobs: int
    missing_blobs: list[dict]
    manifest: dict

    @property
    def rows_total(self) -> int:
        return sum(t.rows for t in self.tables)


async def export_workspace(
    db: Database,
    blobs: BlobStore,
    actor: ActorContext,
    out: str | os.PathLike[str],
    *,
    via: str = "cli",
    permissions: PermissionPolicy | None = None,
) -> ExportReport:
    """actor의 공간 하나를 ``out``(tar.gz)으로 쓴다. 소유자(workspace-admin)만."""
    if not (permissions or ScopePermissionPolicy()).allows(actor, "workspace.export"):
        raise ExportError("forbidden")
    ws = actor.workspace_id
    out = Path(out)
    with tempfile.TemporaryDirectory(prefix="career-export-") as tmp:
        staging = Path(tmp)
        tables, blob_keys, workspace_name, plan = await _dump_tables(db, ws, staging)
        blob_entries, missing = await _check_blobs(blobs, ws, blob_keys)
        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "core_version": __version__,
            "migration_heads": core_migration_heads(),
            "created_at": datetime.now(UTC).isoformat(),
            "workspace": {"id": str(ws), "name": workspace_name},
            "tables": [
                {
                    "name": t.name,
                    "file": _table_file(t.name),
                    "rows": tables[t.name].rows,
                    "sha256": tables[t.name].sha256,
                    "columns": _columns_signature(t),
                }
                for t in plan.tables
            ],
            "blobs": blob_entries,
            "missing_blobs": missing,
            "excluded": {**plan.excluded, **OUT_OF_SCOPE},
            "skipped": plan.skipped,
        }
        partial = out.with_name(f".{out.name}.{uuid4().hex}.part")
        try:
            # 압축·파일 쓰기는 스레드에서 (HTTP 내려받기 중에도 이벤트 루프를 막지 않게).
            tar = await asyncio.to_thread(tarfile.open, partial, "w:gz")
            with tar:
                data = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
                await asyncio.to_thread(_add_bytes, tar, MANIFEST, data)
                for t in plan.tables:
                    data = await asyncio.to_thread((staging / _table_file(t.name)).read_bytes)
                    await asyncio.to_thread(_add_bytes, tar, _table_file(t.name), data)
                for entry in blob_entries:
                    data = await blobs.get(ws, entry["key"])
                    if hashlib.sha256(data).hexdigest() != entry["key"]:
                        raise ExportError("blob_changed", entry["key"])
                    await asyncio.to_thread(_add_bytes, tar, f"blobs/{entry['key']}", data)
            os.replace(partial, out)
        finally:
            partial.unlink(missing_ok=True)
    digest, size = await asyncio.to_thread(_file_digest, out)
    report = ExportReport(
        path=out,
        workspace_id=ws,
        sha256=digest,
        bytes=size,
        tables=[tables[t.name] for t in plan.tables],
        blobs=len(blob_entries),
        missing_blobs=missing,
        manifest=manifest,
    )
    async with db.workspace_session(ws) as s:
        await s.execute(
            insert(ExportRun).values(
                id=uuid4(),
                workspace_id=ws,
                kind="export",
                via=via,
                created_by=actor.subject_id,
                format_version=FORMAT_VERSION,
                archive_sha256=digest,
                archive_bytes=size,
                rows_total=report.rows_total,
                blobs_total=report.blobs,
                created_at=datetime.now(UTC),
            )
        )
    return report


async def _dump_tables(
    db: Database, ws: UUID, staging: Path
) -> tuple[dict[str, TableReport], dict[str, set[str]], str, ExportPlan]:
    """한 스냅샷(REPEATABLE READ, 읽기 전용) 안에서 모든 표를 줄 파일로 쓴다."""
    (staging / "tables").mkdir()
    reports: dict[str, TableReport] = {}
    blob_keys: dict[str, set[str]] = {}
    async with db.engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="REPEATABLE READ")
        async with conn.begin():
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            await _enter_workspace(conn, ws)
            plan = build_plan(await read_catalog(conn))
            name = await conn.scalar(
                text('SELECT name FROM "core"."workspaces" WHERE id = CAST(:ws AS uuid)'),
                {"ws": str(ws)},
            )
            if name is None:
                raise ExportError("workspace_not_found")
            for t in plan.tables:
                h = hashlib.sha256()
                n = 0
                chunks: list[bytes] = []
                result = await conn.stream(text(_rows_sql(t)), {"ws": str(ws)})
                async for row in result:
                    line = row.j.encode() + b"\n"
                    chunks.append(line)
                    h.update(line)
                    n += 1
                await asyncio.to_thread(
                    (staging / _table_file(t.name)).write_bytes, b"".join(chunks)
                )
                reports[t.name] = TableReport(t.name, n, h.hexdigest())
                for col in t.blob_columns:
                    keys = await conn.scalars(
                        text(
                            f'SELECT DISTINCT t."{col}" FROM {t.sql} AS t '
                            f'WHERE {scope_sql(t)} AND t."{col}" IS NOT NULL'
                        ),
                        {"ws": str(ws)},
                    )
                    for key in keys:
                        blob_keys.setdefault(key, set()).add(f"{t.name}.{col}")
    return reports, blob_keys, name, plan


async def _check_blobs(
    blobs: BlobStore, ws: UUID, keys: dict[str, set[str]]
) -> tuple[list[dict], list[dict]]:
    entries: list[dict] = []
    missing: list[dict] = []
    for key in sorted(keys):
        refs = sorted(keys[key])
        if not _BLOB_KEY.fullmatch(key):
            missing.append({"key": key, "refs": refs, "reason": "invalid_key"})
            continue
        try:
            data = await blobs.get(ws, key)
        except (FileNotFoundError, LookupError):
            missing.append({"key": key, "refs": refs, "reason": "not_found"})
            continue
        if hashlib.sha256(data).hexdigest() != key:
            missing.append({"key": key, "refs": refs, "reason": "hash_mismatch"})
            continue
        entries.append({"key": key, "bytes": len(data), "refs": refs})
    return entries, missing


def _table_file(name: str) -> str:
    return f"tables/{name}.jsonl"


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(datetime.now(UTC).timestamp())
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(data))


def _file_digest(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


# ---------------------------------------------------------------- 묶음 읽기


@dataclass(slots=True)
class _Archive:
    manifest: dict
    lines: dict[str, list[str]]  # 표 → 줄 (원문 그대로)
    blob_dir: Path
    blob_keys: list[str]
    sha256: str
    bytes: int


def read_archive(path: str | os.PathLike[str], staging: Path) -> _Archive:
    """묶음을 한 번 훑으며 형식·해시를 확인한다. 원본은 ``staging``에 임시로 꺼낸다.

    정해진 이름의 일반 파일만 받는다 (링크·장치·경로 탈출·중복 이름 거부).
    """
    path = Path(path)
    digest, size = _file_digest(path)
    blob_dir = staging / "blobs"
    blob_dir.mkdir()
    manifest: dict | None = None
    raw_tables: dict[str, bytes] = {}
    seen: set[str] = set()
    try:
        with tarfile.open(path, "r|gz") as tar:
            for member in tar:
                name = member.name
                if name in seen or not member.isreg():
                    raise ArchiveError("unexpected_member", name)
                seen.add(name)
                f = tar.extractfile(member)
                data = f.read() if f else b""
                if manifest is None:
                    if name != MANIFEST:
                        raise ArchiveError("missing_manifest", "manifest.json must come first")
                    manifest = _parse_manifest(data)
                    continue
                if m := _TABLE_FILE.fullmatch(name):
                    raw_tables[m.group(1)] = data
                elif m := _BLOB_FILE.fullmatch(name):
                    key = m.group(1)
                    if hashlib.sha256(data).hexdigest() != key:
                        raise ArchiveError("hash_mismatch", name)
                    (blob_dir / key).write_bytes(data)
                else:
                    raise ArchiveError("unexpected_member", name)
    except (tarfile.TarError, EOFError, OSError, zlib.error) as exc:
        raise ArchiveError("not_an_archive", type(exc).__name__) from exc
    if manifest is None:
        raise ArchiveError("missing_manifest")

    lines: dict[str, list[str]] = {}
    listed = {t["name"] for t in manifest["tables"]}
    if extra := set(raw_tables) - listed:
        raise ArchiveError("unexpected_member", ", ".join(sorted(extra)))
    for entry in manifest["tables"]:
        name = entry["name"]
        if name not in raw_tables:
            raise ArchiveError("missing_member", _table_file(name))
        data = raw_tables[name]
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ArchiveError("hash_mismatch", _table_file(name))
        rows = data.decode().splitlines()
        if len(rows) != entry["rows"]:
            raise ArchiveError("count_mismatch", name)
        lines[name] = rows
    keys = [b["key"] for b in manifest["blobs"]]
    present = {p.name for p in blob_dir.iterdir()}
    if missing := set(keys) - present:
        raise ArchiveError("missing_member", f"{len(missing)} blob(s)")
    if extra := present - set(keys):
        raise ArchiveError("unexpected_member", f"{len(extra)} unlisted blob(s)")
    return _Archive(manifest, lines, blob_dir, keys, digest, size)


def _parse_manifest(data: bytes) -> dict:
    try:
        m = json.loads(data)
    except ValueError as exc:
        raise ArchiveError("invalid_manifest") from exc
    if not isinstance(m, dict) or m.get("format") != FORMAT:
        raise ArchiveError("format_unsupported", "not a career-brain workspace export")
    if m.get("format_version") != FORMAT_VERSION:
        raise ArchiveError(
            "version_mismatch", f"format {m.get('format_version')}, expected {FORMAT_VERSION}"
        )
    try:
        UUID(m["workspace"]["id"])
        ok = isinstance(m["migration_heads"], list) and isinstance(m["workspace"]["name"], str)
        for t in m["tables"]:
            ok = ok and (
                bool(_TABLE_FILE.fullmatch(_table_file(t["name"])))
                and isinstance(t["rows"], int)
                and bool(_BLOB_KEY.fullmatch(t["sha256"]))
                and isinstance(t["columns"], list)
            )
        for b in m["blobs"]:
            ok = ok and bool(_BLOB_KEY.fullmatch(b["key"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ArchiveError("invalid_manifest", type(exc).__name__) from exc
    if not ok:
        raise ArchiveError("invalid_manifest")
    return m


# ---------------------------------------------------------------- 복원


@dataclass(frozen=True, slots=True)
class RestoreReport:
    workspace_id: UUID
    workspace_name: str
    tables: list[TableReport]
    blobs: int
    dry_run: bool
    owners: list[UUID]
    archive_sha256: str
    notes: list[str] = field(default_factory=list)

    @property
    def rows_total(self) -> int:
        return sum(t.rows for t in self.tables)


async def restore_workspace(
    db: Database,
    blobs: BlobStore,
    archive: str | os.PathLike[str],
    *,
    dry_run: bool = False,
    via: str = "cli",
    expected_heads: Sequence[str] | None = None,
) -> RestoreReport:
    """빈 대상(같은 공간 ID의 자료가 없는 DB + 원본 저장소)에 묶음을 되살린다.

    순서: 묶음 해시 확인 → 버전·칸 구성 비교 → 대상이 비었는지 확인 → 한 트랜잭션에서
    행 넣기(ID 그대로) → 다시 읽어 행 수·해시 비교 → 원본 저장(sha256 확인) → commit.
    ``dry_run``이면 마지막에 rollback하고 원본도 쓰지 않는다.
    """
    with tempfile.TemporaryDirectory(prefix="career-restore-") as tmp:
        arc = await asyncio.to_thread(read_archive, archive, Path(tmp))
        m = arc.manifest
        heads = sorted(expected_heads) if expected_heads is not None else core_migration_heads()
        if sorted(m["migration_heads"]) != heads:
            raise ArchiveError(
                "version_mismatch",
                f"archive migrations {m['migration_heads']}, this install {heads}",
            )
        ws = UUID(m["workspace"]["id"])
        async with db.engine.connect() as conn:
            trans = await conn.begin()
            try:
                await _enter_workspace(conn, ws)
                plan = build_plan(await read_catalog(conn))
                _check_schema(plan, m)
                await _check_empty(conn, plan, ws)
                await conn.execute(text("SET CONSTRAINTS ALL DEFERRED"))
                await _insert_all(conn, plan, arc.lines)
                # 미뤄 둔 FK를 지금 검사한다 (commit 전에 실패를 알 수 있게).
                await conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                reports = await _verify(conn, plan, m, ws)
                if not dry_run:
                    await conn.execute(
                        insert(ExportRun).values(
                            id=uuid4(),
                            workspace_id=ws,
                            kind="restore",
                            via=via,
                            created_by=None,
                            format_version=FORMAT_VERSION,
                            archive_sha256=arc.sha256,
                            archive_bytes=arc.bytes,
                            rows_total=sum(r.rows for r in reports),
                            blobs_total=len(arc.blob_keys),
                            created_at=datetime.now(UTC),
                        )
                    )
                    for key in arc.blob_keys:
                        data = await asyncio.to_thread((arc.blob_dir / key).read_bytes)
                        await blobs.put(ws, key, data, "application/octet-stream")
                        if hashlib.sha256(await blobs.get(ws, key)).hexdigest() != key:
                            raise ArchiveError("blob_write_failed", key)
                    await trans.commit()
                else:
                    await trans.rollback()
            except BaseException:
                if trans.is_active:
                    await trans.rollback()
                raise
    notes = []
    if m.get("missing_blobs"):
        notes.append(f"{len(m['missing_blobs'])} blob(s) were missing at export time")
    return RestoreReport(
        workspace_id=ws,
        workspace_name=m["workspace"]["name"],
        tables=reports,
        blobs=len(arc.blob_keys),
        dry_run=dry_run,
        owners=_owners(arc.lines.get("core.memberships", [])),
        archive_sha256=arc.sha256,
        notes=notes,
    )


def _check_schema(plan: ExportPlan, manifest: dict) -> None:
    """같은 migration head라도 표·칸 구성이 실제로 같은지 본다 (부분 migration 등)."""
    archived = {t["name"]: t["columns"] for t in manifest["tables"]}
    target = {t.name: _columns_signature(t) for t in plan.tables}
    if set(archived) != set(target):
        diff = sorted(set(archived) ^ set(target))
        raise ArchiveError("schema_mismatch", f"tables differ: {', '.join(diff)}")
    for name, cols in archived.items():
        if cols != target[name]:
            raise ArchiveError("schema_mismatch", f"columns differ: {name}")


async def _check_empty(conn: AsyncConnection, plan: ExportPlan, ws: UUID) -> None:
    for t in plan.tables:
        if t.scope == _MEMBERS:
            continue  # actor는 여러 공간이 같이 쓸 수 있다 (같은 ID면 그대로 둔다)
        found = await conn.scalar(
            text(f"SELECT EXISTS (SELECT 1 FROM {t.sql} AS t WHERE {scope_sql(t)})"),
            {"ws": str(ws)},
        )
        if found:
            raise ArchiveError("target_not_empty", f"{t.name} already has rows for {ws}")


def _insert_sql(table: Table) -> str:
    """JSON 줄 묶음을 그 표의 행 타입으로 풀어 그대로 넣는다 (ID·시각·값 모두 원본 그대로)."""
    cols = ", ".join(f'"{c.name}"' for c in table.columns)
    values = []
    for c in table.columns:
        if c.not_null and c.type in ("jsonb", "json"):
            # JSON null 값은 jsonb_populate_recordset에서 SQL NULL이 된다. NOT NULL 칸이면 되돌린다.
            values.append(f"COALESCE(r.\"{c.name}\", CAST('null' AS {c.type}))")
        else:
            values.append(f'r."{c.name}"')
    conflict = " ON CONFLICT DO NOTHING" if table.scope == _MEMBERS else ""
    return (
        f"INSERT INTO {table.sql} ({cols}) SELECT {', '.join(values)} "
        f"FROM jsonb_populate_recordset(CAST(NULL AS {table.sql}), CAST(:rows AS jsonb)) AS r"
        f"{conflict}"
    )


def _batches(lines: Sequence[str]) -> Iterable[str]:
    for i in range(0, len(lines), _BATCH):
        yield "[" + ",".join(lines[i : i + _BATCH]) + "]"


async def _insert_all(conn: AsyncConnection, plan: ExportPlan, lines: dict[str, list[str]]) -> None:
    done: set[str] = set()
    for t in plan.tables:
        if t.name in done:
            continue
        group = plan.group_of(t.name)
        steps = [(t.name, lines[t.name])] if group is None else _row_order(plan, group, lines)
        for name, chunk in steps:
            sql = text(_insert_sql(plan.table(name)))
            for batch in _batches(chunk):
                await conn.execute(sql, {"rows": batch})
        done.update(group or {t.name})


def _row_order(
    plan: ExportPlan, group: frozenset[str], lines: dict[str, list[str]]
) -> list[tuple[str, list[str]]]:
    """FK가 순환하는 표 묶음을 행 단위로 정렬한다 (부모 행이 먼저). 연속한 같은 표는 한 번에."""
    names = [t.name for t in plan.tables if t.name in group]
    nodes: list[tuple[str, str, dict[str, Any]]] = []
    for name in names:
        for line in lines[name]:
            nodes.append((name, line, json.loads(line, parse_float=Decimal)))
    index: dict[tuple[str, tuple[str, ...]], dict[tuple, int]] = {}

    def lookup(table: str, cols: tuple[str, ...]) -> dict[tuple, int]:
        key = (table, cols)
        if key not in index:
            index[key] = {
                tuple(row.get(c) for c in cols): i
                for i, (n, _, row) in enumerate(nodes)
                if n == table
            }
        return index[key]

    parents: list[set[int]] = [set() for _ in nodes]
    children: list[list[int]] = [[] for _ in nodes]
    for i, (name, _, row) in enumerate(nodes):
        for fk in plan.table(name).foreign_keys:
            if fk.ref_table not in group or fk.deferrable:
                continue
            value = tuple(row.get(c) for c in fk.columns)
            if any(v is None for v in value):
                continue
            j = lookup(fk.ref_table, fk.ref_columns).get(value)
            if j is not None and j != i and j not in parents[i]:
                parents[i].add(j)
                children[j].append(i)
    pending = [len(p) for p in parents]
    ready = [i for i, n in enumerate(pending) if n == 0]
    heapq.heapify(ready)
    order: list[int] = []
    while ready:
        i = heapq.heappop(ready)
        order.append(i)
        for c in children[i]:
            pending[c] -= 1
            if pending[c] == 0:
                heapq.heappush(ready, c)
    if len(order) != len(nodes):
        raise PlanError(f"rows in {sorted(group)} reference each other in a cycle")
    steps: list[tuple[str, list[str]]] = []
    for i in order:
        name, line, _ = nodes[i]
        if steps and steps[-1][0] == name:
            steps[-1][1].append(line)
        else:
            steps.append((name, [line]))
    return steps


async def _verify(
    conn: AsyncConnection, plan: ExportPlan, manifest: dict, ws: UUID
) -> list[TableReport]:
    expected = {t["name"]: t for t in manifest["tables"]}
    reports = []
    for t in plan.tables:
        rows, digest = await _table_digest(conn, t, ws)
        want = expected[t.name]
        if rows != want["rows"] or digest != want["sha256"]:
            raise ArchiveError(
                "verify_failed",
                f"{t.name}: restored {rows} rows / {digest[:12]}, "
                f"archive {want['rows']} / {want['sha256'][:12]}",
            )
        reports.append(TableReport(t.name, rows, digest))
    return reports


def _owners(membership_lines: Sequence[str]) -> list[UUID]:
    out = []
    for line in membership_lines:
        row = json.loads(line)
        if row.get("role") == "owner":
            out.append(UUID(row["actor_id"]))
    return out


__all__ = [
    "BLOB_COLUMNS",
    "EXCLUDED_TABLES",
    "FORMAT",
    "FORMAT_VERSION",
    "ArchiveError",
    "ExportError",
    "ExportPlan",
    "ExportReport",
    "PlanError",
    "RestoreReport",
    "TableReport",
    "build_plan",
    "core_migration_heads",
    "export_workspace",
    "read_archive",
    "read_catalog",
    "restore_workspace",
]

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
- 흘려 쓰기: 표는 줄 단위로 임시 파일에 쓰고 묶음에도 파일에서 조각씩 넣는다. 복원도 표 파일을 줄 단위로
  읽어 ``_BATCH``줄씩 넣는다. 메모리에 올리는 것은 manifest와 (드문) 행 순환 묶음뿐이다.
- 선택: age 암호화(``recipients``), 새 ID로 다른 빈 공간에 가져오기(``new_ids``), 대기 작업 다시 넣기,
  나중에 받은 삭제 기록 다시 적용(``deletion_logs``). docs/operations/backup.md 참고.
- 기존 공간에 합치기(``merge_workspace``): 모든 ID를 새로 만들고, 겹치는 행(같은 ID·DB 유일 키·내용 키)은
  정책(``skip``/``fail``)대로 처리한다. 기존 행은 고치지 않는다 (INSERT만, 앞뒤 논리 해시로 확인).
- 옛 형식 묶음: ``UPGRADES``(형식 버전 → 다음 버전 단계)를 차례로 적용한 뒤 같은 검사를 한다.
- 옛 migration head 묶음: ``SCHEMA_STEPS``(revision마다 표 변환 단계 또는 바뀜 없음 표시)를 묶음 head 다음부터
  이 설치 head까지 차례로 적용한다 (``convert_schema``).
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import heapq
import io
import json
import os
import re
import tarfile
import tempfile
import zlib
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import IO, Any
from uuid import UUID, uuid4

from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from career_core import __version__
from career_core.db.models import Deletion, ExportRun
from career_core.db.rls import WORKSPACE_SETTING
from career_core.db.session import Database
from career_core.domain.runs import canonical_json
from career_core.domain.workspace import ScopePermissionPolicy
from career_core.ports import ActorContext, BlobStore, PermissionPolicy

FORMAT = "career-brain-workspace-export"
# 2 (c0018): manifest에 ``tombstone_tables``. 1 → 2 단계는 표 내용을 바꾸지 않는다 (``UPGRADES``).
FORMAT_VERSION = 2
SCHEMAS = ("core",)
MANIFEST = "manifest.json"

# 공간 자료지만 묶음에 넣지 않는 표. 새로 빼는 표는 이유를 같이 적는다.
EXCLUDED_TABLES: dict[str, str] = {
    "core.jobs": "작업 큐(일시 상태, 대상 ID뿐). 복원 후 필요한 작업은 다시 만든다",
    "core.embeddings": "검색 벡터. 모델·차원에 따라 다르고 원문에서 다시 만든다 (복원 후 re-index)",
    "core.oauth_identities": "외부 로그인 연결: 설치마다 다시 연결한다 (personal-api oauth-link)",
    "core.oauth_tokens": "본 외부 토큰·폐기 목록: 수명이 짧은 설치별 기록이고 연결(oauth_identities)을 따른다",
    "core.maintenance_results": "이 설치의 주기 유지보수 기록 (c0020). 복원한 설치에서 다시 쌓인다",
    "core.restore_jobs": "웹 복원 작업 상태 (c0024). 이 설치의 임시 파일을 가리키는 일시 상태",
    "core.judge_cache": "판정 모델 응답 캐시 (c0022): 다시 물으면 생기는 파생 자료이고, "
    "지운 문서의 인용을 백업으로 옮기지 않는다",
    "core.model_connection_secrets": "모델 연결 키의 암호문 (c0030): 묶음에 키를 넣지 않는다. "
    "복원한 연결은 끝 4자리만 남아 '다시 넣어야 함'(needs_reentry)으로 보인다",
}
# 복원 후 다시 큐에 넣는 작업 종류 → 대상 표. 내보낼 때 대기·실행 중이던 것만 manifest에 적는다.
REQUEUE_KINDS: dict[str, str] = {
    "parse": "core.parse_runs",
    "extract": "core.extraction_runs",
    "job_fetch": "core.job_fetch_runs",
    "company_fetch": "core.company_pages",  # c0033 회사 페이지 가져오기
}
# 참고 칸의 표 (manifest ``reference_tables``, 선택 키). 참고 원본은 ``core.sources``의 ``purpose = 'reference'``
REFERENCE_TABLES = frozenset(
    {
        "core.companies",
        "core.company_pages",
        "core.company_facts",
        "core.reference_items",
        "core.reference_notes",
    }
)
# 삭제 기록(core.deletions, record_deletion trigger)을 남기는 표. c0014: 앞의 4개, c0018: 나머지.
# migration의 trigger·CHECK와 같아야 한다 (test_export.py가 DB와 비교한다).
_V1_TOMBSTONE_TABLES = frozenset(
    {"core.sources", "core.job_postings", "core.applications", "core.study_items"}
)
TOMBSTONE_TABLES = _V1_TOMBSTONE_TABLES | frozenset(
    {
        "core.experiences",
        "core.claims",
        "core.claim_evidence",
        "core.interview_sessions",
        "core.interview_questions",
        "core.application_documents",
        "core.application_stages",
        "core.study_requirements",
        "core.study_requirement_claims",
        "core.study_assessments",
        "core.study_sessions",
        "core.study_mistakes",
    }
)
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
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# 진행 알림: (단계, 한 것, 전체). 단계는 read·decrypt·match·insert·verify·deletions·blobs·commit.
# 묶음 읽기는 스레드에서 부르므로 받는 쪽은 값만 바꾸는 가벼운 함수여야 한다.
Progress = Callable[[str, int, int], None]


def _no_progress(phase: str, done: int, total: int) -> None:
    return None


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
    # UNIQUE 제약 (이름, 칸). 합치기의 겹침 찾기에 쓴다.
    unique_keys: tuple[tuple[str, tuple[str, ...]], ...] = ()

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
WHERE con.contype IN ('p', 'f', 'u') AND n.nspname = ANY(:schemas)
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
    uniques: dict[int, list[tuple[str, tuple[str, ...]]]] = {}
    names = {t.oid: t.name for t in tables}
    for r in (await conn.execute(text(_CATALOG_CONSTRAINTS), params)).all():
        if r.kind == "p":
            pks[r.oid] = tuple(r.cols)
        elif r.kind == "u":
            uniques.setdefault(r.oid, []).append((r.name, tuple(r.cols)))
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
                unique_keys=tuple(uniques.get(t.oid, ())),
            )
        )
    return sorted(out, key=lambda t: t.name)


_CATALOG_DEFAULTS = """
SELECT n.nspname || '.' || c.relname AS table_name, a.attname AS column_name,
       pg_get_expr(d.adbin, d.adrelid) AS expr
FROM pg_attrdef d
JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
JOIN pg_class c ON c.oid = d.adrelid JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p') AND n.nspname = ANY(:schemas) AND NOT a.attisdropped
  AND a.attgenerated = ''
ORDER BY 1, 2
"""
# 상수 기본값: 글자·수·참거짓·NULL 하나에 형 변환만 (``'{}'::jsonb``, ``(0)::numeric``, ``'a'::text``).
# 함수 호출(``now()``·``gen_random_uuid()``)·``CURRENT_TIMESTAMP`` 같은 값은 행마다 달라 쓰지 않는다.
_CONSTANT_DEFAULT = re.compile(
    r"\(*(?:'(?:[^']|'')*'|-?\d+(?:\.\d+)?|true|false|NULL)\)*"
    r"""(?:::[a-z_][a-z0-9_ ."]*(?:\(\d+(?:,\s*\d+)?\))?(?:\[\])*)*"""
)


@dataclass(frozen=True, slots=True)
class ColumnDefault:
    """칸의 서버 기본값. ``constant``이면 ``value``가 그 값의 ``to_jsonb`` 모양 (변환 단계가 채운다)."""

    expr: str
    constant: bool
    value: Any = None


async def read_column_defaults(
    conn: AsyncConnection, schemas: Sequence[str] = SCHEMAS
) -> dict[str, dict[str, ColumnDefault]]:
    """표 → 칸 → 기본값 (pg_catalog). 상수 기본값은 DB가 ``to_jsonb``로 계산한 값을 같이 둔다.

    변환 단계가 칸을 더할 때 ``to_jsonb`` 모양을 손으로 적지 않게 한다 (``_Archive.add_columns``).
    DEFAULT가 없는 칸은 목록에 없다 (= NULL).
    """
    rows = (await conn.execute(text(_CATALOG_DEFAULTS), {"schemas": list(schemas)})).all()
    constant = [r for r in rows if _CONSTANT_DEFAULT.fullmatch(r.expr.strip())]
    values: dict[tuple[str, str], Any] = {}
    for i in range(0, len(constant), 200):
        chunk = constant[i : i + 200]
        # 식은 카탈로그가 돌려준 기본값 그대로다 (위 정규식으로 상수 모양만 골랐다).
        # 매개변수 없이 드라이버에 그대로 보낸다 (글자 값 안의 ``:이름``을 bind로 읽지 않게).
        cols = ", ".join(f"to_jsonb({r.expr})::text AS d{j}" for j, r in enumerate(chunk))
        got = (await conn.exec_driver_sql(f"SELECT {cols}")).one()
        for j, r in enumerate(chunk):
            values[(r.table_name, r.column_name)] = json.loads(got[j], parse_float=Decimal)
    out: dict[str, dict[str, ColumnDefault]] = {}
    for r in rows:
        key = (r.table_name, r.column_name)
        out.setdefault(r.table_name, {})[r.column_name] = ColumnDefault(
            r.expr, key in values, values.get(key)
        )
    return out


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
    pending_work: int = 0
    encrypted: bool = False

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
    recipients: Sequence[str] | None = None,
) -> ExportReport:
    """actor의 공간 하나를 ``out``(tar.gz)으로 쓴다. 소유자(workspace-admin)만.

    ``recipients``(age 공개 키)가 있으면 묶음 전체를 age로 암호화한다 (``out``은 암호문만 남는다).
    표는 줄 단위로 임시 파일에 흘려 쓰고, 묶음도 파일에서 조각씩 읽어 넣는다 (표·묶음 전체를
    메모리에 올리지 않는다). 원본 파일은 한 번에 하나씩 읽는다.
    """
    if not (permissions or ScopePermissionPolicy()).allows(actor, "workspace.export"):
        raise ExportError("forbidden")
    if recipients:
        from career_core.adapters.age_crypt import AgeError, check_recipients

        try:
            recipients = check_recipients(recipients)
        except AgeError as exc:
            raise ExportError(exc.code, exc.detail) from exc
    ws = actor.workspace_id
    out = Path(out)
    with tempfile.TemporaryDirectory(prefix="career-export-") as tmp:
        staging = Path(tmp)
        dump = await _dump_tables(db, ws, staging)
        blob_entries, missing = await _check_blobs(blobs, ws, dump.blob_keys)
        proofs = await _proof_marks(db, ws)
        for entry in blob_entries:
            if entry["key"] in proofs["blobs"]:
                entry["purpose"] = "proof"
        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "core_version": __version__,
            "migration_heads": core_migration_heads(),
            "created_at": datetime.now(UTC).isoformat(),
            "workspace": {"id": str(ws), "name": dump.workspace_name},
            "tables": [
                {
                    "name": t.name,
                    "file": _table_file(t.name),
                    "rows": dump.tables[t.name].rows,
                    "sha256": dump.tables[t.name].sha256,
                    "columns": _columns_signature(t),
                }
                for t in dump.plan.tables
            ],
            "blobs": blob_entries,
            "missing_blobs": missing,
            # 내보낼 때 대기·실행 중이던 작업. 복원하면 대상이 있는 것만 다시 큐에 넣는다.
            "pending_work": dump.pending_work,
            "excluded": {**dump.plan.excluded, **OUT_OF_SCOPE},
            "skipped": dump.plan.skipped,
            # 이 묶음의 core.deletions가 다루는 표 (v2). 삭제 기록을 다시 적용할 때 범위 안내.
            "tombstone_tables": sorted(TOMBSTONE_TABLES),
            # c0034: 증빙 원본(자료 등급 restricted). 원본 파일은 blobs에 그대로, 여기서 따로 표시한다
            "proofs": proofs,
            # 참고 칸(다른 사람의 글·회사 공개 정보)의 표. 내 경험이 아니다 (c0035). 원본은 sources.purpose
            "reference_tables": sorted(REFERENCE_TABLES),
        }
        partial = out.with_name(f".{out.name}.{uuid4().hex}.part")
        sealed = out.with_name(f".{out.name}.{uuid4().hex}.age")
        try:
            # 압축·파일 쓰기는 스레드에서 (HTTP 내려받기 중에도 이벤트 루프를 막지 않게).
            tar = await asyncio.to_thread(tarfile.open, partial, "w:gz")
            with tar:
                data = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
                await asyncio.to_thread(_add_bytes, tar, MANIFEST, data)
                for t in dump.plan.tables:
                    path = staging / _table_file(t.name)
                    await asyncio.to_thread(_add_file, tar, _table_file(t.name), path)
                for entry in blob_entries:
                    data = await blobs.get(ws, entry["key"])
                    if hashlib.sha256(data).hexdigest() != entry["key"]:
                        raise ExportError("blob_changed", entry["key"])
                    await asyncio.to_thread(_add_bytes, tar, f"blobs/{entry['key']}", data)
                    del data
            if recipients:
                from career_core.adapters.age_crypt import AgeError, encrypt_file

                try:
                    await asyncio.to_thread(encrypt_file, partial, sealed, recipients)
                except AgeError as exc:
                    raise ExportError(exc.code, exc.detail) from exc
                partial.unlink()
                os.replace(sealed, out)
            else:
                os.replace(partial, out)
        finally:
            partial.unlink(missing_ok=True)
            sealed.unlink(missing_ok=True)
    digest, size = await asyncio.to_thread(_file_digest, out)
    report = ExportReport(
        path=out,
        workspace_id=ws,
        sha256=digest,
        bytes=size,
        tables=[dump.tables[t.name] for t in dump.plan.tables],
        blobs=len(blob_entries),
        missing_blobs=missing,
        manifest=manifest,
        pending_work=len(dump.pending_work),
        encrypted=bool(recipients),
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


@dataclass(slots=True)
class _Dump:
    tables: dict[str, TableReport]
    blob_keys: dict[str, set[str]]
    workspace_name: str
    plan: ExportPlan
    pending_work: list[dict]


# 표 파일을 이만큼 모아서 한 번에 쓴다 (스레드 왕복을 줄이되 메모리는 일정하게)
_WRITE_CHUNK = 1 << 20


async def _dump_tables(db: Database, ws: UUID, staging: Path) -> _Dump:
    """한 스냅샷(REPEATABLE READ, 읽기 전용) 안에서 모든 표를 줄 파일로 흘려 쓴다."""
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
                buf = bytearray()
                f = await asyncio.to_thread(open, staging / _table_file(t.name), "wb")
                try:
                    result = await conn.stream(text(_rows_sql(t)), {"ws": str(ws)})
                    async for row in result:
                        line = row.j.encode() + b"\n"
                        h.update(line)
                        buf += line
                        n += 1
                        if len(buf) >= _WRITE_CHUNK:
                            await asyncio.to_thread(f.write, bytes(buf))
                            buf.clear()
                    if buf:
                        await asyncio.to_thread(f.write, bytes(buf))
                finally:
                    await asyncio.to_thread(f.close)
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
            pending = (
                await conn.execute(
                    text(
                        "SELECT kind, target_id::text AS target_id, status FROM core.jobs "
                        "WHERE workspace_id = CAST(:ws AS uuid) "
                        "AND status IN ('queued', 'running') AND kind = ANY(:kinds) "
                        "ORDER BY created_at, id"
                    ),
                    {"ws": str(ws), "kinds": sorted(REQUEUE_KINDS)},
                )
            ).all()
    work = [
        {
            "kind": r.kind,
            "target_table": REQUEUE_KINDS[r.kind],
            "target_id": r.target_id,
            "status": r.status,
        }
        for r in pending
    ]
    return _Dump(reports, blob_keys, name, plan, work)


async def _proof_marks(db: Database, ws: UUID) -> dict[str, list[str]]:
    """증빙 원본 ID와 그 파일 키 (manifest ``proofs``). 묶음을 받은 사람이 증빙 사본을 따로 다루게."""
    async with db.workspace_session(ws) as s:
        # 옛 migration head(c0032 전)의 DB에는 용도 칸이 없다: 증빙도 없다
        has_purpose = await s.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'core' "
                "AND table_name = 'sources' AND column_name = 'purpose'"
            )
        )
        if not has_purpose:
            return {"sources": [], "blobs": []}
        rows = (
            await s.execute(
                text(
                    "SELECT s.id::text AS id, v.blob_key FROM core.sources s "
                    "JOIN core.source_versions v ON v.source_id = s.id "
                    "WHERE s.purpose = 'proof' ORDER BY s.id, v.blob_key"
                )
            )
        ).all()
    return {
        "sources": sorted({r.id for r in rows}),
        "blobs": sorted({r.blob_key for r in rows}),
    }


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
        del data
    return entries, missing


def _table_file(name: str) -> str:
    return f"tables/{name}.jsonl"


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(datetime.now(UTC).timestamp())
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(data))


def _add_file(tar: tarfile.TarFile, name: str, path: Path) -> None:
    """디스크의 파일을 조각씩 읽어 넣는다 (tarfile이 복사 버퍼로 흘려 보낸다)."""
    info = tarfile.TarInfo(name)
    info.size = path.stat().st_size
    info.mtime = int(datetime.now(UTC).timestamp())
    info.mode = 0o600
    with open(path, "rb") as f:
        tar.addfile(info, f)


def _file_digest(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


# ---------------------------------------------------------------- 삭제 기록 내보내기


DELETIONS_FORMAT = "career-brain-deletions"


async def export_deletions(
    db: Database,
    actor: ActorContext,
    out: str | os.PathLike[str],
    *,
    permissions: PermissionPolicy | None = None,
) -> int:
    """삭제 기록만 작은 JSONL로 (첫 줄 머리말). 자주 받아 두면 오래된 묶음을 복원한 뒤 다시 적용한다."""
    if not (permissions or ScopePermissionPolicy()).allows(actor, "workspace.export"):
        raise ExportError("forbidden")
    out = Path(out)
    partial = out.with_name(f".{out.name}.{uuid4().hex}.part")
    n = 0
    try:
        async with db.workspace_session(actor.workspace_id) as s:
            result = await s.stream(
                select(Deletion.target_table, Deletion.target_id, Deletion.deleted_at).order_by(
                    Deletion.deleted_at, Deletion.id
                )
            )
            f = await asyncio.to_thread(open, partial, "w", encoding="utf-8")
            try:
                head = {
                    "format": DELETIONS_FORMAT,
                    "format_version": 1,
                    "workspace_id": str(actor.workspace_id),
                    "created_at": datetime.now(UTC).isoformat(),
                }
                await asyncio.to_thread(f.write, json.dumps(head) + "\n")
                async for table, target, at in result:
                    row = {"target_table": table, "target_id": str(target), "deleted_at": at}
                    await asyncio.to_thread(f.write, json.dumps(row, default=str) + "\n")
                    n += 1
            finally:
                await asyncio.to_thread(f.close)
        os.replace(partial, out)
    finally:
        partial.unlink(missing_ok=True)
    return n


@dataclass(frozen=True, slots=True)
class Tombstone:
    target_table: str
    target_id: str
    deleted_at: str


def read_deletions(path: str | os.PathLike[str]) -> tuple[UUID, list[Tombstone]]:
    """삭제 기록 파일 또는 내보내기 묶음의 ``core.deletions`` 표에서 (공간 ID, 기록)."""
    out: list[Tombstone] = []
    with open(path, encoding="utf-8") as f:
        try:
            head = json.loads(f.readline())
            ws = UUID(head["workspace_id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ArchiveError("invalid_deletions", "bad header") from exc
        if head.get("format") != DELETIONS_FORMAT or head.get("format_version") != 1:
            raise ArchiveError("invalid_deletions", "not a career-brain deletion log")
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                table, target = row["target_table"], str(UUID(row["target_id"]))
                at = str(row["deleted_at"])
            except (ValueError, KeyError, TypeError) as exc:
                raise ArchiveError("invalid_deletions", "bad row") from exc
            if table not in TOMBSTONE_TABLES:
                raise ArchiveError("invalid_deletions", f"unknown table {table[:40]}")
            out.append(Tombstone(table, target, at))
    return ws, out


# ---------------------------------------------------------------- 묶음 읽기


@dataclass(slots=True)
class _Archive:
    manifest: dict
    table_dir: Path  # 표 → staging/tables/<name>.jsonl (원문 그대로)
    blob_dir: Path
    blob_keys: list[str]
    sha256: str
    bytes: int
    # 옛 형식에서 바꿔 읽었으면 원래 형식 버전
    upgraded_from: int | None = None
    # 옛 migration head에서 바꿔 읽었으면 원래 head (``convert_schema``)
    schema_from: list[str] | None = None
    # 변환 단계가 만들거나 고친 표. 칸 이름은 행마다 대상 DB와 비교한다 (``_check_schema``).
    touched: set[str] = field(default_factory=set)
    # 대상 DB의 칸 기본값 (``read_column_defaults``). 변환 전에 읽어 둔다 (단계는 DB 없이 돈다).
    defaults: dict[str, dict[str, ColumnDefault]] | None = None
    # 단계가 서버 기본값으로 채운 (표, 칸) (시험이 그 revision의 기본값과 같은지 본다)
    defaults_used: set[tuple[str, str]] = field(default_factory=set)

    def path(self, name: str) -> Path:
        return self.table_dir / f"{name}.jsonl"

    def has(self, name: str) -> bool:
        return any(e["name"] == name for e in self.manifest["tables"])

    def iter_lines(self, name: str) -> Iterator[str]:
        with open(self.path(name), encoding="utf-8") as f:
            for line in f:
                yield line.rstrip("\n")

    def iter_rows(self, name: str) -> Iterator[dict]:
        """변환 단계용: 행을 dict로 (소수는 ``Decimal`` 그대로)."""
        for line in self.iter_lines(name):
            yield json.loads(line, parse_float=Decimal)

    def read_lines(self, name: str) -> list[str]:
        return list(self.iter_lines(name))

    def rewrite(self, name: str, change: Callable[[dict], dict | None]) -> None:
        """변환 단계용: 표 파일의 행을 하나씩 바꿔 다시 쓴다 (``None``이면 그 행을 뺀다).

        해시·행 수는 모든 단계가 끝난 뒤 다시 계산한다 (``upgrade_archive``·``convert_schema``).
        """
        src = self.path(name)
        tmp = src.with_name(f".{src.name}.{uuid4().hex}")
        with open(src, encoding="utf-8") as f, open(tmp, "w", encoding="utf-8") as out:
            for line in f:
                row = change(json.loads(line, parse_float=Decimal))
                if row is not None:
                    out.write(_json_text(row) + "\n")
        os.replace(tmp, src)
        self.touched.add(name)

    def _entry(self, name: str) -> dict:
        for e in self.manifest["tables"]:
            if e["name"] == name:
                return e
        raise ArchiveError("upgrade_failed", f"no table {name} in the archive")

    def rename_table(self, old: str, new: str) -> None:
        """변환 단계용: 표 이름 바꾸기 (파일·manifest). 행은 그대로 (칸은 ``rewrite``로)."""
        if self.has(new) or not _TABLE_FILE.fullmatch(_table_file(new)):
            raise ArchiveError("upgrade_failed", f"cannot rename {old} to {new}")
        entry = self._entry(old)
        os.replace(self.path(old), self.path(new))
        entry.update(name=new, file=_table_file(new))
        self.touched.discard(old)
        self.touched.add(new)

    def add_table(self, name: str, rows: Iterable[dict] = ()) -> None:
        """변환 단계용: 새 표 (옛 묶음에 없던 표는 보통 빈 표). 표를 나눌 때는 행을 같이 준다."""
        if self.has(name) or not _TABLE_FILE.fullmatch(_table_file(name)):
            raise ArchiveError("upgrade_failed", f"cannot add table {name}")
        with open(self.path(name), "w", encoding="utf-8") as out:
            out.writelines(_json_text(row) + "\n" for row in rows)
        self.manifest["tables"].append(
            {"name": name, "file": _table_file(name), "rows": 0, "sha256": "", "columns": []}
        )
        self.touched.add(name)

    def drop_table(self, name: str) -> None:
        """변환 단계용: 표 지우기 (지금 설치에 없는 표, 또는 다른 표로 합친 뒤)."""
        entry = self._entry(name)
        self.manifest["tables"].remove(entry)
        self.path(name).unlink()
        self.touched.discard(name)

    def column_default(self, table: str, column: str) -> Any:
        """그 칸의 서버 기본값 (``to_jsonb`` 모양). DEFAULT가 없는 칸은 NULL(None).

        상수 기본값(``false``·``0``·``'{}'::jsonb``·``'pending'::text`` 등)만 쓴다. ``now()``처럼 행마다
        다른 기본값은 ``upgrade_failed`` — 단계가 값을 직접 정해야 한다 (``add_columns(..., 칸=값)``).
        대상 DB(이 설치의 head)에서 읽은 값이다: 나중 migration이 기본값을 바꿨으면 그 단계는 옛 값을
        직접 적는다 (``test_schema_steps_cover_every_migration``이 revision 시점 값과 비교한다).
        """
        if self.defaults is None:
            raise ArchiveError("upgrade_failed", "column defaults were not read for conversion")
        d = self.defaults.get(table, {}).get(column)
        self.defaults_used.add((table, column))
        if d is None:
            return None
        if not d.constant:
            raise ArchiveError(
                "upgrade_failed", f"default of {table}.{column} is not a constant ({d.expr[:80]})"
            )
        return copy.deepcopy(d.value)

    def add_columns(self, table: str, *defaulted: str, **values: Any) -> None:
        """변환 단계용: 칸 더하기. 이름만 주면 서버 기본값(``column_default``), ``칸=값``이면 그 값.

        행마다 같은 값을 넣는다. 이미 있는 칸이면 ``upgrade_failed`` (단계를 두 번 적용하지 않게).
        """
        if not self.has(table):
            raise ArchiveError("upgrade_failed", f"no table {table} in the archive")
        add = {c: self.column_default(table, c) for c in defaulted} | values

        def change(row: dict) -> dict:
            if clash := add.keys() & row.keys():
                raise ArchiveError("upgrade_failed", f"{table} already has {sorted(clash)}")
            return {**row, **copy.deepcopy(add)}

        self.rewrite(table, change)

    def ensure_tables(self, *names: str) -> None:
        """변환 단계용: 옛 묶음에 없던 표를 빈 표로 (이미 있으면 그대로)."""
        for name in names:
            if not self.has(name):
                self.add_table(name)


def _json_text(value: Any) -> str:
    """JSON 직렬화. 소수는 ``Decimal`` 그대로 (numeric 칸의 자릿수를 잃지 않게)."""
    if isinstance(value, dict):
        return (
            "{"
            + ", ".join(
                f"{json.dumps(k, ensure_ascii=False)}: {_json_text(v)}" for k, v in value.items()
            )
            + "}"
        )
    if isinstance(value, list):
        return "[" + ", ".join(_json_text(v) for v in value) + "]"
    if isinstance(value, Decimal):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _copy_member(src: IO[bytes] | None, dst: Path) -> tuple[str, int, int]:
    """tar 항목을 조각씩 파일로 옮기며 (sha256, 바이트 수, 줄 수)."""
    h = hashlib.sha256()
    size = lines = 0
    with open(dst, "wb") as out:
        if src is not None:
            while chunk := src.read(1 << 20):
                h.update(chunk)
                out.write(chunk)
                size += len(chunk)
                lines += chunk.count(b"\n")
    return h.hexdigest(), size, lines


def _file_lines_digest(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    lines = 0
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
            lines += chunk.count(b"\n")
    return h.hexdigest(), lines


def read_archive(
    path: str | os.PathLike[str], staging: Path, progress: Progress | None = None
) -> _Archive:
    """묶음을 한 번 훑으며 형식·해시를 확인한다. 표·원본은 ``staging``에 조각씩 꺼낸다.

    정해진 이름의 일반 파일만 받는다 (링크·장치·경로 탈출·중복 이름 거부). manifest만 메모리에 둔다.
    옛 형식 묶음도 여기서는 그대로 꺼낸다 (해시는 받은 파일 기준). 변환은 ``upgrade_archive``.
    """
    tick = progress or _no_progress
    path = Path(path)
    digest, size = _file_digest(path)
    blob_dir = staging / "blobs"
    table_dir = staging / "tables"
    blob_dir.mkdir()
    table_dir.mkdir()
    manifest: dict | None = None
    tables: dict[str, tuple[str, int]] = {}  # 이름 → (sha256, 줄 수)
    seen: set[str] = set()
    tick("read", 0, size)
    try:
        with open(path, "rb") as raw, tarfile.open(fileobj=raw, mode="r|gz") as tar:
            for member in tar:
                name = member.name
                if name in seen or not member.isreg():
                    raise ArchiveError("unexpected_member", name)
                seen.add(name)
                f = tar.extractfile(member)
                if manifest is None:
                    if name != MANIFEST:
                        raise ArchiveError("missing_manifest", "manifest.json must come first")
                    if member.size > MAX_MANIFEST_BYTES:
                        raise ArchiveError("invalid_manifest", "too large")
                    manifest = _parse_manifest(f.read() if f else b"")
                    continue
                if m := _TABLE_FILE.fullmatch(name):
                    h, _, n = _copy_member(f, table_dir / f"{m.group(1)}.jsonl")
                    tables[m.group(1)] = (h, n)
                elif m := _BLOB_FILE.fullmatch(name):
                    key = m.group(1)
                    h, _, _ = _copy_member(f, blob_dir / key)
                    if h != key:
                        raise ArchiveError("hash_mismatch", name)
                else:
                    raise ArchiveError("unexpected_member", name)
                tick("read", min(raw.tell(), size), size)
    except (tarfile.TarError, EOFError, OSError, zlib.error) as exc:
        raise ArchiveError("not_an_archive", type(exc).__name__) from exc
    if manifest is None:
        raise ArchiveError("missing_manifest")

    listed = {t["name"] for t in manifest["tables"]}
    if extra := set(tables) - listed:
        raise ArchiveError("unexpected_member", ", ".join(sorted(extra)))
    for entry in manifest["tables"]:
        name = entry["name"]
        if name not in tables:
            raise ArchiveError("missing_member", _table_file(name))
        h, n = tables[name]
        if h != entry["sha256"]:
            raise ArchiveError("hash_mismatch", _table_file(name))
        if n != entry["rows"]:
            raise ArchiveError("count_mismatch", name)
    keys = [b["key"] for b in manifest["blobs"]]
    present = {p.name for p in blob_dir.iterdir()}
    if missing := set(keys) - present:
        raise ArchiveError("missing_member", f"{len(missing)} blob(s)")
    if extra := present - set(keys):
        raise ArchiveError("unexpected_member", f"{len(extra)} unlisted blob(s)")
    tick("read", size, size)
    return _Archive(manifest, table_dir, blob_dir, keys, digest, size)


MAX_MANIFEST_BYTES = 64 << 20


def _parse_manifest(data: bytes) -> dict:
    """묶음 틀(모든 형식 버전에 공통)만 본다: 형식 이름, 읽을 수 있는 버전, 표 이름·행 수·해시, 원본 키.

    나머지 칸은 형식 변환 뒤 ``_check_manifest``가 본다.
    """
    try:
        m = json.loads(data)
    except ValueError as exc:
        raise ArchiveError("invalid_manifest") from exc
    if not isinstance(m, dict) or m.get("format") != FORMAT:
        raise ArchiveError("format_unsupported", "not a career-brain workspace export")
    version = m.get("format_version")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version not in readable_versions()
    ):
        raise ArchiveError(
            "version_mismatch",
            f"format {version}, this install reads {', '.join(map(str, readable_versions()))}",
        )
    try:
        ok = isinstance(m["tables"], list) and isinstance(m["blobs"], list)
        for t in m["tables"]:
            ok = ok and (
                bool(_TABLE_FILE.fullmatch(_table_file(t["name"])))
                and isinstance(t["rows"], int)
                and bool(_BLOB_KEY.fullmatch(t["sha256"]))
            )
        for b in m["blobs"]:
            ok = ok and bool(_BLOB_KEY.fullmatch(b["key"]))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ArchiveError("invalid_manifest", type(exc).__name__) from exc
    if not ok:
        raise ArchiveError("invalid_manifest")
    return m


def _check_manifest(m: dict) -> None:
    """현재 형식(``FORMAT_VERSION``)의 나머지 칸."""
    try:
        UUID(m["workspace"]["id"])
        ok = isinstance(m["migration_heads"], list) and isinstance(m["workspace"]["name"], str)
        for t in m["tables"]:
            ok = ok and isinstance(t["columns"], list)
        for w in m.get("pending_work", []):
            ok = ok and isinstance(w.get("kind"), str)
            UUID(w["target_id"])
        ok = ok and isinstance(m.get("tombstone_tables", []), list)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ArchiveError("invalid_manifest", type(exc).__name__) from exc
    if not ok:
        raise ArchiveError("invalid_manifest")


# ---------------------------------------------------------------- 옛 형식 변환

# 한 단계: (manifest, 꺼낸 묶음)을 받아 바로 다음 버전으로 바꾼다. manifest는 dict 그대로 고치고,
# 표 내용은 ``_Archive.rewrite``로 바꾼다. 묶음 틀(맨 앞 manifest, tables/*·blobs/* 이름, 표마다
# rows·sha256, 원본 key)은 모든 버전에 같다. 해시·행 수는 단계가 끝난 뒤 다시 계산한다.
FormatStep = Callable[[dict, _Archive], None]


@dataclass(frozen=True, slots=True)
class FormatUpgrade:
    from_version: int
    summary: str
    apply: FormatStep


# from_version → 그 버전을 다음 버전으로 바꾸는 단계
UPGRADES: dict[int, FormatUpgrade] = {}


def format_upgrade(from_version: int, summary: str) -> Callable[[FormatStep], FormatStep]:
    def register(fn: FormatStep) -> FormatStep:
        if from_version in UPGRADES or from_version >= FORMAT_VERSION:
            raise ValueError(f"bad format step {from_version}")
        UPGRADES[from_version] = FormatUpgrade(from_version, summary, fn)
        return fn

    return register


def readable_versions() -> list[int]:
    """이 코드가 읽을 수 있는 형식 버전: 현재 버전과, 현재 버전까지 단계가 끊김 없이 이어지는 옛 버전."""
    out = [FORMAT_VERSION]
    v = FORMAT_VERSION - 1
    while v in UPGRADES:
        out.append(v)
        v -= 1
    return sorted(out)


@format_upgrade(1, "v1 → v2: 표 내용 그대로, manifest에 tombstone_tables(c0014의 4개 표)")
def _upgrade_v1(manifest: dict, arc: _Archive) -> None:
    manifest.setdefault("tombstone_tables", sorted(_V1_TOMBSTONE_TABLES))


def upgrade_archive(arc: _Archive) -> int | None:
    """옛 형식이면 단계를 차례로 적용하고 표 해시·행 수를 다시 계산한다. 원래 버전(바꾸지 않았으면 None)."""
    m = arc.manifest
    start = m["format_version"]
    if start == FORMAT_VERSION:
        return None
    for v in range(start, FORMAT_VERSION):
        UPGRADES[v].apply(m, arc)
        m["format_version"] = v + 1
    _restat(arc)
    m["upgraded_from"] = start
    arc.upgraded_from = start
    return start


def _restat(arc: _Archive) -> None:
    """변환 뒤 표 파일마다 sha256·행 수를 다시 잰다. manifest와 파일 목록이 어긋나면 ``upgrade_failed``."""
    listed = set()
    for entry in arc.manifest["tables"]:
        name = entry["name"]
        if not _TABLE_FILE.fullmatch(_table_file(name)) or not arc.path(name).is_file():
            raise ArchiveError("upgrade_failed", f"table file missing after upgrade: {name}")
        entry["sha256"], entry["rows"] = _file_lines_digest(arc.path(name))
        listed.add(name)
    if extra := {p.name.removesuffix(".jsonl") for p in arc.table_dir.iterdir()} - listed:
        raise ArchiveError("upgrade_failed", f"unlisted table files: {', '.join(sorted(extra))}")


# ---------------------------------------------------------------- 옛 migration head 변환
#
# migration이 내보내는 표의 모양(표·칸 이름·타입)을 바꾸면 그 revision에 단계를 하나 등록한다.
# 옛 head에서 만든 묶음은 그 head 다음 revision부터 이 설치의 head까지 차례로 단계를 적용해 읽는다.
#
# - ``@schema_step("c00NN", "요약")``: 행을 바꾸는 함수 ``(arc) -> None``. 도구는 ``arc.rename_table``,
#   ``arc.add_table``(빈 표·나눈 표), ``arc.drop_table``, ``arc.rewrite``(행마다 dict → dict/None),
#   ``arc.iter_rows``, ``arc.add_columns(표, "칸", 칸=값)``(이름만 주면 대상 DB 카탈로그의 서버 기본값,
#   ``arc.column_default``), ``arc.ensure_tables``(없던 표를 빈 표로).
#   값은 PostgreSQL ``to_jsonb`` 출력 모양으로 쓴다 (복원 확인이 그 모양으로 비교한다).
#   칸 값이 null인 행에서도 깨지지 않아야 한다 (``test_schema_steps_cover_every_migration``이
#   모든 칸이 null인 합성 행으로 돌려 본다).
# - ``no_data_change("c00NN", "이유")``: 내보내는 표의 모양이 그대로인 revision (함수·trigger·색인,
#   내보내지 않는 표). 시험이 migration 앞뒤 카탈로그를 비교해 정말 그대로인지 확인한다.
# - 등록이 없는 revision을 지나야 하는 묶음은 ``version_mismatch``. ``SCHEMA_FLOOR``보다 옛 head는 받지 않는다.

SchemaStepFn = Callable[[_Archive], None]


@dataclass(frozen=True, slots=True)
class SchemaStep:
    revision: str
    summary: str
    apply: SchemaStepFn | None  # None = 내보내는 표 모양이 바뀌지 않는다 (no data change)


# migration revision → 그 revision으로 올리는 단계 (또는 바뀜 없음 표시)
SCHEMA_STEPS: dict[str, SchemaStep] = {}
# 이 head(포함)부터 만든 묶음을 바꿔 읽는다. 이 다음 revision마다 SCHEMA_STEPS에 하나씩 있어야 한다.
# c0011 = 내보내기가 처음 생긴 head (O11). 그보다 옛 head의 묶음은 있을 수 없다.
SCHEMA_FLOOR = "c0011"


def schema_step(revision: str, summary: str) -> Callable[[SchemaStepFn], SchemaStepFn]:
    def register(fn: SchemaStepFn) -> SchemaStepFn:
        _register_step(SchemaStep(revision, summary, fn))
        return fn

    return register


def no_data_change(revision: str, summary: str) -> None:
    _register_step(SchemaStep(revision, summary, None))


def _register_step(step: SchemaStep) -> None:
    if step.revision in SCHEMA_STEPS:
        raise ValueError(f"schema step {step.revision} registered twice")
    SCHEMA_STEPS[step.revision] = step


@schema_step(
    "c0012",
    "공용 문서 복사 출처(source_document_id·source_revision_id = null), 수정안 멱등 키(null), "
    "새 표 application_stages·document_settings (옛 묶음에는 빈 표)",
)
def _schema_c0012(arc: _Archive) -> None:
    arc.add_columns("core.application_documents", "source_document_id", "source_revision_id")
    arc.add_columns("core.document_suggestions", "idempotency_key")
    arc.ensure_tables("core.application_stages", "core.document_settings")


@schema_step(
    "c0013",
    "학습 항목 합치기 표 study_item_merges, 학습 설정 study_settings (빈 표). "
    "study_items.status에 merged를 더한 것은 넓히기라 행은 그대로",
)
def _schema_c0013(arc: _Archive) -> None:
    arc.ensure_tables("core.study_item_merges", "core.study_settings")


@schema_step(
    "c0014",
    "감사 기록 audit_events·삭제 기록 deletions (빈 표). oauth_identities는 내보내지 않음, "
    "승인 무효화 trigger는 행을 바꾸지 않음",
)
def _schema_c0014(arc: _Archive) -> None:
    arc.ensure_tables("core.audit_events", "core.deletions")


no_data_change("c0015", "작업 큐 NOTIFY trigger·유지보수 공간 목록 함수 (작업 큐는 내보내지 않음)")


@schema_step(
    "c0016",
    "모델 장부 run_id·run_step(null), runs.cost_from_ledger(서버 기본값 false: 그 전 실행은 예전 방식), "
    "interview_sessions.idempotency_key·interview_questions.request_id(null), "
    "새 표 document_suggestion_requests·interview_question_requests (빈 표)",
)
def _schema_c0016(arc: _Archive) -> None:
    arc.add_columns("core.model_calls", "run_id", "run_step")
    arc.add_columns("core.runs", "cost_from_ledger")
    arc.add_columns("core.interview_sessions", "idempotency_key")
    arc.add_columns("core.interview_questions", "request_id")
    arc.ensure_tables("core.document_suggestion_requests", "core.interview_question_requests")


no_data_change(
    "c0018", "삭제 기록 trigger를 12개 표로 넓힘, export_runs.kind에 merge (넓히기만, 행은 그대로)"
)
no_data_change(
    "c0019", "승인 대상 버전 함수·trigger, oauth_tokens(내보내지 않음), 감사 보존 함수, 알림"
)
no_data_change("c0020", "다음 작업 시각·유지보수 함수, maintenance_schedule·results(내보내지 않음)")


@schema_step(
    "c0021", "study_settings → workspace_settings (timezone 그대로, tech_aliases = 기본값 [])"
)
def _schema_c0021(arc: _Archive) -> None:
    if not arc.has("core.study_settings"):
        return
    arc.rename_table("core.study_settings", "core.workspace_settings")
    arc.add_columns("core.workspace_settings", "tech_aliases")


no_data_change("c0022", "판정 응답 캐시 judge_cache (내보내지 않음)")
no_data_change("c0023", "jobs.lease_token (작업 큐는 내보내지 않음)")
no_data_change("c0024", "웹 복원 작업 표 restore_jobs (내보내지 않음)")


@schema_step("c0025", "스캔 PDF 비전 비용 확인 표 job_fetch_confirmations 추가 (옛 묶음에는 빈 표)")
def _c0025_fetch_confirmations(arc) -> None:
    arc.ensure_tables("core.job_fetch_confirmations")


no_data_change("c0027", "restore_jobs.started_by·near_choices (웹 복원 작업 표는 내보내지 않음)")


no_data_change("c0026", "judge_cache 대상 ID·삭제 trigger (judge_cache는 내보내지 않음)")


@schema_step("c0029", "parse_runs.hidden_text 추가 (옛 묶음의 파싱은 숨김 글을 넣었다: 'keep')")
def _c0029_parse_hidden_text(arc) -> None:
    if arc.has("core.parse_runs"):
        arc.rewrite("core.parse_runs", lambda row: {**row, "hidden_text": "keep"})


@schema_step(
    "c0030",
    "화면 모델 연결 model_connections·라우트 덮기 model_route_overrides·model_settings (옛 묶음에는 빈 표). "
    "키 암호문 model_connection_secrets는 내보내지 않음",
)
def _c0030_model_connections(arc) -> None:
    arc.ensure_tables("core.model_connections", "core.model_route_overrides", "core.model_settings")


@schema_step(
    "c0031",
    "experiences에 kind('other')·parent_id(null)·started_on/ended_on·ongoing·tags([]). "
    "날짜는 migration과 같이 원문 기간이 분명할 때만 채운다 (모호하면 null)",
)
def _c0031_experience_structure(arc) -> None:
    if not arc.has("core.experiences"):
        return
    from career_core.domain.experiences import parse_period

    def change(row: dict) -> dict:
        period = parse_period(row.get("period_text"))
        return {
            **row,
            "kind": "other",
            "parent_id": None,
            "started_on": period.started_on.isoformat() if period else None,
            "ended_on": period.ended_on.isoformat() if period and period.ended_on else None,
            "ongoing": bool(period and period.ongoing),
            "tags": [],
        }

    arc.rewrite("core.experiences", change)


@schema_step(
    "c0032",
    "sources에 purpose('experience'): 옛 묶음의 원본은 모두 경험 추출 대상이었다. "
    "원본 파일(blob)·버전 해시는 그대로",
)
def _c0032_source_purpose(arc) -> None:
    if arc.has("core.sources"):
        arc.add_columns("core.sources", "purpose")


@schema_step(
    "c0033",
    "회사 조사 표 companies·company_pages·company_facts (옛 묶음에는 빈 표), "
    "job_postings.company_id(null: 옛 공고는 회사에 이어져 있지 않다)",
)
def _c0033_companies(arc) -> None:
    if arc.has("core.job_postings"):
        arc.add_columns("core.job_postings", company_id=None)
    arc.ensure_tables("core.companies", "core.company_pages", "core.company_facts")


@schema_step(
    "c0034",
    "자격·이수 기록 표 credentials (옛 묶음에는 빈 표). 증빙 원본(blob)·해시는 그대로",
)
def _c0034_credentials(arc) -> None:
    arc.ensure_tables("core.credentials")


@schema_step(
    "c0035",
    "참고 자료 보관함 표 reference_items·reference_notes (옛 묶음에는 빈 표)",
)
def _c0035_references(arc) -> None:
    arc.ensure_tables("core.reference_items", "core.reference_notes")


@schema_step(
    "c0036",
    "프로필·증명사진 표 profiles·profile_photos (옛 묶음에는 빈 표). "
    "사진 바이트는 blob_key로 다른 원본처럼 blobs/에 들어간다",
)
def _c0036_profiles(arc) -> None:
    arc.ensure_tables("core.profile_photos", "core.profiles")


@schema_step(
    "c0037",
    "지원 준비 문서 표 application_preps (옛 묶음에는 빈 표). 다시 만들 수 있는 산출물이고 "
    "사람이 고른 경험(choices)이 함께 들어간다",
)
def _c0037_application_prep(arc) -> None:
    arc.ensure_tables("core.application_preps")


@schema_step(
    "c0039",
    "브레인에 넣기: sources.purpose_pending(false: 옛 원본은 사람이 골라 올렸다)·purpose_hint(null), "
    "parse_runs.text_layer(null), document_blocks.bbox(null: 옛 블록에는 위치가 없다)",
)
def _c0039_ingest(arc) -> None:
    if arc.has("core.sources"):
        arc.add_columns("core.sources", "purpose_pending", purpose_hint=None)
    if arc.has("core.parse_runs"):
        arc.add_columns("core.parse_runs", text_layer=None)
    if arc.has("core.document_blocks"):
        arc.add_columns("core.document_blocks", bbox=None)


@schema_step(
    "c0040",
    "학력 표 education (옛 묶음에는 빈 표). 자격·이수의 옛 학력 기록(credentials kind=degree)은 그대로",
)
def _c0040_education(arc) -> None:
    arc.ensure_tables("core.education")


@schema_step(
    "c0041",
    "profiles에 병역·보훈·장애·희망 조건 칸 (옛 묶음에는 빈 값: 글·날짜·bool은 null, 목록은 [])",
)
def _c0041_profile_details(arc) -> None:
    if arc.has("core.profiles"):
        arc.add_columns(
            "core.profiles",
            "desired_roles",
            "desired_locations",
            "employment_types",
            military_status=None,
            military_branch=None,
            military_rank=None,
            military_started_on=None,
            military_ended_on=None,
            military_exemption_reason=None,
            veteran=None,
            disability=None,
            desired_salary=None,
            available_from=None,
        )


@schema_step(
    "c0042",
    "공통 답변 표 answer_assets·answer_asset_claims·answer_asset_uses (옛 묶음에는 빈 표)",
)
def _c0042_answer_assets(arc) -> None:
    arc.ensure_tables("core.answer_assets", "core.answer_asset_claims", "core.answer_asset_uses")


@schema_step(
    "c0043",
    "보고 있는 페이지 보내기: sources.origin_url(null: 옛 원본은 파일로 올렸다). "
    "공고 버전 method에 browser_capture가 더해짐 (옛 버전은 그대로)",
)
def _c0043_browser_capture(arc) -> None:
    if arc.has("core.sources"):
        arc.add_columns("core.sources", origin_url=None)


def _parents() -> dict[str, str | None]:
    from alembic.script import ScriptDirectory

    from career_core.db.migrate import CORE_CHAIN

    script = ScriptDirectory(str(CORE_CHAIN.script_location))
    out: dict[str, str | None] = {}
    for rev in script.walk_revisions():
        down = rev.down_revision
        # core chain은 한 줄이다. 갈래·합침은 변환하지 않는다
        out[rev.revision] = down if isinstance(down, str) or down is None else "<merge>"
    return out


def schema_path(from_heads: Sequence[str], to_heads: Sequence[str]) -> list[str]:
    """``from_heads`` 다음 revision부터 ``to_heads``까지 (오래된 것 먼저). 이을 수 없으면 ``version_mismatch``."""
    mismatch = ArchiveError(
        "version_mismatch", f"archive migrations {list(from_heads)}, this install {list(to_heads)}"
    )
    if len(from_heads) != 1 or len(to_heads) != 1:
        raise mismatch
    parents = _parents()
    path: list[str] = []
    cur: str | None = to_heads[0]
    while cur != from_heads[0]:
        if cur is None or cur not in parents:
            raise mismatch  # 묶음이 이 설치보다 새것이거나 모르는 revision
        path.append(cur)
        cur = parents[cur]
    return path[::-1]


def convert_schema(arc: _Archive, expected_heads: Sequence[str] | None = None) -> list[str] | None:
    """묶음의 migration head가 이 설치와 다르면 단계를 차례로 적용한다. 원래 head(같으면 None).

    단계가 없는 revision을 지나야 하면 ``version_mismatch``. 해시·행 수는 다시 재고, 고친 표의 칸 이름은
    복원·합치기가 대상 DB 카탈로그와 행마다 비교한다 (``_check_schema``).
    """
    m = arc.manifest
    heads = sorted(expected_heads) if expected_heads is not None else core_migration_heads()
    have = sorted(m["migration_heads"])
    if have == heads:
        return None
    path = schema_path(have, heads)
    if missing := [rev for rev in path if rev not in SCHEMA_STEPS]:
        raise ArchiveError(
            "version_mismatch",
            f"archive migrations {have}, this install {heads}: "
            f"no conversion step for {', '.join(missing)}",
        )
    for rev in path:
        step = SCHEMA_STEPS[rev]
        if step.apply is not None:
            step.apply(arc)
    _restat(arc)
    m["migration_heads"] = heads
    m["converted_from_heads"] = have
    arc.schema_from = have
    return have


async def _convert(db: Database, arc: _Archive, expected_heads: Sequence[str] | None) -> None:
    """head가 다르면 대상 DB의 칸 기본값을 읽어 둔 뒤 ``convert_schema`` (단계는 스레드에서, DB 없이)."""
    heads = sorted(expected_heads) if expected_heads is not None else core_migration_heads()
    if sorted(arc.manifest["migration_heads"]) != heads and arc.defaults is None:
        async with db.engine.connect() as conn:
            arc.defaults = await read_column_defaults(conn)
    await asyncio.to_thread(convert_schema, arc, expected_heads)


async def _open_archive(
    source: Path,
    staging: Path,
    identities: Sequence[str | os.PathLike[str]] | None,
    tick: Progress,
) -> tuple[_Archive, bool]:
    """(암호문이면 풀기) → 묶음 확인 → 옛 형식 변환 → 현재 형식 검사. 해시·크기는 받은 파일 기준."""
    digest, size = await asyncio.to_thread(_file_digest, source)
    encrypted = await asyncio.to_thread(_is_encrypted, source)
    if encrypted:
        if not identities:
            raise ArchiveError("encrypted_archive", "pass the age identity file")
        from career_core.adapters.age_crypt import AgeError, decrypt_file

        plain = staging / "archive.tar.gz"
        tick("decrypt", 0, size)
        try:
            await asyncio.to_thread(decrypt_file, source, plain, list(identities))
        except AgeError as exc:
            raise ArchiveError(exc.code, exc.detail) from exc
        tick("decrypt", size, size)
        (staging / "x").mkdir()
        try:
            arc = await asyncio.to_thread(read_archive, plain, staging / "x", tick)
        finally:
            plain.unlink(missing_ok=True)
    else:
        arc = await asyncio.to_thread(read_archive, source, staging, tick)
    arc.sha256, arc.bytes = digest, size
    await asyncio.to_thread(upgrade_archive, arc)
    _check_manifest(arc.manifest)
    return arc, encrypted


async def _read_tombstones(
    deletion_logs: Sequence[str | os.PathLike[str]], source_ws: UUID
) -> list[Tombstone]:
    out: list[Tombstone] = []
    for log_path in deletion_logs:
        log_ws, rows = await asyncio.to_thread(read_deletions, log_path)
        if log_ws != source_ws:
            raise ArchiveError("deletions_workspace_mismatch", str(log_ws))
        out.extend(rows)
    return out


# ---------------------------------------------------------------- ID 재매핑


class IdMap:
    """옛 UUID → 새 UUID. 줄 안의 UUID 문자열(값·중첩 JSON의 키·값)을 모두 같은 규칙으로 바꾼다.

    기본 키(uuid)에 나온 값만 바꾼다. FK·FK 없는 참조(승인 대상, 실행 입력, 멱등 키 안의 ID)는 같은 값이라
    같이 바뀐다. 묶음에 없는 UUID(바깥 ID)는 그대로 둔다.
    """

    def __init__(self) -> None:
        self.forward: dict[str, str] = {}
        self.backward: dict[str, str] = {}

    def add(self, old: str) -> None:
        if old not in self.forward:
            self.set(old, str(uuid4()))

    def set(self, old: str, new: str) -> None:
        """합치기: 옛 ID를 정한 값(새 UUID 또는 겹친 기존 행의 ID)으로."""
        self.forward[old] = new
        self.backward.setdefault(new, old)

    def apply(self, line: str) -> str:
        return _UUID.sub(lambda m: self.forward.get(m.group(0), m.group(0)), line)

    def reverse(self, line: str) -> str:
        return _UUID.sub(lambda m: self.backward.get(m.group(0), m.group(0)), line)

    def get(self, old: str) -> str:
        return self.forward.get(old, old)


def _build_id_map(plan: ExportPlan, arc: _Archive) -> IdMap:
    ids = IdMap()
    for t in plan.tables:
        cols = [c.name for c in t.columns if c.name in t.primary_key and c.type == "uuid"]
        if not cols:
            continue
        for line in arc.iter_lines(t.name):
            row = json.loads(line)
            for c in cols:
                if row.get(c):
                    ids.add(row[c])
    return ids


def _row_digest(line: str) -> int:
    """논리 해시: 정규 JSON(키 순서 무관)의 sha256. 행 순서와 무관하게 더한다 (다중집합)."""
    value = json.loads(line, parse_float=Decimal)
    return int.from_bytes(hashlib.sha256(canonical_json(value).encode()).digest(), "big")


# ---------------------------------------------------------------- 복원


@dataclass(frozen=True, slots=True)
class SkippedDeletion:
    """다시 적용하지 못한 삭제 기록 한 건과 이유.

    reason: ``still_referenced``(RESTRICT FK가 아직 가리킴, detail = 그 표·제약), ``not_permitted``
    (앱 계정이 지울 수 없는 표), ``rejected``(trigger·CHECK가 거부), ``kept_existing``(합치기: 이 공간에
    원래 있던 행이라 지우지 않음), ``error``(그 밖의 DB 오류, detail = SQLSTATE).
    """

    target_table: str
    target_id: str
    reason: str
    detail: str = ""


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
    # 새 ID로 가져왔으면 원래 공간 ID (아니면 None). 해시는 ID를 되돌린 논리 내용으로 비교했다.
    source_workspace_id: UUID | None = None
    requeued: dict[str, int] = field(default_factory=dict)
    deletions_applied: int = 0
    skipped_deletions: list[SkippedDeletion] = field(default_factory=list)
    encrypted: bool = False
    format_upgraded_from: int | None = None
    # 옛 migration head에서 바꿔 읽었으면 그 head (``convert_schema``)
    schema_converted_from: list[str] | None = None
    # 넣은 뒤 대상 버전이 달라 닫은 열린 승인 수 (새 ID로 가져올 때만, ``_recheck_approvals``)
    approvals_invalidated: int = 0

    @property
    def rows_total(self) -> int:
        return sum(t.rows for t in self.tables)

    @property
    def deletions_skipped(self) -> int:
        return len(self.skipped_deletions)


async def restore_workspace(
    db: Database,
    blobs: BlobStore,
    archive: str | os.PathLike[str],
    *,
    dry_run: bool = False,
    via: str = "cli",
    expected_heads: Sequence[str] | None = None,
    new_ids: bool = False,
    identities: Sequence[str | os.PathLike[str]] | None = None,
    deletion_logs: Sequence[str | os.PathLike[str]] = (),
    progress: Progress | None = None,
) -> RestoreReport:
    """빈 대상에 묶음을 되살린다.

    순서: (암호문이면 풀기) → 묶음 해시 확인 → (옛 형식이면 변환) → 버전·칸 구성 비교 → 대상이 비었는지 확인 →
    한 트랜잭션에서 행 넣기 → 다시 읽어 행 수·해시 비교 → 삭제 기록 다시 적용 → 대기 작업 다시 넣기 →
    원본 저장(sha256 확인, 아직 참조되는 것만) → commit. ``dry_run``이면 마지막에 rollback하고 원본도 쓰지 않는다.

    - ``new_ids``: 모든 ID를 새로 만들어 **새 공간**으로 가져온다 (같은 설치에 사본을 만들 때도 충돌이 없다).
      FK와 FK 없는 참조를 같이 바꾸고, 해시는 새 ID를 옛 ID로 되돌린 논리 내용으로 비교한다.
    - ``identities``: 암호화한 묶음을 풀 age 신원 파일.
    - ``deletion_logs``: 묶음보다 나중에 받은 삭제 기록(``export_deletions``). 그 사이 지운 자료를 다시 지운다.
    - 이미 자료가 있는 공간에는 ``merge_workspace``로 합친다.
    """
    tick = progress or _no_progress
    with tempfile.TemporaryDirectory(prefix="career-restore-") as tmp:
        staging = Path(tmp)
        arc, encrypted = await _open_archive(Path(archive), staging, identities, tick)
        m = arc.manifest
        await _convert(db, arc, expected_heads)
        source_ws = UUID(m["workspace"]["id"])
        tombstones = await _read_tombstones(deletion_logs, source_ws)
        async with db.engine.connect() as conn:
            trans = await conn.begin()
            try:
                await _enter_workspace(conn, source_ws)
                plan = build_plan(await read_catalog(conn))
                await asyncio.to_thread(_check_schema, plan, arc)
                ids: IdMap | None = None
                ws = source_ws
                if new_ids:
                    ids = await asyncio.to_thread(_build_id_map, plan, arc)
                    ws = UUID(ids.get(str(source_ws)))
                    await _enter_workspace(conn, ws)
                await _check_empty(conn, plan, ws)
                await conn.execute(text("SET CONSTRAINTS ALL DEFERRED"))
                # 행을 넣는 동안 승인 무효화 trigger를 멈춘다 (묶음의 상태 그대로, c0014)
                await conn.execute(text("SELECT set_config('career.restoring', 'on', true)"))
                total = sum(t["rows"] for t in m["tables"])

                def lines_of(name: str) -> Iterable[str]:
                    lines = arc.iter_lines(name)
                    return lines if ids is None else (ids.apply(x) for x in lines)

                await _insert_all(conn, plan, lines_of, lambda n: tick("insert", n, total))
                await conn.execute(text("SELECT set_config('career.restoring', 'off', true)"))
                # 미뤄 둔 FK를 지금 검사한다 (commit 전에 실패를 알 수 있게).
                await conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                tick("verify", 0, len(plan.tables))
                if ids is None and arc.upgraded_from is None and arc.schema_from is None:
                    reports = await _verify(conn, plan, m, ws)
                else:
                    # 새 ID, 또는 옛 형식·옛 head에서 바꿔 쓴 표(줄 모양이 DB 출력과 다를 수 있다): 논리 해시로
                    reports = await _verify_logical(conn, plan, arc, ids or IdMap(), ws)
                tick("verify", len(plan.tables), len(plan.tables))
                # 새 ID: 승인의 대상 버전 해시에 ID가 들어 있어 다시 계산하면 다르다 → 지금 닫는다.
                # ID 그대로면 묶음의 상태 그대로 둔다 (같은 행·같은 해시).
                invalidated = await _recheck_approvals(conn, None) if ids is not None else 0
                tick("deletions", 0, len(tombstones))
                applied, skipped = await _apply_deletions(
                    conn, tombstones, lambda t, i: ("apply", ids.get(i) if ids else i)
                )
                requeued = await _requeue(conn, m.get("pending_work", []), ids, ws)
                referenced = await _referenced_blobs(conn, plan, ws)
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
                    await _store_blobs(
                        blobs, ws, arc, [k for k in arc.blob_keys if k in referenced], tick
                    )
                    tick("commit", 0, 1)
                    await trans.commit()
                else:
                    await trans.rollback()
            except BaseException:
                if trans.is_active:
                    await trans.rollback()
                raise
        owners = _owners(arc.iter_lines("core.memberships"))
    notes = _archive_notes(arc)
    if skipped:
        notes.append(f"{len(skipped)} deletion(s) could not be re-applied (see skipped_deletions)")
    if invalidated:
        notes.append(_APPROVALS_NOTE.format(n=invalidated))
    return RestoreReport(
        workspace_id=ws,
        workspace_name=m["workspace"]["name"],
        tables=reports,
        blobs=len([k for k in arc.blob_keys if k in referenced]),
        dry_run=dry_run,
        owners=[UUID(ids.get(str(o))) if ids else o for o in owners],
        archive_sha256=arc.sha256,
        notes=notes,
        source_workspace_id=source_ws if ids is not None else None,
        requeued=requeued,
        deletions_applied=applied,
        skipped_deletions=skipped,
        encrypted=encrypted,
        format_upgraded_from=arc.upgraded_from,
        schema_converted_from=arc.schema_from,
        approvals_invalidated=invalidated,
    )


def _archive_notes(arc: _Archive) -> list[str]:
    m = arc.manifest
    notes = []
    if arc.upgraded_from is not None:
        notes.append(f"archive format {arc.upgraded_from} was upgraded to {FORMAT_VERSION}")
    if arc.schema_from is not None:
        notes.append(
            f"archive migrations {', '.join(arc.schema_from)} were converted to "
            f"{', '.join(m['migration_heads'])}"
        )
    if m.get("missing_blobs"):
        notes.append(f"{len(m['missing_blobs'])} blob(s) were missing at export time")
    return notes


async def _store_blobs(
    blobs: BlobStore, ws: UUID, arc: _Archive, keys: Sequence[str], tick: Progress
) -> None:
    """원본을 하나씩 저장하고 다시 읽어 sha256을 확인한다."""
    tick("blobs", 0, len(keys))
    for i, key in enumerate(keys, 1):
        data = await asyncio.to_thread((arc.blob_dir / key).read_bytes)
        await blobs.put(ws, key, data, "application/octet-stream")
        del data
        if hashlib.sha256(await blobs.get(ws, key)).hexdigest() != key:
            raise ArchiveError("blob_write_failed", key)
        tick("blobs", i, len(keys))


def _is_encrypted(path: Path) -> bool:
    from career_core.adapters.age_crypt import is_encrypted

    return is_encrypted(path)


def _check_schema(plan: ExportPlan, arc: _Archive) -> None:
    """같은 migration head라도 표·칸 구성이 실제로 같은지 본다 (부분 migration 등).

    변환 단계가 만들거나 고친 표(``arc.touched``)는 manifest의 칸 목록이 옛 것이므로, 대신 모든 행의 칸
    이름이 대상 표의 칸과 정확히 같은지 본다. 맞으면 manifest의 칸 목록을 대상 것으로 바꾼다.
    """
    manifest = arc.manifest
    archived = {t["name"]: t for t in manifest["tables"]}
    target = {t.name: t for t in plan.tables}
    if set(archived) != set(target):
        diff = sorted(set(archived) ^ set(target))
        raise ArchiveError("schema_mismatch", f"tables differ: {', '.join(diff)}")
    for name, entry in archived.items():
        want = _columns_signature(target[name])
        if name in arc.touched:
            _check_rows_fit(arc, name, [c for c, _ in want])
            entry["columns"] = want
        elif entry["columns"] != want:
            hint = " (a migration changed it without a schema step)" if arc.schema_from else ""
            raise ArchiveError("schema_mismatch", f"columns differ: {name}{hint}")


def _check_rows_fit(arc: _Archive, name: str, columns: Sequence[str]) -> None:
    want = set(columns)
    for i, row in enumerate(arc.iter_rows(name), 1):
        if set(row) != want:
            missing = sorted(want - set(row))
            extra = sorted(set(row) - want)
            raise ArchiveError(
                "schema_mismatch",
                f"{name} row {i} after conversion: missing {missing}, unexpected {extra}"[:300],
            )


async def _check_empty(conn: AsyncConnection, plan: ExportPlan, ws: UUID) -> None:
    for t in plan.tables:
        if t.scope == _MEMBERS:
            continue  # actor는 여러 공간이 같이 쓸 수 있다 (같은 ID면 그대로 둔다)
        found = await conn.scalar(
            text(f"SELECT EXISTS (SELECT 1 FROM {t.sql} AS t WHERE {scope_sql(t)})"),
            {"ws": str(ws)},
        )
        if found:
            raise ArchiveError(
                "target_not_empty",
                f"{t.name} already has rows for {ws} (merge into an existing workspace instead)",
            )


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


def _batches(lines: Iterable[str]) -> Iterator[tuple[str, int]]:
    batch: list[str] = []
    for line in lines:
        batch.append(line)
        if len(batch) >= _BATCH:
            yield "[" + ",".join(batch) + "]", len(batch)
            batch = []
    if batch:
        yield "[" + ",".join(batch) + "]", len(batch)


async def _insert_all(
    conn: AsyncConnection,
    plan: ExportPlan,
    lines_of: Callable[[str], Iterable[str]],
    inserted: Callable[[int], None] | None = None,
) -> None:
    """표마다 ``lines_of(표)``의 줄을 ``_BATCH``줄씩 넣는다. 순환 묶음(드묾)만 그 표들을 메모리에 올린다.

    ``inserted``는 지금까지 넣은 행 수를 받는다 (진행 표시).
    """
    done: set[str] = set()
    count = 0
    for t in plan.tables:
        if t.name in done:
            continue
        group = plan.group_of(t.name)
        if group is None:
            steps: Iterable[tuple[str, Iterable[str]]] = [(t.name, lines_of(t.name))]
        else:
            lines = {n: list(lines_of(n)) for n in group}
            steps = _row_order(plan, group, lines)
        for name, chunk in steps:
            sql = text(_insert_sql(plan.table(name)))
            for batch, n in _batches(chunk):
                await conn.execute(sql, {"rows": batch})
                count += n
                if inserted is not None:
                    inserted(count)
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


_MOD = 1 << 256


async def _verify_logical(
    conn: AsyncConnection, plan: ExportPlan, arc: _Archive, ids: IdMap, ws: UUID
) -> list[TableReport]:
    """새 ID로 넣은 행을 옛 ID로 되돌려 묶음의 행과 같은지 본다 (행 수 + 순서 무관 논리 해시)."""
    reports = []
    for t in plan.tables:
        want = 0
        want_rows = 0
        for line in arc.iter_lines(t.name):
            want = (want + _row_digest(line)) % _MOD
            want_rows += 1
        got = 0
        got_rows = 0
        result = await conn.stream(text(_rows_sql(t)), {"ws": str(ws)})
        async for row in result:
            got = (got + _row_digest(ids.reverse(row.j))) % _MOD
            got_rows += 1
        if (got_rows, got) != (want_rows, want):
            raise ArchiveError(
                "verify_failed", f"{t.name}: restored {got_rows} rows, archive {want_rows}"
            )
        reports.append(TableReport(t.name, got_rows, f"{got:064x}"))
    return reports


# 삭제 기록 한 건을 어떻게 할지: ("apply", 대상 ID) | ("ignore", "") | ("skip", 이유)
DeletionDecision = Callable[[str, str], tuple[str, str]]


def _skip_reason(exc: DBAPIError) -> tuple[str, str]:
    orig = exc.orig
    state = getattr(orig, "sqlstate", None) or ""
    diag = getattr(orig, "diag", None)
    table = getattr(diag, "table_name", None)
    schema = getattr(diag, "schema_name", None)
    constraint = getattr(diag, "constraint_name", None)
    where = f"{schema}.{table}" if schema and table else (table or "")
    if state == "23503":  # foreign_key_violation: 지우려는 행을 아직 다른 행이 가리킨다
        return "still_referenced", f"{where} ({constraint})" if constraint else where
    if state == "42501":  # insufficient_privilege
        return "not_permitted", where
    if state.startswith("23") or state == "P0001":  # 다른 제약 위반, trigger RAISE
        return "rejected", constraint or state
    return "error", state


async def _apply_deletions(
    conn: AsyncConnection, tombstones: Sequence[Tombstone], decide: DeletionDecision
) -> tuple[int, list[SkippedDeletion]]:
    """나중 삭제 기록을 다시 적용한다. 지울 수 없는 대상은 건너뛰고 이유를 남긴다.

    묶음에 없던 대상(그 전에 이미 지워짐)은 조용히 넘어간다 (기록도 남기지 않는다).
    """
    applied = 0
    skipped: list[SkippedDeletion] = []
    seen: set[tuple[str, str]] = set()
    for d in tombstones:
        if (d.target_table, d.target_id) in seen:
            continue
        seen.add((d.target_table, d.target_id))
        action, value = decide(d.target_table, d.target_id)
        if action == "ignore":
            continue
        if action == "skip":
            skipped.append(SkippedDeletion(d.target_table, d.target_id, value))
            continue
        schema, table = d.target_table.split(".", 1)
        target = value
        savepoint = await conn.begin_nested()
        try:
            # trigger가 지금 시각으로 남기기 전에 원래 삭제 시각으로 먼저 기록한다 (trigger는 같은 키면 그대로)
            await conn.execute(
                text(
                    "INSERT INTO core.deletions (id, workspace_id, target_table, target_id, "
                    "deleted_at, created_at) VALUES (CAST(:id AS uuid), core.current_workspace_id(), "
                    ":t, CAST(:target AS uuid), CAST(:at AS timestamptz), now()) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"id": str(uuid4()), "t": d.target_table, "target": target, "at": d.deleted_at},
            )
            gone = await conn.execute(
                text(f'DELETE FROM "{schema}"."{table}" WHERE id = CAST(:id AS uuid)'),
                {"id": target},
            )
            if not gone.rowcount:
                await savepoint.rollback()  # 이 묶음에 없던 대상: 기록도 남기지 않는다
                continue
            await savepoint.commit()
            applied += 1
        except DBAPIError as exc:
            await savepoint.rollback()
            reason, detail = _skip_reason(exc)
            skipped.append(SkippedDeletion(d.target_table, d.target_id, reason, detail))
    return applied, skipped


async def _requeue(
    conn: AsyncConnection,
    work: Sequence[dict],
    ids: IdMap | None,
    ws: UUID,
    allowed: Callable[[str, str], bool] | None = None,
) -> dict[str, int]:
    """내보낼 때 대기·실행 중이던 작업을 다시 큐에 넣는다 (대상이 남아 있는 것만, 같은 대상은 한 번)."""
    out: dict[str, int] = {}
    for w in work:
        table = REQUEUE_KINDS.get(w.get("kind", ""))
        if table is None:
            continue
        schema, name = table.split(".", 1)
        target = ids.get(w["target_id"]) if ids is not None else w["target_id"]
        if allowed is not None and not allowed(table, target):
            continue
        exists = await conn.scalar(
            text(f'SELECT EXISTS (SELECT 1 FROM "{schema}"."{name}" WHERE id = CAST(:id AS uuid))'),
            {"id": target},
        )
        if not exists:
            continue
        added = await conn.execute(
            text(
                "INSERT INTO core.jobs (id, workspace_id, kind, target_id, status, attempts, "
                "available_at, created_at) VALUES (CAST(:id AS uuid), CAST(:ws AS uuid), :kind, "
                "CAST(:target AS uuid), 'queued', 0, now(), now()) ON CONFLICT DO NOTHING"
            ),
            {"id": str(uuid4()), "ws": str(ws), "kind": w["kind"], "target": target},
        )
        if added.rowcount:
            out[w["kind"]] = out.get(w["kind"], 0) + 1
    return out


_APPROVALS_NOTE = "{n} open approval(s) were invalidated: the target changed (request them again)"
_OPEN_APPROVALS = (
    "FROM core.approvals a WHERE a.target_kind = 'application' "
    "AND a.status IN ('pending', 'approved')"
)


async def _recheck_approvals(conn: AsyncConnection, applications: set[str] | None) -> int:
    """열린 제출 승인을 대상 버전 함수(c0019 ``recheck_application_approvals``)로 다시 확인한다.

    ``applications``가 None이면 공간의 모든 지원, 아니면 그 지원들만. 버전이 요청 때와 다르면
    ``target_changed``(대상이 없으면 ``target_missing``)로 닫힌다. 닫은 수를 돌려준다.
    복원이 행을 넣는 동안은 trigger가 꺼져 있으므로(``career.restoring``) 넣은 뒤에 부른다.
    """
    if applications is not None and not applications:
        return 0
    where = "" if applications is None else " AND a.target_id = ANY(CAST(:apps AS uuid[]))"
    params = {} if applications is None else {"apps": sorted(applications)}
    open_ids = list(await conn.scalars(text(f"SELECT a.id {_OPEN_APPROVALS}{where}"), params))
    if not open_ids:
        return 0
    await conn.execute(
        text(
            "SELECT core.recheck_application_approvals(t.target_id) "
            f"FROM (SELECT DISTINCT a.target_id {_OPEN_APPROVALS}{where}) t"
        ),
        params,
    )
    closed = await conn.scalar(
        text("SELECT count(*) FROM core.approvals WHERE id = ANY(:ids) AND status = 'invalidated'"),
        {"ids": open_ids},
    )
    return int(closed or 0)


async def _referenced_blobs(conn: AsyncConnection, plan: ExportPlan, ws: UUID) -> set[str]:
    keys: set[str] = set()
    for t in plan.tables:
        for col in t.blob_columns:
            keys.update(
                await conn.scalars(
                    text(
                        f'SELECT DISTINCT t."{col}" FROM {t.sql} AS t '
                        f'WHERE {scope_sql(t)} AND t."{col}" IS NOT NULL'
                    ),
                    {"ws": str(ws)},
                )
            )
    return keys


def _owners(membership_lines: Iterable[str]) -> list[UUID]:
    out = []
    for line in membership_lines:
        row = json.loads(line)
        if row.get("role") == "owner":
            out.append(UUID(row["actor_id"]))
    return out


# ---------------------------------------------------------------- 기존 공간에 합치기

DUPLICATE_POLICIES = ("skip", "fail")
# 합칠 때 넣지 않는 표 (행은 묶음에 있어도). 이유는 보고에 같이 나간다.
MERGE_SKIPPED_TABLES: dict[str, str] = {
    "core.workspaces": "대상 공간 그대로 (이름·설정을 바꾸지 않는다)",
    "core.actors": "묶음의 사람은 대상 공간 구성원(같은 ID) 또는 합치는 사람으로 바꾼다",
    "core.memberships": "대상 공간의 구성원 그대로",
    "core.export_runs": "묶음 공간의 백업 기록. 합치기는 이 공간에 kind=merge로 남는다",
    "core.deletions": "묶음 공간의 삭제 기록 (그 공간의 ID라 이 공간에 다시 적용할 일이 없다)",
}
# 겹침을 찾을 때 먼저 보는 칸 (내용 주소·정규화한 주소·멱등 키). 나머지 UNIQUE는 그다음.
_STRONG_KEY_COLUMNS = ("sha256", "content_hash", "canonical_url", "idempotency_key")


@dataclass(frozen=True, slots=True)
class SameExcept:
    """내용 키: ``ignore`` 칸(상태·진행·바뀐 시각·메모처럼 나중에 고치는 칸)만 빼고 모든 칸이 같으면 같은 행.

    ``created_at``과 정체를 정하는 칸(제목·문장·문제 등)은 남기므로, 만든 뒤 상태·메모만 바뀐 행을 같은
    행으로 본다. 정체 칸을 고친 행은 다른 행으로 더해진다 (겹침을 놓쳐 행이 하나 늘 뿐, 다른 행을 잘못
    합쳐 잃지는 않는 쪽으로). 같은 내용(``content``) 다음에 본다.

    ``identity``: 사용자가 고칠 수 있는 정체 칸. 이 칸까지 빼면 같은 기존 행(같은 만든 시각·만든 사람·부모)이
    있으면 "비슷한 행"(``NearDuplicate``)으로 보고에 올린다. 기본은 지금처럼 둘 다 두기, 사용자가 쌍마다
    "들어오는 행 건너뛰기"를 고르면 기존 행으로 잇는다 (``merge_workspace(near_duplicates=…)``).
    """

    ignore: tuple[str, ...]
    identity: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return "same_except:" + "+".join(self.ignore)


# 비슷한 행(정체 칸만 다른 행)을 사용자 선택으로 건너뛴 수의 키 (``MergeTableReport.skipped_by``)
NEAR_DUPLICATE_KEY = "near_duplicate"
NEAR_KEEP_BOTH = "keep_both"
NEAR_SKIP_INCOMING = "skip_incoming"
# 보고에 올리는 비슷한 행 쌍의 상한 (넘으면 ``near_duplicates_total``만 늘고 나머지는 둘 다 둔다)
MAX_NEAR_DUPLICATES = 200
_NEAR_VALUE_CHARS = 300


@dataclass(frozen=True, slots=True)
class NearDuplicate:
    """정체 칸만 다른 (들어오는 행, 기존 행) 한 쌍. 값은 정체 칸만, 긴 글은 잘라서."""

    table: str
    incoming_id: str  # 묶음의 ID
    existing_id: str  # 이 공간의 ID
    columns: list[str]  # 값이 다른 정체 칸
    incoming: dict[str, Any]
    existing: dict[str, Any]
    decision: str = NEAR_KEEP_BOTH


MergeKey = tuple[str, ...] | SameExcept

# DB UNIQUE가 없거나(또는 NULL이 되는 칸뿐이거나) 행이 나중에 바뀌는 표의 내용 키.
# 칸 목록 = 그 칸들이 같으면 같은 행 (NOT NULL 칸만). ``SameExcept`` = 고치는 칸을 뺀 나머지가 같으면.
# ``test_merge_keys_cover_keyless_tables``가 합칠 표마다 키(UNIQUE·여기·CHILD_MERGE_KEYS) 또는
# ``CONTENT_ONLY_TABLES``의 이유가 있는지 본다.
EXTRA_MERGE_KEYS: dict[str, tuple[MergeKey, ...]] = {
    # 같은 파싱 실행의 같은 순번 블록 = 같은 블록 (파싱 결과는 다시 만들 수 있는 파생 자료)
    "core.document_blocks": (("parse_run_id", "ordinal"),),
    # 지원: 제목·상태·결과 메모·제출 시각은 진행하며 바뀐다. 공고·공고 버전·만든 시각·만든 사람이 같으면 같은 지원
    "core.applications": (
        SameExcept(("title", "status", "result_note", "submitted_at", "updated_at")),
    ),
    # 지원 문서: UNIQUE(지원, 문항 번호)는 문항이 없는 문서(NULL)에 안 걸린다. 제목·제한·현재 revision은 바뀐다
    "core.application_documents": (
        SameExcept(
            (
                "title",
                "question_prompt",
                "char_limit",
                "byte_limit",
                "count_rule",
                "current_revision",
                "updated_at",
            )
        ),
    ),
    # 전형 단계: 순서·이름·일정·결과·메모는 고친다. 지원·제출본·종류·만든 시각이 같으면 같은 단계
    "core.application_stages": (
        SameExcept(
            ("position", "name", "scheduled_at", "outcome", "decided_at", "note", "updated_at")
        ),
    ),
    # 추출 실행: 상태·결과 수·오류는 실행하며 채워진다
    "core.extraction_runs": (
        SameExcept(
            (
                "status",
                "error_code",
                "blocks_total",
                "blocks_sent",
                "experiences_created",
                "claims_created",
                "claims_dropped",
                "updated_at",
            )
        ),
    ),
    # 경험: 확인 상태만 바뀐다고 본다 (제목·조직·역할을 고치면 다른 행 — 다른 경험을 합쳐 잃지 않게).
    # 제목·조직·역할·기간만 다른 행은 비슷한 행으로 보여 주고 고르게 한다
    "core.experiences": (
        SameExcept(
            # c0030: 종류·상하·날짜·태그는 정리하며 바뀐다 (정체 칸이 아니다)
            (
                "status",
                "updated_at",
                "kind",
                "parent_id",
                "started_on",
                "ended_on",
                "ongoing",
                "tags",
            ),
            ("title", "organization", "role", "period_text"),
        ),
    ),
    # 주장: 확인(상태·확인한 사람·시각). 문장·종류·기여는 정체 칸 (문장·기여를 고친 행은 비슷한 행)
    "core.claims": (
        SameExcept(
            ("status", "confirmed_by", "confirmed_at", "updated_at"), ("statement", "contribution")
        ),
    ),
    # 인터뷰 세션: 상태만 (멱등 키 UNIQUE는 NULL이면 안 걸린다)
    "core.interview_sessions": (SameExcept(("status",)),),
    # 공고 가져오기 기록: 결과 칸은 실행하며 채워진다
    "core.job_fetch_runs": (
        SameExcept(
            (
                "status",
                "error_code",
                "http_status",
                "version_id",
                "unchanged",
                "candidates",
                "notes",
                "updated_at",
            )
        ),
    ),
    # 실행: 진행·결과·비용·대기 칸은 계속 바뀐다. workflow·입력·실행자·만든 시각이 정체
    "core.runs": (
        SameExcept(
            (
                "status",
                "position",
                "step",
                "steps_total",
                "result",
                "stop_requested",
                "wait_reason",
                "waits",
                "retry_at",
                "approval_id",
                "error_code",
                "cost_usd",
                "cost_unknown",
                "model_calls",
                "resumes",
                "started_at",
                "finished_at",
                "updated_at",
            )
        ),
    ),
    # 승인: 결정·사용·무효 칸만 바뀐다. 행위·대상·대상 버전·내용 해시·요청 시각이 같으면 같은 요청
    "core.approvals": (
        SameExcept(
            (
                "status",
                "decided_by",
                "decided_via",
                "decided_at",
                "decision_note",
                "used_by",
                "used_at",
                "used_result_id",
                "invalid_reason",
                "updated_at",
            )
        ),
    ),
    # 수정안: 검토(상태·거절 이유·적용한 revision)만 바뀐다 (멱등 키 UNIQUE는 NULL이면 안 걸린다)
    "core.document_suggestions": (
        SameExcept(("status", "reject_reasons", "applied_revision_id", "updated_at")),
    ),
    # 학습: 상태·메모·일정·완료 시각은 고친다. 제목·종류·요건·만든 시각이 정체
    "core.study_assessments": (
        SameExcept(("status", "scheduled_at", "notes", "updated_at"), ("title",)),
    ),
    "core.study_items": (
        SameExcept(("status", "notes", "assessment_id", "completed_at", "updated_at"), ("title",)),
    ),
    "core.study_attempts": (SameExcept(("notes",)),),
    "core.study_sessions": (SameExcept(("notes", "resources")),),
    # 오답: 복습이 간격·반복·마감·상태를 바꾼다. 문제·답·이유가 정체
    "core.study_mistakes": (
        SameExcept(
            (
                "status",
                "repetitions",
                "interval_days",
                "ease",
                "due_at",
                "last_reviewed_at",
                "reason_note",
                "updated_at",
            ),
            ("question", "wrong_answer", "correct_answer"),
        ),
    ),
    # c0033 회사 원문: 가져오기 결과 칸은 작업이 채운다. 회사·종류·주소·요청자·만든 시각이 정체
    "core.company_pages": (
        SameExcept(
            (
                "final_url",
                "title",
                "status",
                "error_code",
                "http_status",
                "method",
                "text",
                "content_hash",
                "fetched_at",
                "model_call_id",
                "candidates",
                "notes",
                "updated_at",
            )
        ),
    ),
    # c0034 자격·이수 기록: 확인 상태·연결·상세·원문 위치는 바뀐다. 이름·번호·날짜·점수가 정체
    "core.credentials": (
        SameExcept(
            (
                "status",
                "confirmed_by",
                "confirmed_at",
                "source_id",
                "experience_id",
                "parse_run_id",
                "field_spans",
                "detail",
                "updated_at",
            ),
            (
                "kind",
                "name",
                "issuer",
                "number",
                "acquired_on",
                "expires_on",
                "started_on",
                "ended_on",
                "score",
                "hours",
            ),
        ),
    ),
    # c0035 참고 글: 분류 칸(종류·제목·회사·직무·연도·메모·등급)과 가리기는 바뀐다. 원본·만든 사람·만든 시각이 정체
    "core.reference_items": (
        SameExcept(
            (
                "kind",
                "title",
                "company_id",
                "role",
                "year",
                "source_note",
                "data_class",
                "text",
                "pii_masked",
                "updated_at",
            )
        ),
    ),
    # c0035 구조 메모: 확인은 바뀐다. 메모 칸이 정체 (고친 메모는 비슷한 행)
    "core.reference_notes": (
        SameExcept(
            ("status", "confirmed_by", "confirmed_at", "updated_at"),
            ("question", "structure", "materials", "competencies"),
        ),
    ),
    # c0040 학력: 상태·날짜·학점·메모·연결은 고친다. 학교·학위 과정·만든 시각이 정체 (학교를 고친 행은 비슷한 행)
    "core.education": (
        SameExcept(
            (
                "campus",
                "major",
                "extra_major",
                "extra_major_kind",
                "status",
                "transferred",
                "entered_on",
                "graduated_on",
                "gpa",
                "gpa_scale",
                "note",
                "source_id",
                "experience_id",
                "updated_at",
            ),
            ("school", "degree"),
        ),
    ),
    # c0042 공통 답변: 종류는 고친다. 제목·본문이 정체 (고친 답변은 비슷한 행)
    "core.answer_assets": (SameExcept(("kind", "updated_at"), ("title", "body")),),
    # c0033 회사 항목: 확인·기준일은 바뀐다. 본문·종류·인용은 정체 (고친 행은 비슷한 행)
    "core.company_facts": (
        SameExcept(
            (
                "status",
                "confirmed_by",
                "confirmed_at",
                "as_of",
                "as_of_precision",
                "updated_at",
            ),
            ("kind", "body", "quote", "quote_start", "quote_end"),
        ),
    ),
}
# 내용 키가 필요 없는 표와 이유: 한 번 쓰면 고치지 않는 기록(같은 행은 내용이 같다 → ``content``),
# 또는 NULL이 되는 칸이 있어도 실제로는 늘 걸리는 UNIQUE가 있는 표.
CONTENT_ONLY_TABLES: dict[str, str] = {
    "core.audit_events": "추가만 되는 감사 기록 (고치지 않는다)",
    "core.interview_answers": "추가만 되는 답 (앱 계정이 고치거나 지우지 않는다)",
    "core.model_calls": "호출마다 한 번 쓰는 장부",
    "core.study_reviews": "복습 한 번의 기록 (고치지 않는다)",
    "core.document_suggestion_requests": "요청 기록 (c0016, 앱 계정은 추가·조회만)",
    "core.interview_question_requests": "요청 기록 (c0016, 앱 계정은 추가·조회만)",
    # CHECK(ck_claim_evidence_kind)로 범위·답 중 하나만 있으므로 UNIQUE(주장, 범위)·(주장, 답) 중 하나가 늘 걸린다
    "core.claim_evidence": "UNIQUE(claim_id, source_span_id)·(claim_id, interview_answer_id) 중 하나",
    "core.job_fetch_confirmations": "가져오기 실행 하나의 비용 확인 기록 (c0025, 같은 실행·같은 내용이면 겹친다)",
    "core.answer_asset_uses": "공통 답변을 지원 문서에 넣은 기록 (c0042, 넣을 때 한 번 쓰고 고치지 않는다)",
}
# 자식 행의 키로 부모를 찾는 경우: 원본은 버전의 sha256(같은 파일)으로 겹친다 (업로드 중복 방지와 같은 규칙)
CHILD_MERGE_KEYS: dict[str, tuple[str, str, str]] = {
    "core.sources": ("core.source_versions", "source_id", "sha256"),
}


@dataclass(frozen=True, slots=True)
class MergeTableReport:
    name: str
    archive_rows: int
    added: int
    # 겹쳐서 넣지 않은 행: 찾은 키 이름 → 수 ("id" = 같은 ID, 그 밖은 UNIQUE 제약·내용 키 이름)
    skipped_by: dict[str, int]
    # 넣은 행의 논리 해시 (순서 무관 합). 넣기 전후 공간 전체 해시의 차이와 같음을 확인했다.
    sha256: str

    @property
    def skipped(self) -> int:
        return self.archive_rows - self.added


@dataclass(frozen=True, slots=True)
class MergeReport:
    workspace_id: UUID
    workspace_name: str
    source_workspace_id: UUID
    source_workspace_name: str
    on_duplicate: str
    dry_run: bool
    archive_sha256: str
    tables: list[MergeTableReport]
    blobs: int  # 저장한(또는 dry-run이면 저장할) 원본 수
    actors_mapped: int  # 합치는 사람으로 바꾼 묶음의 actor 수
    not_merged: dict[str, str]
    requeued: dict[str, int] = field(default_factory=dict)
    deletions_applied: int = 0
    skipped_deletions: list[SkippedDeletion] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    encrypted: bool = False
    format_upgraded_from: int | None = None
    schema_converted_from: list[str] | None = None
    # 합친 지원(넣었거나 겹친)의 열린 승인 중 대상 버전이 바뀌어 닫은 수 (``_recheck_approvals``)
    approvals_invalidated: int = 0
    # 정체 칸만 다른 (들어오는 행, 기존 행) 쌍과 고른 처리 (``SameExcept.identity``). 상한까지만 싣는다
    near_duplicates: list[NearDuplicate] = field(default_factory=list)
    near_duplicates_total: int = 0

    @property
    def rows_added(self) -> int:
        return sum(t.added for t in self.tables)

    @property
    def rows_skipped(self) -> int:
        return sum(t.skipped for t in self.tables if t.name not in self.not_merged)

    @property
    def deletions_skipped(self) -> int:
        return len(self.skipped_deletions)


@dataclass(slots=True)
class _MergeState:
    ids: IdMap
    keep: dict[str, bytearray]  # 표 → 줄마다 1(넣음)/0(넣지 않음)
    skipped_by: dict[str, dict[str, int]]
    inserted: dict[str, set[str]]  # 표 → 새로 넣는 행의 (새) ID
    matched: dict[str, set[str]]  # 표 → 겹친 기존 행의 ID
    actors_mapped: int
    # (표, 묶음 ID) → 건너뛰고 이을 기존 ID (사용자가 "들어오는 행 건너뛰기"를 고른 비슷한 행)
    near_choices: Mapping[tuple[str, str], str] = field(default_factory=dict)
    near: list[NearDuplicate] = field(default_factory=list)
    near_total: int = 0
    near_used: set[tuple[str, str]] = field(default_factory=set)


def _key_order(name_cols: tuple[str, tuple[str, ...]]) -> tuple[int, str]:
    _, cols = name_cols
    strong = any(c.endswith(_STRONG_KEY_COLUMNS) for c in cols)
    return (0 if strong else 1, name_cols[0])


def _key(row: dict, cols: Sequence[str]) -> tuple[str, ...] | None:
    vals = [row.get(c) for c in cols]
    if any(v is None for v in vals):
        return None  # NULL은 UNIQUE에서 서로 다르다
    return tuple(canonical_json(v) for v in vals)


async def _target_rows(
    conn: AsyncConnection, table: Table, cols: Sequence[str], ws: UUID
) -> list[dict]:
    pairs = ", ".join(f"'{c}', t.\"{c}\"" for c in cols)
    result = await conn.stream(
        text(
            f"SELECT jsonb_build_object({pairs})::text AS j FROM {table.sql} AS t "
            f"WHERE {scope_sql(table)}"
        ),
        {"ws": str(ws)},
    )
    return [json.loads(r.j) async for r in result]


async def _match_all(
    conn: AsyncConnection,
    plan: ExportPlan,
    arc: _Archive,
    ws: UUID,
    me: UUID,
    tick: Progress,
    near_choices: Mapping[tuple[str, str], str] | None = None,
) -> _MergeState:
    """표마다 (FK 순서로) 묶음의 행이 대상 공간에 이미 있는지 정하고, 모든 ID의 새 값을 정한다.

    찾는 순서: 같은 ID(그리고 이미 정한 FK 칸이 같음) → DB UNIQUE 제약(내용 주소·주소·멱등 키 먼저) →
    내용 키(``EXTRA_MERGE_KEYS``의 칸 목록·``CHILD_MERGE_KEYS``) → 같은 내용(기본 키 밖 모든 칸, ``content``)
    → 고치는 칸을 뺀 같은 내용(``EXTRA_MERGE_KEYS``의 ``SameExcept``).
    찾으면 그 기존 행의 ID로, 못 찾으면 새 UUID로. 못 찾은 행 중 정체 칸까지 빼면 같은 기존 행이 있으면
    비슷한 행으로 적고, ``near_choices``에 (표, 묶음 ID) → 그 기존 ID가 있으면 기존 행으로 잇는다.
    키 칸은 부모 표의 결정이 끝난 뒤 바꾼 값으로 비교한다. 아직 정하지 않은 표(뒤 표·같은 표)를 가리키는
    FK 칸이 든 키는 쓰지 않는다. 이번에 넣기로 한 행의 키도 색인에 넣어 묶음 안의 겹침도 잡는다.
    """
    ids = IdMap()
    m = arc.manifest
    source_ws = m["workspace"]["id"]
    ids.set(source_ws, str(ws))
    members = set(
        await conn.scalars(
            text(
                'SELECT actor_id::text FROM "core"."memberships" '
                "WHERE workspace_id = CAST(:ws AS uuid)"
            ),
            {"ws": str(ws)},
        )
    )
    mapped = 0
    for line in arc.iter_lines("core.actors"):
        old = json.loads(line)["id"]
        if old in members:
            ids.set(old, old)
        else:
            ids.set(old, str(me))
            mapped += 1

    state = _MergeState(ids, {}, {}, {}, {}, mapped, near_choices=dict(near_choices or {}))
    decided = {"core.workspaces", "core.actors"}
    for n, t in enumerate(plan.tables):
        tick("match", n, len(plan.tables))
        rows = next(e["rows"] for e in m["tables"] if e["name"] == t.name)
        state.keep[t.name] = bytearray(rows)  # 모두 0 = 넣지 않음
        state.skipped_by[t.name] = {}
        if t.name in MERGE_SKIPPED_TABLES or t.scope != _WORKSPACE:
            decided.add(t.name)
            continue
        await _match_table(conn, plan, arc, t, ws, state, decided)
        decided.add(t.name)
    if stale := set(state.near_choices) - state.near_used:
        # 확인 뒤 이 공간이 바뀌어 고른 쌍이 더는 그 쌍이 아니다 (다시 확인하고 고른다)
        table, row = min(stale)
        raise ArchiveError(
            "near_duplicate_changed", f"{len(stale)} chosen pair(s), e.g. {table} {row}"
        )
    tick("match", len(plan.tables), len(plan.tables))
    return state


async def _match_table(
    conn: AsyncConnection,
    plan: ExportPlan,
    arc: _Archive,
    t: Table,
    ws: UUID,
    state: _MergeState,
    decided: set[str],
) -> None:
    ids = state.ids
    pk_cols = t.primary_key
    pk = pk_cols[0] if len(pk_cols) == 1 and _col_type(t, pk_cols[0]) == "uuid" else None
    unsettled: set[str] = set()
    settled_fk: set[str] = set()
    for fk in t.foreign_keys:
        target = fk.ref_table
        for c in fk.columns:
            if c == "workspace_id":
                continue
            if target in decided and target != t.name:
                settled_fk.add(c)
            else:
                unsettled.add(c)
    settled_fk -= unsettled
    keys: list[tuple[str, tuple[str, ...]]] = []
    if pk is None:
        keys.append(("primary_key", pk_cols))
    extra = EXTRA_MERGE_KEYS.get(t.name, ())
    candidates = sorted(t.unique_keys, key=_key_order) + [
        ("+".join(k), k) for k in extra if not isinstance(k, SameExcept)
    ]
    for name, cols in candidates:
        if (pk is not None and pk in cols) or unsettled & set(cols):
            continue
        keys.append((name, cols))
    needed = set(pk_cols) | settled_fk
    # 마지막 키: 기본 키·아직 정하지 않은 FK 칸을 뺀 모든 칸이 같은 행 (UNIQUE가 없는 표에서도,
    # 앞서 합쳐 새 ID로 들어간 행을 같은 묶음으로 다시 합칠 때 또 넣지 않게)
    content_skip = set(pk_cols) | unsettled
    # 그다음: 고치는 칸을 뺀 내용 (``SameExcept``)
    loose = [(k.name, content_skip | set(k.ignore)) for k in extra if isinstance(k, SameExcept)]
    # 마지막: 정체 칸까지 뺀 내용이 같은 **기존** 행 = 비슷한 행 (보고에 올리고 사용자가 고른다)
    near = [
        (k.identity, content_skip | set(k.ignore) | set(k.identity))
        for k in extra
        if isinstance(k, SameExcept) and k.identity and pk is not None
    ]
    by_id: dict[str, dict] = {}
    index: dict[str, dict[tuple[str, ...], str]] = {name: {} for name, _ in keys}
    content: dict[str, str] = {}
    loose_index: dict[str, dict[str, str]] = {name: {} for name, _ in loose}
    near_index: list[dict[str, str]] = [{} for _ in near]
    near_values: dict[str, dict] = {}
    result = await conn.stream(text(_rows_sql(t)), {"ws": str(ws)})
    async for r in result:
        row = json.loads(r.j, parse_float=Decimal)
        ref = row[pk] if pk else ""
        if pk:
            by_id[ref] = {c: row.get(c) for c in needed}
        for name, cols in keys:
            k = _key(row, cols)
            if k is not None:
                index[name].setdefault(k, ref)
        content.setdefault(_content_key(row, content_skip), ref)
        for name, skip in loose:
            loose_index[name].setdefault(_content_key(row, skip), ref)
        for (identity, skip), idx in zip(near, near_index, strict=True):
            if idx.setdefault(_content_key(row, skip), ref) == ref:
                near_values[ref] = {c: row.get(c) for c in identity}
    child = CHILD_MERGE_KEYS.get(t.name)
    child_hits: dict[str, str] = {}
    if child is not None and pk is not None:
        child_table, fk_col, key_col = child
        target_child = {
            r[key_col]: r[fk_col]
            for r in await _target_rows(conn, plan.table(child_table), [fk_col, key_col], ws)
        }
        for line in arc.iter_lines(child_table):
            r = json.loads(line)
            hit = target_child.get(r.get(key_col))
            if hit is not None:
                child_hits.setdefault(r[fk_col], hit)

    keep = state.keep[t.name]
    skipped_by = state.skipped_by[t.name]
    inserted = state.inserted.setdefault(t.name, set())
    matched = state.matched.setdefault(t.name, set())
    for i, line in enumerate(arc.iter_lines(t.name)):
        row = json.loads(ids.apply(line), parse_float=Decimal)
        hit: str | None = None
        by = ""
        old = json.loads(line)[pk] if pk else None
        if pk is not None and old in by_id:
            ex = by_id[old]
            if all(ex.get(c) == row.get(c) for c in settled_fk):
                hit, by = old, "id"
        if hit is None:
            for name, cols in keys:
                k = _key(row, cols)
                if k is not None and k in index[name]:
                    hit, by = index[name][k], name
                    break
        if hit is None and old is not None and old in child_hits:
            hit, by = child_hits[old], f"{child[0]}.{child[2]}"
        digest = _content_key(row, content_skip)
        if hit is None and digest in content:
            hit, by = content[digest], "content"
        loose_keys = [(name, _content_key(row, skip)) for name, skip in loose]
        if hit is None:
            for name, k in loose_keys:
                if k in loose_index[name]:
                    hit, by = loose_index[name][k], name
                    break
        if hit is None and near:
            hit = _near_duplicate(t.name, old, row, near, near_index, near_values, state)
            by = NEAR_DUPLICATE_KEY
        if hit is not None:
            skipped_by[by] = skipped_by.get(by, 0) + 1
            if pk is not None:
                ids.set(old, hit)
                matched.add(hit)
            continue
        keep[i] = 1
        new = str(uuid4()) if pk is not None else ""
        if pk is not None:
            ids.set(old, new)
            inserted.add(new)
        for name, cols in keys:
            k = _key(row, cols)
            if k is not None:
                index[name].setdefault(k, new)
        content.setdefault(digest, new)
        for name, k in loose_keys:
            loose_index[name].setdefault(k, new)


def _near_duplicate(
    table: str,
    old: str,
    row: dict,
    near: list[tuple[tuple[str, ...], set[str]]],
    near_index: list[dict[str, str]],
    near_values: dict[str, dict],
    state: _MergeState,
) -> str | None:
    """정체 칸만 다른 기존 행이 있으면 적고, 사용자가 건너뛰기를 고른 쌍이면 그 기존 ID (아니면 None)."""
    for (identity, skip), idx in zip(near, near_index, strict=True):
        existing = idx.get(_content_key(row, skip))
        if existing is None:
            continue
        theirs = near_values.get(existing, {})
        mine = {c: row.get(c) for c in identity}
        chosen = state.near_choices.get((table, old))
        skip_it = chosen == existing
        if chosen is not None and skip_it:
            state.near_used.add((table, old))
        state.near_total += 1
        if len(state.near) < MAX_NEAR_DUPLICATES:
            state.near.append(
                NearDuplicate(
                    table=table,
                    incoming_id=old,
                    existing_id=existing,
                    columns=[c for c in identity if mine[c] != theirs.get(c)],
                    incoming={c: _near_value(v) for c, v in mine.items()},
                    existing={c: _near_value(theirs.get(c)) for c in identity},
                    decision=NEAR_SKIP_INCOMING if skip_it else NEAR_KEEP_BOTH,
                )
            )
        return existing if skip_it else None
    return None


def _near_value(value: Any) -> Any:
    """보고용 값: 글은 잘라서, 그 밖의 값(숫자·JSON)은 정규 JSON 글로."""
    if value is None or isinstance(value, bool | int):
        return value
    out = value if isinstance(value, str) else canonical_json(value)
    return out if len(out) <= _NEAR_VALUE_CHARS else out[: _NEAR_VALUE_CHARS - 1] + "…"


def _content_key(row: dict, skip: set[str]) -> str:
    body = {k: v for k, v in row.items() if k not in skip}
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def _col_type(t: Table, name: str) -> str:
    return next(c.type for c in t.columns if c.name == name)


async def _scope_digest(conn: AsyncConnection, t: Table, ws: UUID) -> tuple[int, int]:
    """공간 안 그 표의 (행 수, 순서 무관 논리 해시)."""
    total = n = 0
    result = await conn.stream(text(_rows_sql(t)), {"ws": str(ws)})
    async for row in result:
        total = (total + _row_digest(row.j)) % _MOD
        n += 1
    return n, total


async def merge_workspace(
    db: Database,
    blobs: BlobStore,
    actor: ActorContext,
    archive: str | os.PathLike[str],
    *,
    on_duplicate: str = "skip",
    dry_run: bool = False,
    via: str = "cli",
    expected_heads: Sequence[str] | None = None,
    identities: Sequence[str | os.PathLike[str]] | None = None,
    deletion_logs: Sequence[str | os.PathLike[str]] = (),
    permissions: PermissionPolicy | None = None,
    progress: Progress | None = None,
    near_duplicates: Mapping[tuple[str, str], str] | None = None,
    staging_dir: str | os.PathLike[str] | None = None,
) -> MergeReport:
    """묶음을 actor의 (자료가 있는) 공간에 합친다. 소유자(workspace-admin)만.

    - 모든 ID를 새로 만든다. 겹치는 행(``_match_all``)은 ``on_duplicate``대로: ``skip``이면 넣지 않고 그
      행을 가리키던 자식은 기존 행을 가리키게 한다, ``fail``이면 아무것도 쓰지 않고 ``duplicates_found``.
    - 공간·구성원·백업 기록·삭제 기록은 넣지 않는다 (``MERGE_SKIPPED_TABLES``). 묶음의 actor는 대상 공간의
      구성원(같은 ID)이면 그대로, 아니면 합치는 사람으로 바꾼다.
    - 기존 행은 고치거나 지우지 않는다: INSERT만 하고, 표마다 넣기 전후 공간 전체의 논리 해시 차이가
      넣은 행의 해시 합과 같은지 본다 (추가만 되는 표도 그대로). 다르면 rollback.
    - 한 트랜잭션(REPEATABLE READ)이다. 확인이 끝난 뒤에만 원본을 저장하고 commit. 하나라도 틀리면 아무것도 남지 않는다.
    - ``deletion_logs``(묶음 공간의 나중 삭제 기록)는 이번에 넣은 행에만 적용한다 (기존 행은 ``kept_existing``).
    - 비슷한 행(``SameExcept.identity``: 정체 칸만 다른 기존 행)은 보고 ``near_duplicates``에 쌍으로 싣는다.
      기본은 둘 다 둔다(들어오는 행을 새 행으로). ``near_duplicates``에 (표, 묶음 ID) → 기존 ID를 주면 그 쌍은
      들어오는 행을 건너뛰고 기존 행으로 잇는다 (자식도 기존 행을 가리킨다). 고른 쌍이 더는 그 쌍이 아니면
      ``near_duplicate_changed``. 사용자가 고른 건너뛰기는 ``fail`` 정책의 겹침으로 세지 않는다.
    - ``staging_dir``: 묶음을 풀어 둘 폴더 (없으면 시스템 임시 디렉터리). 웹 복원 worker는 작업 폴더를 쓴다.
    """
    if on_duplicate not in DUPLICATE_POLICIES:
        raise ExportError("invalid_policy", on_duplicate[:20])
    if not (permissions or ScopePermissionPolicy()).allows(actor, "workspace.restore"):
        raise ExportError("forbidden")
    tick = progress or _no_progress
    ws = actor.workspace_id
    with tempfile.TemporaryDirectory(prefix="career-merge-", dir=staging_dir) as tmp:
        staging = Path(tmp)
        arc, encrypted = await _open_archive(Path(archive), staging, identities, tick)
        m = arc.manifest
        await _convert(db, arc, expected_heads)
        source_ws = UUID(m["workspace"]["id"])
        tombstones = await _read_tombstones(deletion_logs, source_ws)
        async with db.engine.connect() as conn:
            # 다른 요청이 같은 공간에 쓰더라도 전후 해시는 이 트랜잭션의 스냅샷 기준이다
            conn = await conn.execution_options(isolation_level="REPEATABLE READ")
            trans = await conn.begin()
            try:
                await _enter_workspace(conn, ws)
                plan = build_plan(await read_catalog(conn))
                await asyncio.to_thread(_check_schema, plan, arc)
                name = await conn.scalar(
                    text('SELECT name FROM "core"."workspaces" WHERE id = CAST(:ws AS uuid)'),
                    {"ws": str(ws)},
                )
                if name is None:
                    raise ExportError("workspace_not_found")
                state = await _match_all(
                    conn, plan, arc, ws, actor.subject_id, tick, near_duplicates
                )
                counted = [
                    t.name
                    for t in plan.tables
                    if t.name not in MERGE_SKIPPED_TABLES and t.scope == _WORKSPACE
                ]
                dupes = {
                    n: sum(c for k, c in state.skipped_by[n].items() if k != NEAR_DUPLICATE_KEY)
                    for n in counted
                }
                if on_duplicate == "fail" and any(dupes.values()):
                    summary = ", ".join(f"{n} {c}" for n, c in dupes.items() if c)
                    raise ArchiveError("duplicates_found", summary[:500])

                tick("verify", 0, len(plan.tables))
                before = {t.name: await _scope_digest(conn, t, ws) for t in plan.tables}
                added: dict[str, list[int]] = {t.name: [0, 0] for t in plan.tables}
                blob_refs: set[str] = set()
                blob_cols = {t.name: t.blob_columns for t in plan.tables}

                def lines_of(table: str) -> Iterator[str]:
                    keep = state.keep[table]
                    acc = added[table]
                    for i, line in enumerate(arc.iter_lines(table)):
                        if not keep[i]:
                            continue
                        out = state.ids.apply(line)
                        acc[0] += 1
                        acc[1] = (acc[1] + _row_digest(out)) % _MOD
                        if blob_cols[table]:
                            row = json.loads(out)
                            blob_refs.update(row[c] for c in blob_cols[table] if row.get(c))
                        yield out

                total = sum(sum(k) for k in state.keep.values())
                await conn.execute(text("SET CONSTRAINTS ALL DEFERRED"))
                await conn.execute(text("SELECT set_config('career.restoring', 'on', true)"))
                try:
                    await _insert_all(conn, plan, lines_of, lambda n: tick("insert", n, total))
                    await conn.execute(text("SELECT set_config('career.restoring', 'off', true)"))
                    await conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                except IntegrityError as exc:
                    reason, detail = _skip_reason(exc)
                    raise ArchiveError("merge_conflict", detail or reason) from exc

                reports = []
                for n, t in enumerate(plan.tables):
                    tick("verify", n, len(plan.tables))
                    rows_after, digest_after = await _scope_digest(conn, t, ws)
                    rows_before, digest_before = before[t.name]
                    got = (rows_after - rows_before, (digest_after - digest_before) % _MOD)
                    if got != tuple(added[t.name]):
                        raise ArchiveError(
                            "verify_failed",
                            f"{t.name}: {got[0]} new rows, expected {added[t.name][0]}",
                        )
                    archive_rows = len(state.keep[t.name])
                    reports.append(
                        MergeTableReport(
                            t.name,
                            archive_rows,
                            added[t.name][0],
                            dict(state.skipped_by[t.name]),
                            f"{added[t.name][1]:064x}",
                        )
                    )
                tick("verify", len(plan.tables), len(plan.tables))
                # 넣는 동안 꺼 둔 즉시 무효화(career.restoring)를 대신한다: 넣었거나 겹친 지원의 열린
                # 승인을 같은 트랜잭션에서 c0019 함수로 다시 계산해 바뀐 것만 닫는다 (확인 뒤라 전후 해시와 무관).
                touched_apps = state.inserted.get("core.applications", set()) | state.matched.get(
                    "core.applications", set()
                )
                invalidated = await _recheck_approvals(conn, touched_apps)

                def decide(table: str, target: str) -> tuple[str, str]:
                    new = state.ids.get(target)
                    if new in state.inserted.get(table, ()):
                        return "apply", new
                    if new in state.matched.get(table, ()):
                        return "skip", "kept_existing"
                    return "ignore", ""

                tick("deletions", 0, len(tombstones))
                applied, skipped = await _apply_deletions(conn, tombstones, decide)
                requeued = await _requeue(
                    conn,
                    m.get("pending_work", []),
                    state.ids,
                    ws,
                    allowed=lambda table, target: target in state.inserted.get(table, ()),
                )
                still = await _referenced_blobs(conn, plan, ws)
                in_archive = set(arc.blob_keys)
                to_store = sorted(k for k in blob_refs if k in still and k in in_archive)
                missing = sorted(k for k in blob_refs if k in still and k not in in_archive)
                if not dry_run:
                    await conn.execute(
                        insert(ExportRun).values(
                            id=uuid4(),
                            workspace_id=ws,
                            kind="merge",
                            via=via,
                            created_by=actor.subject_id,
                            format_version=FORMAT_VERSION,
                            archive_sha256=arc.sha256,
                            archive_bytes=arc.bytes,
                            rows_total=sum(r.added for r in reports),
                            blobs_total=len(to_store),
                            created_at=datetime.now(UTC),
                        )
                    )
                    await _store_blobs(blobs, ws, arc, to_store, tick)
                    tick("commit", 0, 1)
                    await trans.commit()
                else:
                    await trans.rollback()
            except BaseException:
                if trans.is_active:
                    await trans.rollback()
                raise
    notes = _archive_notes(arc)
    if missing:
        notes.append(f"{len(missing)} referenced blob(s) are not in the archive")
    if skipped:
        notes.append(f"{len(skipped)} deletion(s) were not re-applied (see skipped_deletions)")
    if invalidated:
        notes.append(_APPROVALS_NOTE.format(n=invalidated))
    not_merged = {
        t: reason
        for t, reason in MERGE_SKIPPED_TABLES.items()
        if any(r.name == t and r.archive_rows for r in reports)
    }
    return MergeReport(
        workspace_id=ws,
        workspace_name=name,
        source_workspace_id=source_ws,
        source_workspace_name=m["workspace"]["name"],
        on_duplicate=on_duplicate,
        dry_run=dry_run,
        archive_sha256=arc.sha256,
        tables=reports,
        blobs=len(to_store),
        actors_mapped=state.actors_mapped,
        not_merged=not_merged,
        requeued=requeued,
        deletions_applied=applied,
        skipped_deletions=skipped,
        notes=notes,
        encrypted=encrypted,
        format_upgraded_from=arc.upgraded_from,
        schema_converted_from=arc.schema_from,
        approvals_invalidated=invalidated,
        near_duplicates=state.near,
        near_duplicates_total=state.near_total,
    )


__all__ = [
    "BLOB_COLUMNS",
    "CONTENT_ONLY_TABLES",
    "DELETIONS_FORMAT",
    "DUPLICATE_POLICIES",
    "EXCLUDED_TABLES",
    "EXTRA_MERGE_KEYS",
    "FORMAT",
    "FORMAT_VERSION",
    "MAX_NEAR_DUPLICATES",
    "MERGE_SKIPPED_TABLES",
    "NEAR_DUPLICATE_KEY",
    "NEAR_KEEP_BOTH",
    "NEAR_SKIP_INCOMING",
    "REQUEUE_KINDS",
    "SCHEMA_FLOOR",
    "SCHEMA_STEPS",
    "TOMBSTONE_TABLES",
    "UPGRADES",
    "ArchiveError",
    "ColumnDefault",
    "ExportError",
    "ExportPlan",
    "ExportReport",
    "FormatUpgrade",
    "IdMap",
    "MergeReport",
    "MergeTableReport",
    "NearDuplicate",
    "PlanError",
    "Progress",
    "RestoreReport",
    "SameExcept",
    "SchemaStep",
    "SkippedDeletion",
    "TableReport",
    "Tombstone",
    "build_plan",
    "convert_schema",
    "core_migration_heads",
    "export_deletions",
    "export_workspace",
    "format_upgrade",
    "merge_workspace",
    "no_data_change",
    "read_archive",
    "read_catalog",
    "read_column_defaults",
    "read_deletions",
    "readable_versions",
    "restore_workspace",
    "schema_path",
    "schema_step",
    "upgrade_archive",
]

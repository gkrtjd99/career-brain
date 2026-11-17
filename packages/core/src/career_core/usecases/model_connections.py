"""화면에서 모델 연결·용도별 라우트·월 예산 관리 (O22, plan §21).

- 파일(``models.toml``)의 연결·라우트·예산은 그대로 읽는다 (읽기 전용, ``source: "file"``).
  화면에서 만든 연결은 DB(``core.model_connections``)에 두고 ``source: "ui"``. 이름이 파일과 겹치면 거부한다.
- 라우트·예산은 화면 값이 파일 값을 덮는다. 라우트 덮기를 지우면(``null``) 파일 값으로 돌아간다.
- 키는 ``SecretBox``로 암호화해 ``core.model_connection_secrets``에 둔다. 응답·로그·장부·내보내기에 키 값을
  넣지 않는다 (끝 4자리만). 게이트웨이에는 연결마다 합성 환경변수 이름(``CAREER_CONN_<NAME>_KEY``)을 주고,
  풀어낸 키를 ``os.environ`` 위에 얹은 Mapping으로 넘긴다 (``os.environ``은 바꾸지 않는다).
- ``ModelConnections``는 게이트웨이의 ``ConfigOverlay``다. 이것을 주입한 앱(개인 앱)에서만 켜진다.
"""

from __future__ import annotations

import re
from collections import ChainMap
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from career_core.adapters.secret_box import SecretBox, last4
from career_core.db import Database
from career_core.db.models import (
    ModelConnection,
    ModelConnectionSecret,
    ModelRouteOverride,
    ModelSettings,
)
from career_core.llm.config import (
    BILLING_METERED,
    BILLINGS,
    EMBED_PURPOSE,
    KIND_LOCAL,
    PRESETS,
    TOKEN_PARAMS,
    EndpointConfig,
    ModelsConfig,
    Route,
)
from career_core.llm.gateway import month_spend
from career_core.ports import ActorContext

# 앱이 부르는 용도 (화면이 아직 정하지 않은 용도도 보여 줄 수 있게). default는 명시 라우트가 없는 용도의 기본.
KNOWN_PURPOSES: tuple[str, ...] = (
    "default",
    "extract",
    "interview",
    "doc_suggest",
    "job_extract",
    "job_extract_image",
    "company_facts",
    "reference_notes",
    "application_prep",
    "embed",
    "judge_claim",
    "judge_visibility",
)
SELECTABLE_DATA_CLASSES = frozenset({"public", "private"})
PRICE_PROVIDERS = frozenset(p.pricing_provider for p in PRESETS.values() if p.pricing_provider)
KEY_ENV_PREFIX = "CAREER_CONN_"
TEST_TIMEOUT_SECONDS = 10.0
ANTHROPIC_API_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
MAX_KEY_CHARS = 1000
MAX_MODELS_LISTED = 500

_NAME = re.compile(r"[a-z][a-z0-9_-]{0,39}")
_PURPOSE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_URL = re.compile(r"https?://[^\s]{1,490}")

KEY_SET = "set"
KEY_MISSING = "missing"
KEY_NOT_NEEDED = "not_needed"
KEY_NEEDS_REENTRY = "needs_reentry"


class ModelAdminError(Exception):
    """화면 설정 오류. ``code``만 응답에 나간다 (입력 값, 특히 키는 넣지 않는다)."""

    def __init__(self, code: str, status: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def key_env_name(name: str) -> str:
    """화면 연결의 합성 키 환경변수 이름. 실제 환경변수가 아니라 게이트웨이에 넘기는 Mapping의 키다."""
    return KEY_ENV_PREFIX + re.sub(r"[^A-Z0-9]", "_", name.upper()) + "_KEY"


@dataclass(frozen=True, slots=True)
class ConnectionInput:
    """POST 본문 / PATCH 본문(``fields``에 보낸 칸만)."""

    name: str | None = None
    preset: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    billing: str | None = None
    price_as: str | None = None
    data_classes: Sequence[str] | None = None
    token_param: str | None = None
    fields: frozenset[str] = field(default_factory=frozenset)


@dataclass(slots=True)
class _State:
    connections: list[ModelConnection]
    secrets: dict[UUID, bytes]
    overrides: list[ModelRouteOverride]
    settings: ModelSettings | None


async def _read_state(s: AsyncSession, ws: UUID) -> _State:
    conns = (
        (
            await s.execute(
                select(ModelConnection)
                .where(ModelConnection.workspace_id == ws)
                .order_by(ModelConnection.name)
            )
        )
        .scalars()
        .all()
    )
    secrets = {
        r.connection_id: bytes(r.ciphertext)
        for r in (
            await s.execute(
                select(ModelConnectionSecret.connection_id, ModelConnectionSecret.ciphertext).where(
                    ModelConnectionSecret.workspace_id == ws
                )
            )
        ).all()
    }
    overrides = (
        (
            await s.execute(
                select(ModelRouteOverride)
                .where(ModelRouteOverride.workspace_id == ws)
                .order_by(ModelRouteOverride.purpose)
            )
        )
        .scalars()
        .all()
    )
    settings = await s.scalar(select(ModelSettings).where(ModelSettings.workspace_id == ws))
    return _State(list(conns), secrets, list(overrides), settings)


def _endpoint(row: ModelConnection, has_key: bool) -> EndpointConfig:
    preset = PRESETS[row.preset]
    metered = row.billing == BILLING_METERED
    needs_key_env = preset.api_key_env is not None or has_key or row.api_key_last4 is not None
    return EndpointConfig(
        name=row.name,
        kind=preset.kind,
        base_url=row.base_url or preset.base_url,
        api_key_env=key_env_name(row.name) if needs_key_env else None,
        # 종량제 직접 연결(LiteLLM 등)은 단가 기준(price_as)을 단가 제공자로 쓴다
        pricing_provider=preset.pricing_provider or (row.price_as if metered else None),
        billing=row.billing,
        token_param=row.token_param,
        data_classes=frozenset(row.data_classes or ()),
        price_as=row.price_as,
        preset=row.preset,
    )


def _route_problem(purpose: str, endpoint: EndpointConfig | None) -> str | None:
    if endpoint is None:
        return "unknown_endpoint"
    if purpose != EMBED_PURPOSE and endpoint.kind == KIND_LOCAL:
        return "local_only_embed"
    if purpose == EMBED_PURPOSE and endpoint.kind == "anthropic":
        return "embed_needs_openai"
    return None


@dataclass(frozen=True, slots=True)
class _Merged:
    config: ModelsConfig
    env: Mapping[str, str]
    ui_names: frozenset[str]
    route_sources: dict[str, str]
    key_states: dict[str, str]
    last4: dict[str, str | None]
    budget_source: str


def _merge(
    base: ModelsConfig, base_env: Mapping[str, str], state: _State, box: SecretBox
) -> _Merged:
    endpoints = dict(base.endpoints)
    keys: dict[str, str] = {}
    key_states: dict[str, str] = {}
    shown_last4: dict[str, str | None] = {}
    ui_names: set[str] = set()
    for row in state.connections:
        if row.name in base.endpoints or row.preset not in PRESETS:
            continue  # 파일이 이긴다 (화면 저장은 겹치면 거부하지만, 파일이 나중에 같은 이름을 더할 수 있다)
        cipher = state.secrets.get(row.id)
        plain = box.decrypt(cipher) if cipher is not None else None
        endpoints[row.name] = _endpoint(row, plain is not None)
        ui_names.add(row.name)
        shown_last4[row.name] = row.api_key_last4
        if plain:
            keys[key_env_name(row.name)] = plain
            key_states[row.name] = KEY_SET
        elif row.api_key_last4 is not None or cipher is not None:
            key_states[row.name] = KEY_NEEDS_REENTRY  # 복원했거나 암호 키가 바뀌었다
        elif PRESETS[row.preset].api_key_env is not None:
            key_states[row.name] = KEY_MISSING
        else:
            key_states[row.name] = KEY_NOT_NEEDED
    routes = dict(base.routes)
    route_sources = dict.fromkeys(base.routes, "file")
    for o in state.overrides:
        if _route_problem(o.purpose, endpoints.get(o.endpoint)) is None:
            routes[o.purpose] = Route(o.endpoint, o.model)
            route_sources[o.purpose] = "ui"
    budget = base.budget
    budget_source = "file"
    if state.settings is not None and state.settings.monthly_usd is not None:
        budget = replace(base.budget, monthly_usd=Decimal(state.settings.monthly_usd))
        budget_source = "ui"
    config = ModelsConfig(
        endpoints=endpoints,
        routes=routes,
        budget=budget,
        price_files=base.price_files,
        embedding=base.embedding,
    )
    env: Mapping[str, str] = ChainMap(keys, base_env) if keys else base_env  # type: ignore[arg-type]
    return _Merged(
        config,
        env,
        frozenset(ui_names),
        route_sources,
        key_states,
        shown_last4,
        budget_source,
    )


def _file_key_state(ep: EndpointConfig, env: Mapping[str, str]) -> str:
    if not ep.api_key_env:
        return KEY_NOT_NEEDED
    return KEY_SET if env.get(ep.api_key_env) else KEY_MISSING


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


def _money(value: Decimal | None) -> str | None:
    return None if value is None else format(Decimal(value).normalize(), "f")


class ModelConnections:
    """``ConfigOverlay`` + 화면 설정 저장. 시험 호출 transport는 시험에서 바꿀 수 있다."""

    def __init__(
        self,
        db: Database,
        box: SecretBox,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        test_timeout: float = TEST_TIMEOUT_SECONDS,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self._db = db
        self._box = box
        self._transport = transport
        self._timeout = test_timeout
        self._clock = clock

    def __repr__(self) -> str:
        return "ModelConnections(<db>)"

    # ------------------------------------------------------------ ConfigOverlay

    async def signature(self, workspace_id: UUID) -> Hashable:
        """싼 확인: 화면 설정 버전 + 행 수·마지막 수정 시각 (복원으로 행이 들어온 경우도 잡는다)."""
        async with self._db.workspace_session(workspace_id) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT "
                        "(SELECT version FROM core.model_settings WHERE workspace_id = :ws) AS v, "
                        "(SELECT count(*) FROM core.model_connections WHERE workspace_id = :ws) "
                        "AS nc, "
                        "(SELECT max(updated_at) FROM core.model_connections "
                        "WHERE workspace_id = :ws) AS mc, "
                        "(SELECT count(*) FROM core.model_connection_secrets "
                        "WHERE workspace_id = :ws) AS ns, "
                        "(SELECT max(updated_at) FROM core.model_connection_secrets "
                        "WHERE workspace_id = :ws) AS ms, "
                        "(SELECT count(*) FROM core.model_route_overrides "
                        "WHERE workspace_id = :ws) AS nr, "
                        "(SELECT max(updated_at) FROM core.model_route_overrides "
                        "WHERE workspace_id = :ws) AS mr, "
                        "(SELECT monthly_usd FROM core.model_settings WHERE workspace_id = :ws) "
                        "AS mu"
                    ),
                    {"ws": workspace_id},
                )
            ).one()
        return tuple(row)

    async def build(
        self, workspace_id: UUID, base: ModelsConfig, env: Mapping[str, str]
    ) -> tuple[ModelsConfig, Mapping[str, str]]:
        async with self._db.workspace_session(workspace_id) as s:
            state = await _read_state(s, workspace_id)
        merged = _merge(base, env, state, self._box)
        return merged.config, merged.env

    # ------------------------------------------------------------ 읽기

    async def status(
        self, actor: ActorContext, base: ModelsConfig, base_env: Mapping[str, str]
    ) -> dict[str, Any]:
        ws = actor.workspace_id
        async with self._db.workspace_session(ws) as s:
            state = await _read_state(s, ws)
        merged = _merge(base, base_env, state, self._box)
        rows = {r.name: r for r in state.connections}
        connections = []
        for name, ep in merged.config.endpoints.items():
            ui = name in merged.ui_names
            row = rows.get(name) if ui else None
            connections.append(
                {
                    "name": name,
                    "source": "ui" if ui else "file",
                    "preset": ep.preset,
                    "kind": ep.kind,
                    "base_url": ep.base_url,
                    "billing": ep.billing,
                    "price_as": ep.price_as,
                    "data_classes": sorted(ep.data_classes & SELECTABLE_DATA_CLASSES),
                    "key": merged.key_states[name] if ui else _file_key_state(ep, base_env),
                    "key_last4": merged.last4.get(name) if ui else None,
                    "last_test_at": _iso(row.last_test_at) if row else None,
                    "last_test_ok": row.last_test_ok if row else None,
                    "leaves_host": ep.leaves_host,
                }
            )
        connections.sort(key=lambda c: (c["source"] != "file", c["name"]))
        routes = [
            {
                "purpose": purpose,
                "endpoint": route.endpoint,
                "model": route.model,
                "source": merged.route_sources.get(purpose, "file"),
            }
            for purpose, route in sorted(merged.config.routes.items())
        ]
        spent = await month_spend(self._db, ws, self._clock())
        return {
            "connections": connections,
            "routes": routes,
            "purposes": list(KNOWN_PURPOSES),
            "budget": {
                "monthly_usd": _money(merged.config.budget.monthly_usd),
                "source": merged.budget_source,
                "spent_usd_this_month": _money(spent) or "0",
            },
            "presets": [
                {
                    "name": name,
                    "kind": p.kind,
                    "base_url": p.base_url,
                    "billing": p.billing,
                    "needs_key": p.api_key_env is not None,
                }
                for name, p in PRESETS.items()
            ],
        }

    async def effective(
        self, workspace_id: UUID, base: ModelsConfig, base_env: Mapping[str, str]
    ) -> ModelsConfig:
        config, _ = await self.build(workspace_id, base, base_env)
        return config

    # ------------------------------------------------------------쓰기

    async def create(self, actor: ActorContext, base: ModelsConfig, body: ConnectionInput) -> None:
        name = body.name or ""
        if not _NAME.fullmatch(name):
            raise ModelAdminError("invalid_name")
        if name in base.endpoints:
            raise ModelAdminError("connection_name_taken", 409)
        values = _validated(
            preset=body.preset or "openai-compatible",
            base_url=body.base_url,
            billing=body.billing,
            price_as=body.price_as,
            data_classes=body.data_classes,
            token_param=body.token_param,
        )
        key = _checked_key(body.api_key, values["preset"]) if body.api_key is not None else None
        ws = actor.workspace_id
        try:
            async with self._db.workspace_session(ws) as s:
                others = (
                    await s.execute(
                        select(ModelConnection.name).where(ModelConnection.workspace_id == ws)
                    )
                ).scalars()
                if any(key_env_name(o) == key_env_name(name) for o in others):
                    raise ModelAdminError("connection_name_taken", 409)
                row = ModelConnection(
                    workspace_id=ws,
                    name=name,
                    updated_by=actor.subject_id,
                    api_key_last4=last4(key) if key else None,
                    **values,
                )
                s.add(row)
                await s.flush()
                if key:
                    s.add(
                        ModelConnectionSecret(
                            workspace_id=ws, connection_id=row.id, ciphertext=self._box.encrypt(key)
                        )
                    )
                await _bump(s, actor)
        except IntegrityError as exc:
            raise ModelAdminError("connection_name_taken", 409) from exc

    async def update(
        self, actor: ActorContext, base: ModelsConfig, name: str, body: ConnectionInput
    ) -> None:
        ws = actor.workspace_id
        async with self._db.workspace_session(ws) as s:
            row = await s.scalar(
                select(ModelConnection).where(
                    ModelConnection.workspace_id == ws, ModelConnection.name == name
                )
            )
            if row is None:
                if name in base.endpoints:
                    raise ModelAdminError("connection_read_only", 409)  # 파일에서 온 연결
                raise ModelAdminError("connection_not_found", 404)
            f = body.fields
            if "name" in f and body.name != name:
                raise ModelAdminError("name_immutable")
            preset = body.preset if "preset" in f else row.preset
            if preset is None:
                raise ModelAdminError("unknown_preset")
            preset_changed = preset != row.preset
            values = _validated(
                preset=preset,
                base_url=body.base_url if "base_url" in f else row.base_url,
                billing=body.billing
                if "billing" in f
                else (None if preset_changed else row.billing),
                price_as=body.price_as if "price_as" in f else row.price_as,
                data_classes=body.data_classes if "data_classes" in f else row.data_classes,
                token_param=body.token_param
                if "token_param" in f
                else (None if preset_changed else row.token_param),
            )
            for k, v in values.items():
                setattr(row, k, v)
            row.updated_by = actor.subject_id
            if "api_key" in f:
                await s.execute(
                    delete(ModelConnectionSecret).where(
                        ModelConnectionSecret.workspace_id == ws,
                        ModelConnectionSecret.connection_id == row.id,
                    )
                )
                if body.api_key is None:
                    row.api_key_last4 = None
                else:
                    key = _checked_key(body.api_key, preset)
                    row.api_key_last4 = last4(key)
                    s.add(
                        ModelConnectionSecret(
                            workspace_id=ws, connection_id=row.id, ciphertext=self._box.encrypt(key)
                        )
                    )
            elif PRESETS[preset].kind == KIND_LOCAL and row.api_key_last4 is not None:
                raise ModelAdminError("key_not_used")
            await _bump(s, actor)

    async def delete(self, actor: ActorContext, base: ModelsConfig, name: str) -> None:
        ws = actor.workspace_id
        async with self._db.workspace_session(ws) as s:
            row = await s.scalar(
                select(ModelConnection).where(
                    ModelConnection.workspace_id == ws, ModelConnection.name == name
                )
            )
            if row is None:
                code = "connection_read_only" if name in base.endpoints else "connection_not_found"
                raise ModelAdminError(code, 409 if name in base.endpoints else 404)
            used = await s.scalar(
                select(func.count())
                .select_from(ModelRouteOverride)
                .where(ModelRouteOverride.workspace_id == ws, ModelRouteOverride.endpoint == name)
            )
            if used:
                raise ModelAdminError("connection_in_use", 409)
            await s.delete(row)  # 키 암호문은 FK CASCADE로 같이 지워진다
            await _bump(s, actor)

    async def put_routes(
        self,
        actor: ActorContext,
        base: ModelsConfig,
        base_env: Mapping[str, str],
        routes: Mapping[str, str | None],
    ) -> None:
        ws = actor.workspace_id
        if len(routes) > 64:
            raise ModelAdminError("too_many_routes")
        async with self._db.workspace_session(ws) as s:
            state = await _read_state(s, ws)
            endpoints = _merge(base, base_env, state, self._box).config.endpoints
            for purpose, value in routes.items():
                if not isinstance(purpose, str) or not _PURPOSE.fullmatch(purpose):
                    raise ModelAdminError("invalid_purpose")
                if value is None:
                    continue
                if not isinstance(value, str):
                    raise ModelAdminError("invalid_route")
                endpoint, sep, model = value.partition("/")
                if not sep or not endpoint or not model or len(model) > 200:
                    raise ModelAdminError("invalid_route")
                if problem := _route_problem(purpose, endpoints.get(endpoint)):
                    raise ModelAdminError(problem)
            current = {o.purpose: o for o in state.overrides}
            for purpose, value in routes.items():
                row = current.get(purpose)
                if value is None:
                    if row is not None:
                        await s.delete(row)
                    continue
                endpoint, _, model = value.partition("/")
                if row is None:
                    s.add(
                        ModelRouteOverride(
                            workspace_id=ws,
                            purpose=purpose,
                            endpoint=endpoint,
                            model=model,
                            updated_by=actor.subject_id,
                        )
                    )
                else:
                    row.endpoint, row.model = endpoint, model
                    row.updated_by = actor.subject_id
            await _bump(s, actor)

    async def put_budget(self, actor: ActorContext, monthly_usd: Any) -> None:
        amount = _budget(monthly_usd)
        async with self._db.workspace_session(actor.workspace_id) as s:
            await _bump(s, actor, monthly_usd=amount)

    # ------------------------------------------------------------ 시험

    async def test(
        self, actor: ActorContext, base: ModelsConfig, base_env: Mapping[str, str], name: str
    ) -> dict[str, Any]:
        """과금되지 않는 모델 목록(``GET {base_url}/models``, Anthropic ``GET /v1/models``)만 부른다."""
        ws = actor.workspace_id
        async with self._db.workspace_session(ws) as s:
            state = await _read_state(s, ws)
        merged = _merge(base, base_env, state, self._box)
        ep = merged.config.endpoints.get(name)
        if ep is None:
            raise ModelAdminError("connection_not_found", 404)
        key = merged.env.get(ep.api_key_env) if ep.api_key_env else None
        result: dict[str, Any]
        if ep.kind == KIND_LOCAL:  # 이 프로세스 안의 모델: 부를 곳이 없다
            result = {"ok": True, "models": []}
        elif ep.api_key_env and not key:
            result = {"ok": False, "models": [], "error_code": "key_missing"}
        else:
            result = await self._probe(ep, key)
        if name in merged.ui_names:
            row = next(r for r in state.connections if r.name == name)
            async with self._db.workspace_session(ws) as s:
                # updated_at은 그대로 둔다: 시험 결과만으로 게이트웨이 설정을 다시 만들지 않게
                await s.execute(
                    update(ModelConnection)
                    .where(ModelConnection.workspace_id == ws, ModelConnection.id == row.id)
                    .values(
                        last_test_at=self._clock(),
                        last_test_ok=bool(result["ok"]),
                        updated_at=ModelConnection.updated_at,
                    )
                )
        return result

    async def _probe(self, ep: EndpointConfig, key: str | None) -> dict[str, Any]:
        if ep.kind == "anthropic":
            url = (ep.base_url or ANTHROPIC_API_URL).rstrip("/") + "/v1/models"
            headers = {"anthropic-version": ANTHROPIC_VERSION}
            params = {"limit": "1000"}
            if key:
                headers["x-api-key"] = key
        else:
            if not ep.base_url:
                return {"ok": False, "models": [], "error_code": "unreachable"}
            url = ep.base_url.rstrip("/") + "/models"
            headers, params = {}, {}
            if key:
                headers["Authorization"] = f"Bearer {key}"
        headers["User-Agent"] = "career-brain-connection-test"
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout, follow_redirects=False
            ) as client:
                resp = await client.get(url, headers=headers, params=params)
        except (httpx.TimeoutException, httpx.TransportError, httpx.InvalidURL):
            return {"ok": False, "models": [], "error_code": "unreachable"}
        if resp.status_code in (401, 403):
            return {"ok": False, "models": [], "error_code": "unauthorized"}
        if resp.status_code != 200:
            return {"ok": False, "models": [], "error_code": "bad_response"}
        try:
            data = resp.json().get("data")
            ids = sorted({str(m["id"]) for m in data if isinstance(m, dict) and m.get("id")})
        except (ValueError, AttributeError, TypeError, KeyError):
            return {"ok": False, "models": [], "error_code": "bad_response"}
        return {"ok": True, "models": ids[:MAX_MODELS_LISTED]}


async def _bump(s: AsyncSession, actor: ActorContext, **values: Any) -> None:
    """화면 설정 버전 올리기 (worker가 보고 다시 읽는다). 행이 없으면 만든다 (동시에 와도 한 행)."""
    now = datetime.now(UTC)
    stmt = pg_insert(ModelSettings).values(
        id=uuid4(),
        workspace_id=actor.workspace_id,
        version=1,
        updated_by=actor.subject_id,
        created_at=now,
        updated_at=now,
        **values,
    )
    await s.execute(
        stmt.on_conflict_do_update(
            index_elements=[ModelSettings.workspace_id],
            set_={
                "version": ModelSettings.version + 1,
                "updated_by": actor.subject_id,
                "updated_at": now,
                **values,
            },
        )
    )


def _validated(
    *,
    preset: str,
    base_url: str | None,
    billing: str | None,
    price_as: str | None,
    data_classes: Sequence[str] | None,
    token_param: str | None,
) -> dict[str, Any]:
    """``parse_models_config``와 같은 규칙 + 화면 규칙 (restricted 금지, 주소 형식)."""
    p = PRESETS.get(preset) if isinstance(preset, str) else None
    if p is None:
        raise ModelAdminError("unknown_preset")
    if base_url is not None:
        if not isinstance(base_url, str):
            raise ModelAdminError("invalid_base_url")
        base_url = base_url.strip() or None
    if base_url is not None and (p.kind == KIND_LOCAL or not _URL.fullmatch(base_url)):
        raise ModelAdminError("invalid_base_url")
    if p.kind == "openai" and not (base_url or p.base_url):
        raise ModelAdminError("base_url_required")
    billing = billing or p.billing
    if billing not in BILLINGS:
        raise ModelAdminError("invalid_billing")
    if price_as is not None and price_as not in PRICE_PROVIDERS:
        raise ModelAdminError("invalid_price_as")
    token_param = token_param or p.token_param
    if token_param not in TOKEN_PARAMS:
        raise ModelAdminError("invalid_token_param")
    classes = list(data_classes or [])
    if not all(isinstance(c, str) for c in classes):
        raise ModelAdminError("invalid_data_class")
    if "restricted" in classes:
        raise ModelAdminError("restricted_not_allowed")
    if set(classes) - SELECTABLE_DATA_CLASSES:
        raise ModelAdminError("invalid_data_class")
    return {
        "preset": preset,
        "base_url": base_url,
        "billing": billing,
        "price_as": price_as,
        "token_param": token_param,
        "data_classes": sorted(set(classes)),
    }


def _checked_key(value: Any, preset: str) -> str:
    if PRESETS[preset].kind == KIND_LOCAL:
        raise ModelAdminError("key_not_used")
    if not isinstance(value, str):
        raise ModelAdminError("invalid_api_key")
    key = value.strip()
    if not key or len(key) > MAX_KEY_CHARS or any(c.isspace() or not c.isprintable() for c in key):
        raise ModelAdminError("invalid_api_key")
    return key


def _budget(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ModelAdminError("invalid_budget")
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ModelAdminError("invalid_budget") from exc
    if not amount.is_finite() or amount < 0 or amount > 1_000_000:
        raise ModelAdminError("invalid_budget")
    return amount.quantize(Decimal("0.0001"))


__all__ = [
    "KEY_NEEDS_REENTRY",
    "KNOWN_PURPOSES",
    "ConnectionInput",
    "ModelAdminError",
    "ModelConnections",
    "key_env_name",
]

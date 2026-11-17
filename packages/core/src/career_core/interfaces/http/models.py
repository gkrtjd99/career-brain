"""모델 연결·용도별 라우트·월 예산 (O22, plan §21). 개인 앱이 ``CoreDeps.model_connections``를 줄 때만 켜진다.

- 읽기 ``models.read``(read), 바꾸기 ``models.manage``(workspace-admin). MCP 도구로는 열지 않는다.
- 본문은 직접 읽고 검사한다: FastAPI의 422 응답은 잘못된 입력 값을 그대로 돌려주므로, 키가 응답에 실리지
  않게 오류는 코드(``invalid_body`` 등)만 낸다.
- 바꾸는 요청은 모두 바뀐 뒤의 상태(``GET /core/models/status``와 같은 모양)를 돌려준다.

NOTE: `from __future__ import annotations`를 쓰지 않는다 (FastAPI 런타임 타입 해석).
"""

import json
import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from career_core.llm.config import ModelsConfig
from career_core.ports import ActorContext
from career_core.usecases.model_connections import (
    ConnectionInput,
    ModelAdminError,
    ModelConnections,
)

_FIELDS = (
    "name",
    "preset",
    "base_url",
    "api_key",
    "billing",
    "price_as",
    "data_classes",
    "token_param",
)
_STR_FIELDS = frozenset(_FIELDS) - {"data_classes"}
MAX_BODY_BYTES = 16 * 1024


async def _body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="body_too_large")
    try:
        data = json.loads(raw or b"{}")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=422, detail="invalid_body") from None
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="invalid_body")
    return data


def _connection_input(data: dict[str, Any]) -> ConnectionInput:
    unknown = set(data) - set(_FIELDS)
    if unknown:
        raise HTTPException(status_code=422, detail="unknown_field")
    for k in _STR_FIELDS & data.keys():
        if data[k] is not None and not isinstance(data[k], str):
            raise HTTPException(status_code=422, detail=f"invalid_{k}")
    classes = data.get("data_classes")
    if classes is not None and not isinstance(classes, list):
        raise HTTPException(status_code=422, detail="invalid_data_class")
    return ConnectionInput(**{k: data[k] for k in data}, fields=frozenset(data))


def add_model_routes(router: APIRouter, deps, authorize) -> None:
    def admin() -> ModelConnections:
        conns = getattr(deps, "model_connections", None)
        if conns is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_enabled")
        return conns

    def base() -> tuple[ModelsConfig, Any]:
        gw = deps.models
        if gw is None:
            return ModelsConfig(), os.environ
        return gw.config, gw.env

    async def current(actor: ActorContext) -> dict[str, Any]:
        config, env = base()
        return await admin().status(actor, config, env)

    def changed(actor: ActorContext) -> None:
        if deps.models is not None:
            deps.models.invalidate(
                actor.workspace_id
            )  # 이 프로세스는 다음 호출에서 바로 다시 읽는다

    def fail(exc: ModelAdminError) -> HTTPException:
        return HTTPException(status_code=exc.status, detail=exc.code)

    @router.get("/models/status")
    async def models_status(
        actor: Annotated[ActorContext, Depends(authorize("models.read"))],
    ) -> dict[str, Any]:
        return await current(actor)

    @router.post("/models/connections", status_code=201)
    async def create_connection(
        request: Request,
        actor: Annotated[ActorContext, Depends(authorize("models.manage"))],
    ) -> dict[str, Any]:
        conns = admin()
        body = _connection_input(await _body(request))
        try:
            await conns.create(actor, base()[0], body)
        except ModelAdminError as exc:
            raise fail(exc) from None
        changed(actor)
        return await current(actor)

    @router.patch("/models/connections/{name}")
    async def update_connection(
        name: str,
        request: Request,
        actor: Annotated[ActorContext, Depends(authorize("models.manage"))],
    ) -> dict[str, Any]:
        conns = admin()
        body = _connection_input(await _body(request))
        try:
            await conns.update(actor, base()[0], name, body)
        except ModelAdminError as exc:
            raise fail(exc) from None
        changed(actor)
        return await current(actor)

    @router.delete("/models/connections/{name}")
    async def delete_connection(
        name: str,
        actor: Annotated[ActorContext, Depends(authorize("models.manage"))],
    ) -> dict[str, Any]:
        conns = admin()
        try:
            await conns.delete(actor, base()[0], name)
        except ModelAdminError as exc:
            raise fail(exc) from None
        changed(actor)
        return await current(actor)

    @router.post("/models/connections/{name}/test")
    async def test_connection(
        name: str,
        actor: Annotated[ActorContext, Depends(authorize("models.manage"))],
    ) -> JSONResponse:
        conns = admin()
        config, env = base()
        try:
            result = await conns.test(actor, config, env, name)
        except ModelAdminError as exc:
            raise fail(exc) from None
        return JSONResponse(result)

    @router.put("/models/routes")
    async def put_routes(
        request: Request,
        actor: Annotated[ActorContext, Depends(authorize("models.manage"))],
    ) -> dict[str, Any]:
        conns = admin()
        data = await _body(request)
        routes = data.get("routes")
        if set(data) - {"routes"} or not isinstance(routes, dict):
            raise HTTPException(status_code=422, detail="invalid_body")
        config, env = base()
        try:
            await conns.put_routes(actor, config, env, routes)
        except ModelAdminError as exc:
            raise fail(exc) from None
        changed(actor)
        return await current(actor)

    @router.put("/models/budget")
    async def put_budget(
        request: Request,
        actor: Annotated[ActorContext, Depends(authorize("models.manage"))],
    ) -> dict[str, Any]:
        conns = admin()
        data = await _body(request)
        if set(data) != {"monthly_usd"}:
            raise HTTPException(status_code=422, detail="invalid_body")
        try:
            await conns.put_budget(actor, data["monthly_usd"])
        except ModelAdminError as exc:
            raise fail(exc) from None
        changed(actor)
        return await current(actor)

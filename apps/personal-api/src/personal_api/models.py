"""개인 앱의 모델 게이트웨이 조립."""

from __future__ import annotations

import threading

from career_core.adapters.secret_box import SecretBox
from career_core.db import Database
from career_core.llm.config import KIND_LOCAL, EndpointConfig, ModelsConfig, load_models_config
from career_core.llm.gateway import ModelGateway, default_client_factory
from career_core.llm.pricing import PriceBook
from career_core.ports import EmbeddingClient, ModelClient
from career_core.usecases.model_connections import ModelConnections

from personal_api.settings import Settings

_local_clients: dict[tuple[str, str | None], ModelClient | EmbeddingClient] = {}
_local_lock = threading.Lock()


def shared_client_factory(
    endpoint: EndpointConfig, api_key: str | None
) -> ModelClient | EmbeddingClient:
    """로컬 임베딩 모델은 프로세스에 하나만 올린다.

    `serve`는 API와 내장 worker가 각자 게이트웨이를 갖는다. 로컬 모델(수백 MB~GB)을
    둘이 따로 올리지 않도록 공유한다. 로컬 클라이언트는 스레드 안전하고 이벤트 루프에
    묶이지 않는다. HTTP 제공자 클라이언트는 루프에 묶이므로 공유하지 않는다.
    """
    if endpoint.kind != KIND_LOCAL:
        return default_client_factory(endpoint, api_key)
    key = (endpoint.name, endpoint.model_dir)
    with _local_lock:
        client = _local_clients.get(key)
        if client is None:
            client = _local_clients[key] = default_client_factory(endpoint, api_key)
        return client


def load_config(settings: Settings) -> ModelsConfig:
    return load_models_config(settings.models_config)


def load_prices(config: ModelsConfig) -> PriceBook:
    return PriceBook.load(*config.price_files)


def build_connections(settings: Settings, db: Database) -> ModelConnections:
    """O22 화면 설정 (연결·라우트·예산). 키는 CAREER_SECRET_KEY 또는 secret_key_file로 암호화한다."""
    return ModelConnections(db, SecretBox(key_file=settings.secret_key_file))


def build_gateway(
    settings: Settings, db: Database, connections: ModelConnections | None = None
) -> ModelGateway:
    """파일 설정 + 공간별 화면 설정. 화면에서 바꾸면 몇 초 안에(같은 프로세스면 바로) 반영된다."""
    config = load_config(settings)
    return ModelGateway(
        db,
        config,
        load_prices(config),
        client_factory=shared_client_factory,
        overlay=connections or build_connections(settings, db),
    )

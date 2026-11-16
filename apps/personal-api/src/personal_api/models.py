"""개인 앱의 모델 게이트웨이 조립."""

from __future__ import annotations

from career_core.db import Database
from career_core.llm.config import ModelsConfig, load_models_config
from career_core.llm.gateway import ModelGateway
from career_core.llm.pricing import PriceBook

from personal_api.settings import Settings


def load_config(settings: Settings) -> ModelsConfig:
    return load_models_config(settings.models_config)


def load_prices(config: ModelsConfig) -> PriceBook:
    return PriceBook.load(*config.price_files)


def build_gateway(settings: Settings, db: Database) -> ModelGateway:
    config = load_config(settings)
    return ModelGateway(db, config, load_prices(config))

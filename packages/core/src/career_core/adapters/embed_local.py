"""로컬 임베딩 (fastembed, ONNX Runtime). torch 없이 CPU에서 돈다.

설치: `career-core[local-embed]`. 모델 파일은 처음 쓸 때 한 번 받는다 (`model_dir`).
자료가 이 기계 밖으로 나가지 않으므로 게이트웨이가 egress 검사를 하지 않는다.
모델은 처음 호출할 때 올리고, 프로세스가 살아 있는 동안 재사용한다.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Sequence
from typing import Any

from career_core.llm.config import EndpointConfig
from career_core.llm.types import (
    EMBED_QUERY,
    EmbeddingCompletion,
    ModelCallError,
    TokenUsage,
    UsagePart,
)

# 합성 30문항 측정(docs/architecture/search.md): e5-large와 같은 재현율, 메모리는 절반 이하.
DEFAULT_LOCAL_MODEL = "minishlab/potion-multilingual-128M"
IDLE_UNLOAD_SECONDS = 300


def e5_prefix(model: str, text: str, input_type: str) -> str:
    """e5 계열은 학습 때 쓴 접두어를 붙여야 성능이 나온다. fastembed는 붙여 주지 않는다."""
    if "e5" not in model.lower():
        return text
    return ("query: " if input_type == EMBED_QUERY else "passage: ") + text


class LocalEmbeddingClient:
    def __init__(self, endpoint: EndpointConfig, *, threads: int | None = None) -> None:
        self._cache_dir = endpoint.model_dir or os.environ.get("CAREER_MODEL_DIR")
        # 기본 1~2 스레드: 개인 서버에서 검색 색인이 CPU를 다 쓰지 않게 한다.
        self._threads = threads or int(os.environ.get("CAREER_EMBED_THREADS", "2"))
        self._models: dict[str, Any] = {}
        self._lock = threading.Lock()
        # 한동안 안 쓰면 모델을 내려 메모리를 돌려준다 (0이면 계속 들고 있음).
        self._idle = float(os.environ.get("CAREER_EMBED_IDLE_SECONDS", IDLE_UNLOAD_SECONDS))
        self._timer: threading.Timer | None = None

    def unload(self) -> None:
        with self._lock:
            self._models.clear()
        import gc

        gc.collect()

    def _touch(self) -> None:
        if self._idle <= 0:
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._idle, self.unload)
            self._timer.daemon = True
            self._timer.start()

    def _model(self, name: str) -> Any:
        with self._lock:
            model = self._models.get(name)
            if model is None:
                try:
                    from fastembed import TextEmbedding
                except ImportError as exc:
                    raise ModelCallError("local_embedding_not_installed") from exc
                try:
                    model = TextEmbedding(name, cache_dir=self._cache_dir, threads=self._threads)
                except ValueError as exc:
                    raise ModelCallError("model_not_found") from exc
                self._models[name] = model
            return model

    def _run(self, model: str, texts: Sequence[str], input_type: str) -> list[tuple[float, ...]]:
        engine = self._model(model)
        prepared = [e5_prefix(model, t, input_type) for t in texts]
        return [tuple(float(x) for x in vec) for vec in engine.embed(prepared, batch_size=32)]

    async def embed(
        self, model: str, texts: Sequence[str], *, input_type: str, dimensions: int | None
    ) -> EmbeddingCompletion:
        vectors = await asyncio.to_thread(self._run, model, list(texts), input_type)
        self._touch()
        usage = TokenUsage(input_tokens=sum(len(t) for t in texts))  # 무료: 글자 수만 참고로 남긴다
        return EmbeddingCompletion(
            vectors=tuple(vectors), model=model, parts=(UsagePart(model, usage),)
        )

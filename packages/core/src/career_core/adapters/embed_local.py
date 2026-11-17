"""로컬 임베딩 (fastembed, ONNX Runtime). torch 없이 CPU에서 돈다.

설치: `career-core[local-embed]`. 모델 파일은 처음 쓸 때 한 번 받는다 (`model_dir`).
자료가 이 기계 밖으로 나가지 않으므로 게이트웨이가 egress 검사를 하지 않는다.
모델은 처음 호출할 때 올리고, 프로세스가 살아 있는 동안 재사용한다.
같은 엔드포인트가 검색 결과 다시 정렬(`routes.rerank`, fastembed `TextCrossEncoder`)도 맡는다.

어휘 자르기 (potion-multilingual-128M): 다국어 Unigram 토크나이저(어휘 50만 개)가 상주 메모리의 대부분이다.
한국어·영어 자료에 나올 수 없는 토큰(허용 글자 밖의 글자가 든 토큰: 키릴·아랍·가나 문자, 이모지 등)을
빼고 임베딩 행도 같이 잘라 캐시에 한 번 만든다. 허용 글자만으로 된 글은 토큰 분할이 그대로라 벡터가
비트 단위로 같다. 허용 글자 밖의 글자는 모르는 토큰(평균에서 빠짐)이 된다.
`CAREER_EMBED_TRIM=0`이면 원래 모델을 그대로 쓴다.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from career_core.llm.config import EndpointConfig
from career_core.llm.types import (
    EMBED_QUERY,
    EmbeddingCompletion,
    ModelCallError,
    RerankCompletion,
    TokenUsage,
    UsagePart,
)

# 합성 30문항 측정(docs/architecture/search.md): e5-large와 같은 R@5, 어휘를 자르고 mmap으로 열어
# 모델 단독 최대 RSS ~0.95GB → ~0.44GB.
DEFAULT_LOCAL_MODEL = "minishlab/potion-multilingual-128M"
IDLE_UNLOAD_SECONDS = 300

# 어휘를 자를 모델 (정적 model2vec, 다국어 Unigram 어휘). 다른 모델은 그대로 쓴다.
TRIM_MODELS = frozenset({"minishlab/potion-multilingual-128M"})
# 허용 글자 규칙이나 파일 모양을 바꾸면 올린다 (캐시 폴더 이름에 들어가 새로 만든다).
TRIM_VERSION = 1
TRIM_DIR = "career-trimmed"
_STATIC_FILES = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "model.safetensors",
)


def e5_prefix(model: str, text: str, input_type: str) -> str:
    """e5 계열은 학습 때 쓴 접두어를 붙여야 성능이 나온다. fastembed는 붙여 주지 않는다."""
    if "e5" not in model.lower():
        return text
    return ("query: " if input_type == EMBED_QUERY else "passage: ") + text


# ---------------------------------------------------------------- 어휘 자르기


def keep_char(ch: str) -> bool:
    """남길 토큰의 글자: ASCII, 라틴 확장(é, ü), 한글(음절·자모), 한자(CJK 통합),
    일반 문장부호·CJK 문장부호·전각 문자, 화살표·기호·원문자·도형, 낱말 경계 표시(▁)."""
    o = ord(ch)
    return (
        o < 0x250  # ASCII + 라틴-1 + 라틴 확장 A·B
        or 0x1100 <= o <= 0x11FF  # 한글 자모
        or 0x2000 <= o <= 0x206F  # 일반 문장부호 (“ ” • … ※)
        or 0x2100 <= o <= 0x27BF  # 글자 모양 기호·화살표·수학·도형·딩뱃 (→ ★ ✓)
        or 0x2460 <= o <= 0x24FF  # 원문자 (①)
        or 0x3000 <= o <= 0x303F  # CJK 문장부호 (「 」)
        or 0x3130 <= o <= 0x318F  # 한글 호환 자모
        or 0x4E00 <= o <= 0x9FFF  # 한자
        or 0xAC00 <= o <= 0xD7A3  # 한글 음절
        or 0xFF00 <= o <= 0xFFEF  # 전각·반각
        or ch == "▁"
    )


def _read_safetensors_header(path: Path) -> tuple[int, dict[str, Any]]:
    with open(path, "rb") as f:
        size = struct.unpack("<Q", f.read(8))[0]
        return size, json.loads(f.read(size))


def trim_static_model(src: Path, dst: Path) -> int:
    """`src`(model2vec 정적 모델 폴더)에서 허용 글자 토큰만 남긴 모델을 `dst`에 만든다. 남긴 토큰 수.

    결정적이다: 같은 원본이면 같은 파일이 나온다. 토큰 순서·점수는 그대로 두고 번호만 앞으로 당긴다
    (Unigram 분할은 점수로만 정해지므로 허용 글자만으로 된 글의 분할이 바뀌지 않는다).
    임시 폴더에 다 쓴 뒤 이름을 바꿔 반쯤 쓴 캐시가 남지 않게 한다.
    """
    import numpy as np

    tok = json.loads((src / "tokenizer.json").read_text("utf-8"))
    model = tok["model"]
    if model.get("type") != "Unigram":
        raise ValueError("only Unigram static tokenizers are trimmed")
    vocab = model["vocab"]
    special = {a["id"] for a in tok.get("added_tokens", [])} | {model.get("unk_id")}
    keep = [i for i, (piece, _) in enumerate(vocab) if i in special or all(map(keep_char, piece))]
    remap = {old: new for new, old in enumerate(keep)}
    model["vocab"] = [vocab[i] for i in keep]
    if model.get("unk_id") is not None:
        model["unk_id"] = remap[model["unk_id"]]
    for added in tok.get("added_tokens", []):
        added["id"] = remap[added["id"]]
    rows_in_vocab = len(vocab)
    del vocab, remap

    header_size, header = _read_safetensors_header(src / "model.safetensors")
    meta = header["embeddings"]
    if meta["dtype"] != "F32" or set(header) - {"embeddings", "__metadata__"}:
        raise ValueError("unexpected static model tensors")
    rows, dim = meta["shape"]
    if rows != rows_in_vocab:
        raise ValueError("vocabulary and embedding rows differ")

    tmp = dst.with_name(dst.name + f".tmp{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    (tmp / "tokenizer.json").write_text(json.dumps(tok, ensure_ascii=False), "utf-8")
    del tok, model  # 행렬을 옮기기 전에 파이썬 객체(수백 MB)를 놓는다
    nbytes = len(keep) * dim * 4
    new_header = json.dumps(
        {"embeddings": {"dtype": "F32", "shape": [len(keep), dim], "data_offsets": [0, nbytes]}}
    ).encode()
    new_header += b" " * (-len(new_header) % 8)
    index = np.asarray(keep, dtype=np.int64)
    chunk = (
        65_536  # 원본 행을 차례로 64MB씩 읽어 남길 행만 쓴다 (mmap 대신: 읽은 쪽이 RSS에 남지 않게)
    )
    with open(src / "model.safetensors", "rb") as fin, open(tmp / "model.safetensors", "wb") as f:
        f.write(struct.pack("<Q", len(new_header)))
        f.write(new_header)
        fin.seek(8 + header_size + meta["data_offsets"][0])
        for start in range(0, rows, chunk):
            n = min(chunk, rows - start)
            block = np.frombuffer(fin.read(n * dim * 4), dtype="<f4").reshape(n, dim)
            lo, hi = np.searchsorted(index, [start, start + n])
            f.write(block[index[lo:hi] - start].tobytes())
    for name in ("config.json", "tokenizer_config.json", "special_tokens_map.json"):
        if (src / name).exists():
            shutil.copyfile(src / name, tmp / name)
    (tmp / "trim.json").write_text(
        json.dumps({"version": TRIM_VERSION, "source": str(src), "kept": len(keep), "of": rows}),
        "utf-8",
    )
    try:
        tmp.rename(dst)
    except OSError:
        # 다른 프로세스가 먼저 만들었다 (같은 결과): 내 것은 버린다
        shutil.rmtree(tmp, ignore_errors=True)
        if not (dst / "trim.json").exists():
            raise
    return len(keep)


def _source_dir(name: str, cache_dir: str | None) -> Path:
    """fastembed가 받은 원본 폴더 (없으면 fastembed로 받는다)."""
    from fastembed import TextEmbedding
    from fastembed.common.utils import define_cache_dir
    from huggingface_hub import snapshot_download

    cache = str(define_cache_dir(cache_dir))
    try:
        path = snapshot_download(
            repo_id=name, cache_dir=cache, local_files_only=True, allow_patterns=list(_STATIC_FILES)
        )
    except OSError:  # 아직 받지 않음 (LocalEntryNotFoundError는 OSError)
        TextEmbedding(name, cache_dir=cache, lazy_load=True)  # 내려받기만 한다
        path = snapshot_download(
            repo_id=name, cache_dir=cache, local_files_only=True, allow_patterns=list(_STATIC_FILES)
        )
    src = Path(path)
    if not all((src / f).exists() for f in ("tokenizer.json", "model.safetensors")):
        raise ModelCallError("model_not_found")
    return src


def trimmed_model_dir(name: str, cache_dir: str | None) -> Path:
    """잘라낸 모델 폴더. 없으면 만든다.

    만드는 일(원본 tokenizer.json 18MB를 파이썬 객체로 읽기)은 순간 메모리가 수백 MB라
    별도 프로세스에서 한다. 이 프로세스의 메모리 바닥이 올라간 채 남지 않게 하기 위해서다.
    """
    from fastembed.common.utils import define_cache_dir

    src = _source_dir(name, cache_dir)
    # 원본 폴더 이름(허브 revision)이 바뀌면 새로 만든다
    slug = name.replace("/", "--")
    dst = Path(define_cache_dir(cache_dir)) / TRIM_DIR / f"{slug}-{src.name[:12]}-v{TRIM_VERSION}"
    if (dst / "trim.json").exists():
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", __spec__.name if __spec__ else __name__, "trim", str(src), str(dst)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    if not (dst / "trim.json").exists():
        raise ModelCallError("model_trim_failed")
    return dst


def _trim_enabled(name: str) -> bool:
    return name in TRIM_MODELS and os.environ.get("CAREER_EMBED_TRIM", "1") != "0"


# ---------------------------------------------------------------- 클라이언트


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
                kwargs: dict[str, Any] = {}
                try:
                    if _trim_enabled(name):
                        # 잘라낸 어휘·행으로 연다 (한국어·영어 글의 벡터는 원래 모델과 같다)
                        kwargs["specific_model_path"] = str(
                            trimmed_model_dir(name, self._cache_dir)
                        )
                    # 정적(model2vec) 모델은 임베딩 행렬을 mmap으로 연다: 벡터는 같고 쓰는 행만 메모리에
                    # 올라간다. ONNX 모델은 이 인자를 무시한다 (fastembed 0.9).
                    model = TextEmbedding(
                        name, cache_dir=self._cache_dir, threads=self._threads, mmap=True, **kwargs
                    )
                except (ValueError, subprocess.CalledProcessError) as exc:
                    raise ModelCallError("model_not_found") from exc
                self._models[name] = model
            return model

    def _cross_encoder(self, name: str) -> Any:
        """검색 결과 다시 정렬용 cross-encoder (`routes.rerank`). 임베딩 모델과 같은 캐시·내리기를 쓴다."""
        key = "rerank:" + name
        with self._lock:
            model = self._models.get(key)
            if model is None:
                try:
                    from fastembed.rerank.cross_encoder import TextCrossEncoder
                except ImportError as exc:
                    raise ModelCallError("local_rerank_not_installed") from exc
                try:
                    model = TextCrossEncoder(name, cache_dir=self._cache_dir, threads=self._threads)
                except ValueError as exc:
                    raise ModelCallError("model_not_found") from exc
                self._models[key] = model
            return model

    def _rerank(self, model: str, query: str, documents: Sequence[str]) -> tuple[float, ...]:
        engine = self._cross_encoder(model)
        return tuple(float(x) for x in engine.rerank(query, list(documents), batch_size=32))

    async def rerank(self, model: str, query: str, documents: Sequence[str]) -> RerankCompletion:
        scores = await asyncio.to_thread(self._rerank, model, query, list(documents))
        self._touch()
        usage = TokenUsage(input_tokens=len(query) * len(documents) + sum(map(len, documents)))
        return RerankCompletion(scores=scores, model=model, parts=(UsagePart(model, usage),))

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


def _main(argv: Sequence[str]) -> None:
    # 내부용: python -m career_core.adapters.embed_local trim <원본 폴더> <대상 폴더>
    if len(argv) != 3 or argv[0] != "trim":
        raise SystemExit("usage: python -m career_core.adapters.embed_local trim <src> <dst>")
    kept = trim_static_model(Path(argv[1]), Path(argv[2]))
    print(f"kept {kept} tokens")


if __name__ == "__main__":
    _main(sys.argv[1:])

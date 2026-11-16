"""로컬 임베딩 어휘 자르기: 합성 Unigram 정적 모델로 확인한다 (모델을 받지 않는다).

- 허용 글자(한글·영문)만으로 된 글은 자르기 전과 벡터가 같다.
- 허용 밖 글자(키릴 문자)가 든 토큰은 빠지고, 같은 원본이면 같은 파일이 나온다.
"""

import json
import struct

import numpy as np
import pytest

pytest.importorskip("fastembed")
from career_core.adapters.embed_local import keep_char, trim_static_model
from fastembed.text.static_embedding import StaticTextEmbedding

PIECES = ["▁", "▁파이썬", "으로", "▁서버", "를", "▁Python", "▁server", "▁при", "вет", "s", "e", "r"]


def _write_model(path, rng):
    path.mkdir()
    vocab = [["[PAD]", 0.0], ["[UNK]", 0.0]] + [[p, -float(i + 1)] for i, p in enumerate(PIECES)]
    tok = {
        "version": "1.0",
        "truncation": None,
        "padding": None,
        "added_tokens": [
            {
                "id": 0,
                "content": "[PAD]",
                "single_word": False,
                "lstrip": False,
                "rstrip": False,
                "normalized": False,
                "special": True,
            },
            {
                "id": 1,
                "content": "[UNK]",
                "single_word": False,
                "lstrip": False,
                "rstrip": False,
                "normalized": False,
                "special": True,
            },
        ],
        "normalizer": None,
        "pre_tokenizer": {
            "type": "Metaspace",
            "replacement": "▁",
            "prepend_scheme": "always",
            "split": False,
        },
        "post_processor": None,
        "decoder": None,
        "model": {"type": "Unigram", "unk_id": 1, "vocab": vocab, "byte_fallback": False},
    }
    (path / "tokenizer.json").write_text(json.dumps(tok, ensure_ascii=False), "utf-8")
    matrix = rng.standard_normal((len(vocab), 8)).astype("<f4")
    header = json.dumps(
        {
            "embeddings": {
                "dtype": "F32",
                "shape": list(matrix.shape),
                "data_offsets": [0, matrix.nbytes],
            }
        }
    ).encode()
    header += b" " * (-len(header) % 8)
    with open(path / "model.safetensors", "wb") as f:
        f.write(struct.pack("<Q", len(header)) + header + matrix.tobytes())
    (path / "config.json").write_text("{}")


def _embed(model_dir, texts):
    m = StaticTextEmbedding(
        "minishlab/potion-multilingual-128M", specific_model_path=str(model_dir), mmap=True
    )
    return np.array(list(m.embed(texts)))


def test_trim_keeps_korean_english_vectors(tmp_path):
    src, dst, again = tmp_path / "src", tmp_path / "dst", tmp_path / "again"
    _write_model(src, np.random.default_rng(7))
    kept = trim_static_model(src, dst)
    assert kept == 2 + len(PIECES) - 2  # "▁при", "вет" 제외
    vocab = [
        p for p, _ in json.loads((dst / "tokenizer.json").read_text("utf-8"))["model"]["vocab"]
    ]
    assert "▁при" not in vocab and "▁파이썬" in vocab and vocab[:2] == ["[PAD]", "[UNK]"]
    texts = ["파이썬으로 서버를", "Python server", "servers"]
    assert np.array_equal(_embed(src, texts), _embed(dst, texts))
    # 허용 밖 글자는 모르는 토큰이 되어 평균에서 빠진다
    assert not np.allclose(_embed(src, ["Python привет"]), _embed(dst, ["Python привет"]))
    trim_static_model(src, again)
    for name in ("tokenizer.json", "model.safetensors"):
        assert (dst / name).read_bytes() == (again / name).read_bytes()


def test_keep_char():
    assert all(map(keep_char, "가A1é漢→①▁「"))
    assert not any(map(keep_char, "пあ😀ا"))

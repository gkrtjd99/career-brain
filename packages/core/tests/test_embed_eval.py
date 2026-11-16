"""임베딩 비교 평가(`evals.embed_eval`): 합성 자료가 맞게 짜였는지와 순위 계산."""

import pytest
from career_core.evals import embed_eval
from career_core.llm.types import EmbeddingCompletion, TokenUsage, UsagePart


def test_fixture_is_consistent():
    fx = embed_eval.load_fixture()
    ids = set(fx["passages"])
    assert len(ids) == 40
    assert all(q["expect"] in ids for q in fx["queries"])
    assert {q["type"] for q in fx["queries"]} == set(embed_eval.TYPES)


def test_rank_orders_by_cosine():
    vecs = {"a": (1.0, 0.0), "b": (0.6, 0.8), "c": (0.0, 1.0)}
    assert embed_eval.rank((0.0, 2.0), vecs, "c") == 1
    assert embed_eval.rank((0.0, 2.0), vecs, "a") == 3


@pytest.mark.anyio
async def test_evaluate_spec_with_scripted_client(monkeypatch, tmp_path):
    fx = embed_eval.load_fixture()
    order = list(fx["passages"])
    answer = {q["q"]: q["expect"] for q in fx["queries"]}

    class Scripted:
        """정답 글과 같은 축의 벡터를 준다 → 모두 1위여야 한다."""

        async def embed(self, model, texts, *, input_type, dimensions):
            def vec(t):
                pid = answer.get(t) or next(p for p in order if fx["passages"][p] == t)
                return tuple(1.0 if p == pid else 0.0 for p in order)

            usage = TokenUsage(input_tokens=len(texts))
            return EmbeddingCompletion(
                vectors=tuple(vec(t) for t in texts), model=model, parts=(UsagePart(model, usage),)
            )

    monkeypatch.setattr(embed_eval, "default_client_factory", lambda ep, key: Scripted())
    models = tmp_path / "models.toml"
    models.write_text('[endpoints.local]\npreset = "local"\n', encoding="utf-8")
    res = await embed_eval.evaluate_spec(str(models), "local/fake:64", fx)
    assert res.scores["all"].hit1 == len(fx["queries"])
    assert res.input_tokens == len(fx["passages"]) + len(fx["queries"])

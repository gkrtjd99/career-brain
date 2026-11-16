"""임베딩만 비교하는 평가: 키워드 검색 없이 벡터 순위로 정답 글을 몇 위에 찾는지 잰다.

`search_eval`은 원문 낱말이 든 질문이 많아 키워드 검색이 대부분을 찾아 주므로 임베딩 차이가
잘 안 보인다. 여기서는 질문과 정답 글의 낱말을 되도록 겹치지 않게 했고(바꿔 말하기, 한↔영,
약어·한글 표기), 같은 기술을 다른 일에 쓴 글을 섞어 두었다. DB를 쓰지 않는다. 합성 자료만.

    uv run python -m career_core.evals.embed_eval --models models.toml \\
        --spec openai/text-embedding-3-small:512 --spec openai/text-embedding-3-small:1024

`--spec`은 `<엔드포인트>/<모델>[:차원]`. 엔드포인트 설정·키 환경변수 이름은 `--models` 파일에서
읽는다(키 값은 출력하지 않는다). API 임베딩은 과금된다: 글 40개 + 질문 52개, 실행당 수천 토큰.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

from career_core.llm.config import load_models_config, parse_route
from career_core.llm.gateway import default_client_factory

TYPES = ("ko_para", "en_to_ko", "ko_to_en", "mixed")


def load_fixture() -> dict[str, Any]:
    text = resources.files("career_core.evals").joinpath("embed_eval.json").read_text("utf-8")
    return json.loads(text)


@dataclass
class Score:
    hit1: int = 0
    hit5: int = 0
    rr: float = 0.0
    total: int = 0

    def add(self, rank: int) -> None:
        self.total += 1
        self.hit1 += rank == 1
        self.hit5 += rank <= 5
        self.rr += 1 / rank

    def __str__(self) -> str:
        mrr = self.rr / self.total if self.total else 0.0
        return (
            f"R@1 {self.hit1:>2}/{self.total:<2} R@5 {self.hit5:>2}/{self.total:<2} MRR {mrr:.3f}"
        )


@dataclass
class Result:
    spec: str
    scores: dict[str, Score] = field(default_factory=dict)
    input_tokens: int = 0


def _norm(v: tuple[float, ...]) -> tuple[float, ...]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return tuple(x / n for x in v)


def rank(query_vec, passage_vecs: dict[str, tuple[float, ...]], expect: str) -> int:
    q = _norm(query_vec)
    sims = {
        pid: sum(a * b for a, b in zip(q, _norm(v), strict=True)) for pid, v in passage_vecs.items()
    }
    ordered = sorted(sims, key=lambda pid: (-sims[pid], pid))
    return ordered.index(expect) + 1


async def evaluate_spec(models_path: str | None, spec: str, fx: dict[str, Any]) -> Result:
    route_text, _, dims_text = spec.partition(":")
    route = parse_route(route_text)
    config = load_models_config(models_path)
    endpoint = config.endpoints.get(route.endpoint)
    if endpoint is None:
        raise SystemExit(f"endpoint {route.endpoint!r} is not in the models file")
    key = os.environ.get(endpoint.api_key_env) if endpoint.api_key_env else None
    if endpoint.api_key_env and not key:
        raise SystemExit(f"{endpoint.api_key_env} is not set")
    client = default_client_factory(endpoint, key)
    dims = int(dims_text) if dims_text else None

    ids = list(fx["passages"])
    res = Result(spec)
    done = await client.embed(
        route.model, [fx["passages"][i] for i in ids], input_type="passage", dimensions=dims
    )
    asked = await client.embed(
        route.model, [q["q"] for q in fx["queries"]], input_type="query", dimensions=dims
    )
    for comp in (done, asked):
        res.input_tokens += sum(p.usage.input_tokens for p in comp.parts)
    vecs = dict(zip(ids, done.vectors, strict=True))
    res.scores = {t: Score() for t in (*TYPES, "all")}
    for q, qv in zip(fx["queries"], asked.vectors, strict=True):
        r = rank(qv, vecs, q["expect"])
        res.scores[q["type"]].add(r)
        res.scores["all"].add(r)
    return res


async def _main(models_path: str | None, specs: list[str]) -> None:
    fx = load_fixture()
    counts = {t: sum(q["type"] == t for q in fx["queries"]) for t in TYPES}
    print(f"passages {len(fx['passages'])}, queries {len(fx['queries'])} {counts}")
    for spec in specs:
        res = await evaluate_spec(models_path, spec, fx)
        print(f"\n{spec}  (input tokens {res.input_tokens})")
        for kind, score in res.scores.items():
            print(f"  {kind:9} {score}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", default="models.toml")
    parser.add_argument("--spec", action="append", required=True)
    args = parser.parse_args()
    asyncio.run(_main(args.models, args.spec))


if __name__ == "__main__":
    main()

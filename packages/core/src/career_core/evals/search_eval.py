"""검색 평가: 합성 자료 30문항으로 키워드·의미·결합 검색의 재현율을 잰다.

사용자 DB를 건드리지 않는다. 임시 DB를 만들어 시드하고 지운다.

    uv run python -m career_core.evals.search_eval --models models.toml

설정에 `routes.rerank`가 있으면 다시 정렬한 결과도 따로 잰다 (`keyword+rr`, `hybrid+rr`).

(임시 DB를 만들 수 있는 관리자 접속: `CAREER_TEST_ADMIN_URL`, 기본은 로컬 docker compose)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from dataclasses import dataclass, field, replace
from importlib import resources
from pathlib import Path
from typing import Any

from sqlalchemy import select

from career_core.adapters.blobstore import LocalBlobStore
from career_core.adapters.parsers import default_local_parsers
from career_core.db import Database, DocumentBlock
from career_core.domain.workspace import Role, scopes_for_role
from career_core.jobs.worker import index_handlers, parsing_handlers, run_until_idle
from career_core.llm.gateway import ModelGateway
from career_core.policies import ProviderEgressPolicy
from career_core.ports import ActorContext
from career_core.usecases import brain, interviews
from career_core.usecases.extraction import find_quote
from career_core.usecases.indexing import IndexDeps
from career_core.usecases.parsing import ParsingDeps
from career_core.usecases.search import search
from career_core.usecases.sources import upload_source
from career_core.usecases.workspace_settings import update_settings
from career_core.usecases.workspaces import create_workspace


def load_fixture() -> dict[str, Any]:
    return json.loads(resources.files(__package__).joinpath("search_eval.json").read_text("utf-8"))


async def seed(db: Database, blobs: LocalBlobStore, actor: ActorContext, fx: dict) -> None:
    if fx.get("workspace_aliases"):
        await update_settings(db, actor, tech_aliases=fx["workspace_aliases"])
    for src in fx["sources"]:
        await upload_source(
            db,
            blobs,
            actor,
            filename=src["filename"],
            data="\n\n".join(src["blocks"]).encode(),
            declared_mime="text/plain",
            max_bytes=1 << 20,
            data_class=src["data_class"],
            title=src["title"],
        )
    deps = ParsingDeps(
        db=db, blobs=blobs, egress=ProviderEgressPolicy(), local_parsers=default_local_parsers()
    )
    await run_until_idle(db, parsing_handlers(deps))
    async with db.workspace_session(actor.workspace_id) as s:
        blocks = (await s.execute(select(DocumentBlock))).scalars().all()
    for exp in fx["experiences"]:
        exp_id = await brain.create_experience(
            db,
            actor,
            title=exp["title"],
            organization=exp.get("organization"),
            role=exp.get("role"),
            period_text=exp.get("period"),
        )
        for c in exp["claims"]:
            spans = []
            for b in blocks:
                pos = find_quote(b.text, c["quote"])
                if pos:
                    spans.append(await brain.create_span(db, actor, b.id, *pos))
                    break
            await brain.add_claim(
                db,
                actor,
                exp_id,
                statement=c["statement"],
                kind=c["kind"],
                contribution=c["contribution"],
                span_ids=spans,
            )
        for item in exp.get("interview", []):
            sid = await interviews.start_interview(db, actor, exp_id)
            view = await interviews.get_interview(db, actor, sid)
            q = next(q for q in view.questions if q.focus == item["focus"])
            await interviews.answer_question(
                db, actor, q.id, answer=item["answer"], kind="difficulty", contribution="personal"
            )


@dataclass
class Score:
    hit1: int = 0
    hit5: int = 0
    total: int = 0
    rr: float = 0.0  # MRR@10의 합 (10위 밖은 0)
    seconds: list[float] = field(default_factory=list)  # 질문마다 검색 시간

    def add(self, rank: int | None, seconds: float | None = None) -> None:
        self.total += 1
        self.hit1 += rank == 1
        self.hit5 += rank is not None and rank <= 5
        self.rr += 1 / rank if rank is not None and rank <= 10 else 0.0
        if seconds is not None:
            self.seconds.append(seconds)

    def __str__(self) -> str:
        mrr = self.rr / self.total if self.total else 0.0
        out = f"recall@1 {self.hit1}/{self.total}  recall@5 {self.hit5}/{self.total}  MRR {mrr:.3f}"
        if self.seconds:
            ms = sorted(x * 1000 for x in self.seconds)
            p95 = ms[min(len(ms) - 1, int(len(ms) * 0.95))]
            out += f"  p50 {ms[len(ms) // 2]:.0f}ms p95 {p95:.0f}ms"
        return out


def rank_of(hits, expect: str) -> int | None:
    for i, h in enumerate(hits, start=1):
        if expect in h.snippet:
            return i
    return None


PRIVATE = frozenset({"private"})
Mode = tuple[ModelGateway | None, bool]  # (게이트웨이, 다시 정렬)


async def _timed(db, actor, q: str, gw, rerank: bool, **kw):
    started = time.perf_counter()
    result = await search(db, actor, q, gateway=gw, limit=10, rerank=rerank, **kw)
    if rerank and not result.reranked and result.rerank_note not in (None, "single_word"):
        raise SystemExit(f"rerank failed: {result.rerank_note}")
    return result, time.perf_counter() - started


async def evaluate(
    db: Database,
    actor: ActorContext,
    fx: dict,
    gateway: ModelGateway | None,
    *,
    modes: dict[str, Mode] | None = None,
) -> dict[str, dict[str, Score]]:
    """{"keyword"|"hybrid"|…: {"keyword"|"paraphrase"|"all"|"alias"|"alias_off": Score}}

    `all`은 30문항(keyword + paraphrase)만 센다. `alias`는 별칭 질문(`alias_queries`, 공간 별칭 사전으로
    넓힘), `alias_off`는 같은 질문을 넓히지 않고 (비교용). `modes`를 주지 않으면 키워드만과 결합(게이트웨이).
    """
    out: dict[str, dict[str, Score]] = {}
    if modes is None:
        modes = {"keyword": (None, False)}
        if gateway:
            modes["hybrid"] = (gateway, False)
    for mode, (gw, rerank) in modes.items():
        scores = {
            "keyword": Score(),
            "paraphrase": Score(),
            "all": Score(),
            "alias": Score(),
            "alias_off": Score(),
        }
        for q in fx["queries"]:
            result, took = await _timed(db, actor, q["q"], gw, rerank)
            r = rank_of(result.hits, q["expect"])
            scores[q["type"]].add(r)
            scores["all"].add(r, took)
        for q in fx.get("alias_queries", []):
            for key, expand in (("alias", True), ("alias_off", False)):
                result, _ = await _timed(db, actor, q["q"], gw, rerank, expand_aliases=expand)
                scores[key].add(rank_of(result.hits, q["expect"]))
        out[mode] = scores
    return out


async def _main(models_path: str | None) -> None:
    from career_core.llm.config import load_models_config
    from career_core.llm.pricing import PriceBook
    from career_core.testing import migrated_database

    config = load_models_config(models_path)
    with migrated_database() as urls, tempfile.TemporaryDirectory() as tmp:
        db = Database(urls.app)
        try:
            ws, subject = await create_workspace(db, name="eval", owner_display_name="eval")
            actor = ActorContext(subject, ws, scopes_for_role(Role.OWNER))
            prices = PriceBook.load(*config.price_files)
            gateway = ModelGateway(db, config, prices) if config.embedding_route() else None
            modes: dict[str, Mode] = {"keyword": (None, False)}
            if config.rerank_route():
                # 키워드 + 다시 정렬: 임베딩 라우트를 뺀 설정
                no_embed = {k: v for k, v in config.routes.items() if k != "embed"}
                keyword_rr = ModelGateway(db, replace(config, routes=no_embed), prices)
                modes["keyword+rr"] = (keyword_rr, True)
            if gateway:
                modes["hybrid"] = (gateway, False)
                if config.rerank_route():
                    modes["hybrid+rr"] = (gateway, True)
            await seed(db, LocalBlobStore(Path(tmp)), actor, load_fixture())
            if gateway:
                await run_until_idle(db, index_handlers(IndexDeps(db, gateway)))
                print("embedding space:", gateway.embedding_space())
            if route := config.rerank_route():
                print(f"rerank: {route.endpoint}/{route.model} top_n {config.rerank.top_n}")
                # 모델을 먼저 올린다 (첫 질문의 시간에 올리는 시간이 섞이지 않게)
                await keyword_rr.rerank(actor, "warm up", ["a", "b"], data_classes=PRIVATE)
            fx = load_fixture()
            for mode, scores in (await evaluate(db, actor, fx, gateway, modes=modes)).items():
                for kind, score in scores.items():
                    print(f"{mode:10} {kind:10} {score}")
        finally:
            await db.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", default="models.toml")
    args = parser.parse_args()
    asyncio.run(_main(args.models))


if __name__ == "__main__":
    main()

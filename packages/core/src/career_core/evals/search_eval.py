"""검색 평가: 합성 자료 30문항으로 키워드·의미·결합 검색의 재현율을 잰다.

사용자 DB를 건드리지 않는다. 임시 DB를 만들어 시드하고 지운다.

    uv run python -m career_core.evals.search_eval --models models.toml

(임시 DB를 만들 수 있는 관리자 접속: `CAREER_TEST_ADMIN_URL`, 기본은 로컬 docker compose)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from dataclasses import dataclass
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
from career_core.usecases.workspaces import create_workspace


def load_fixture() -> dict[str, Any]:
    return json.loads(resources.files(__package__).joinpath("search_eval.json").read_text("utf-8"))


async def seed(db: Database, blobs: LocalBlobStore, actor: ActorContext, fx: dict) -> None:
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

    def add(self, rank: int | None) -> None:
        self.total += 1
        self.hit1 += rank == 1
        self.hit5 += rank is not None and rank <= 5

    def __str__(self) -> str:
        return f"recall@1 {self.hit1}/{self.total}  recall@5 {self.hit5}/{self.total}"


def rank_of(hits, expect: str) -> int | None:
    for i, h in enumerate(hits, start=1):
        if expect in h.snippet:
            return i
    return None


async def evaluate(
    db: Database, actor: ActorContext, fx: dict, gateway: ModelGateway | None
) -> dict[str, dict[str, Score]]:
    """{"keyword"|"hybrid": {"keyword"|"paraphrase"|"all": Score}}"""
    out: dict[str, dict[str, Score]] = {}
    modes = {"keyword": None, "hybrid": gateway} if gateway else {"keyword": None}
    for mode, gw in modes.items():
        scores = {"keyword": Score(), "paraphrase": Score(), "all": Score()}
        for q in fx["queries"]:
            result = await search(db, actor, q["q"], gateway=gw, limit=5)
            r = rank_of(result.hits, q["expect"])
            scores[q["type"]].add(r)
            scores["all"].add(r)
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
            gateway = (
                ModelGateway(db, config, PriceBook.load(*config.price_files))
                if config.embedding_route()
                else None
            )
            await seed(db, LocalBlobStore(Path(tmp)), actor, load_fixture())
            if gateway:
                await run_until_idle(db, index_handlers(IndexDeps(db, gateway)))
                print("embedding space:", gateway.embedding_space())
            for mode, scores in (await evaluate(db, actor, load_fixture(), gateway)).items():
                for kind, score in scores.items():
                    print(f"{mode:8} {kind:10} {score}")
        finally:
            await db.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", default="models.toml")
    args = parser.parse_args()
    asyncio.run(_main(args.models))


if __name__ == "__main__":
    main()

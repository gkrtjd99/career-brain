"""한국어 내용어 평가: 규칙 기반(기본)과 형태소 분석기(선택 설치 `korean`, kiwipiepy) 비교.

DB를 쓰지 않는다. 합성 세트 `korean_eval.json`:

- terms: 조사·어미·복합 명사 낱말에서 뽑은 내용어 ↔ gold (정밀도·재현율·F1, 완전 일치 수)
- spacing: 띄어쓰기만 다른 두 표기가 같은 내용어 집합이 되는가
- pairs: 공고 요건 ↔ 문서 문장의 단어 일치 판정 (요건 가시성 기준선과 같은 규칙, `matched_terms`)
- roles: 수정안이 원문에 없는 역할 표현(주도·총괄·이끌다…)을 넣었는가 (사실 변경 검사 `lead_terms`)
- sentences / sentence_pairs: 공고·이력서 문체의 문장 (2026-11-17 추가, 역시 합성). 낱말 세트와 따로 센다.

    uv run python -m career_core.evals.korean_eval            # 설치된 분석기 모두
    uv run python -m career_core.evals.korean_eval --json     # 기계용 출력
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from importlib import resources
from typing import Any

from career_core.domain.documents import (
    KO_STOPWORDS,
    key_terms,
    korean_terms,
    lead_terms,
    matched_terms,
)
from career_core.domain.korean import RuleAnalyzer, use_analyzer


def load_fixture() -> dict[str, Any]:
    return json.loads(resources.files(__package__).joinpath("korean_eval.json").read_text("utf-8"))


def _words(text: str) -> set[str]:
    return {w for w in korean_terms(text) if w not in KO_STOPWORDS}


def _score_terms(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_type: dict[str, dict[str, Any]] = {}
    for case in cases:
        got = _words(case["text"])
        gold = set(case["gold"])
        t = by_type.setdefault(case["type"], {"tp": 0, "fp": 0, "fn": 0, "exact": 0, "n": 0})
        t["tp"] += len(got & gold)
        t["fp"] += len(got - gold)
        t["fn"] += len(gold - got)
        t["exact"] += got == gold
        t["n"] += 1
    total = {k: sum(t[k] for t in by_type.values()) for k in ("tp", "fp", "fn", "exact", "n")}
    by_type["all"] = total
    for t in by_type.values():
        p = t["tp"] / (t["tp"] + t["fp"]) if t["tp"] + t["fp"] else 1.0
        r = t["tp"] / (t["tp"] + t["fn"]) if t["tp"] + t["fn"] else 1.0
        t["precision"], t["recall"] = round(p, 3), round(r, 3)
        t["f1"] = round(2 * p * r / (p + r), 3) if p + r else 0.0
    return by_type


def _score_pairs(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """요건 가시성과 같은 판정 (`matched_terms`: 규칙이면 부분 문자열, 분석기면 내용어 집합)."""
    pairs: dict[str, Any] = {"correct": 0, "n": 0, "fp": 0, "fn": 0, "wrong": []}
    for p in cases:
        got = bool(matched_terms(p["doc"], key_terms(p["req"])))
        pairs["n"] += 1
        if got == p["match"]:
            pairs["correct"] += 1
        else:
            pairs["fp" if got else "fn"] += 1
            pairs["wrong"].append(p["req"] + " / " + p["doc"])
    return pairs


def evaluate(analyzer: Any, fx: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"analyzer": analyzer.name}
    with use_analyzer(analyzer):
        out["terms"] = _score_terms(fx["terms"])
        out["spacing"] = {
            "same": sum(_words(a) == _words(b) for a, b in fx["spacing"]),
            "n": len(fx["spacing"]),
        }
        out["pairs"] = _score_pairs(fx["pairs"])
        # 공고·이력서 문체 문장 (2026-11-17 추가): 낱말 세트와 따로 센다
        out["sentences"] = _score_terms(fx.get("sentences", []))
        out["sentence_pairs"] = _score_pairs(fx.get("sentence_pairs", []))
        roles = {"correct": 0, "n": 0, "wrong": []}
        for c in fx.get("roles", []):
            got = bool(lead_terms(c["suggested"]) - lead_terms(c["original"]))
            roles["n"] += 1
            if got == c["drift"]:
                roles["correct"] += 1
            else:
                roles["wrong"].append(c["original"] + " → " + c["suggested"])
        out["roles"] = roles
        # 속도: 세트의 모든 글을 20번 (분석기의 결과 기억은 매번 비운다)
        texts = [c["text"] for c in (*fx["terms"], *fx.get("sentences", []))] + [
            p["doc"] for p in (*fx["pairs"], *fx.get("sentence_pairs", []))
        ]
        t0 = time.perf_counter()
        for _ in range(20):
            getattr(analyzer, "_cache", {}).clear()
            for x in texts:
                analyzer.content_words(x)
        out["ms_per_text"] = round(1000 * (time.perf_counter() - t0) / (20 * len(texts)), 4)
    return out


def _max_rss_mb() -> float:
    v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return v / 1e6 if sys.platform == "darwin" else v / 1e3


def analyzers() -> list[tuple[Any, float]]:
    """(분석기, 올리며 늘어난 최대 RSS MB). kiwipiepy가 없으면 규칙 기반만."""
    out: list[tuple[Any, float]] = [(RuleAnalyzer(), 0.0)]
    try:
        from career_core.domain.korean import KiwiAnalyzer

        before = _max_rss_mb()
        kiwi = KiwiAnalyzer()
        kiwi.content_words("결제시스템을 배포했습니다")  # 첫 분석까지 포함
        out.append((kiwi, _max_rss_mb() - before))
    except ImportError:
        pass
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    fx = load_fixture()
    results = []
    for analyzer, rss in analyzers():
        r = evaluate(analyzer, fx)
        r["load_rss_mb"] = round(rss)
        results.append(r)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    for r in results:
        print(f"== {r['analyzer']}  (load +{r['load_rss_mb']}MB RSS, {r['ms_per_text']}ms/글)")
        for label in ("terms", "sentences"):
            for kind, t in r[label].items():
                print(
                    f"  {label:9} {kind:9} P {t['precision']:.3f}  R {t['recall']:.3f}"
                    f"  F1 {t['f1']:.3f}  exact {t['exact']}/{t['n']}"
                )
        s = r["spacing"]
        print(f"  spacing  same {s['same']}/{s['n']}")
        ro = r["roles"]
        print(
            f"  roles    {ro['correct']}/{ro['n']}" + "".join(f"\n      x {w}" for w in ro["wrong"])
        )
        for label in ("pairs", "sentence_pairs"):
            p = r[label]
            print(
                f"  {label:14} {p['correct']}/{p['n']}  FP {p['fp']}  FN {p['fn']}"
                + "".join(f"\n      x {w}" for w in p["wrong"])
            )


if __name__ == "__main__":
    main()

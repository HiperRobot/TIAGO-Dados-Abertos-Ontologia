# -*- coding: utf-8 -*-
"""Routing experiment: which dataset answers a question, under each condition.

Usage (from the repository root):
    python study/routing/run.py --gold study/gold/private/battery_v2.jsonl

Gold format, one JSON object per line:
    {"id": "...", "question": "...", "valid": ["dataset-name", ...], "stratum": "..."}

Deterministic and offline. Writes study/results/routing_<gold>.json.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mcp_tiago_dados_abertos.catalogo import ranking, retrieval  # noqa: E402
from mcp_tiago_dados_abertos.catalogo.contracts import load_all_contracts  # noqa: E402
from study.routing import metrics as M  # noqa: E402
from study.routing.catalogs import Graph, GrainResolver, from_ontology, schema_only  # noqa: E402

CONDITIONS = ["schema", "contracts", "contracts+onto", "onto_sem", "onto_full"]
PAIRS = [("schema", "contracts"), ("contracts", "contracts+onto"), ("contracts", "onto_full"),
         ("contracts", "onto_sem"), ("schema", "onto_sem")]


def route(catalog: dict, question: str, depth: int = 10) -> list[str]:
    return [name for _, name, _ in ranking.rank_datasets(catalog, question, top_n=depth)]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    gold = [json.loads(line) for line in args.gold.read_text(encoding="utf-8").splitlines() if line.strip()]
    g = Graph()
    full = load_all_contracts()
    catalogs = {"schema": schema_only(full), "contracts": full,
                "onto_sem": from_ontology(g, literals=False), "onto_full": from_ontology(g, literals=True)}
    resolver = GrainResolver(g)

    rankings: dict[str, list[list[str]]] = {}
    for cond in ("schema", "contracts", "onto_sem", "onto_full"):
        retrieval.build_index(catalogs[cond])  # the BM25 index is a process-wide singleton
        rankings[cond] = [route(catalogs[cond], it["question"]) for it in gold]
    rankings["contracts+onto"] = [resolver.rerank(r, it["question"])
                                  for r, it in zip(rankings["contracts"], gold)]

    ranks = {c: [M.rank_of(set(it["valid"]), r) for it, r in zip(gold, rankings[c])] for c in CONDITIONS}
    hit = {c: [r == 1 for r in ranks[c]] for c in CONDITIONS}
    rr = {c: [1 / r if r else 0.0 for r in ranks[c]] for c in CONDITIONS}

    result = {"gold": args.gold.name, "n": len(gold),
              "corpus_commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                              capture_output=True, text=True).stdout.strip(),
              "datasets": {c: len(catalogs[c]) for c in catalogs},
              "conditions": {c: M.summarise(ranks[c]) for c in CONDITIONS}}

    tests = {f"{a} vs {b}": M.mcnemar(hit[a], hit[b]) for a, b in PAIRS}
    adj = M.holm({k: v["p"] for k, v in tests.items()})
    for (a, b), (k, v) in zip(PAIRS, tests.items()):
        v["p_holm"] = adj[k]
        v["top1_diff"] = M.bootstrap_diff([float(x) for x in hit[a]], [float(x) for x in hit[b]])
        v["mrr_diff"] = M.bootstrap_diff(rr[a], rr[b])
    result["paired"] = tests

    strata = defaultdict(list)
    for i, it in enumerate(gold):
        strata[it.get("stratum", "all")].append(i)
    result["by_stratum"] = {s: {"n": len(ix), **{c: sum(hit[c][i] for i in ix) / len(ix) for c in CONDITIONS}}
                            for s, ix in sorted(strata.items())}
    result["changed_by_ontology"] = [
        {"id": it.get("id"), "valid": it["valid"],  # no question text: the gold is private
         "contracts": rankings["contracts"][i][:3], "contracts+onto": rankings["contracts+onto"][i][:3]}
        for i, it in enumerate(gold) if rankings["contracts"][i][:3] != rankings["contracts+onto"][i][:3]]

    out = args.out or ROOT / "study" / "results" / f"routing_{args.gold.stem}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{args.gold.name}: n={len(gold)}  corpus={result['corpus_commit']}")
    for c in CONDITIONS:
        s = result["conditions"][c]
        print(f"  {c:15s} top1={s['top1']:.3f} [{s['top1_ci95'][0]:.3f},{s['top1_ci95'][1]:.3f}]"
              f"  top3={s['top3']:.3f}  MRR={s['mrr']:.3f}")
    for k, v in tests.items():
        print(f"  {k:28s} only_a={v['only_a']:3d} only_b={v['only_b']:3d} p={v['p']:.4g} holm={v['p_holm']:.4g}")
    for s, v in result["by_stratum"].items():
        print(f"    {s:16s} n={v['n']:3d} " + " ".join(f"{c}={v[c]:.2f}" for c in CONDITIONS))
    print(f"  rankings changed by the ontology re-rank: {len(result['changed_by_ontology'])}")
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

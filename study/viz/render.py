# -*- coding: utf-8 -*-
"""Render study/viz/ontology_map.html from the ledger and the result files.

    python study/viz/render.py
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ORDER = ["Catálogo e identidade", "Grão, dimensões e classificação", "Métricas", "Colunas",
         "Apelidos de valores", "Apoio ao agente (perguntas e SQL)", "Camada física e operação"]
PHYSICAL = {"column.physicalType", "column.castExpression", "kind / apiVersion", "team", "slaProperties",
            "partitionPruning", "sourceExpression", "schema.physical*"}
AGENT = {"vectorTags", "typicalQuestions", "aiContext", "agentInstructions", "sql_patterns", "fewShotQueries"}
LABELS = {"schema": "Só schema", "contracts": "Contratos (produção)", "contracts+onto": "Contratos + reordenação pela ontologia",
          "onto_sem": "Ontologia, só conteúdo semântico", "onto_full": "Ontologia, conteúdo completo"}
GOLD = {"battery_v2": "Bateria v2", "golden_204": "Conjunto de 204"}


def group(c: str) -> str:
    if c.startswith("metric."):
        return "Métricas"
    if c in PHYSICAL or c.startswith("servers."):
        return "Camada física e operação"
    if c.startswith("column."):
        return "Colunas"
    if c == "value_aliases":
        return "Apelidos de valores"
    if c in AGENT:
        return "Apoio ao agente (perguntas e SQL)"
    if c.startswith(("row_grain", "semantics.")) or c in ("dimension_columns", "granularity", "spatialGranularity", "query_routing"):
        return "Grão, dimensões e classificação"
    return "Catálogo e identidade"


def main() -> int:
    L = json.loads((ROOT / "study/fidelity/ledger.json").read_text(encoding="utf-8"))
    by, gr, ds, voc = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    tot, V = Counter(), Counter()
    for r in L["rows"]:
        lv, n = r["level"], r["n"]
        ds[r["contract"]]["na" if lv == "n/a" else lv] += n
        if lv == "n/a":
            continue
        by[r["construct"]][lv] += n
        gr[group(r["construct"])][lv] += n
        tot[lv] += n
        if lv != "dropped":
            voc[r["construct"]][r["vocab"]] += n
            V[r["vocab"]] += n
    f = lambda c: {"semantic": c["semantic"], "literal": c["literal"], "dropped": c["dropped"]}  # noqa: E731

    def vlabel(c):
        v = voc[c]
        return "—" if not v else ("padrão" if not v["own"] else ("próprio" if not v["standard"] else "misto"))

    routing, agent = [], []
    for p in sorted((ROOT / "study/results").glob("routing_*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        key = p.stem.replace("routing_", "")
        routing.append({"gold": GOLD.get(key, key), "n": d["n"],
                        "rows": [{"label": LABELS.get(c, c), "v": s["top1"], "lo": s["top1_ci95"][0], "hi": s["top1_ci95"][1]}
                                 for c, s in d["conditions"].items()]})
    for p in sorted((ROOT / "study/results").glob("agent_*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        key = next((k for k in GOLD if k in p.stem), p.stem)
        agent.append({"gold": GOLD.get(key, key), "n": d["n"], "model": d["model"].split(".")[-1], "effort": d["effort"],
                      "rows": [{"label": LABELS.get(c, c), "v": s["accuracy"], "lo": s["accuracy_ci95"][0], "hi": s["accuracy_ci95"][1],
                                "tokens": round(s["input_tokens_mean"]), "calls": round(s["tool_calls_mean"], 1),
                                "none": s["no_answer"]} for c, s in d["conditions"].items()]})

    D = {"commit": L["corpus_commit"], "contracts": L["contracts"], "triples": L["triples"], "classes": L["nodes_by_class"],
         "total": f(tot), "vocab": {"standard": V["standard"], "own": V["own"]},
         "groups": [{"name": g, **f(gr[g])} for g in ORDER],
         "constructs": sorted([{"group": group(c), "name": c, "vocab": vlabel(c), **f(v)} for c, v in by.items()],
                              key=lambda x: (ORDER.index(x["group"]), -(x["semantic"] + x["literal"] + x["dropped"]))),
         "datasets": [{"name": k, **f(v), "na": v["na"]} for k, v in sorted(ds.items())],
         "routing": routing, "agent": agent}
    html = (HERE / "template.html").read_text(encoding="utf-8").replace("__DATA__", json.dumps(D, ensure_ascii=False))
    (HERE / "ontology_map.html").write_text(html, encoding="utf-8", newline="\n")
    print(f"rendered: {len(html)} bytes, {len(routing)} routing result(s), {len(agent)} agent result(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

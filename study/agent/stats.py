# -*- coding: utf-8 -*-
"""Paired statistics for the agent round: adds a "paired" block to each result file.

    python study/agent/stats.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from study.routing import metrics as M  # noqa: E402

PAIRS = [("schema", "contracts"), ("contracts", "onto_sem"), ("contracts", "onto_full"), ("schema", "onto_sem")]


def main() -> int:
    for path in sorted((ROOT / "study" / "results").glob("agent_*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        ok = {c: [bool(r["correct"]) for r in rows] for c, rows in d["items"].items()}
        tok = {c: [float(r["input"] + r["cache_read"] + r["cache_write"]) for r in rows] for c, rows in d["items"].items()}
        tests = {f"{a} vs {b}": M.mcnemar(ok[a], ok[b]) for a, b in PAIRS if a in ok and b in ok}
        adj = M.holm({k: v["p"] for k, v in tests.items()})
        for (a, b) in PAIRS:
            k = f"{a} vs {b}"
            if k in tests:
                tests[k]["p_holm"] = adj[k]
                tests[k]["accuracy_diff"] = M.bootstrap_diff([float(x) for x in ok[a]], [float(x) for x in ok[b]])
                tests[k]["input_tokens_diff"] = M.bootstrap_diff(tok[a], tok[b])
        d["paired"] = tests
        path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        print(path.name)
        for k, v in tests.items():
            print(f"  {k:24s} only_a={v['only_a']:3d} only_b={v['only_b']:3d} p={v['p']:.4g} holm={v['p_holm']:.4g}"
                  f"  d_acc={v['accuracy_diff'][0]:+.3f} [{v['accuracy_diff'][1]:+.3f},{v['accuracy_diff'][2]:+.3f}]"
                  f"  d_tokens={v['input_tokens_diff'][0]:+.0f} [{v['input_tokens_diff'][1]:+.0f},{v['input_tokens_diff'][2]:+.0f}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

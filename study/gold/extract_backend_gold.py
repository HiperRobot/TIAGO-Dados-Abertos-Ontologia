# -*- coding: utf-8 -*-
"""Convert the human-written question sets of the Tiago-PDA.Backend repository
into the gold format of study/routing/run.py.

The source repository is private (onstec-solucoes). The converted files go to
study/gold/private/, which is git-ignored: they must not be published without
authorisation from ONS.

Usage:
    python study/gold/extract_backend_gold.py --backend <path to Tiago-PDA.Backend>

Sources
  battery_v2   scripts/quality_gates/golden_queries/scenarios.csv on the branch
              origin/feature/golden_queries_improvements (curated test battery v2).
              Memory follow-ups (ids MEM-*) depend on the previous question and
              are excluded; rows with no expected dataset are excluded.
  golden_204  benchmark/selector_precision/golden_queries_204.json (version 3.0),
              questions with exactly one correct dataset.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent / "private"
BRANCH = "origin/feature/golden_queries_improvements"


def git_show(repo: Path, ref: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "show", ref], capture_output=True, check=True).stdout.decode("utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", required=True, type=Path)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    rows = list(csv.DictReader(io.StringIO(git_show(args.backend, f"{BRANCH}:scripts/quality_gates/golden_queries/scenarios.csv"))))
    commit = subprocess.run(["git", "-C", str(args.backend), "rev-parse", "--short", BRANCH],
                            capture_output=True, text=True).stdout.strip()
    items = []
    for r in rows:
        valid = [x.strip() for x in re.split(r"[;,|]", r.get("expected_datasets", "")) if x.strip()]
        if r["id"].startswith("MEM") or not valid:
            continue
        tags = r.get("tags", "").split(",")
        stratum = next((t for t in ("facil", "media", "dificil") if t in tags), "sem_dificuldade")
        items.append({"id": r["id"], "question": r["question"], "valid": valid, "stratum": stratum,
                      "block": r.get("block", "")})
    (OUT / "battery_v2.jsonl").write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in items), encoding="utf-8")
    print(f"battery_v2: {len(items)} of {len(rows)} rows kept (source {BRANCH} @ {commit})")

    src = args.backend / "benchmark" / "selector_precision" / "golden_queries_204.json"
    if not src.exists():
        raw = git_show(args.backend, "origin/develop:benchmark/selector_precision/golden_queries_204.json")
    else:
        raw = src.read_text(encoding="utf-8", errors="replace")
    entries = json.loads(raw)["entries"]
    items = [{"id": e["id"], "question": e["pergunta"], "valid": e["valid_datasets"],
              "stratum": e.get("challenge_type", "all"), "difficulty": e.get("difficulty", "")} for e in entries]
    (OUT / "golden_204.jsonl").write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in items), encoding="utf-8")
    print(f"golden_204: {len(items)} questions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

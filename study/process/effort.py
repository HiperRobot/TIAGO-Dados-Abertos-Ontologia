# -*- coding: utf-8 -*-
"""Effort figures for RQ1, from process records of the two attempts.

First attempt: the conversation log of the AI coding assistant session (kept in
study/process/private/, not published). Only aggregate counts leave this script:
no prompt text, no tool arguments.

Second attempt: the version history and the files of study/.

    python study/process/effort.py
Writes study/process/effort.json.
"""
from __future__ import annotations

import ast
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SESSION = ROOT / "study" / "process" / "private" / "kiro_session"
OUT = ROOT / "study" / "process" / "effort.json"


def payload(raw):
    if isinstance(raw, dict):
        return raw
    for parse in (json.loads, ast.literal_eval):
        try:
            return parse(raw)
        except Exception:
            continue
    return {}


def first_attempt() -> dict:
    events = []
    for line in (SESSION / "messages.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            events.append((r["timestamp"][:10], payload(r["payload"])))
    by_day = defaultdict(Counter)
    tools, elapsed = Counter(), defaultdict(float)
    for day, p in events:
        t = p.get("type")
        by_day[day][t] += 1
        if t == "tool_call":
            tools[p.get("toolName", "?")] += 1
        if t == "usage_summary":
            try:
                elapsed[day] += float(p.get("elapsedTime", 0)) / 1000
            except (TypeError, ValueError):
                pass
    snaps = Counter()
    files = Counter()
    for d in (SESSION / "snapshots").iterdir():
        if d.is_dir():
            for f in d.rglob("*"):
                if f.is_file() and f.name != ".hash":
                    files[str(f.relative_to(d)).replace("\\", "/")] += 1
    for day, c in sorted(by_day.items()):
        snaps[day] = 0
    meta = json.loads((SESSION / "session.json").read_text(encoding="utf-8"))
    days = [{"day": day, "user_prompts": c["user"], "assistant_messages": c["assistant"],
             "tool_calls": c["tool_call"], "assistant_seconds": round(elapsed[day])}
            for day, c in sorted(by_day.items()) if c["user"] or c["tool_call"]]
    return {
        "assistant_model": meta.get("modelId"), "events": len(events),
        "user_prompts": sum(d["user_prompts"] for d in days),
        "tool_calls": sum(d["tool_calls"] for d in days),
        "assistant_seconds": sum(d["assistant_seconds"] for d in days),
        "working_days": days, "tool_calls_by_tool": dict(tools.most_common()),
        "file_snapshots": sum(files.values()), "distinct_files": len(files),
        "most_revised_files": dict(files.most_common(8)),
    }


def second_attempt() -> dict:
    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout
    commits = [c for c in git("log", "--format=%h %ad %s", "--date=short", "--", "study", "thesis").splitlines() if c]
    loc = {}
    for f in sorted((ROOT / "study").rglob("*.py")):
        if ".venv" in f.parts or "private" in f.parts:
            continue
        loc[str(f.relative_to(ROOT)).replace("\\", "/")] = sum(1 for _ in f.open(encoding="utf-8"))
    ledger = json.loads((ROOT / "study" / "fidelity" / "ledger.json").read_text(encoding="utf-8"))
    tbox = (ROOT / "study" / "ontology" / "tbox.ttl").read_text(encoding="utf-8")
    return {"commits": len(commits), "commit_log": commits, "python_lines": loc,
            "python_lines_total": sum(loc.values()),
            "tbox_lines_hand_written": tbox.count("\n"), "abox_triples_generated": ledger["triples"],
            "hand_written_instance_data": 0}


def main() -> int:
    out = {"first_attempt": first_attempt(), "second_attempt": second_attempt()}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    a, b = out["first_attempt"], out["second_attempt"]
    print(f"first attempt: {a['user_prompts']} prompts, {a['tool_calls']} tool calls, "
          f"{a['assistant_seconds'] / 60:.0f} min of assistant time, {a['file_snapshots']} snapshots of {a['distinct_files']} files")
    for d in a["working_days"]:
        print("   ", d)
    print("    tools:", a["tool_calls_by_tool"])
    print(f"second attempt: {b['commits']} commits, {b['python_lines_total']} lines of Python, "
          f"{b['tbox_lines_hand_written']} lines of hand-written schema, {b['abox_triples_generated']} generated triples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

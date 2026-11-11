# -*- coding: utf-8 -*-
"""Agent round: an LLM picks the dataset, using tools fed by each condition's catalogue.

The model sees three tools that mirror the MCP server (search, describe) plus a
final-answer tool. What changes between conditions is the catalogue behind the
tools, exactly as in study/routing/run.py:

    schema | contracts | onto_sem | onto_full

Measured per condition: accuracy of the chosen dataset, abstentions, tool calls,
and input/output tokens (the context cost of each semantic layer).

Runs on Amazon Bedrock through the Anthropic SDK. Credentials come from the
standard AWS chain (e.g. AWS_PROFILE). EVERY RUN COSTS MONEY: start with --limit.

    AWS_PROFILE=<profile> study/.venv/Scripts/python study/agent/run.py \
        --gold study/gold/private/battery_v2.jsonl --limit 5
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import anthropic  # noqa: E402
from anthropic import AnthropicBedrockMantle  # noqa: E402

from mcp_tiago_dados_abertos.catalogo import ranking, retrieval  # noqa: E402
from mcp_tiago_dados_abertos.catalogo.contracts import load_all_contracts  # noqa: E402
from study.routing import metrics as M  # noqa: E402
from study.routing.catalogs import Graph, from_ontology, schema_only  # noqa: E402

SYSTEM = (
    "Você atende perguntas sobre os dados abertos do ONS (Operador Nacional do Sistema Elétrico). "
    "Sua tarefa é decidir qual dataset do catálogo responde à pergunta do usuário. "
    "Use buscar_dataset para procurar candidatos e descrever_dataset quando precisar de detalhes. "
    "Quando decidir, chame escolher_dataset com o nome exato do dataset. "
    "Se nenhum dataset do catálogo responder à pergunta, chame escolher_dataset com nome vazio."
)

TOOLS = [
    {"name": "buscar_dataset",
     "description": "Busca datasets do catálogo por tema. Devolve até 5 candidatos, do mais ao menos relevante.",
     "input_schema": {"type": "object", "properties": {"tema": {"type": "string", "description": "Assunto ou pergunta em linguagem natural."}},
                      "required": ["tema"], "additionalProperties": False}},
    {"name": "descrever_dataset",
     "description": "Devolve a descrição completa de um dataset: colunas e o que o catálogo sabe sobre ele.",
     "input_schema": {"type": "object", "properties": {"nome": {"type": "string", "description": "Nome exato do dataset."}},
                      "required": ["nome"], "additionalProperties": False}},
    {"name": "escolher_dataset",
     "description": "Resposta final: o dataset que responde à pergunta. Chame uma única vez.",
     "input_schema": {"type": "object", "properties": {"nome": {"type": "string", "description": "Nome exato do dataset, ou vazio se nenhum servir."}},
                      "required": ["nome"], "additionalProperties": False}},
]


def summary(meta: dict) -> dict:
    """One search hit, as the condition's catalogue can describe it."""
    out = {"nome": meta["name"]}
    if meta.get("tags"):
        out["tags"] = meta["tags"]
    if meta.get("rag_context"):
        out["contexto"] = meta["rag_context"]
    if len(out) == 1:
        out["colunas"] = [c.get("name") for c in meta.get("columns", [])]
    return out


def describe(meta: dict) -> dict:
    keep = ("name", "tags", "vector_tags", "rag_context", "granularity", "spatial_granularity",
            "typical_questions", "columns", "semantics")
    return {k: meta[k] for k in keep if meta.get(k)}


class Catalogue:
    def __init__(self, catalog: dict):
        self.catalog = catalog
        self.by_name = {m["name"]: m for m in catalog.values()}

    def call(self, name: str, args: dict) -> str:
        if name == "buscar_dataset":
            retrieval.build_index(self.catalog)  # the index is a process-wide singleton
            hits = ranking.rank_datasets(self.catalog, str(args.get("tema", "")), top_n=5)
            return json.dumps([summary(m) for _, _, m in hits], ensure_ascii=False)
        if name == "descrever_dataset":
            meta = self.by_name.get(str(args.get("nome", "")))
            return json.dumps(describe(meta), ensure_ascii=False) if meta else "Dataset não encontrado no catálogo."
        return "ok"


def ask(client, model: str, effort: str, cat: Catalogue, question: str, max_turns: int = 8) -> dict:
    messages = [{"role": "user", "content": question}]
    usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    calls, choice, stop = 0, None, None
    for _ in range(max_turns):
        resp = client.messages.create(
            model=model, max_tokens=16000, system=SYSTEM, tools=TOOLS, messages=messages,
            output_config={"effort": effort},
        )
        u = resp.usage
        usage["input"] += u.input_tokens or 0
        usage["output"] += u.output_tokens or 0
        usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        stop = resp.stop_reason
        if stop in ("refusal", "max_tokens"):
            break
        uses = [b for b in resp.content if b.type == "tool_use"]
        if not uses:
            break
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for b in uses:  # all results of one turn go back in a single user message
            calls += 1
            if b.name == "escolher_dataset":
                choice = str((b.input or {}).get("nome", "")).strip()
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": cat.call(b.name, b.input or {})})
        if choice is not None:
            break
        messages.append({"role": "user", "content": results})
    return {"choice": choice, "stop": stop, "tool_calls": calls, **usage}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True, type=Path)
    ap.add_argument("--model", default="anthropic.claude-opus-5-5")
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--conditions", nargs="+", default=["schema", "contracts", "onto_sem", "onto_full"])
    ap.add_argument("--limit", type=int, default=None, help="first N questions only (pilot)")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    gold = [json.loads(x) for x in args.gold.read_text(encoding="utf-8").splitlines() if x.strip()]
    if args.limit:
        gold = gold[: args.limit]
    g, full = Graph(), load_all_contracts()
    catalogs = {"schema": schema_only(full), "contracts": full,
                "onto_sem": from_ontology(g, literals=False), "onto_full": from_ontology(g, literals=True)}
    client = AnthropicBedrockMantle(aws_region=args.region)

    result = {"gold": args.gold.name, "n": len(gold), "model": args.model, "effort": args.effort,
              "corpus_commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                              capture_output=True, text=True).stdout.strip(),
              "conditions": {}, "items": {}}
    for cond in args.conditions:
        cat, rows, t0 = Catalogue(catalogs[cond]), [], time.time()
        for it in gold:
            try:
                r = ask(client, args.model, args.effort, cat, it["question"])
            except anthropic.RateLimitError:
                time.sleep(30)
                r = ask(client, args.model, args.effort, cat, it["question"])
            except anthropic.APIStatusError as e:
                r = {"choice": None, "stop": f"api_error_{e.status_code}", "tool_calls": 0,
                     "input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
            r["id"], r["correct"] = it.get("id"), bool(r["choice"]) and r["choice"] in it["valid"]
            rows.append(r)
        n = len(rows)
        k = sum(r["correct"] for r in rows)
        lo, hi = M.wilson(k, n)
        result["conditions"][cond] = {
            "accuracy": k / n, "accuracy_ci95": [lo, hi],
            "no_answer": sum(1 for r in rows if not r["choice"]) / n,
            "wrong_answer": sum(1 for r in rows if r["choice"] and not r["correct"]) / n,
            "tool_calls_mean": sum(r["tool_calls"] for r in rows) / n,
            "input_tokens_mean": sum(r["input"] + r["cache_read"] + r["cache_write"] for r in rows) / n,
            "output_tokens_mean": sum(r["output"] for r in rows) / n,
            "seconds": round(time.time() - t0, 1),
        }
        result["items"][cond] = rows
        c = result["conditions"][cond]
        print(f"  {cond:10s} acc={c['accuracy']:.3f} sem_resposta={c['no_answer']:.2f} "
              f"chamadas={c['tool_calls_mean']:.1f} in={c['input_tokens_mean']:.0f} out={c['output_tokens_mean']:.0f} "
              f"({c['seconds']}s)")

    out = args.out or ROOT / "study" / "results" / f"agent_{args.gold.stem}_{args.model.split('.')[-1]}_{args.effort}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

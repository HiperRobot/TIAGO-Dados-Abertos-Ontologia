# study/ — case study: migrating data contracts to an ontology

Research material for an undergraduate thesis (PUC-Rio, Projeto Final II). Nothing here is
imported by the production server in `src/`.

| Folder | What it holds |
|---|---|
| `ontology/` | `tbox.ttl` (hand-written schema), `build_abox.py` (generator), `abox.ttl` / `abox.json` (generated from the 80 contracts) |
| `fidelity/` | `ledger.json`: every contract construct occurrence and how it reached the ontology |
| `routing/` | the production router fed by five catalogues; metrics and paired statistics |
| `agent/` | an LLM on Amazon Bedrock chooses the dataset through tools backed by each catalogue |
| `mcp/` | prototype MCP server that answers from the ontology |
| `gold/` | converter for the question sets; the sets themselves are private and git-ignored |
| `process/` | effort figures of the two attempts; the raw assistant log is private and git-ignored |
| `results/` | aggregate results (no question text) |
| `viz/` | page with the class map, the contract-vs-ontology comparison and the results |

## Reproduce

Run from the repository root. PyYAML is the only dependency of the offline steps.

```bash
python study/ontology/build_abox.py                 # ontology + ledger
python study/gold/extract_backend_gold.py --backend <path to the private question repository>
python study/routing/run.py --gold study/gold/private/battery_v2.jsonl
python study/routing/run.py --gold study/gold/private/golden_204.jsonl
python study/process/effort.py
python study/viz/render.py
```

The agent round and the MCP prototype need a virtual environment with `anthropic[bedrock]`
and `fastmcp==3.4.6`, and AWS credentials with access to Amazon Bedrock. Every agent run is
billed; start with `--limit`.

```bash
AWS_PROFILE=<profile> study/.venv/Scripts/python study/agent/run.py \
    --gold study/gold/private/battery_v2.jsonl --model us.anthropic.claude-sonnet-5-5 --limit 5
python study/agent/stats.py
study/.venv/Scripts/python study/mcp/server.py      # stdio MCP server
```

## Status and known limits

- The namespace `https://w3id.org/tiago-ons/` is provisional and not registered.
- CIM class names were checked against CIM17 documentation; the CIM namespace IRI was not.
- The agent round measures the choice of dataset, not the correctness of the final answer.
- The earlier exploratory attempt is kept in the branch `tcc/research-kiro-v0.1`.

"""Build the interactive knowledge-graph page of the ontology.

Reads the generated A-Box (study/ontology/abox.json), the hand-written T-Box
(study/ontology/tbox.ttl, spatial entities only), the fidelity ledger and, when
present, the agent results and the private question sets. Writes
study/viz/ontology_graph.html from study/viz/graph_template.html.

Only dataset identifiers and counts leave the private question sets: no
question text is written to the page.

    python study/viz/build_graph.py
"""
from __future__ import annotations

import collections
import glob
import json
import os
import re
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
P = lambda *a: os.path.join(ROOT, *a)
DATA = "https://w3id.org/tiago-ons/data/"

abox = json.load(open(P("study/ontology/abox.json"), encoding="utf-8"))


def short(uri: str) -> str:
    return uri[len(DATA):] if uri.startswith(DATA) else uri


def typ(s):
    return next((v for p, k, v, _ in abox.get(s, []) if p == "a"), None)


TYPE = {  # ontology class -> page type key
    "ons:Dataset": "dataset", "ons:Metric": "metric", "ons:Dimension": "dimension",
    "ons:Column": "column", "ons:QueryTemplate": "template", "ons:ValueAlias": "alias",
    "ons:Family": "family", "ons:Domain": "domain", "ons:Perspective": "perspective",
    "ons:UseCase": "usecase", "ons:Status": "status", "ons:QuantityKind": "qkind",
    "ons:Unit": "unit", "ons:Aggregation": "aggregation",
}
DETAIL = {"column", "template", "alias"}

nodes: dict[str, dict] = {}
edges: list[tuple[str, str, str]] = []


def lits(po, pred, lang_first=True):
    return [v for p, k, v, _ in po if p == pred and k == "lit"]


def refs(po, pred):
    return [v for p, k, v, _ in po if p == pred and k == "ref"]


def first(po, pred):
    x = lits(po, pred)
    return x[0] if x else None


# ---- spatial entities from the T-Box ---------------------------------------
tbox = open(P("study/ontology/tbox.ttl"), encoding="utf-8").read()
for m in re.finditer(r"^ons:(\w+)\s+a ons:SpatialEntityType ;(.*?)\.\s*$", tbox, re.S | re.M):
    name, body = m.group(1), m.group(2)
    label = re.search(r'rdfs:label "([^"]+)"@en', body)
    nodes["ons:" + name] = {
        "id": "ons:" + name, "t": "entity", "l": name,
        "d": {
            "Nome em inglês": label.group(1) if label else name,
            "Rótulos em português": ", ".join(re.findall(r'"([^"]+)"@pt', body)) or None,
            "Correspondência no IEC CIM": ", ".join(re.findall(r"closeMatch (cim:\w+)", body)) or None,
        },
    }
    for parent in re.findall(r"ons:partOf ([^;.]+)", body):
        for p in re.findall(r"ons:(\w+)", parent):
            edges.append(("ons:" + name, "ons:" + p, "partOf"))

# ---- A-Box -------------------------------------------------------------------
title_to_id = {}
for s, po in abox.items():
    t = TYPE.get(typ(s))
    if not t:
        continue
    sid = short(s)
    label = first(po, "skos:prefLabel") or first(po, "dct:title") or first(po, "skos:notation") or sid.split("/")[-1]
    d: dict = {}
    if t == "dataset":
        title_to_id[first(po, "dct:title")] = sid
        alts = lits(po, "skos:altLabel")
        d = {
            "Identificador": first(po, "dct:title"),
            "Descrição": first(po, "dct:description"),
            "Quando usar": first(po, "ons:usage"),
            "Limitações": first(po, "ons:limitation"),
            "Resolução temporal": first(po, "ons:temporalResolution"),
            "Periodicidade": first(po, "dct:accrualPeriodicity"),
            "Atualização": first(po, "ons:freshness"),
            "Período coberto": first(po, "dct:temporal"),
            "Onde está o dado": first(po, "dcat:accessURL"),
            "Página no portal": (refs(po, "dct:source") or [None])[0],
            "Instrução para agentes": first(po, "ons:agentInstruction"),
            "Perguntas de exemplo do contrato": lits(po, "ons:exampleQuestion")[:6] or None,
            "Sinônimos": (f"{len(alts)} no total: " + "; ".join(alts[:14])) if alts else None,
        }
    elif t == "metric":
        d = {
            "Expressão SQL": first(po, "ons:expression"),
            "Somável no tempo": {"true": "sim", "false": "não"}.get(first(po, "ons:timeAdditive") or "", None),
            "Horas por registro": first(po, "ons:intervalHours"),
            "Modos de agregação": ", ".join(json.loads(first(po, "ons:aggregationSQL") or "{}").keys()) or None,
        }
    elif t == "column":
        av = lits(po, "ons:allowedValue")
        d = {
            "Descrição": first(po, "dct:description"),
            "Tipo": first(po, "dct:type"), "Tipo físico": first(po, "ons:physicalType"),
            "Papel": first(po, "ons:semanticRole"),
            "Valores permitidos": (", ".join(av[:20]) + (" …" if len(av) > 20 else "")) if av else None,
            "Sinônimos": "; ".join(lits(po, "skos:altLabel")[:10]) or None,
        }
    elif t == "template":
        d = {"SQL": first(po, "ons:sqlTemplate"), "Unidade de saída": first(po, "ons:outputUnitLabel")}
    elif t == "alias":
        d = {"Código no dado": first(po, "skos:notation"),
             "Como as pessoas chamam": "; ".join(lits(po, "skos:altLabel")[:12]) or None,
             "Filtro SQL": first(po, "ons:filter")}
    elif t in ("qkind", "unit"):
        d = {"Correspondência QUDT": ", ".join(r.split("/")[-1] for r in refs(po, "skos:closeMatch")) or None,
             "Também escrito como": "; ".join(lits(po, "skos:altLabel")) or None}
    nodes[sid] = {"id": sid, "t": t, "l": label, "d": {k: v for k, v in d.items() if v}}

KINDS = {  # predicate -> edge kind
    "ons:inDomain": "inDomain", "ons:inFamily": "inFamily", "ons:hasPerspective": "perspective",
    "ons:spatialGrain": "grain", "ons:rowEntity": "rowEntity", "ons:hasStatus": "status",
    "ons:supportsUseCase": "supports", "ons:doesNotSupportUseCase": "notSupports",
    "ons:hasMetric": "hasMetric", "ons:quantityKind": "qkind", "ons:unit": "unit",
    "ons:defaultAggregation": "aggregation", "ons:forbiddenAggregation": "forbidden",
    "ons:hasDimension": "hasDimension", "ons:refersTo": "refersTo", "ons:onColumn": "onColumn",
    "ons:hasColumn": "hasColumn", "ons:computedFrom": "computedFrom", "ons:hasTemplate": "hasTemplate",
    "ons:usesMetric": "usesMetric", "ons:hasValueAlias": "hasAlias",
}
for s, po in abox.items():
    if short(s) not in nodes:
        continue
    for p, k, v, _ in po:
        if k == "ref" and p in KINDS:
            tgt = short(v)
            if tgt in nodes:
                edges.append((short(s), tgt, KINDS[p]))

# ---- fidelity per dataset ------------------------------------------------------
ledger = json.load(open(P("study/fidelity/ledger.json"), encoding="utf-8"))
fid = collections.defaultdict(collections.Counter)
for r in ledger["rows"]:
    fid[r["contract"]][r["level"]] += r["n"]
for title, sid in title_to_id.items():
    c = fid.get(title)
    if c:
        nodes[sid]["fid"] = {k: c.get(k, 0) for k in ("semantic", "literal", "dropped")}

# ---- similarity between datasets (shared vocabulary) ---------------------------
STOP = set("de da do das dos e em por para com sem a o as os um uma no na nos nas ao aos que qual quais "
           "como mais menos entre sobre ate desde the of and dados dataset ons sin hora dia mes ano".split())


def toks(text):
    text = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return {w for w in re.findall(r"[a-z0-9]+", text) if len(w) >= 3 and w not in STOP}


vocab = {}
for s, po in abox.items():
    if typ(s) == "ons:Dataset":
        words = " ".join(lits(po, "skos:altLabel") + lits(po, "dcat:keyword") + [first(po, "dct:title") or ""])
        vocab[short(s)] = toks(words)
sims = set()
for a, wa in vocab.items():
    scored = []
    for b, wb in vocab.items():
        if a != b and wa and wb:
            j = len(wa & wb) / len(wa | wb)
            if j >= 0.12:
                scored.append((j, b))
    for j, b in sorted(scored, reverse=True)[:3]:
        sims.add((min(a, b), max(a, b), round(j, 2)))
for a, b, j in sims:
    edges.append((a, b, "similar:" + str(j)))

# ---- agent results per dataset and confusions ----------------------------------
gold = {}
for f in glob.glob(P("study/gold/private/*.jsonl")):
    for line in open(f, encoding="utf-8"):
        x = json.loads(line)
        gold[x["id"]] = x["valid"]
stats = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0])))
conf = collections.Counter()
models_seen = []
for f in sorted(glob.glob(P("study/results/agent_*_medium.json"))):
    res = json.load(open(f, encoding="utf-8"))
    model = re.search(r"claude-(\w+)-5-5", f).group(1).capitalize()
    models_seen.append(model)
    for cond, items in res["items"].items():
        for it in items:
            valid = gold.get(it["id"])
            if not valid:
                continue
            for v in valid:
                sid = title_to_id.get(v)
                if sid:
                    stats[sid][model][cond][1] += 1
                    stats[sid][model][cond][0] += int(it["correct"])
            if not it["correct"] and it["choice"] and cond != "schema":
                a, b = title_to_id.get(valid[0]), title_to_id.get(it["choice"])
                if a and b and a != b:
                    conf[(a, b)] += 1
for sid, m in stats.items():
    nodes[sid]["ag"] = {mod: {c: v for c, v in cs.items()} for mod, cs in m.items()}
for (a, b), n in conf.items():
    if n >= 2:
        edges.append((a, b, "confused:" + str(n)))

# ---- write -------------------------------------------------------------------------
seen = set()
E = []
for s, t, k in edges:
    key = (s, t, k.split(":")[0])
    if s in nodes and t in nodes and key not in seen:
        seen.add(key)
        E.append([s, t, k])
graph = {"nodes": list(nodes.values()), "edges": E, "models": sorted(set(models_seen)),
         "hasAgent": bool(gold)}
payload = json.dumps(graph, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
tpl = open(P("study/viz/graph_template.html"), encoding="utf-8").read()
out = tpl.replace("/*GRAPH_DATA*/", payload)
open(P("study/viz/ontology_graph.html"), "w", encoding="utf-8", newline="\n").write(out)
cnt = collections.Counter(n["t"] for n in nodes.values())
ek = collections.Counter(k.split(":")[0] for *_, k in E)
print("nodes", len(nodes), dict(cnt))
print("edges", len(E), dict(ek))
print("bytes", len(out.encode("utf-8")))

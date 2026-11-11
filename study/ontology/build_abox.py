# -*- coding: utf-8 -*-
"""Derive the ontology A-Box from the ODCS data contracts, and keep a ledger.

Every construct occurrence found in a contract is recorded in a ledger with the
level at which it reached the ontology:

  semantic  typed resource or controlled concept, linked in the graph
            (queryable and joinable across datasets)
  literal   content preserved, but as an opaque text/SQL/JSON literal
  dropped   not migrated
  n/a       the contract explicitly declares "no value" (e.g. entity=dataset_row)

and with the vocabulary that carries it: "standard" (DCAT, DCT, SKOS, QB, CSVW,
QUDT) or "own" (ons: namespace). The ledger is the raw data of the fidelity RQ;
study/fidelity/report.py aggregates it.

Usage (from the repository root):
    python study/ontology/build_abox.py
Writes study/ontology/abox.ttl and study/fidelity/ledger.json.
Only dependency: PyYAML (already a dependency of the server).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts" / "ons"
OUT_TTL = ROOT / "study" / "ontology" / "abox.ttl"
OUT_LEDGER = ROOT / "study" / "fidelity" / "ledger.json"

ONS = "https://w3id.org/tiago-ons/onto#"
DATA = "https://w3id.org/tiago-ons/data/"
QUDT_QK = "http://qudt.org/vocab/quantitykind/"
QUDT_UNIT = "http://qudt.org/vocab/unit/"

PREFIXES = """@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix dct:  <http://purl.org/dc/terms/> .
@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix ons:  <https://w3id.org/tiago-ons/onto#> .
"""


# ── helpers ─────────────────────────────────────────────────────────────────
def norm(text) -> str:
    """Lowercase, strip accents and surrounding blanks."""
    s = unicodedata.normalize("NFKD", str(text or "").strip().lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def slug(text) -> str:
    return re.sub(r"[^a-z0-9]+", "_", norm(text)).strip("_") or "x"


def iri(*parts) -> str:
    return "<" + DATA + "/".join(slug(p) if i else p for i, p in enumerate(parts)) + ">"


def lit(value, lang: str | None = None, dtype: str | None = None) -> str:
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    if lang:
        return f'"{s}"@{lang}'
    if dtype:
        return f'"{s}"^^xsd:{dtype}'
    return f'"{s}"'


class Graph:
    """Minimal Turtle writer: subject -> [(predicate, object)] in insertion order."""

    def __init__(self):
        self.nodes: dict[str, list[tuple[str, str]]] = defaultdict(list)

    def add(self, s: str, p: str, o: str) -> None:
        if (p, o) not in self.nodes[s]:
            self.nodes[s].append((p, o))

    def triples(self) -> int:
        return sum(len(v) for v in self.nodes.values())

    def dump(self) -> str:
        out = [PREFIXES]
        for s, pos in self.nodes.items():
            body = " ;\n    ".join(f"{p} {o}" for p, o in pos)
            out.append(f"{s}\n    {body} .\n")
        return "\n".join(out)


class Ledger:
    def __init__(self):
        self.rows: Counter = Counter()

    def rec(self, contract: str, construct: str, level: str, vocab: str = "-", n: int = 1):
        if n:
            self.rows[(contract, construct, level, vocab)] += n


# ── controlled vocabularies (normalisation tables) ──────────────────────────
# unit string as written in a contract (normalised) -> (canonical id, QUDT unit or None)
UNITS = {
    "mwmed": ("MWmed", None), "mw": ("MW", "MegaW"), "mwh": ("MWh", "MegaW-HR"),
    "gwh": ("GWh", "GigaW-HR"), "mwmes": ("MWmes", None), "m3/s": ("m3_per_s", "M3-PER-SEC"),
    "%": ("percent", "PERCENT"), "r$/mwh": ("BRL_per_MWh", None), "m": ("m", "M"),
    "mva": ("MVA", "MegaV-A"), "kv": ("kV", "KiloV"), "mvar": ("Mvar", None),
    "minutos": ("min", "MIN"), "min": ("min", "MIN"), "horas": ("h", "HR"), "h": ("h", "HR"),
    "horas (h)": ("h", "HR"), "s": ("s", "SEC"), "hz": ("Hz", "HZ"), "km": ("km", "KiloM"),
    "m/s": ("m_per_s", "M-PER-SEC"), "hm3": ("hm3", None), "pu": ("pu", None), "p.u.": ("pu", None),
    "graus decimais": ("deg", "DEG"), "% mlt": ("percent_MLT", None), "%mlt": ("percent_MLT", None),
}
# quantity_kind of the contract -> QUDT quantity kind (only where the match is safe)
QUANTITY_KINDS = {
    "average_power": "ActivePower", "power": "ActivePower", "energy": "Energy",
    "energy_stock": "Energy", "flow": "VolumeFlowRate", "volume": "Volume",
    "duration": "Time", "duration_minutes": "Time", "ratio": "DimensionlessRatio",
    "percent": "DimensionlessRatio", "irradiance": "Irradiance", "level": "Length",
}
AGGREGATIONS = {"average": "avg", "maximum": "max", "minimum": "min"}
ENTITIES = {
    "usina": "Usina", "subsistema": "Subsistema", "reservatorio": "Reservatorio",
    "subestacao": "Subestacao", "bacia": "Bacia", "ree": "REE", "agente": "Agente",
    "nacional": "SIN", "sin": "SIN", "por_bacia": "Bacia", "por_linha": "Linha",
    "por_subsistema": "Subsistema", "por_usina": "Usina",
}
NO_ENTITY = {"dataset_row", "", "none"}


def unit_node(g: Graph, raw) -> tuple[str, bool]:
    """Return (IRI of the unit concept, has_standard_match)."""
    key = norm(raw).replace("³", "3").replace("·", ".")
    canon, qudt = UNITS.get(key, (slug(key), None))
    node = iri("unit", canon)
    g.add(node, "a", "ons:Unit")
    g.add(node, "skos:prefLabel", lit(canon))
    g.add(node, "skos:altLabel", lit(str(raw).strip()))
    if qudt:
        g.add(node, "skos:closeMatch", f"<{QUDT_UNIT}{qudt}>")
    return node, bool(qudt)


def concept(g: Graph, kind: str, cls: str, raw) -> str:
    node = iri(kind, raw)
    g.add(node, "a", cls)
    g.add(node, "skos:prefLabel", lit(slug(raw)))
    if norm(raw) != str(raw):
        g.add(node, "skos:altLabel", lit(str(raw).strip(), "pt"))
    return node


# ── one contract ────────────────────────────────────────────────────────────
def migrate(doc: dict, g: Graph, L: Ledger) -> None:
    name = doc["name"]
    ds = iri("dataset", name)
    cp = {c["property"]: c.get("value") for c in doc.get("customProperties") or []}
    sem = cp.get("semantics") or {}
    rec = lambda construct, level, vocab="-", n=1: L.rec(name, construct, level, vocab, n)  # noqa: E731

    # identity and catalogue metadata
    g.add(ds, "a", "ons:Dataset")
    g.add(ds, "dct:identifier", lit(doc.get("id", name)))
    g.add(ds, "dct:title", lit(name))
    rec("id / name", "semantic", "standard", 2)
    if doc.get("version"):
        g.add(ds, "owl:versionInfo", lit(doc["version"]))
        rec("version", "semantic", "standard")
    if doc.get("tenant"):
        g.add(ds, "dct:publisher", lit(doc["tenant"]))
        rec("tenant", "semantic", "standard")
    for k in ("kind", "apiVersion"):
        if doc.get(k):
            rec("kind / apiVersion", "dropped")
    if doc.get("status"):
        g.add(ds, "ons:hasStatus", concept(g, "status", "ons:Status", doc["status"]))
        rec("status", "semantic", "own")
    if doc.get("domain"):
        g.add(ds, "ons:inDomain", concept(g, "domain", "ons:Domain", doc["domain"]))
        rec("domain", "semantic", "own")
    for t in doc.get("tags") or []:
        g.add(ds, "dcat:keyword", lit(t, "pt"))
        rec("tags", "semantic", "standard")
    desc = doc.get("description") or {}
    for key, prop, vocab in (("purpose", "dct:description", "standard"),
                             ("usage", "ons:usage", "own"), ("limitations", "ons:limitation", "own")):
        if desc.get(key):
            g.add(ds, prop, lit(desc[key], "pt"))
            rec(f"description.{key}", "literal", vocab)
    for a in doc.get("authoritativeDefinitions") or []:
        if a.get("url"):
            g.add(ds, "dct:source", f"<{a['url']}>")
            rec("authoritativeDefinitions", "semantic", "standard")
    for s in doc.get("slaProperties") or []:
        g.add(ds, "ons:freshness", lit(f"{s.get('value')} — {s.get('description', '')}".strip(" —"), "pt"))
        rec("slaProperties", "literal", "own")
    for s in doc.get("servers") or []:
        if s.get("location"):
            g.add(ds, "dcat:accessURL", lit(s["location"]))
            rec("servers.location", "literal", "standard")
        rec("servers.type / format", "dropped", n=sum(1 for k in ("type", "format", "server") if s.get(k)))
    team = doc.get("team") or {}
    rec("team", "dropped", n=1 + len(team.get("members") or []))

    # dataset-level custom properties
    for v in cp.get("vectorTags") or []:
        g.add(ds, "skos:altLabel", lit(v, "pt"))
        rec("vectorTags", "semantic", "standard")
    for q in cp.get("typicalQuestions") or []:
        g.add(ds, "ons:exampleQuestion", lit(q, "pt"))
        rec("typicalQuestions", "literal", "own")
    if cp.get("aiContext"):
        g.add(ds, "ons:retrievalContext", lit(cp["aiContext"], "pt"))
        rec("aiContext", "literal", "own")
    if cp.get("agentInstructions"):
        g.add(ds, "ons:agentInstruction", lit(cp["agentInstructions"], "pt"))
        rec("agentInstructions", "literal", "own")
    if cp.get("granularity"):
        g.add(ds, "ons:temporalResolution", lit(norm(cp["granularity"])))
        rec("granularity", "literal", "standard")
    if cp.get("sourceExpression"):
        g.add(ds, "ons:sourceExpression", lit(cp["sourceExpression"]))
        rec("sourceExpression", "literal", "own")
    if cp.get("license"):
        g.add(ds, "dct:license", lit(cp["license"]))
        rec("license", "semantic", "standard")
    if cp.get("temporalCoverageStart") or cp.get("temporalCoverageEnd"):
        g.add(ds, "dct:temporal", lit(f"{cp.get('temporalCoverageStart', '')}/{cp.get('temporalCoverageEnd', '')}"))
        rec("temporalCoverage", "semantic", "standard",
            sum(1 for k in ("temporalCoverageStart", "temporalCoverageEnd") if cp.get(k)))
    if cp.get("partitionPruning"):
        rec("partitionPruning", "dropped")
    sg = norm(cp.get("spatialGranularity"))
    if sg:
        if sg in ENTITIES:
            g.add(ds, "ons:spatialGrain", f"ons:{ENTITIES[sg]}")
            rec("spatialGranularity", "semantic", "own")
        else:
            rec("spatialGranularity", "dropped")

    # columns
    col_iri: dict[str, str] = {}
    for sch in doc.get("schema") or []:
        for k in ("physicalName", "physicalType", "logicalType"):
            if sch.get(k):
                rec("schema.physical*", "dropped")
        for p in sch.get("properties") or []:
            c = iri("column", name, p["name"])
            col_iri[p["name"]] = c
            g.add(ds, "ons:hasColumn", c)
            g.add(c, "a", "ons:Column")
            g.add(c, "dct:title", lit(p["name"]))
            rec("column.name", "semantic", "standard")
            if p.get("logicalType"):
                g.add(c, "dct:type", lit(p["logicalType"]))
                rec("column.logicalType", "semantic", "standard")
            if p.get("physicalType"):
                g.add(c, "ons:physicalType", lit(p["physicalType"]))
                rec("column.physicalType", "literal", "own")
            if p.get("description"):
                g.add(c, "dct:description", lit(str(p["description"]).strip(), "pt"))
                rec("column.description", "literal", "standard")
            if p.get("primaryKey"):
                g.add(ds, "ons:rowKey", c)
                rec("column.primaryKey", "semantic", "own")
            for x in p.get("customProperties") or []:
                key, val = x.get("property"), x.get("value")
                if key == "aliases":
                    for a in val or []:
                        g.add(c, "skos:altLabel", lit(a, "pt"))
                        rec("column.aliases", "semantic", "standard")
                elif key == "semanticType":
                    g.add(c, "ons:semanticRole", lit(val))
                    rec("column.semanticType", "literal", "own")
                elif key == "enum":
                    for e in val or []:
                        g.add(c, "ons:allowedValue", lit(e))
                        rec("column.enum", "literal", "own")
                elif key == "unit":
                    u, std = unit_node(g, val)
                    g.add(c, "ons:unit", u)
                    rec("column.unit", "semantic", "standard" if std else "own")
                elif key == "castExpression":
                    g.add(c, "ons:castExpression", lit(val))
                    rec("column.castExpression", "literal", "own")
                elif key in ("availableFrom", "coverageNote"):
                    g.add(c, f"ons:{key}", lit(val))
                    rec(f"column.{key}", "literal", "own")
                else:
                    rec(f"column.{key}", "dropped")

    # semantics block
    if sem.get("temporal_perspective"):
        g.add(ds, "ons:hasPerspective", concept(g, "perspective", "ons:Perspective", sem["temporal_perspective"]))
        rec("semantics.temporal_perspective", "semantic", "own")
    if sem.get("family"):
        fam = concept(g, "family", "ons:Family", sem["family"])
        g.add(ds, "ons:inFamily", fam)
        rec("semantics.family", "semantic", "own")
        if sem.get("family_default"):
            g.add(ds, "ons:defaultOfFamily", fam)
            rec("semantics.family_default", "semantic", "own")
    for t in sem.get("measure_terms") or []:
        g.add(ds, "skos:hiddenLabel", lit(t, "pt"))
        rec("semantics.measure_terms", "semantic", "standard")

    rg = sem.get("row_grain") or {}
    ent = norm(rg.get("entity"))
    if "entity" in rg:
        if ent in NO_ENTITY:
            rec("row_grain.entity", "n/a")
        elif ent in ENTITIES:
            g.add(ds, "ons:rowEntity", f"ons:{ENTITIES[ent]}")
            rec("row_grain.entity", "semantic", "own")
        else:
            g.add(ds, "rdfs:comment", lit(f"row entity: {rg['entity']}"))
            rec("row_grain.entity", "literal", "standard")
    sp = norm(rg.get("spatial"))
    if sp:
        if sp in ENTITIES:
            g.add(ds, "ons:spatialGrain", f"ons:{ENTITIES[sp]}")
            rec("row_grain.spatial", "semantic", "own")
        else:
            rec("row_grain.spatial", "dropped")
    if rg.get("temporal"):
        g.add(ds, "ons:temporalResolution", lit(norm(rg["temporal"])))
        rec("row_grain.temporal", "literal", "standard")
    if rg.get("temporal_column"):
        tc = col_iri.get(rg["temporal_column"])
        if tc:
            g.add(ds, "ons:timeColumn", tc)
            rec("row_grain.temporal_column", "semantic", "own")
        else:
            rec("row_grain.temporal_column", "dropped")
    for k in rg.get("one_row_per") or []:
        if k in col_iri:
            g.add(ds, "ons:rowKey", col_iri[k])
            rec("row_grain.one_row_per", "semantic", "own")
        else:
            rec("row_grain.one_row_per", "dropped")
    if "has_total_row" in rg:
        rec("row_grain.has_total_row", "dropped")

    for role, col in (sem.get("dimension_columns") or {}).items():
        if not col:
            rec("dimension_columns", "n/a")
            continue
        d = iri("dimension", name, role)
        g.add(ds, "ons:hasDimension", d)
        g.add(d, "a", "ons:Dimension")
        g.add(d, "skos:prefLabel", lit(role))
        if col in col_iri:
            g.add(d, "ons:onColumn", col_iri[col])
        if norm(role) in ENTITIES:
            g.add(d, "ons:refersTo", f"ons:{ENTITIES[norm(role)]}")
        rec("dimension_columns", "semantic", "standard")

    metric_iri: dict[str, str] = {}
    for m in sem.get("metrics") or []:
        mi = iri("metric", name, m.get("id"))
        metric_iri[m.get("id")] = mi
        g.add(ds, "ons:hasMetric", mi)
        g.add(mi, "a", "ons:Metric")
        rec("metric.id", "semantic", "standard")
        if m.get("label"):
            g.add(mi, "skos:prefLabel", lit(m["label"], "pt"))
            rec("metric.label", "semantic", "standard")
        if m.get("description"):
            g.add(mi, "dct:description", lit(m["description"], "pt"))
            rec("metric.description", "literal", "standard")
        for a in m.get("aliases") or []:
            g.add(mi, "skos:altLabel", lit(a, "pt"))
            rec("metric.aliases", "semantic", "standard")
        for c in m.get("columns") or []:
            if c in col_iri:
                g.add(mi, "ons:computedFrom", col_iri[c])
                rec("metric.columns", "semantic", "own")
            else:
                rec("metric.columns", "dropped")
        if m.get("expression"):
            g.add(mi, "ons:expression", lit(m["expression"]))
            rec("metric.expression", "literal", "own")
        if m.get("quantity_kind"):
            qk = concept(g, "quantitykind", "ons:QuantityKind", m["quantity_kind"])
            match = QUANTITY_KINDS.get(norm(m["quantity_kind"]))
            if match:
                g.add(qk, "skos:closeMatch", f"<{QUDT_QK}{match}>")
            g.add(mi, "ons:quantityKind", qk)
            rec("metric.quantity_kind", "semantic", "standard" if match else "own")
        if m.get("unit_native"):
            u, std = unit_node(g, m["unit_native"])
            g.add(mi, "ons:unit", u)
            rec("metric.unit_native", "semantic", "standard" if std else "own")
        if m.get("default_aggregation"):
            a = norm(m["default_aggregation"])
            g.add(mi, "ons:defaultAggregation", concept(g, "aggregation", "ons:Aggregation", AGGREGATIONS.get(a, a)))
            rec("metric.default_aggregation", "semantic", "own")
        forb = [AGGREGATIONS.get(norm(f), norm(f)) for f in m.get("forbidden_aggregations") or []]
        for f in forb:
            g.add(mi, "ons:forbiddenAggregation", concept(g, "aggregation", "ons:Aggregation", f))
            rec("metric.forbidden_aggregations", "semantic", "own")
        if "forbidden_aggregations" in m:
            g.add(mi, "ons:timeAdditive", lit("false" if "sum" in forb else "true", dtype="boolean"))
        if m.get("interval_hours") is not None:
            g.add(mi, "ons:intervalHours", lit(m["interval_hours"], dtype="decimal"))
            rec("metric.interval_hours", "semantic", "own")
        if m.get("aggregation_modes"):
            g.add(mi, "ons:aggregationSQL", lit(json.dumps(m["aggregation_modes"], ensure_ascii=False)))
            rec("metric.aggregation_modes", "literal", "own", len(m["aggregation_modes"]))

    qr = sem.get("query_routing") or {}
    for k, v in qr.items():
        if k == "primary_use_case" and v:
            g.add(ds, "ons:primaryUseCase", concept(g, "usecase", "ons:UseCase", v))
            rec("query_routing", "semantic", "own")
        elif k.startswith("supports_"):
            if v:
                g.add(ds, "ons:supportsUseCase", concept(g, "usecase", "ons:UseCase", k[len("supports_"):]))
            rec("query_routing", "semantic", "own")
        else:
            rec("query_routing", "dropped")

    for col, values in (sem.get("value_aliases") or {}).items():
        if not isinstance(values, dict):
            rec("value_aliases", "dropped")
            continue
        for code, spec in values.items():
            va = iri("value", name, col, code)
            g.add(ds, "ons:hasValueAlias", va)
            g.add(va, "a", "ons:ValueAlias")
            g.add(va, "skos:notation", lit(code))
            if col in col_iri:
                g.add(va, "ons:onColumn", col_iri[col])
            spec = spec if isinstance(spec, dict) else {"aliases": spec if isinstance(spec, list) else [spec]}
            for a in spec.get("aliases") or []:
                g.add(va, "skos:altLabel", lit(a, "pt"))
            if spec.get("filter"):
                g.add(va, "ons:filter", lit(spec["filter"]))
            rec("value_aliases", "semantic", "standard")

    def template(kind: str, key, spec: dict, construct: str) -> None:
        t = iri("template", name, f"{kind}_{key}")
        g.add(ds, "ons:hasTemplate", t)
        g.add(t, "a", "ons:QueryTemplate")
        g.add(t, "skos:prefLabel", lit(str(key)))
        sql = spec.get("template") or spec.get("sql")
        if sql:
            g.add(t, "ons:sqlTemplate", lit(sql))
        if spec.get("question"):
            g.add(t, "ons:exampleQuestion", lit(spec["question"], "pt"))
        if spec.get("output_unit"):
            g.add(t, "ons:outputUnitLabel", lit(spec["output_unit"]))
        for mid in (spec.get("matches") or {}).get("metrics") or []:
            if mid in metric_iri:
                g.add(t, "ons:usesMetric", metric_iri[mid])
        rec(construct, "literal", "own")

    for key, spec in (sem.get("sql_patterns") or {}).items():
        if isinstance(spec, dict):
            template("pattern", key, spec, "sql_patterns")
        else:
            rec("sql_patterns", "dropped")
    for i, fs in enumerate(cp.get("fewShotQueries") or []):
        if isinstance(fs, dict):
            template("fewshot", i, fs, "fewShotQueries")

    known = {"temporal_perspective", "family", "family_default", "measure_terms", "row_grain",
             "dimension_columns", "metrics", "query_routing", "value_aliases", "sql_patterns"}
    for k in sem:
        if k not in known:
            rec(f"semantics.{k}", "dropped")
    known_cp = {"vectorTags", "typicalQuestions", "aiContext", "agentInstructions", "granularity",
                "spatialGranularity", "sourceExpression", "license", "temporalCoverageStart",
                "temporalCoverageEnd", "partitionPruning", "fewShotQueries", "semantics"}
    for k in cp:
        if k not in known_cp:
            rec(f"customProperties.{k}", "dropped")


def main() -> int:
    g, L = Graph(), Ledger()
    files = sorted(CONTRACTS.glob("*.odcs.yaml"))
    for f in files:
        migrate(yaml.safe_load(f.read_text(encoding="utf-8")), g, L)
    OUT_TTL.write_text(g.dump(), encoding="utf-8")
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, check=True).stdout.strip()
    except Exception:
        commit = "unknown"
    types = Counter(o for pos in g.nodes.values() for p, o in pos if p == "a")
    OUT_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    OUT_LEDGER.write_text(json.dumps({
        "corpus_commit": commit, "contracts": len(files), "triples": g.triples(),
        "nodes_by_class": dict(types.most_common()),
        "rows": [{"contract": c, "construct": k, "level": lv, "vocab": v, "n": n}
                 for (c, k, lv, v), n in sorted(L.rows.items())],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(files)} contracts -> {g.triples()} triples, {len(g.nodes)} nodes")
    print("nodes by class:", dict(types.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

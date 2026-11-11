# -*- coding: utf-8 -*-
"""Catalogues for each study condition, all consumed by the SAME production router.

The router (mcp_tiago_dados_abertos.catalogo.ranking.rank_datasets) is never
modified. What changes between conditions is only the catalogue it is given:

  schema      name + column names/types only (floor)
  contracts   the catalogue the server builds from the ODCS contracts (production)
  onto_sem    rebuilt from the ontology, SEMANTIC content only: typed nodes and
              labels; no free text, no example questions, no SQL
  onto_full   rebuilt from the ontology, semantic + literal content

onto_* are reconstructed from study/ontology/abox.json, never from the
contracts, so a difference between `contracts` and `onto_full` is a real
round-trip loss of the migration.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ABOX = ROOT / "study" / "ontology" / "abox.json"
TBOX = ROOT / "study" / "ontology" / "tbox.ttl"
DATA = "https://w3id.org/tiago-ons/data/"


def norm(text) -> str:
    s = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


# ── graph access ────────────────────────────────────────────────────────────
class Graph:
    def __init__(self, path: Path = ABOX):
        self.n: dict[str, list] = json.loads(path.read_text(encoding="utf-8"))

    def vals(self, s: str, p: str) -> list[str]:
        return [v for q, _, v, _ in self.n.get(s, []) if q == p]

    def one(self, s: str, p: str, default=""):
        v = self.vals(s, p)
        return v[0] if v else default

    def label(self, s: str) -> str:
        return self.one(s, "skos:prefLabel", s.rsplit("/", 1)[-1].replace("ons:", ""))

    def datasets(self) -> list[str]:
        return [s for s, rows in self.n.items() if ["a", "ref", "ons:Dataset", ""] in rows]


def schema_only(catalog: dict) -> dict:
    """Floor: what a plain schema catalogue would know."""
    out = {}
    for key, m in catalog.items():
        out[key] = {
            "orgao": m.get("orgao"), "name": m["name"], "status": m.get("status", "active"),
            "columns": [{"name": c.get("name"), "type": c.get("type")} for c in m.get("columns", [])],
        }
    return out


def from_ontology(g: Graph, literals: bool) -> dict:
    """Rebuild a router catalogue from the ontology graph."""
    out = {}
    for ds in g.datasets():
        name = g.one(ds, "dct:title")
        cols = []
        for c in g.vals(ds, "ons:hasColumn"):
            unit = g.one(c, "ons:unit")
            col = {
                "name": g.one(c, "dct:title"), "type": g.one(c, "ons:physicalType") if literals else "",
                "aliases": g.vals(c, "skos:altLabel"), "unit": g.label(unit) if unit else "",
            }
            if literals:
                col["description"] = g.one(c, "dct:description")
                col["semantic_type"] = g.one(c, "ons:semanticRole")
                col["enum"] = g.vals(c, "ons:allowedValue")
            cols.append(col)
        routing = {f"supports_{g.label(u)}": True for u in g.vals(ds, "ons:supportsUseCase")}
        routing.update({f"supports_{g.label(u)}": False for u in g.vals(ds, "ons:doesNotSupportUseCase")})
        primary = g.one(ds, "ons:primaryUseCase")
        if primary:
            routing["primary_use_case"] = g.label(primary)
            routing.pop(f"supports_{g.label(primary)}", None)
        family = g.one(ds, "ons:inFamily")
        ent = g.one(ds, "ons:rowEntity")
        spat = g.vals(ds, "ons:spatialGrain")
        sem = {
            "temporal_perspective": g.label(g.one(ds, "ons:hasPerspective")) if g.one(ds, "ons:hasPerspective") else "",
            "family": g.label(family) if family else None,
            "family_default": bool(g.vals(ds, "ons:defaultOfFamily")),
            "measure_terms": g.vals(ds, "skos:hiddenLabel"),
            "query_routing": routing,
            "row_grain": {"entity": norm(g.label(ent)) if ent else "dataset_row",
                          "temporal": g.one(ds, "ons:temporalResolution") if literals else ""},
        }
        status = g.one(ds, "ons:hasStatus")
        meta = {
            "orgao": "ons", "name": name, "status": g.label(status) if status else "active",
            "tags": g.vals(ds, "dcat:keyword"), "vector_tags": g.vals(ds, "skos:altLabel"),
            "spatial_granularity": norm(g.label(spat[0])) if spat else "",
            "columns": cols, "semantics": sem,
        }
        if literals:
            meta["rag_context"] = g.one(ds, "ons:retrievalContext")
            meta["typical_questions"] = g.vals(ds, "ons:exampleQuestion")
            meta["granularity"] = g.one(ds, "dct:accrualPeriodicity")
            meta["few_shot_queries"] = [{"question": q} for t in g.vals(ds, "ons:hasTemplate")
                                        for q in g.vals(t, "ons:exampleQuestion")]
        else:
            # the router penalises catalogues with no granularity and few columns; a
            # semantic-only catalogue still knows its grain through typed nodes.
            meta["granularity"] = "typed" if (ent or spat) else ""
        out[f"ons/{name}"] = meta
    return out


# ── what the ontology adds on top of the contracts (condition contracts+onto) ─
DEPTH = {"SIN": 0, "Subsistema": 1, "Bacia": 1, "Linha": 1, "REE": 2, "Usina": 2, "Subestacao": 2,
         "Agente": 2, "Reservatorio": 3, "UsinaHidreletrica": 3}


class GrainResolver:
    """Family-grain disambiguation, with no tunable weight.

    The contracts describe each dataset in isolation. The ontology knows that
    several datasets belong to one Family and differ only by spatial grain, and
    which words denote each grain (entity labels from the T-Box, coded values
    from the A-Box). When the router's best answer belongs to a family and the
    question names a grain, family members at that grain are moved ahead of
    their siblings. Positions held by other datasets are not touched.
    """

    def __init__(self, g: Graph):
        self.family, self.grain = {}, {}
        self.alias: dict[str, str] = {}
        tbox = TBOX.read_text(encoding="utf-8")
        for m in re.finditer(r"ons:(\w+)\s+a ons:SpatialEntityType(.*?)\n\n", tbox + "\n\n", re.S):
            for a in re.findall(r'"([^"]+)"@pt', m.group(2)):
                self.alias[norm(a)] = m.group(1)
        for ds in g.datasets():
            name = g.one(ds, "dct:title")
            fam = g.one(ds, "ons:inFamily")
            if fam:
                self.family[name] = fam
            ents = g.vals(ds, "ons:rowEntity") + g.vals(ds, "ons:spatialGrain")
            self.grain[name] = {e.replace("ons:", "") for e in ents}
            col_entity = {}
            for d in g.vals(ds, "ons:hasDimension"):
                ref, col = g.one(d, "ons:refersTo"), g.one(d, "ons:onColumn")
                if ref and col:
                    col_entity[col] = ref.replace("ons:", "")
            for va in g.vals(ds, "ons:hasValueAlias"):
                e = col_entity.get(g.one(va, "ons:onColumn"))
                if e:
                    for a in g.vals(va, "skos:altLabel"):
                        if len(norm(a)) >= 4:
                            self.alias.setdefault(norm(a), e)

    def asked_grain(self, question: str) -> str | None:
        q = f" {re.sub(r'[^a-z0-9]+', ' ', norm(question))} "
        hits = {e for a, e in self.alias.items() if f" {re.sub(r'[^a-z0-9]+', ' ', a).strip()} " in q}
        return max(hits, key=lambda e: DEPTH.get(e, 0)) if hits else None

    def rerank(self, names: list[str], question: str) -> list[str]:
        if not names or names[0] not in self.family:
            return names
        asked = self.asked_grain(question)
        if not asked:
            return names
        fam = self.family[names[0]]
        slots = [i for i, n in enumerate(names) if self.family.get(n) == fam]
        members = [names[i] for i in slots]
        ordered = sorted(members, key=lambda n: asked not in self.grain.get(n, ()))  # stable
        out = list(names)
        for i, n in zip(slots, ordered):
            out[i] = n
        return out

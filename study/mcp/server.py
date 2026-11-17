# -*- coding: utf-8 -*-
"""MCP server that exposes the ONS catalogue FROM THE ONTOLOGY.

Research prototype. It does not touch the production server: it reads
study/ontology/abox.json and tbox.ttl, and reuses the production router only to
rank datasets. It answers what the contracts cannot answer one dataset at a
time: which datasets measure the same thing at another grain, what a metric's
unit is and whether it may be summed along time.

    study/.venv/Scripts/python study/mcp/server.py            # stdio
    ONTO_LITERALS=0 ... server.py                             # semantic content only

Client configuration (stdio):
    {"mcpServers": {"tiago-ontologia": {"command": "<python>", "args": ["study/mcp/server.py"]}}}
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from fastmcp import FastMCP  # noqa: E402

from mcp_tiago_dados_abertos.catalogo import ranking, retrieval  # noqa: E402
from study.routing.catalogs import TBOX, Graph, from_ontology  # noqa: E402

READ_ONLY = {"readOnlyHint": True, "idempotentHint": True, "destructiveHint": False, "openWorldHint": False}
LITERALS = os.environ.get("ONTO_LITERALS", "1") != "0"

mcp = FastMCP("tiago-ontologia")
G = Graph()
CATALOG = from_ontology(G, literals=LITERALS)
BY_NAME = {G.one(ds, "dct:title"): ds for ds in G.datasets()}


def _grain(ds: str) -> list[str]:
    return sorted({e.replace("ons:", "") for e in G.vals(ds, "ons:rowEntity") + G.vals(ds, "ons:spatialGrain")})


def _metric(m: str) -> dict:
    unit, qk = G.one(m, "ons:unit"), G.one(m, "ons:quantityKind")
    additive = G.one(m, "ons:timeAdditive")
    return {
        "id": m.rsplit("/", 1)[-1], "nome": G.one(m, "skos:prefLabel"),
        "apelidos": G.vals(m, "skos:altLabel"),
        "unidade": G.label(unit) if unit else None, "grandeza": G.label(qk) if qk else None,
        "pode_somar_no_tempo": None if additive == "" else additive == "true",
        "agregacao_padrao": G.label(G.one(m, "ons:defaultAggregation")) if G.one(m, "ons:defaultAggregation") else None,
        "agregacoes_proibidas": [G.label(a) for a in G.vals(m, "ons:forbiddenAggregation")],
        "colunas": [G.one(c, "dct:title") for c in G.vals(m, "ons:computedFrom")],
    }


def _family(ds: str) -> list[dict]:
    fam = G.one(ds, "ons:inFamily")
    if not fam:
        return []
    return [{"dataset": G.one(o, "dct:title"), "grao": _grain(o), "referencia_da_familia": bool(G.vals(o, "ons:defaultOfFamily"))}
            for o in G.datasets() if G.one(o, "ons:inFamily") == fam and o != ds]


@mcp.tool(annotations=READ_ONLY)
def buscar_dataset(tema: str) -> str:
    """Procura datasets do ONS por assunto. Devolve até 5 candidatos, do mais ao menos relevante,
    com o grão espacial e a perspectiva temporal de cada um. Passe a pergunta inteira do usuário."""
    retrieval.build_index(CATALOG)
    hits = ranking.rank_datasets(CATALOG, tema, top_n=5)
    out = []
    for _, name, _ in hits:
        ds = BY_NAME[name]
        persp = G.one(ds, "ons:hasPerspective")
        out.append({"dataset": name, "grao": _grain(ds), "perspectiva": G.label(persp) if persp else None,
                    "familia": G.label(G.one(ds, "ons:inFamily")) if G.one(ds, "ons:inFamily") else None})
    return json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
def descrever_dataset(nome: str) -> str:
    """Descreve um dataset a partir da ontologia: o que mede, em que grão, suas métricas (unidade,
    grandeza e se podem ser somadas no tempo), dimensões e os datasets irmãos em outros grãos."""
    ds = BY_NAME.get(nome)
    if not ds:
        return json.dumps({"erro": f"dataset '{nome}' não existe", "sugestao": "use buscar_dataset"}, ensure_ascii=False)
    persp, dom = G.one(ds, "ons:hasPerspective"), G.one(ds, "ons:inDomain")
    out = {
        "dataset": nome, "dominio": G.label(dom) if dom else None, "perspectiva": G.label(persp) if persp else None,
        "grao": _grain(ds), "resolucao_temporal": G.one(ds, "ons:temporalResolution") or None,
        "chave_da_linha": [G.one(c, "dct:title") for c in G.vals(ds, "ons:rowKey")],
        "metricas": [_metric(m) for m in G.vals(ds, "ons:hasMetric")],
        "dimensoes": [{"papel": G.one(d, "skos:prefLabel"), "coluna": G.one(G.one(d, "ons:onColumn"), "dct:title")}
                      for d in G.vals(ds, "ons:hasDimension")],
        "outros_graos_da_mesma_medida": _family(ds),
    }
    if LITERALS:
        out["descricao"] = G.one(ds, "dct:description") or None
        out["limitacoes"] = G.one(ds, "ons:limitation") or None
    return json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
def pode_somar(dataset: str, metrica: str) -> str:
    """Diz se uma métrica pode ser somada ao longo do tempo, e por quê. Use antes de escrever
    SUM sobre uma coluna: estoques e potências médias não se somam no tempo."""
    ds = BY_NAME.get(dataset)
    if not ds:
        return json.dumps({"erro": f"dataset '{dataset}' não existe"}, ensure_ascii=False)
    for m in G.vals(ds, "ons:hasMetric"):
        info = _metric(m)
        if metrica in (info["id"], info["nome"], *info["apelidos"], *info["colunas"]):
            ok = info["pode_somar_no_tempo"]
            return json.dumps({
                "metrica": info["id"], "unidade": info["unidade"], "grandeza": info["grandeza"],
                "pode_somar_no_tempo": ok,
                "motivo": ("o contrato não declara regra de agregação" if ok is None else
                           "SUM é agregação proibida para esta métrica" if not ok else "nenhuma restrição declarada"),
                "use_em_vez_disso": info["agregacao_padrao"] if ok is False else None,
            }, ensure_ascii=False)
    return json.dumps({"erro": f"métrica '{metrica}' não encontrada em {dataset}",
                       "metricas": [_metric(m)["id"] for m in G.vals(ds, "ons:hasMetric")]}, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
def datasets_por_grao(familia: str) -> str:
    """Lista os datasets de uma família de medida (por exemplo 'ear', 'ena', 'carga') com o grão
    espacial de cada um, para escolher entre subsistema, bacia, reservatório, usina."""
    out = [{"dataset": G.one(ds, "dct:title"), "grao": _grain(ds), "referencia_da_familia": bool(G.vals(ds, "ons:defaultOfFamily"))}
           for ds in G.datasets() if G.one(ds, "ons:inFamily") and G.label(G.one(ds, "ons:inFamily")) == familia.strip().lower()]
    if not out:
        fams = sorted({G.label(G.one(ds, "ons:inFamily")) for ds in G.datasets() if G.one(ds, "ons:inFamily")})
        return json.dumps({"erro": f"família '{familia}' não existe", "familias": fams}, ensure_ascii=False)
    return json.dumps(out, ensure_ascii=False)


@mcp.resource("ontologia://esquema", mime_type="text/turtle")
def esquema() -> str:
    """Esquema (T-Box) da ontologia TIAGO-ONS em Turtle."""
    return TBOX.read_text(encoding="utf-8")


if __name__ == "__main__":
    mcp.run()

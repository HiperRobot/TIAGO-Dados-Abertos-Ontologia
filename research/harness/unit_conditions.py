# -*- coding: utf-8 -*-
"""Arms U0/U1/U2 do experimento de unidade e aditividade.

Cada arm recebe (sql, colunas, catalog, ontologia) e devolve, para cada coluna,
um veredito {unit, additive, abstem} — onde abstem=True sinaliza "não sei provar"
(unit=None e additive indecidido). As três leem o MESMO SQL; muda só o
conhecimento usado para julgar.

  U0  heurística por nome de coluna  — proxy de um LLM nu, sem contrato
  U1  contrato (result_schema atual) — código de produção, fail-closed
  U2  contrato + ontologia           — U1, e resgate ontológico SÓ onde U1 abstém

Determinístico, offline.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
for _p in (_RAIZ, _RAIZ / "src"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _norm(t: str) -> str:
    s = unicodedata.normalize("NFKD", (t or "").lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


# ── U0: heurística por NOME da coluna (proxy de LLM nu) ─────────────────────────
# Um julgador sem contrato só tem o nome da coluna e o shape do SQL. Estas regras
# imitam o "chute informado" que um LLM faria: achar a unidade no sufixo do nome e
# assumir que SUM é aditivo. É propositalmente ingênuo — é o baseline.
_UNIDADE_POR_NOME = [
    (r"gwh", "GWh"),
    (r"mwh", "MWh"),
    (r"mwmed|mw_med|mwmedio", "MWmed"),
    (r"mwmes|mw_mes", "MWmes"),
    (r"m3s|m3_s|vazao", "m3/s"),
    (r"rs_mwh|r\$/mwh|preco|cmo|pld|cvu", "R$/MWh"),
    (r"pct|percent|_perc|taxa|fator", "%"),
]


def _u0_unidade(col_nome: str) -> str | None:
    n = _norm(col_nome)
    for pat, unidade in _UNIDADE_POR_NOME:
        if re.search(pat, n):
            return unidade
    return None  # U0 não achou pista => "não sei" (mas sem o rigor de abstenção de U1)


def _u0_additive(col_nome: str, agg: str | None) -> bool:
    # Regra ingênua clássica de LLM: "é uma soma, logo é somável". É exatamente o
    # erro que o contrato evita (somar estoque/preço). Mantida de propósito.
    return (agg or "").upper() == "SUM"


def aggs_do_sql(sql: str, colunas: list[str]) -> dict[str, str]:
    """Detecta o agregador externo de cada coluna-alias no SELECT (p/ U0, sem contrato)."""
    import sqlglot
    from sqlglot import exp
    out: dict[str, str] = {}
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return out
    sel = tree.find(exp.Select)
    if sel is None:
        return out
    for proj in sel.expressions:
        alias = (proj.alias_or_name or "").lower()
        inner = proj.this if isinstance(proj, exp.Alias) else proj
        agg = inner if isinstance(inner, exp.AggFunc) else next(iter(inner.find_all(exp.AggFunc)), None)
        if alias and agg is not None:
            out[alias] = type(agg).__name__.upper()
    return out


def avaliar_u0(sql: str, colunas: list[str], aggs: dict[str, str] | None = None) -> dict:
    """U0 não parseia contrato. Usa nome da coluna + agg detectado no SQL."""
    aggs = aggs if aggs is not None else aggs_do_sql(sql, colunas)
    out = {}
    for c in colunas:
        unidade = _u0_unidade(c)
        out[c] = {
            "unit": unidade,
            "additive": _u0_additive(c, aggs.get(c)),
            # U0 "abstém" só quando nem o nome deu pista de unidade; mas note que,
            # diferente de U1, ele AINDA crava uma aditividade (erro-confiante).
            "abstem": unidade is None,
        }
    return out


# ── U1: contrato (produção) ─────────────────────────────────────────────────────
def avaliar_u1(sql: str, colunas: list[str], catalog: dict) -> dict:
    """Chama o result_schema de produção. Abstenção = unit_tier 'unknown'."""
    from mcp_tiago_dados_abertos.resposta.result_schema import result_schema_do_sql
    rs = result_schema_do_sql(sql, colunas, catalog)
    out = {}
    cols_rs = {c.get("name"): c for c in (rs or {}).get("columns", [])}
    for c in colunas:
        info = cols_rs.get(c, {})
        tier = info.get("unit_tier", "unknown")
        unidade = info.get("unit")
        # abstenção de unidade: tier unknown OU name_hint (tier mais fraco, "palpite")
        abstem_unit = tier in ("unknown", "name_hint") or unidade is None
        out[c] = {
            "unit": None if abstem_unit else unidade,
            "unit_tier": tier,
            "additive": bool(info.get("additive")),
            "role": info.get("role", "unknown"),
            "metric": info.get("metric"),
            "quantity_kind": info.get("quantity_kind"),
            # abstenção de aditividade: role unknown/dimension => não é medida provada
            "abstem": abstem_unit,
            "abstem_additive": info.get("role") in (None, "unknown", "dimension"),
        }
    return out


# ── U2: contrato + ontologia (resgate SÓ onde U1 abstém) ────────────────────────
_ADIT_POR_GRANDEZA = {
    # aditividade_temporal da ontologia -> additive (coluna crua)
    "additive": True,   # energia, fluxo: soma no tempo é legítima
    "stock": False,     # estoque: somar no tempo conta 2x
    "average": False,   # potência média/preço: agrega por AVG, não SUM
}


def _grandeza_do_dataset(nome_ds: str, onto: dict) -> dict | None:
    """Via binding: dataset -> fenomeno -> grandeza {unidade_nativa, aditividade_temporal}."""
    b = onto.get("dataset_bindings", {}).get(nome_ds)
    if not b or not b.get("mede"):
        return None
    fen = onto.get("fenomenos", {}).get(b["mede"])
    if not fen:
        return None
    return onto.get("grandezas", {}).get(fen.get("grandeza"))


def avaliar_u2(sql: str, colunas: list[str], catalog: dict, onto: dict) -> dict:
    """U1 e, onde U1 abstém, tenta resgatar pela ontologia. Não-destrutivo."""
    from mcp_tiago_dados_abertos.resposta.provenance import datasets_do_sql
    base = avaliar_u1(sql, colunas, catalog)
    # qual(is) dataset(s) o SQL toca -> grandeza candidata pela ontologia
    metas = datasets_do_sql(sql, catalog or {})
    nomes_ds = [m.get("name") for m in metas if isinstance(m, dict) and m.get("name")]
    grandeza = None
    if len(nomes_ds) == 1:  # resgate só quando a origem é inequívoca
        grandeza = _grandeza_do_dataset(nomes_ds[0], onto)

    # COUNT não tem unidade física: resgatar unidade sobre COUNT é sempre erro.
    # A abstenção de unidade de U1 sobre COUNT é CORRETA e deve ser preservada.
    aggs = aggs_do_sql(sql, colunas)

    out = {}
    for c in colunas:
        v = dict(base[c])
        v["resgatado"] = False
        e_count = (aggs.get(c) == "COUNT") or v.get("unit_tier") == "count" \
            or v.get("quantity_kind") == "count"
        if grandeza is not None and not e_count:
            # resgate de UNIDADE: U1 absteve e a coluna parece ser a medida
            if v["abstem"] and v.get("role") in ("measure", "ratio", None, "unknown"):
                # só resgata se a coluna não é claramente dimensão/tempo
                if v.get("role") not in ("dimension", "temporal"):
                    v["unit"] = grandeza.get("unidade_nativa")
                    v["unit_tier"] = "ontologia"
                    v["abstem"] = False
                    v["resgatado"] = True
            # resgate de ADITIVIDADE: onde U1 não decidiu a medida (nunca em COUNT)
            if v.get("abstem_additive"):
                adit = _ADIT_POR_GRANDEZA.get(grandeza.get("aditividade_temporal"))
                if adit is not None and v.get("role") not in ("dimension", "temporal"):
                    v["additive"] = adit
                    v["abstem_additive"] = False
                    v["resgatado"] = True
        out[c] = v
    return out


def avaliar(arm: str, sql: str, colunas: list[str], catalog: dict, onto: dict,
            aggs: dict[str, str] | None = None) -> dict:
    """Dispatcher por arm."""
    if arm == "U0":
        return avaliar_u0(sql, colunas, aggs)  # aggs=None => detecta do SQL
    if arm == "U1":
        return avaliar_u1(sql, colunas, catalog)
    if arm == "U2":
        return avaliar_u2(sql, colunas, catalog, onto)
    raise ValueError(f"arm desconhecido: {arm}")

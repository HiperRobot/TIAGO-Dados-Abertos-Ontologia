# -*- coding: utf-8 -*-
"""Runner do experimento de unidade/aditividade: U0 vs U1 vs U2 sobre um gold set.

Para cada coluna-medida do gold, compara o veredito de cada arm com o gabarito e
classifica o desfecho em: correto / errado-confiante / abstenção. Reporta acurácia
de unidade e de aditividade, a decomposição, o resgate de U2, e McNemar pareado.

Uso:
    python -m research.harness.run_unidade \
        --gold research/harness/GOLD_UNIDADE.example.json \
        --ontologia research/ontologia/ons_ontologia.json \
        --saida research/harness/rodada_unidade.json

Determinístico, offline (só parseia SQL + lê contratos).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
for _p in (_RAIZ, _RAIZ / "src"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from research.harness import metrics as M  # noqa: E402
from research.harness import unit_conditions as UC  # noqa: E402


def _norm_unit(u):
    return None if u is None else str(u).strip().lower()


def _desfecho_unidade(julgado: dict, esperado_unit) -> str:
    """correto / errado / abstencao, para a UNIDADE de uma coluna."""
    ju = _norm_unit(julgado.get("unit"))
    gu = _norm_unit(esperado_unit)
    if julgado.get("abstem") or ju is None:
        # abstenção só conta como tal se o gold TEM uma unidade a acertar;
        # se o gold também é None (ex.: COUNT), abster = acertar o "sem unidade"
        return "abstencao" if gu is not None else "correto"
    return "correto" if ju == gu else "errado"


def _desfecho_additive(julgado: dict, esperado_add, arm: str) -> str:
    """correto / errado / abstencao, para a ADITIVIDADE."""
    # U1/U2 marcam abstem_additive quando a coluna não é medida provada
    if arm in ("U1", "U2") and julgado.get("abstem_additive"):
        return "abstencao"
    return "correto" if bool(julgado.get("additive")) == bool(esperado_add) else "errado"


def _avaliar_arm(arm, gold, cat, onto):
    """Roda o arm em todos os itens; devolve listas de desfecho por coluna-medida."""
    unidade, aditiv = [], []
    acerto_unit_bin, acerto_add_bin = [], []  # p/ McNemar (True=correto)
    resgates = {"unit": 0, "add": 0, "unit_erro": 0, "add_erro": 0}
    detalhes = []
    for item in gold:
        sql = item["sql"]
        cols = item["colunas"]
        esperado = item.get("esperado", {})
        res = UC.avaliar(arm, sql, cols, cat, onto)
        for col, exp_val in esperado.items():
            v = res.get(col, {})
            du = _desfecho_unidade(v, exp_val.get("unit"))
            da = _desfecho_additive(v, exp_val.get("additive"), arm)
            unidade.append(du)
            aditiv.append(da)
            acerto_unit_bin.append(du == "correto")
            acerto_add_bin.append(da == "correto")
            # contabiliza resgate de U2
            if arm == "U2" and v.get("resgatado"):
                if du == "correto":
                    resgates["unit"] += 1
                elif du == "errado":
                    resgates["unit_erro"] += 1
                if da == "correto":
                    resgates["add"] += 1
                elif da == "errado":
                    resgates["add_erro"] += 1
            detalhes.append({"item": item.get("id"), "col": col, "arm": arm,
                             "unit": du, "additive": da, "julgado_unit": v.get("unit"),
                             "resgatado": v.get("resgatado", False)})
    return {
        "unidade": unidade, "aditividade": aditiv,
        "acerto_unit": acerto_unit_bin, "acerto_add": acerto_add_bin,
        "resgates": resgates, "detalhes": detalhes,
    }


def _resumo(lista):
    n = len(lista)
    if n == 0:
        return {"n": 0}
    from collections import Counter
    c = Counter(lista)
    return {"n": n, "correto": c["correto"] / n, "errado": c["errado"] / n,
            "abstencao": c["abstencao"] / n,
            "correto_abs": c["correto"], "errado_abs": c["errado"], "abstencao_abs": c["abstencao"]}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Experimento unidade/aditividade U0/U1/U2")
    ap.add_argument("--gold", required=True, type=Path)
    ap.add_argument("--ontologia", required=True, type=Path)
    ap.add_argument("--saida", type=Path, default=None)
    ap.add_argument("--arms", nargs="+", default=["U0", "U1", "U2"])
    args = ap.parse_args()

    raw = json.loads(args.gold.read_text(encoding="utf-8"))
    gold = raw.get("itens", raw if isinstance(raw, list) else [])
    onto = json.loads(args.ontologia.read_text(encoding="utf-8"))
    bind = args.ontologia.parent / "bindings_derivados.json"
    if bind.exists():
        onto["dataset_bindings"] = json.loads(bind.read_text(encoding="utf-8-sig")).get("dataset_bindings", {})

    from mcp_tiago_dados_abertos.catalogo.contracts import load_all_contracts
    cat = load_all_contracts()

    n_medidas = sum(len(it.get("esperado", {})) for it in gold)
    print(f"gold: {len(gold)} consultas, {n_medidas} colunas-medida | catálogo: {len(cat)} datasets\n")

    res = {arm: _avaliar_arm(arm, gold, cat, onto) for arm in args.arms}

    print("== Acurácia de UNIDADE (correto / errado-confiante / abstenção) ==")
    for arm in args.arms:
        r = _resumo(res[arm]["unidade"])
        print(f"  {arm}: correto={r['correto']:.3f}  errado={r['errado']:.3f}  abstencao={r['abstencao']:.3f}")

    print("\n== Acurácia de ADITIVIDADE ==")
    for arm in args.arms:
        r = _resumo(res[arm]["aditividade"])
        print(f"  {arm}: correto={r['correto']:.3f}  errado={r['errado']:.3f}  abstencao={r['abstencao']:.3f}")

    if "U2" in res:
        rg = res["U2"]["resgates"]
        print(f"\n== Resgate ontológico de U2 (sobre abstenções de U1) ==")
        print(f"  unidade: {rg['unit']} acertos, {rg['unit_erro']} erros")
        print(f"  aditividade: {rg['add']} acertos, {rg['add_erro']} erros")

    print("\n== McNemar pareado (acerto de unidade, top) ==")
    pares = []
    for a, b in [(args.arms[i], args.arms[j]) for i in range(len(args.arms)) for j in range(i + 1, len(args.arms))]:
        mc = M.mcnemar(res[a]["acerto_unit"], res[b]["acerto_unit"], a, b)
        print(f"  unidade {a} vs {b}: {mc.resumo()}")
        mca = M.mcnemar(res[a]["acerto_add"], res[b]["acerto_add"], a, b)
        print(f"  aditiv. {a} vs {b}: {mca.resumo()}")
        pares.append({"a": a, "b": b, "unidade_p": mc.p_valor, "aditividade_p": mca.p_valor})

    if args.saida:
        dump = {
            "unidade": {arm: _resumo(res[arm]["unidade"]) for arm in args.arms},
            "aditividade": {arm: _resumo(res[arm]["aditividade"]) for arm in args.arms},
            "resgate_u2": res.get("U2", {}).get("resgates"),
            "mcnemar": pares,
            "detalhes": [d for arm in args.arms for d in res[arm]["detalhes"]],
        }
        args.saida.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nresultados salvos em {args.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

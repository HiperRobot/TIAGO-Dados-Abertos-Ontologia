# -*- coding: utf-8 -*-
"""Runner do experimento de roteamento: C0 vs C1 vs C2 sobre um gold set.

Executa as três condições nas MESMAS perguntas, calcula top-k/MRR por condição
e o teste de McNemar pareado entre elas, e imprime os pares de confusão. É o
substituto do `scripts/benchmark_roteamento.py` citado no ROUTING.md (que nunca
existiu) — mas isolado em research/, sem acoplar ao pacote de produção.

Uso:
    python -m research.harness.run_benchmark \
        --gold research/harness/GOLDEN_ROUTING.example.json \
        --ontologia research/ontologia/ons_ontologia.json \
        --saida research/harness/rodada.json

Requer o pacote instalado (pip install -e ".[dev]") para importar o catálogo.
O roteamento é 100% offline (não toca o S3): só lê os contratos.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# permite rodar como script solto, resolvendo 'research' e o pacote de produção
# (src/) no sys.path, mesmo sem `pip install -e .`.
_RAIZ = Path(__file__).resolve().parents[2]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))
_SRC = _RAIZ / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from research.harness import metrics as M  # noqa: E402
from research.harness.conditions import make_catalog, route  # noqa: E402


def _carregar_gold(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    itens = raw.get("itens", raw if isinstance(raw, list) else [])
    if not itens:
        raise SystemExit(f"gold set vazio ou mal formado: {path}")
    return itens


def _carregar_catalogo() -> dict:
    from mcp_tiago_dados_abertos.catalogo.contracts import load_all_contracts
    cat = load_all_contracts()
    if not cat:
        raise SystemExit("catálogo vazio — rode `pip install -e .` e confira CONTRACTS_DIR")
    return cat


def _nome_de(key_ou_nome: str) -> str:
    """Normaliza 'ons/carga-energia' -> 'carga-energia' (o gold usa só o name)."""
    return key_ou_nome.split("/")[-1]


def _rankear_condicao(catalog_full: dict, gold: list[dict], cond: str, onto: dict | None,
                      pesos: dict | None = None):
    cat = make_catalog(catalog_full, cond)
    predicoes = []
    for item in gold:
        # C2 e C3 precisam da ontologia; C0 e C1 não
        usar_onto = onto if cond in ("C2", "C3") else None
        ranked = route(cat, item["pergunta"], cond, top_n=10, ontologia=usar_onto, pesos=pesos)
        nomes = [_nome_de(nome) for _, nome, _ in ranked]
        predicoes.append((item["esperado"], nomes))
    return predicoes


def main() -> int:
    # Windows: o stdout padrão é cp1252 e quebra nos caracteres de caixa (──, →).
    # Forçar UTF-8 evita o UnicodeEncodeError ao redirecionar a saída para arquivo.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Benchmark de roteamento C0/C1/C2")
    ap.add_argument("--gold", required=True, type=Path)
    ap.add_argument("--ontologia", required=True, type=Path)
    ap.add_argument("--bindings", type=Path, default=None,
                    help="JSON de bindings derivados (gerar_bindings.py). Se omitido, usa a "
                         "cobertura completa ao lado da ontologia, ou a semente embutida.")
    ap.add_argument("--saida", type=Path, default=None)
    ap.add_argument("--condicoes", nargs="+", default=["C0", "C1", "C2", "C3"])
    ap.add_argument("--pesos", type=Path, default=None,
                    help="JSON de pesos calibrados (calibrar.py). Se omitido, usa pesos_boost.json "
                         "ao lado da ontologia se existir, senão os pesos default.")
    args = ap.parse_args()

    gold = _carregar_gold(args.gold)
    onto = json.loads(args.ontologia.read_text(encoding="utf-8"))

    # Pesos do boost (só C2): --pesos > pesos_boost.json ao lado da ontologia > default.
    pesos = None
    pesos_path = args.pesos or (args.ontologia.parent.parent / "harness/pesos_boost.json")
    if pesos_path and pesos_path.exists():
        _pw = json.loads(pesos_path.read_text(encoding="utf-8"))
        pesos = _pw.get("pesos", _pw)  # aceita o dump do calibrador ou um dict puro
        print(f"pesos do boost: calibrados (de {pesos_path.name})")
    else:
        print("pesos do boost: default (não calibrado)")

    # Bindings: preferência explícita --bindings > bindings_derivados.json ao lado
    # da ontologia > semente embutida na própria ontologia. O derivado cobre os 74
    # datasets; a semente só 12. Mantém C2 com o mapa completo e ancorado no corpus.
    bind_path = args.bindings or (args.ontologia.parent / "bindings_derivados.json")
    if bind_path.exists():
        _bl = json.loads(bind_path.read_text(encoding="utf-8-sig"))
        onto["dataset_bindings"] = _bl.get("dataset_bindings", onto.get("dataset_bindings", {}))
        print(f"bindings: {len(onto['dataset_bindings'])} datasets (de {bind_path.name})")
    else:
        print(f"bindings: {len(onto.get('dataset_bindings', {}))} datasets (semente embutida)")

    catalog = _carregar_catalogo()
    print(f"catálogo: {len(catalog)} datasets | gold: {len(gold)} perguntas\n")

    resultados = {}
    for cond in args.condicoes:
        # C2 e C3 precisam da ontologia; pesos só C2
        usar_onto = onto if cond in ("C2", "C3") else None
        usar_pesos = pesos if cond == "C2" else None
        pred = _rankear_condicao(catalog, gold, cond, usar_onto, pesos=usar_pesos)
        res = M.avaliar(pred)
        resultados[cond] = res
        print(f"[{cond}] {res.resumo()}")

    # Comparações pareadas (todas as combinações adjacentes + C1 vs C2 que é a tese)
    print("\n── Comparações pareadas (McNemar exato, top-1) ──")
    pares = []
    conds = args.condicoes
    for i in range(len(conds)):
        for j in range(i + 1, len(conds)):
            a, b = conds[i], conds[j]
            mc = M.mcnemar(resultados[a].acertos_top1, resultados[b].acertos_top1, a, b)
            print(f"{a} vs {b}: {mc.resumo()}")
            pares.append({"a": a, "b": b, "b_count": mc.b, "c_count": mc.c,
                          "p_valor": mc.p_valor, "direcao": mc.direcao})

    # Pares de confusão da melhor condição semântica
    print("\n── Pares de confusão (C2 se presente, senão C1) ──")
    alvo = "C2" if "C2" in resultados else ("C1" if "C1" in resultados else conds[-1])
    for (esp, dev), qtd in M.top_pares_confusao(resultados[alvo], k=10):
        print(f"  {qtd}x  {esp}  →  {dev}")

    if args.saida:
        dump = {
            "condicoes": {c: {"top1": r.top1, "top3": r.top3, "top5": r.top5,
                              "mrr": r.mrr, "fora_top10": r.fora_top10, "n": r.n}
                          for c, r in resultados.items()},
            "mcnemar": pares,
            "confusao_alvo": {f"{e}->{d}": q
                              for (e, d), q in M.top_pares_confusao(resultados[alvo], 20)},
        }
        args.saida.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nresultados salvos em {args.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

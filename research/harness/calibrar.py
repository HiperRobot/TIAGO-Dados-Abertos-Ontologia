# -*- coding: utf-8 -*-
"""Calibra os pesos do boost ontológico (C2) no DEV set. NUNCA toca o teste.

Procura a combinação de pesos que maximiza MRR (ou top-1) no GOLDEN_DEV.json e
grava em pesos_boost.json. O runner carrega esse arquivo se existir.

Eficiência: o ranking BM25 de C1 não depende dos pesos. Rankeamos C1 UMA vez por
pergunta (com profundidade generosa) e cacheamos; cada combinação de pesos só
reaplica o re-ranking ontológico sobre o ranking cacheado. Assim o grid search
não reconstrói o índice BM25 a cada ponto.

Busca: coordinate ascent (sobe um peso de cada vez até estabilizar). Mais barato
que grid completo e suficiente — o espaço é suave e os pesos quase independentes.

Uso:
    python -m research.harness.calibrar \
        --dev research/harness/GOLDEN_DEV.json \
        --ontologia research/ontologia/ons_ontologia.json \
        --saida research/harness/pesos_boost.json \
        --metrica mrr
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

from research.harness import ontology_boost as OB  # noqa: E402
from research.harness import metrics as M  # noqa: E402

# Grade de valores candidatos por peso. Sinais positivos crescem; penalidades
# descem. Mantidos na faixa dos sinais de C1 (±10) para não dominar o BM25.
GRADE = {
    "fenomeno_match":      [0, 4, 6, 8, 10, 12],
    "fenomeno_conflito":   [0, -4, -6, -8, -10, -12],
    "grao_exato":          [0, 3, 5, 7, 9],
    "grao_ancestral":      [0, -2, -3, -5, -8],
    "grao_descendente":    [0, 1, 2, 3],
    "grao_incompativel":   [0, -3, -6, -9],
    "perspectiva_match":   [0, 2, 4, 6],
    "perspectiva_conflito":[0, -2, -4, -6],
}


def _nome(k: str) -> str:
    return k.split("/")[-1]


def _rankings_base(gold, ontologia):
    """Rankeia C1 UMA vez por pergunta (com meta p/ reaplicar boost). Cache.

    Retorna lista de (esperado, ranked_base), onde ranked_base é [(score,nome,meta),...]
    já com nome NORMALIZADO para o 'name' do dataset — e o boost reaplicado por cima.
    """
    from research.harness.conditions import make_catalog
    from mcp_tiago_dados_abertos.catalogo import ranking as _ranking
    from mcp_tiago_dados_abertos.catalogo import retrieval as _retrieval
    from mcp_tiago_dados_abertos.catalogo.contracts import load_all_contracts

    catalog = load_all_contracts()
    cat_c1 = make_catalog(catalog, "C1")
    _retrieval.build_index(cat_c1)  # uma vez
    base = []
    for item in gold:
        ranked = _ranking.rank_datasets(cat_c1, item["pergunta"], top_n=10)
        base.append((item["esperado"], item["pergunta"], ranked))
    return base


def _avaliar_pesos(base, ontologia, pesos) -> M.ResultadoRoteamento:
    """Reaplica o boost com `pesos` sobre o ranking base cacheado e mede."""
    predicoes = []
    for esperado, pergunta, ranked in base:
        reranked = OB.aplicar_boost(ranked, pergunta, ontologia, top_n=10, pesos=pesos)
        nomes = [_nome(n) for _, n, _ in reranked]
        predicoes.append((esperado, nomes))
    return M.avaliar(predicoes)


def _score(res: M.ResultadoRoteamento, metrica: str) -> float:
    return res.mrr if metrica == "mrr" else res.top1


def coordinate_ascent(base, ontologia, metrica: str, max_passes: int = 3):
    """Sobe um peso de cada vez; repete até nenhum peso melhorar. Determinístico."""
    atual = dict(OB.PESOS_DEFAULT)
    melhor_score = _score(_avaliar_pesos(base, ontologia, atual), metrica)
    historico = [("inicial", dict(atual), melhor_score)]

    for passe in range(max_passes):
        mudou = False
        for chave, candidatos in GRADE.items():
            melhor_val = atual[chave]
            for v in candidatos:
                if v == atual[chave]:
                    continue
                tentativa = dict(atual)
                tentativa[chave] = v
                s = _score(_avaliar_pesos(base, ontologia, tentativa), metrica)
                # desempate: prefere mudança que melhora; em empate, mantém o atual
                if s > melhor_score + 1e-9:
                    melhor_score = s
                    melhor_val = v
                    mudou = True
            if melhor_val != atual[chave]:
                atual[chave] = melhor_val
                historico.append((chave, dict(atual), melhor_score))
        if not mudou:
            break
    return atual, melhor_score, historico


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Calibra pesos do boost no DEV set")
    ap.add_argument("--dev", required=True, type=Path)
    ap.add_argument("--ontologia", required=True, type=Path)
    ap.add_argument("--saida", type=Path, default=_RAIZ / "research/harness/pesos_boost.json")
    ap.add_argument("--metrica", choices=["mrr", "top1"], default="mrr")
    args = ap.parse_args()

    raw = json.loads(args.dev.read_text(encoding="utf-8"))
    gold = raw.get("itens", raw if isinstance(raw, list) else [])
    onto = json.loads(args.ontologia.read_text(encoding="utf-8"))
    bind = args.ontologia.parent / "bindings_derivados.json"
    if bind.exists():
        onto["dataset_bindings"] = json.loads(bind.read_text(encoding="utf-8-sig")).get("dataset_bindings", {})

    print(f"DEV: {len(gold)} perguntas | otimizando {args.metrica}")
    base = _rankings_base(gold, onto)

    # linha de base: C1 puro (sem boost) e C2 com pesos default
    c1 = M.avaliar([(e, [_nome(n) for _, n, _ in r]) for e, _, r in base])
    default = _avaliar_pesos(base, onto, OB.PESOS_DEFAULT)
    print(f"C1 (sem boost)      : top1={c1.top1:.3f} mrr={c1.mrr:.3f}")
    print(f"C2 (pesos default)  : top1={default.top1:.3f} mrr={default.mrr:.3f}")

    melhores, score, hist = coordinate_ascent(base, onto, args.metrica)
    res = _avaliar_pesos(base, onto, melhores)
    print(f"C2 (pesos calibrados): top1={res.top1:.3f} mrr={res.mrr:.3f}")
    print(f"\npesos calibrados: {json.dumps(melhores, ensure_ascii=False)}")

    dump = {
        "pesos": melhores,
        "metrica_otimizada": args.metrica,
        "dev_n": len(gold),
        "dev_c1": {"top1": c1.top1, "mrr": c1.mrr},
        "dev_c2_default": {"top1": default.top1, "mrr": default.mrr},
        "dev_c2_calibrado": {"top1": res.top1, "mrr": res.mrr},
        "aviso": "pesos calibrados no DEV; reportar APENAS métricas de teste no paper",
    }
    args.saida.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"gravado em {args.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

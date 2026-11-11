# -*- coding: utf-8 -*-
"""Gera CANDIDATOS a gold set de roteamento a partir dos contratos — para revisão humana.

NÃO produz um gold set final automaticamente. Produz candidatos rotulados que um
humano revisa, corrige e aprova. O objetivo é dar partida na anotação sem partir
do zero, preservando as duas invariantes de validade do experimento:

  1. PROVENIÊNCIA. Cada pergunta carrega `fonte`:
       - "contrato"  : veio literal de typicalQuestions/fewShotQueries do dataset.
                       ESTAS PERGUNTAS ESTÃO NO ÍNDICE BM25 (ver retrieval._documento).
                       Usá-las no teste de C1/C2 é VAZAMENTO. Servem para o DEV set
                       (calibração do boost) ou como material para parafrasear.
       - "sintetica" : derivada por transformação (coloquializar, trocar período,
                       variar entidade). NÃO está no índice. Elegível para o TESTE.
     O gold final de teste deve conter SOMENTE fonte="sintetica" (ou perguntas
     escritas à mão). O harness não impõe isso — a disciplina é do pesquisador.

  2. CATEGORIA. Classifica cada pergunta pelo que a ontologia detecta (fenômeno,
     grão, perspectiva) + heurística de intenção, para o gold ser estratificável
     (ver PROTOCOLO §4.1). Categorias difíceis (confusão) são o alvo do estudo.

Saída: JSONL (uma pergunta por linha), fácil de revisar linha a linha e versionar.

Uso:
    python -m research.harness.gerar_gold_candidatos \
        --ontologia research/ontologia/ons_ontologia.json \
        --saida research/harness/gold_candidatos.jsonl \
        --por-dataset 4
"""
from __future__ import annotations

import argparse
import json
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


# ── Transformações para gerar perguntas SINTÉTICAS (fora do índice BM25) ────────
# Coloquializações: substituições de superfície que preservam a intenção mas
# afastam a pergunta do texto literal do contrato.
_COLOQUIAL = [
    (r"\bqual foi\b", "qual"),
    (r"\bqual é\b", "qual"),
    (r"\bapresentou\b", "teve"),
    (r"\bde energia\b", ""),
    (r"\bno subsistema\b", "no"),
    (r"\bno período de\b", "em"),
    (r"\bao longo\b", "durante"),
]


def _coloquializar(pergunta: str) -> str | None:
    """Aplica substituições de superfície. Retorna None se nada mudou."""
    out = pergunta
    for pat, rep in _COLOQUIAL:
        out = re.sub(pat, rep, out, flags=re.IGNORECASE)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out if _norm(out) != _norm(pergunta) else None


# ── Classificação de categoria ─────────────────────────────────────────────────
_INVENTARIO = {"quais", "quantos", "quantas", "lista", "listar", "existem", "cadastro", "inventario"}
_RANKING = {"maior", "menor", "top", "ranking", "pico", "maximo", "minimo", "mais", "menos"}
_SERIE = {"evolucao", "evoluiu", "historico", "tendencia", "serie", "ultimos", "desde", "variou"}
_ANO_RE = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")


def _categoria(pergunta: str, onto: dict, binding: dict | None) -> str:
    """Categoria estratificadora. Prioriza os eixos difíceis que o estudo investiga."""
    from research.harness.ontology_boost import explicar
    pnorm = _norm(pergunta)
    tokens = set(re.findall(r"\b\w+\b", pnorm))
    det = explicar(pergunta, onto)

    if tokens & _INVENTARIO and not _ANO_RE.search(pergunta):
        return "inventario"
    if det["perspectiva"] in ("Programado", "Previsto"):
        return "perspectiva_futura"
    # grão fino é terreno dos pares de confusão de grão — MAS só quando a palavra de
    # grão é uma ENTIDADE ESPACIAL (reservatório/bacia como lugar), não um TIPO de
    # usina (eólica/solar são fonte, não grão). Evita falso positivo de "geração
    # eólica no subsistema X" cair em confusao_grao.
    _ENTIDADE_GRAO_FINO = ("reservatorio", "represa", "acude", "bacia", "ree",
                           "reservatorio equivalente", "usina ", "usinas ", "planta")
    if det["grao"] in ("Reservatorio", "Usina", "Bacia", "REE") and \
            any(e in f" {pnorm} " for e in _ENTIDADE_GRAO_FINO):
        return "confusao_grao"
    if tokens & _RANKING:
        return "ranking"
    if tokens & _SERIE:
        return "serie_temporal"
    if det["grao"] == "Subsistema" and det["fenomeno"]:
        return "simples_subsistema"
    return "outro"


def _coletar(cat: dict, onto: dict, por_dataset: int):
    """Varre o catálogo e emite candidatos (dicts) com proveniência e categoria."""
    out = []
    for key, meta in sorted(cat.items()):
        if meta.get("status") == "discontinued":
            continue
        nome = meta.get("name", key.split("/")[-1])
        binding = onto.get("dataset_bindings", {}).get(nome)
        # perguntas do contrato (fonte=contrato) — DEV set / material de paráfrase
        contrato_qs = [q for q in meta.get("typical_questions", []) if isinstance(q, str)]
        contrato_qs += [f.get("question", "") for f in meta.get("few_shot_queries", [])
                        if isinstance(f, dict) and f.get("question")]
        vistos = set()
        n = 0
        for q in contrato_qs:
            q = q.strip()
            if not q or _norm(q) in vistos:
                continue
            vistos.add(_norm(q))
            out.append({
                "pergunta": q, "esperado": nome, "fonte": "contrato",
                "categoria": _categoria(q, onto, binding),
                "no_indice_bm25": True, "elegivel_teste": False,
            })
            # tenta uma versão sintética a partir desta
            sint = _coloquializar(q)
            if sint and _norm(sint) not in vistos:
                vistos.add(_norm(sint))
                out.append({
                    "pergunta": sint, "esperado": nome, "fonte": "sintetica",
                    "derivada_de": q, "categoria": _categoria(sint, onto, binding),
                    "no_indice_bm25": False, "elegivel_teste": True,
                })
            n += 1
            if n >= por_dataset:
                break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Gera candidatos a gold set (revisão humana)")
    ap.add_argument("--ontologia", required=True, type=Path)
    ap.add_argument("--saida", required=True, type=Path)
    ap.add_argument("--por-dataset", type=int, default=4,
                    help="máx. de perguntas do contrato aproveitadas por dataset")
    args = ap.parse_args()

    from mcp_tiago_dados_abertos.catalogo.contracts import load_all_contracts
    onto = json.loads(args.ontologia.read_text(encoding="utf-8"))
    bind_path = args.ontologia.parent / "bindings_derivados.json"
    if bind_path.exists():
        onto["dataset_bindings"] = json.loads(
            bind_path.read_text(encoding="utf-8-sig")).get("dataset_bindings", {})
    cat = load_all_contracts()
    candidatos = _coletar(cat, onto, args.por_dataset)

    with args.saida.open("w", encoding="utf-8") as fh:
        for c in candidatos:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")

    # sumário por categoria e proveniência (stderr, não polui o JSONL)
    from collections import Counter
    cat_count = Counter(c["categoria"] for c in candidatos)
    fonte_count = Counter(c["fonte"] for c in candidatos)
    elegiveis = sum(1 for c in candidatos if c["elegivel_teste"])
    print(f"candidatos: {len(candidatos)}  (elegíveis p/ teste: {elegiveis})", file=sys.stderr)
    print(f"por fonte: {dict(fonte_count)}", file=sys.stderr)
    print(f"por categoria: {dict(cat_count)}", file=sys.stderr)
    print(f"gravado em {args.saida}", file=sys.stderr)
    print("\nATENÇÃO: isto são CANDIDATOS. Revise à mão. Para o conjunto de TESTE use "
          "só fonte='sintetica' ou perguntas escritas à mão — nunca fonte='contrato' "
          "(estão no índice BM25 => vazamento).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

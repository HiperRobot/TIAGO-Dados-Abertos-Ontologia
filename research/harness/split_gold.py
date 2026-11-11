# -*- coding: utf-8 -*-
"""Separa o JSONL de candidatos (já revisado) em DEV e TESTE, com as travas de validade.

Entrada: um JSONL no formato de gerar_gold_candidatos.py, idealmente DEPOIS da
revisão humana (campos corrigidos, perguntas ruins removidas, categorias ajustadas).

Regras de split (as travas que protegem o experimento):
  - TESTE recebe SOMENTE perguntas com fonte != "contrato" e elegivel_teste=True.
    Perguntas do contrato estão no índice BM25 e inflariam C1 (vazamento).
  - DEV recebe o resto (perguntas do contrato), usado só para calibrar os pesos
    do boost ontológico. O teste nunca entra na calibração.
  - Nenhuma pergunta aparece nos dois conjuntos.

Saída: dois JSON no formato que run_benchmark.py consome ({"itens": [...]}).

Uso:
    python -m research.harness.split_gold \
        --entrada research/harness/gold_candidatos.jsonl \
        --dev research/harness/GOLDEN_DEV.json \
        --teste research/harness/GOLDEN_TESTE.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def _ler_jsonl(path: Path) -> list[dict]:
    itens = []
    for i, linha in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        linha = linha.strip()
        if not linha:
            continue
        try:
            itens.append(json.loads(linha))
        except json.JSONDecodeError as e:
            raise SystemExit(f"linha {i} inválida em {path}: {e}")
    return itens


def _formato_runner(itens: list[dict]) -> dict:
    """Projeta para o formato mínimo que o runner espera (+ categoria para estrato)."""
    return {"itens": [{"pergunta": it["pergunta"], "esperado": it["esperado"],
                       "categoria": it.get("categoria", "outro")} for it in itens]}


def main() -> int:
    ap = argparse.ArgumentParser(description="Split dev/teste do gold de roteamento")
    ap.add_argument("--entrada", required=True, type=Path)
    ap.add_argument("--dev", required=True, type=Path)
    ap.add_argument("--teste", required=True, type=Path)
    args = ap.parse_args()

    itens = _ler_jsonl(args.entrada)
    teste, dev = [], []
    vistos_teste = set()
    for it in itens:
        elegivel = it.get("fonte") != "contrato" and it.get("elegivel_teste", False)
        if elegivel:
            chave = (it["pergunta"].strip().lower(), it["esperado"])
            if chave in vistos_teste:
                continue  # dedup
            vistos_teste.add(chave)
            teste.append(it)
        else:
            dev.append(it)

    args.dev.write_text(json.dumps(_formato_runner(dev), ensure_ascii=False, indent=2),
                        encoding="utf-8")
    args.teste.write_text(json.dumps(_formato_runner(teste), ensure_ascii=False, indent=2),
                          encoding="utf-8")

    print(f"DEV   : {len(dev)} perguntas  -> {args.dev}", file=sys.stderr)
    print(f"TESTE : {len(teste)} perguntas -> {args.teste}", file=sys.stderr)
    print(f"TESTE por categoria: {dict(Counter(it.get('categoria','outro') for it in teste))}",
          file=sys.stderr)
    if len(teste) < 150:
        print(f"\nAVISO: {len(teste)} perguntas de teste é pouco para o estudo final "
              "(alvo 300-500). Escreva/parafraseie mais perguntas sintéticas, "
              "sobretudo nas categorias de confusão.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

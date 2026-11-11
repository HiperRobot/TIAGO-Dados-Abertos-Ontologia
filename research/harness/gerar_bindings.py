# -*- coding: utf-8 -*-
"""Deriva dataset_bindings {mede, grao, perspectiva} dos próprios contratos.

Mantém a ontologia ANCORADA no corpus: o binding de cada dataset sai do bloco
`semantics` do contrato, não de intuição. Isso é condição de validade do
experimento — C2 não pode introduzir conhecimento que C1 jamais teria.

Mapeamento:
  mede        <- casa semantics.measure_terms / family contra fenomenos[].aliases
  grao        <- semantics.row_grain.entity  (mapeado p/ classe espacial)
  perspectiva <- semantics.temporal_perspective

Emite JSON no stdout e, com --comparar, aponta divergências contra a semente
curada em ons_ontologia.json (útil para auditar a semente).

Uso:
    python -m research.harness.gerar_bindings --ontologia research/ontologia/ons_ontologia.json
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))
_SRC = _RAIZ / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _norm(t: str) -> str:
    s = unicodedata.normalize("NFKD", (t or "").lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


_ENTIDADE_POR_GRAO = {
    "subsistema": "Subsistema", "submercado": "Subsistema", "ree": "REE",
    "bacia": "Bacia", "reservatorio": "Reservatorio", "usina": "Usina",
    "subestacao": "Subestacao", "sin": "SIN",
    # fonte_agregada = balanço por fonte dentro de um subsistema; o grão espacial é subsistema
    "fonte_agregada": "Subsistema",
    # dataset_row / oferta_resposta_demanda não têm grão espacial — ficam None de propósito
}


def _casar_fenomeno(termos: list[str], family: str, nome_dataset: str, fenomenos: dict) -> str | None:
    """Casa measure_terms/family (sinal forte) contra os aliases de cada fenômeno.
    Em último caso, cai para o NOME do dataset (sinal fraco), para cobrir contratos
    sem measure_terms (ex.: ind_confiarb_*, subestacao, linha-transmissao).

    Casamento por igualdade de termo normalizado (termos/family) ou substring de
    alias no nome (fallback). Fenômenos testados na ordem de declaração; o primeiro
    que casar vence — declare os mais específicos antes dos genéricos (Cadastro).
    """
    pool = {_norm(t) for t in termos}
    if family:
        pool.add(_norm(family))
    # 1) sinal forte: measure_terms / family por igualdade
    for nome, info in fenomenos.items():
        aliases = {_norm(a) for a in info.get("aliases", [])}
        if pool & aliases:
            return nome
    # 2) sinal fraco: alias aparece no NOME do dataset (hífens/underscores viram espaço)
    nome_norm = _norm(nome_dataset).replace("-", " ").replace("_", " ")
    nome_norm = f" {nome_norm} "
    for nome, info in fenomenos.items():
        for a in info.get("aliases", []):
            an = _norm(a)
            if f" {an} " in nome_norm or (len(an) >= 5 and an in nome_norm):
                return nome
    return None


# Padrões "por-X" no nome do dataset -> classe espacial. Do mais específico ao
# mais genérico; o primeiro que casar vence. 'ree' antes de 'reservatorio' porque
# 'reservatorio-equivalente' é REE, não reservatório físico.
_GRAO_POR_NOME = [
    ("ree", "REE"),
    ("reservatorio equivalente", "REE"),
    ("bacia", "Bacia"),
    ("reservatorio", "Reservatorio"),
    ("subsistema", "Subsistema"),
    ("submercado", "Subsistema"),
    ("usina", "Usina"),
    ("subestacao", "Subestacao"),
]


def _inferir_grao_por_nome(nome_dataset: str) -> str | None:
    n = f" {_norm(nome_dataset).replace('-', ' ').replace('_', ' ')} "
    for chave, classe in _GRAO_POR_NOME:
        if f" {chave} " in n or chave in n:
            return classe
    return None


def derivar(catalog: dict, onto: dict) -> dict:
    fenomenos = onto.get("fenomenos", {})
    out = {}
    for key, meta in catalog.items():
        if meta.get("status") == "discontinued":
            continue
        sem = meta.get("semantics") or {}
        termos = sem.get("measure_terms") or []
        family = sem.get("family") or ""
        nome_ds = meta.get("name", key.split("/")[-1])
        fen = _casar_fenomeno(termos, family, nome_ds, fenomenos)
        grao_raw = _norm(str(((sem.get("row_grain") or {}).get("entity")) or ""))
        grao = _ENTIDADE_POR_GRAO.get(grao_raw)
        # Fallback de grão pelo NOME quando o contrato marca entity='dataset_row'
        # (ex.: 'ear-diario-por-ree-...' tem grão REE, mas o contrato não declara).
        if grao is None and grao_raw in ("dataset_row", ""):
            grao = _inferir_grao_por_nome(nome_ds)
        persp_raw = _norm(str(sem.get("temporal_perspective") or ""))
        persp = {"verificado": "Verificado", "programado": "Programado",
                 "previsto": "Previsto", "cadastral": "Cadastral"}.get(persp_raw)
        nome = meta.get("name", key.split("/")[-1])
        out[nome] = {"mede": fen, "grao": grao, "perspectiva": persp}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ontologia", required=True, type=Path)
    ap.add_argument("--comparar", action="store_true", help="aponta divergências vs a semente curada")
    args = ap.parse_args()

    from mcp_tiago_dados_abertos.catalogo.contracts import load_all_contracts
    onto = json.loads(args.ontologia.read_text(encoding="utf-8"))
    catalog = load_all_contracts()
    derivado = derivar(catalog, onto)

    if args.comparar:
        semente = onto.get("dataset_bindings", {})
        print(f"# derivados: {len(derivado)} | semente: {len(semente)}", file=sys.stderr)
        for nome, sem_b in semente.items():
            der_b = derivado.get(nome)
            if der_b != sem_b:
                print(f"DIVERGE {nome}: semente={sem_b} derivado={der_b}", file=sys.stderr)
        incompletos = [n for n, b in derivado.items() if not b.get("mede")]
        if incompletos:
            print(f"# sem fenômeno casado ({len(incompletos)}): {incompletos}", file=sys.stderr)

    print(json.dumps({"dataset_bindings": derivado}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

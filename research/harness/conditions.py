# -*- coding: utf-8 -*-
"""Condições experimentais C0 / C1 / C2 / C3 para o estudo de ablação do roteamento.

A pergunta de pesquisa é: *o que uma ontologia formal adiciona SOBRE data
contracts já ricos?* As quatro condições isolam camadas de conhecimento:

  C0  schema-only   — piso. Só nome do dataset + colunas básicas. Simula o
                      baseline "o modelo adivinha pelo nome" descrito no README.
  C1  contracts     — estado atual do servidor: ODCS completo (tags, vectorTags,
                      aiContext, typicalQuestions, semantics). É o `catalog` que
                      load_all_contracts() devolve, sem alteração.
  C2  contracts+ont — C1 mais um re-ranking guiado pela ontologia (ver
                      ontology_boost.py). O par C1 vs C2 responde à tese.
  C3  ontologia-only— ranking PURAMENTE ontológico: sem BM25, só casamento de
                      aliases da ontologia. Isola o valor da ontologia sozinha.
                      Comparando C3 vs C0: quanto a ontologia adiciona sobre nada.
                      Comparando C3 vs C1: se ontologia sozinha chega perto de contracts.

Nenhuma condição toca o código de produção: C0 DEGRADA uma cópia do catálogo;
C2 PÓS-PROCESSA o ranking de C1; C3 usa um ranker alternativo.

Uso:
    from research.harness.conditions import make_catalog, route
    cat_c0 = make_catalog(catalog, "C0")
    ranking = route(cat_c0, "qual a carga do SE em 2020", "C0", top_n=5)
"""
from __future__ import annotations

import copy
import unicodedata
from typing import Literal

from mcp_tiago_dados_abertos.catalogo import ranking as _ranking
from mcp_tiago_dados_abertos.catalogo import retrieval as _retrieval

Condicao = Literal["C0", "C1", "C2", "C3"]

# Campos que a camada de data contracts acrescenta sobre o schema cru. C0 zera
# todos: é a definição operacional de "sem camada semântica".
_CAMPOS_CONTRATO = (
    "tags",
    "vector_tags",
    "rag_context",
    "agent_instructions",
    "typical_questions",
    "few_shot_queries",
    "semantics",
)


def _degradar_para_c0(meta: dict) -> dict:
    """Devolve uma cópia do meta com a camada de contrato removida.

    Mantém: name, columns (nome/tipo/descrição curta), granularity, s3. Remove:
    tudo que é curadoria semântica. O objetivo é um baseline honesto que ainda
    sabe o nome e as colunas do dataset — como um catálogo de schema puro.
    """
    m = copy.deepcopy(meta)
    for campo in _CAMPOS_CONTRATO:
        if campo in m:
            m[campo] = [] if isinstance(m[campo], list) else ({} if isinstance(m[campo], dict) else "")
    # columns: preserva nome/tipo, descarta aliases/enum/examples/semantic_type
    # (são curadoria do contrato, não schema físico).
    cols = []
    for c in m.get("columns", []):
        cols.append({
            "name": c.get("name"),
            "type": c.get("type"),
            "description": (c.get("description") or "")[:0],  # zera descrição curada
        })
    m["columns"] = cols
    return m


def make_catalog(catalog: dict, cond: Condicao) -> dict:
    """Materializa o catálogo da condição. C1 e C2 compartilham o catálogo cheio
    (o que muda em C2 é o re-ranking, não o catálogo). C3 usa catálogo degradado
    porque o ranking é puramente ontológico."""
    if cond == "C0":
        return {k: _degradar_para_c0(v) for k, v in catalog.items()}
    if cond == "C3":
        # C3 usa a ontologia pura, mas precisa do catálogo para listar os datasets.
        # Degradamos para C0 para isolar o efeito: se C3 > C0, o ganho é da ontologia.
        return {k: _degradar_para_c0(v) for k, v in catalog.items()}
    return catalog  # C1, C2


def _norm(t: str) -> str:
    """Normaliza texto para comparação: lowercase, sem acentos."""
    s = unicodedata.normalize("NFKD", (t or "").lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _cita_algum(pergunta_norm: str, aliases: list[str]) -> bool:
    """Algum alias aparece como palavra na pergunta?"""
    p = f" {pergunta_norm} "
    return any(f" {_norm(a)} " in p for a in aliases)


def _rankear_por_ontologia(catalog: dict, pergunta: str, ontologia: dict, top_n: int = 5
                           ) -> list[tuple[int, str, dict]]:
    """Ranking puramente ontológico (C3): sem BM25, só casamento de aliases.
    
    Para cada dataset com binding na ontologia, calcula um score baseado em:
    - Fenômeno: +10 se a pergunta menciona o fenômeno que o dataset mede
    - Grão: +5 se menciona o grão espacial do dataset
    - Perspectiva: +3 se menciona a perspectiva temporal
    
    Datasets sem binding ficam com score 0. Desempate por nome (determinístico).
    """
    pnorm = _norm(pergunta)
    bindings = ontologia.get("dataset_bindings", {})
    fenomenos = ontologia.get("fenomenos", {})
    entidades = ontologia.get("entidades_espaciais", {})
    perspectivas = ontologia.get("perspectivas", {})
    
    # Detectar sinais da pergunta
    fen_detectado = None
    for nome, info in fenomenos.items():
        if _cita_algum(pnorm, info.get("aliases", [])):
            fen_detectado = nome
            break
    
    grao_detectado = None
    for nome, info in sorted(entidades.items(), key=lambda kv: -kv[1].get("nivel", 0)):
        if _cita_algum(pnorm, info.get("aliases", [])):
            grao_detectado = nome
            break
    # Nomes de subsistemas também indicam grão
    if not grao_detectado:
        for aliases in ontologia.get("subsistemas", {}).values():
            if _cita_algum(pnorm, aliases):
                grao_detectado = "Subsistema"
                break
    
    persp_detectada = None
    for nome, aliases in perspectivas.items():
        if _cita_algum(pnorm, aliases):
            persp_detectada = nome
            break
    
    # Calcular scores
    resultados = []
    for nome_dataset, meta in catalog.items():
        nome_limpo = nome_dataset.split("/")[-1]  # 'ons/carga-energia' -> 'carga-energia'
        b = bindings.get(nome_limpo, {})
        
        score = 0
        if b:
            if fen_detectado and b.get("mede") == fen_detectado:
                score += 10
            if grao_detectado and b.get("grao") == grao_detectado:
                score += 5
            if persp_detectada and b.get("perspectiva") == persp_detectada:
                score += 3
        
        resultados.append((score, nome_limpo, meta))
    
    # Ordenar: score desc, nome asc (determinístico)
    resultados.sort(key=lambda x: (-x[0], x[1]))
    return resultados[:top_n]


def route(catalog_cond: dict, pergunta: str, cond: Condicao, top_n: int = 5,
          ontologia: dict | None = None, pesos: dict | None = None) -> list[tuple[int, str, dict]]:
    """Roteia uma pergunta sob a condição dada. Retorna [(score, nome, meta), ...].

    `pesos` (só C2): dict de pesos do boost; None usa o default. O calibrador varia isto.

    Condições:
      C0, C1: BM25 sobre o catálogo (degradado ou cheio)
      C2: BM25 + re-ranking ontológico
      C3: ranking PURAMENTE ontológico (sem BM25), para isolar o valor da ontologia

    IMPORTANTE: o índice BM25 é um singleton global reconstruído por fingerprint
    do catálogo. Como C0 e C1 têm fingerprints diferentes (chaves iguais, conteúdo
    diferente — mas o fingerprint só olha as CHAVES), forçamos a reconstrução
    explícita para o índice refletir o catálogo da condição corrente.
    """
    # C3: ranking puramente ontológico, sem BM25
    if cond == "C3":
        if ontologia is None:
            raise ValueError("C3 exige a ontologia carregada (parametro 'ontologia').")
        return _rankear_por_ontologia(catalog_cond, pergunta, ontologia, top_n=top_n)
    
    _retrieval.build_index(catalog_cond)  # evita índice obsoleto entre condições
    ranked = _ranking.rank_datasets(catalog_cond, pergunta, top_n=top_n)

    if cond != "C2":
        return ranked

    # C2: re-ranking ontológico sobre o resultado de C1.
    from research.harness.ontology_boost import aplicar_boost
    if ontologia is None:
        raise ValueError("C2 exige a ontologia carregada (parametro 'ontologia').")
    return aplicar_boost(ranked, pergunta, ontologia, top_n=top_n, pesos=pesos)

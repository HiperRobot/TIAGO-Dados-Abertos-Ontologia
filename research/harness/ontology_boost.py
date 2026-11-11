# -*- coding: utf-8 -*-
"""Re-ranking ontológico — a camada que distingue C2 de C1.

C1 já rankeou por BM25 + keyword. C2 reordena esse resultado usando as RELAÇÕES
da ontologia, que o escore lexical de C1 não enxerga:

  1. Fenômeno  — a pergunta fala de "carga", "EAR", "corte"? Dataset cujo binding
                 'mede' casa o fenômeno ganha; dataset de fenômeno conflitante
                 (carga vs balanço vs geração) perde. Ataca os pares de confusão.
  2. Grão espacial — a pergunta cita subsistema, bacia, usina? Via hierarquia
                 parteDe, premia o dataset no grão pedido e penaliza grão errado
                 da mesma família (EAR por subsistema vs por reservatório).
  3. Perspectiva — "previsto/programado" vs "verificado"; reforça o eixo que C1
                 já trata por palavra, agora como relação declarada.

É deliberadamente um re-ranking ADITIVO sobre C1 (não substitui o escore). Assim
o experimento mede o DELTA da ontologia, mantendo tudo o mais igual. Os pesos
abaixo são a configuração inicial; devem ser calibrados num conjunto de
desenvolvimento separado do teste (ver protocolo, §Calibração).
"""
from __future__ import annotations

import unicodedata

# Pesos do boost. Mesma ordem de grandeza dos sinais de C1 (ver ranking.py:
# perspectiva=6, familia=9, medida=6) para o re-ranking ser comparável, não
# dominante. Estes são a configuração INICIAL (não calibrada); o calibrador
# (calibrar.py) procura a melhor combinação no dev set e grava em pesos_boost.json.
#
# Grão espacial é direcional (ver _relacao_grao). Pedir fino e receber grosso
# (ancestral) é o erro que o piloto expôs: EAR por reservatório indo para EAR por
# subsistema. Por isso 'ancestral', quando a pergunta foi ESPECÍFICA, é penalizado.
PESOS_DEFAULT = {
    "fenomeno_match": 8,
    "fenomeno_conflito": -8,
    "grao_exato": 7,
    "grao_ancestral": -3,    # dataset agrega demais o que a pergunta pediu específico
    "grao_descendente": 1,   # dataset mais fino: compõe o pedido, leve bônus
    "grao_incompativel": -6,
    "perspectiva_match": 4,
    "perspectiva_conflito": -4,
}

# Retrocompat: nomes antigos como constantes de módulo (alguns testes/leituras usam).
W_FENOMENO_MATCH = PESOS_DEFAULT["fenomeno_match"]
W_FENOMENO_CONFLITO = PESOS_DEFAULT["fenomeno_conflito"]
W_GRAO_EXATO = PESOS_DEFAULT["grao_exato"]
W_GRAO_ANCESTRAL = PESOS_DEFAULT["grao_ancestral"]
W_GRAO_DESCENDENTE = PESOS_DEFAULT["grao_descendente"]
W_GRAO_INCOMPATIVEL = PESOS_DEFAULT["grao_incompativel"]
W_PERSPECTIVA_MATCH = PESOS_DEFAULT["perspectiva_match"]
W_PERSPECTIVA_CONFLITO = PESOS_DEFAULT["perspectiva_conflito"]


def _norm(t: str) -> str:
    s = unicodedata.normalize("NFKD", (t or "").lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _cita_algum(pergunta_norm: str, aliases: list[str]) -> bool:
    """Algum alias aparece como palavra na pergunta?"""
    p = f" {pergunta_norm} "
    return any(f" {_norm(a)} " in p for a in aliases)


def _detectar_fenomeno(pnorm: str, onto: dict) -> str | None:
    for nome, info in onto.get("fenomenos", {}).items():
        if _cita_algum(pnorm, info.get("aliases", [])):
            return nome
    return None


def _detectar_grao(pnorm: str, onto: dict) -> str | None:
    # Cita um subsistema específico? então o grão é Subsistema (ou mais fino se
    # citar usina/bacia). Verifica entidades da mais fina para a mais grossa.
    ents = onto.get("entidades_espaciais", {})
    # ordena por nivel desc (mais fino primeiro) para o grão mais específico vencer
    for nome, info in sorted(ents.items(), key=lambda kv: -kv[1].get("nivel", 0)):
        if _cita_algum(pnorm, info.get("aliases", [])):
            return nome
    # nomes de subsistema (sudeste etc.) implicam grão Subsistema
    for aliases in onto.get("subsistemas", {}).values():
        if _cita_algum(pnorm, aliases):
            return "Subsistema"
    return None


def _detectar_perspectiva(pnorm: str, onto: dict) -> str | None:
    for nome, aliases in onto.get("perspectivas", {}).items():
        if _cita_algum(pnorm, aliases):
            return nome
    return None


def _relacao_grao(grao_pergunta: str, grao_dataset: str, onto: dict) -> str:
    """Relação entre o grão pedido e o do dataset, pela hierarquia parteDe.

      'exato'      — mesmo grão (match ideal).
      'ancestral'  — o dataset agrega o pedido (pergunta: reservatório; dataset: SIN).
                     Responde, mas perde detalhe: match fraco.
      'descendente'— o dataset é MAIS FINO que o pedido (pergunta: subsistema;
                     dataset: por reservatório). Ainda compõe o pedido via agregação:
                     tolerável, não ideal.
      'incompativel'— nenhum caminho parteDe liga os dois (ex.: Usina vs Subestação).

    A assimetria importa: pedir reservatório e receber o dataset por subsistema é
    PIOR (não dá para desagregar) do que pedir subsistema e receber por reservatório
    (dá para somar). É isso que o boost direcional explora.
    """
    if grao_pergunta == grao_dataset:
        return "exato"
    ents = onto.get("entidades_espaciais", {})

    def _sobe(ini):
        atual, visto = ini, set()
        while atual and atual not in visto:
            visto.add(atual)
            yield atual
            atual = ents.get(atual, {}).get("parte_de")

    if grao_dataset in set(_sobe(grao_pergunta)):   # dataset é ancestral do pedido
        return "ancestral"
    if grao_pergunta in set(_sobe(grao_dataset)):   # dataset é descendente (mais fino)
        return "descendente"
    return "incompativel"


def aplicar_boost(ranked: list[tuple[int, str, dict]], pergunta: str, onto: dict,
                  top_n: int = 5, pesos: dict | None = None) -> list[tuple[int, str, dict]]:
    """Reordena o ranking de C1 aplicando os sinais ontológicos. Determinístico.

    `pesos`: dict com as chaves de PESOS_DEFAULT. Se None, usa PESOS_DEFAULT.
    O calibrador passa combinações diferentes aqui sem tocar o código.
    """
    w = {**PESOS_DEFAULT, **(pesos or {})}
    pnorm = _norm(pergunta)
    fen_q = _detectar_fenomeno(pnorm, onto)
    grao_q = _detectar_grao(pnorm, onto)
    persp_q = _detectar_perspectiva(pnorm, onto)
    bindings = onto.get("dataset_bindings", {})

    reranked = []
    for score, nome, meta in ranked:
        delta = 0
        b = bindings.get(nome)
        if b:
            # Fenômeno
            if fen_q and b.get("mede"):
                delta += w["fenomeno_match"] if b["mede"] == fen_q else w["fenomeno_conflito"]
            # Grão espacial (direcional)
            if grao_q and b.get("grao"):
                rel = _relacao_grao(grao_q, b["grao"], onto)
                nivel_pedido = onto.get("entidades_espaciais", {}).get(grao_q, {}).get("nivel", 0)
                pediu_especifico = nivel_pedido >= 3  # Reservatorio/Usina/Subestacao
                if rel == "exato":
                    delta += w["grao_exato"]
                elif rel == "ancestral":
                    # só penaliza se a pergunta foi específica; senão, ancestral é aceitável
                    delta += w["grao_ancestral"] if pediu_especifico else 0
                elif rel == "descendente":
                    delta += w["grao_descendente"]
                else:
                    delta += w["grao_incompativel"]
            # Perspectiva
            if persp_q and b.get("perspectiva"):
                delta += w["perspectiva_match"] if b["perspectiva"] == persp_q else w["perspectiva_conflito"]
        reranked.append((score + delta, nome, meta))

    # desempate determinístico igual ao de produção: score desc, nome asc
    reranked.sort(key=lambda x: (-x[0], x[1]))
    return reranked[:top_n]


def explicar(pergunta: str, onto: dict) -> dict:
    """Diagnóstico: o que a ontologia detectou na pergunta. Útil para error analysis."""
    pnorm = _norm(pergunta)
    return {
        "fenomeno": _detectar_fenomeno(pnorm, onto),
        "grao": _detectar_grao(pnorm, onto),
        "perspectiva": _detectar_perspectiva(pnorm, onto),
    }

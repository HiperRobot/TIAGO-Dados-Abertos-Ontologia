# -*- coding: utf-8 -*-
"""Métricas de roteamento e comparação pareada entre condições.

Roteamento (por condição):
    top-k accuracy, MRR, taxa fora do top-10, matriz de confusão.

Comparação pareada (C_a vs C_b, nas MESMAS perguntas):
    teste de McNemar exato (binomial). Reporta discordâncias b/c e p-valor.
    O teste pareado é o correto aqui: as duas condições veem exatamente as
    mesmas perguntas, então o que importa são as perguntas em que UMA acerta e
    a outra erra, não a contagem marginal.

Sem dependências além da biblioteca padrão. McNemar exato via distribuição
binomial calculada à mão (sem scipy) para o harness rodar em qualquer ambiente.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import comb


@dataclass
class ResultadoRoteamento:
    """Métricas de uma condição sobre um gold set."""
    n: int
    top1: float
    top3: float
    top5: float
    mrr: float
    fora_top10: float
    acertos_top1: list[bool] = field(default_factory=list)  # por pergunta, p/ McNemar
    confusao: dict = field(default_factory=dict)  # (esperado -> devolvido): contagem

    def resumo(self) -> str:
        return (f"n={self.n}  top1={self.top1:.3f}  top3={self.top3:.3f}  "
                f"top5={self.top5:.3f}  MRR={self.mrr:.3f}  fora_top10={self.fora_top10:.3f}")


def _posicao(ranking_nomes: list[str], esperado: str) -> int | None:
    """Posição 1-indexada do dataset esperado no ranking, ou None se ausente."""
    for i, nome in enumerate(ranking_nomes, start=1):
        if nome == esperado:
            return i
    return None


def avaliar(predicoes: list[tuple[str, list[str]]]) -> ResultadoRoteamento:
    """`predicoes`: lista de (dataset_esperado, [nomes_rankeados]).

    Os rankings devem ter profundidade >= 10 para a métrica fora_top10 fazer
    sentido; chame route(..., top_n=10) ao gerar.
    """
    n = len(predicoes)
    if n == 0:
        return ResultadoRoteamento(0, 0, 0, 0, 0, 0)
    c1 = c3 = c5 = fora = 0
    soma_rr = 0.0
    acertos_top1: list[bool] = []
    confusao: dict = {}
    for esperado, ranking in predicoes:
        pos = _posicao(ranking, esperado)
        acertos_top1.append(pos == 1)
        if pos is None or pos > 10:
            fora += 1
        if pos is not None:
            soma_rr += 1.0 / pos
            if pos <= 1:
                c1 += 1
            if pos <= 3:
                c3 += 1
            if pos <= 5:
                c5 += 1
        if pos != 1:  # registra o par de confusão (o que veio em 1º no lugar)
            devolvido = ranking[0] if ranking else "<vazio>"
            chave = (esperado, devolvido)
            confusao[chave] = confusao.get(chave, 0) + 1
    return ResultadoRoteamento(
        n=n, top1=c1 / n, top3=c3 / n, top5=c5 / n,
        mrr=soma_rr / n, fora_top10=fora / n,
        acertos_top1=acertos_top1, confusao=confusao,
    )


def _binom_sf(k: int, n: int, p: float = 0.5) -> float:
    """P(X >= k) para X ~ Binomial(n, p). Soma exata, sem scipy."""
    return sum(comb(n, i) * p**i * (1 - p)**(n - i) for i in range(k, n + 1))


@dataclass
class ResultadoMcNemar:
    b: int        # a_acerta & b_erra  (A melhor nesta pergunta)
    c: int        # a_erra & b_acerta  (B melhor)
    n_discordante: int
    p_valor: float
    direcao: str  # qual condição teve mais acertos exclusivos

    def resumo(self) -> str:
        return (f"McNemar exato: b={self.b} (só A), c={self.c} (só B), "
                f"discordâncias={self.n_discordante}, p={self.p_valor:.4f}  [{self.direcao}]")


def mcnemar(acertos_a: list[bool], acertos_b: list[bool], nome_a="A", nome_b="B") -> ResultadoMcNemar:
    """McNemar exato (bicaudal) sobre acertos pareados top-1 das condições A e B."""
    if len(acertos_a) != len(acertos_b):
        raise ValueError("as duas condições precisam das MESMAS perguntas, na mesma ordem")
    b = sum(1 for xa, xb in zip(acertos_a, acertos_b) if xa and not xb)
    c = sum(1 for xa, xb in zip(acertos_a, acertos_b) if xb and not xa)
    n = b + c
    if n == 0:
        return ResultadoMcNemar(b, c, 0, 1.0, "empate (sem discordância)")
    k = max(b, c)
    p = min(1.0, 2.0 * _binom_sf(k, n, 0.5))  # bicaudal
    if b > c:
        direcao = f"{nome_a} > {nome_b}"
    elif c > b:
        direcao = f"{nome_b} > {nome_a}"
    else:
        direcao = "empate"
    return ResultadoMcNemar(b=b, c=c, n_discordante=n, p_valor=p, direcao=direcao)


def top_pares_confusao(res: ResultadoRoteamento, k: int = 10) -> list[tuple[tuple[str, str], int]]:
    """Os k pares (esperado -> devolvido) mais frequentes. Guia a calibração."""
    return sorted(res.confusao.items(), key=lambda kv: -kv[1])[:k]

# -*- coding: utf-8 -*-
"""Routing metrics and paired statistics. Standard library only."""
from __future__ import annotations

import random
from math import comb, sqrt


def rank_of(valid: set[str], ranking: list[str]) -> int | None:
    """1-based position of the first valid dataset, or None."""
    for i, name in enumerate(ranking, start=1):
        if name in valid:
            return i
    return None


def summarise(ranks: list[int | None]) -> dict:
    n = len(ranks)
    hit = lambda k: sum(1 for r in ranks if r is not None and r <= k) / n  # noqa: E731
    top1 = hit(1)
    lo, hi = wilson(round(top1 * n), n)
    return {"n": n, "top1": top1, "top1_ci95": [lo, hi], "top3": hit(3), "top5": hit(5),
            "mrr": sum(1 / r for r in ranks if r) / n}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return centre - half, centre + half


def mcnemar(a: list[bool], b: list[bool]) -> dict:
    """Exact two-sided McNemar test on paired correct/incorrect outcomes."""
    only_a = sum(1 for x, y in zip(a, b) if x and not y)
    only_b = sum(1 for x, y in zip(a, b) if y and not x)
    n = only_a + only_b
    p = 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, i) for i in range(max(only_a, only_b), n + 1)) / 2 ** n)
    return {"only_a": only_a, "only_b": only_b, "p": p}


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni adjusted p-values."""
    out, running = {}, 0.0
    ordered = sorted(pvalues.items(), key=lambda kv: kv[1])
    for i, (k, p) in enumerate(ordered):
        running = max(running, min(1.0, p * (len(ordered) - i)))
        out[k] = running
    return out


def bootstrap_diff(a: list[float], b: list[float], reps: int = 10000, seed: int = 0) -> list[float]:
    """Mean of (b - a) with a 95% percentile bootstrap interval, paired."""
    rnd = random.Random(seed)
    d = [y - x for x, y in zip(a, b)]
    n = len(d)
    means = sorted(sum(d[rnd.randrange(n)] for _ in range(n)) / n for _ in range(reps))
    return [sum(d) / n, means[int(0.025 * reps)], means[int(0.975 * reps)]]

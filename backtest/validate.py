"""Валидация целостности: детектор заглядывания в будущее и дыр в данных.

Главный страх бэктестера — незаметный lookahead в кастомной стратегии (например,
стратегия где-то использует будущие данные). Проверка простая и строгая: меняем
бары ПОСЛЕ момента k на мусор и убеждаемся, что equity-кривая ДО k не шелохнулась.
Если шелохнулась — стратегия видит будущее. Честный движок такой тест проходит всегда.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from .core import Bar
from .engine import run


def _perturb_future(data: dict[str, list[Bar]], k: int, seed: int = 0) -> dict[str, list[Bar]]:
    """Заменить бары с индекса k+1 до конца на искажённые (детерминированно)."""
    rng = random.Random(seed)
    out = {}
    for t, bars in data.items():
        new = []
        for i, b in enumerate(bars):
            if i <= k:
                new.append(b)
            else:
                f = 1.0 + rng.uniform(0.2, 0.8)     # сильное искажение будущего
                new.append(Bar(t=b.t, o=b.o * f, h=b.h * f, l=b.l * f,
                               c=b.c * f, v=b.v))
        out[t] = new
    return out


@dataclass
class LookaheadCheck:
    lookahead_detected: bool
    first_divergence_i: int          # −1, если расхождений нет
    split_i: int

    def summary(self) -> str:
        if not self.lookahead_detected:
            return f"✓ lookahead не обнаружен (возмущение будущего после бара {self.split_i} не повлияло на прошлое)"
        return (f"⚠ ОБНАРУЖЕН lookahead: equity разошлась на баре {self.first_divergence_i} "
                f"≤ точки возмущения {self.split_i} — стратегия использует будущие данные")


def detect_lookahead(strategy_factory, data: dict[str, list[Bar]],
                     split_frac: float = 0.5, seed: int = 0, tol: float = 1e-6,
                     **run_kw) -> LookaheadCheck:
    """Прогнать стратегию на исходных и на «испорченных после k» данных; сравнить equity[:k+1].

    strategy_factory(data) → свежий экземпляр стратегии. Аргумент data передаётся, чтобы
    поймать и стратегии, которые читают ленту напрямую за пределами текущего бара: такой
    «читатель будущего», построенный на искажённой ленте, изменит прошлую equity → детект.
    Честная стратегия (только ctx, прошлое) и корректный движок тест проходят всегда."""
    n = max(len(b) for b in data.values())
    k = int(n * split_frac)
    perturbed = _perturb_future(data, k, seed)
    base = run(strategy_factory(data), data, **run_kw)
    pert = run(strategy_factory(perturbed), perturbed, **run_kw)
    m = min(len(base.equity), len(pert.equity), k + 1)
    for i in range(m):
        if abs(base.equity[i] - pert.equity[i]) > tol * max(1.0, abs(base.equity[i])):
            return LookaheadCheck(True, i, k)
    return LookaheadCheck(False, -1, k)


@dataclass
class GapReport:
    median_dt: float
    n_gaps: int
    gap_indices: list          # индексы баров, перед которыми аномальный разрыв
    duplicate_ts: int

    def summary(self) -> str:
        return (f"Data check: медианный шаг {self.median_dt/86400:.2f} дн, "
                f"аномальных разрывов {self.n_gaps}, дублей метки времени {self.duplicate_ts}"
                + (f"\n  разрывы у баров: {self.gap_indices[:10]}" if self.gap_indices else ""))


def detect_gaps(bars: list[Bar], factor: float = 1.5) -> GapReport:
    """Найти разрывы во времени (dt > factor·медиана) и дубли меток времени."""
    if len(bars) < 3:
        return GapReport(0.0, 0, [], 0)
    dts = [bars[i].t - bars[i - 1].t for i in range(1, len(bars))]
    s = sorted(dts)
    median = s[len(s) // 2] or 1.0
    gaps = [i + 1 for i, dt in enumerate(dts) if dt > factor * median]
    dups = sum(1 for dt in dts if dt <= 0)
    return GapReport(float(median), len(gaps), gaps, dups)

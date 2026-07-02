"""Сценарный анализ: один бэктест — это один путь одного рынка. Здесь стратегия
прогоняется по МНОЖЕСТВУ синтетических миров (разные сиды) и по РАЗНЫМ режимам
(тренд / случайное блуждание / возврат к среднему / цикл), чтобы увидеть распределение
исходов и понять, в каких условиях стратегия живёт, а в каких ломается.

Дополняет montecarlo (тот ресэмплит ОДИН путь) — здесь генерируются новые пути целиком.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import candles
from .engine import run
from .metrics import metrics

_GENERATORS = {"gbm": candles.gbm, "trend": candles.trend,
               "mean_revert": candles.mean_revert, "sine": candles.sine}


def _percentile(xs, q):
    if not xs:
        return 0.0
    s = sorted(xs)
    idx = q * (len(s) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


@dataclass
class ScenarioStats:
    generator: str
    n: int
    metric: str
    mean: float
    std: float
    p5: float
    p50: float
    p95: float
    frac_positive: float        # доля миров, где метрика > 0
    frac_beats_return: float    # доля миров с положительной итоговой доходностью

    def line(self) -> str:
        return (f"  {self.generator:<12} {self.metric}: mean={self.mean:+.2f} "
                f"std={self.std:.2f} p5/p50/p95={self.p5:+.2f}/{self.p50:+.2f}/{self.p95:+.2f} "
                f">0 у {self.frac_positive*100:.0f}%, прибыльны {self.frac_beats_return*100:.0f}%")


def across_seeds(strategy_factory, generator: str = "gbm", seeds=range(50),
                 metric: str = "sharpe", bars: int = 750, commission: float = 0.0005,
                 slippage: float = 0.0, **gen_kw) -> ScenarioStats:
    """Прогнать стратегию по многим сидам одного генератора → распределение метрики."""
    gen = _GENERATORS[generator]
    vals, rets = [], []
    for sd in seeds:
        data = gen("SYN", bars=bars, seed=sd, **gen_kw)
        res = run(strategy_factory(), data, commission=commission, slippage=slippage)
        m = metrics(res)
        v = getattr(m, metric)
        if v == v and abs(v) != float("inf"):
            vals.append(v)
        rets.append(m.total_return)
    n = len(vals)
    mean = sum(vals) / n if n else 0.0
    std = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5 if n else 0.0
    return ScenarioStats(
        generator=generator, n=len(rets), metric=metric, mean=mean, std=std,
        p5=_percentile(vals, 0.05), p50=_percentile(vals, 0.50), p95=_percentile(vals, 0.95),
        frac_positive=(sum(1 for v in vals if v > 0) / n) if n else 0.0,
        frac_beats_return=(sum(1 for r in rets if r > 0) / len(rets)) if rets else 0.0)


def across_regimes(strategy_factory, seeds=range(20), metric: str = "sharpe",
                   bars: int = 750, commission: float = 0.0005,
                   slippage: float = 0.0) -> list[ScenarioStats]:
    """Прогнать стратегию по всем режимам рынка → понять её область применимости."""
    return [across_seeds(strategy_factory, g, seeds, metric, bars, commission, slippage)
            for g in _GENERATORS]


def regimes_report(stats: list[ScenarioStats]) -> str:
    L = [f"Сценарии по режимам ({stats[0].n} миров каждый, метрика {stats[0].metric}):"]
    for s in stats:
        L.append(s.line())
    return "\n".join(L)

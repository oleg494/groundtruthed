"""Monte-Carlo устойчивости: бутстрап порядка сделок и баровых доходностей.

Одна реализованная equity-кривая — лишь один из возможных путей. Если перетасовать
порядок сделок (или ресэмплить дневные доходности с возвращением), получим распределение
итогов и просадок. Узкое распределение с положительным p5 — признак устойчивости;
широкое, уходящее в минус, — признак, что результат держался на удаче/порядке.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from .engine import Result


def _percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    idx = q * (len(s) - 1)
    lo = int(idx)
    frac = idx - lo
    hi = min(lo + 1, len(s) - 1)
    return s[lo] * (1 - frac) + s[hi] * frac


def _max_dd(equity: list[float]) -> float:
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0 if peak else 0.0)
    return mdd


@dataclass
class MCResult:
    n: int
    source: str
    ret_p5: float
    ret_p50: float
    ret_p95: float
    dd_p5: float            # худшая (самая глубокая) просадка — нижний хвост
    dd_p50: float
    dd_median: float
    prob_profit: float
    worst_ret: float
    best_ret: float

    def summary(self) -> str:
        return (f"Monte-Carlo ({self.source}, n={self.n}):\n"
                f"  доходность p5/p50/p95: {self.ret_p5*100:+.1f}% / "
                f"{self.ret_p50*100:+.1f}% / {self.ret_p95*100:+.1f}%\n"
                f"  просадка p50/p5(хвост): {self.dd_median*100:.1f}% / {self.dd_p5*100:.1f}%\n"
                f"  P(прибыль) = {self.prob_profit*100:.0f}%   "
                f"диапазон: {self.worst_ret*100:+.1f}%..{self.best_ret*100:+.1f}%")


def bootstrap_trades(res: Result, n: int = 2000, seed: int = 0) -> MCResult:
    """Ресэмплим СДЕЛКИ с возвращением → распределение итогов и просадок.

    Важно: простая перетасовка порядка не меняет сумму P&L (итог инвариантен к
    перестановке — меняется лишь путь просадки). Поэтому берём выборку с возвращением
    того же размера: и итог, и просадка варьируются. Малое число сделок → узкое и
    ненадёжное распределение, что само по себе честный сигнал."""
    pnls = [t.pnl for t in res.trades]
    rng = random.Random(seed)
    finals, dds = [], []
    if not pnls:
        return MCResult(n, "trades", 0, 0, 0, 0, 0, 0, 0, 0, 0)
    k = len(pnls)
    for _ in range(n):
        sample = [rng.choice(pnls) for _ in range(k)]
        eq = [res.cash0]
        for p in sample:
            eq.append(eq[-1] + p)
        finals.append(eq[-1] / res.cash0 - 1.0)
        dds.append(_max_dd(eq))
    return MCResult(
        n=n, source="trades",
        ret_p5=_percentile(finals, 0.05), ret_p50=_percentile(finals, 0.50),
        ret_p95=_percentile(finals, 0.95),
        dd_p5=_percentile(dds, 0.05), dd_p50=_percentile(dds, 0.50),
        dd_median=_percentile(dds, 0.50),
        prob_profit=sum(1 for f in finals if f > 0) / len(finals),
        worst_ret=min(finals), best_ret=max(finals))


def bootstrap_returns(res: Result, n: int = 2000, seed: int = 0) -> MCResult:
    """Ресэмплим баровые доходности с возвращением → распределение путей."""
    eq = res.equity
    rets = [eq[i] / eq[i - 1] - 1.0 for i in range(1, len(eq)) if eq[i - 1]]
    rng = random.Random(seed)
    finals, dds = [], []
    if not rets:
        return MCResult(n, "returns", 0, 0, 0, 0, 0, 0, 0, 0, 0)
    L = len(rets)
    for _ in range(n):
        path = [res.cash0]
        for _ in range(L):
            path.append(path[-1] * (1 + rng.choice(rets)))
        finals.append(path[-1] / res.cash0 - 1.0)
        dds.append(_max_dd(path))
    return MCResult(
        n=n, source="returns",
        ret_p5=_percentile(finals, 0.05), ret_p50=_percentile(finals, 0.50),
        ret_p95=_percentile(finals, 0.95),
        dd_p5=_percentile(dds, 0.05), dd_p50=_percentile(dds, 0.50),
        dd_median=_percentile(dds, 0.50),
        prob_profit=sum(1 for f in finals if f > 0) / len(finals),
        worst_ret=min(finals), best_ret=max(finals))

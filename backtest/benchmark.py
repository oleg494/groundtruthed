"""Сравнение с бенчмарком: alpha/beta, корреляция, tracking error, information ratio,
up/down capture. Бенчмарк — любой другой Result (обычно buy&hold той же корзины).

Метрики считаются по выровненным баровым доходностям equity-кривых. Годовая нормировка
берётся из медианного шага баров (дневные → 252).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .engine import Result


def _aligned_returns(a: Result, b: Result):
    """Доходности equity двух прогонов, выровненные по общим меткам времени."""
    ea = dict(zip(a.times, a.equity))
    eb = dict(zip(b.times, b.equity))
    common = sorted(set(ea) & set(eb))
    ra, rb = [], []
    for i in range(1, len(common)):
        t0, t1 = common[i - 1], common[i]
        if ea[t0] and eb[t0]:
            ra.append(ea[t1] / ea[t0] - 1.0)
            rb.append(eb[t1] / eb[t0] - 1.0)
    dt = (common[1] - common[0]) if len(common) > 1 else 86400
    ppy = 252.0 if dt >= 86400 * 0.9 else (365.25 * 86400 / dt)
    return ra, rb, ppy


@dataclass
class Comparison:
    alpha_annual: float
    beta: float
    correlation: float
    tracking_error: float
    information_ratio: float
    up_capture: float
    down_capture: float
    excess_return: float          # суммарная доходность стратегии − бенчмарка

    def summary(self) -> str:
        return (f"vs benchmark:\n"
                f"  alpha (год.)        = {self.alpha_annual*100:+.2f}%\n"
                f"  beta                = {self.beta:.2f}\n"
                f"  корреляция          = {self.correlation:.2f}\n"
                f"  tracking error      = {self.tracking_error*100:.2f}%\n"
                f"  information ratio   = {self.information_ratio:.2f}\n"
                f"  up/down capture     = {self.up_capture*100:.0f}% / {self.down_capture*100:.0f}%\n"
                f"  избыточная дох-ть   = {self.excess_return*100:+.2f}%")


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def compare(strat: Result, benchmark: Result) -> Comparison:
    rs, rb, ppy = _aligned_returns(strat, benchmark)
    if len(rs) < 2:
        return Comparison(0, 0, 0, 0, 0, 0, 0, 0)
    ms, mb = _mean(rs), _mean(rb)
    var_b = sum((r - mb) ** 2 for r in rb) / len(rb)
    cov = sum((rs[i] - ms) * (rb[i] - mb) for i in range(len(rs))) / len(rs)
    beta = cov / var_b if var_b else 0.0
    alpha_bar = ms - beta * mb
    alpha_annual = alpha_bar * ppy
    # корреляция
    sd_s = math.sqrt(sum((r - ms) ** 2 for r in rs) / len(rs))
    sd_b = math.sqrt(var_b)
    corr = cov / (sd_s * sd_b) if sd_s and sd_b else 0.0
    # tracking error и information ratio
    active = [rs[i] - rb[i] for i in range(len(rs))]
    ma = _mean(active)
    te_bar = math.sqrt(sum((x - ma) ** 2 for x in active) / len(active))
    te = te_bar * math.sqrt(ppy)
    ir = (ma / te_bar * math.sqrt(ppy)) if te_bar else 0.0
    # up/down capture
    up_s = sum(rs[i] for i in range(len(rs)) if rb[i] > 0)
    up_b = sum(rb[i] for i in range(len(rb)) if rb[i] > 0)
    dn_s = sum(rs[i] for i in range(len(rs)) if rb[i] < 0)
    dn_b = sum(rb[i] for i in range(len(rb)) if rb[i] < 0)
    up_cap = (up_s / up_b) if up_b else 0.0
    dn_cap = (dn_s / dn_b) if dn_b else 0.0
    excess = strat.total_return - benchmark.total_return
    return Comparison(alpha_annual=alpha_annual, beta=beta, correlation=corr,
                      tracking_error=te, information_ratio=ir,
                      up_capture=up_cap, down_capture=dn_cap, excess_return=excess)

"""Метрики результата: доходность, риск, просадки, статистика сделок.

Годовая нормировка берётся из медианного шага баров (дневные → ~252 торг. дня,
часовые → свой коэффициент). Безрисковая ставка по умолчанию 0 — Sharpe/Sortino
считаются «как есть»; при желании передай rf_annual.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .engine import Result

SEC_YEAR = 365.25 * 86400


@dataclass
class Metrics:
    bars: int
    days: float
    total_return: float
    cagr: float
    ann_vol: float
    sharpe: float
    sortino: float
    max_drawdown: float          # отрицательное число, доля (−0.23 = −23%)
    max_dd_duration_bars: int
    calmar: float
    num_trades: int
    win_rate: float
    profit_factor: float
    avg_trade_ret: float
    avg_win: float
    avg_loss: float
    expectancy: float
    avg_exposure: float
    best_bar: float
    worst_bar: float
    commissions_paid: float
    final_equity: float

    def as_dict(self) -> dict:
        return asdict(self)


def _bar_returns(equity: list[float]) -> list[float]:
    return [equity[i] / equity[i - 1] - 1.0
            for i in range(1, len(equity)) if equity[i - 1]]


def _median_dt(times: list[int]) -> float:
    if len(times) < 2:
        return 86400.0
    dts = sorted(times[i] - times[i - 1] for i in range(1, len(times)))
    return float(dts[len(dts) // 2]) or 86400.0


def _max_drawdown(equity: list[float]) -> tuple[float, int]:
    """Максимальная просадка (доля, ≤0) и её длительность в барах."""
    peak = equity[0] if equity else 0.0
    mdd = 0.0
    dur = cur = 0
    peak_i = 0
    for i, v in enumerate(equity):
        if v > peak:
            peak, peak_i = v, i
        dd = v / peak - 1.0 if peak else 0.0
        if dd < mdd:
            mdd = dd
        cur = i - peak_i
        dur = max(dur, cur)
    return mdd, dur


def _ann_factor(median_dt: float) -> float:
    """Сколько баров в году. Для дневных баров используем 252 торговых дня."""
    if median_dt >= 86400 * 0.9:                 # дневные и реже
        bars_per_day = 1.0
        return 252.0 * bars_per_day
    return SEC_YEAR / median_dt                  # внутридневные — календарно


def metrics(res: Result, rf_annual: float = 0.0) -> Metrics:
    eq = res.equity or [res.cash0]
    times = res.times or [0, 86400]
    rets = _bar_returns(eq)
    median_dt = _median_dt(times)
    ppy = _ann_factor(median_dt)
    span_days = (times[-1] - times[0]) / 86400 if len(times) > 1 else 0.0
    years = max(span_days / 365.25, 1e-9)

    total = eq[-1] / res.cash0 - 1.0 if res.cash0 else 0.0
    cagr = (eq[-1] / res.cash0) ** (1 / years) - 1.0 if res.cash0 and eq[-1] > 0 else -1.0

    if rets:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        sd = math.sqrt(var)
        ann_vol = sd * math.sqrt(ppy)
        rf_bar = rf_annual / ppy
        sharpe = ((mean - rf_bar) / sd * math.sqrt(ppy)) if sd else 0.0
        downside = [min(r - rf_bar, 0.0) for r in rets]
        dd_sd = math.sqrt(sum(d * d for d in downside) / len(downside))
        sortino = ((mean - rf_bar) / dd_sd * math.sqrt(ppy)) if dd_sd else 0.0
        best, worst = max(rets), min(rets)
    else:
        ann_vol = sharpe = sortino = best = worst = 0.0

    mdd, dd_dur = _max_drawdown(eq)
    calmar = (cagr / abs(mdd)) if mdd else 0.0

    # — статистика сделок —
    trs = res.trades
    n = len(trs)
    wins = [t for t in trs if t.pnl > 0]
    losses = [t for t in trs if t.pnl < 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    win_rate = len(wins) / n if n else 0.0
    profit_factor = (gross_win / gross_loss) if gross_loss else (math.inf if gross_win else 0.0)
    avg_win = (gross_win / len(wins)) if wins else 0.0
    avg_loss = (-gross_loss / len(losses)) if losses else 0.0
    avg_trade_ret = (sum(t.ret for t in trs) / n) if n else 0.0
    expectancy = (sum(t.pnl for t in trs) / n) if n else 0.0

    avg_exp = (sum(res.exposure) / len(res.exposure)) if res.exposure else 0.0

    return Metrics(
        bars=res.bars, days=span_days, total_return=total, cagr=cagr,
        ann_vol=ann_vol, sharpe=sharpe, sortino=sortino,
        max_drawdown=mdd, max_dd_duration_bars=dd_dur, calmar=calmar,
        num_trades=n, win_rate=win_rate, profit_factor=profit_factor,
        avg_trade_ret=avg_trade_ret, avg_win=avg_win, avg_loss=avg_loss,
        expectancy=expectancy, avg_exposure=avg_exp,
        best_bar=best, worst_bar=worst,
        commissions_paid=res.commissions_paid, final_equity=eq[-1])

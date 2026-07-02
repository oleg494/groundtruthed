"""Портфельные стратегии: периодический ребаланс корзины к целевым весам.

Три схемы весов: фиксированные (заданные), равные (equal weight) и обратные к
волатильности (inverse-vol — грубое risk parity). Ребаланс раз в N баров и только
если дрейф веса превысил полосу (drift_band) — чтобы не платить комиссию на шум.
"""
from __future__ import annotations

from typing import Optional

from .engine import Strategy, Context
from . import indicators as ta


class RebalancePortfolio(Strategy):
    """Ребаланс корзины к целевым весам.

    weights:
      - dict {ticker: вес} — фиксированные веса (нормируются к сумме);
      - "equal"            — равные веса по всем тикерам данных;
      - "inverse_vol"      — веса ∝ 1/волатильность (трейлинг vol_lookback).
    invest — суммарная доля капитала в рынке (остальное — кэш-буфер).
    drift_band — ребалансим инструмент, только если |текущая доля − цель| > band.
    """
    name = "rebalance"

    def __init__(self, weights="equal", rebalance: int = 20, invest: float = 0.98,
                 drift_band: float = 0.0, vol_lookback: int = 20,
                 cap: float = 1.0):
        self.weights = weights
        self.rebalance = rebalance
        self.invest = invest
        self.drift_band = drift_band
        self.vol_lookback = vol_lookback
        self.cap = cap

    def _target_weights(self, ctx: Context) -> dict[str, float]:
        ts = [t for t in ctx.tickers() if ctx.price(t)]
        if not ts:
            return {}
        if isinstance(self.weights, dict):
            raw = {t: float(self.weights.get(t, 0.0)) for t in ts}
        elif self.weights == "inverse_vol":
            raw = {}
            for t in ts:
                rets = ta.returns(ctx.closes(t, self.vol_lookback + 1))
                sd = ta.stdev(rets, len(rets)) if rets else None
                raw[t] = (1.0 / sd) if sd else 0.0
        else:                                   # equal
            raw = {t: 1.0 for t in ts}
        total = sum(raw.values())
        if total <= 0:
            return {}
        # нормируем и применяем потолок на инструмент
        w = {t: min(v / total, self.cap) for t, v in raw.items()}
        s = sum(w.values()) or 1.0
        return {t: v / s * self.invest for t, v in w.items()}

    def on_bar(self, ctx: Context) -> None:
        if ctx.i % self.rebalance != 0:
            return
        targets = self._target_weights(ctx)
        if not targets:
            return
        eq = ctx.equity
        for t, target in targets.items():
            px = ctx.price(t)
            if not px:
                continue
            cur_notional = ctx.instrument(t).notional(px, ctx.position(t))
            cur_frac = cur_notional / eq if eq else 0.0
            if abs(cur_frac - target) > self.drift_band:
                ctx.order_target_percent(t, target)


def equal_weight(rebalance: int = 20, **kw) -> RebalancePortfolio:
    return RebalancePortfolio(weights="equal", rebalance=rebalance, **kw)


def inverse_vol(rebalance: int = 20, vol_lookback: int = 20, **kw) -> RebalancePortfolio:
    return RebalancePortfolio(weights="inverse_vol", rebalance=rebalance,
                              vol_lookback=vol_lookback, **kw)

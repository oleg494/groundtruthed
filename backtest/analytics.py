"""Аналитика на уровне сделок: серии (стрики), периоды удержания, payoff.

Дополняет metrics.py, который смотрит на equity-кривую. Здесь — взгляд со стороны
отдельных сделок: насколько длинными бывают полосы убытков (важно для психики и
риск-лимитов), как долго держится позиция, каково соотношение средней прибыли к убытку.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .engine import Result


@dataclass
class TradeAnalytics:
    num_trades: int
    max_consec_wins: int
    max_consec_losses: int
    avg_holding_bars: float
    median_holding_bars: float
    max_holding_bars: int
    largest_win: float
    largest_loss: float
    payoff_ratio: float          # средняя прибыль / средний убыток (по модулю)
    long_trades: int
    short_trades: int

    def summary(self) -> str:
        return (f"Trade analytics ({self.num_trades} сделок, "
                f"{self.long_trades} long / {self.short_trades} short):\n"
                f"  макс. серия выигрышей/проигрышей: {self.max_consec_wins} / "
                f"{self.max_consec_losses}\n"
                f"  удержание (баров): сред {self.avg_holding_bars:.1f}, "
                f"медиана {self.median_holding_bars:.0f}, макс {self.max_holding_bars}\n"
                f"  крупнейшие прибыль/убыток: {self.largest_win:+.0f} / "
                f"{self.largest_loss:+.0f} ₽\n"
                f"  payoff ratio (avg win / avg loss): {self.payoff_ratio:.2f}")


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def trade_analytics(res: Result) -> TradeAnalytics:
    trs = res.trades
    n = len(trs)
    if n == 0:
        return TradeAnalytics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    holdings = [max(t.exit_i - t.entry_i, 0) for t in trs]
    wins = [t.pnl for t in trs if t.pnl > 0]
    losses = [t.pnl for t in trs if t.pnl < 0]
    # серии
    max_w = cur_w = max_l = cur_l = 0
    for t in trs:
        if t.pnl > 0:
            cur_w += 1
            cur_l = 0
        elif t.pnl < 0:
            cur_l += 1
            cur_w = 0
        else:
            cur_w = cur_l = 0
        max_w = max(max_w, cur_w)
        max_l = max(max_l, cur_l)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    payoff = (avg_win / abs(avg_loss)) if avg_loss else (math.inf if avg_win else 0.0)
    return TradeAnalytics(
        num_trades=n, max_consec_wins=max_w, max_consec_losses=max_l,
        avg_holding_bars=sum(holdings) / n, median_holding_bars=_median(holdings),
        max_holding_bars=max(holdings),
        largest_win=max((t.pnl for t in trs), default=0.0),
        largest_loss=min((t.pnl for t in trs), default=0.0),
        payoff_ratio=payoff,
        long_trades=sum(1 for t in trs if t.side == "long"),
        short_trades=sum(1 for t in trs if t.side == "short"))

"""Продвинутый риск-анализ: хвостовые метрики, индекс язвы, топ-просадки, календарь.

metrics.py даёт стандартный набор (Sharpe/maxDD/...). Здесь — то, что показывает
ПОВЕДЕНИЕ хвоста и боль удержания: VaR/CVaR (сколько теряем в плохой день), Ulcer
(глубина×длительность просадок), Omega/gain-to-pain (асимметрия прибыль/убыток),
таблица крупнейших просадок с временем восстановления и помесячная доходность.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .engine import Result

SEC_YEAR = 365.25 * 86400


def _bar_returns(eq: list[float]) -> list[float]:
    return [eq[i] / eq[i - 1] - 1.0 for i in range(1, len(eq)) if eq[i - 1]]


def _percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    idx = q * (len(s) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def value_at_risk(returns: list[float], alpha: float = 0.05) -> float:
    """Исторический VaR: квантиль alpha распределения доходностей (≤0, «плохой день»)."""
    return _percentile(returns, alpha) if returns else 0.0


def conditional_var(returns: list[float], alpha: float = 0.05) -> float:
    """CVaR / Expected Shortfall: средняя доходность в худших alpha случаях."""
    if not returns:
        return 0.0
    var = value_at_risk(returns, alpha)
    tail = [r for r in returns if r <= var]
    return sum(tail) / len(tail) if tail else var


def ulcer_index(equity: list[float]) -> float:
    """Ulcer Index: RMS просадок в % — наказывает и глубину, и длительность."""
    if len(equity) < 2:
        return 0.0
    peak = equity[0]
    sq = 0.0
    for v in equity:
        peak = max(peak, v)
        dd = (v / peak - 1.0) * 100 if peak else 0.0
        sq += dd * dd
    return math.sqrt(sq / len(equity))


def tail_ratio(returns: list[float]) -> float:
    """Отношение правого хвоста к левому: |95-й перцентиль| / |5-й перцентиль|. >1 — хорошо."""
    if not returns:
        return 0.0
    right = abs(_percentile(returns, 0.95))
    left = abs(_percentile(returns, 0.05))
    return (right / left) if left else 0.0


def omega_ratio(returns: list[float], threshold: float = 0.0) -> float:
    """Omega: сумма доходностей выше порога / сумма недостач ниже порога."""
    gains = sum(r - threshold for r in returns if r > threshold)
    losses = -sum(r - threshold for r in returns if r < threshold)
    return (gains / losses) if losses else (math.inf if gains else 0.0)


def gain_to_pain(returns: list[float]) -> float:
    """Gain-to-Pain: сумма всех доходностей / сумма модулей отрицательных."""
    pain = sum(-r for r in returns if r < 0)
    return (sum(returns) / pain) if pain else (math.inf if sum(returns) > 0 else 0.0)


@dataclass
class DrawdownEpisode:
    start_i: int
    trough_i: int
    end_i: int               # индекс восстановления (или последний бар, если не восстановилась)
    depth: float             # доля, ≤0
    length: int              # баров от пика до восстановления
    to_trough: int           # баров от пика до дна
    recovered: bool


def drawdown_table(res: Result, top_n: int = 5) -> list[DrawdownEpisode]:
    """Крупнейшие просадки с временем до дна и восстановления."""
    eq = res.equity
    episodes: list[DrawdownEpisode] = []
    if len(eq) < 2:
        return episodes
    peak = eq[0]
    peak_i = 0
    trough = eq[0]
    trough_i = 0
    in_dd = False
    for i, v in enumerate(eq):
        if v >= peak:
            if in_dd:                       # просадка закрылась восстановлением
                episodes.append(DrawdownEpisode(
                    peak_i, trough_i, i, trough / eq[peak_i] - 1.0,
                    i - peak_i, trough_i - peak_i, True))
                in_dd = False
            peak, peak_i = v, i
            trough, trough_i = v, i
        else:
            if not in_dd:
                in_dd = True
                trough, trough_i = v, i
            elif v < trough:
                trough, trough_i = v, i
    if in_dd:                               # незакрытая просадка на конце
        episodes.append(DrawdownEpisode(
            peak_i, trough_i, len(eq) - 1, trough / eq[peak_i] - 1.0,
            len(eq) - 1 - peak_i, trough_i - peak_i, False))
    episodes.sort(key=lambda e: e.depth)
    return episodes[:top_n]


def monthly_returns(res: Result) -> dict:
    """Помесячная доходность (компаундинг баровых) → {year: {month: ret}} и {year: 'YTD'}."""
    eq, ts = res.equity, res.times
    buckets: dict[tuple[int, int], float] = {}
    order: list[tuple[int, int]] = []
    for i in range(1, len(eq)):
        if not eq[i - 1]:
            continue
        d = datetime.fromtimestamp(ts[i], tz=timezone.utc)
        key = (d.year, d.month)
        if key not in buckets:
            buckets[key] = 1.0
            order.append(key)
        buckets[key] *= eq[i] / eq[i - 1]
    table: dict[int, dict] = {}
    for (y, mth) in order:
        table.setdefault(y, {})[mth] = buckets[(y, mth)] - 1.0
    for y in table:
        ytd = 1.0
        for mth in sorted(table[y]):
            ytd *= 1 + table[y][mth]
        table[y]["YTD"] = ytd - 1.0
    return table


@dataclass
class RiskReport:
    var_95: float
    cvar_95: float
    ulcer: float
    tail_ratio: float
    omega: float
    gain_to_pain: float
    worst_drawdowns: list

    def summary(self) -> str:
        L = ["Risk report:",
             f"  VaR(5%) / CVaR(5%)  = {self.var_95*100:.2f}% / {self.cvar_95*100:.2f}% (за бар)",
             f"  Ulcer index         = {self.ulcer:.2f}",
             f"  tail ratio          = {self.tail_ratio:.2f}",
             f"  Omega(0)            = {self.omega:.2f}",
             f"  gain-to-pain        = {self.gain_to_pain:.2f}",
             "  топ просадок (глубина / до дна / восстановление, баров):"]
        for e in self.worst_drawdowns:
            rec = f"{e.length}" if e.recovered else f"{e.length}+ (не восст.)"
            L.append(f"    {e.depth*100:6.2f}%   {e.to_trough:>4}   {rec}")
        return "\n".join(L)


def risk_report(res: Result, top_n: int = 5) -> RiskReport:
    rets = _bar_returns(res.equity)
    return RiskReport(
        var_95=value_at_risk(rets, 0.05), cvar_95=conditional_var(rets, 0.05),
        ulcer=ulcer_index(res.equity), tail_ratio=tail_ratio(rets),
        omega=omega_ratio(rets), gain_to_pain=gain_to_pain(rets),
        worst_drawdowns=drawdown_table(res, top_n))


def calendar_text(res: Result) -> str:
    """Текстовая помесячная таблица доходности."""
    table = monthly_returns(res)
    if not table:
        return "(нет данных для календаря)"
    months = ["янв", "фев", "мар", "апр", "май", "июн",
              "июл", "авг", "сен", "окт", "ноя", "дек"]
    L = ["Календарь доходности (%):",
         "  год  " + "".join(f"{m:>7}" for m in months) + f"{'YTD':>8}"]
    for y in sorted(table):
        row = f"  {y} "
        for mth in range(1, 13):
            v = table[y].get(mth)
            row += f"{v*100:>7.1f}" if v is not None else f"{'·':>7}"
        row += f"{table[y]['YTD']*100:>8.1f}"
        L.append(row)
    return "\n".join(L)

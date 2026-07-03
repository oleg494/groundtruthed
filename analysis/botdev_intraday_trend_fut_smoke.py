# -*- coding: utf-8 -*-
"""Smoke-тест кандидата INTRADAY_TREND_FUT на синтетике: стратегия вообще торгует?

Генерируем 120 синтетических сессий 10:00–23:00 МСК из 30-мин баров (26 баров/день).
Половина дней — с устойчивым внутридневным дрейфом (направление задаётся сидом),
остальные — чистое блуждание. Если механика верна, стратегия должна входить после
полудня по направлению дрейфа и зарабатывать именно в дрейфовые дни.

Запуск: python analysis/botdev_intraday_trend_fut_smoke.py
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest.core import Bar, Instrument  # noqa: E402
from backtest.engine import run  # noqa: E402
from backtest.metrics import metrics  # noqa: E402
from backtest.strategies import build  # noqa: E402

MSK = 3 * 3600
BARS_PER_DAY = 26          # 10:00..23:00 МСК по 30 минут


def synth_sessions(days: int = 120, seed: int = 7, s0: float = 100.0,
                   drift_frac: float = 0.5) -> dict[str, list[Bar]]:
    """Синтетические внутридневные сессии. drift_frac дней — трендовые."""
    rng = random.Random(seed)
    bars: list[Bar] = []
    p = s0
    base = 1_767_600_000 // 86400 * 86400   # полночь UTC, старт ~2026-01
    for d in range(days):
        day_start = base + d * 86400 + 7 * 3600   # 10:00 МСК = 07:00 UTC
        trend_day = rng.random() < drift_frac
        drift = rng.choice([-1, 1]) * 0.0012 if trend_day else 0.0
        for i in range(BARS_PER_DAY):
            o = p
            r = drift + rng.gauss(0, 0.0018)
            c = o * (1 + r)
            hi = max(o, c) * (1 + rng.uniform(0, 0.0008))
            lo = min(o, c) * (1 - rng.uniform(0, 0.0008))
            bars.append(Bar(t=day_start + i * 1800, o=round(o, 6), h=round(hi, 6),
                            l=round(lo, 6), c=round(c, 6),
                            v=round(rng.uniform(1e3, 1e5))))
            p = c
    return {"SYN": bars}


def main() -> None:
    data = synth_sessions()
    inst = {"SYN": Instrument("SYN", multiplier=1.0, kind="futures")}
    for params in ({}, {"adx_min": 20.0}, {"take_r": 2.0, "stop_mult": 1.0}):
        res = run(build("intraday_trend", **params), data, cash=100_000,
                  commission=0.0005, slippage=0.0005, instruments=inst)
        m = metrics(res)
        print(f"params={params}: trades={m.num_trades} "
              f"ret={m.total_return*100:+.2f}% sharpe={m.sharpe:.2f} "
              f"winrate={m.win_rate*100:.0f}% pf={m.profit_factor:.2f}")


if __name__ == "__main__":
    main()

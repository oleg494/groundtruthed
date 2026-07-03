"""DAILY_BREAKOUT_FUT — пробой дневного Дончиана на 30-мин фьючерсах: полный конвейер.

Гипотеза (вторая волна, инверсия kill-пула 2026-07-02): убиты оба УТРЕННИХ режима
(ORB и его контртренд), но пробой N-ДНЕВНОГО диапазона, исполняемый внутри дня по
30-мин барам, — другой класс (позиционный пробой, позиция живёт дни, ночёвки есть).
Стратегия `daily_breakout` в backtest/strategies.py.

Конвейер: smoke на синтетике → IS grid (фиксируем число испытаний) → anchored
walk-forward → Deflated Sharpe → сравнение с buyhold и random на тех же данных.
Данные: 30-мин свечи из кэша backtest/.cache (fetch read-only через sandbox-домен).
Комиссия/слиппедж — как у первой волны (0.05%+0.05%) для сопоставимости.

Запуск:  python analysis/botdev2_daily_breakout_fut.py
"""
from __future__ import annotations

import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest import candles, strategies  # noqa: E402
from backtest import robust as robust_mod  # noqa: E402
from backtest.core import Bar, Instrument  # noqa: E402
from backtest.engine import run  # noqa: E402
from backtest.metrics import metrics  # noqa: E402
from backtest.optimize import grid_search, walk_forward  # noqa: E402
from lab.instruments import INSTRUMENTS  # noqa: E402

CASH = 100_000.0
COMMISSION = 0.0005
SLIPPAGE = 0.0005
INTERVAL = "CANDLE_INTERVAL_30_MIN"
DAYS = 120                      # ключ кэша; from_tinvest фактически берёт days*1.5
TICKERS = ["BMQ6", "NGN6", "GLDRUBF"]

# Скромная сетка: N дневного канала, обратный канал M<N, ATR-трейл (0 = выкл).
# 4 × 3 × 3 = 36 комбо минус несовместимые (exit_m>=n) → фиксируем фактическое число.
GRID = {
    "n":          [5, 10, 15, 20],
    "exit_m":     [3, 5, 10],
    "trail_mult": [0, 2.0, 3.0],
}
WF_SPLITS = 4


def grid_valid_size(g: dict) -> int:
    """Число ВАЛИДНЫХ комбинаций (assert exit_m<n отсекает часть сетки)."""
    n = 0
    import itertools
    for combo in itertools.product(*g.values()):
        p = dict(zip(g.keys(), combo))
        if p["exit_m"] < p["n"]:
            n += 1
    return n


# ───────── синтетика: 30-мин лента с многодневными трендами (для smoke) ─────────
def synth_multiday_trend(days: int = 160, seed: int = 3) -> dict[str, list[Bar]]:
    """30-мин бары 10:00–23:00 МСК; режимы по ~40 дней: ап-тренд → даун-тренд → …

    Благоприятный для дневного пробоя мир: тренды живут недели. Смоук: стратегия
    обязана торговать в обе стороны и не сливать на таком рынке."""
    rng = random.Random(seed)
    bars: list[Bar] = []
    day0 = 1_767_225_600            # 2026-01-01 00:00 UTC
    p = 100.0
    for d in range(days):
        drift = 0.004 if (d // 40) % 2 == 0 else -0.004     # за день
        base = day0 + d * 86400 + 7 * 3600                  # 07:00 UTC = 10:00 МСК
        for i in range(26):
            p *= math.exp(drift / 26 + rng.gauss(0, 0.0011))
            o = bars[-1].c if bars else p
            hi, lo = max(o, p) * 1.0006, min(o, p) * 0.9994
            bars.append(Bar(t=base + i * 1800, o=round(o, 4), h=round(hi, 4),
                            l=round(lo, 4), c=round(p, 4), v=1000))
    return {"SYN": bars}


def smoke() -> bool:
    data = synth_multiday_trend()
    res = run(strategies.build("daily_breakout", n=10, exit_m=5, trail_mult=0),
              data, cash=CASH, commission=COMMISSION, slippage=SLIPPAGE)
    m = metrics(res)
    longs = sum(1 for tr in res.trades if tr.side == "long")
    shorts = len(res.trades) - longs
    # ночёвки: хотя бы одна сделка длиннее одного дня (26 баров)
    multi_day = sum(1 for tr in res.trades if tr.exit_i - tr.entry_i > 26)
    print(f"SMOKE synth_multiday_trend(160 дн): сделок {m.num_trades} "
          f"(лонгов {longs}, шортов {shorts}, многодневных {multi_day}), "
          f"ret {m.total_return*100:+.2f}%, WR {m.win_rate*100:.0f}%, "
          f"PF {m.profit_factor:.2f}")
    res2 = run(strategies.build("daily_breakout", n=10, exit_m=5, trail_mult=2.0),
               data, cash=CASH, commission=COMMISSION, slippage=SLIPPAGE)
    m2 = metrics(res2)
    print(f"SMOKE с трейлом 2.0×ATR: сделок {m2.num_trades}, ret {m2.total_return*100:+.2f}%")
    return (m.num_trades > 0 and longs > 0 and shorts > 0 and multi_day > 0
            and m.total_return > 0)


# ───────────────────── реальные данные ─────────────────────
def load(ticker: str) -> list[Bar]:
    uid = INSTRUMENTS[ticker]["uid"]
    data = candles.from_tinvest(uid, ticker, days=DAYS, interval=INTERVAL)
    return data[ticker]


def mrow(m) -> dict:
    return {"ret_pct": round(m.total_return * 100, 2), "sharpe": round(m.sharpe, 2),
            "maxdd_pct": round(m.max_drawdown * 100, 2), "trades": m.num_trades,
            "winrate": round(m.win_rate * 100, 1), "pf": round(m.profit_factor, 2)}


def pipeline(ticker: str, grid: dict, wf_splits: int) -> dict:
    bars = load(ticker)
    mult = INSTRUMENTS[ticker]["point_rub"]
    data = {ticker: bars}
    inst = {ticker: Instrument(ticker, multiplier=mult, kind="futures")}
    kw = dict(cash=CASH, commission=COMMISSION, slippage=SLIPPAGE)
    d0 = datetime.fromtimestamp(bars[0].t, timezone.utc).date()
    d1 = datetime.fromtimestamp(bars[-1].t, timezone.utc).date()
    print(f"\n━━ {ticker}: {len(bars)} баров 30-мин, {d0} → {d1}, point_rub={mult}")

    # 1. IS grid по всей ленте (In-Sample по построению!)
    pts = grid_search(strategies.REGISTRY["daily_breakout"], data, grid,
                      metric="sharpe", instruments=inst, **kw)
    n_trials = grid_valid_size(grid)
    print(f"  grid: {n_trials} валидных комбинаций, прогонов {len(pts)}")
    best = pts[0]
    bm = best.metrics
    print(f"  IS best: {best.params}")
    print(f"  IS best: ret {bm.total_return*100:+.2f}%  Sharpe {bm.sharpe:+.2f}  "
          f"maxDD {bm.max_drawdown*100:.2f}%  сделок {bm.num_trades}  "
          f"WR {bm.win_rate*100:.1f}%  PF {bm.profit_factor:.2f}")

    # 2. Deflated Sharpe лучшей точки с поправкой на размер сетки
    rb = robust_mod.assess(best.result, pts, metric="sharpe")
    print(f"  DSR (×{rb.n_trials} исп.): {rb.deflated_sharpe*100:.1f}%   "
          f"PSR: {rb.psr*100:.1f}%   доля положит. точек сетки: "
          f"{rb.sens_frac_positive*100:.0f}%")

    # 3. Anchored walk-forward: та же сетка
    wf = walk_forward(strategies.REGISTRY["daily_breakout"], data, grid,
                      n_splits=wf_splits, metric="sharpe", instruments=inst, **kw)
    wrows = []
    for i, w in enumerate(wf.windows):
        o = w.oos
        lo = datetime.fromtimestamp(w.oos_range[0], timezone.utc).date()
        hi = datetime.fromtimestamp(w.oos_range[1], timezone.utc).date()
        print(f"  WF{i+1} OOS {lo}..{hi}: ret {o.total_return*100:+7.2f}%  "
              f"Sharpe {o.sharpe:+6.2f}  сделок {o.num_trades:>3}  {w.best_params}")
        wrows.append({"oos": f"{lo}..{hi}", "ret_pct": round(o.total_return * 100, 2),
                      "sharpe": round(o.sharpe, 2), "trades": o.num_trades,
                      "params": w.best_params})
    oos_ret = wf.oos_return()
    print(f"  WF сквозной OOS: {oos_ret*100:+.2f}%")

    # 4. Бенчмарки на тех же данных/модели
    bh = metrics(run(strategies.build("buyhold"), data, instruments=inst, **kw))
    rnd = [metrics(run(strategies.build("random", seed=s), data, instruments=inst, **kw))
           for s in range(20)]
    rnd_ret = sorted(m.total_return for m in rnd)
    rnd_med = rnd_ret[len(rnd_ret) // 2]
    print(f"  buyhold: ret {bh.total_return*100:+.2f}%  Sharpe {bh.sharpe:+.2f}  "
          f"maxDD {bh.max_drawdown*100:.2f}%")
    print(f"  random(20 сидов): медиана ret {rnd_med*100:+.2f}%  "
          f"[{rnd_ret[0]*100:+.2f}% .. {rnd_ret[-1]*100:+.2f}%]")

    return {"bars": len(bars), "period": f"{d0}..{d1}", "n_trials": n_trials,
            "is_best": {"params": best.params, **mrow(bm)},
            "dsr": round(rb.deflated_sharpe, 4), "psr": round(rb.psr, 4),
            "frac_positive": round(rb.sens_frac_positive, 3),
            "wf": wrows, "wf_oos_ret_pct": round(oos_ret * 100, 2),
            "buyhold": mrow(bh),
            "random_median_ret_pct": round(rnd_med * 100, 2),
            "random_range_pct": [round(rnd_ret[0] * 100, 2), round(rnd_ret[-1] * 100, 2)]}


def main() -> None:
    print(f"DAILY_BREAKOUT_FUT конвейер: 30-мин, комиссия {COMMISSION*100:.2f}% + "
          f"слип {SLIPPAGE*100:.2f}%, сетка {grid_valid_size(GRID)} валидных комбинаций, "
          f"WFA {WF_SPLITS} окна (anchored)\n")

    if not smoke():
        print("SMOKE FAIL: стратегия не торгует/сливает на благоприятной синтетике — стоп")
        sys.exit(1)

    out = {"generated": datetime.now(timezone.utc).isoformat(), "interval": "30min",
           "commission": COMMISSION, "slippage": SLIPPAGE,
           "grid": {k: [str(x) for x in v] for k, v in GRID.items()},
           "n_trials_per_ticker": grid_valid_size(GRID), "results": {}}
    for tk in TICKERS:
        try:
            out["results"][tk] = pipeline(tk, GRID, WF_SPLITS)
        except Exception as e:                              # noqa: BLE001
            print(f"  {tk}: ОШИБКА {e}")
            out["results"][tk] = {"error": str(e)}

    path = ROOT / "analysis" / "botdev2_daily_breakout_fut.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nJSON: {path}")


if __name__ == "__main__":
    main()

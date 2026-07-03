"""ORB_REVERSAL — контртренд ложных пробоев утреннего диапазона: полный конвейер.

Гипотеза (deep/orb_moex_analysis.md): классический ORB на MOEX структурно убыточен
(~66% ложных пробоев, подтверждено нашим же прогоном analysis/orb_backtest.json:
-39..-44% на BMQ6/NGN6/GLDRUBF) → торгуем ПРОТИВ пробоя (reversal=True в стратегии
`orb`), с фильтрами флэтового режима (Hurst<=, ADX<=) и ATR-коридором ширины диапазона.

Конвейер: smoke на синтетике → IS grid (фиксируем число испытаний) → anchored
walk-forward → Deflated Sharpe → сравнение с buyhold и random на тех же данных.
Данные: 30-мин свечи из кэша backtest/.cache (fetch read-only через sandbox-домен).
Комиссия/слиппедж — те же, что в analysis/orb_backtest.py (0.05%+0.05%), для
сопоставимости с чистым ORB.

Запуск:  python analysis/botdev_orb_reversal.py [--fast]
"""
from __future__ import annotations

import json
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
DAYS = 120                      # ключ кэша; фактически from_tinvest берёт days*1.5
TICKERS = ["BMQ6", "NGN6", "GLDRUBF"]

# Сетка (модифицируется --fast). reversal=True и range_bars=1 фиксированы.
# 0 = фильтр выключен. Пороги — из deep/market_regime_moex.md (ADX<20 флэт, Hurst<0.5
# антиперсистентность) и deep/orb_moex_analysis.md (ATR-коридор 0.5–1.5x).
GRID = {
    "reversal":     [True],
    "range_bars":   [1],
    "hurst_max":    [0, 0.5, 0.55],
    "adx_min":      [0, 20, 25],          # для reversal это ВЕРХНИЙ порог ADX
    "atr_mult_min": [0, 0.5],
    "atr_mult_max": [0, 1.5],
    "take_r":       [1.0, 1.5, 2.0],
    "risk_cap":     [0.003, 0.005],
}
WF_SPLITS = 4


def grid_size(g: dict) -> int:
    n = 1
    for v in g.values():
        n *= len(v)
    return n


# ───────────────────── синтетика: 30-мин день с ложными пробоями ─────────────────────
def synth_intraday(days: int = 60, seed: int = 7) -> dict[str, list[Bar]]:
    """30-мин бары 10:00–23:00 МСК: утренний выброс за диапазон и возврат к среднему.

    Специально благоприятный для reversal мир: первые бары дня задают диапазон, затем
    цена «пробивает» его и возвращается (OU-процесс). Смоук: стратегия должна торговать.
    """
    rng = random.Random(seed)
    bars: list[Bar] = []
    day0 = 1_767_225_600            # 2026-01-01 00:00 UTC
    p = 100.0
    for d in range(days):
        base = day0 + d * 86400 + 7 * 3600      # 07:00 UTC = 10:00 МСК
        mean = p
        for i in range(26):                     # 26 баров по 30 мин до 23:00 МСК
            if i == 2:                          # утренний ложный выброс
                p = mean * (1 + rng.choice([-1, 1]) * rng.uniform(0.004, 0.01))
            else:                               # возврат к среднему + шум
                p += 0.25 * (mean - p) + mean * rng.gauss(0, 0.0012)
            o = bars[-1].c if bars else p
            hi, lo = max(o, p) * 1.0008, min(o, p) * 0.9992
            bars.append(Bar(t=base + i * 1800, o=round(o, 4), h=round(hi, 4),
                            l=round(lo, 4), c=round(p, 4), v=1000))
    return {"SYN": bars}


def smoke() -> bool:
    data = synth_intraday()
    res = run(strategies.build("orb", reversal=True, range_bars=1, take_r=1.5),
              data, cash=CASH, commission=COMMISSION, slippage=SLIPPAGE)
    m = metrics(res)
    longs = sum(1 for tr in res.trades if tr.side == "long")
    shorts = len(res.trades) - longs
    print(f"SMOKE synth_intraday(60 дн): сделок {m.num_trades} "
          f"(лонгов {longs}, шортов {shorts}), ret {m.total_return*100:+.2f}%, "
          f"WR {m.win_rate*100:.0f}%, PF {m.profit_factor:.2f}")
    return m.num_trades > 0 and longs > 0 and shorts > 0


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

    # 1. IS grid по всей ленте (это In-Sample по построению!)
    pts = grid_search(strategies.REGISTRY["orb"], data, grid,
                      metric="sharpe", instruments=inst, **kw)
    n_trials = grid_size(grid)
    print(f"  grid: {n_trials} комбинаций, валидных прогонов {len(pts)}")
    best = pts[0]
    bm = best.metrics
    print(f"  IS best: {best.params}")
    print(f"  IS best: ret {bm.total_return*100:+.2f}%  Sharpe {bm.sharpe:+.2f}  "
          f"maxDD {bm.max_drawdown*100:.2f}%  сделок {bm.num_trades}  "
          f"WR {bm.win_rate*100:.1f}%  PF {bm.profit_factor:.2f}")

    # 2. Deflated Sharpe лучшей точки с поправкой на размер сетки
    rb = robust_mod.assess(best.result, pts, metric="sharpe")
    print(f"  DSR (×{rb.n_trials} исп.): {rb.deflated_sharpe*100:.1f}%   "
          f"PSR: {rb.psr*100:.1f}%   доля положит. точек сетки: {rb.sens_frac_positive*100:.0f}%")

    # 3. Anchored walk-forward: та же сетка, {wf_splits} окон
    wf = walk_forward(strategies.REGISTRY["orb"], data, grid, n_splits=wf_splits,
                      metric="sharpe", instruments=inst, **kw)
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
    fast = "--fast" in sys.argv
    grid = dict(GRID)
    if fast:
        grid.update(hurst_max=[0, 0.55], adx_min=[0, 25], take_r=[1.5], risk_cap=[0.005])
    print(f"ORB_REVERSAL конвейер: 30-мин, комиссия {COMMISSION*100:.2f}% + "
          f"слип {SLIPPAGE*100:.2f}%, сетка {grid_size(grid)} комбинаций, "
          f"WFA {WF_SPLITS} окон (anchored)\n")

    ok = smoke()
    if not ok:
        print("SMOKE FAIL: стратегия не торгует на благоприятной синтетике — стоп")
        sys.exit(1)

    out = {"generated": datetime.now(timezone.utc).isoformat(), "interval": "30min",
           "commission": COMMISSION, "slippage": SLIPPAGE,
           "grid": {k: [str(x) for x in v] for k, v in grid.items()},
           "n_trials_per_ticker": grid_size(grid), "results": {}}
    for tk in TICKERS:
        try:
            out["results"][tk] = pipeline(tk, grid, WF_SPLITS)
        except Exception as e:                              # noqa: BLE001
            print(f"  {tk}: ОШИБКА {e}")
            out["results"][tk] = {"error": str(e)}

    path = ROOT / "analysis" / "botdev_orb_reversal.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nJSON: {path}")


if __name__ == "__main__":
    main()

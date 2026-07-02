"""Кандидат MEANREV_HURST: Боллинджер-реверсия с Hurst-гейтом на корзине акций MOEX.

Гипотеза (deep/market_regime_moex.md): возврат к среднему работает в
антиперсистентном режиме (Hurst<0.45). Старый meanrev (Boll 20/2) архивирован
в ферме после Deflated Sharpe=0% БЕЗ фильтра — проверяем, оживает ли эдж с гейтом.

Полный конвейер: фетч дневных свечей (sandbox, кэш) → grid (in-sample) →
anchored walk-forward с прогревом → Deflated Sharpe → сравнение с buyhold и random
(на полной ленте И на OOS-отрезке). Вердикт survive только если OOS>0, DSR>0.5
и стратегия бьёт оба бенчмарка.

Read-only, заявок не размещает. Запуск:  python analysis/botdev_meanrev_hurst.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest import candles, strategies                    # noqa: E402
from backtest.engine import run                              # noqa: E402
from backtest.metrics import metrics                         # noqa: E402
from backtest.optimize import grid_search                    # noqa: E402
from backtest.robust import assess                           # noqa: E402
from lab.instruments import INSTRUMENTS                      # noqa: E402

DAYS = 1000            # календарных; from_tinvest берёт days*1.5 → старт ~2022-05,
                       # февраль-2022 не пересекаем (граница режима, deep/walk_forward_moex.md)
CASH = 100_000.0
COMMISSION = 0.0005
SLIPPAGE = 0.0005

# корзина: все акции из lab/instruments (SBER + BASKET)
UIDS = {t: m["uid"] for t, m in INSTRUMENTS.items() if m["kind"] == "share"}

# скромная сетка 3×3×4 = 36 испытаний (фиксируем для DSR). hurst_n=100 ФИКС
# (deep/walk_forward_moex.md: пороги режима не оптимизировать; окно 100-200 из
# литературы). hurst_max=1.5 — контроль «гейт выключен» = старый meanrev.
GRID = {"bb_n": [15, 20, 30], "bb_k": [1.5, 2.0, 2.5],
        "hurst_max": [0.42, 0.45, 0.50, 1.5]}
N_TRIALS = 36
N_SPLITS = 4


def fetch():
    data = {}
    for tk, uid in UIDS.items():
        try:
            d = candles.from_tinvest(uid, tk, days=DAYS)
            data[tk] = d[tk]
            print(f"  {tk}: {len(d[tk])} баров")
        except Exception as e:                               # noqa: BLE001
            print(f"  {tk}: фетч не удался ({e}) — пропуск")
    # выравнивание по общим таймстемпам (у бумаг бывают разные торговые дни)
    common = set.intersection(*[{b.t for b in bars} for bars in data.values()])
    return {t: [b for b in sorted(bars, key=lambda x: x.t) if b.t in common]
            for t, bars in data.items()}


def _oos_stats(res, is_hi):
    """Метрики OOS-участка: equity от первого бара >= is_hi."""
    i0 = next(i for i, tt in enumerate(res.times) if tt >= is_hi)
    eq = res.equity[i0:]
    oos_ret = eq[-1] / eq[0] - 1.0
    ntr = sum(1 for tr in res.trades
              if tr.exit_i < len(res.times) and res.times[tr.exit_i] >= is_hi)
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1]]
    if rets:
        mu = sum(rets) / len(rets)
        sd = (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5
        sh = (mu / sd * 252 ** 0.5) if sd else 0.0
    else:
        sh = 0.0
    return oos_ret, sh, ntr


def wfa_warm(data, grid, n_splits, **kw):
    """Anchored WFA с ПРОГРЕВОМ: штатный optimize.walk_forward режет OOS-окно без
    истории, и при hurst_n=100 стратегия полокна молчит. Здесь best-параметры с IS
    гонятся на [oos_lo - warmup, oos_hi), метрики — только по OOS-участку."""
    times = sorted({b.t for bars in data.values() for b in bars})
    n = len(times)
    seg = n // (n_splits + 1)
    out = []
    for k in range(1, n_splits + 1):
        is_hi = times[k * seg]
        oos_hi = times[(k + 1) * seg] if (k + 1) * seg < n else times[-1] + 1
        is_data = {t: [b for b in bars if b.t < is_hi] for t, bars in data.items()}
        pts = grid_search(strategies.MeanRevHurst, is_data, grid,
                          metric="sharpe", **kw)
        best = pts[0]
        warm = max(best.params["bb_n"], 100) + 5      # hurst_n=100 фикс
        lo_idx = max(0, k * seg - warm)
        run_data = {t: [b for b in bars if times[lo_idx] <= b.t < oos_hi]
                    for t, bars in data.items()}
        res = run(strategies.MeanRevHurst(**best.params), run_data, **kw)
        oos_ret, sh, ntr = _oos_stats(res, is_hi)
        out.append({"params": best.params, "is_sharpe": best.metrics.sharpe,
                    "ret": oos_ret, "sharpe": sh, "trades": ntr})
    total = 1.0
    for w in out:
        total *= 1 + w["ret"]
    return out, total - 1.0


def show(tag, m):
    print(f"{tag:44} ret {m.total_return*100:+8.2f}%  Sharpe {m.sharpe:+6.2f}  "
          f"dd {m.max_drawdown*100:7.2f}%  PF {m.profit_factor:5.2f}  "
          f"сделок {m.num_trades}")


def main():
    print("=== фетч корзины ===")
    data = fetch()
    n = min(len(b) for b in data.values())
    import datetime
    ts_all = sorted({b.t for bars in data.values() for b in bars})
    t0 = datetime.datetime.utcfromtimestamp(ts_all[0])
    t1 = datetime.datetime.utcfromtimestamp(ts_all[-1])
    print(f"корзина: {len(data)} бумаг × {n} общих баров ({t0:%Y-%m-%d} … {t1:%Y-%m-%d})\n")

    kw = dict(cash=CASH, commission=COMMISSION, slippage=SLIPPAGE)

    # 1. grid in-sample (полная лента — для DSR и ориентира; вердикт по WFA)
    print(f"=== grid {GRID} → {N_TRIALS} испытаний ===")
    pts = grid_search(strategies.MeanRevHurst, data, GRID, metric="sharpe", **kw)
    for p in pts[:6]:
        show(str(p.params), p.metrics)
    print("  … контроль без гейта (hurst_max=1.5), топ по Sharpe среди них:")
    nogate = [p for p in pts if p.params["hurst_max"] == 1.5]
    for p in nogate[:2]:
        show("  " + str(p.params), p.metrics)
    best = pts[0]

    # 2. anchored walk-forward (с прогревом истории)
    print(f"\n=== anchored walk-forward (warm-up), {N_SPLITS} окон ===")
    windows, oos_total = wfa_warm(data, GRID, N_SPLITS, **kw)
    for i, w in enumerate(windows):
        print(f"  окно {i+1}: OOS ret {w['ret']*100:+7.2f}%  Sharpe {w['sharpe']:+6.2f}  "
              f"сделок {w['trades']:3}  IS Sharpe {w['is_sharpe']:+.2f}  "
              f"params {w['params']}")
    print(f"  сквозной OOS: {oos_total*100:+.2f}%")
    oos_trades = sum(w["trades"] for w in windows)
    print(f"  всего OOS-сделок: {oos_trades} "
          f"({'≥30 на шаг НЕ выполняется' if any(w['trades'] < 30 for w in windows) else 'ок'})")

    # 3. Deflated Sharpe (испытания = точки сетки)
    rob = assess(best.result, pts, metric="sharpe")
    print(f"\n=== робастность ===\n{rob.summary()}")

    # 4. бенчмарки: полная лента + OOS-отрезок (последние 4/5 ленты)
    print("\n=== бенчмарки (полная лента) ===")
    bh_res = run(strategies.build("buyhold"), data, **kw)
    rnd_res = run(strategies.build("random", seed=1), data, **kw)
    bh, rnd = metrics(bh_res), metrics(rnd_res)
    show("buyhold", bh)
    show("random(seed=1)", rnd)
    show(f"best IS {best.params}", best.metrics)

    times = sorted({b.t for bars in data.values() for b in bars})
    seg = len(times) // (N_SPLITS + 1)
    oos_start = times[seg]
    bh_oos, _, _ = _oos_stats(bh_res, oos_start)
    rnd_oos, _, _ = _oos_stats(rnd_res, oos_start)
    print(f"\n=== OOS-отрезок ({datetime.datetime.utcfromtimestamp(oos_start):%Y-%m-%d} …) ===")
    print(f"  стратегия (сшитый WFA): {oos_total*100:+.2f}%")
    print(f"  buyhold:                {bh_oos*100:+.2f}%")
    print(f"  random(seed=1):         {rnd_oos*100:+.2f}%")

    # 5. вердикт
    oos_pos = oos_total > 0
    dsr_ok = rob.deflated_sharpe > 0.5
    beats = oos_total > bh_oos and oos_total > rnd_oos
    print(f"\nOOS>0: {oos_pos}   DSR>0.5: {dsr_ok} ({rob.deflated_sharpe:.3f})   "
          f"бьёт buyhold и random на OOS: {beats}")
    print("ВЕРДИКТ:", "SURVIVE — кандидат в ферму" if (oos_pos and dsr_ok and beats)
          else "KILL — эдж не доказан")


if __name__ == "__main__":
    main()

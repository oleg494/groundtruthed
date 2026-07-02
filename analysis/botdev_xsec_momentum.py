"""Кандидат XSEC_MOMENTUM: кросс-секционный 12-1 моментум на корзине акций MOEX.

Полный конвейер: фетч дневных свечей (sandbox, кэш) → grid (in-sample) →
anchored walk-forward → Deflated Sharpe → сравнение с buyhold и random.
Вердикт survive только если OOS>0, DSR>0.5 и стратегия бьёт оба бенчмарка.

Read-only, заявок не размещает. Запуск:  python analysis/botdev_xsec_momentum.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest import candles, strategies                    # noqa: E402
from backtest.engine import run                              # noqa: E402
from backtest.metrics import metrics                         # noqa: E402
from backtest.optimize import grid_search, walk_forward      # noqa: E402
from backtest.robust import assess                           # noqa: E402
from lab.instruments import INSTRUMENTS                      # noqa: E402

DAYS = 1000            # календарных; старт ~2023-10 — февраль-2022 не пересекаем
CASH = 100_000.0
COMMISSION = 0.0005
SLIPPAGE = 0.0005

# 11 ликвидных бумаг: 7 из lab/instruments + 4 с uid из репо (dashboard/validate)
EXTRA = {
    "NVTK": "0da66728-6c30-44c4-9264-df8fac2467ee",
    "MGNT": "ca845f68-6c43-44bc-b584-330d2a1e5eb7",
    "TATN": "88468f6c-c67a-4fb4-a006-53eed803883c",
    "VTBR": "8e2b0325-0292-4654-8a18-4f63ed3b0e09",
}
UIDS = {t: m["uid"] for t, m in INSTRUMENTS.items() if m["kind"] == "share"}
UIDS.update(EXTRA)

# скромная сетка: 4×2×2×2 = 32 испытания (фиксируем для DSR)
GRID = {"lookback": [63, 126, 189, 252], "skip": [0, 21],
        "rebalance": [10, 21], "top": [2, 3]}
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


def wfa_warm(data, grid, n_splits, **kw):
    """Anchored WFA с ПРОГРЕВОМ: штатный optimize.walk_forward режет OOS-окно без
    истории, и при lookback~189 стратегия полокна молчит (0-6 сделок — не оценка).
    Здесь best-параметры с IS гонятся на [oos_lo - warmup, oos_hi), а метрики
    считаются только по OOS-участку equity-кривой."""
    times = sorted({b.t for bars in data.values() for b in bars})
    n = len(times)
    seg = n // (n_splits + 1)
    out, capital = [], kw.get("cash", CASH)
    for k in range(1, n_splits + 1):
        is_hi = times[k * seg]
        oos_hi = times[(k + 1) * seg] if (k + 1) * seg < n else times[-1] + 1
        is_data = {t: [b for b in bars if b.t < is_hi] for t, bars in data.items()}
        pts = grid_search(strategies.XSecMomentum, is_data, grid,
                          metric="sharpe", **kw)
        best = pts[0]
        warm = best.params["lookback"] + best.params.get("skip", 0) + 5
        lo_idx = max(0, k * seg - warm)
        run_data = {t: [b for b in bars if times[lo_idx] <= b.t < oos_hi]
                    for t, bars in data.items()}
        res = run(strategies.XSecMomentum(**best.params), run_data, **kw)
        # OOS-метрики: equity от первого бара >= is_hi (граница OOS)
        i0 = next(i for i, tt in enumerate(res.times) if tt >= is_hi)
        eq = res.equity[i0:]
        oos_ret = eq[-1] / eq[0] - 1.0
        ntr = sum(1 for tr in res.trades
                  if tr.exit_i < len(res.times) and res.times[tr.exit_i] >= is_hi)
        # побаровый Sharpe OOS-участка, годовая нормировка 252
        rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1]]
        mu = sum(rets) / len(rets)
        sd = (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5
        sh = (mu / sd * 252 ** 0.5) if sd else 0.0
        capital *= 1 + oos_ret
        out.append({"params": best.params, "is_sharpe": best.metrics.sharpe,
                    "ret": oos_ret, "sharpe": sh, "trades": ntr})
    total = 1.0
    for w in out:
        total *= 1 + w["ret"]
    return out, total - 1.0


def show(tag, m):
    print(f"{tag:24} ret {m.total_return*100:+8.2f}%  Sharpe {m.sharpe:+6.2f}  "
          f"dd {m.max_drawdown*100:7.2f}%  PF {m.profit_factor:5.2f}  "
          f"сделок {m.num_trades}")


def main():
    print("=== фетч корзины ===")
    data = fetch()
    n = min(len(b) for b in data.values())
    import datetime
    t0 = datetime.datetime.utcfromtimestamp(min(b.t for b in data[next(iter(data))]))
    t1 = datetime.datetime.utcfromtimestamp(max(b.t for b in data[next(iter(data))]))
    print(f"корзина: {len(data)} бумаг × {n} общих баров ({t0:%Y-%m-%d} … {t1:%Y-%m-%d})\n")

    kw = dict(cash=CASH, commission=COMMISSION, slippage=SLIPPAGE)

    # 1. grid in-sample (полная лента — только для DSR и ориентира, вердикт по WFA)
    print(f"=== grid {GRID} → {4*2*2*2} испытаний ===")
    pts = grid_search(strategies.XSecMomentum, data, GRID, metric="sharpe", **kw)
    for p in pts[:5]:
        show(str(p.params), p.metrics)
    best = pts[0]

    # 2. anchored walk-forward (с прогревом истории — иначе lookback съедает полокна)
    print(f"\n=== anchored walk-forward (warm-up), {N_SPLITS} окон ===")
    windows, oos_total = wfa_warm(data, GRID, N_SPLITS, **kw)
    for i, w in enumerate(windows):
        print(f"  окно {i+1}: OOS ret {w['ret']*100:+7.2f}%  Sharpe {w['sharpe']:+6.2f}  "
              f"сделок {w['trades']:3}  IS Sharpe {w['is_sharpe']:+.2f}  "
              f"params {w['params']}")
    print(f"  сквозной OOS: {oos_total*100:+.2f}%")

    # 3. Deflated Sharpe (испытания = точки сетки)
    rob = assess(best.result, pts, metric="sharpe")
    print(f"\n=== робастность ===\n{rob.summary()}")

    # 4. бенчмарки на тех же данных
    print("\n=== бенчмарки ===")
    bh = metrics(run(strategies.build("buyhold"), data, **kw))
    rnd = metrics(run(strategies.build("random", seed=1), data, **kw))
    show("buyhold", bh)
    show("random(seed=1)", rnd)
    show(f"best IS {best.params}", best.metrics)

    # 5. вердикт
    oos_pos = oos_total > 0
    dsr_ok = rob.deflated_sharpe > 0.5
    beats = (best.metrics.total_return > bh.total_return
             and best.metrics.total_return > rnd.total_return)
    print(f"\nOOS>0: {oos_pos}   DSR>0.5: {dsr_ok} ({rob.deflated_sharpe:.3f})   "
          f"бьёт buyhold и random (IS): {beats}")
    print("ВЕРДИКТ:", "SURVIVE — кандидат в ферму" if (oos_pos and dsr_ok and beats)
          else "KILL — эдж не доказан")


if __name__ == "__main__":
    main()

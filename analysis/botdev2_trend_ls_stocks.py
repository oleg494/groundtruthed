"""Кандидат TREND_LS_STOCKS (вторая волна): лонг-шорт Donchian-тренд на корзине акций.

Гипотеза — инверсия диагноза MEANREV_HURST первой волны: MOEX пост-2022 персистентен
(медианный Hurst 0.56–0.64), meanrev там убит структурно — проверяем трендовое
зеркало на акциях, ЛОНГ И ШОРТ (первая волна гоняла тренд только на золоте лонг-онли).
Ставка за шорт КС+2% = 16.25% годовых на нотионал шортовой ноги — ежебарный кост.

Полный конвейер: фетч дневных свечей (sandbox, кэш) → grid (in-sample) →
anchored walk-forward с прогревом → Deflated Sharpe → сравнение с buyhold и random.
Вердикт survive только если сквозной OOS>0, DSR>0.5 и стратегия бьёт оба бенчмарка.

Read-only, заявок не размещает. Запуск:  python analysis/botdev2_trend_ls_stocks.py
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

DAYS = 1000            # календарных; старт ~2023-10 — февраль-2022 не пересекаем
CASH = 100_000.0
COMMISSION = 0.0005
SLIPPAGE = 0.0005
BORROW = 16.25         # КС 14.25 + 2% — ставка за шорт, % годовых на нотионал

# та же корзина 11 бумаг, что у xsec_momentum (7 из lab/instruments + 4 c uid из репо)
EXTRA = {
    "NVTK": "0da66728-6c30-44c4-9264-df8fac2467ee",
    "MGNT": "ca845f68-6c43-44bc-b584-330d2a1e5eb7",
    "TATN": "88468f6c-c67a-4fb4-a006-53eed803883c",
    "VTBR": "8e2b0325-0292-4654-8a18-4f63ed3b0e09",
}
UIDS = {t: m["uid"] for t, m in INSTRUMENTS.items() if m["kind"] == "share"}
UIDS.update(EXTRA)

# скромная сетка: 4×2×2 = 16 испытаний (фиксируем для DSR); borrow НЕ оптимизируем
GRID = {"n": [20, 40, 55, 70], "exit_n": [10, 20], "stop_mult": [0.0, 3.0]}
N_TRIALS = 16
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


def make(params):
    return strategies.TrendLSStocks(borrow=BORROW, **params)


def wfa_warm(data, grid, n_splits, **kw):
    """Anchored WFA с ПРОГРЕВОМ (паттерн из botdev_xsec_momentum): штатный
    walk_forward режет OOS-окно без истории, и при n~70 стратегия молчит четверть
    окна. Здесь best-параметры с IS гонятся на [oos_lo − warm, oos_hi), а метрики
    считаются только по OOS-участку equity-кривой; позиция, открытая в прогреве,
    честно доживает до OOS (anchored-континуация)."""
    times = sorted({b.t for bars in data.values() for b in bars})
    n = len(times)
    seg = n // (n_splits + 1)
    out = []
    for k in range(1, n_splits + 1):
        is_hi = times[k * seg]
        oos_hi = times[(k + 1) * seg] if (k + 1) * seg < n else times[-1] + 1
        is_data = {t: [b for b in bars if b.t < is_hi] for t, bars in data.items()}
        pts = grid_search(strategies.TrendLSStocks, is_data,
                          dict(grid, borrow=[BORROW]), metric="sharpe", **kw)
        best = pts[0]
        warm = best.params["n"] + 14 + 5          # канал + ATR-окно + запас
        lo_idx = max(0, k * seg - warm)
        run_data = {t: [b for b in bars if times[lo_idx] <= b.t < oos_hi]
                    for t, bars in data.items()}
        res = run(make({k2: v for k2, v in best.params.items() if k2 != "borrow"}),
                  run_data, **kw)
        i0 = next(i for i, tt in enumerate(res.times) if tt >= is_hi)
        eq = res.equity[i0:]
        oos_ret = eq[-1] / eq[0] - 1.0
        ntr = sum(1 for tr in res.trades
                  if tr.exit_i < len(res.times) and res.times[tr.exit_i] >= is_hi)
        rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1]]
        mu = sum(rets) / len(rets)
        sd = (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5
        sh = (mu / sd * 252 ** 0.5) if sd else 0.0
        out.append({"params": best.params, "is_sharpe": best.metrics.sharpe,
                    "ret": oos_ret, "sharpe": sh, "trades": ntr,
                    "oos_lo": is_hi, "oos_hi": oos_hi})
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
    ts0 = data[next(iter(data))]
    t0 = datetime.datetime.utcfromtimestamp(min(b.t for b in ts0))
    t1 = datetime.datetime.utcfromtimestamp(max(b.t for b in ts0))
    print(f"корзина: {len(data)} бумаг × {n} общих баров ({t0:%Y-%m-%d} … {t1:%Y-%m-%d})\n")

    kw = dict(cash=CASH, commission=COMMISSION, slippage=SLIPPAGE)

    # 1. grid in-sample (полная лента — для DSR и ориентира, вердикт по WFA)
    print(f"=== grid {GRID} → {N_TRIALS} испытаний (borrow={BORROW} фикс.) ===")
    pts = grid_search(strategies.TrendLSStocks, data, dict(GRID, borrow=[BORROW]),
                      metric="sharpe", **kw)
    for p in pts[:5]:
        show(str({k: v for k, v in p.params.items() if k != "borrow"}), p.metrics)
    best = pts[0]

    # 2. anchored walk-forward с прогревом
    print(f"\n=== anchored walk-forward (warm-up), {N_SPLITS} окон ===")
    windows, oos_total = wfa_warm(data, GRID, N_SPLITS, **kw)
    for i, w in enumerate(windows):
        pp = {k: v for k, v in w["params"].items() if k != "borrow"}
        print(f"  окно {i+1}: OOS ret {w['ret']*100:+7.2f}%  Sharpe {w['sharpe']:+6.2f}  "
              f"сделок {w['trades']:3}  IS Sharpe {w['is_sharpe']:+.2f}  params {pp}")
    print(f"  сквозной OOS: {oos_total*100:+.2f}%")

    # 3. Deflated Sharpe (испытания = 16 точек сетки)
    rob = assess(best.result, pts, metric="sharpe")
    print(f"\n=== робастность ===\n{rob.summary()}")

    # 4. бенчмарки: полная лента + отдельно OOS-отрезок (от границы первого окна)
    print("\n=== бенчмарки (полная лента) ===")
    bh = metrics(run(strategies.build("buyhold"), data, **kw))
    rnd = metrics(run(strategies.build("random", seed=1), data, **kw))
    show("buyhold", bh)
    show("random(seed=1)", rnd)
    show("best IS " + str({k: v for k, v in best.params.items() if k != "borrow"}),
         best.metrics)

    oos_lo = windows[0]["oos_lo"]
    oos_data = {t: [b for b in bars if b.t >= oos_lo] for t, bars in data.items()}
    print("\n=== бенчмарки (OOS-отрезок, от границы окна 1) ===")
    bh_o = metrics(run(strategies.build("buyhold"), oos_data, **kw))
    rnd_o = metrics(run(strategies.build("random", seed=1), oos_data, **kw))
    show("buyhold OOS", bh_o)
    show("random(seed=1) OOS", rnd_o)
    print(f"{'стратегия, сшитый OOS (WFA)':44} ret {oos_total*100:+8.2f}%")

    # 5. абляции на best-IS параметрах (диагностика, не вердикт)
    print("\n=== абляции (best-IS параметры, полная лента) ===")
    bp = {k: v for k, v in best.params.items() if k != "borrow"}
    ab_free = metrics(run(strategies.TrendLSStocks(borrow=0.0, **bp), data, **kw))
    ab_lo = metrics(run(strategies.TrendLSStocks(borrow=BORROW,
                                                 **dict(bp, short=False)), data, **kw))
    show("borrow=0 (бесплатный шорт)", ab_free)
    show("short=False (лонг-онли)", ab_lo)

    # 6. вердикт
    oos_pos = oos_total > 0
    dsr_ok = rob.deflated_sharpe > 0.5
    beats_is = (best.metrics.total_return > bh.total_return
                and best.metrics.total_return > rnd.total_return)
    beats_oos = (oos_total > bh_o.total_return and oos_total > rnd_o.total_return)
    print(f"\nOOS>0: {oos_pos} ({oos_total*100:+.2f}%)   "
          f"DSR>0.5: {dsr_ok} ({rob.deflated_sharpe:.3f})   "
          f"бьёт bh и random IS: {beats_is}   OOS: {beats_oos}")
    verdict = oos_pos and dsr_ok and beats_is and beats_oos
    print("ВЕРДИКТ:", "SURVIVE — кандидат в ферму" if verdict
          else "KILL — эдж не доказан")


if __name__ == "__main__":
    main()

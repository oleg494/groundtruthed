# -*- coding: utf-8 -*-
"""PAIRS_ZSCORE: anchored walk-forward с пере-отбором пар на каждом шаге.

Отбор пар (только на IS-данных, OOS не видит):
  1) corr лог-цен > CORR_MIN;
  2) стационарность спреда log(A/B) — ADF-прокси на stdlib:
     AR(1)-коэффициент rho спреда < RHO_MAX (half-life = -ln2/ln(rho) конечен и мал)
     И доля пересечений IS-среднего > CROSS_MIN (стационарный ряд часто пересекает
     среднее; random walk — редко).
  3) ранжирование по скорости возврата (меньше rho → быстрее), топ-K пар.

Далее grid_search параметров pairs_z на IS → лучший набор гоняем на OOS.
OOS-кривые компаундятся. DSR — по сшитой OOS-кривой, n_trials = размер сетки.

Запуск:  python analysis/botdev_pairs_zscore_wfa.py [--is-bars 504] [--oos-bars 126]
"""
from __future__ import annotations

import argparse
import itertools
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import candles, metrics, run
from backtest.core import Bar
from backtest.optimize import grid_search
from backtest.robust import _bar_sharpe, deflated_sharpe, probabilistic_sharpe
from backtest.strategies import BuyHold, PairsZScoreBasket, RandomTrader

UIDS = {
    "SBER": "e6123145-9665-43e0-8413-cd61b8aa9b13",
    "GAZP": "962e2a95-02a9-4171-abd7-aa198dbe643a",
    "LKOH": "02cfdf61-6298-4c0f-a9ca-9cabc82afaf3",
    "ROSN": "fd417230-19cf-4e7b-9623-f7c9ca18ec6b",
    "GMKN": "509edd0c-129c-4ee2-934d-7f6246126da1",
    "PLZL": "10620843-28ce-44e8-80c2-f26ceb1bd3e1",
    "CHMF": "fa6aae10-b8d5-48c8-bbfd-d320d925d096",
}

# пороги отбора пар (фиксированы ДО прогонов, не оптимизируются — не входят в n_trials)
CORR_MIN = 0.80      # корреляция лог-цен на IS
RHO_MAX = 0.985      # AR(1) спреда: half-life < ~46 дней
CROSS_MIN = 0.04     # пересечений IS-среднего на бар (стационарность)
TOP_K = 4            # пар в портфеле

GRID = {                      # 3*3*3 = 27 комбинаций → n_trials для DSR
    "lookback": [20, 40, 60],
    "entry_z": [1.5, 2.0, 2.5],
    "exit_z": [0.25, 0.5, 0.75],
}

CASH = 1_000_000
COMM = 0.0005
SLIP = 0.0005


def _corr(x: list[float], y: list[float]) -> float:
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    return sxy / (sx * sy) if sx and sy else 0.0


def _ar1(s: list[float]) -> float:
    """AR(1)-коэффициент демeaned-ряда (OLS без свободного члена)."""
    m = sum(s) / len(s)
    d = [v - m for v in s]
    num = sum(d[i] * d[i - 1] for i in range(1, len(d)))
    den = sum(v * v for v in d[:-1])
    return num / den if den else 1.0


def _crossings(s: list[float]) -> float:
    """Доля пересечений среднего на бар."""
    m = sum(s) / len(s)
    d = [v - m for v in s]
    k = sum(1 for i in range(1, len(d)) if d[i] * d[i - 1] < 0)
    return k / (len(d) - 1)


def select_pairs(data: dict[str, list[Bar]], t_hi: int, verbose: bool = False) -> list[str]:
    """Отбор пар СТРОГО на барах с t < t_hi. Возвращает топ-K строк 'A-B'."""
    closes = {}
    for tk, bars in data.items():
        cs = [b.c for b in bars if b.t < t_hi]
        if len(cs) >= 120:
            closes[tk] = cs
    cands = []
    for a, b in itertools.combinations(sorted(closes), 2):
        n = min(len(closes[a]), len(closes[b]))
        la = [math.log(v) for v in closes[a][-n:]]
        lb = [math.log(v) for v in closes[b][-n:]]
        c = _corr(la, lb)
        if c < CORR_MIN:
            continue
        spread = [x - y for x, y in zip(la, lb)]
        rho, cross = _ar1(spread), _crossings(spread)
        if rho >= RHO_MAX or cross < CROSS_MIN:
            continue
        hl = -math.log(2) / math.log(rho) if 0 < rho < 1 else float("inf")
        cands.append((rho, hl, c, cross, f"{a}-{b}"))
    cands.sort()                                # меньший rho = быстрее возврат
    if verbose:
        for rho, hl, c, cross, p in cands:
            print(f"    {p}: corr={c:.3f} rho={rho:.4f} HL={hl:.1f}д cross={cross:.3f}")
    return [p for *_, p in cands[:TOP_K]]


def _slice(data, t0, t1):
    return {tk: [b for b in bars if t0 <= b.t < t1] for tk, bars in data.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--is-bars", type=int, default=504)   # ~2 года начальный IS
    ap.add_argument("--oos-bars", type=int, default=126)  # ~6 мес OOS-шаг
    ap.add_argument("--days", type=int, default=1040)
    args = ap.parse_args()

    data = {}
    for tk, uid in UIDS.items():
        data.update(candles.from_tinvest(uid, tk, days=args.days))
    times = sorted({b.t for bars in data.values() for b in bars})
    n = len(times)
    d0 = datetime.fromtimestamp(times[0], timezone.utc).date()
    d1 = datetime.fromtimestamp(times[-1], timezone.utc).date()
    print(f"данные: {len(data)} тикеров, {n} общих баров, {d0} .. {d1}")

    n_trials = len(GRID["lookback"]) * len(GRID["entry_z"]) * len(GRID["exit_z"])
    print(f"сетка: {n_trials} комбинаций (n_trials для DSR); отбор пар фикс.: "
          f"corr>{CORR_MIN}, rho<{RHO_MAX}, cross>{CROSS_MIN}, top{TOP_K}")

    # anchored WFA
    steps = []
    eq, capital = [CASH], CASH
    grid_sr_bars = []                     # побаровые SR grid-точек (для sr_std DSR)
    k = args.is_bars
    step_no = 0
    while k + 10 < n:
        oos_hi_i = min(k + args.oos_bars, n)
        is_t_hi = times[k]
        oos_t_hi = times[oos_hi_i - 1] + 1
        step_no += 1
        is_d0 = datetime.fromtimestamp(times[0], timezone.utc).date()
        is_d1 = datetime.fromtimestamp(times[k - 1], timezone.utc).date()
        oos_d1 = datetime.fromtimestamp(times[oos_hi_i - 1], timezone.utc).date()
        print(f"\nШАГ {step_no}: IS {is_d0}..{is_d1} ({k} баров) → OOS ..{oos_d1} "
              f"({oos_hi_i - k} баров)")

        pairs = select_pairs(data, is_t_hi, verbose=True)
        if not pairs:
            print("  нет пар, прошедших фильтры — шаг пропущен (флэт)")
            eq.append(capital)
            k = oos_hi_i
            continue
        print(f"  отобрано: {pairs}")

        is_data = _slice(data, times[0], is_t_hi)
        grid = dict(GRID, pairs=[",".join(pairs)])
        pts = grid_search(PairsZScoreBasket, is_data, grid, metric="sharpe",
                          cash=CASH, commission=COMM, slippage=SLIP)
        grid_sr_bars.extend(_bar_sharpe(p.result)[0] for p in pts)
        best = pts[0]
        bp = {kk: v for kk, v in best.params.items() if kk != "pairs"}
        print(f"  IS best: {bp} sharpe={best.metrics.sharpe:.2f} "
              f"ret={best.metrics.total_return*100:+.1f}% trades={best.metrics.num_trades}")

        # OOS-прогон с прогревом: хвост IS длиной lookback даёт стратегии историю
        # с первого OOS-бара; warmup запрещает сделки на IS-части (не lookahead —
        # прогрев целиком из прошлого). Метрики считаем только по OOS-части кривой.
        lb = best.params["lookback"]
        warm_lo = times[max(0, k - lb)]
        oos_data = _slice(data, warm_lo, oos_t_hi)
        res = run(PairsZScoreBasket(**dict(best.params, warmup=lb)), oos_data,
                  cash=capital, commission=COMM, slippage=SLIP)
        cut = sum(1 for t in res.times if t < is_t_hi)      # число warmup-баров в кривой
        res.times, res.equity = res.times[cut:], res.equity[cut:]
        res.exposure = res.exposure[cut:]
        m = metrics(res)
        print(f"  OOS: ret={m.total_return*100:+.2f}% sharpe={m.sharpe:.2f} "
              f"maxDD={m.max_drawdown*100:.2f}% trades={m.num_trades} PF={m.profit_factor:.2f}")
        steps.append((step_no, pairs, bp, m))
        eq.extend(res.equity)
        capital = res.final_equity
        k = oos_hi_i

    # ── сводка OOS ──
    oos_ret = eq[-1] / eq[0] - 1
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1]]
    mu = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets))
    sr_bar = mu / sd if sd else 0.0
    sr_ann = sr_bar * math.sqrt(252)
    peak, mdd = eq[0], 0.0
    for e in eq:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    total_trades = sum(m.num_trades for *_, m in steps)
    print(f"\n══ СШИТЫЙ OOS: ret={oos_ret*100:+.2f}% sharpe_ann={sr_ann:.2f} "
          f"maxDD={mdd*100:.2f}% trades={total_trades} шагов={len(steps)}")

    # ── DSR по сшитой OOS-кривой ──
    skew = sum((r - mu) ** 3 for r in rets) / len(rets) / sd ** 3 if sd else 0.0
    kurt = sum((r - mu) ** 4 for r in rets) / len(rets) / sd ** 4 if sd else 3.0
    if len(grid_sr_bars) > 1:
        mu_g = sum(grid_sr_bars) / len(grid_sr_bars)
        sr_std = math.sqrt(sum((x - mu_g) ** 2 for x in grid_sr_bars) / len(grid_sr_bars))
    else:
        sr_std = 1.0
    psr = probabilistic_sharpe(sr_bar, len(rets), 0.0, skew, kurt)
    dsr = deflated_sharpe(sr_bar, len(rets), n_trials, sr_std=sr_std, skew=skew, kurt=kurt)
    print(f"PSR={psr*100:.1f}%  DSR(×{n_trials} исп., sr_std={sr_std:.4f})={dsr*100:.1f}%  "
          f"skew={skew:.2f} kurt={kurt:.1f}")

    # ── бенчмарки на том же суммарном OOS-периоде ──
    bench_lo, bench_hi = times[args.is_bars], times[-1] + 1
    bdata = _slice(data, bench_lo, bench_hi)
    bh = run(BuyHold(), bdata, cash=CASH, commission=COMM, slippage=SLIP)
    mbh = metrics(bh)
    rnd_rets = []
    for seed in range(20):
        rr = run(RandomTrader(seed=seed), bdata, cash=CASH, commission=COMM, slippage=SLIP)
        rnd_rets.append(metrics(rr).total_return)
    rnd_avg = sum(rnd_rets) / len(rnd_rets)
    print(f"\nбенчмарки на OOS-периоде ({datetime.fromtimestamp(bench_lo, timezone.utc).date()}..):")
    print(f"  buyhold(7 равновзв.): ret={mbh.total_return*100:+.2f}% sharpe={mbh.sharpe:.2f} "
          f"maxDD={mbh.max_drawdown*100:.2f}%")
    print(f"  random(20 сидов):     ret_avg={rnd_avg*100:+.2f}% "
          f"min={min(rnd_rets)*100:+.1f}% max={max(rnd_rets)*100:+.1f}%")

    ok = oos_ret > 0 and dsr > 0.5 and oos_ret > mbh.total_return and oos_ret > rnd_avg
    print(f"\nВЕРДИКТ-условия: OOS>0: {oos_ret > 0}; DSR>0.5: {dsr > 0.5}; "
          f"> buyhold: {oos_ret > mbh.total_return}; > random: {oos_ret > rnd_avg}"
          f"\n→ {'SURVIVE' if ok else 'KILL'}")


if __name__ == "__main__":
    main()

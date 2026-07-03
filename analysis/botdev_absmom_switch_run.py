"""ABSMOM_SWITCH: полный конвейер испытания кандидата (grid → anchored WFA → DSR → бенчмарки).

Гипотеза: абсолютный (time-series) моментум — доходность актива за N мес > порога →
держим актив, иначе кэш-прокси под KEYRATE (LQDT). Активы: SBER, LKOH, GLDRUBF +
портфель равновзвешенно. Данные строго ПОСТ-2022-03-24 (февраль-2022 не пересекается
ни одним IS/OOS шагом anchored WFA — см. deep/walk_forward_moex.md, Approach 1).

Запуск:  python analysis/botdev_absmom_switch_run.py [--asset SBER|LKOH|GLDRUBF|PORTF|all]
Свечи: кэш backtest/.cache (SBER 2000д уже есть), LKOH/GLDRUBF дофетч через candles.from_tinvest.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backtest import candles, metrics, run, strategies  # noqa: E402
from backtest.core import Bar, Instrument  # noqa: E402
from backtest.optimize import grid_search, walk_forward  # noqa: E402
from backtest.robust import deflated_sharpe, probabilistic_sharpe  # noqa: E402

CUTOFF = int(datetime(2022, 3, 24, tzinfo=timezone.utc).timestamp())  # пост-приостановка MOEX
CASH = 1_000_000.0
COMM, SLIP = 0.0005, 0.0005
RATE = 14.25                                    # KEYRATE (scripts/market_context.py)
GRID = {"lb_m": list(range(1, 13)),            # lookback 1..12 месяцев
        "hurdle": [0.0, 0.05, 0.10]}           # годовой порог 0/5/10%
N_TRIALS = len(GRID["lb_m"]) * len(GRID["hurdle"])   # 36 испытаний сетки (для DSR)


def excess_sharpe(m) -> float:
    """Цель отбора: Sharpe ИЗБЫТОЧНОЙ доходности над кэш-ставкой. Сидение в кэше
    (0 сделок, детерминированный рост под ставку) даёт vol=0 и «бесконечный» сырой
    Sharpe — исключаем такие вырожденные конфигурации штрафом."""
    if m.ann_vol <= 1e-9 or m.num_trades == 0:
        return -1e9
    return (m.cagr - RATE / 100.0) / m.ann_vol


def _bar_stats_excess(res) -> tuple[float, int, float, float]:
    """Побаровый Sharpe избыточной (над ставкой) доходности + skew/kurt."""
    eq = res.equity
    rf = RATE / 100.0 / 252.0
    rets = [eq[i] / eq[i - 1] - 1.0 - rf for i in range(1, len(eq)) if eq[i - 1]]
    n = len(rets)
    if n < 2:
        return 0.0, n, 0.0, 3.0
    mu = sum(rets) / n
    sd = (sum((r - mu) ** 2 for r in rets) / n) ** 0.5
    if sd < 1e-9:      # чисто-кэшевое окно: excess≡0, sd — машинный шум → SR=0, не мусор
        return 0.0, n, 0.0, 3.0
    skew = sum((r - mu) ** 3 for r in rets) / n / sd ** 3
    kurt = sum((r - mu) ** 4 for r in rets) / n / sd ** 4
    return mu / sd, n, skew, kurt

UIDS = {  # из lab/instruments.py
    "SBER":    "e6123145-9665-43e0-8413-cd61b8aa9b13",
    "LKOH":    "02cfdf61-6298-4c0f-a9ca-9cabc82afaf3",
    "GLDRUBF": "b347fe28-0d2a-45bf-b3bd-cda8a6ac64e6",
}
FUT = {"GLDRUBF"}   # kind=futures, point_rub=1.0 → multiplier=1.0


def _load_cache_best(ticker: str) -> list[Bar] | None:
    """Самый глубокий дневной кэш-файл тикера (любой даты) — сеть не трогаем."""
    best = None
    for f in glob.glob(os.path.join(ROOT, "backtest", ".cache",
                                    f"{ticker}_CANDLE_INTERVAL_DAY_*.json")):
        raw = json.loads(open(f, encoding="utf-8").read())
        if best is None or len(raw) > len(best):
            best = raw
    return [Bar(**b) for b in best] if best else None


def load(ticker: str, min_days_back: int) -> list[Bar]:
    """Кэш, если он покрывает нужную глубину; иначе один фетч через sandbox-домен."""
    bars = _load_cache_best(ticker)
    now = datetime.now(timezone.utc).timestamp()
    if bars and bars[0].t <= now - min_days_back * 86400 * 0.98:
        return bars
    data = candles.from_tinvest(UIDS[ticker], ticker, days=int(min_days_back / 1.5))
    fresh = data[ticker]
    return fresh if not bars or len(fresh) > len(bars) else bars


def slice_post2022(bars: list[Bar]) -> list[Bar]:
    return [b for b in bars if b.t >= CUTOFF]


def instr(tickers: list[str]) -> dict[str, Instrument]:
    return {t: Instrument(t, kind="futures" if t in FUT else "cash", multiplier=1.0)
            for t in tickers}


def bench(data, insts):
    """buyhold и random (медиана по 21 сиду) на тех же данных/издержках."""
    bh = metrics(run(strategies.BuyHold(), data, cash=CASH, commission=COMM,
                     slippage=SLIP, instruments=insts))
    rnd_ret, rnd_sh = [], []
    for seed in range(21):
        m = metrics(run(strategies.RandomTrader(seed=seed), data, cash=CASH,
                        commission=COMM, slippage=SLIP, instruments=insts))
        rnd_ret.append(m.total_return); rnd_sh.append(m.sharpe)
    return bh, statistics.median(rnd_ret), statistics.median(rnd_sh)


def study(name: str, data: dict[str, list[Bar]], n_splits: int) -> None:
    insts = instr(list(data))
    nbars = len(next(iter(data.values())))
    t0 = datetime.utcfromtimestamp(min(b.t for bars in data.values() for b in bars))
    t1 = datetime.utcfromtimestamp(max(b.t for bars in data.values() for b in bars))
    print(f"\n{'='*74}\n{name}: {nbars} баров  {t0:%Y-%m-%d} → {t1:%Y-%m-%d}  "
          f"(сетка {N_TRIALS} комбинаций, WFA splits={n_splits})\n{'='*74}")

    # 1) grid IS (полная лента), цель — excess-Sharpe над ставкой (анти-вырождение)
    pts = grid_search(strategies.AbsMomentumSwitch, data, GRID, objective=excess_sharpe,
                      cash=CASH, commission=COMM, slippage=SLIP, instruments=insts)
    best = pts[0]
    bm = best.metrics
    print(f"[IS grid] best {best.params}: ret={bm.total_return*100:+.1f}% CAGR={bm.cagr*100:+.1f}% "
          f"sharpe={bm.sharpe:.2f} (excess={excess_sharpe(bm):.2f}) maxDD={bm.max_drawdown*100:.1f}% "
          f"trades={bm.num_trades} PF={bm.profit_factor:.2f} expo={bm.avg_exposure*100:.0f}%")
    top5 = ", ".join(f"{p.params['lb_m']}м/{p.params['hurdle']:.2f}→{excess_sharpe(p.metrics):.2f}"
                     for p in pts[:5])
    print(f"[IS grid] топ-5 по excess-Sharpe: {top5}")

    # 2) anchored walk-forward (та же цель отбора внутри IS)
    wf = walk_forward(strategies.AbsMomentumSwitch, data, GRID, n_splits=n_splits,
                      objective=excess_sharpe, cash=CASH, commission=COMM, slippage=SLIP,
                      instruments=insts)
    import math
    is_ex, oos_ex = [], []
    for i, w in enumerate(wf.windows, 1):
        o = w.oos
        d0 = datetime.utcfromtimestamp(w.oos_range[0]).strftime("%Y-%m-%d")
        d1 = datetime.utcfromtimestamp(w.oos_range[1]).strftime("%Y-%m-%d")
        # excess-SR окна по барам OOS-кривой (устойчиво к «держим до конца окна», 0 round-trip)
        oex = _bar_stats_excess(w.oos_result)[0] * math.sqrt(252)
        is_ex.append(w.is_metric); oos_ex.append(oex)
        # чисто-кэшевое окно: vol≈0 → сырой Sharpe вырожден (~1e14), печатаем n/a
        sh = f"{o.sharpe:.2f}" if abs(o.sharpe) < 100 else "n/a(кэш)"
        print(f"[WFA {i}] IS_exSR={w.is_metric:.2f} params={w.best_params} | "
              f"OOS {d0}→{d1}: ret={o.total_return*100:+.2f}% sharpe={sh} "
              f"exSR={oex:.2f} trades={o.num_trades}")
    oos_tot = wf.oos_return()
    oos_m, oos_ex_sr = metrics_from_curve(wf)
    ratio = (sum(oos_ex)/len(oos_ex)) / (sum(is_ex)/len(is_ex)) if is_ex and sum(is_ex) else 0.0
    print(f"[WFA] сквозной OOS: ret={oos_tot*100:+.2f}%  sharpe(сырой)={oos_m:.2f}  "
          f"exSR(над кэш-ставкой)={oos_ex_sr:.2f}  IS→OOS excess-degradation "
          f"ratio={ratio:.2f} (окон {len(wf.windows)})")

    # 3) DSR на ИЗБЫТОЧНЫХ доходностях (иначе кэш-ставка даёт SR>0 без скилла);
    #    испытания = 36 точек сетки, sr_std — разброс побарового excess-SR по сетке
    sr_bar, n_obs, skew, kurt = _bar_stats_excess(best.result)
    sr_bars = [_bar_stats_excess(p.result)[0] for p in pts]
    mu_sr = sum(sr_bars) / len(sr_bars)
    sr_std = (sum((x - mu_sr) ** 2 for x in sr_bars) / len(sr_bars)) ** 0.5 or 1.0
    psr = probabilistic_sharpe(sr_bar, n_obs, 0.0, skew, kurt)
    dsr = deflated_sharpe(sr_bar, n_obs, N_TRIALS, sr_std=sr_std, skew=skew, kurt=kurt)
    ex_vals = [excess_sharpe(p.metrics) for p in pts if excess_sharpe(p.metrics) > -1e8]
    frac_pos = sum(1 for v in ex_vals if v > 0) / len(ex_vals) if ex_vals else 0.0
    print(f"[DSR] PSR(excess)={psr*100:.1f}%  DeflatedSR(excess)={dsr*100:.1f}% "
          f"(n_trials={N_TRIALS}, sr_std={sr_std:.4f})  excess-Sharpe>0 у {frac_pos*100:.0f}% сетки")

    # 4) бенчмарки: на полной ленте (для IS-строки) и на СШИТОМ OOS-периоде — главное
    bh, rnd_ret, rnd_sh = bench(data, insts)
    print(f"[BENCH/full] buyhold: ret={bh.total_return*100:+.1f}% sharpe={bh.sharpe:.2f} "
          f"maxDD={bh.max_drawdown*100:.1f}% | random(медиана 21 сид): "
          f"ret={rnd_ret*100:+.1f}% sharpe={rnd_sh:.2f}")
    if wf.windows:
        oos_lo = wf.windows[0].oos_range[0]
        oos_data = {t: [b for b in bars if b.t >= oos_lo] for t, bars in data.items()}
        bho, rndo_ret, rndo_sh = bench(oos_data, insts)
        d0 = datetime.utcfromtimestamp(oos_lo).strftime("%Y-%m-%d")
        v_bh = "БЬЁТ" if oos_tot > bho.total_return else "ПРОИГРЫВАЕТ"
        v_rnd = "БЬЁТ" if oos_tot > rndo_ret else "ПРОИГРЫВАЕТ"
        print(f"[BENCH/OOS с {d0}] WFA-OOS={oos_tot*100:+.2f}% (sharpe {oos_m:.2f}) vs "
              f"buyhold={bho.total_return*100:+.1f}% (sharpe {bho.sharpe:.2f}, "
              f"maxDD {bho.max_drawdown*100:.1f}%) → {v_bh}; vs random={rndo_ret*100:+.1f}% → {v_rnd}")

    # 5) абляция: тот же best без кэш-прокси (rate=0) — сколько даёт сам сигнал
    r0 = metrics(run(strategies.AbsMomentumSwitch(rate=0.0, **best.params), data,
                     cash=CASH, commission=COMM, slippage=SLIP, instruments=insts))
    print(f"[ABL] best params при rate=0: ret={r0.total_return*100:+.1f}% sharpe={r0.sharpe:.2f} "
          f"(вклад кэш-прокси = {bm.total_return*100 - r0.total_return*100:+.1f} п.п.)")


def metrics_from_curve(wf) -> tuple[float, float]:
    """(сырой Sharpe, excess-Sharpe над ставкой) сшитой OOS-кривой, годовые (252)."""
    import math
    eq = wf.equity
    rf = RATE / 100.0 / 252.0
    rets = [eq[i] / eq[i-1] - 1 for i in range(1, len(eq)) if eq[i-1]]
    if len(rets) < 2:
        return 0.0, 0.0
    m = sum(rets) / len(rets)
    sd = (sum((r - m) ** 2 for r in rets) / len(rets)) ** 0.5
    if not sd:
        return 0.0, 0.0
    return m / sd * math.sqrt(252), (m - rf) / sd * math.sqrt(252)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", default="all")
    a = ap.parse_args()

    sber = slice_post2022(load("SBER", 2900))          # кэш 2000д (с 2018) → срез
    lkoh = slice_post2022(load("LKOH", 1650))          # нужно с ~2021-12 → фетч 1100д
    gld = slice_post2022(load("GLDRUBF", 1200))        # с запуска контракта (2023-07)

    jobs = {
        "SBER":    ({"SBER": sber}, 4),
        "LKOH":    ({"LKOH": lkoh}, 4),
        "GLDRUBF": ({"GLDRUBF": gld}, 3),
    }
    # портфель: общее окно (по самому молодому — GLDRUBF)
    start = max(bars[0].t for bars in (sber, lkoh, gld))
    jobs["PORTF"] = ({"SBER": [b for b in sber if b.t >= start],
                      "LKOH": [b for b in lkoh if b.t >= start],
                      "GLDRUBF": [b for b in gld if b.t >= start]}, 3)

    for name, (data, splits) in jobs.items():
        if a.asset != "all" and a.asset != name:
            continue
        study(name, data, splits)


if __name__ == "__main__":
    main()

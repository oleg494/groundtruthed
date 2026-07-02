"""Кандидат CALENDAR_CB: полный конвейер (grid → событийный anchored WFA → DSR → бенчмарки).

Гипотеза — календарный эффект заседаний ЦБ РФ (измерение:
analysis/botdev2_calendar_cb_measure.py): направленный дрейф в событийных окнах
вокруг плановых решений по ключевой ставке. Стратегия backtest.strategies.CalendarCB
торгует только окна [-5..+5] торговых дней от заседания: вход по сигналу на закрытии
дня entry_k, исполнение next open, выход через hold баров.

Walk-forward — событийный anchored, шаг = ОДНО заседание: IS = вся история до
границы окна заседания j (граница = дата заседания − 9 календарных дней), сетка
перебирается на IS, лучший набор гоняется на OOS-ломте [граница_j, граница_{j+1}).
Сшивка компаундингом. Сетка 3×4×2 = 24 испытания на инструмент (фиксировано для DSR).

Read-only: только кэш backtest/.cache (sandbox-домен). Заявок нет.
Запуск:  python analysis/botdev2_calendar_cb.py
"""
from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest import candles, strategies                     # noqa: E402
from backtest.core import Instrument                          # noqa: E402
from backtest.engine import run                               # noqa: E402
from backtest.metrics import metrics                          # noqa: E402
from backtest.optimize import grid_search                     # noqa: E402
from backtest.robust import assess                            # noqa: E402

# календарь плановых заседаний ЦБ (проверен по cbr.ru/dkp/cal_mp 2026-07-02;
# внеочередные 28.02/08.04/26.05.2022 и 15.08.2023 исключены — не антиципируемы)
CB_MEETINGS = [
    "2022-04-29", "2022-06-10", "2022-07-22", "2022-09-16", "2022-10-28", "2022-12-16",
    "2023-02-10", "2023-03-17", "2023-04-28", "2023-06-09", "2023-07-21",
    "2023-09-15", "2023-10-27", "2023-12-15",
    "2024-02-16", "2024-03-22", "2024-04-26", "2024-06-07",
    "2024-07-26", "2024-09-13", "2024-10-25", "2024-12-20",
    "2025-02-14", "2025-03-21", "2025-04-25", "2025-06-06",
    "2025-07-25", "2025-09-12", "2025-10-24", "2025-12-19",
    "2026-02-13", "2026-03-20", "2026-04-24", "2026-06-19",
]
DATES_STR = ",".join(CB_MEETINGS)

# тикер -> (uid, days под тёплый кэш 2026-07-02, kind, multiplier=point_rub)
INSTR = {
    "SBER":    ("e6123145-9665-43e0-8413-cd61b8aa9b13", 1040, "cash",    1.0),
    "IMOEXF":  ("5bcff194-f10d-4314-b9ee-56b7fdb344fd", 1300, "futures", 10.0),
    "GLDRUBF": ("b347fe28-0d2a-45bf-b3bd-cda8a6ac64e6", 1300, "futures", 1.0),
}

CASH = 100_000.0
COMMISSION = 0.0005
SLIPPAGE = 0.0005
GRID = {"dates": [DATES_STR],
        "entry_k": [-5, -1, 0], "hold": [1, 2, 3, 4], "direction": [1, -1]}
N_TRIALS = 3 * 4 * 2                     # 24 испытания на инструмент
PRE_DAYS = 9                             # граница окна = заседание − 9 кал. дней
MSK = 3 * 3600


def bdate(t: int) -> dt.date:
    return dt.datetime.utcfromtimestamp(t + MSK).date()


def show(tag, m):
    print(f"  {tag:42} ret {m.total_return*100:+8.2f}%  Sharpe {m.sharpe:+6.2f}  "
          f"dd {m.max_drawdown*100:6.2f}%  сделок {m.num_trades}")


def seg_stats(times, equity, t_lo, t_hi=None):
    """Доходность/Sharpe участка equity-кривой [t_lo, t_hi)."""
    pts = [(tt, e) for tt, e in zip(times, equity)
           if tt >= t_lo and (t_hi is None or tt < t_hi)]
    if len(pts) < 2:
        return 0.0, 0.0
    eq = [e for _, e in pts]
    ret = eq[-1] / eq[0] - 1.0
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1]]
    mu = sum(rets) / len(rets)
    sd = (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5
    return ret, (mu / sd * 252 ** 0.5) if sd else 0.0


def pipeline(ticker: str):
    uid, days, kind, mult = INSTR[ticker]
    data = candles.from_tinvest(uid, ticker, days=days)
    bars = data[ticker]
    inst = {ticker: Instrument(ticker, multiplier=mult, kind=kind)}
    kw = dict(cash=CASH, commission=COMMISSION, slippage=SLIPPAGE, instruments=inst)
    d0, d1 = bdate(bars[0].t), bdate(bars[-1].t)
    meets = [dt.date.fromisoformat(s) for s in CB_MEETINGS if d0 <= dt.date.fromisoformat(s) <= d1]
    print(f"\n{'='*72}\n{ticker} ({kind}, mult={mult}): {len(bars)} баров "
          f"({d0} … {d1}), заседаний в диапазоне: {len(meets)}")

    # ── 1. grid на полной ленте (ориентир IS + испытания для DSR) ──
    pts = grid_search(strategies.CalendarCB, data, GRID, metric="sharpe", **kw)
    print(f"\n--- grid {N_TRIALS} комбо (entry_k×hold×direction), полная лента, топ-5 ---")
    for p in pts[:5]:
        pp = {k: v for k, v in p.params.items() if k != "dates"}
        show(str(pp), p.metrics)
    best = pts[0]

    # ── 2. событийный anchored walk-forward: шаг = одно заседание ──
    n_is = max(8, len(meets) // 2)
    bounds = [dt.datetime.combine(m - dt.timedelta(days=PRE_DAYS), dt.time())
              .replace(tzinfo=dt.timezone.utc).timestamp() - MSK for m in meets]
    bounds = [int(b) for b in bounds]
    print(f"\n--- anchored WFA по заседаниям: IS ≥ {n_is} заседаний, "
          f"OOS-шагов {len(meets) - n_is} ---")
    capital = CASH
    st_times, st_eq = [], []               # сшитая OOS-кривая
    steps = []
    for j in range(n_is, len(meets)):
        t_lo = bounds[j]
        t_hi = bounds[j + 1] if j + 1 < len(meets) else bars[-1].t + 1
        is_data = {ticker: [b for b in bars if b.t < t_lo]}
        oos_data = {ticker: [b for b in bars if t_lo <= b.t < t_hi]}
        if not oos_data[ticker]:
            continue
        ipts = grid_search(strategies.CalendarCB, is_data, GRID, metric="sharpe", **kw)
        bp = ipts[0].params
        res = run(strategies.CalendarCB(**bp), oos_data,
                  cash=capital, commission=COMMISSION, slippage=SLIPPAGE,
                  instruments=inst)
        r = res.final_equity / capital - 1.0
        capital = res.final_equity
        st_times.extend(res.times)
        st_eq.extend(res.equity)
        pp = {k: v for k, v in bp.items() if k != "dates"}
        steps.append({"meet": meets[j], "ret": r, "trades": len(res.trades),
                      "params": pp, "is_sharpe": ipts[0].metrics.sharpe})
        print(f"  {meets[j]}  OOS {r*100:+6.2f}%  сделок {len(res.trades)}  "
              f"IS Sharpe {ipts[0].metrics.sharpe:+5.2f}  params {pp}")
    oos_total = capital / CASH - 1.0
    n_tr = sum(s["trades"] for s in steps)
    # Sharpe сшитой OOS-кривой
    rets = [st_eq[i] / st_eq[i - 1] - 1 for i in range(1, len(st_eq)) if st_eq[i - 1]]
    if rets:
        mu = sum(rets) / len(rets)
        sd = (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5
        oos_sharpe = mu / sd * 252 ** 0.5 if sd else 0.0
    else:
        oos_sharpe = 0.0
    print(f"  сквозной OOS: {oos_total*100:+.2f}%  (Sharpe сшитой кривой {oos_sharpe:+.2f}, "
          f"OOS-сделок всего {n_tr}, шагов {len(steps)})")
    print(f"  сделок на шаг ≤ {max((s['trades'] for s in steps), default=0)} — "
          f"правило ≥30/шаг (.agents/AGENTS.md) НЕ выполняется: статистика слабая")

    # ── 3. DSR (испытания = 24 точки сетки полной ленты) ──
    rob = assess(best.result, pts, metric="sharpe")
    print(f"\n--- робастность ---\n{rob.summary()}")

    # ── 4. бенчмарки на OOS-периоде (сплошной buyhold/random от начала OOS) ──
    oos_t0 = bounds[n_is]
    bh_res = run(strategies.build("buyhold"), data, **kw)
    rnd_res = run(strategies.build("random", seed=1), data, **kw)
    bh_ret, bh_sh = seg_stats(bh_res.times, bh_res.equity, oos_t0)
    rnd_ret, rnd_sh = seg_stats(rnd_res.times, rnd_res.equity, oos_t0)
    print(f"\n--- бенчмарки OOS-периода (с {bdate(oos_t0)}) ---")
    print(f"  стратегия (сшитый WFA):  {oos_total*100:+8.2f}%")
    print(f"  buyhold:                 {bh_ret*100:+8.2f}%  (Sharpe {bh_sh:+.2f})")
    print(f"  random(seed=1):          {rnd_ret*100:+8.2f}%  (Sharpe {rnd_sh:+.2f})")

    # ── 5. вердикт ──
    oos_pos = oos_total > 0
    dsr_ok = rob.deflated_sharpe > 0.5
    beats = oos_total > bh_ret and oos_total > rnd_ret
    print(f"\n  OOS>0: {oos_pos}   DSR>0.5: {dsr_ok} ({rob.deflated_sharpe:.3f})   "
          f"бьёт buyhold И random на OOS: {beats}")
    verdict = "SURVIVE" if (oos_pos and dsr_ok and beats) else "KILL"
    print(f"  ВЕРДИКТ {ticker}: {verdict}")
    return {"ticker": ticker, "oos": oos_total, "oos_sharpe": oos_sharpe,
            "dsr": rob.deflated_sharpe, "bh": bh_ret, "rnd": rnd_ret,
            "trades": n_tr, "verdict": verdict,
            "best_is": {k: v for k, v in best.params.items() if k != "dates"},
            "best_is_m": best.metrics}


def main():
    out = [pipeline(t) for t in INSTR]
    print(f"\n{'='*72}\nСВОДКА CALENDAR_CB (grid {N_TRIALS} испытаний/инструмент, "
          f"WFA шаг = заседание)")
    for r in out:
        print(f"  {r['ticker']:8} OOS {r['oos']*100:+7.2f}%  DSR {r['dsr']*100:5.1f}%  "
              f"bh(OOS) {r['bh']*100:+7.2f}%  rnd(OOS) {r['rnd']*100:+7.2f}%  "
              f"сделок {r['trades']:3}  → {r['verdict']}")
    alive = [r for r in out if r["verdict"] == "SURVIVE"]
    print("\nИТОГ:", "SURVIVE: " + ", ".join(r["ticker"] for r in alive) if alive
          else "KILL — ни один инструмент не прошёл тройной критерий")


if __name__ == "__main__":
    main()

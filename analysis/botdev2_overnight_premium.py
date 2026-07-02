"""Кандидат OVERNIGHT_PREMIUM (вторая волна bot-dev): премия за ночное владение.

Гипотеза (мировая литература — Cooper/Cliff/Gulen и др.: значимая часть
equity-премии реализуется overnight, close→open; интрадей-часть слаба или
отрицательна). На MOEX публично никем не проверялась — здесь сначала ИЗМЕРЕНИЕ
(мини-оракул: разложение суммарной доходности на overnight/intraday по часовым
свечам с проверкой телескопического тождества), затем — если есть что ловить —
полный конвейер: grid → anchored WFA → DSR → бенчмарки → издержки.

Фазы:
  0. smoke на синтетике с ИЗВЕСТНОЙ истиной (гэп +0.4%/ночь, день плоский):
     движок против независимой репликации арифметики — бит-в-бит;
  1. фетч часовых свечей SBER/GAZP/LKOH (365 дней, sandbox-домен, кэш);
  2. измерение: overnight (close посл. вечернего бара → open первого утреннего)
     vs intraday, t-статистики по дневным наблюдениям; отдельно «захватываемая»
     версия (open посл. вечернего часа → open второго утреннего — ровно то, что
     физически исполнит движок/живая торговля без trade-at-close);
  3. конвейер стратегии overnight (backtest/strategies.py) с издержками:
     2 сделки/день → 0.1% на круг минимум (комиссия 0.05%/сторона), стандарт
     проекта 0.2% (плюс слиппедж 0.05%/сторона).

Read-only, заявок не размещает. Запуск: python analysis/botdev2_overnight_premium.py
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest import candles, strategies                     # noqa: E402
from backtest.core import Bar, Instrument                    # noqa: E402
from backtest.engine import run                              # noqa: E402
from backtest.metrics import metrics                         # noqa: E402
from backtest.optimize import grid_search, walk_forward      # noqa: E402
from backtest.robust import assess                           # noqa: E402
from lab.instruments import INSTRUMENTS                      # noqa: E402

DAYS = 365
CASH = 100_000.0
COMMISSION = 0.0005          # 0.05%/сторона — тариф Т-Инвестиций «Трейдер»
SLIPPAGE = 0.0005            # 0.05%/сторона — стандарт конвейера
TZ = 3                       # МСК
TICKERS = ["SBER", "GAZP", "LKOH"]
# скромная сетка: 3 часа входа × 2 бара выхода = 6 испытаний на тикер (для DSR)
GRID = {"entry_hour": [18, 21, 22], "exit_bar": [1, 2]}
N_SPLITS = 4


# ───────────────────────── фаза 0: smoke-оракул ─────────────────────────
def synth_hourly(n_days: int = 300, gap: float = 0.004, s0: float = 100.0):
    """Синтетика с известной истиной: весь рост — в ночном гэпе, день плоский."""
    bars, p = [], s0
    day0 = 20_000                                # произвольная эпоха-день
    for d in range(n_days):
        if d:
            p *= 1 + gap
        for h in range(10, 24):                  # часовые бары 10:00–23:00 МСК
            t = (day0 + d) * 86400 + h * 3600 - TZ * 3600
            bars.append(Bar(t=t, o=p, h=p, l=p, c=p, v=1000))
    return {"SYN": bars}


def smoke() -> bool:
    print("=== фаза 0: smoke на синтетике (гэп +0.4%/ночь, день плоский) ===")
    n_days, gap, frac = 300, 0.004, 0.95
    data = synth_hourly(n_days, gap)
    res = run(strategies.build("overnight", mode="overnight", entry_hour=22,
                               exit_bar=1, frac=frac),
              data, cash=CASH, commission=0.0, slippage=0.0)
    # независимая репликация арифметики: 299 ночей, вход int(frac·eq/p) штук
    eq, p = CASH, 100.0
    for _ in range(1, n_days):
        qty = int(frac * eq / p)
        p2 = p * (1 + gap)
        eq += qty * (p2 - p)
        p = p2
    rel = abs(res.final_equity - eq) / eq
    print(f"  overnight: движок {res.final_equity:,.2f} vs репликация {eq:,.2f} "
          f"(отн. расхождение {rel:.2e}), сделок {len(res.trades)}")
    res_id = run(strategies.build("overnight", mode="intraday", entry_hour=22,
                                  exit_bar=1, frac=frac),
                 data, cash=CASH, commission=0.0, slippage=0.0)
    print(f"  intraday (день плоский → ожидаем 0): ret {res_id.total_return*100:+.4f}%")
    ok = rel < 1e-9 and abs(res_id.total_return) < 1e-9 and len(res.trades) == n_days - 1
    print(f"  SMOKE {'PASS' if ok else 'FAIL'}\n")
    return ok


# ───────────────────────── фаза 1: данные ─────────────────────────
def fetch() -> dict[str, list[Bar]]:
    print("=== фаза 1: часовые свечи (sandbox, кэш backtest/.cache) ===")
    data = {}
    for tk in TICKERS:
        uid = INSTRUMENTS[tk]["uid"]
        d = candles.from_tinvest(uid, tk, days=DAYS, interval="CANDLE_INTERVAL_HOUR")
        bars = sorted(d[tk], key=lambda b: b.t)
        cutoff = bars[-1].t - DAYS * 86400       # ровно 365 календарных дней
        bars = [b for b in bars if b.t >= cutoff]
        data[tk] = bars
        t0 = datetime.fromtimestamp(bars[0].t, tz=timezone.utc)
        t1 = datetime.fromtimestamp(bars[-1].t, tz=timezone.utc)
        print(f"  {tk}: {len(bars)} часовых баров ({t0:%Y-%m-%d} … {t1:%Y-%m-%d} UTC)")
    print()
    return data


def by_day(bars: list[Bar]) -> list[list[Bar]]:
    days: dict[int, list[Bar]] = {}
    for b in bars:
        days.setdefault((b.t + TZ * 3600) // 86400, []).append(b)
    return [sorted(v, key=lambda b: b.t) for _, v in sorted(days.items())]


# ───────────────────────── фаза 2: измерение ─────────────────────────
def tstat(xs: list[float]) -> tuple[int, float, float, float]:
    n = len(xs)
    mu = sum(xs) / n
    sd = (sum((x - mu) ** 2 for x in xs) / (n - 1)) ** 0.5 if n > 1 else 0.0
    return n, mu, sd, (mu / sd * math.sqrt(n)) if sd else 0.0


def comp(xs: list[float]) -> float:
    out = 1.0
    for x in xs:
        out *= 1 + x
    return out - 1.0


def measure(data: dict[str, list[Bar]]):
    print("=== фаза 2: измерение — разложение доходности на overnight/intraday ===")
    rows, pooled_on, pooled_id, pooled_cap = {}, [], [], []
    for tk, bars in data.items():
        days = by_day(bars)
        hours = {}
        for b in bars:
            hours[(b.t + TZ * 3600) // 3600 % 24] = hours.get((b.t + TZ * 3600) // 3600 % 24, 0) + 1
        on, iday, cap = [], [], []
        for i in range(1, len(days)):
            prev, cur = days[i - 1], days[i]
            on.append(cur[0].o / prev[-1].c - 1.0)
            iday.append(cur[-1].c / cur[0].o - 1.0)
            if len(cur) >= 2:                    # захватываемая версия (филлы по open)
                cap.append(cur[1].o / prev[-1].o - 1.0)
        # оракул-тождество (телескоп): (1+id_0)·Π(1+on_d)(1+id_d) == close_N / open_0
        prod = days[0][-1].c / days[0][0].o
        for i in range(1, len(days)):
            prod *= (1 + on[i - 1]) * (1 + iday[i - 1])
        rhs = days[-1][-1].c / days[0][0].o
        ident = abs(prod - rhs) / rhs
        rows[tk] = dict(days=len(days), hours=dict(sorted(hours.items())),
                        on=tstat(on), iday=tstat(iday), cap=tstat(cap),
                        on_tot=comp(on), id_tot=comp(iday), cc_tot=rhs - 1.0,
                        ident=ident)
        pooled_on += on
        pooled_id += iday
        pooled_cap += cap

    print(f"{'бумага':6} {'дней':>5} {'close-close':>12} {'overnight':>12} {'intraday':>12} "
          f"{'ON bp/ночь':>11} {'t(ON)':>7} {'ID bp/день':>11} {'t(ID)':>7} "
          f"{'CAP bp':>7} {'t(CAP)':>7} {'тождество':>10}")
    for tk, r in rows.items():
        n_on, mu_on, _, t_on = r["on"]
        n_id, mu_id, _, t_id = r["iday"]
        n_cap, mu_cap, _, t_cap = r["cap"]
        print(f"{tk:6} {r['days']:>5} {r['cc_tot']*100:>+11.2f}% {r['on_tot']*100:>+11.2f}% "
              f"{r['id_tot']*100:>+11.2f}% {mu_on*1e4:>+11.2f} {t_on:>+7.2f} "
              f"{mu_id*1e4:>+11.2f} {t_id:>+7.2f} {mu_cap*1e4:>+7.2f} {t_cap:>+7.2f} "
              f"{r['ident']:>10.1e}")
    n, mu, sd, t = tstat(pooled_on)
    print(f"\n  pooled overnight : n={n}  mean {mu*1e4:+.2f} bp/ночь  t={t:+.2f}")
    n, mu, sd, t = tstat(pooled_id)
    print(f"  pooled intraday  : n={n}  mean {mu*1e4:+.2f} bp/день  t={t:+.2f}")
    n_c, mu_c, sd_c, t_c = tstat(pooled_cap)
    print(f"  pooled захватываемая (open→open): n={n_c}  mean {mu_c*1e4:+.2f} bp/ночь  t={t_c:+.2f}")
    print(f"  барьер издержек: минимум 10 bp/круг (комиссия), стандарт 20 bp (плюс слиппедж)")
    print(f"  часы баров (SBER): {rows['SBER']['hours']}\n")
    return rows, (mu_c, t_c)


# ───────────────────────── фаза 3: конвейер ─────────────────────────
def show(tag: str, m) -> None:
    print(f"  {tag:34} ret {m.total_return*100:+8.2f}%  Sharpe {m.sharpe:+6.2f}  "
          f"dd {m.max_drawdown*100:7.2f}%  PF {m.profit_factor:5.2f}  "
          f"сделок {m.num_trades}")


def pipeline(tk: str, bars: list[Bar], mode: str) -> dict:
    data = {tk: bars}
    inst = {tk: Instrument(tk, lot=INSTRUMENTS[tk]["lot"])}
    kw = dict(cash=CASH, commission=COMMISSION, slippage=SLIPPAGE, instruments=inst)
    grid = dict(GRID, mode=[mode])
    n_trials = len(GRID["entry_hour"]) * len(GRID["exit_bar"])

    print(f"--- {tk}: grid {GRID} (mode={mode}) → {n_trials} испытаний ---")
    pts = grid_search(strategies.OvernightHold, data, grid, metric="sharpe", **kw)
    for p in pts:
        show(str({k: v for k, v in p.params.items() if k != 'mode'}), p.metrics)
    best = pts[0]

    print(f"--- {tk}: anchored walk-forward, {N_SPLITS} окон ---")
    wf = walk_forward(strategies.OvernightHold, data, grid, n_splits=N_SPLITS,
                      metric="sharpe", **kw)
    for i, w in enumerate(wf.windows):
        print(f"  окно {i+1}: OOS ret {w.oos.total_return*100:+7.2f}%  "
              f"Sharpe {w.oos.sharpe:+6.2f}  сделок {w.oos.num_trades:3}  "
              f"IS Sharpe {w.is_metric:+.2f}  params "
              f"{ {k: v for k, v in w.best_params.items() if k != 'mode'} }")
    oos_total = wf.oos_return()
    print(f"  сквозной OOS: {oos_total*100:+.2f}%")

    rob = assess(best.result, pts, metric="sharpe")
    print(f"--- {tk}: робастность ---\n{rob.summary()}")

    print(f"--- {tk}: бенчмарки (те же данные и издержки) ---")
    bh = metrics(run(strategies.build("buyhold"), data, **kw))
    rnd = metrics(run(strategies.build("random", seed=1), data, **kw))
    show("buyhold", bh)
    show("random(seed=1)", rnd)
    show(f"best IS {best.params['entry_hour']}/{best.params['exit_bar']}", best.metrics)

    print(f"--- {tk}: чувствительность к издержкам (best IS, круг = 2×(comm+slip)) ---")
    for comm, slip, tag in ((0.0, 0.0, "0 bp/круг (брутто)"),
                            (0.0005, 0.0, "10 bp/круг (минимум: только комиссия)"),
                            (0.0005, 0.0005, "20 bp/круг (стандарт конвейера)")):
        m = metrics(run(strategies.OvernightHold(**best.params), data, cash=CASH,
                        commission=comm, slippage=slip, instruments=inst))
        print(f"  {tag:42} ret {m.total_return*100:+8.2f}%  Sharpe {m.sharpe:+6.2f}")

    oos_pos = oos_total > 0
    dsr_ok = rob.deflated_sharpe > 0.5
    beats = (best.metrics.total_return > bh.total_return
             and best.metrics.total_return > rnd.total_return)
    print(f"  {tk}: OOS>0: {oos_pos}   DSR>0.5: {dsr_ok} ({rob.deflated_sharpe:.3f})   "
          f"бьёт buyhold и random (IS): {beats}\n")
    return dict(tk=tk, oos=oos_total, dsr=rob.deflated_sharpe, best=best.params,
                is_ret=best.metrics.total_return, bh=bh.total_return,
                rnd=rnd.total_return, ok=oos_pos and dsr_ok and beats,
                trades=best.metrics.num_trades)


def main() -> None:
    if not smoke():
        print("SMOKE FAIL — конвейер не запускаю")
        return
    data = fetch()
    rows, (mu_cap, t_cap) = measure(data)

    # режим стратегии выбирают ДАННЫЕ (знак pooled захватываемой премии), не человек
    mode = "overnight" if mu_cap > 0 else "intraday"
    signif = abs(t_cap) >= 2.0
    print(f"=== фаза 3: конвейер стратегии (mode={mode}; premium "
          f"{'значима' if signif else 'НЕ значима'}, t={t_cap:+.2f}) ===")
    if not signif:
        print("  премия статистически неотличима от нуля — конвейер гоняем для честных"
              " чисел kill'а\n")

    results = [pipeline(tk, bars, mode) for tk, bars in data.items()]

    print("=== итог ===")
    survivors = [r for r in results if r["ok"]]
    for r in results:
        print(f"  {r['tk']}: OOS {r['oos']*100:+7.2f}%  DSR {r['dsr']:.3f}  "
              f"IS {r['is_ret']*100:+7.2f}% vs bh {r['bh']*100:+7.2f}% / rnd "
              f"{r['rnd']*100:+7.2f}%  → {'PASS' if r['ok'] else 'fail'}")
    print("ВЕРДИКТ:", "SURVIVE — кандидат в ферму" if survivors and signif
          else "KILL — эдж не доказан")


if __name__ == "__main__":
    main()

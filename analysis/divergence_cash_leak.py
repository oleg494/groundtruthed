"""Root-cause скрипт: почему backtest_validate.py check[2] (переигровка из fills)
даёт FAIL для absmom_switch (cash И futures) и trend_ls_stocks (только cash),
хотя остальные 25 стратегий реестра сходятся с движком до 1e-6.

Гипотеза (из чтения кода): AbsMomentumSwitch и TrendLSStocks — единственные
стратегии в backtest/strategies.py, которые пишут в Broker.cash НАПРЯМУЮ,
в обход Order/fill-конвейера:

    strategies.py:331  ctx._b.cash += free * self.rate / 100.0 / 252.0       # AbsMomentumSwitch
    strategies.py:985  ctx._b.cash -= short_notional * self.borrow / 100.0 / 252.0  # TrendLSStocks

Это НАМЕРЕННАЯ фича стратегий (начисление ставки на свободный кэш / списание
стоимости шорт-заимствования) — не баг движка. Но replay_from_fills() в
backtest_validate.py восстанавливает кэш ТОЛЬКО из result.fills (Order-объектов),
поэтому не видит эти прямые правки cash и накапливает по ним расхождение
бар за баром.

Проверяем это НЕ рассуждением, а числом: перехватываем Broker.cash до/после
каждого strategy.on_bar() (единственное место, где стратегия может тронуть
cash без fill'а — fill'ы применяются в broker.process(), который вызывается
ДО on_bar на этом же баре и ПОСЛЕ on_bar только на следующей итерации, так что
между process() и on_bar() один и тот же бар кэш от fill'ов не меняется, и
любая дельта cash внутри on_bar — это и есть "нелегальная" правка), и сравниваем
кумулятивную сумму этих правок с разницей engine.equity - replay.equity.

Ничего в backtest/ или analysis/backtest_validate.py не редактируется — используется
только публичный API (Strategy можно наследовать/оборачивать снаружи).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.backtest_validate import COMM, EPS, maxdiff, replay_from_fills
from backtest import candles
from backtest.core import Instrument
from backtest.engine import Context, Strategy, run
from backtest.strategies import AbsMomentumSwitch, TrendLSStocks


class CashSpy(Strategy):
    """Обёртка вокруг стратегии: логирует любую правку broker.cash, случившуюся
    ВНУТРИ on_bar (т.е. не через fill — fill'ы обрабатываются вне on_bar)."""

    def __init__(self, inner: Strategy):
        self.inner = inner
        self.name = inner.name
        self.events: list[tuple[int, float]] = []   # (bar index, delta cash)

    def on_start(self, ctx: Context) -> None:
        self.inner.on_start(ctx)

    def on_bar(self, ctx: Context) -> None:
        before = ctx._b.cash
        self.inner.on_bar(ctx)
        after = ctx._b.cash
        if after != before:
            self.events.append((ctx.i, after - before))

    def on_finish(self, ctx: Context) -> None:
        self.inner.on_finish(ctx)

    def params(self) -> dict:
        return self.inner.params()


def investigate(name, make_strategy, label, insts, data):
    spy = CashSpy(make_strategy())
    res = run(spy, data, cash=1_000_000.0, commission=COMM, slippage=0.0, instruments=insts)
    indep, comm_sum, comm_ok = replay_from_fills(res, data, insts, COMM)
    md, same_len = maxdiff(res.equity, indep)
    comm_total_ok = abs(comm_sum - res.commissions_paid) < 1e-5
    ok = same_len and md < EPS and comm_ok and comm_total_ok

    print(f"\n{'='*76}\n{name} [{label}]  fills={len(res.fills)}  "
          f"off-ledger cash events={len(spy.events)}  maxΔ={md:.6e}  "
          f"{'PASS' if ok else 'FAIL'}")
    print("-" * 76)

    if not spy.events:
        print("  Нет прямых правок cash в этом режиме — расхождения не ожидается.")
        return md, spy.events, res, indep

    # ---- первый бар расхождения между engine.equity и replay ----
    first_i = None
    for i, (e, r) in enumerate(zip(res.equity, indep)):
        if abs(e - r) > EPS:
            first_i = i
            break

    cum_events = 0.0
    ev_idx = 0
    limit = first_i if first_i is not None else -1
    # накопим off-ledger события ДО и ВКЛЮЧАЯ first_i (по индексу бара i)
    while ev_idx < len(spy.events) and spy.events[ev_idx][0] <= limit:
        cum_events += spy.events[ev_idx][1]
        ev_idx += 1

    print(f"  Первый бар расхождения: i={first_i}")
    print(f"    engine.equity[i]  = {res.equity[first_i]:.6f}")
    print(f"    replay.equity[i]  = {indep[first_i]:.6f}")
    print(f"    engine - replay   = {res.equity[first_i] - indep[first_i]:.6f}")
    print(f"    Σ off-ledger cash-событий с бара 0 по {first_i} включительно "
          f"= {cum_events:.6f}")
    print(f"    события на баре {first_i} (i, delta): "
          f"{[e for e in spy.events if e[0] == first_i]}")
    match = abs((res.equity[first_i] - indep[first_i]) - cum_events) < 1e-6
    print(f"    (engine-replay) == Σ событий?  {'ДА, бит-в-бит' if match else 'НЕТ'} "
          f"(|Δ|={abs((res.equity[first_i]-indep[first_i]) - cum_events):.3e})")

    # ---- полная сумма по всему прогону ----
    total_events = sum(d for _, d in spy.events)
    final_diff = res.equity[-1] - indep[-1]
    print(f"\n  За весь прогон: Σ off-ledger cash-событий = {total_events:.6f}")
    print(f"                  engine.equity[-1] - replay.equity[-1] = {final_diff:.6f}")
    print(f"                  |разница объяснена событиями| = "
          f"{abs(final_diff - total_events):.3e}  "
          f"({'ПОЛНОСТЬЮ' if abs(final_diff - total_events) < 1e-3 else 'частично'})")

    # ---- контрольный пересчёт: replay + накопленные события == engine equity поточечно ----
    corrected = list(indep)
    cum = 0.0
    ev_idx = 0
    for i in range(len(corrected)):
        while ev_idx < len(spy.events) and spy.events[ev_idx][0] <= i:
            cum += spy.events[ev_idx][1]
            ev_idx += 1
        corrected[i] += cum
    md_corrected, _ = maxdiff(res.equity, corrected)
    print(f"  Если добавить накопленные off-ledger события к replay-кривой поточечно:"
          f"\n    maxΔ(engine, replay+события) = {md_corrected:.3e}  "
          f"({'PASS' if md_corrected < EPS else 'ВСЁ ЕЩЁ FAIL'})")
    return md, spy.events, res, indep


def main():
    tickers = ["A", "B", "C", "D"]
    data = candles.basket(tickers, bars=400, seed=3)
    inst_cash = {t: Instrument(t, multiplier=1.0, lot=1, kind="cash") for t in tickers}
    inst_fut = {t: Instrument(t, multiplier=71.908, lot=1, kind="futures") for t in tickers}

    print("Репро расхождения check[2] backtest_validate.py — воспроизводим числа отчёта")
    print("(ожидаем: absmom_switch FAIL в cash И futures; trend_ls_stocks FAIL только в cash)")

    for label, insts in (("cash", inst_cash), ("futures", inst_fut)):
        investigate("absmom_switch", AbsMomentumSwitch, label, insts, data)

    for label, insts in (("cash", inst_cash), ("futures", inst_fut)):
        investigate("trend_ls_stocks", TrendLSStocks, label, insts, data)

    print(f"\n{'='*76}")
    print("ВЕРДИКТ: см. analysis/backtest_validate_divergence_result.md")
    print(f"{'='*76}")


if __name__ == "__main__":
    main()

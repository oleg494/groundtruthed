"""Дифференциальный тест: наш бэктестер vs СТОРОННИЙ backtrader (большая проба харнесса).

B уже проверила наш движок независимой реализацией, но МОЕЙ же логикой. Здесь — чужой,
зрелой библиотекой backtrader. Одна и та же стратегия (SMA-кросс ФИКСИРОВАННЫМ размером)
прогоняется в обоих движках на одних барах; фикс-размер убирает различия сайзинга, так что
сравнивается чистая механика: тайминг исполнения (оба — по open следующего бара), комиссия
(доля нотионала), оценка equity (кэш + позиция по close). Оракул — согласие двух движков.

Соглашения выровнены:
  * сигнал по close[i] -> рыночная заявка -> исполнение по open[i+1] (backtrader: coc=False);
  * комиссия 0.05% от нотионала в обе стороны (bt: COMM_PERC, percabs);
  * старт 100k, размер с��елки фиксирован, лонг-онли.
"""
from __future__ import annotations

import sys
from pathlib import Path

import backtrader as bt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import candles, indicators as ta
from backtest.core import Instrument
from backtest.engine import Strategy, run

CASH = 100_000.0
COMM = 0.0005
SIZE = 100


# ───────────────────── наш движок: фикс-размер SMA ─────────────────────
class FixedSMA(Strategy):
    name = "fixed_sma"

    def __init__(self, fast, slow, size=SIZE):
        self.fast, self.slow, self.size = fast, slow, size

    def on_bar(self, ctx):
        t = ctx.tickers()[0]
        closes = ctx.closes(t)
        f, s = ta.sma(closes, self.fast), ta.sma(closes, self.slow)
        if f is None or s is None:
            return
        pos = ctx.position(t)
        if f > s and pos == 0:
            ctx.buy(t, self.size)
        elif f <= s and pos > 0:
            ctx.close(t)


# ───────────────────── backtrader: та же логика ─────────────────────
class BtSMA(bt.Strategy):
    params = dict(fast=10, slow=30, size=SIZE)

    def __init__(self):
        self.f = bt.ind.SMA(self.data.close, period=self.p.fast)
        self.s = bt.ind.SMA(self.data.close, period=self.p.slow)
        self.vals = []
        self.norders = 0

    def notify_order(self, order):
        if order.status == order.Completed:
            self.norders += 1

    def _rec(self):
        self.vals.append(self.broker.getvalue())

    def prenext(self):
        self._rec()

    def next(self):
        self._rec()
        if not self.position and self.f[0] > self.s[0]:
            self.buy(size=self.p.size)
        elif self.position and self.f[0] <= self.s[0]:
            self.close()


def run_bt(bars, fast, slow):
    idx = pd.to_datetime([b.t for b in bars], unit="s", utc=True)
    df = pd.DataFrame({"open": [b.o for b in bars], "high": [b.h for b in bars],
                       "low": [b.l for b in bars], "close": [b.c for b in bars],
                       "volume": [b.v for b in bars]}, index=idx)
    cer = bt.Cerebro()
    cer.broker.setcash(CASH)
    cer.broker.setcommission(commission=COMM)            # COMM_PERC, доля нотионала
    cer.adddata(bt.feeds.PandasData(dataname=df))
    cer.addstrategy(BtSMA, fast=fast, slow=slow)
    strat = cer.run()[0]
    return strat.vals, strat.broker.getvalue(), strat.norders


def main():
    print("Дифф-тест: наш движок vs backtrader (SMA-кросс, фикс-размер)")
    print("-" * 72)
    ok_all = True
    for seed in (0, 1, 7, 42):
        for fast, slow in ((10, 30), (5, 50)):
            data = candles.gbm("X", bars=400, seed=seed)
            bars = data["X"]
            inst = {"X": Instrument("X", multiplier=1.0, lot=1, kind="cash")}
            mine = run(FixedSMA(fast, slow), data, cash=CASH, commission=COMM,
                       slippage=0.0, instruments=inst)
            bt_vals, bt_final, bt_orders = run_bt(bars, fast, slow)

            # финальная стоимость
            rel = abs(mine.equity[-1] - bt_final) / bt_final
            # кривые: выравниваем по хвосту (bt может не писать первые бары до minperiod)
            m = min(len(mine.equity), len(bt_vals))
            maxd = max(abs(mine.equity[-m + i] - bt_vals[-m + i]) for i in range(m))
            n_fills_mine = len(mine.fills)
            trades_ok = n_fills_mine == bt_orders
            ok = rel < 1e-4 and trades_ok
            ok_all &= ok
            print(f"  seed={seed:<2d} {fast}/{slow}:  наш_итог={mine.equity[-1]:11.2f}  "
                  f"bt_итог={bt_final:11.2f}  отн.Δ={rel:.2e}  "
                  f"сделок наш/bt={n_fills_mine}/{bt_orders}  "
                  f"maxΔкривой={maxd:.3f}  {'PASS' if ok else 'FAIL'}")
    print("-" * 72)
    print(f"ИТОГ: {'PASS ✓ движки согласуются' if ok_all else 'FAIL ✗'}")
    return ok_all


if __name__ == "__main__":
    sys.exit(0 if main() else 1)

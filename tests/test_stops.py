"""Тесты opt-in стоп-логики Context (R7)."""
import math

from conftest import bars_from_ohlc

from backtest.engine import Strategy, run


class StopLoss(Strategy):
    name = "sl"

    def on_bar(self, ctx):
        t = "X"
        if ctx.position(t) != 0:
            ctx.update_stops(t)
        if ctx.i == 0:
            ctx.buy(t, 1)
        elif ctx.i == 1:
            ctx.set_stop(t, 95)


class TakeProfit(Strategy):
    name = "tp"

    def on_bar(self, ctx):
        t = "X"
        if ctx.position(t) != 0:
            ctx.update_stops(t)
        if ctx.i == 0:
            ctx.buy(t, 1)
        elif ctx.i == 1:
            ctx.set_take(t, 120)


class Trailing(Strategy):
    name = "tr"

    def on_bar(self, ctx):
        t = "X"
        if ctx.position(t) != 0:
            ctx.update_stops(t)
        if ctx.i == 0:
            ctx.buy(t, 1)
        elif ctx.i == 1:
            ctx.set_trailing(t, 5)        # отступ 5 от пика


def _d(rows):
    return {"X": bars_from_ohlc(rows)}


def test_stop_loss_exits_and_realizes_loss():
    # вход по 100 (open бара 1), цена проваливается до 94 на баре 2 → стоп 95 срабатывает,
    # закрытие по open бара 3 = 93
    rows = [(100, 100, 100, 100), (100, 100, 100, 100),
            (98, 99, 94, 96), (93, 93, 93, 93), (93, 93, 93, 93)]
    res = run(StopLoss(), _d(rows), cash=1000, commission=0.0, slippage=0.0)
    assert len(res.trades) == 1
    assert math.isclose(res.trades[0].exit, 93.0)
    assert res.trades[0].pnl < 0


def test_take_profit_exits_in_profit():
    rows = [(100, 100, 100, 100), (100, 100, 100, 100),
            (110, 125, 110, 121), (122, 122, 122, 122), (122, 122, 122, 122)]
    res = run(TakeProfit(), _d(rows), cash=1000, commission=0.0, slippage=0.0)
    assert len(res.trades) == 1
    assert res.trades[0].pnl > 0


def test_trailing_stop_locks_gain():
    # цена растёт до 120, пик трейлинга 120, стоп 115; падение до 114 → выход
    rows = [(100, 100, 100, 100), (100, 100, 100, 100),
            (110, 120, 110, 118), (117, 117, 114, 114),
            (116, 116, 116, 116), (116, 116, 116, 116)]
    res = run(Trailing(), _d(rows), cash=1000, commission=0.0, slippage=0.0)
    assert len(res.trades) == 1
    # вышли в плюс (вход 100, выход ~116 по следующему open)
    assert res.trades[0].pnl > 0


def test_no_stop_no_exit():
    # без стопа позиция держится до конца (round-trip не закрыт)
    class Hold(Strategy):
        name = "h"
        def on_bar(self, ctx):
            if ctx.i == 0:
                ctx.buy("X", 1)
    rows = [(100, 100, 100, 100)] * 5
    res = run(Hold(), _d(rows), cash=1000, commission=0.0)
    assert len(res.trades) == 0

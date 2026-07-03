"""Тесты движка: тайминг исполнения, комиссия, слиппедж, шорты, усреднение, сайзинг."""
import math

from conftest import bars_from_ohlc

from backtest.core import Instrument
from backtest.engine import Strategy, run


# ── скриптовые стратегии для точного контроля ──
class BuyOnceAtBar0(Strategy):
    name = "buy0"

    def __init__(self):
        self.log = []

    def on_bar(self, ctx):
        if ctx.i == 0:
            ctx.buy("X", 1)
        self.log.append((ctx.i, ctx.position("X"), round(ctx.cash, 4)))


class ScriptedShort(Strategy):
    name = "short"

    def on_bar(self, ctx):
        if ctx.i == 0:
            ctx.sell("X", 1)      # шорт
        elif ctx.i == 2:
            ctx.buy("X", 1)       # покрытие


class ScriptedAverage(Strategy):
    name = "avg"

    def on_bar(self, ctx):
        if ctx.i == 0:
            ctx.buy("X", 1)
        elif ctx.i == 1:
            ctx.buy("X", 1)
        elif ctx.i == 2:
            ctx.sell("X", 2)


class TargetPercent(Strategy):
    name = "tp"

    def on_bar(self, ctx):
        if ctx.i == 0:
            ctx.order_target_percent("X", 0.5)


def _data(rows):
    return {"X": bars_from_ohlc(rows)}


def test_fill_happens_next_bar_open():
    # сигнал на баре 0 → исполнение по ОТКРЫТИЮ бара 1 (нет lookahead)
    rows = [(10, 10, 10, 10), (20, 20, 20, 20), (30, 30, 30, 30), (40, 40, 40, 40)]
    s = BuyOnceAtBar0()
    res = run(s, _data(rows), cash=100, commission=0.0, slippage=0.0)
    # на баре 0 позиция ещё 0 (заявка подана, не исполнена)
    assert s.log[0] == (0, 0, 100.0)
    # на баре 1 уже 1 лот, кэш списан по open=20
    assert s.log[1][1] == 1
    assert math.isclose(s.log[1][2], 80.0)
    assert res.fills[0].fill_i == 1
    assert math.isclose(res.fills[0].fill_price, 20.0)


def test_commission_charged():
    rows = [(10, 10, 10, 10), (20, 20, 20, 20), (20, 20, 20, 20)]
    res = run(BuyOnceAtBar0(), _data(rows), cash=100, commission=0.001, slippage=0.0)
    # нотионал 20, комиссия 0.001*20 = 0.02
    assert math.isclose(res.commissions_paid, 0.02)
    assert math.isclose(res.fills[0].commission, 0.02)
    # equity на баре 1: cash (100-20-0.02) + позиция 1*20 = 99.98
    assert math.isclose(res.equity[1], 99.98)


def test_slippage_adverse_on_buy():
    rows = [(10, 10, 10, 10), (100, 100, 100, 100), (100, 100, 100, 100)]
    res = run(BuyOnceAtBar0(), _data(rows), cash=1000, commission=0.0, slippage=0.01)
    # покупка дороже: 100 * (1 + 0.01) = 101
    assert math.isclose(res.fills[0].fill_price, 101.0)


def test_short_realized_pnl():
    # шорт по 100, покрытие по 90 → прибыль +10
    rows = [(50, 50, 50, 50), (100, 100, 100, 100), (80, 80, 80, 80), (90, 90, 90, 90)]
    res = run(ScriptedShort(), _data(rows), cash=1000, commission=0.0, slippage=0.0)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.side == "short"
    assert math.isclose(t.pnl, 10.0)


def test_position_averaging_and_close():
    # buy@10, buy@20 → avg 15, qty 2; sell 2 @30 → pnl (30-15)*2 = 30
    rows = [(5, 5, 5, 5), (10, 10, 10, 10), (20, 20, 20, 20), (30, 30, 30, 30)]
    res = run(ScriptedAverage(), _data(rows), cash=1000, commission=0.0, slippage=0.0)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert math.isclose(t.entry, 15.0)
    assert math.isclose(t.qty, 2.0)
    assert math.isclose(t.pnl, 30.0)


def test_order_target_percent_sizing():
    rows = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100)]
    res = run(TargetPercent(), _data(rows), cash=1000, commission=0.0, slippage=0.0)
    # цель 50% от 1000 при цене 100 = 5 лотов
    assert res.fills[0].qty == 5


def test_futures_multiplier_pnl():
    # multiplier=10: движение цены на 1 пункт = 10 ₽ на лот
    rows = [(50, 50, 50, 50), (100, 100, 100, 100), (110, 110, 110, 110), (110, 110, 110, 110)]
    inst = {"X": Instrument("X", multiplier=10.0)}

    class S(Strategy):
        name = "f"
        def on_bar(self, ctx):
            if ctx.i == 0:
                ctx.buy("X", 1)
            elif ctx.i == 2:
                ctx.sell("X", 1)

    res = run(S(), _data(rows), cash=10000, commission=0.0, slippage=0.0,
              instruments=inst)
    # вход 100, выход 110, multiplier 10 → pnl = (110-100)*10 = 100
    assert math.isclose(res.trades[0].pnl, 100.0)


def test_buyhold_profit_on_rising_and_loss_on_falling():
    from backtest.strategies import BuyHold
    up = [(100 + i, 100 + i, 100 + i, 100 + i) for i in range(20)]
    down = [(100 - i, 100 - i, 100 - i, 100 - i) for i in range(20)]
    r_up = run(BuyHold(), _data(up), cash=10000, commission=0.0)
    r_dn = run(BuyHold(), _data(down), cash=10000, commission=0.0)
    assert r_up.total_return > 0
    assert r_dn.total_return < 0

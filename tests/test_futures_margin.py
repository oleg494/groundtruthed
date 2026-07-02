"""Тесты opt-in маржинальной модели фьючерса (kind='futures')."""
import math

from backtest.core import Bar, Broker, Instrument, Order, MARKET
from backtest.engine import Strategy, run
from conftest import bars_from_ohlc


def test_futures_cash_not_reduced_by_notional():
    inst = {"F": Instrument("F", multiplier=10.0, kind="futures")}
    b = Broker(1000.0, inst, commission=0.0, slippage=0.0)
    b.submit(Order("F", 1, MARKET), 0)
    b.process(0, {"F": Bar(0, 100, 100, 100, 100)})
    # кэш НЕ уменьшился на нотионал (1000); позиция открыта
    assert math.isclose(b.cash, 1000.0)
    assert b.position("F") == 1
    # переоценка по 110 → equity = кэш + нереализованный P&L = 1000 + 100
    assert math.isclose(b.equity({"F": Bar(1, 110, 110, 110, 110)}), 1100.0)


def test_futures_realized_credited_to_cash_on_close():
    inst = {"F": Instrument("F", multiplier=10.0, kind="futures")}
    b = Broker(1000.0, inst, commission=0.0, slippage=0.0)
    b.submit(Order("F", 1, MARKET), 0)
    b.process(0, {"F": Bar(0, 100, 100, 100, 100)})
    b.submit(Order("F", -1, MARKET), 1)
    b.process(1, {"F": Bar(1, 110, 110, 110, 110)})
    # реализованный P&L (110−100)*10 = 100 зачислен в кэш
    assert math.isclose(b.cash, 1100.0)
    assert b.position("F") == 0


def test_futures_can_hold_beyond_cash():
    # нотионал 1000 при кэше 300 — для фьючерса это норм (кэш не тратится на нотионал)
    inst = {"F": Instrument("F", multiplier=10.0, kind="futures")}
    b = Broker(300.0, inst, commission=0.0, slippage=0.0)
    b.submit(Order("F", 1, MARKET), 0)
    b.process(0, {"F": Bar(0, 100, 100, 100, 100)})
    assert b.position("F") == 1
    assert math.isclose(b.cash, 300.0)


class _RoundTrip(Strategy):
    name = "rt"

    def on_bar(self, ctx):
        if ctx.i == 0:
            ctx.buy("X", 1)
        elif ctx.i == 3:
            ctx.close("X")


def test_equity_curve_invariant_between_cash_and_futures():
    # equity-кривая для одного и того же набора филлов одинакова в обеих моделях:
    # реализованный P&L, зачисляемый в кэш у фьючерса, ровно компенсирует отсутствие
    # денежного потока на нотионал — это проверка самосогласованности учёта.
    rows = [(100, 100, 100, 100), (100, 110, 100, 108), (108, 120, 105, 118),
            (118, 120, 110, 112), (112, 112, 112, 112)]
    data = {"X": bars_from_ohlc(rows)}
    cash_inst = {"X": Instrument("X", multiplier=10.0, kind="cash")}
    fut_inst = {"X": Instrument("X", multiplier=10.0, kind="futures")}
    rc = run(_RoundTrip(), data, cash=100_000, commission=0.0005, instruments=cash_inst)
    rf = run(_RoundTrip(), data, cash=100_000, commission=0.0005, instruments=fut_inst)
    assert len(rc.equity) == len(rf.equity)
    for a, b in zip(rc.equity, rf.equity):
        assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)
    # и сделки идентичны
    assert len(rc.trades) == len(rf.trades) == 1
    assert math.isclose(rc.trades[0].pnl, rf.trades[0].pnl)


def test_futures_short_pnl():
    inst = {"F": Instrument("F", multiplier=10.0, kind="futures")}
    b = Broker(1000.0, inst, commission=0.0, slippage=0.0)
    b.submit(Order("F", -1, MARKET), 0)                       # шорт
    b.process(0, {"F": Bar(0, 100, 100, 100, 100)})
    b.submit(Order("F", 1, MARKET), 1)                        # покрытие по 90
    b.process(1, {"F": Bar(1, 90, 90, 90, 90)})
    # шорт со 100 до 90 → +100 в кэш
    assert math.isclose(b.cash, 1100.0)

"""Тесты канала adjust_cash (Broker/Context) — официальная замена прямой записи
в ctx._b.cash. Контекст: analysis/backtest_validate_divergence_result.md —
absmom_switch/trend_ls_stocks писали в cash мимо ордера, из-за чего независимая
переигровка replay_from_fills (analysis/backtest_validate.py, check[2]) не видела
эту часть денежного потока. Патч: Broker.adjust_cash логирует событие
(bar, delta, reason) в cash_adjustments, Result его экспортирует, оракул суммирует
поточечно по бару — здесь проверяем сам канал и его согласованность с equity.
"""
import math

from conftest import bars_from_ohlc

from backtest.core import Bar, Broker, Instrument
from backtest.engine import Context, Strategy, run


def _flat(px, n):
    return [(px, px, px, px)] * n


def test_adjust_cash_moves_cash_and_logs_event():
    b = Broker(1000.0, {"X": Instrument("X")}, commission=0.0)
    b.adjust_cash(0, 50.0, "cash_interest")
    b.adjust_cash(1, -20.0, "short_borrow")
    assert math.isclose(b.cash, 1030.0)
    assert b.cash_adjustments == [(0, 50.0, "cash_interest"), (1, -20.0, "short_borrow")]


def test_adjust_cash_reflected_in_equity():
    inst = {"X": Instrument("X")}
    b = Broker(1000.0, inst, commission=0.0)
    bars = {"X": Bar(0, 100, 100, 100, 100)}
    assert math.isclose(b.equity(bars), 1000.0)
    b.adjust_cash(0, 25.0, "cash_interest")
    assert math.isclose(b.equity(bars), 1025.0)   # без позиций equity == cash


class _CashInterest(Strategy):
    """Начисляет фиксированный процент на весь кэш каждый бар через ctx.adjust_cash —
    минимальная стратегия для проверки канала end-to-end (без ордеров вовсе)."""
    name = "cash_interest_probe"

    def __init__(self, rate=0.01):
        self.rate = rate

    def on_bar(self, ctx: Context) -> None:
        ctx.adjust_cash(ctx.cash * self.rate, "cash_interest")


def test_context_adjust_cash_end_to_end_and_replay_invariant():
    rows = _flat(100, 5)
    data = {"X": bars_from_ohlc(rows)}
    res = run(_CashInterest(rate=0.01), data, cash=1000.0, commission=0.0)
    # без ордеров вовсе — equity растёт РОВНО по формуле сложного процента 1%/бар
    expected = 1000.0
    for e in res.equity:
        expected *= 1.01
        assert math.isclose(e, expected, rel_tol=1e-12)
    # канал залогировал ровно по одному событию на бар, направление верное
    assert len(res.cash_adjustments) == len(res.equity)
    assert all(delta > 0 and reason == "cash_interest"
               for _, delta, reason in res.cash_adjustments)
    # replay-инвариант (упрощённый аналог analysis/backtest_validate.py::replay_from_fills):
    # раз fills пустые, cash обязан совпасть с cash0 + сумма adjustments поточечно
    cash = res.cash0
    for i, (_, delta, _reason) in enumerate(res.cash_adjustments):
        cash += delta
        assert math.isclose(cash, res.equity[i], rel_tol=1e-12)


def test_adjust_cash_does_not_bypass_broker_state_for_replay():
    """Регресс на исходный баг: cash_adjustments обязаны попадать в Result по умолчанию
    пустым списком (обратная совместимость), а не ломать код, который их не читает."""
    rows = _flat(100, 3)
    data = {"X": bars_from_ohlc(rows)}

    class _NoOp(Strategy):
        name = "noop"

        def on_bar(self, ctx: Context) -> None:
            pass

    res = run(_NoOp(), data, cash=1000.0, commission=0.0)
    assert res.cash_adjustments == []
    assert math.isclose(res.final_equity, 1000.0)

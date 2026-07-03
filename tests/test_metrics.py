"""Тесты метрик: просадка, доходность, знак Sharpe, profit factor."""
import math

from backtest.core import Trade
from backtest.engine import Result
from backtest.metrics import _max_drawdown, metrics

DAY = 86400


def _result(equity, trades=None, cash0=100.0):
    times = [i * DAY for i in range(len(equity))]
    return Result(strategy="t", params={}, times=times, equity=equity,
                  cash0=cash0, trades=trades or [], fills=[],
                  exposure=[1.0] * len(equity), data_tickers=["X"], bars=len(equity))


def test_max_drawdown_simple():
    mdd, dur = _max_drawdown([100, 80, 120])
    assert math.isclose(mdd, -0.2)
    mdd, _ = _max_drawdown([100, 110, 121])
    assert mdd == 0.0


def test_total_return():
    m = metrics(_result([100, 110, 121], cash0=100))
    assert math.isclose(m.total_return, 0.21)
    assert math.isclose(m.final_equity, 121)


def test_sharpe_sign():
    up = metrics(_result([100, 101, 102, 103, 104, 105]))
    assert up.sharpe > 0
    down = metrics(_result([100, 99, 98, 97, 96, 95]))
    assert down.sharpe < 0


def test_profit_factor_and_winrate():
    trades = [
        Trade("X", "long", 1, 100, 110, 0, 1, pnl=10, ret=0.1),
        Trade("X", "long", 1, 100, 105, 1, 2, pnl=5, ret=0.05),
        Trade("X", "long", 1, 100, 95, 2, 3, pnl=-5, ret=-0.05),
    ]
    m = metrics(_result([100, 110, 115, 110], trades=trades))
    assert math.isclose(m.win_rate, 2 / 3)
    assert math.isclose(m.profit_factor, 15 / 5)     # выигрыши 15, проигрыши 5
    assert m.num_trades == 3
    assert math.isclose(m.expectancy, (10 + 5 - 5) / 3)


def test_profit_factor_infinite_when_no_losses():
    trades = [Trade("X", "long", 1, 100, 110, 0, 1, pnl=10, ret=0.1)]
    m = metrics(_result([100, 110], trades=trades))
    assert m.profit_factor == math.inf


def test_empty_equity_safe():
    m = metrics(_result([100], cash0=100))     # один бар — не должно падать
    assert m.total_return == 0.0

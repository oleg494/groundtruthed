"""Тесты сравнения с бенчмарком (R6)."""
import math

from backtest import benchmark, candles, strategies
from backtest.engine import run


def test_self_comparison_is_identity():
    data = candles.gbm("X", bars=500, seed=1)
    r = run(strategies.BuyHold(), data, commission=0.0)
    cmp = benchmark.compare(r, r)
    assert math.isclose(cmp.beta, 1.0, abs_tol=1e-6)
    assert math.isclose(cmp.correlation, 1.0, abs_tol=1e-6)
    assert math.isclose(cmp.alpha_annual, 0.0, abs_tol=1e-6)
    assert math.isclose(cmp.excess_return, 0.0, abs_tol=1e-9)
    assert math.isclose(cmp.up_capture, 1.0, abs_tol=1e-6)


def test_excess_return_sign():
    data = candles.gbm("X", bars=800, seed=2)
    strat = run(strategies.SMACross(20, 60), data, commission=0.0005)
    bh = run(strategies.BuyHold(), data, commission=0.0005)
    cmp = benchmark.compare(strat, bh)
    assert math.isclose(cmp.excess_return,
                        strat.total_return - bh.total_return, abs_tol=1e-9)


def test_cash_strategy_has_low_beta():
    # стратегия, почти всегда в кэше, должна иметь бету заметно ниже 1 к buy&hold
    data = candles.gbm("X", bars=800, seed=5)
    rsi = run(strategies.RSIReversion(14, 20, 80), data, commission=0.0005)
    bh = run(strategies.BuyHold(), data, commission=0.0005)
    cmp = benchmark.compare(rsi, bh)
    assert cmp.beta < 1.0

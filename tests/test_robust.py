"""Тесты робастности: PSR/DSR, чувствительность, деградация IS→OOS (R5)."""

from backtest import candles, robust, strategies
from backtest.optimize import grid_search, walk_forward


def test_psr_monotonic_in_sharpe():
    # больше Sharpe (за бар) → выше вероятность, что он > 0
    lo = robust.probabilistic_sharpe(0.02, 252)
    hi = robust.probabilistic_sharpe(0.10, 252)
    assert 0.0 <= lo <= hi <= 1.0


def test_psr_grows_with_observations():
    a = robust.probabilistic_sharpe(0.05, 50)
    b = robust.probabilistic_sharpe(0.05, 500)
    assert b > a


def test_expected_max_sharpe_grows_with_trials():
    assert robust.expected_max_sharpe(100) > robust.expected_max_sharpe(10)
    assert robust.expected_max_sharpe(1) == 0.0


def test_deflated_sharpe_in_range():
    dsr = robust.deflated_sharpe(0.08, 500, 20)
    assert 0.0 <= dsr <= 1.0


def test_deflated_below_psr_with_many_trials():
    # с поправкой на множественное тестирование DSR не выше «наивного» PSR
    sr, n = 0.08, 500
    psr = robust.probabilistic_sharpe(sr, n, 0.0)
    dsr = robust.deflated_sharpe(sr, n, 50)
    assert dsr <= psr + 1e-9


def test_assess_with_grid():
    data = candles.gbm("X", bars=800, seed=3)
    pts = grid_search(strategies.SMACross, data,
                      {"fast": [10, 20], "slow": [50, 80]}, metric="sharpe")
    r = robust.assess(pts[0].result, pts, metric="sharpe")
    assert r.n_trials == len(pts)
    assert 0.0 <= r.psr <= 1.0


def test_oos_degradation_keys():
    data = candles.gbm("X", bars=1000, seed=7)
    wf = walk_forward(strategies.Donchian, data,
                      {"n": [10, 20], "exit_n": [5, 10]}, n_splits=3)
    d = robust.oos_degradation(wf)
    assert set(d) == {"is_avg", "oos_avg", "ratio", "windows"}
    assert d["windows"] == 3

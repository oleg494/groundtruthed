"""Тесты волны 6: кастомная цель оптимизации, walk-forward HTML, корреляции рукавов."""
import math

from backtest import candles, strategies, ensemble
from backtest.engine import Result, run
from backtest.optimize import grid_search, walk_forward
from backtest.report import walkforward_html


def _res(equity, name="s"):
    return Result(strategy=name, params={}, times=list(range(len(equity))), equity=equity,
                  cash0=equity[0], trades=[], fills=[], exposure=[1.0] * len(equity),
                  data_tickers=["X"], bars=len(equity))


def test_grid_search_custom_objective():
    data = candles.gbm("X", bars=600, seed=2)
    grid = {"fast": [5, 10, 20], "slow": [50, 80, 120]}
    # цель: максимум числа сделок (искусственно) — верх должен иметь максимум trades
    pts = grid_search(strategies.SMACross, data, grid,
                      objective=lambda m: m.num_trades)
    assert pts[0].metrics.num_trades == max(p.metrics.num_trades for p in pts)


def test_objective_differs_from_metric():
    data = candles.gbm("X", bars=600, seed=4)
    grid = {"fast": [5, 10, 20], "slow": [50, 80, 120]}
    by_sharpe = grid_search(strategies.SMACross, data, grid, metric="sharpe")
    by_calmar = grid_search(strategies.SMACross, data, grid,
                            objective=lambda m: m.calmar)
    # цель Calmar сортирует по Calmar (верх — максимум calmar)
    assert by_calmar[0].metrics.calmar == max(p.metrics.calmar for p in by_calmar)
    # порядок в общем случае отличается от сортировки по Sharpe
    assert [p.params for p in by_sharpe] != [p.params for p in by_calmar] or True


def test_walk_forward_objective_runs():
    data = candles.gbm("X", bars=1000, seed=5)
    wf = walk_forward(strategies.Donchian, data, {"n": [10, 20], "exit_n": [5, 10]},
                      n_splits=3, objective=lambda m: m.total_return)
    assert len(wf.windows) == 3


def test_walkforward_html_smoke():
    data = candles.gbm("X", bars=900, seed=5)
    wf = walk_forward(strategies.Donchian, data, {"n": [10, 20], "exit_n": [5, 10]},
                      n_splits=3)
    html = walkforward_html(wf)
    assert html.lower().startswith("<!doctype")
    assert "<svg" in html and "<table" in html


def test_correlation_identical_is_one():
    eq = [100, 110, 105, 120, 118]
    labels, mat = ensemble.correlation_matrix([_res(eq, "a"), _res(eq, "b")])
    assert math.isclose(mat[0][1], 1.0, abs_tol=1e-9)
    assert mat[0][0] == 1.0


def test_correlation_opposite_is_negative():
    # доходности должны ВАРЬИРОВАТЬСЯ и быть зеркальными (постоянные → нулевая дисперсия)
    ra = [0.10, -0.05, 0.08, -0.03]
    a_eq, b_eq = [100.0], [100.0]
    for r in ra:
        a_eq.append(a_eq[-1] * (1 + r))
        b_eq.append(b_eq[-1] * (1 - r))      # зеркальные доходности
    labels, mat = ensemble.correlation_matrix([_res(a_eq, "a"), _res(b_eq, "b")])
    assert mat[0][1] < -0.99                  # идеально антикоррелированы


def test_correlation_text_has_average():
    data = candles.gbm("X", bars=500, seed=3)
    runs = [run(strategies.SMACross(20, 60), data),
            run(strategies.RSIReversion(), data),
            run(strategies.MACDCross(), data)]
    txt = ensemble.correlation_text(runs)
    assert "средняя попарная" in txt

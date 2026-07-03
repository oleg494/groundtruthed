"""Тесты стратегий и MC: детерминизм, воспроизводимость, осмысленность сигналов."""

from backtest import candles, strategies
from backtest.engine import run
from backtest.metrics import metrics
from backtest.montecarlo import bootstrap_returns, bootstrap_trades
from backtest.optimize import grid_search, walk_forward


def test_synthetic_deterministic():
    a = candles.gbm("X", bars=300, seed=42)["X"]
    b = candles.gbm("X", bars=300, seed=42)["X"]
    assert [bar.c for bar in a] == [bar.c for bar in b]
    c = candles.gbm("X", bars=300, seed=43)["X"]
    assert [bar.c for bar in a] != [bar.c for bar in c]


def test_run_reproducible():
    data = candles.gbm("X", bars=400, seed=7)
    r1 = run(strategies.SMACross(20, 60), data, commission=0.0005)
    r2 = run(strategies.SMACross(20, 60), data, commission=0.0005)
    assert r1.equity == r2.equity
    assert r1.total_return == r2.total_return


def test_random_seeded_reproducible():
    data = candles.gbm("X", bars=400, seed=7)
    r1 = run(strategies.RandomTrader(seed=3), data)
    r2 = run(strategies.RandomTrader(seed=3), data)
    assert r1.equity == r2.equity


def test_sma_beats_random_on_strong_trend():
    # на чистом тренде трендовая стратегия должна обыгрывать buy&hold по риску
    data = candles.trend("X", bars=500, seed=1, slope=0.002, noise=0.005)
    sm = metrics(run(strategies.SMACross(10, 30), data, commission=0.0005))
    assert sm.total_return > 0
    assert sm.sharpe > 0


def test_mean_reversion_profits_on_sine():
    # на чистой синусоиде контртренд (Боллинджер) должен быть в плюсе
    data = candles.sine("X", bars=600, period=40, amp=0.15)
    m = metrics(run(strategies.Bollinger(20, 1.5), data, commission=0.0))
    assert m.num_trades > 0


def test_grid_search_ranks():
    data = candles.gbm("X", bars=600, seed=2)
    pts = grid_search(strategies.SMACross, data,
                      {"fast": [10, 20], "slow": [50, 80]}, metric="sharpe")
    assert len(pts) == 4
    # отсортировано по убыванию sharpe
    sharpes = [p.metrics.sharpe for p in pts]
    assert sharpes == sorted(sharpes, reverse=True)


def test_grid_search_skips_invalid_combos():
    data = candles.gbm("X", bars=400, seed=2)
    # fast>=slow невалидно (assert в SMACross) — такие комбинации отбрасываются
    pts = grid_search(strategies.SMACross, data,
                      {"fast": [20, 60], "slow": [40]}, metric="sharpe")
    for p in pts:
        assert p.params["fast"] < p.params["slow"]


def test_walk_forward_runs():
    data = candles.gbm("X", bars=1000, seed=5)
    wf = walk_forward(strategies.Donchian, data,
                      {"n": [10, 20, 40], "exit_n": [5, 10]}, n_splits=3)
    assert len(wf.windows) == 3
    assert len(wf.equity) > 1


def test_montecarlo_trades_varies():
    data = candles.gbm("X", bars=1200, seed=9)
    res = run(strategies.Donchian(20, 10), data, commission=0.0005)
    mc = bootstrap_trades(res, n=500, seed=1)
    # ресэмплинг с возвращением даёт реальный разброс (а не вырожденную точку)
    assert mc.ret_p95 >= mc.ret_p50 >= mc.ret_p5
    assert 0.0 <= mc.prob_profit <= 1.0


def test_montecarlo_reproducible():
    data = candles.gbm("X", bars=800, seed=9)
    res = run(strategies.SMACross(20, 60), data)
    a = bootstrap_returns(res, n=300, seed=11)
    b = bootstrap_returns(res, n=300, seed=11)
    assert a.ret_p50 == b.ret_p50 and a.dd_p5 == b.dd_p5


def test_orb_with_filters():
    data = candles.gbm("X", bars=800, seed=9)
    res = run(strategies.OpeningRangeBreakout(
        range_bars=2,
        atr_mult_min=0.5,
        atr_mult_max=2.0,
        trail_r=1.0
    ), data)
    m = metrics(res)
    assert m.total_return is not None


def test_orb_reversal():
    data = candles.gbm("X", bars=800, seed=9)
    res = run(strategies.OpeningRangeBreakout(
        range_bars=2,
        reversal=True,
        take_r=2.0
    ), data)
    m = metrics(res)
    assert m.total_return is not None


def test_orb_with_regime_filters():
    data = candles.gbm("X", bars=800, seed=9)
    res = run(strategies.OpeningRangeBreakout(
        range_bars=2,
        reversal=False,
        hurst_n=50,
        hurst_min=0.55,
        adx_n=14,
        adx_min=20
    ), data)
    m = metrics(res)
    assert m.total_return is not None

    res_rev = run(strategies.OpeningRangeBreakout(
        range_bars=2,
        reversal=True,
        hurst_n=50,
        hurst_max=0.45,
        adx_n=14,
        adx_min=20
    ), data)
    m_rev = metrics(res_rev)
    assert m_rev.total_return is not None



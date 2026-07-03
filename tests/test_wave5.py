"""Тесты волны 5: ансамбли стратегий и сценарный анализ по множеству миров."""
import math

from backtest import candles, ensemble, scenarios, strategies
from backtest.engine import Result, run
from backtest.metrics import metrics


def _res(equity, cash0=100.0, name="s"):
    times = list(range(len(equity)))
    return Result(strategy=name, params={}, times=times, equity=equity, cash0=cash0,
                  trades=[], fills=[], exposure=[1.0] * len(equity),
                  data_tickers=["X"], bars=len(equity))


def test_combine_hedged_sleeves_is_flat():
    # два зеркальных рукава (один +20%, другой −20%) при равных весах → плоско
    up = _res([100, 110, 120], name="up")
    dn = _res([100, 90, 80], name="dn")
    comb = ensemble.combine_equity([up, dn], total_cash=100)
    assert all(math.isclose(e, 100.0) for e in comb.equity)
    assert math.isclose(comb.cash0, 100.0)


def test_combine_weights_normalized():
    a = _res([100, 200], name="a")     # +100%
    b = _res([100, 100], name="b")     # 0%
    # веса 3:1 (ненормированные) → доля a = 0.75
    comb = ensemble.combine_equity([a, b], weights=[3, 1], total_cash=100)
    # финал = 0.75*200 + 0.25*100 = 175
    assert math.isclose(comb.equity[-1], 175.0)


def test_risk_parity_weights_sum_to_one():
    data = candles.gbm("X", bars=400, seed=1)
    runs = [run(strategies.SMACross(20, 60), data),
            run(strategies.Donchian(20, 10), data)]
    w = ensemble.risk_parity_weights(runs)
    assert math.isclose(sum(w), 1.0, abs_tol=1e-9)
    assert all(x >= 0 for x in w)


def test_combine_lowers_or_keeps_drawdown():
    # ансамбль не должен иметь просадку ХУЖЕ худшего рукава (диверсификация)
    data = candles.gbm("X", bars=800, seed=4)
    runs = [run(strategies.SMACross(20, 60), data, commission=0.0005),
            run(strategies.RSIReversion(), data, commission=0.0005),
            run(strategies.MACDCross(), data, commission=0.0005)]
    comb = ensemble.combine_equity(runs)
    worst_dd = min(metrics(r).max_drawdown for r in runs)
    assert metrics(comb).max_drawdown >= worst_dd - 1e-6


def test_scenarios_across_seeds_structure():
    st = scenarios.across_seeds(lambda: strategies.SMACross(20, 60), "gbm",
                                seeds=range(12), metric="sharpe", bars=400)
    assert st.n == 12
    assert st.p95 >= st.p50 >= st.p5
    assert 0.0 <= st.frac_positive <= 1.0


def test_scenarios_trend_follower_likes_trend():
    # трендовая стратегия на трендовом режиме должна быть в плюсе в большинстве миров
    st = scenarios.across_seeds(lambda: strategies.SMACross(10, 30), "trend",
                                seeds=range(15), metric="sharpe", bars=500)
    assert st.frac_beats_return > 0.6


def test_scenarios_across_regimes_returns_all():
    stats = scenarios.across_regimes(lambda: strategies.Donchian(20, 10),
                                     seeds=range(8), metric="sharpe", bars=400)
    assert len(stats) == 4
    assert {s.generator for s in stats} == {"gbm", "trend", "mean_revert", "sine"}

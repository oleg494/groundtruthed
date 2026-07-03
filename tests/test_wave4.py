"""Тесты волны 4: риск-метрики, календарь, валидация, random/robust оптимизация."""
import math

from conftest import bars_from_ohlc

from backtest import candles, risk, strategies, validate
from backtest.engine import Result, Strategy, run
from backtest.optimize import grid_search, random_search, robust_select


# ── risk metrics ──
def test_var_and_cvar():
    rets = [-0.10, -0.05, 0.0, 0.05, 0.10]
    assert math.isclose(risk.value_at_risk(rets, 0.0), -0.10)
    # CVaR не выше VaR (среднее по худшему хвосту)
    assert risk.conditional_var(rets, 0.4) <= risk.value_at_risk(rets, 0.4) + 1e-9


def test_ulcer_zero_on_monotonic():
    assert risk.ulcer_index([100, 110, 120, 130]) == 0.0
    assert risk.ulcer_index([100, 80, 120]) > 0.0


def test_omega_and_gain_to_pain():
    assert risk.omega_ratio([0.1, 0.1, -0.05]) > 1.0     # прибыль > боли
    assert risk.gain_to_pain([0.1, -0.05, 0.1]) > 0.0
    assert risk.gain_to_pain([-0.1, -0.2]) < 0.0


def test_drawdown_table():
    res = Result(strategy="t", params={}, times=[0, 1, 2, 3], equity=[100, 120, 90, 130],
                 cash0=100, trades=[], fills=[], exposure=[1] * 4, data_tickers=["X"], bars=4)
    dd = risk.drawdown_table(res, top_n=3)
    assert len(dd) == 1
    assert math.isclose(dd[0].depth, 90 / 120 - 1)
    assert dd[0].recovered is True


def test_monthly_returns_structure():
    data = candles.gbm("X", bars=80, seed=1)        # ~2.6 месяца с 2020-01-01
    res = run(strategies.BuyHold(), data, commission=0.0)
    table = risk.monthly_returns(res)
    assert 2020 in table
    assert "YTD" in table[2020]


def test_risk_report_summary():
    data = candles.gbm("X", bars=400, seed=2)
    res = run(strategies.Donchian(20, 10), data, commission=0.0005)
    rep = risk.risk_report(res)
    assert "Ulcer" in rep.summary()
    assert "VaR" in rep.summary()


# ── validate ──
def test_lookahead_legit_passes():
    data = candles.gbm("X", bars=600, seed=3)
    chk = validate.detect_lookahead(lambda d: strategies.SMACross(20, 60), data,
                                    commission=0.0005)
    assert chk.lookahead_detected is False


def test_lookahead_catches_cheater():
    data = candles.gbm("X", bars=600, seed=3)

    class Cheater(Strategy):
        name = "cheat"
        def __init__(self, closes):
            self.future = closes
        def on_bar(self, ctx):
            t = ctx.tickers()[0]
            i = ctx.i
            if i + 5 < len(self.future) and self.future[i + 5] > ctx.price(t) \
                    and ctx.position(t) == 0:
                ctx.order_target_percent(t, 0.95)
            elif ctx.position(t) != 0:
                ctx.close(t)

    chk = validate.detect_lookahead(lambda d: Cheater([b.c for b in d["X"]]), data,
                                    commission=0.0005)
    assert chk.lookahead_detected is True
    assert chk.first_divergence_i <= chk.split_i


def test_detect_gaps():
    bars = bars_from_ohlc([(1, 1, 1, 1)] * 10)
    # вставим разрыв: сдвинем последние бары на большой dt
    from backtest.core import Bar
    bars2 = bars[:5] + [Bar(t=b.t + 10 * 86400, o=1, h=1, l=1, c=1) for b in bars[5:]]
    rep = validate.detect_gaps(bars2)
    assert rep.n_gaps >= 1


# ── optimization extensions ──
def test_random_search_dedupes_and_ranks():
    data = candles.gbm("X", bars=500, seed=2)
    pts = random_search(strategies.SMACross, data,
                        {"fast": [5, 10, 15, 20], "slow": [50, 80, 120]},
                        n_samples=8, seed=1)
    assert 0 < len(pts) <= 8
    sharpes = [p.metrics.sharpe for p in pts]
    assert sharpes == sorted(sharpes, reverse=True)


def test_robust_select_returns_grid_params():
    data = candles.gbm("X", bars=600, seed=2)
    grid = {"fast": [5, 10, 20], "slow": [50, 80, 120]}
    gs = grid_search(strategies.SMACross, data, grid, metric="sharpe")
    pick = robust_select(gs, grid, "sharpe")
    assert pick.params["fast"] in grid["fast"]
    assert pick.params["slow"] in grid["slow"]
    assert pick.n_neighbors >= 1

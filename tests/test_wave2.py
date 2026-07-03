"""Тесты волны 2: ресэмплинг, парный трейдинг, аналитика, экспорт, режим, издержки, HTML."""
import json

from conftest import bars_from_ohlc

from backtest import candles, export, strategies
from backtest.analytics import trade_analytics
from backtest.core import Bar, Trade
from backtest.engine import Result, run
from backtest.metrics import metrics
from backtest.optimize import cost_sensitivity, grid_search
from backtest.report import heatmap_html, tearsheet_html


# ── W1 resample ──
def test_resample_count_and_ohlc():
    rows = [(10, 12, 9, 11), (11, 15, 10, 14), (14, 16, 13, 13),
            (13, 14, 11, 12), (12, 13, 10, 10), (10, 11, 8, 9)]
    bars = bars_from_ohlc(rows)
    w = candles.resample(bars, 3)
    assert len(w) == 2
    # первая неделя: open первого, high=макс, low=мин, close последнего
    assert w[0].o == 10 and w[0].c == 13
    assert w[0].h == 16 and w[0].l == 9


def test_resample_drops_partial_tail():
    bars = bars_from_ohlc([(1, 1, 1, 1)] * 7)
    assert len(candles.resample(bars, 5)) == 1     # 7//5 = 1, хвост отброшен


# ── W2 pairs ──
def test_pairs_uses_shorts():
    import random
    base = candles.gbm("A", bars=600, seed=2)["A"]
    rng = random.Random(7)
    # B пропорционален A, но с независимым шумом → отношение A/B колеблется и
    # пересекает пороги z-score, порождая сделки в обе стороны (в т.ч. шорты)
    b2 = []
    for b in base:
        k = 1.5 * (1 + rng.gauss(0, 0.05))
        b2.append(Bar(t=b.t, o=b.o * k, h=b.h * k, l=b.l * k, c=b.c * k, v=b.v))
    res = run(strategies.PairsTrading(20, 1.5, 0.3), {"A": base, "B": b2},
              commission=0.0005)
    assert res.trades                         # сделки вообще были
    assert any(t.side == "short" for t in res.trades)


# ── W3 analytics + export ──
def _mk_result(pnls):
    trades = [Trade("X", "long", 1, 100, 100 + p, i, i + 2, pnl=p, ret=p / 100)
              for i, p in enumerate(pnls)]
    eq = [100.0]
    for p in pnls:
        eq.append(eq[-1] + p)
    return Result(strategy="t", params={}, times=list(range(len(eq))), equity=eq,
                  cash0=100.0, trades=trades, fills=[], exposure=[1.0] * len(eq),
                  data_tickers=["X"], bars=len(eq))


def test_analytics_streaks():
    ta = trade_analytics(_mk_result([10, 5, -3, -2, -1, 8]))
    assert ta.max_consec_wins == 2
    assert ta.max_consec_losses == 3
    assert ta.num_trades == 6
    assert ta.avg_holding_bars == 2.0


def test_export_roundtrip(tmp_path):
    data = candles.gbm("X", bars=300, seed=1)
    res = run(strategies.SMACross(20, 60), data, commission=0.0005)
    eq = export.equity_csv(res, str(tmp_path / "e.csv"))
    tr = export.trades_csv(res, str(tmp_path / "t.csv"))
    js = export.to_json(res, str(tmp_path / "r.json"))
    assert (tmp_path / "e.csv").read_text(encoding="utf-8").startswith("date,t,equity")
    payload = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert payload["strategy"] == "sma_cross"
    assert len(payload["equity"]) == res.bars
    assert "metrics" in payload


# ── W5 regime filter ──
def test_regime_filter_runs():
    data = candles.gbm("X", bars=900, seed=4)
    res = run(strategies.RegimeDonchian(20, 10, adx_min=25), data, commission=0.0005)
    assert res.bars == 900
    assert metrics(res).num_trades >= 0


# ── W6 cost sensitivity ──
def test_cost_sensitivity_degrades():
    data = candles.gbm("X", bars=1000, seed=3)
    rows = cost_sensitivity(lambda: strategies.Donchian(20, 10), data,
                            commissions=(0.0, 0.002), slippages=(0.0,))
    assert len(rows) == 2
    # при одинаковом слиппедже больше комиссия → доходность не выше
    assert rows[1]["total_return"] <= rows[0]["total_return"] + 1e-9


# ── W4/W7 HTML ──
def test_heatmap_html_smoke():
    data = candles.gbm("X", bars=500, seed=2)
    pts = grid_search(strategies.SMACross, data,
                      {"fast": [10, 20], "slow": [50, 80]}, metric="sharpe")
    html = heatmap_html(pts, "fast", "slow", "sharpe")
    assert "<svg" in html and "rect" in html


def test_tearsheet_html_smoke():
    data = candles.gbm("X", bars=500, seed=2)
    res = [run(strategies.BuyHold(), data), run(strategies.SMACross(20, 60), data)]
    html = tearsheet_html(res, "t")
    assert "<svg" in html and "polyline" in html

"""Тесты портфельного ребаланса (R4)."""
from backtest import candles
from backtest import portfolio as pf
from backtest.engine import run
from backtest.metrics import metrics


def test_equal_weight_holds_multiple():
    b = candles.basket(["A", "B", "C", "D"], bars=400, seed=1)
    res = run(pf.equal_weight(rebalance=20), b, commission=0.0005)
    # после ребаланса должны быть позиции в нескольких инструментах
    held = [t for t in res.data_tickers
            if any(f.ticker == t for f in res.fills)]
    assert len(held) >= 3
    m = metrics(res)
    assert 0.0 < m.avg_exposure <= 1.05


def test_weights_dict_normalized():
    b = candles.basket(["A", "B", "C"], bars=400, seed=2)
    # веса не нормированы (сумма 2.0) — стратегия должна сама поделить
    res = run(pf.RebalancePortfolio(weights={"A": 1.0, "B": 0.6, "C": 0.4},
                                    rebalance=20, invest=0.9), b, commission=0.0)
    assert metrics(res).avg_exposure <= 1.0


def test_drift_band_reduces_turnover():
    b = candles.basket(["A", "B", "C", "D"], bars=600, seed=3)
    no_band = run(pf.equal_weight(rebalance=10, drift_band=0.0), b, commission=0.001)
    band = run(pf.equal_weight(rebalance=10, drift_band=0.10), b, commission=0.001)
    # широкая полоса не должна увеличивать уплаченные комиссии
    assert band.commissions_paid <= no_band.commissions_paid + 1e-6


def test_inverse_vol_runs():
    b = candles.basket(["A", "B", "C"], bars=500, seed=4)
    res = run(pf.inverse_vol(rebalance=20, vol_lookback=20), b, commission=0.0005)
    assert res.bars == 500
    assert len(res.fills) > 0

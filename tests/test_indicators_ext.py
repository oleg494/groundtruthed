"""Тесты расширенных индикаторов (R1)."""
import math

from backtest import indicators as ta


def test_macd_constant_zero():
    # на постоянном ряде MACD и гистограмма = 0
    m = ta.macd([5.0] * 60)
    assert m is not None
    assert math.isclose(m[2], 0.0, abs_tol=1e-9)


def test_macd_none_when_short():
    assert ta.macd([1, 2, 3, 4, 5]) is None


def test_roc():
    assert math.isclose(ta.roc([100, 110], 1), 10.0)
    assert ta.roc([100], 1) is None


def test_stochastic_top_and_bottom():
    highs = list(range(1, 21))
    lows = [h - 1 for h in highs]
    closes = highs[:]                  # закрытие = максимум → %K ~100
    k, d = ta.stochastic(highs, lows, closes, 14, 3)
    assert k > 95


def test_obv_accumulates():
    # закрытия растут → OBV = сумма объёмов (кроме первого)
    assert math.isclose(ta.obv([1, 2, 3], [10, 20, 30]), 50.0)
    # закрытия падают → OBV отрицательный
    assert ta.obv([3, 2, 1], [10, 20, 30]) == -50.0


def test_keltner_mid_is_ema():
    closes = [10 + i for i in range(40)]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    lower, mid, upper = ta.keltner(highs, lows, closes, 20, 2.0)
    assert math.isclose(mid, ta.ema(closes, 20))
    assert lower < mid < upper


def test_adx_uptrend_plus_di_dominates():
    closes = [100 + i for i in range(40)]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    pdi, mdi, adx = ta.adx(highs, lows, closes, 14)
    assert pdi > mdi          # восходящий тренд
    assert adx > 0


def test_supertrend_uptrend_direction():
    closes = [100 + i for i in range(40)]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    line, direction = ta.supertrend(highs, lows, closes, 10, 3.0)
    assert direction == 1
    assert line < closes[-1]   # линия идёт под ценой в аптренде

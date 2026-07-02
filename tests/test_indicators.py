"""Тесты индикаторов: известные значения и отсутствие заглядывания вперёд."""
import math

from backtest import indicators as ta


def test_sma_basic():
    assert ta.sma([1, 2, 3, 4], 2) == 3.5
    assert ta.sma([1, 2, 3, 4], 4) == 2.5
    assert ta.sma([1, 2], 3) is None          # данных не хватает
    assert ta.sma([], 1) is None


def test_ema_constant_series():
    # EMA постоянного ряда равна самой константе
    assert ta.ema([5.0] * 30, 10) == 5.0


def test_ema_seeded_with_sma():
    # первое значение EMA — это SMA первых n точек, дальше сглаживание
    xs = [1, 2, 3, 4, 5, 6]
    # n=3: seed = mean(1,2,3)=2; затем по 4,5,6
    k = 2 / 4
    v = 2.0
    for x in (4, 5, 6):
        v = x * k + v * (1 - k)
    assert math.isclose(ta.ema(xs, 3), v)


def test_stdev():
    assert ta.stdev([2, 2, 2, 2], 4) == 0.0
    # популяционное СКО [0,2,4,6] = sqrt(5)
    assert math.isclose(ta.stdev([0, 2, 4, 6], 4), math.sqrt(5))


def test_rsi_extremes():
    assert ta.rsi(list(range(1, 30)), 14) == 100.0       # только рост
    assert ta.rsi(list(range(30, 1, -1)), 14) == 0.0     # только падение


def test_rsi_none_when_short():
    assert ta.rsi([1, 2, 3], 14) is None


def test_donchian_excludes_current_bar():
    # текущий бар (последний) НЕ должен попадать в канал — иначе пробой тривиален
    highs = [1, 2, 3, 4, 5, 10]
    lows = [1, 1, 1, 1, 1, 1]
    lower, upper = ta.donchian(highs, lows, 4)
    assert upper == 5            # max по [2,3,4,5], без текущего 10
    assert lower == 1


def test_bollinger_mid_is_sma():
    xs = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
    lower, mid, upper = ta.bollinger(xs, 5, 2.0)
    assert mid == ta.sma(xs, 5)
    assert lower < mid < upper


def test_atr_positive():
    highs = [10, 11, 12, 13, 14, 15, 16]
    lows = [9, 10, 11, 12, 13, 14, 15]
    closes = [9.5, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5]
    a = ta.atr(highs, lows, closes, 3)
    assert a is not None and a > 0


def test_crosses():
    assert ta.crossed_up(1, 2, 3, 2) is True
    assert ta.crossed_up(3, 2, 4, 5) is False
    assert ta.crossed_down(3, 2, 1, 2) is True


def test_hurst_basic():
    # синусоида (mean reversion) должна давать малый Hurst exponent (< 0.5)
    sine_series = [10.0 + math.sin(i * 0.5) for i in range(120)]
    h_sine = ta.hurst(sine_series, 100)
    assert h_sine is not None
    assert h_sine < 0.5

    # устойчивый тренд должен давать высокий Hurst exponent (> 0.5)
    trend_series = [10.0 + i * 0.5 for i in range(120)]
    h_trend = ta.hurst(trend_series, 100)
    assert h_trend is not None
    assert h_trend > 0.5


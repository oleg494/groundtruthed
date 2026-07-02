"""Тесты модуля сайзинга (R3)."""
import math

from backtest import sizing


def test_fixed_fractional():
    assert math.isclose(sizing.fixed_fractional(10000, 100, 1.0, 0.5), 50.0)
    assert sizing.fixed_fractional(10000, 0, 1.0) == 0.0


def test_atr_risk_qty_one_percent_rule():
    # риск 1% от 100k при стопе 2·ATR(=5) = 10 на единицу → 1000/10 = 100 единиц
    assert math.isclose(sizing.atr_risk_qty(100000, 5, 1.0, 0.01, 2.0), 100.0)
    assert sizing.atr_risk_qty(100000, 0, 1.0) == 0.0


def test_atr_risk_respects_multiplier():
    # множитель 10 → каждый пункт стоит в 10 раз больше → позиция в 10 раз меньше
    q1 = sizing.atr_risk_qty(100000, 5, 1.0, 0.01, 2.0)
    q10 = sizing.atr_risk_qty(100000, 5, 10.0, 0.01, 2.0)
    assert math.isclose(q1 / q10, 10.0)


def test_vol_target_frac_and_cap():
    assert math.isclose(sizing.vol_target_frac(0.30, 0.15, 1.0), 0.5)
    assert sizing.vol_target_frac(0.05, 0.15, 1.0) == 1.0     # упёрлись в потолок
    assert sizing.vol_target_frac(0.0) == 0.0


def test_kelly():
    assert math.isclose(sizing.kelly_fraction(0.6, 2.0, 1.0), 0.4)
    assert sizing.kelly_fraction(0.4, 1.0) == 0.0            # отриц. край → 0
    assert sizing.kelly_fraction(0.9, 5.0, 0.5) == 0.5      # обрезано потолком

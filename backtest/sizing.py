"""Сайзинг позиции: сколько единиц брать. Чистые функции, без состояния.

Размер позиции часто важнее самого сигнала: правильный риск на сделку определяет,
переживёшь ли серию убытков. Здесь — четыре классических подхода. Все возвращают
ЖЕЛАЕМОЕ число единиц (или долю капитала); округление к лоту — на стороне стратегии.
"""
from __future__ import annotations


def fixed_fractional(equity: float, price: float, multiplier: float = 1.0,
                     frac: float = 0.95) -> float:
    """Фиксированная доля капитала в нотионал. Возвращает число единиц."""
    if price <= 0 or multiplier <= 0:
        return 0.0
    return frac * equity / (price * multiplier)


def atr_risk_qty(equity: float, atr: float, multiplier: float = 1.0,
                 risk_frac: float = 0.01, stop_mult: float = 2.0) -> float:
    """Риск на сделку = risk_frac·equity, стоп на stop_mult·ATR.

    Размер такой, что при срабатывании стопа потеряем ровно risk_frac капитала:
    qty = risk_рубли / (stop_mult·ATR·multiplier). Это «правило 1%» из риск-менеджмента.
    """
    per_unit_risk = stop_mult * atr * multiplier
    if per_unit_risk <= 0:
        return 0.0
    return (risk_frac * equity) / per_unit_risk


def vol_target_frac(ann_vol: float, target_vol: float = 0.15,
                    max_leverage: float = 1.0) -> float:
    """Доля капитала для таргета волатильности: target_vol / реализованная_vol (с потолком)."""
    if ann_vol <= 0:
        return 0.0
    return min(target_vol / ann_vol, max_leverage)


def kelly_fraction(win_rate: float, win_loss_ratio: float, cap: float = 0.5) -> float:
    """Критерий Келли f* = W − (1−W)/R, обрезанный сверху cap.

    Полный Келли агрессивен и чувствителен к ошибкам оценки — на практике берут
    половину/четверть, поэтому дефолтный потолок 0.5. Отрицательное f* → 0 (не входить).
    """
    if win_loss_ratio <= 0:
        return 0.0
    f = win_rate - (1.0 - win_rate) / win_loss_ratio
    return max(0.0, min(f, cap))

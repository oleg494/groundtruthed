"""Классические учебные стратегии для демонстрации движка.

Это общеизвестные шаблоны (buy&hold, пересечение средних, пробой Дончиана, RSI,
Боллинджер, momentum по корзине) — не торговые рекомендации и не связаны с чьим-либо
портфелем. Любой «сигнал» обязан в бэктесте бить buy&hold и random — для этого они тут
как бенчмарки. Все стратегии детерминированы (кроме RandomTrader с явным сидом).
"""
from __future__ import annotations

import random
from typing import Optional

from . import indicators as ta
from . import sizing
from .engine import Context, Strategy


# ───────────────────────── бенчмарки ─────────────────────────
class BuyHold(Strategy):
    """Бенчмарк №1: на первом баре вложить frac капитала, держать до конца."""
    name = "buyhold"

    def __init__(self, frac: float = 0.99):
        self.frac = frac
        self._done = False

    def on_bar(self, ctx: Context) -> None:
        if self._done:
            return
        for t in ctx.tickers():
            if ctx.price(t):
                ctx.order_target_percent(t, self.frac / len(ctx.tickers()))
        self._done = True


class RandomTrader(Strategy):
    """Бенчмарк №2: монетка. Сид фиксирует прогон — воспроизводимо."""
    name = "random"

    def __init__(self, p: float = 0.05, seed: int = 0):
        self.p = p
        self.seed = seed
        self._rng = random.Random(seed)

    def on_bar(self, ctx: Context) -> None:
        for t in ctx.tickers():
            if self._rng.random() > self.p or not ctx.price(t):
                continue
            if self._rng.random() < 0.5:
                ctx.order_target_percent(t, 0.9 / len(ctx.tickers()))
            else:
                ctx.close(t)


# ───────────────────────── трендовые ─────────────────────────
class SMACross(Strategy):
    """Пересечение средних: fast>slow → лонг на frac, иначе в кэш (лонг-онли)."""
    name = "sma_cross"

    def __init__(self, fast: int = 20, slow: int = 60, frac: float = 0.95):
        assert fast < slow, "fast должен быть короче slow"
        self.fast, self.slow, self.frac = fast, slow, frac

    def on_bar(self, ctx: Context) -> None:
        t = ctx.tickers()[0]
        closes = ctx.closes(t)
        f, s = ta.sma(closes, self.fast), ta.sma(closes, self.slow)
        if f is None or s is None:
            return
        if f > s and ctx.position(t) == 0:
            ctx.order_target_percent(t, self.frac)
        elif f <= s and ctx.position(t) != 0:
            ctx.close(t)


class Donchian(Strategy):
    """Пробой канала Дончиана: close>верх(n) → лонг; close<низ(exit_n) → выход."""
    name = "donchian"

    def __init__(self, n: int = 20, exit_n: int = 10, frac: float = 0.95):
        self.n, self.exit_n, self.frac = n, exit_n, frac

    def on_bar(self, ctx: Context) -> None:
        t = ctx.tickers()[0]
        highs, lows, closes = ctx.highs(t), ctx.lows(t), ctx.closes(t)
        ch = ta.donchian(highs, lows, self.n)
        ex = ta.donchian(highs, lows, self.exit_n)
        if ch is None or ex is None:
            return
        price = closes[-1]
        if price > ch[1] and ctx.position(t) == 0:
            ctx.order_target_percent(t, self.frac)
        elif price < ex[0] and ctx.position(t) != 0:
            ctx.close(t)


class Momentum(Strategy):
    """Корзинный momentum: держать те тикеры, у кого доходность за lookback > 0.

    Раз в rebalance баров перекладывается равными весами в положительные по
    импульсу инструменты (лонг-онли)."""
    name = "momentum"

    def __init__(self, lookback: int = 90, rebalance: int = 20, top: int = 3):
        self.lookback, self.rebalance, self.top = lookback, rebalance, top

    def on_bar(self, ctx: Context) -> None:
        if ctx.i % self.rebalance != 0:
            return
        scored = []
        for t in ctx.tickers():
            closes = ctx.closes(t)
            if len(closes) <= self.lookback or not closes[-self.lookback - 1]:
                continue
            mom = closes[-1] / closes[-self.lookback - 1] - 1.0
            scored.append((mom, t))
        scored.sort(reverse=True)
        winners = [t for mom, t in scored[:self.top] if mom > 0]
        for t in ctx.tickers():
            if t in winners:
                ctx.order_target_percent(t, 0.95 / max(len(winners), 1))
            elif ctx.position(t) != 0:
                ctx.close(t)


# ───────────────────────── контртрендовые ─────────────────────────
class RSIReversion(Strategy):
    """RSI mean-reversion: RSI<low → лонг, RSI>high → выход (лонг-онли)."""
    name = "rsi_reversion"

    def __init__(self, n: int = 14, low: float = 30.0, high: float = 55.0,
                 frac: float = 0.95):
        self.n, self.low, self.high, self.frac = n, low, high, frac

    def on_bar(self, ctx: Context) -> None:
        t = ctx.tickers()[0]
        r = ta.rsi(ctx.closes(t), self.n)
        if r is None:
            return
        if r < self.low and ctx.position(t) == 0:
            ctx.order_target_percent(t, self.frac)
        elif r > self.high and ctx.position(t) != 0:
            ctx.close(t)


class Bollinger(Strategy):
    """Боллинджер-реверсия: close<нижней → лонг, close>средней → выход."""
    name = "bollinger"

    def __init__(self, n: int = 20, k: float = 2.0, frac: float = 0.95):
        self.n, self.k, self.frac = n, k, frac

    def on_bar(self, ctx: Context) -> None:
        t = ctx.tickers()[0]
        bb = ta.bollinger(ctx.closes(t), self.n, self.k)
        if bb is None:
            return
        lower, mid, _ = bb
        price = ctx.price(t)
        if price < lower and ctx.position(t) == 0:
            ctx.order_target_percent(t, self.frac)
        elif price > mid and ctx.position(t) != 0:
            ctx.close(t)


class MACDCross(Strategy):
    """MACD: гистограмма >0 (macd>signal) → лонг, <0 → выход (лонг-онли)."""
    name = "macd"

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9,
                 frac: float = 0.95):
        self.fast, self.slow, self.signal, self.frac = fast, slow, signal, frac

    def on_bar(self, ctx: Context) -> None:
        t = ctx.tickers()[0]
        m = ta.macd(ctx.closes(t), self.fast, self.slow, self.signal)
        if m is None:
            return
        hist = m[2]
        if hist > 0 and ctx.position(t) == 0:
            ctx.order_target_percent(t, self.frac)
        elif hist < 0 and ctx.position(t) != 0:
            ctx.close(t)


class Turtle(Strategy):
    """Черепахи: вход на пробое Дончиана(n), выход — обратный канал(exit_n) ИЛИ
    трейлинг-стоп от максимума за время сделки на atr_mult·ATR (лонг-онли)."""
    name = "turtle"

    def __init__(self, n: int = 20, exit_n: int = 10, atr_n: int = 14,
                 atr_mult: float = 2.0, frac: float = 0.95):
        self.n, self.exit_n = n, exit_n
        self.atr_n, self.atr_mult, self.frac = atr_n, atr_mult, frac
        self._peak = 0.0

    def on_bar(self, ctx: Context) -> None:
        t = ctx.tickers()[0]
        highs, lows, closes = ctx.highs(t), ctx.lows(t), ctx.closes(t)
        ch = ta.donchian(highs, lows, self.n)
        ex = ta.donchian(highs, lows, self.exit_n)
        a = ta.atr(highs, lows, closes, self.atr_n)
        if ch is None or ex is None or a is None:
            return
        price = closes[-1]
        pos = ctx.position(t)
        if pos == 0:
            if price > ch[1]:
                ctx.order_target_percent(t, self.frac)
                self._peak = price
        else:
            self._peak = max(self._peak, price)
            stop = self._peak - self.atr_mult * a
            if price < ex[0] or price < stop:
                ctx.close(t)


class VolTarget(Strategy):
    """Таргетирование волатильности: размер позиции ∝ target_vol / реализованная_vol,
    с потолком max_leverage. Всегда в рынке, лонг-онли (учебная)."""
    name = "voltarget"

    def __init__(self, lookback: int = 20, target_vol: float = 0.15,
                 max_leverage: float = 1.0, rebalance: int = 5):
        self.lookback, self.target_vol = lookback, target_vol
        self.max_leverage, self.rebalance = max_leverage, rebalance

    def on_bar(self, ctx: Context) -> None:
        if ctx.i % self.rebalance != 0:
            return
        t = ctx.tickers()[0]
        closes = ctx.closes(t)
        if len(closes) < self.lookback + 1:
            return
        rets = ta.returns(closes[-self.lookback - 1:])
        sd = ta.stdev(rets, len(rets)) if rets else None
        if not sd:
            return
        ann_vol = sd * (252 ** 0.5)
        frac = min(self.target_vol / ann_vol, self.max_leverage) if ann_vol else 0.0
        ctx.order_target_percent(t, max(0.0, frac))


class DualMomentum(Strategy):
    """Двойной импульс (Антоначчи): среди корзины берём top по относительному импульсу,
    но только если абсолютный импульс > 0; иначе — в кэш. Перекладка раз в rebalance."""
    name = "dualmom"

    def __init__(self, lookback: int = 120, rebalance: int = 20, top: int = 2):
        self.lookback, self.rebalance, self.top = lookback, rebalance, top

    def on_bar(self, ctx: Context) -> None:
        if ctx.i % self.rebalance != 0:
            return
        ranked = []
        for t in ctx.tickers():
            closes = ctx.closes(t)
            if len(closes) <= self.lookback or not closes[-self.lookback - 1]:
                continue
            mom = closes[-1] / closes[-self.lookback - 1] - 1.0
            ranked.append((mom, t))
        ranked.sort(reverse=True)
        winners = [t for mom, t in ranked[:self.top] if mom > 0]   # абс. фильтр
        for t in ctx.tickers():
            if t in winners:
                ctx.order_target_percent(t, 0.95 / len(winners))
            elif ctx.position(t) != 0:
                ctx.close(t)


class XSecMomentum(Strategy):
    """Кросс-секционный «12-1» моментум: ранжируем корзину по доходности за lookback
    баров, ПРОПУСКАЯ последние skip баров (классический скип месяца — отсекает
    краткосрочный реверс), держим top равными весами, ребаланс раз в rebalance баров.

    В отличие от dualmom — ЧИСТО относительный моментум: без абсолютного фильтра,
    всегда полностью в рынке (гипотеза — само ранжирование даёт эдж, а не тайминг)."""
    name = "xsec_momentum"

    def __init__(self, lookback: int = 231, skip: int = 21, rebalance: int = 21,
                 top: int = 3, frac: float = 0.95):
        assert skip < lookback, "skip должен быть короче lookback"
        self.lookback, self.skip = lookback, skip
        self.rebalance, self.top, self.frac = rebalance, top, frac

    def on_bar(self, ctx: Context) -> None:
        if ctx.i % self.rebalance != 0:
            return
        scored = []
        for t in ctx.tickers():
            closes = ctx.closes(t)
            # доходность окна [-lookback-1, -skip-1]: последние skip баров пропущены
            if len(closes) <= self.lookback or not closes[-self.lookback - 1]:
                continue
            recent = closes[-self.skip - 1] if self.skip else closes[-1]
            scored.append((recent / closes[-self.lookback - 1] - 1.0, t))
        if len(scored) < len(ctx.tickers()):       # ждём прогрева ВСЕЙ корзины
            return
        scored.sort(reverse=True)
        # список, НЕ set: порядок исполнения ордеров-победителей обязан быть
        # детерминирован (set строк хешируется по PYTHONHASHSEED — рандомизирован
        # между процессами) — иначе итоговая доходность плывёт от запуска к запуску
        # при отсутствии lookahead-бага (см. analysis/rerun_fixed_engine_result.md)
        winners = [t for _, t in scored[:self.top]]
        for t in ctx.tickers():                     # сначала продажи — освобождаем кэш
            if t not in winners and ctx.position(t) != 0:
                ctx.close(t)
        for t in winners:
            ctx.order_target_percent(t, self.frac / self.top)


class AbsMomentumSwitch(Strategy):
    """Абсолютный (time-series) моментум с уходом в кэш-прокси: если доходность
    тикера за lb_m месяцев (21 бар/мес) выше порога hurdle (годовой, масштабируется
    на окно) — держим его равновзвешенно, иначе выходим в кэш. Свободный кэш
    ежедневно капитализируется по ставке rate ≈ ключевая ЦБ (прокси фонда денежного
    рынка / LQDT). Решение раз в rebalance баров. Только дневные бары (начислятор
    считает бар = торговый день, 252 в году)."""
    name = "absmom_switch"

    def __init__(self, lb_m: int = 6, hurdle: float = 0.0, rebalance: int = 21,
                 rate: float = 14.25, frac: float = 0.95):
        assert lb_m >= 1
        self.lb_m, self.hurdle, self.rebalance = lb_m, hurdle, rebalance
        self.rate, self.frac = rate, frac

    def on_bar(self, ctx: Context) -> None:
        # начислятор кэша: пока стратегия «в кэше», деньги не мёртвые, а под ставкой.
        # У futures кэш не тратится на нотионал (маржинальная модель) — не начисляем
        # ставку на условно «занятые» под позицию деньги (нотионал открытых фьючей).
        blocked = sum(abs(ctx.instrument(t).notional(ctx.price(t) or 0.0, ctx.position(t)))
                      for t in ctx.tickers()
                      if ctx.instrument(t).is_futures and ctx.position(t))
        free = max(0.0, ctx.cash - blocked)
        ctx.adjust_cash(free * self.rate / 100.0 / 252.0, "cash_interest")
        if ctx.i % self.rebalance != 0:
            return
        lb = self.lb_m * 21
        n = len(ctx.tickers())
        for t in ctx.tickers():
            closes = ctx.closes(t)
            if len(closes) <= lb or not closes[-lb - 1]:
                continue
            mom = closes[-1] / closes[-lb - 1] - 1.0
            if mom > self.hurdle * self.lb_m / 12.0:   # порог годовой → на окно
                ctx.order_target_percent(t, self.frac / n)
            elif ctx.position(t) != 0:
                ctx.close(t)


class ATRBreakout(Strategy):
    """Пробой Дончиана с риск-сайзингом по ATR (правило 1%): размер позиции считается
    так, чтобы стоп на stop_mult·ATR стоил ровно risk_frac капитала. Выход — стоп или
    обратный канал. Демонстрирует sizing.atr_risk_qty (лонг-онли)."""
    name = "atr_breakout"

    def __init__(self, n: int = 20, exit_n: int = 10, atr_n: int = 14,
                 stop_mult: float = 2.0, risk_frac: float = 0.01):
        self.n, self.exit_n, self.atr_n = n, exit_n, atr_n
        self.stop_mult, self.risk_frac = stop_mult, risk_frac
        self._stop = 0.0

    def on_bar(self, ctx: Context) -> None:
        t = ctx.tickers()[0]
        highs, lows, closes = ctx.highs(t), ctx.lows(t), ctx.closes(t)
        ch = ta.donchian(highs, lows, self.n)
        ex = ta.donchian(highs, lows, self.exit_n)
        a = ta.atr(highs, lows, closes, self.atr_n)
        if ch is None or ex is None or a is None:
            return
        price = closes[-1]
        pos = ctx.position(t)
        if pos == 0 and price > ch[1]:
            raw = sizing.atr_risk_qty(ctx.equity, a, ctx.multiplier(t),
                                      self.risk_frac, self.stop_mult)
            lot = max(ctx.lot(t), 1)
            qty = int(raw / lot) * lot
            # потолок: не превышаем доступный капитал по нотионалу
            cap = int(ctx.equity / (price * ctx.multiplier(t) * lot)) * lot
            qty = min(qty, cap)
            if qty > 0:
                ctx.buy(t, qty)
                self._stop = price - self.stop_mult * a
        elif pos != 0 and (price < ex[0] or price < self._stop):
            ctx.close(t)


class SMATrail(Strategy):
    """Вход на price>SMA, выход — ТОЛЬКО трейлинг-стоп на atr_mult·ATR от пика.
    Демонстрирует opt-in стоп-логику Context (set_trailing/update_stops)."""
    name = "sma_trail"

    def __init__(self, n: int = 50, atr_n: int = 14, atr_mult: float = 3.0,
                 frac: float = 0.95):
        self.n, self.atr_n, self.atr_mult, self.frac = n, atr_n, atr_mult, frac

    def on_bar(self, ctx: Context) -> None:
        t = ctx.tickers()[0]
        if ctx.update_stops(t):        # стоп сработал → вышли, ждём нового сигнала
            return
        closes = ctx.closes(t)
        ma = ta.sma(closes, self.n)
        a = ta.atr(ctx.highs(t), ctx.lows(t), closes, self.atr_n)
        if ma is None or a is None:
            return
        if ctx.position(t) == 0 and ctx.price(t) > ma:
            ctx.order_target_percent(t, self.frac)
            ctx.set_trailing(t, self.atr_mult * a)


class RegimeDonchian(Strategy):
    """Пробой Дончиана с режимным фильтром: вход только когда ADX>adx_min И +DI>−DI
    (есть восходящий тренд). В боковике (низкий ADX) пробои игнорируются — фильтр
    отсекает пилу. Выход — обратный канал (лонг-онли).

    Опциональный Hurst-гейт (hurst_min>0): дополнительно требует H>=hurst_min по окну
    hurst_n (R/S на ценах, см. deep/market_regime_moex.md: H>0.55 + ADX>25 = тренд).
    hurst_min=0 → гейт выключен, поведение прежнее."""
    name = "regime_donchian"

    def __init__(self, n: int = 20, exit_n: int = 10, adx_n: int = 14,
                 adx_min: float = 25.0, frac: float = 0.95,
                 hurst_n: int = 100, hurst_min: float = 0.0):
        self.n, self.exit_n, self.adx_n, self.adx_min, self.frac = \
            n, exit_n, adx_n, adx_min, frac
        self.hurst_n, self.hurst_min = hurst_n, hurst_min

    def on_bar(self, ctx: Context) -> None:
        t = ctx.tickers()[0]
        highs, lows, closes = ctx.highs(t), ctx.lows(t), ctx.closes(t)
        ch = ta.donchian(highs, lows, self.n)
        ex = ta.donchian(highs, lows, self.exit_n)
        adx = ta.adx(highs, lows, closes, self.adx_n)
        if ch is None or ex is None or adx is None:
            return
        pdi, mdi, adx_val = adx
        price = closes[-1]
        if ctx.position(t) == 0:
            if price > ch[1] and adx_val > self.adx_min and pdi > mdi:
                if self.hurst_min > 0:                      # Hurst-гейт (opt-in)
                    h = ta.hurst(closes, self.hurst_n)
                    if h is None or h < self.hurst_min:
                        return
                ctx.order_target_percent(t, self.frac)
        elif price < ex[0]:
            ctx.close(t)


class PairsTrading(Strategy):
    """Парный трейдинг (стат-арбитраж двух тикеров). Торгуем z-score отношения цен A/B:
    z высоко (A дорог относительно B) → шорт A + лонг B; z низко → наоборот; |z|<exit → флэт.
    Требует ровно ≥2 тикера в данных. Использует шорты (qty<0 через target_percent<0)."""
    name = "pairs"

    def __init__(self, lookback: int = 30, entry_z: float = 2.0, exit_z: float = 0.5,
                 frac: float = 0.45):
        self.lookback, self.entry_z, self.exit_z, self.frac = lookback, entry_z, exit_z, frac

    def on_bar(self, ctx: Context) -> None:
        ts = ctx.tickers()
        if len(ts) < 2:
            return
        a, b = ts[0], ts[1]
        ca, cb = ctx.closes(a), ctx.closes(b)
        if len(ca) < self.lookback + 1 or len(cb) < self.lookback + 1:
            return
        ratios = [ca[i] / cb[i] for i in range(len(ca)) if cb[i]]
        z = ta.zscore(ratios, self.lookback)
        if z is None:
            return
        posA = ctx.position(a)
        if abs(z) < self.exit_z and posA != 0:
            ctx.close(a)
            ctx.close(b)
        elif z > self.entry_z and posA >= 0:
            ctx.order_target_percent(a, -self.frac)   # шорт A
            ctx.order_target_percent(b, self.frac)    # лонг B
        elif z < -self.entry_z and posA <= 0:
            ctx.order_target_percent(a, self.frac)    # лонг A
            ctx.order_target_percent(b, -self.frac)   # шорт B


class OpeningRangeBreakout(Strategy):
    """Пробой утреннего диапазона (интрадей) — порт логики daybot/run.py на бар-движок.

    Требует ВНУТРИДНЕВНЫЕ бары (30-мин/час). Диапазон = первые range_bars баров
    сессии; после — вход на пробое в обе стороны (выше → лонг, ниже → шорт), стоп за
    пробойной свечой с тем же риск-коридором, что у daybot (min_risk..risk_cap), тейк
    take_r·R. Ночных позиций нет: всё закрывается, когда МСК-час ≥ close_hour. Это
    проверка, есть ли у идеи эдж, ПРЕЖДЕ чем гонять её недели в песочнице.

    Отличие от daybot (делает бэктест строже, не мягче): вход исполняется по открытию
    СЛЕДУЮЩЕГО бара (движок без lookahead), а не мгновенно по рынку на тике.
    """
    name = "orb"

    def __init__(self, range_bars: int = 1, risk_cap: float = 0.005,
                 min_risk: float = 0.0015, take_r: float = 2.0,
                 close_hour: int = 23, max_entries: int = 3, frac: float = 0.95,
                 tz_offset: int = 3, adx_n: int = 14, adx_min: float = 0.0,
                 atr_n: int = 100, atr_mult_min: float = 0.0, atr_mult_max: float = 0.0,
                 trail_r: float = 0.0, reversal: bool = False,
                 hurst_n: int = 100, hurst_min: float = 0.0, hurst_max: float = 0.0):
        self.range_bars, self.risk_cap, self.min_risk = range_bars, risk_cap, min_risk
        self.take_r, self.close_hour, self.max_entries = take_r, close_hour, max_entries
        self.frac, self.tz_offset = frac, tz_offset
        self.adx_n, self.adx_min = adx_n, adx_min   # adx_min=0 → фильтр выключен
        self.atr_n, self.atr_mult_min, self.atr_mult_max = atr_n, atr_mult_min, atr_mult_max
        self.trail_r, self.reversal = trail_r, reversal
        self.hurst_n, self.hurst_min, self.hurst_max = hurst_n, hurst_min, hurst_max
        self._day = None
        self._hi = self._lo = 0.0
        self._nbars = self._entries = 0
        self._range_valid = True

    def _regime_ok(self, ctx: Context, t: str, long: bool) -> bool:
        """Режимный фильтр:
        - Для reversal=False (тренд): требует Hurst >= hurst_min и ADX >= adx_min.
        - Для reversal=True (флэт/возврат): требует Hurst <= hurst_max и ADX <= adx_min.
        """
        # 1. Проверяем индекс Херста
        if not self.reversal and self.hurst_min > 0:
            h_val = ta.hurst(ctx.closes(t), self.hurst_n)
            if h_val is None or h_val < self.hurst_min:
                return False
        elif self.reversal and self.hurst_max > 0:
            h_val = ta.hurst(ctx.closes(t), self.hurst_n)
            if h_val is None or h_val > self.hurst_max:
                return False

        # 2. Проверяем ADX
        if self.adx_min > 0:
            a = ta.adx(ctx.highs(t), ctx.lows(t), ctx.closes(t), self.adx_n)
            if a is None:
                return False
            pdi, mdi, adx_val = a
            if not self.reversal:  # Тренд: ADX >= adx_min и направление по DI
                if adx_val < self.adx_min:
                    return False
                return pdi > mdi if long else mdi > pdi
            else:  # Ложный пробой/Флэт: ADX <= adx_min
                if adx_val > self.adx_min:
                    return False
        return True


    def _check_range_valid(self, ctx: Context, t: str) -> None:
        """Проверить, что ширина утреннего диапазона лежит в разумных пределах относительно ATR."""
        self._range_valid = True
        if self.atr_mult_min > 0 or self.atr_mult_max > 0:
            atr_val = ta.atr(ctx.highs(t), ctx.lows(t), ctx.closes(t), self.atr_n)
            if atr_val is None:
                self._range_valid = False
            else:
                range_w = self._hi - self._lo
                if self.atr_mult_min > 0 and range_w < self.atr_mult_min * atr_val:
                    self._range_valid = False
                if self.atr_mult_max > 0 and range_w > self.atr_mult_max * atr_val:
                    self._range_valid = False

    def _day_of(self, t: int) -> int:
        return (t + self.tz_offset * 3600) // 86400

    def _hour(self, t: int) -> int:
        return ((t + self.tz_offset * 3600) // 3600) % 24

    def on_bar(self, ctx: Context) -> None:
        t = ctx.tickers()[0]
        bar = ctx.bar(t)
        if bar is None:
            return
        day, hour = self._day_of(bar.t), self._hour(bar.t)

        if day != self._day:                      # новая сессия
            if ctx.position(t) != 0:              # осиротевшая позиция — закрыть
                ctx.close(t)
                ctx.clear_stops(t)
            self._day, self._hi, self._lo = day, bar.h, bar.l
            self._nbars, self._entries = 1, 0
            self._range_valid = True
            if self.range_bars == 1:
                self._check_range_valid(ctx, t)
            return

        if hour >= self.close_hour:               # конец дня — flat, без ночных
            if ctx.position(t) != 0:
                ctx.close(t)
                ctx.clear_stops(t)
            return

        if ctx.position(t) != 0:                  # в позиции — ведём стоп/тейк
            ctx.update_stops(t)
            return

        if self._nbars < self.range_bars:         # ещё строим диапазон
            self._hi, self._lo = max(self._hi, bar.h), min(self._lo, bar.l)
            self._nbars += 1
            if self._nbars == self.range_bars:
                self._check_range_valid(ctx, t)
            return

        if not self._range_valid:
            return

        if self._entries >= self.max_entries:
            return
        price = bar.c
        if price > self._hi and self._regime_ok(ctx, t, long=not self.reversal):   # пробой вверх
            if not self.reversal:   # классический пробой → лонг
                stop = max(bar.l, price * (1 - self.risk_cap))
                stop = min(stop, price * (1 - self.min_risk))
                if ctx.order_target_percent(t, self.frac):
                    ctx.set_stop(t, stop)
                    ctx.set_take(t, price + self.take_r * (price - stop))
                    if self.trail_r > 0:
                        ctx.set_trailing(t, self.trail_r * (price - stop))
                    self._entries += 1
            else:                   # ложный пробой → вход в шорт
                stop = min(bar.h, price * (1 + self.risk_cap))
                stop = max(stop, price * (1 + self.min_risk))
                if ctx.order_target_percent(t, -self.frac):
                    ctx.set_stop(t, stop)
                    ctx.set_take(t, price - self.take_r * (stop - price))
                    if self.trail_r > 0:
                        ctx.set_trailing(t, self.trail_r * (stop - price))
                    self._entries += 1
        elif price < self._lo and self._regime_ok(ctx, t, long=self.reversal):  # пробой вниз
            if not self.reversal:   # классический пробой → шорт
                stop = min(bar.h, price * (1 + self.risk_cap))
                stop = max(stop, price * (1 + self.min_risk))
                if ctx.order_target_percent(t, -self.frac):
                    ctx.set_stop(t, stop)
                    ctx.set_take(t, price - self.take_r * (stop - price))
                    if self.trail_r > 0:
                        ctx.set_trailing(t, self.trail_r * (stop - price))
                    self._entries += 1
            else:                   # ложный пробой → вход в лонг
                stop = max(bar.l, price * (1 - self.risk_cap))
                stop = min(stop, price * (1 - self.min_risk))
                if ctx.order_target_percent(t, self.frac):
                    ctx.set_stop(t, stop)
                    ctx.set_take(t, price + self.take_r * (price - stop))
                    if self.trail_r > 0:
                        ctx.set_trailing(t, self.trail_r * (price - stop))
                    self._entries += 1


class IntradayTrend(Strategy):
    """Внутридневной тренд после часа открытия (зеркальная гипотеза к orb_reversal).

    Раз ложные пробои утром — норма, то направление, ПОДТВЕРДИВШЕЕСЯ к полудню
    (цена выше/ниже сессионного VWAP и цены открытия дня, ADX по 30-мин барам не
    ниже порога с согласным DI), с большей вероятностью доживает до вечера.
    Требует ВНУТРИДНЕВНЫЕ бары (30-мин).

    Вход: первый бар с MSK-временем в окне [confirm_hour, confirm_hour+entry_window) —
    лонг если close > VWAP·(1+vwap_band) и close > открытия дня, шорт зеркально.
    Стоп: stop_mult·ATR(atr_n). Тейк take_r·R (take_r=0 — без тейка, держим до
    вечера). Ночных позиций нет: всё закрывается при MSK-часе >= close_hour.
    Одна попытка в день. Вход исполняется по открытию СЛЕДУЮЩЕГО бара (движок
    без lookahead) — строже живой торговли.
    """
    name = "intraday_trend"

    def __init__(self, confirm_hour: float = 12.0, entry_window: float = 1.0,
                 adx_n: int = 14, adx_min: float = 0.0,
                 stop_mult: float = 1.5, atr_n: int = 28, take_r: float = 0.0,
                 close_hour: float = 22.5, frac: float = 0.95,
                 vwap_band: float = 0.0, tz_offset: int = 3):
        self.confirm_hour, self.entry_window = confirm_hour, entry_window
        self.adx_n, self.adx_min = adx_n, adx_min
        self.stop_mult, self.atr_n, self.take_r = stop_mult, atr_n, take_r
        self.close_hour, self.frac = close_hour, frac
        self.vwap_band, self.tz_offset = vwap_band, tz_offset
        self._day = None
        self._open = 0.0            # цена открытия сессии
        self._pv = self._vv = 0.0   # аккумуляторы VWAP: Σ(typical·vol), Σvol
        self._csum = self._cn = 0.0  # запасной VWAP без объёма: Σclose, N
        self._entered = False

    def _day_of(self, t: int) -> int:
        return (t + self.tz_offset * 3600) // 86400

    def _hourf(self, t: int) -> float:
        return ((t + self.tz_offset * 3600) % 86400) / 3600.0

    def _vwap(self) -> Optional[float]:
        if self._vv > 0:
            return self._pv / self._vv
        return self._csum / self._cn if self._cn else None

    def on_bar(self, ctx: Context) -> None:
        t = ctx.tickers()[0]
        bar = ctx.bar(t)
        if bar is None:
            return
        day, hourf = self._day_of(bar.t), self._hourf(bar.t)

        if day != self._day:                      # новая сессия
            if ctx.position(t) != 0:              # осиротевшая позиция — закрыть
                ctx.close(t)
                ctx.clear_stops(t)
            self._day, self._open = day, bar.o
            self._pv = self._vv = self._csum = self._cn = 0.0
            self._entered = False

        tp = (bar.h + bar.l + bar.c) / 3.0        # копим сессионный VWAP
        self._pv += tp * bar.v
        self._vv += bar.v
        self._csum += bar.c
        self._cn += 1

        if hourf >= self.close_hour:              # конец дня — flat, без ночных
            if ctx.position(t) != 0:
                ctx.close(t)
                ctx.clear_stops(t)
            return

        if ctx.position(t) != 0:                  # в позиции — ведём стоп/тейк
            ctx.update_stops(t)
            return

        if self._entered or not (self.confirm_hour <= hourf
                                 < self.confirm_hour + self.entry_window):
            return

        vwap = self._vwap()
        if vwap is None or not self._open:
            return
        price = bar.c
        long = price > vwap * (1 + self.vwap_band) and price > self._open
        short = price < vwap * (1 - self.vwap_band) and price < self._open
        if not (long or short):
            return

        if self.adx_min > 0:                      # трендовый фильтр по ADX/DI
            a = ta.adx(ctx.highs(t), ctx.lows(t), ctx.closes(t), self.adx_n)
            if a is None:
                return
            pdi, mdi, adx_val = a
            if adx_val < self.adx_min or (pdi <= mdi if long else mdi <= pdi):
                return

        atr_val = ta.atr(ctx.highs(t), ctx.lows(t), ctx.closes(t), self.atr_n)
        if atr_val is None or atr_val <= 0:
            return
        self._entered = True                      # одна попытка в день
        risk = self.stop_mult * atr_val
        if long and ctx.order_target_percent(t, self.frac):
            ctx.set_stop(t, price - risk)
            if self.take_r > 0:
                ctx.set_take(t, price + self.take_r * risk)
        elif short and ctx.order_target_percent(t, -self.frac):
            ctx.set_stop(t, price + risk)
            if self.take_r > 0:
                ctx.set_take(t, price - self.take_r * risk)


from .portfolio import RebalancePortfolio  # noqa: E402  (избегаем цикла на уровне модуля)

REGISTRY: dict[str, type[Strategy]] = {
    c.name: c for c in (BuyHold, RandomTrader, SMACross, Donchian,
                        Momentum, RSIReversion, Bollinger,
                        MACDCross, Turtle, VolTarget, DualMomentum, XSecMomentum,
                        AbsMomentumSwitch, ATRBreakout,
                        SMATrail, RebalancePortfolio, PairsTrading, RegimeDonchian,
                        OpeningRangeBreakout, IntradayTrend)
}


def build(name: str, **params) -> Strategy:
    """Создать стратегию по имени из реестра с параметрами."""
    if name not in REGISTRY:
        raise KeyError(f"неизвестная стратегия {name!r}; есть: {list(REGISTRY)}")
    return REGISTRY[name](**params)


class PairsZScoreBasket(Strategy):
    """Мульти-парный стат-арбитраж на корзине акций. Пары НЕ выбирает сама —
    получает их строкой `pairs` ("LKOH-ROSN,SBER-GAZP"); отбор пар делается снаружи
    и СТРОГО на данных до OOS-окна (analysis/botdev_pairs_zscore_wfa.py), иначе
    классический lookahead парного арбитража.

    Спред = log(A/B) (бета=1). z-score спреда за lookback:
    z > entry_z → A дорог: шорт A + лонг B (leg-доля на ногу); z < -entry_z → наоборот;
    |z| < exit_z → флэт; |z| > stop_z → стоп (пара разъехалась, коинтеграция сломалась).
    Действия только при смене состояния — без перекладки каждый бар."""
    name = "pairs_z"

    def __init__(self, pairs: str = "", lookback: int = 40, entry_z: float = 2.0,
                 exit_z: float = 0.5, stop_z: float = 4.0, frac: float = 0.9,
                 warmup: int = 0):
        assert exit_z < entry_z < stop_z, "нужно exit_z < entry_z < stop_z"
        self.pairs = [tuple(p.split("-")) for p in str(pairs).split(",") if "-" in p]
        self.lookback, self.entry_z, self.exit_z = int(lookback), entry_z, exit_z
        self.stop_z, self.frac, self.warmup = stop_z, frac, int(warmup)
        self._state: dict[tuple, int] = {}   # пара -> знак позиции по A (-1/0/+1)

    def _z(self, ctx: Context, a: str, b: str):
        import math
        ca, cb = ctx.closes(a, self.lookback), ctx.closes(b, self.lookback)
        if len(ca) < self.lookback or len(cb) < self.lookback:
            return None
        s = [math.log(x / y) for x, y in zip(ca, cb) if x > 0 and y > 0]
        return ta.zscore(s, self.lookback) if len(s) == self.lookback else None

    def on_bar(self, ctx: Context) -> None:
        # warmup: первые warmup баров не торгуем — прогрев истории хвостом IS
        # перед OOS-окном без сделок в IS-части (см. analysis/botdev_pairs_zscore_wfa.py)
        if not self.pairs or ctx.i < self.warmup:
            return
        leg = self.frac / (2 * len(self.pairs))          # доля equity на одну ногу
        for pair in self.pairs:
            a, b = pair
            z = self._z(ctx, a, b)
            if z is None:
                continue
            st = self._state.get(pair, 0)
            if st == 0 and self.entry_z < abs(z) < self.stop_z:
                sign = -1 if z > 0 else 1                # z>0 → A дорог → шорт A
                ctx.order_target_percent(a, sign * leg)
                ctx.order_target_percent(b, -sign * leg)
                self._state[pair] = sign
            elif st != 0 and (abs(z) < self.exit_z or abs(z) > self.stop_z):
                ctx.close(a)                              # возврат к среднему или разъезд
                ctx.close(b)
                self._state[pair] = 0


REGISTRY[PairsZScoreBasket.name] = PairsZScoreBasket


class MeanRevHurst(Strategy):
    """Боллинджер-реверсия с режимным Hurst-гейтом (кандидат MEANREV_HURST).

    Гипотеза (deep/market_regime_moex.md): возврат к среднему работает только в
    антиперсистентном режиме (Hurst < ~0.45). Обычный meanrev (Boll 20/2) без
    фильтра в ферме показал Deflated Sharpe = 0% — здесь вход разрешён лишь когда
    R/S-Hurst по окну hurst_n дневных баров ниже hurst_max. Выход на средней полосе
    БЕЗ гейта: фильтруем только входы. Работает по корзине: равный вес
    frac/число тикеров, лонг-онли. hurst_max>=1 → гейт выключен (чистый meanrev)."""
    name = "meanrev_hurst"

    def __init__(self, bb_n: int = 20, bb_k: float = 2.0, hurst_n: int = 100,
                 hurst_max: float = 0.45, frac: float = 0.95):
        self.bb_n, self.bb_k = bb_n, bb_k
        self.hurst_n, self.hurst_max = hurst_n, hurst_max
        self.frac = frac

    def on_bar(self, ctx: Context) -> None:
        ts = ctx.tickers()
        for t in ts:
            closes = ctx.closes(t)
            bb = ta.bollinger(closes, self.bb_n, self.bb_k)
            if bb is None or not ctx.price(t):
                continue
            lower, mid, _ = bb
            price = ctx.price(t)
            if ctx.position(t) == 0:
                if price >= lower:
                    continue
                if self.hurst_max < 1.0:                   # гейт только на вход
                    h = ta.hurst(closes, self.hurst_n)
                    if h is None or h > self.hurst_max:
                        continue
                ctx.order_target_percent(t, self.frac / len(ts))
            elif price > mid:
                ctx.close(t)


REGISTRY[MeanRevHurst.name] = MeanRevHurst


class DailyBreakoutFut(Strategy):
    """Пробой ДНЕВНОГО канала Дончиана, исполняемый внутри дня на 30-мин барах
    (кандидат DAILY_BREAKOUT_FUT, вторая волна bot-dev).

    Отличие от убитых ORB-режимов: диапазон не утренний, а N ПОЛНЫХ предыдущих
    дней (позиционный пробой, не интрадей). Требует ВНУТРИДНЕВНЫЕ бары (30-мин).
    Вход: close 30-мин бара выше максимума N завершённых дней → лонг; ниже
    минимума → шорт (фьючерсы). Исполнение по open следующего бара (движок без
    lookahead). Позиция живёт днями — на ночь НЕ закрывается.
    Выход: обратный канал M<N завершённых дней и/или трейлинг-стоп на
    trail_mult·ATR(atr_n) по ДНЕВНЫМ барам (trail_mult=0 → только канал).
    Граница дня — МСК (tz_offset часов к UTC)."""
    name = "daily_breakout"

    def __init__(self, n: int = 20, exit_m: int = 10, atr_n: int = 14,
                 trail_mult: float = 0.0, frac: float = 0.95, tz_offset: int = 3):
        assert exit_m < n, "exit_m должен быть короче n"
        self.n, self.exit_m, self.atr_n = n, exit_m, atr_n
        self.trail_mult, self.frac, self.tz_offset = trail_mult, frac, tz_offset
        self._day = None
        self._hi = self._lo = self._close = 0.0
        self._days: list[tuple[float, float, float]] = []  # (hi, lo, close) завершённых дней

    def _daily_atr(self) -> Optional[float]:
        """ATR по агрегатам завершённых дней (true range с учётом гэпов)."""
        ds = self._days
        if len(ds) < self.atr_n + 1:
            return None
        trs = []
        for i in range(len(ds) - self.atr_n, len(ds)):
            hi, lo, _ = ds[i]
            pc = ds[i - 1][2]
            trs.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
        return sum(trs) / len(trs)

    def on_bar(self, ctx: Context) -> None:
        t = ctx.tickers()[0]
        bar = ctx.bar(t)
        if bar is None:
            return
        day = (bar.t + self.tz_offset * 3600) // 86400
        if day != self._day:                      # новый день: вчерашний — в историю
            if self._day is not None:
                self._days.append((self._hi, self._lo, self._close))
            self._day, self._hi, self._lo = day, bar.h, bar.l
        else:
            self._hi, self._lo = max(self._hi, bar.h), min(self._lo, bar.l)
        self._close = bar.c

        if len(self._days) < self.n:              # прогрев: нужен полный канал
            return
        pos = ctx.position(t)
        price = bar.c
        if pos != 0:                              # в позиции: трейлинг + обратный канал
            if ctx.update_stops(t):
                return
            ex = self._days[-self.exit_m:]
            if pos > 0 and price < min(lo for _, lo, _ in ex):
                ctx.close(t)
                ctx.clear_stops(t)
            elif pos < 0 and price > max(hi for hi, _, _ in ex):
                ctx.close(t)
                ctx.clear_stops(t)
            return
        ch_hi = max(hi for hi, _, _ in self._days[-self.n:])
        ch_lo = min(lo for _, lo, _ in self._days[-self.n:])
        if price > ch_hi:                         # пробой N-дневного максимума → лонг
            if ctx.order_target_percent(t, self.frac) and self.trail_mult > 0:
                a = self._daily_atr()
                if a:
                    ctx.set_trailing(t, self.trail_mult * a)
        elif price < ch_lo:                       # пробой N-дневного минимума → шорт
            if ctx.order_target_percent(t, -self.frac) and self.trail_mult > 0:
                a = self._daily_atr()
                if a:
                    ctx.set_trailing(t, self.trail_mult * a)


REGISTRY[DailyBreakoutFut.name] = DailyBreakoutFut


class TrendLSStocks(Strategy):
    """Лонг-шорт тренд Дончиана по корзине акций (кандидат TREND_LS_STOCKS).

    Гипотеза — инверсия диагноза MEANREV_HURST: медианный R/S-Hurst акций MOEX
    пост-2022 = 0.56–0.64 (рынок ПЕРСИСТЕНТЕН), значит торговать надо тренд, а не
    возврат. Первая волна гоняла тренд только на золоте лонг-онли — здесь корзина
    акций и ОБЕ стороны: пробой верха канала n → лонг, пробой низа → шорт
    (у акций тренды двусторонние). Выход — обратный канал exit_n; опциональный
    трейлинг stop_mult·ATR (stop_mult=0 → выключен). Равный вес frac/N на тикер.

    Шорт на MOEX платный: брокер берёт ~КС+2% годовых на нотионал шортовой ноги.
    Моделируем ежебарным костом borrow/252 на текущий шорт-нотионал (borrow —
    годовой %, при КС 14.25 → 16.25; borrow=0 — абляция «бесплатный шорт»;
    short=False — абляция лонг-онли). Только дневные бары (кост считает
    бар = торговый день, 252 в году)."""
    name = "trend_ls_stocks"

    def __init__(self, n: int = 40, exit_n: int = 20, stop_mult: float = 0.0,
                 atr_n: int = 14, borrow: float = 16.25, frac: float = 0.95,
                 short: bool = True):
        self.n, self.exit_n = n, exit_n
        self.stop_mult, self.atr_n = stop_mult, atr_n
        self.borrow, self.frac, self.short = borrow, frac, short

    def on_bar(self, ctx: Context) -> None:
        ts = ctx.tickers()
        # ставка за шорт: ежебарно списываем borrow/252 от нотионала шортовой ноги
        # (акции; фьючерсам кост не начисляем — у них шорт без займа бумаг)
        if self.borrow > 0:
            short_notional = sum(
                ctx.instrument(t).notional(ctx.price(t) or 0.0, -ctx.position(t))
                for t in ts
                if ctx.position(t) < 0 and not ctx.instrument(t).is_futures)
            if short_notional > 0:
                ctx.adjust_cash(-short_notional * self.borrow / 100.0 / 252.0, "short_borrow")
        w = self.frac / len(ts)
        for t in ts:
            if ctx.position(t) != 0 and ctx.update_stops(t):
                continue                          # трейлинг сработал — вышли
            highs, lows, closes = ctx.highs(t), ctx.lows(t), ctx.closes(t)
            ch = ta.donchian(highs, lows, self.n)
            ex = ta.donchian(highs, lows, self.exit_n)
            if ch is None or ex is None or not ctx.price(t):
                continue
            price = closes[-1]
            pos = ctx.position(t)
            if pos == 0:
                long = price > ch[1]
                shrt = self.short and price < ch[0]
                if not (long or shrt):
                    continue
                if (ctx.order_target_percent(t, w if long else -w)
                        and self.stop_mult > 0):
                    a = ta.atr(highs, lows, closes, self.atr_n)
                    if a:
                        ctx.set_trailing(t, self.stop_mult * a)
            elif pos > 0 and price < ex[0]:       # лонг: пробой низа exit-канала
                ctx.close(t)
                ctx.clear_stops(t)
            elif pos < 0 and price > ex[1]:       # шорт: пробой верха exit-канала
                ctx.close(t)
                ctx.clear_stops(t)


REGISTRY[TrendLSStocks.name] = TrendLSStocks


class VolRegimeSwitch(Strategy):
    """Vol-таргетирование на равновзвешенной корзине (кандидат VOL_REGIME_SWITCH).

    Инверсия диагноза ABSMOM_SWITCH: там переключение по ЗНАКУ доходности + кэш-прокси
    маскировали отсутствие эджа. Здесь переключение по ВОЛАТИЛЬНОСТИ (режиму), без
    прогноза направления и БЕЗ начисления ставки на кэш: экспозиция корзины =
    min(max_leverage, target_vol / realized_vol(lookback)), где realized_vol —
    годовая вола РАВНОВЗВЕШЕННОГО портфельного ретёрна (не средняя по бумагам:
    портфельная вола учитывает корреляции). Классика Moreira & Muir (2017):
    vol-managed portfolio улучшает Sharpe владения без прогноза направления.

    Свободный кэш при сниженной экспозиции лежит МЁРТВЫМ (0%) — сознательно
    консервативно, чтобы не повторить кэш-прокси-артефакт absmom. Ребаланс раз в
    rebalance баров; band — мёртвая зона по экспозиции (перекладка только если
    |новая − текущая| >= band), душит комиссионный чёрн. Только дневные бары
    (нормировка 252). Лонг-онли; max_leverage=1.0 → только де-левередж buyhold'а."""
    name = "vol_regime_switch"

    def __init__(self, lookback: int = 20, target_vol: float = 0.15,
                 max_leverage: float = 1.0, rebalance: int = 5,
                 band: float = 0.05, frac: float = 0.99):
        assert lookback >= 2
        self.lookback, self.target_vol = int(lookback), target_vol
        self.max_leverage, self.rebalance = max_leverage, int(rebalance)
        self.band, self.frac = band, frac
        self._expo = -1.0            # текущая целевая экспозиция (<0 — ещё не входили)

    def on_bar(self, ctx: Context) -> None:
        if ctx.i % self.rebalance != 0:
            return
        ts = ctx.tickers()
        cols = []
        for t in ts:
            closes = ctx.closes(t, self.lookback + 1)
            if len(closes) < self.lookback + 1 or not ctx.price(t):
                return                              # ждём прогрева ВСЕЙ корзины
            cols.append(ta.returns(closes))
        port = [sum(col) / len(col) for col in zip(*cols)]   # равновзвешенный ретёрн
        sd = ta.stdev(port, len(port)) if port else None
        if sd is None:
            return
        ann_vol = sd * (252 ** 0.5)
        expo = min(self.max_leverage, self.target_vol / ann_vol) if ann_vol > 0 \
            else self.max_leverage
        if self._expo >= 0 and abs(expo - self._expo) < self.band:
            return                                  # мёртвая зона — не дёргаемся
        self._expo = expo
        for t in ts:
            ctx.order_target_percent(t, self.frac * expo / len(ts))


REGISTRY[VolRegimeSwitch.name] = VolRegimeSwitch


class OvernightHold(Strategy):
    """Премия за ночное владение (кандидат OVERNIGHT_PREMIUM, вторая волна).

    Гипотеза (мировая литература: значимая часть equity-премии реализуется
    overnight, close→open; интрадей-часть слаба или отрицательна): держать бумагу
    ТОЛЬКО через ночь, днём — в кэше. На MOEX никем публично не проверялась.
    Требует ЧАСОВЫЕ бары. Знак премии выясняется измерением (см.
    analysis/botdev2_overnight_premium.py) — отсюда два режима.

    Движок исполняет заявки по open СЛЕДУЮЩЕГО бара, поэтому «купить на закрытии
    вечёрки» реализуется так:
      mode='overnight': сигнал на баре с MSK-часом entry_hour → филл по open
        следующего часового бара (entry_hour=22 → вход по open бара 23:00 МСК,
        последнего вечернего); выход — сигнал на exit_bar-м баре нового дня →
        филл по open следующего (exit_bar=1 → выход по open бара ~11:00).
        Захват: open(последнего вечернего часа) → open(второго утреннего) —
        ночной гэп плюс два граничных часа.
      mode='intraday' (зеркало, если премия дневная): вход — сигнал на первом
        баре дня (филл по open второго, ~11:00), выход — сигнал на баре с часом
        entry_hour (филл по open следующего, ~23:00). Ночных позиций нет.

    Если бара entry_hour в дне нет (короткая сессия) — входа в этот день нет;
    заявка, поданная на последнем баре дня, честно исполнится утренним open
    (гэп уже упущен — так и в живой торговле). Одна попытка входа в день.
    Лонг-онли. Граница дня — МСК (tz_offset часов к UTC)."""
    name = "overnight"

    def __init__(self, mode: str = "overnight", entry_hour: int = 22,
                 exit_bar: int = 1, frac: float = 0.95, tz_offset: int = 3):
        assert mode in ("overnight", "intraday"), "mode: overnight|intraday"
        assert exit_bar >= 1
        self.mode, self.entry_hour, self.exit_bar = mode, int(entry_hour), int(exit_bar)
        self.frac, self.tz_offset = frac, tz_offset
        self._day = None            # текущий MSK-день ленты
        self._bars_today = 0        # номер бара внутри дня (1 = первый)
        self._sig_day = None        # день, в котором вход уже подавался
        self._entry_day = None      # день подачи входа (для границы ночи)

    def _msk(self, t: int) -> tuple[int, int]:
        s = t + self.tz_offset * 3600
        return s // 86400, (s // 3600) % 24

    def on_bar(self, ctx: Context) -> None:
        t = ctx.tickers()[0]
        bar = ctx.bar(t)
        if bar is None:
            return
        day, hour = self._msk(bar.t)
        if day != self._day:
            self._day, self._bars_today = day, 0
        self._bars_today += 1
        pos = ctx.position(t)

        if self.mode == "overnight":
            if pos == 0:
                if hour == self.entry_hour and self._sig_day != day:
                    if ctx.order_target_percent(t, self.frac):
                        self._sig_day = self._entry_day = day
            elif day != self._entry_day and self._bars_today >= self.exit_bar:
                ctx.close(t)                       # филл по open следующего бара
        else:                                      # mode='intraday'
            if pos == 0:
                if (self._bars_today == 1 and hour < self.entry_hour
                        and self._sig_day != day):
                    if ctx.order_target_percent(t, self.frac):
                        self._sig_day = self._entry_day = day
            elif hour >= self.entry_hour or day != self._entry_day:
                ctx.close(t)                       # вечером или осиротевшая — flat


REGISTRY[OvernightHold.name] = OvernightHold


class CalendarCB(Strategy):
    """Календарный эффект заседаний ЦБ РФ (кандидат CALENDAR_CB, вторая волна).

    Гипотеза (измерение — analysis/botdev2_calendar_cb_measure.py): вокруг плановых
    решений по ключевой ставке (8/год, пятницы, календарь публикуется на год вперёд)
    есть паттерн волатильности (сжатие до, расширение после) и, возможно,
    направленный дрейф. Стратегия торгует ТОЛЬКО событийные окна: сигнал на
    закрытии дня entry_k (в торговых днях от дня решения; <0 — до заседания,
    0 — день решения), исполнение движком — open следующего бара; выход через
    hold баров (тоже по open следующего). Вне окон — в кэше.

    dates — ISO-даты заседаний через запятую (публичный календарь ЦБ, известен
    ex ante — lookahead'а нет). entry_k переводится в календарные дни в
    предположении пятничных заседаний (все плановые — пятницы); праздничный сдвиг
    покрывает допуск tol: вход на первом баре с датой >= целевой, но не позже
    целевой + tol дней. direction: +1 лонг / -1 шорт. Дневные бары."""
    name = "calendar_cb"

    def __init__(self, dates: str = "", entry_k: int = 0, hold: int = 2,
                 direction: int = 1, frac: float = 0.95, tol: int = 3,
                 tz_offset: int = 3):
        import datetime as dt
        assert hold >= 1 and int(direction) in (1, -1)
        self.entry_k, self.hold = int(entry_k), int(hold)
        self.direction, self.frac = int(direction), frac
        self.tol, self.tz_offset = int(tol), int(tz_offset)
        k = self.entry_k                    # торговые дни → календарные (пятница)
        cal = k + 2 * ((k + 4) // 5) if k > 0 else k - 2 * (abs(k) // 5)
        ms = [dt.date.fromisoformat(s.strip()) for s in str(dates).split(",")
              if s.strip()]
        self._targets = sorted(m + dt.timedelta(days=cal) for m in ms)
        self._ti = 0            # указатель на следующую необработанную целевую дату
        self._held: Optional[int] = None    # None=flat, иначе баров с сигнала входа

    def _date(self, t: int):
        import datetime as dt
        return (dt.datetime.utcfromtimestamp(t) +
                dt.timedelta(hours=self.tz_offset)).date()

    def on_bar(self, ctx: Context) -> None:
        t = ctx.tickers()[0]
        bar = ctx.bar(t)
        if bar is None:
            return
        if self._held is not None:          # событийное окно — ведём счётчик баров
            self._held += 1
            if self._held >= self.hold:
                ctx.close(t)
                self._held = None
            return
        d = self._date(bar.t)
        # пропустить целевые даты, ушедшие дальше допуска (длинные праздники)
        while self._ti < len(self._targets) and \
                (d - self._targets[self._ti]).days > self.tol:
            self._ti += 1
        if self._ti < len(self._targets) and self._targets[self._ti] <= d:
            self._ti += 1
            if ctx.order_target_percent(t, self.direction * self.frac):
                self._held = 0


REGISTRY[CalendarCB.name] = CalendarCB

"""Технические индикаторы — чистые функции на списках float, без numpy.

Соглашение: на вход подаётся последовательность цен по возрастанию времени
(старые слева, свежие справа). Функции возвращают одно «текущее» значение по
последним точкам — ровно то, что нужно стратегии на закрытии бара. Если данных
не хватает, возвращается None (стратегия трактует как «сигнала нет»).

Никакого заглядывания вперёд: всё считается только по переданному окну, а движок
подаёт сюда историю строго до текущего бара включительно.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence


def sma(xs: Sequence[float], n: int) -> Optional[float]:
    """Простое скользящее среднее по последним n точкам."""
    if n <= 0 or len(xs) < n:
        return None
    return sum(xs[-n:]) / n


def ema(xs: Sequence[float], n: int) -> Optional[float]:
    """Экспоненциальное скользящее. Сидируется SMA первых n точек (как в TA-Lib)."""
    if n <= 0 or len(xs) < n:
        return None
    k = 2.0 / (n + 1)
    val = sum(xs[:n]) / n
    for x in xs[n:]:
        val = x * k + val * (1 - k)
    return val


def stdev(xs: Sequence[float], n: int, sample: bool = False) -> Optional[float]:
    """Стандартное отклонение последних n точек. sample=True → делитель n−1."""
    if n <= 0 or len(xs) < n:
        return None
    w = xs[-n:]
    m = sum(w) / n
    div = (n - 1) if sample else n
    if div <= 0:
        return 0.0
    return math.sqrt(sum((x - m) ** 2 for x in w) / div)


def zscore(xs: Sequence[float], n: int) -> Optional[float]:
    """Z-оценка последней точки относительно окна n."""
    if len(xs) < n:
        return None
    m = sma(xs, n)
    sd = stdev(xs, n)
    if sd is None or sd == 0:
        return None
    return (xs[-1] - m) / sd


def rsi(xs: Sequence[float], n: int = 14) -> Optional[float]:
    """RSI Уайлдера по n периодам. 0..100. None, если точек < n+1."""
    if len(xs) < n + 1:
        return None
    gains, losses = 0.0, 0.0
    # начальное среднее по первым n изменениям
    for i in range(1, n + 1):
        d = xs[i] - xs[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / n, losses / n
    # сглаживание Уайлдера по остатку
    for i in range(n + 1, len(xs)):
        d = xs[i] - xs[i - 1]
        avg_g = (avg_g * (n - 1) + max(d, 0.0)) / n
        avg_l = (avg_l * (n - 1) + max(-d, 0.0)) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - 100.0 / (1.0 + rs)


def bollinger(xs: Sequence[float], n: int = 20, k: float = 2.0):
    """Полосы Боллинджера → (lower, mid, upper) или None."""
    mid = sma(xs, n)
    sd = stdev(xs, n)
    if mid is None or sd is None:
        return None
    return (mid - k * sd, mid, mid + k * sd)


def donchian(highs: Sequence[float], lows: Sequence[float], n: int):
    """Канал Дончиана по предыдущим n барам → (lower, upper) или None.

    ВАЖНО: берём n баров ДО последнего (xs[-n-1:-1]), чтобы пробой текущего бара
    сравнивался с каналом, не включающим сам текущий бар (иначе сигнал тривиален)."""
    if len(highs) < n + 1 or len(lows) < n + 1:
        return None
    return (min(lows[-n - 1:-1]), max(highs[-n - 1:-1]))


def atr(highs: Sequence[float], lows: Sequence[float],
        closes: Sequence[float], n: int = 14) -> Optional[float]:
    """Average True Range (сглаживание Уайлдера)."""
    if len(closes) < n + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    if len(trs) < n:
        return None
    val = sum(trs[:n]) / n
    for tr in trs[n:]:
        val = (val * (n - 1) + tr) / n
    return val


def crossed_up(fast_prev: float, slow_prev: float,
               fast_now: float, slow_now: float) -> bool:
    """fast пересёк slow снизу вверх между прошлым и текущим баром."""
    return fast_prev <= slow_prev and fast_now > slow_now


def crossed_down(fast_prev: float, slow_prev: float,
                 fast_now: float, slow_now: float) -> bool:
    return fast_prev >= slow_prev and fast_now < slow_now


def returns(xs: Sequence[float]) -> list[float]:
    """Простые доходности период-к-периоду."""
    return [xs[i] / xs[i - 1] - 1.0 for i in range(1, len(xs)) if xs[i - 1]]


# ───────────────────────── расширенные (R1) ─────────────────────────
def _ema_series(xs: Sequence[float], n: int) -> list[float]:
    """EMA в каждой точке начиная с индекса n−1 (сид — SMA первых n). Длина = len−n+1."""
    if len(xs) < n:
        return []
    k = 2.0 / (n + 1)
    val = sum(xs[:n]) / n
    out = [val]
    for x in xs[n:]:
        val = x * k + val * (1 - k)
        out.append(val)
    return out


def macd(xs: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD → (macd, signal, hist) или None. macd = EMA(fast) − EMA(slow)."""
    if len(xs) < slow + signal:
        return None
    ef = _ema_series(xs, fast)
    es = _ema_series(xs, slow)
    # выравниваем по длине более короткого (slow начинается позже)
    tail = len(es)
    ef = ef[-tail:]
    macd_line = [a - b for a, b in zip(ef, es)]
    if len(macd_line) < signal:
        return None
    sig = _ema_series(macd_line, signal)
    m = macd_line[-1]
    s = sig[-1]
    return (m, s, m - s)


def roc(xs: Sequence[float], n: int = 12) -> Optional[float]:
    """Rate of Change, % за n периодов."""
    if len(xs) < n + 1 or not xs[-n - 1]:
        return None
    return (xs[-1] / xs[-n - 1] - 1.0) * 100.0


def stochastic(highs: Sequence[float], lows: Sequence[float],
               closes: Sequence[float], n: int = 14, d: int = 3):
    """Стохастик → (%K, %D) или None. %D — SMA(%K, d)."""
    if len(closes) < n + d - 1:
        return None
    ks = []
    for i in range(len(closes) - d + 1, len(closes) + 1):
        win_h = max(highs[i - n:i])
        win_l = min(lows[i - n:i])
        rng = win_h - win_l
        ks.append(100.0 * (closes[i - 1] - win_l) / rng if rng else 50.0)
    return (ks[-1], sum(ks) / len(ks))


def obv(closes: Sequence[float], volumes: Sequence[float]) -> Optional[float]:
    """On-Balance Volume (последнее накопленное значение)."""
    if len(closes) < 2:
        return None
    val = 0.0
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            val += volumes[i]
        elif closes[i] < closes[i - 1]:
            val -= volumes[i]
    return val


def keltner(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
            n: int = 20, mult: float = 2.0):
    """Каналы Кельтнера → (lower, mid, upper) или None. mid = EMA(close), ширина = mult·ATR."""
    mid = ema(closes, n)
    a = atr(highs, lows, closes, n)
    if mid is None or a is None:
        return None
    return (mid - mult * a, mid, mid + mult * a)


def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        n: int = 14):
    """ADX/DMI Уайлдера → (plus_di, minus_di, adx) или None.

    ADX > ~25 трактуется как наличие тренда (направление — по знаку +DI − −DI)."""
    if len(closes) < 2 * n + 1:
        return None
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))

    def _wilder(seq):
        sm = sum(seq[:n])
        out = [sm]
        for x in seq[n:]:
            sm = sm - sm / n + x
            out.append(sm)
        return out

    str_ = _wilder(trs)
    sp = _wilder(plus_dm)
    sm = _wilder(minus_dm)
    pdi = [100.0 * p / t if t else 0.0 for p, t in zip(sp, str_)]
    mdi = [100.0 * m / t if t else 0.0 for m, t in zip(sm, str_)]
    dx = [100.0 * abs(p - m) / (p + m) if (p + m) else 0.0 for p, m in zip(pdi, mdi)]
    if len(dx) < n:
        return None
    adx_val = sum(dx[:n]) / n
    for x in dx[n:]:
        adx_val = (adx_val * (n - 1) + x) / n
    return (pdi[-1], mdi[-1], adx_val)


def supertrend(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
               n: int = 10, mult: float = 3.0):
    """Supertrend → (line, direction) или None. direction: +1 аптренд, −1 даунтренд."""
    if len(closes) < n + 1:
        return None
    a = atr(highs, lows, closes, n)
    if a is None:
        return None
    # пересчёт по хвосту: упрощённая итеративная схема с фиксированным ATR хвоста
    hl2 = [(highs[i] + lows[i]) / 2 for i in range(len(closes))]
    upper = [hl2[i] + mult * a for i in range(len(closes))]
    lower = [hl2[i] - mult * a for i in range(len(closes))]
    direction = 1
    st = lower[0]
    for i in range(1, len(closes)):
        if closes[i] > upper[i - 1]:
            direction = 1
        elif closes[i] < lower[i - 1]:
            direction = -1
        else:
            if direction == 1:
                lower[i] = max(lower[i], lower[i - 1])
            else:
                upper[i] = min(upper[i], upper[i - 1])
        st = lower[i] if direction == 1 else upper[i]
    return (st, direction)


def hurst(xs: Sequence[float], n: int = 100) -> Optional[float]:
    """Коэффициент Херста методом R/S (Rescaled Range) на ценах.

    xs: исходный ряд цен.
    n: размер скользящего окна.
    """
    if len(xs) < n or n < 16:
        return None
    prices = xs[-n:]
    y = []
    for i in range(1, len(prices)):
        if prices[i-1] <= 0 or prices[i] <= 0:
            return None
        y.append(math.log(prices[i] / prices[i-1]))

    sizes = []
    current_size = 8
    while current_size <= len(y):
        sizes.append(current_size)
        current_size *= 2

    if len(sizes) < 2:
        return None

    rs_vals = []
    for L in sizes:
        num_blocks = len(y) // L
        block_rs = []
        for b in range(num_blocks):
            block = y[b*L : (b+1)*L]
            m = sum(block) / L
            cum = 0.0
            cum_devs = [0.0]
            for val in block:
                cum += val - m
                cum_devs.append(cum)
            R = max(cum_devs) - min(cum_devs)
            var = sum((val - m) ** 2 for val in block) / L
            S = math.sqrt(var) if var > 0 else 0.0
            if S > 0:
                block_rs.append(R / S)
        if block_rs:
            rs_vals.append(sum(block_rs) / len(block_rs))
        else:
            rs_vals.append(None)

    valid_points = [(math.log(L), math.log(rs)) for L, rs in zip(sizes, rs_vals) if rs is not None and rs > 0]
    if len(valid_points) < 2:
        return None

    num_pts = len(valid_points)
    sum_x = sum(pt[0] for pt in valid_points)
    sum_y = sum(pt[1] for pt in valid_points)
    sum_xx = sum(pt[0]**2 for pt in valid_points)
    sum_xy = sum(pt[0] * pt[1] for pt in valid_points)

    denominator = (num_pts * sum_xx - sum_x ** 2)
    if abs(denominator) < 1e-9:
        return None

    slope = (num_pts * sum_xy - sum_x * sum_y) / denominator
    return slope


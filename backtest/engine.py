"""Событийный движок: гоняет стратегию по объединённой ленте баров.

Порядок на каждом шаге ленты i (метка времени t):
  1. broker.process(i)  — исполняем отложенные с прошлого бара ордера по open[i]
                          (это и есть «исполнение на следующем баре» → нет lookahead);
  2. наращиваем историю контекста баром i;
  3. strategy.on_bar(ctx) — стратегия видит close[i], ставит заявки (сработают на i+1);
  4. фиксируем equity по close[i].

Данные: dict ticker -> list[Bar] (по возрастанию времени). Тикеры могут иметь разные
ленты — движок берёт объединение меток времени и на каждом шаге отдаёт те бары,
что есть на эту метку.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .core import Bar, Broker, Instrument, Order, Trade, MARKET, LIMIT


# ───────────────────────── контекст ─────────────────────────
class Context:
    """То, что видит стратегия на каждом баре. Только чтение прошлого + подача заявок."""

    def __init__(self, broker: Broker, instruments: dict[str, Instrument]):
        self._b = broker
        self._inst = instruments
        self._hist: dict[str, list[Bar]] = {t: [] for t in instruments}
        self._cur: dict[str, Bar] = {}
        self._stops: dict[str, dict] = {}
        self.i = -1
        self.t = 0

    # — время —
    @property
    def date(self) -> datetime:
        return datetime.fromtimestamp(self.t, tz=timezone.utc)

    # — котировки (только прошлое+настоящее) —
    def bar(self, ticker: str) -> Optional[Bar]:
        return self._cur.get(ticker)

    def price(self, ticker: str) -> Optional[float]:
        b = self._cur.get(ticker)
        return b.c if b else None

    def history(self, ticker: str) -> list[Bar]:
        return self._hist[ticker]

    def closes(self, ticker: str, n: Optional[int] = None) -> list[float]:
        xs = [b.c for b in self._hist[ticker]]
        return xs[-n:] if n else xs

    def highs(self, ticker: str, n: Optional[int] = None) -> list[float]:
        xs = [b.h for b in self._hist[ticker]]
        return xs[-n:] if n else xs

    def lows(self, ticker: str, n: Optional[int] = None) -> list[float]:
        xs = [b.l for b in self._hist[ticker]]
        return xs[-n:] if n else xs

    def opens(self, ticker: str, n: Optional[int] = None) -> list[float]:
        xs = [b.o for b in self._hist[ticker]]
        return xs[-n:] if n else xs

    # — счёт —
    @property
    def cash(self) -> float:
        return self._b.cash

    @property
    def equity(self) -> float:
        return self._b.equity(self._cur)

    def position(self, ticker: str) -> float:
        return self._b.position(ticker)

    def tickers(self) -> list[str]:
        return list(self._inst)

    def instrument(self, ticker: str) -> Instrument:
        return self._inst[ticker]

    def multiplier(self, ticker: str) -> float:
        return self._inst[ticker].multiplier

    def lot(self, ticker: str) -> int:
        return self._inst[ticker].lot

    # — заявки —
    def order(self, ticker: str, qty: float, limit_price: Optional[float] = None,
              tif_bars: Optional[int] = None) -> Optional[Order]:
        """Подать заявку. qty знаковое (+buy/−sell). Если limit_price — лимит."""
        if qty == 0:
            return None
        o = Order(ticker=ticker, qty=qty,
                  type=LIMIT if limit_price is not None else MARKET,
                  limit_price=limit_price, tif_bars=tif_bars)
        return self._b.submit(o, self.i)

    def buy(self, ticker: str, qty: float, **kw) -> Optional[Order]:
        return self.order(ticker, abs(qty), **kw)

    def sell(self, ticker: str, qty: float, **kw) -> Optional[Order]:
        return self.order(ticker, -abs(qty), **kw)

    def close(self, ticker: str) -> Optional[Order]:
        """Закрыть позицию по рынку."""
        pos = self._b.position(ticker)
        return self.order(ticker, -pos) if pos else None

    def order_target_percent(self, ticker: str, frac: float) -> Optional[Order]:
        """Привести позицию к доле frac от equity (рыночной заявкой, округление к лоту)."""
        px = self.price(ticker)
        if not px:
            return None
        inst = self._inst[ticker]
        target_notional = frac * self.equity
        cur_qty = self._b.position(ticker)
        cur_notional = inst.notional(px, cur_qty)
        delta_notional = target_notional - cur_notional
        unit = inst.notional(px, 1)
        if unit == 0:
            return None
        delta_qty = delta_notional / unit
        # округляем к кратному лоту, по модулю вниз — не перебираем цель
        lot = max(inst.lot, 1)
        steps = int(delta_qty / lot)
        qty = steps * lot
        return self.order(ticker, qty) if qty else None

    def cancel(self, order_id: int) -> bool:
        return self._b.cancel(order_id)

    def pending(self) -> list[Order]:
        return [o for o in self._b.pending if o.status == "new"]

    # — стопы (opt-in: стратегия сама вызывает update_stops в начале on_bar) —
    def set_stop(self, ticker: str, level: float) -> None:
        """Жёсткий стоп-лосс: при касании level баром позиция закроется."""
        self._stops.setdefault(ticker, {})["stop"] = level

    def set_take(self, ticker: str, level: float) -> None:
        """Тейк-профит на уровне level."""
        self._stops.setdefault(ticker, {})["take"] = level

    def set_trailing(self, ticker: str, distance: float) -> None:
        """Трейлинг-стоп: отступ distance (в единицах цены) от пика/дна за время сделки."""
        s = self._stops.setdefault(ticker, {})
        s["trail"] = distance
        s["peak"] = self.price(ticker) or 0.0

    def clear_stops(self, ticker: str) -> None:
        self._stops.pop(ticker, None)

    def update_stops(self, ticker: str) -> bool:
        """Проверить стопы по текущему бару. Если сработал — закрыть и вернуть True.

        Вызывать в начале on_bar: `if ctx.update_stops(t): return`. Длинная позиция
        срабатывает по low бара (стоп/трейл) или high (тейк); короткая — зеркально.
        Закрытие исполняется по open следующего бара (как любая рыночная заявка)."""
        pos = self._b.position(ticker)
        bar = self._cur.get(ticker)
        s = self._stops.get(ticker)
        if not pos or bar is None or not s:
            return False
        long = pos > 0
        # трейлинг: обновляем экстремум и пересчитываем уровень
        if "trail" in s:
            if long:
                s["peak"] = max(s.get("peak", bar.c), bar.h)
                s["stop"] = max(s.get("stop", -1e18), s["peak"] - s["trail"])
            else:
                s["peak"] = min(s.get("peak", bar.c), bar.l)
                s["stop"] = min(s.get("stop", 1e18), s["peak"] + s["trail"])
        hit = False
        if "stop" in s:
            hit = (bar.l <= s["stop"]) if long else (bar.h >= s["stop"])
        if not hit and "take" in s:
            hit = (bar.h >= s["take"]) if long else (bar.l <= s["take"])
        if hit:
            self.close(ticker)
            self.clear_stops(ticker)
            return True
        return False

    # — внутреннее (движок) —
    def _advance(self, i: int, t: int, bars: dict[str, Bar]) -> None:
        self.i, self.t, self._cur = i, t, bars
        for tk, b in bars.items():
            self._hist[tk].append(b)


# ───────────────────────── стратегия ─────────────────────────
class Strategy:
    """База стратегии. Переопредели on_bar; on_start/on_finish — по желанию."""
    name = "base"

    def params(self) -> dict:
        """Параметры для отчёта/оптимизатора (по умолчанию — публичные поля экземпляра)."""
        return {k: v for k, v in vars(self).items() if not k.startswith("_")}

    def on_start(self, ctx: Context) -> None:  # noqa: U100
        pass

    def on_bar(self, ctx: Context) -> None:
        raise NotImplementedError

    def on_finish(self, ctx: Context) -> None:  # noqa: U100
        pass


# ───────────────────────── результат ─────────────────────────
@dataclass
class Result:
    strategy: str
    params: dict
    times: list[int]               # метки времени equity-кривой
    equity: list[float]            # equity по close каждого бара
    cash0: float
    trades: list[Trade]
    fills: list[Order]
    exposure: list[float] = field(default_factory=list)
    commissions_paid: float = 0.0
    data_tickers: list[str] = field(default_factory=list)
    bars: int = 0

    @property
    def final_equity(self) -> float:
        return self.equity[-1] if self.equity else self.cash0

    @property
    def total_return(self) -> float:
        return self.final_equity / self.cash0 - 1.0 if self.cash0 else 0.0


# ───────────────────────── прогон ─────────────────────────
def _timeline(data: dict[str, list[Bar]]):
    """Объединённая лента: (t, {ticker: Bar}) по возрастанию времени."""
    index: dict[str, dict[int, Bar]] = {t: {b.t: b for b in bars} for t, bars in data.items()}
    all_t = sorted({b.t for bars in data.values() for b in bars})
    for t in all_t:
        yield t, {tk: idx[t] for tk, idx in index.items() if t in idx}


def run(strategy: Strategy, data: dict[str, list[Bar]], cash: float = 100_000.0,
        commission: float = 0.0005, slippage: float = 0.0,
        instruments: Optional[dict[str, Instrument]] = None) -> Result:
    """Прогнать стратегию по данным. Возвращает Result с equity-кривой и сделками."""
    if not data:
        raise ValueError("data is empty")
    if instruments is None:
        instruments = {t: Instrument(t) for t in data}
    # убедимся, что под каждый тикер данных есть инструмент
    for t in data:
        instruments.setdefault(t, Instrument(t))

    broker = Broker(cash, instruments, commission=commission, slippage=slippage)
    ctx = Context(broker, instruments)
    strategy.on_start(ctx)

    times, equity, exposure = [], [], []
    for i, (t, bars) in enumerate(_timeline(data)):
        broker.process(i, bars)        # филлы заявок прошлого бара по open[i]
        ctx._advance(i, t, bars)       # история += бар i
        strategy.on_bar(ctx)           # стратегия видит close[i], ставит заявки на i+1
        times.append(t)
        equity.append(broker.equity(bars))
        exposure.append(broker.exposure(bars))
    strategy.on_finish(ctx)

    return Result(
        strategy=getattr(strategy, "name", type(strategy).__name__),
        params=strategy.params(),
        times=times, equity=equity, cash0=cash,
        trades=list(broker.trades), fills=list(broker.fills),
        exposure=exposure, commissions_paid=broker.commissions_paid,
        data_tickers=list(data), bars=len(times))

"""Ядро симулятора: бар, инструмент, ордер, брокер с комиссией и проскальзыванием.

Модель денег — кэш-модель «как для акций»: на покупку кэш уменьшается на нотионал
плюс комиссию, на продажу — растёт. Equity = кэш + рыночная стоимость позиций.
Маржинальную модель фьючерсов (где кэш не движется, а блокируется ГО) сознательно
НЕ воспроизводим — для учебного движка кэш-модель проще и согласована с метриками;
для фьючерса достаточно задать multiplier (рублей за 1.0 пункта на единицу).

Антизаглядывание (no lookahead): сигнал считается по закрытию бара i, а исполняется
на ОТКРЫТИИ бара i+1. Брокер не знает будущего — он обрабатывает отложенные ордера
ровно в тот бар, который ему передал движок.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Optional


# ───────────────────────── данные ─────────────────────────
@dataclass(frozen=True)
class Bar:
    """Одна свеча. t — epoch-секунды UTC. Цены в «родных» единицах инструмента."""
    t: int
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0

    def __post_init__(self) -> None:
        # дешёвая защита от битых свечей: high>=low и они охватывают open/close
        if self.h < self.l:
            raise ValueError(f"bar {self.t}: high {self.h} < low {self.l}")


@dataclass(frozen=True)
class Instrument:
    """Торгуемый инструмент. multiplier — рублей за 1.0 движения цены на единицу.

    Акции: multiplier=1.0 (цена уже в рублях за штуку). Фьючерс GLDRUBF: цена в
    пунктах, 1 пункт = point_rub рублей → multiplier=point_rub. lot — размер лота
    в штуках (нотионал считается за единицу 'qty'; qty задаёт стратегия как угодно,
    лот используется лишь как подсказка для round-сайзинга в стратегиях).

    kind управляет денежной моделью:
      "cash"    — как акции: покупка тратит кэш на нотионал, equity = кэш + стоимость позиций;
      "futures" — маржинальная: кэш НЕ тратится на нотионал (блокируется ГО), реализованный
                  P&L кредитуется в кэш при сокращении, equity = кэш + нереализованный P&L.
                  Это согласовано с песочницей T-Invest (см. lab/strategy.py: честный equity).
    """
    ticker: str
    multiplier: float = 1.0
    lot: int = 1
    step: float = 0.01
    kind: str = "cash"            # "cash" (как акции) | "futures" (маржа/вармаржа)
    margin_rate: float = 1.0      # доля нотионала под ГО (для futures; справочно/для exposure)

    def notional(self, price: float, qty: float) -> float:
        return price * self.multiplier * qty

    @property
    def is_futures(self) -> bool:
        return self.kind == "futures"


# ───────────────────────── ордера ─────────────────────────
MARKET = "market"
LIMIT = "limit"

NEW = "new"
FILLED = "filled"
CANCELLED = "cancelled"
EXPIRED = "expired"

_oid = itertools.count(1)


@dataclass
class Order:
    ticker: str
    qty: float                      # знаковое: + покупка, − продажа
    type: str = MARKET
    limit_price: Optional[float] = None
    tif_bars: Optional[int] = None  # limit живёт N баров; None → до конца теста
    id: int = field(default_factory=lambda: next(_oid))
    created_i: int = -1
    status: str = NEW
    fill_price: float = 0.0
    fill_i: int = -1
    commission: float = 0.0

    def __post_init__(self) -> None:
        if self.qty == 0:
            raise ValueError("order qty must be non-zero")
        if self.type == LIMIT and self.limit_price is None:
            raise ValueError("limit order requires limit_price")


# ───────────────────────── позиция ────────────────────────
@dataclass
class Position:
    """Позиция по среднему. Знак qty: long>0, short<0."""
    qty: float = 0.0
    avg: float = 0.0          # средняя цена входа (в единицах цены)
    realized: float = 0.0     # реализованный P&L в рублях (за вычетом комиссий сделок)


@dataclass
class Trade:
    """Закрытая (или частично закрытая) сделка — round trip для статистики."""
    ticker: str
    side: str                 # 'long' | 'short'
    qty: float
    entry: float
    exit: float
    entry_i: int
    exit_i: int
    pnl: float                # рублёвый P&L без комиссий (комиссии учтены в equity)
    ret: float                # доходность на нотионал входа


# ───────────────────────── брокер ─────────────────────────
class Broker:
    """Симулятор исполнения. Хранит кэш, позиции, отложенные ордера и историю сделок.

    commission — доля нотионала (в обе стороны). slippage — доля цены, всегда против
    нас (покупаем дороже, продаём дешевле). Маркет-ордера исполняются по open текущего
    бара ± slippage; лимиты — если бар своим диапазоном [l,h] коснулся лимита.
    """

    def __init__(self, cash: float, instruments: dict[str, Instrument],
                 commission: float = 0.0005, slippage: float = 0.0):
        self.cash0 = cash
        self.cash = cash
        self.instruments = instruments
        self.commission = commission
        self.slippage = slippage
        self.positions: dict[str, Position] = {t: Position() for t in instruments}
        self.pending: list[Order] = []
        self.fills: list[Order] = []
        self.trades: list[Trade] = []
        self.commissions_paid = 0.0

    # — приём заявок —
    def submit(self, order: Order, i: int) -> Order:
        order.created_i = i
        self.pending.append(order)
        return order

    def cancel(self, order_id: int) -> bool:
        for o in self.pending:
            if o.id == order_id and o.status == NEW:
                o.status = CANCELLED
                return True
        return False

    def position(self, ticker: str) -> float:
        return self.positions[ticker].qty

    # — обработка одного бара —
    def process(self, i: int, bars: dict[str, Bar]) -> None:
        """Исполнить отложенные ордера против баров бара i. Вызывается движком на
        каждом баре ДО того, как стратегия увидит этот бар (fill по open i)."""
        still: list[Order] = []
        for o in self.pending:
            if o.status != NEW:
                continue
            bar = bars.get(o.ticker)
            if bar is None:                       # нет котировки на этот бар — ждём
                still.append(o)
                continue
            fill = self._try_fill(o, bar)
            if fill is None:
                if o.tif_bars is not None and i - o.created_i >= o.tif_bars:
                    o.status = EXPIRED
                else:
                    still.append(o)
                continue
            self._apply_fill(o, fill, i)
        self.pending = still

    def _try_fill(self, o: Order, bar: Bar) -> Optional[float]:
        """Вернуть цену исполнения или None, если лимит не сработал на этом баре."""
        if o.type == MARKET:
            slip = self.slippage * (1 if o.qty > 0 else -1)
            return bar.o * (1 + slip)
        # LIMIT: покупка исполняется если рынок опускался до лимита (low<=limit),
        # продажа — если поднимался до лимита (high>=limit). Цена = лимит (консервативно).
        if o.qty > 0 and bar.l <= o.limit_price:
            return min(o.limit_price, bar.o)      # гэп в нашу пользу — по open
        if o.qty < 0 and bar.h >= o.limit_price:
            return max(o.limit_price, bar.o)
        return None

    def _apply_fill(self, o: Order, price: float, i: int) -> None:
        inst = self.instruments[o.ticker]
        notional = inst.notional(price, abs(o.qty))
        comm = notional * self.commission
        self.commissions_paid += comm
        self.cash -= comm
        # денежный поток зависит от типа инструмента:
        #  cash-модель (акции): покупка уводит кэш на нотионал, продажа возвращает;
        #  futures: кэш на нотионал НЕ движется (блокируется ГО), вместо этого в кэш
        #  кредитуется реализованный P&L при сокращении позиции (вариационная маржа).
        if not inst.is_futures:
            self.cash -= inst.notional(price, o.qty)   # qty знаковое
        realized = self._update_position(o.ticker, o.qty, price, i, inst)
        if inst.is_futures:
            self.cash += realized
        o.status = FILLED
        o.fill_price = price
        o.fill_i = i
        o.commission = comm
        self.fills.append(o)

    def _update_position(self, ticker: str, dqty: float, price: float,
                         i: int, inst: Instrument) -> float:
        """Обновить позицию по средней. Возвращает реализованный P&L этого филла (0, если
        позиция только открывалась/наращивалась). Для futures этот P&L кредитуется в кэш."""
        p = self.positions[ticker]
        old = p.qty
        new = old + dqty
        same_dir = (old >= 0 and dqty > 0) or (old <= 0 and dqty < 0)
        if old == 0 or same_dir:
            # открытие/наращивание — пересчёт средней
            p.avg = (p.avg * abs(old) + price * abs(dqty)) / (abs(old) + abs(dqty))
            p.qty = new
            if old == 0:
                p.entry_i = i  # type: ignore[attr-defined]
            return 0.0
        # сокращение/закрытие/переворот — реализуем P&L по закрываемой части
        closed = min(abs(dqty), abs(old))
        side = "long" if old > 0 else "short"
        direction = 1 if old > 0 else -1
        pnl = (price - p.avg) * direction * closed * inst.multiplier
        p.realized += pnl
        entry_i = getattr(p, "entry_i", i)
        self.trades.append(Trade(
            ticker=ticker, side=side, qty=closed, entry=p.avg, exit=price,
            entry_i=entry_i, exit_i=i, pnl=pnl,
            ret=(pnl / (p.avg * closed * inst.multiplier)) if p.avg else 0.0))
        if abs(dqty) <= abs(old):
            p.qty = new
            if p.qty == 0:
                p.avg = 0.0
        else:
            # переворот: остаток открывает противоположную позицию по цене сделки
            p.qty = new
            p.avg = price
            p.entry_i = i  # type: ignore[attr-defined]
        return pnl

    # — оценка —
    def equity(self, bars: dict[str, Bar]) -> float:
        eq = self.cash
        for t, p in self.positions.items():
            if not p.qty:
                continue
            bar = bars.get(t)
            if bar is None:
                continue
            inst = self.instruments[t]
            if inst.is_futures:
                # фьючерс: кэш не платил нотионал → вносим только нереализованный P&L
                eq += (bar.c - p.avg) * p.qty * inst.multiplier
            else:
                eq += inst.notional(bar.c, p.qty)   # акции: рыночная стоимость позиции
        return eq

    def exposure(self, bars: dict[str, Bar]) -> float:
        """Доля капитала в рынке (абс. нотионал позиций / equity)."""
        gross = sum(self.instruments[t].notional(bars[t].c, abs(p.qty))
                    for t, p in self.positions.items()
                    if p.qty and t in bars)
        eq = self.equity(bars)
        return gross / eq if eq else 0.0

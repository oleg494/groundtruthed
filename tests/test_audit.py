"""CI-аудит движка по deep/backtest_engine_audit.md (12 блоков).

Дополняет (не дублирует) analysis/backtest_validate.py (независимая реконструкция
equity) и analysis/inference_validate.py (калибровка PSR/DSR, детектор lookahead):
здесь — офлайн pytest-оракулы на синтетике: читер-детекторы, инварианты сохранения
денег, краевые случаи стопов/лимиток/гэпов, шорты, детерминизм сидов.

Стратегии определены локально — backtest/strategies.py правит соседний воркфлоу.
Тесты, вскрывшие реальные баги движка, помечены xfail(strict=True) — движок здесь
НЕ чиним (см. analysis/engine_audit_result.md).
"""
import math
import random

import pytest

from backtest import candles
from backtest.core import Bar, Broker, Instrument, Order, MARKET
from backtest.engine import Result, Strategy, run
from backtest.montecarlo import bootstrap_returns, bootstrap_trades
from backtest.optimize import walk_forward
from backtest.robust import deflated_sharpe, expected_max_sharpe, probabilistic_sharpe
from conftest import bars_from_ohlc

DAY = 86400


def _d(rows):
    return {"X": bars_from_ohlc(rows)}


def _flat(px, n):
    return [(px, px, px, px)] * n


# ───────────────────── локальные скриптовые стратегии ─────────────────────
class _BuyAt(Strategy):
    """Подать знаковую заявку qty на баре i_buy, закрыть на i_close (если задан)."""
    name = "buyat"

    def __init__(self, i_buy=0, qty=1, i_close=None):
        self.i_buy, self.qty, self.i_close = i_buy, qty, i_close

    def on_bar(self, ctx):
        if ctx.i == self.i_buy:
            ctx.order("X", self.qty)
        elif self.i_close is not None and ctx.i == self.i_close:
            ctx.close("X")


class _SMA(Strategy):
    """SMA-кросс с фиксированным qty (сайзинг не зависит от equity →
    поток филлов идентичен при любых комиссиях/слиппедже — точный оракул)."""
    name = "sma_fixed"

    def __init__(self, fast=5, slow=20, qty=10):
        assert fast < slow
        self.fast, self.slow, self.qty = fast, slow, qty

    def on_bar(self, ctx):
        t = ctx.tickers()[0]
        cs = ctx.closes(t)
        if len(cs) < self.slow:
            return
        f = sum(cs[-self.fast:]) / self.fast
        s = sum(cs[-self.slow:]) / self.slow
        pos = ctx.position(t)
        if f > s and pos == 0:
            ctx.buy(t, self.qty)
        elif f <= s and pos != 0:
            ctx.close(t)


# ══════════════════ блок 1: no-lookahead ══════════════════
class _SameBarOracle(Strategy):
    """ЧИТЕР со знанием ТЕКУЩЕГО бара: видит, что бар i был растущим (c>o),
    и пытается забрать это движение. В честном движке филл — по open(i+1),
    движение бара i уже ушло → на мартингале заработать НЕЛЬЗЯ."""
    name = "samebar"

    def on_bar(self, ctx):
        t = ctx.tickers()[0]
        b = ctx.bar(t)
        if ctx.position(t):
            ctx.close(t)
        elif b and b.c > b.o:
            ctx.buy(t, 10)


def test_samebar_cheater_earns_nothing_in_honest_engine():
    # mu=0 → цена-мартингал: у same-bar сигнала нет предсказательной силы на i+1
    data = candles.gbm("SYN", bars=400, seed=11, mu=0.0)
    res = run(_SameBarOracle(), data, cash=100_000, commission=0.0, slippage=0.0)
    honest = res.final_equity - res.cash0
    # «нечестный» бенчмарк: исполнение на том же баре (open→close каждого up-бара)
    dishonest = sum((b.c - b.o) * 10 for b in data["SYN"] if b.c > b.o)
    assert dishonest > 0
    assert len(res.fills) > 50            # читер активно торговал
    # честный движок не даёт забрать внутрибарное движение
    assert abs(honest) < 0.35 * dishonest


class _HistoryAudit(Strategy):
    """Проверяет на каждом баре, что контекст не отдаёт будущее."""
    name = "hist"

    def __init__(self):
        self.ok = True

    def on_bar(self, ctx):
        t = ctx.tickers()[0]
        h = ctx.history(t)
        self.ok &= len(h) == ctx.i + 1          # ровно прошлое + текущий бар
        self.ok &= h[-1] is ctx.bar(t)          # последний бар истории = текущий
        self.ok &= len(ctx.closes(t, 999)) <= ctx.i + 1


def test_context_history_contains_no_future_bars():
    s = _HistoryAudit()
    run(s, candles.gbm("SYN", bars=100, seed=1), cash=1000)
    assert s.ok


# ══════════════════ блок 2: исполнение на следующем open ══════════════════
def test_order_on_last_bar_never_fills():
    rows = _flat(100, 3)
    res = run(_BuyAt(i_buy=2), _d(rows), cash=1000, commission=0.0)
    assert res.fills == []                     # следующего бара нет — филла нет
    assert math.isclose(res.final_equity, 1000.0)


def test_market_fill_at_open_ignores_intrabar_move():
    # бар филла открылся по 100 и рухнул до 50 — филл всё равно по open=100,
    # убыток от падения честно ложится в equity того же бара
    rows = [(100, 100, 100, 100), (100, 100, 50, 50), (50, 50, 50, 50)]
    res = run(_BuyAt(i_buy=0, qty=1), _d(rows), cash=1000, commission=0.0)
    assert math.isclose(res.fills[0].fill_price, 100.0)
    assert math.isclose(res.equity[1], 1000 - 100 + 50)   # кэш 900 + позиция 50


# ══════════════════ блок 3: лимитные заявки ══════════════════
class _LimitBuy(Strategy):
    name = "lb"

    def __init__(self, limit, tif=None):
        self.limit, self.tif = limit, tif

    def on_bar(self, ctx):
        if ctx.i == 0:
            ctx.buy("X", 1, limit_price=self.limit, tif_bars=self.tif)


def test_limit_buy_fills_only_on_touch_and_at_limit():
    # касание: low=94 <= limit=95, open=100 выше → филл ровно по лимиту 95
    rows = [(100, 100, 100, 100), (100, 101, 94, 96), (96, 96, 96, 96)]
    res = run(_LimitBuy(95), _d(rows), cash=1000, commission=0.0)
    assert len(res.fills) == 1
    assert math.isclose(res.fills[0].fill_price, 95.0)
    # без касания — заявка висит, филла нет
    rows2 = [(100, 100, 100, 100), (100, 101, 96, 98), (98, 99, 97, 98)]
    res2 = run(_LimitBuy(95), _d(rows2), cash=1000, commission=0.0)
    assert res2.fills == []


def test_limit_gap_through_fills_at_open_not_worse():
    # гэп вниз сквозь лимит 95: open=90 — покупка по 90 (в нашу пользу), не по 95
    rows = [(100, 100, 100, 100), (90, 91, 89, 90), (90, 90, 90, 90)]
    res = run(_LimitBuy(95), _d(rows), cash=1000, commission=0.0)
    assert math.isclose(res.fills[0].fill_price, 90.0)


def test_limit_tif_expires_without_fill():
    rows = _flat(100, 6)                       # цена никогда не падает до 95
    res = run(_LimitBuy(95, tif=2), _d(rows), cash=1000, commission=0.0)
    assert res.fills == []
    assert math.isclose(res.final_equity, 1000.0)


# ══════════════════ блок 4: стопы и тейки ══════════════════
class _StopTake(Strategy):
    name = "st"

    def __init__(self, qty=1, stop=None, take=None):
        self.qty, self.stop, self.take = qty, stop, take

    def on_bar(self, ctx):
        if ctx.position("X"):
            if ctx.update_stops("X"):
                return
        if ctx.i == 0:
            ctx.order("X", self.qty)
        elif ctx.i == 1:
            if self.stop is not None:
                ctx.set_stop("X", self.stop)
            if self.take is not None:
                ctx.set_take("X", self.take)


def test_gap_through_stop_fills_at_open_not_stop_price():
    # вход @100, стоп 95; бар 2 гэпует на 70 → выход по open бара 3 = 68, НЕ по 95
    rows = [(100, 100, 100, 100), (100, 100, 100, 100),
            (70, 71, 69, 70), (68, 68, 68, 68), (68, 68, 68, 68)]
    res = run(_StopTake(stop=95), _d(rows), cash=1000, commission=0.0)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert math.isclose(t.exit, 68.0)          # хуже стопа — гэп не «спасает»
    assert t.exit < 95
    assert math.isclose(t.pnl, -32.0)


def test_ambiguous_bar_stop_and_take_close_exactly_once():
    # бар 2 задевает И стоп (94<=95), И тейк (106>=105) — закрытие ровно одно;
    # исход не зависит от порядка срабатывания: выход всегда по open следующего бара
    rows = [(100, 100, 100, 100), (100, 100, 100, 100),
            (100, 106, 94, 100), (100, 100, 100, 100), (100, 100, 100, 100)]
    res = run(_StopTake(stop=95, take=105), _d(rows), cash=1000, commission=0.0)
    assert len(res.fills) == 2                 # вход + один выход, без дублей
    assert len(res.trades) == 1
    assert math.isclose(res.trades[0].exit, 100.0)


def test_short_stop_triggers_on_high_and_fills_next_open():
    # шорт @100, стоп 105; бар 2 high=106 → триггер, покрытие по open бара 3 = 107
    rows = [(100, 100, 100, 100), (100, 100, 100, 100),
            (104, 106, 103, 104), (107, 107, 107, 107), (107, 107, 107, 107)]
    res = run(_StopTake(qty=-1, stop=105), _d(rows), cash=1000, commission=0.0)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.side == "short"
    assert math.isclose(t.exit, 107.0)         # гэп сквозь стоп — по open, хуже стопа
    assert math.isclose(t.pnl, -7.0)


# ══════════════════ блок 5: шорты ══════════════════
def test_short_equity_accounting_on_rising_market():
    # шорт 1 @100: кэш +100; рост до 120 → equity падает ровно на 20
    rows = [(100, 100, 100, 100), (100, 100, 100, 100),
            (120, 120, 120, 120), (120, 120, 120, 120)]
    res = run(_BuyAt(i_buy=0, qty=-1), _d(rows), cash=1000, commission=0.0)
    assert math.isclose(res.equity[1], 1000.0)             # 1100 кэш − 100 позиция
    assert math.isclose(res.equity[2], 980.0)              # 1100 − 120
    assert res.equity[2] < res.equity[1]


@pytest.mark.xfail(
    strict=True,
    reason="БАГ (аудит блок 5): движок не проверяет достаточность средств — "
    "покупка на 1000× кэша исполняется, кэш уходит в -99900 без всякого "
    "маржинального ограничения; шорт так же неограничен")
def test_insufficient_funds_order_rejected():
    b = Broker(100.0, {"X": Instrument("X")}, commission=0.0)
    b.submit(Order("X", 1000, MARKET), 0)                  # нотионал 100 000 при кэше 100
    b.process(0, {"X": Bar(0, 100, 100, 100, 100)})
    # честный брокер обязан отклонить/урезать заявку — кэш не может уйти в минус
    assert b.cash >= 0 or b.position("X") == 0


# ══════════════════ блок 6: фьючерсная маржа + сохранение денег ══════════════════
class _RandomTrader(Strategy):
    """Случайные маркет/лимит-заявки обоих знаков — длинный сценарий для
    инварианта сохранения денег."""
    name = "rnd"

    def __init__(self, seed=0):
        self._rng = random.Random(seed)

    def on_bar(self, ctx):
        for t in ctx.tickers():
            px = ctx.price(t)
            if px is None:
                continue
            r = self._rng.random()
            if r < 0.25:
                ctx.order(t, self._rng.choice([-3, -2, -1, 1, 2, 3]))
            elif r < 0.40:
                off = self._rng.uniform(-0.02, 0.02)
                ctx.order(t, self._rng.choice([-2, -1, 1, 2]),
                          limit_price=px * (1 + off), tif_bars=5)
            elif r < 0.50 and ctx.position(t):
                ctx.close(t)


def _replay_equity(res: Result, data, instruments, comm):
    """Независимая переигровка кэша/позиций из потока филлов (без Broker)."""
    closes = {t: {b.t: b.c for b in bars} for t, bars in data.items()}
    fills_by_i: dict[int, list] = {}
    for o in res.fills:
        fills_by_i.setdefault(o.fill_i, []).append(o)
    cash = res.cash0
    pos = {t: 0.0 for t in instruments}
    avg = {t: 0.0 for t in instruments}
    curve = []
    for i, ts in enumerate(res.times):
        for o in fills_by_i.get(i, []):
            inst = instruments[o.ticker]
            mult = inst.multiplier
            cash -= o.fill_price * mult * abs(o.qty) * comm
            old = pos[o.ticker]
            if not inst.is_futures:
                cash -= o.fill_price * mult * o.qty
            new = old + o.qty
            same = (old >= 0 and o.qty > 0) or (old <= 0 and o.qty < 0)
            if old == 0 or same:
                avg[o.ticker] = (avg[o.ticker] * abs(old) + o.fill_price * abs(o.qty)) \
                    / (abs(old) + abs(o.qty))
            else:
                closed = min(abs(o.qty), abs(old))
                d = 1 if old > 0 else -1
                if inst.is_futures:
                    cash += (o.fill_price - avg[o.ticker]) * d * closed * mult
                if abs(o.qty) > abs(old):
                    avg[o.ticker] = o.fill_price
                elif new == 0:
                    avg[o.ticker] = 0.0
            pos[o.ticker] = new
        eq = cash
        for t, p in pos.items():
            if not p:
                continue
            cl = closes[t].get(ts)
            if cl is None:
                continue
            inst = instruments[t]
            eq += (cl - avg[t]) * p * inst.multiplier if inst.is_futures \
                else cl * inst.multiplier * p
        curve.append(eq)
    return curve, pos, avg


@pytest.mark.parametrize("kind,mult", [("cash", 1.0), ("futures", 10.0)])
def test_money_conservation_long_random_scenario(kind, mult):
    """Инвариант: equity движка на КАЖДОМ баре совпадает с независимой переигровкой
    из филлов, а итог = cash0 + Σ realized + unrealized − комиссии (до копейки)."""
    comm = 0.0005
    tickers = ["A", "B"]
    data = candles.basket(tickers, bars=300, seed=7)
    insts = {t: Instrument(t, multiplier=mult, kind=kind) for t in tickers}
    res = run(_RandomTrader(seed=13), data, cash=1_000_000, commission=comm,
              instruments=insts)
    assert len(res.fills) > 100                # сценарий действительно длинный
    curve, pos, avg = _replay_equity(res, data, insts, comm)
    assert len(curve) == len(res.equity)
    for a, b in zip(res.equity, curve):
        assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)
    # итоговое тождество из независимых слагаемых Result
    last_close = {t: data[t][-1].c for t in tickers}
    unreal = sum((last_close[t] - avg[t]) * pos[t] * mult for t in tickers if pos[t])
    ident = res.cash0 + sum(t.pnl for t in res.trades) + unreal - res.commissions_paid
    assert math.isclose(res.final_equity, ident, rel_tol=1e-9, abs_tol=1e-6)
    # комиссия начислена на каждый филл и сходится в сумме
    assert all(o.commission > 0 for o in res.fills)
    assert math.isclose(sum(o.commission for o in res.fills),
                        res.commissions_paid, abs_tol=1e-6)


@pytest.mark.xfail(
    strict=True,
    reason="БАГ/упрощение (аудит блок 6): нет maintenance margin и margin call — "
    "фьючерсная позиция переживает просадку до отрицательного equity без "
    "принудительного закрытия; просадки в бэктесте занижены против реальности")
def test_futures_margin_call_liquidates_before_negative_equity():
    inst = {"F": Instrument("F", multiplier=10.0, kind="futures")}
    b = Broker(100.0, inst, commission=0.0)
    b.submit(Order("F", 1, MARKET), 0)
    b.process(0, {"F": Bar(0, 1000, 1000, 1000, 1000)})
    crash = {"F": Bar(1, 500, 500, 500, 500)}              # −5000 при кэше 100
    b.process(1, crash)
    # честная маржинальная модель: позиция закрыта до того, как equity < 0
    assert b.equity(crash) >= 0 or b.position("F") == 0


@pytest.mark.xfail(
    strict=True,
    reason="БАГ (инвариант учёта): Broker.equity пропускает позицию, если у тикера "
    "нет бара на текущей метке времени — стоимость позиции исчезает из equity "
    "(ложный провал кривой на мульти-тикерных лентах с дырами) вместо оценки "
    "по последней известной цене")
def test_equity_uses_last_known_price_when_bar_missing():
    a = bars_from_ohlc(_flat(50, 5))
    b = bars_from_ohlc(_flat(100, 5))
    del b[2]                                               # у B нет бара на t=2
    data = {"A": a, "B": b}

    class BuyB(Strategy):
        name = "bb"

        def on_bar(self, ctx):
            if ctx.i == 0:
                ctx.buy("B", 1)

    res = run(BuyB(), data, cash=1000, commission=0.0)
    # позиция B (100) обязана оцениваться по последней цене, а не исчезать
    assert math.isclose(res.equity[2], 1000.0)


# ══════════════════ блок 7: комиссии ══════════════════
def test_roundtrip_commission_charged_both_sides():
    rows = _flat(100, 5)
    res = run(_BuyAt(i_buy=0, qty=1, i_close=2), _d(rows), cash=1000,
              commission=0.001)
    assert len(res.fills) == 2
    assert math.isclose(res.commissions_paid, 0.2)         # 100*0.001 вход + выход
    assert math.isclose(res.final_equity, 1000 - 0.2)


def test_commission_charged_on_stop_exit_too():
    rows = [(100, 100, 100, 100), (100, 100, 100, 100),
            (90, 90, 90, 90), (90, 90, 90, 90), (90, 90, 90, 90)]
    res = run(_StopTake(stop=95), _d(rows), cash=1000, commission=0.001)
    assert len(res.fills) == 2
    assert all(o.commission > 0 for o in res.fills)        # стоп-выход не бесплатный
    assert math.isclose(sum(o.commission for o in res.fills), res.commissions_paid)


def test_commission_stress_exact_degradation():
    # фиксированный qty → филлы идентичны при любой ставке; деградация equity
    # обязана РОВНО равняться сумме комиссий (breakeven-оракул из отчёта, блок 7)
    data = candles.gbm("SYN", bars=300, seed=2)
    r0 = run(_SMA(), data, cash=100_000, commission=0.0)
    r1 = run(_SMA(), data, cash=100_000, commission=0.001)
    assert len(r0.fills) == len(r1.fills) > 0
    assert r1.final_equity < r0.final_equity
    assert math.isclose(r0.final_equity - r1.final_equity,
                        r1.commissions_paid, rel_tol=1e-9, abs_tol=1e-6)


# ══════════════════ блок 8: slippage ══════════════════
def test_slippage_adverse_on_sell():
    rows = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100)]
    res = run(_BuyAt(i_buy=0, qty=-1), _d(rows), cash=1000, commission=0.0,
              slippage=0.01)
    assert math.isclose(res.fills[0].fill_price, 99.0)     # продаём дешевле open


def test_slippage_monotonic_degradation():
    data = candles.gbm("SYN", bars=300, seed=2)
    eqs = [run(_SMA(), data, cash=100_000, commission=0.0,
               slippage=s).final_equity for s in (0.0, 0.001, 0.005)]
    assert eqs[0] > eqs[1] > eqs[2]


def test_limit_fill_has_no_slippage():
    # лимит — пассивная заявка: филл ровно по лимитной цене, слиппедж не применяется
    rows = [(100, 100, 100, 100), (100, 101, 94, 96), (96, 96, 96, 96)]
    res = run(_LimitBuy(95), _d(rows), cash=1000, commission=0.0, slippage=0.05)
    assert math.isclose(res.fills[0].fill_price, 95.0)


# ══════════════════ блок 9: ребаланс ══════════════════
class _Target(Strategy):
    name = "tgt"

    def __init__(self, frac=0.5):
        self.frac = frac

    def on_bar(self, ctx):
        if ctx.i == 0:
            ctx.order_target_percent("X", self.frac)


def test_target_percent_never_overshoots_target():
    # цена 3: 50% от 1000 = 500 → 166 шт (498), НЕ 167 (501 > цели)
    rows = _flat(3, 3)
    res = run(_Target(0.5), _d(rows), cash=1000, commission=0.0)
    assert res.fills[0].qty == 166
    assert res.fills[0].qty * 3 <= 0.5 * 1000


def test_rebalance_sized_on_close_filled_at_next_open():
    # сайзинг по close(t)=100 (5 шт), филл по open(t+1)=120 — движок не
    # «переисполняет» по цене решения; фактический вес честно уезжает от цели
    rows = [(100, 100, 100, 100), (120, 120, 120, 120), (120, 120, 120, 120)]
    res = run(_Target(0.5), _d(rows), cash=1000, commission=0.0)
    assert res.fills[0].qty == 5                           # из close=100
    assert math.isclose(res.fills[0].fill_price, 120.0)    # но по open(t+1)


# ══════════════════ блок 10: walk-forward ══════════════════
def _wf(data, n_splits=3):
    grid = {"fast": [3, 5], "slow": [10, 20]}
    return walk_forward(_SMA, data, grid, n_splits=n_splits, metric="total_return",
                        cash=100_000, commission=0.0005)


def test_walkforward_windows_anchored_and_disjoint():
    data = candles.gbm("SYN", bars=240, seed=5)
    wf = _wf(data)
    assert len(wf.windows) == 3
    t0 = min(b.t for b in data["SYN"])
    for w in wf.windows:
        assert w.is_range[0] == t0                         # anchored: IS от начала
        assert w.is_range[1] == w.oos_range[0]             # OOS сразу после IS
        assert w.oos_range[0] < w.oos_range[1]
    # OOS-окна не перекрываются и идут подряд
    for a, b in zip(wf.windows, wf.windows[1:]):
        assert a.oos_range[1] <= b.oos_range[0]


def test_walkforward_stitched_equity_is_oos_only():
    data = candles.gbm("SYN", bars=240, seed=5)
    wf = _wf(data)
    oos_bars = sum(len(w.oos_result.times) for w in wf.windows)
    assert len(wf.equity) == 1 + oos_bars                  # cash0 + только OOS-бары
    n = len(data["SYN"])
    assert oos_bars == n - n // 4                          # всё после первого сегмента


def test_walkforward_no_leak_from_future_segments():
    """Возмущение ПОСЛЕДНЕГО сегмента не смеет менять ранние окна:
    их IS-выбор и OOS-результат зависят только от прошлого."""
    data = candles.gbm("SYN", bars=240, seed=5)
    rng = random.Random(9)
    bars = data["SYN"]
    cut = 180                                              # последний сегмент из 4
    mangled = list(bars[:cut])
    for b in bars[cut:]:
        f = 1.0 + rng.uniform(0.3, 0.8)
        mangled.append(Bar(t=b.t, o=b.o * f, h=b.h * f, l=b.l * f, c=b.c * f, v=b.v))
    wf1, wf2 = _wf(data), _wf({"SYN": mangled})
    for w1, w2 in zip(wf1.windows[:2], wf2.windows[:2]):   # окна 1-2 целиком в прошлом
        assert w1.best_params == w2.best_params
        assert math.isclose(w1.is_metric, w2.is_metric)
        assert math.isclose(w1.oos.total_return, w2.oos.total_return)


# ══════════════════ блок 11: Monte Carlo ══════════════════
def _fake_result(pnls, cash0=1000.0):
    from backtest.core import Trade
    trades = [Trade("X", "long", 1, 100, 100 + p, i, i + 1, p,
                    p / 100.0) for i, p in enumerate(pnls)]
    eq = [cash0]
    for p in pnls:
        eq.append(eq[-1] + p)
    return Result(strategy="fake", params={}, times=list(range(len(eq))),
                  equity=eq, cash0=cash0, trades=trades, fills=[])


def test_montecarlo_deterministic_by_seed():
    res = _fake_result([10, -5, 7, -3, 12, 4, -8, 6])
    a = bootstrap_trades(res, n=300, seed=1)
    b = bootstrap_trades(res, n=300, seed=1)
    assert a == b                                          # один сид → бит-в-бит
    c = bootstrap_trades(res, n=300, seed=2)
    assert a.ret_p50 != c.ret_p50 or a.dd_p5 != c.dd_p5    # другой сид → другой мир
    x = bootstrap_returns(res, n=300, seed=3)
    y = bootstrap_returns(res, n=300, seed=3)
    assert x == y


def test_montecarlo_degenerate_when_all_trades_equal():
    # k одинаковых P&L: выборка с возвращением всегда даёт ту же сумму —
    # распределение обязано схлопнуться в точку, просадки нет
    res = _fake_result([10.0] * 6)
    mc = bootstrap_trades(res, n=500, seed=0)
    assert math.isclose(mc.ret_p5, mc.ret_p95)
    assert math.isclose(mc.ret_p50, 60.0 / 1000.0)
    assert math.isclose(mc.dd_p5, 0.0)
    assert math.isclose(mc.prob_profit, 1.0)


def test_montecarlo_percentiles_ordered_and_bounds():
    res = _fake_result([10, -5, 7, -3, 12, 4, -8, 6, -2, 9])
    for mc in (bootstrap_trades(res, n=500, seed=4),
               bootstrap_returns(res, n=500, seed=4)):
        assert mc.ret_p5 <= mc.ret_p50 <= mc.ret_p95
        assert mc.dd_p5 <= mc.dd_p50 <= 0.0                # просадка не бывает > 0
        assert 0.0 <= mc.prob_profit <= 1.0
        assert mc.worst_ret <= mc.ret_p5 and mc.ret_p95 <= mc.best_ret


# ══════════════════ блок 12: DSR ══════════════════
def test_dsr_equals_psr_when_single_trial():
    # n_trials=1 → sr_star=0 → дефлировать нечего: DSR == PSR
    assert expected_max_sharpe(1) == 0.0
    sr, n = 0.12, 252
    assert math.isclose(deflated_sharpe(sr, n, 1), probabilistic_sharpe(sr, n))


def test_psr_penalizes_fat_tails_and_negative_skew():
    # игнор ненормальности — источник завышения (аудит блок 12): жирные хвосты
    # и отрицательный скос ОБЯЗАНЫ снижать уверенность при sr>0
    sr, n = 0.1, 252
    base = probabilistic_sharpe(sr, n, skew=0.0, kurt=3.0)
    assert probabilistic_sharpe(sr, n, skew=0.0, kurt=10.0) < base
    assert probabilistic_sharpe(sr, n, skew=-1.5, kurt=3.0) < base


def test_psr_guard_on_tiny_sample():
    # T<2 наблюдений: никакой уверенности, ответ — «не знаю» (0.5), не сертификация
    assert probabilistic_sharpe(0.9, 1) == 0.5
    assert probabilistic_sharpe(0.9, 0) == 0.5


# ══════════════════ детерминизм сидов ══════════════════
def test_synthetic_data_deterministic_by_seed():
    assert candles.gbm("S", bars=200, seed=3) == candles.gbm("S", bars=200, seed=3)
    assert candles.gbm("S", bars=200, seed=3) != candles.gbm("S", bars=200, seed=4)
    assert candles.basket(["A", "B"], bars=50, seed=1) == \
        candles.basket(["A", "B"], bars=50, seed=1)


def test_run_reproducible_bit_for_bit():
    data = candles.gbm("SYN", bars=250, seed=6)
    r1 = run(_SMA(), data, cash=100_000, commission=0.0005)
    r2 = run(_SMA(), data, cash=100_000, commission=0.0005)
    assert r1.equity == r2.equity
    assert len(r1.trades) == len(r2.trades)
    assert [o.fill_price for o in r1.fills] == [o.fill_price for o in r2.fills]
    r3 = run(_RandomTrader(seed=42), data, cash=100_000)
    r4 = run(_RandomTrader(seed=42), data, cash=100_000)
    assert r3.equity == r4.equity

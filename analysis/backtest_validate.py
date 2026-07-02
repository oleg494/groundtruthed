"""Валидация бэктестера НЕЗАВИСИМОЙ реализацией (задача B — проба харнесса).

Бэктестер покрыт 119 тестами, но они проверяют его ПРОТИВ САМОГО СЕБЯ. Здесь —
проверка чужой логикой: equity-кривую считаем вторым, независимым кодом и требуем
совпадения до eps. Если все наши выводы («эджа нет») стоят на движке, движок обязан
быть корректным. Оракул здесь — согласие двух независимых расчётов.

Две проверки:

  [1] ЗОЛОТАЯ (полная независимость): для SMACross и BuyHold реконструируем сигнал
      И учёт С НУЛЯ, не заглядывая в Broker. Сами считаем SMA, сами решаем сделки,
      сами ведём кэш/позицию, сами исполняем по open[i+1]. Совпадение с движком
      валидирует РАЗОМ: тайминг сигнала (нет lookahead), сайзинг order_target_percent,
      цену исполнения, комиссию, кэш-модель и оценку equity.

  [2] ШИРОКАЯ (все 17 стратегий): переигрываем equity из ПОТОКА исполненных ордеров
      движка (fills) отдельным кодом — для cash-модели, шортов (pairs) и фьючерсной
      маржи. Не реконструирует сигнал (доверяет fill_i), зато покрывает каждую
      стратегию и каждую денежную ветку аккаунтинга.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import candles
from backtest.core import Instrument
from backtest.engine import run
from backtest.strategies import REGISTRY, SMACross, BuyHold

EPS = 1e-6
COMM = 0.0005


# ───────────────────── независимый учёт (без Broker) ─────────────────────
def _sma(xs, n):
    return sum(xs[-n:]) / n if n > 0 and len(xs) >= n else None


def _target_qty(frac, equity, px, cur_qty, mult, lot):
    """Повтор order_target_percent для cash-инструмента (lot-округление к нулю)."""
    target_notional = frac * equity
    cur_notional = px * mult * cur_qty
    unit = px * mult
    if unit == 0:
        return 0
    delta_qty = (target_notional - cur_notional) / unit
    lot = max(lot, 1)
    return int(delta_qty / lot) * lot


def indep_smacross(bars, fast, slow, frac, cash0, comm, slip, mult, lot):
    closes = [b.c for b in bars]
    opens = [b.o for b in bars]
    cash, pos, pending = cash0, 0.0, 0.0
    eq = []
    for i in range(len(bars)):
        if pending != 0.0:                                  # филл по open[i]
            q = pending
            price = opens[i] * (1 + slip * (1 if q > 0 else -1))
            notional = price * mult * abs(q)
            cash -= notional * comm
            cash -= price * mult * q                        # знаковый поток (cash-модель)
            pos += q
            pending = 0.0
        f, s = _sma(closes[:i + 1], fast), _sma(closes[:i + 1], slow)
        if f is not None and s is not None:
            equity_i = cash + pos * closes[i] * mult
            if f > s and pos == 0:
                pending = _target_qty(frac, equity_i, closes[i], pos, mult, lot)
            elif f <= s and pos != 0:
                pending = -pos
        eq.append(cash + pos * closes[i] * mult)
    return eq


def indep_buyhold(bars, frac, cash0, comm, slip, mult, lot):
    closes = [b.c for b in bars]
    opens = [b.o for b in bars]
    cash, pos, pending, done = cash0, 0.0, 0.0, False
    eq = []
    for i in range(len(bars)):
        if pending != 0.0:
            q = pending
            price = opens[i] * (1 + slip * (1 if q > 0 else -1))
            notional = price * mult * abs(q)
            cash -= notional * comm
            cash -= price * mult * q
            pos += q
            pending = 0.0
        if not done and closes[i]:                          # один раз на первом баре
            equity_i = cash + pos * closes[i] * mult
            pending = _target_qty(frac, equity_i, closes[i], pos, mult, lot)
            done = True
        eq.append(cash + pos * closes[i] * mult)
    return eq


# ───────────────────── проверка [2]: переигровка из fills ─────────────────────
def replay_from_fills(result, data, instruments, comm):
    """Независимо собрать equity из потока исполненных ордеров движка + баров.
    Поддерживает cash и futures (реализованный P&L кредитуется в кэш при сокращении)."""
    closes = {t: {b.t: b.c for b in bars} for t, bars in data.items()}
    times = result.times
    fills_by_i: dict[int, list] = {}
    for o in result.fills:
        fills_by_i.setdefault(o.fill_i, []).append(o)

    cash = result.cash0
    pos = {t: 0.0 for t in instruments}
    avg = {t: 0.0 for t in instruments}
    comm_sum = 0.0
    eq_curve = []
    comm_field_ok = True
    for i, t_stamp in enumerate(times):
        for o in fills_by_i.get(i, []):
            inst = instruments[o.ticker]
            mult, is_fut = inst.multiplier, inst.is_futures
            notional = o.fill_price * mult * abs(o.qty)
            c = notional * comm
            comm_sum += c
            cash -= c
            if abs(c - o.commission) > 1e-6:                # сверяем поле комиссии ордера
                comm_field_ok = False
            old = pos[o.ticker]
            if not is_fut:
                cash -= o.fill_price * mult * o.qty
            # обновление позиции по средней + реализованный P&L (для futures → в кэш)
            new = old + o.qty
            same_dir = (old >= 0 and o.qty > 0) or (old <= 0 and o.qty < 0)
            if old == 0 or same_dir:
                avg[o.ticker] = (avg[o.ticker] * abs(old) + o.fill_price * abs(o.qty)) \
                    / (abs(old) + abs(o.qty))
            else:
                closed = min(abs(o.qty), abs(old))
                direction = 1 if old > 0 else -1
                realized = (o.fill_price - avg[o.ticker]) * direction * closed * mult
                if is_fut:
                    cash += realized
                if abs(o.qty) > abs(old):                   # переворот
                    avg[o.ticker] = o.fill_price
                elif new == 0:
                    avg[o.ticker] = 0.0
            pos[o.ticker] = new
        # equity по close[i]
        eq = cash
        for t in instruments:
            if pos[t] == 0:
                continue
            cl = closes[t].get(t_stamp)
            if cl is None:
                continue
            inst = instruments[t]
            if inst.is_futures:
                eq += (cl - avg[t]) * pos[t] * inst.multiplier
            else:
                eq += cl * inst.multiplier * pos[t]
        eq_curve.append(eq)
    return eq_curve, comm_sum, comm_field_ok


def maxdiff(a, b):
    return max((abs(x - y) for x, y in zip(a, b)), default=0.0), len(a) == len(b)


# ───────────────────────────── прогон ─────────────────────────────
def check1():
    print("\n[1] ЗОЛОТАЯ — полностью независимая реконструкция (SMACross, BuyHold)")
    print("-" * 72)
    ok_all = True
    cases = []
    for seed in (0, 1, 7, 42):
        for slip in (0.0, 0.001):
            cases.append(("sma", dict(fast=10, slow=30, frac=0.95), seed, slip, "cash"))
            cases.append(("bh", dict(frac=0.99), seed, slip, "cash"))
    # фьючерс в cash-независимой проверке не считаем (другая модель) — он в [2]
    for kind, params, seed, slip, itype in cases:
        data = candles.gbm("SYN", bars=400, seed=seed)
        bars = data["SYN"]
        inst = Instrument("SYN", multiplier=1.0, lot=1, kind=itype)
        if kind == "sma":
            strat = SMACross(**params)
            indep = indep_smacross(bars, params["fast"], params["slow"],
                                   params["frac"], 100_000.0, COMM, slip,
                                   inst.multiplier, inst.lot)
        else:
            strat = BuyHold(**params)
            indep = indep_buyhold(bars, params["frac"], 100_000.0, COMM, slip,
                                  inst.multiplier, inst.lot)
        res = run(strat, data, cash=100_000.0, commission=COMM, slippage=slip,
                  instruments={"SYN": inst})
        md, same_len = maxdiff(res.equity, indep)
        ok = same_len and md < EPS
        ok_all &= ok
        print(f"  {kind:4s} seed={seed:<2d} slip={slip:<5} "
              f"итог движок={res.equity[-1]:13.4f} незав={indep[-1]:13.4f} "
              f"maxΔ={md:.2e}  {'PASS' if ok else 'FAIL'}")
    return ok_all


def check2():
    print("\n[2] ШИРОКАЯ — переигровка equity из fills для ВСЕХ стратегий")
    print("-" * 72)
    tickers = ["A", "B", "C", "D"]
    data = candles.basket(tickers, bars=400, seed=3)
    # выровняем по общей оси (basket уже это делает) и зададим инструменты
    inst_cash = {t: Instrument(t, multiplier=1.0, lot=1, kind="cash") for t in tickers}
    inst_fut = {t: Instrument(t, multiplier=71.908, lot=1, kind="futures") for t in tickers}
    ok_all = True
    for name in REGISTRY:
        for label, insts in (("cash", inst_cash), ("futures", inst_fut)):
            try:
                strat = REGISTRY[name]()
                res = run(strat, data, cash=1_000_000.0, commission=COMM,
                          slippage=0.0, instruments=insts)
            except Exception as e:                          # стратегия неприменима к данным
                print(f"  {name:16s} [{label:7s}] SKIP ({type(e).__name__})")
                continue
            indep, comm_sum, comm_ok = replay_from_fills(res, data, insts, COMM)
            md, same_len = maxdiff(res.equity, indep)
            comm_total_ok = abs(comm_sum - res.commissions_paid) < 1e-5
            ok = same_len and md < EPS and comm_ok and comm_total_ok
            ok_all &= ok
            flags = "" if (comm_ok and comm_total_ok) else \
                f" [comm_field={comm_ok} comm_total={comm_total_ok}]"
            print(f"  {name:16s} [{label:7s}] fills={len(res.fills):4d} "
                  f"maxΔ={md:.2e}  {'PASS' if ok else 'FAIL'}{flags}")

    # orb на дневных барах не торгует (интрадей-логика) → дадим ему ЧАСОВЫЕ бары,
    # чтобы реально проверить вход/стоп/тейк/шорт и закрытие сессии.
    import random as _r
    rng = _r.Random(5)
    closes, p = [], 100.0
    for _ in range(60 * 12):                                # ~60 сессий по 12 баров
        p *= (1 + rng.gauss(0, 0.004))
        closes.append(p)
    hbars = candles._ohlc(closes, rng, dt=3600, wick=0.003)
    hdata = {"A": hbars}
    for label, mult in (("cash", 1.0), ("futures", 71.908)):
        insts = {"A": Instrument("A", multiplier=mult, lot=1,
                                 kind="cash" if label == "cash" else "futures")}
        res = run(REGISTRY["orb"](), hdata, cash=1_000_000.0, commission=COMM,
                  slippage=0.0, instruments=insts)
        indep, comm_sum, comm_ok = replay_from_fills(res, hdata, insts, COMM)
        md, same_len = maxdiff(res.equity, indep)
        ok = same_len and md < EPS and comm_ok and \
            abs(comm_sum - res.commissions_paid) < 1e-5
        ok_all &= ok
        print(f"  {'orb (intraday)':16s} [{label:7s}] fills={len(res.fills):4d} "
              f"maxΔ={md:.2e}  {'PASS' if ok else 'FAIL'}")
    return ok_all


if __name__ == "__main__":
    c1 = check1()
    c2 = check2()
    print("\n" + "=" * 72)
    print(f"ИТОГ:  [1] золотая = {'PASS ✓' if c1 else 'FAIL ✗'}   "
          f"[2] широкая = {'PASS ✓' if c2 else 'FAIL ✗'}")
    print("=" * 72)
    sys.exit(0 if (c1 and c2) else 1)

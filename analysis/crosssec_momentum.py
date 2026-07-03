"""Гипотеза №1: кросс-секционный моментум по корзине MOEX — есть ли эдж?

Идея (отличается от уже забракованного time-series SMA): ранжируем 6 бумаг корзины
по относительной силе за lookback дней, держим top-N, ребаланс раз в rebalance дней,
с фильтром абсолютного импульса (на медвежьем рынке — в кэш). В backtest/ это уже
реализовано как `dualmom` (двойной импульс Антоначчи) — пишем не стратегию, а ЧЕСТНУЮ
проверку.

Защита от самообмана (главное):
  1. Split IS/OOS: параметры смотрим на первой половине истории, ВЕРДИКТ выносим по
     второй (out-of-sample), которую стратегия «не видела».
  2. Бенчмарк — equal-weight buy&hold корзины. Моментум обязан бить пассивное держание
     ПОСЛЕ комиссий, иначе смысла в активности нет.
  3. Смотрим на УСТОЙЧИВОСТЬ по сетке lookback/top, а не на лучший прогон (лучший из
     многих — почти всегда переобучение).

Read-only, заявок не размещает. Запуск:  python analysis/crosssec_momentum.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest import candles, strategies  # noqa: E402
from backtest.engine import run  # noqa: E402
from backtest.metrics import metrics  # noqa: E402
from lab.instruments import BASKET, INSTRUMENTS  # noqa: E402

DAYS = 700
CASH = 100_000.0
COMMISSION = 0.0005
SLIPPAGE = 0.0005

# разумные, заранее заданные значения — НЕ перебор ради лучшего
LOOKBACKS = (60, 90, 120)
TOPS = (1, 2, 3)
REBALANCE = 20


def fetch_basket():
    data = {}
    for tk in BASKET:
        try:
            d = candles.from_tinvest(INSTRUMENTS[tk]["uid"], tk, days=DAYS)
            data[tk] = d[tk]
        except Exception as e:                       # noqa: BLE001
            print(f"  {tk}: фетч не удался ({e}) — пропуск")
    return align(data)


def align(data):
    """Выровнять корзину по ОБЩИМ таймстемпам: у бумаг разное число баров, и срез по
    индексу без выравнивания = срез по РАЗНЫМ датам (движок клеит по времени → каша)."""
    common = set.intersection(*[{b.t for b in bars} for bars in data.values()])
    return {t: [b for b in sorted(bars, key=lambda x: x.t) if b.t in common]
            for t, bars in data.items()}


def slice_data(data, lo, hi):
    """Срез баров [lo:hi] по всем тикерам. Данные уже выровнены — индекс == дата."""
    return {t: bars[lo:hi] for t, bars in data.items()}


def run_strat(key, params, data):
    res = run(strategies.build(key, **params), data,
              cash=CASH, commission=COMMISSION, slippage=SLIPPAGE)
    m = metrics(res)
    return {"ret": m.total_return * 100, "sharpe": m.sharpe,
            "dd": m.max_drawdown * 100, "trades": m.num_trades}


def main():
    data = fetch_basket()
    if len(data) < 3:
        print("недостаточно данных корзины"); return
    n = min(len(b) for b in data.values())
    half = n // 2
    print(f"=== Кросс-секционный моментум по корзине ({len(data)} бумаг, {n} баров) ===")
    print(f"комиссия {COMMISSION*100:.2f}% + слиппедж {SLIPPAGE*100:.2f}%; "
          f"IS=[0:{half}] OOS=[{half}:{n}]\n")

    is_data = slice_data(data, 0, half)
    oos_data = slice_data(data, half, n)

    # бенчмарк: equal-weight buy&hold на каждой половине
    bh_is = run_strat("buyhold", {}, is_data)
    bh_oos = run_strat("buyhold", {}, oos_data)
    print(f"{'бенчмарк buyhold':28} IS ret {bh_is['ret']:+7.2f}%  |  "
          f"OOS ret {bh_oos['ret']:+7.2f}%  (это планка, которую надо бить)\n")

    print(f"{'dualmom (lb/top)':28}{'IS ret':>9}{'OOS ret':>9}{'OOS Sh':>8}"
          f"{'OOS dd':>8}{'сдел':>6}  эдж OOS?")
    print("-" * 84)
    # ЧЕСТНЫЙ критерий эджа: положительная доходность OOS И положительный Sharpe.
    # «Бьёт падающий buyhold, оставаясь в минусе» — это защита кэшем, а не эдж.
    edge = []
    for lb in LOOKBACKS:
        for top in TOPS:
            p = {"lookback": lb, "rebalance": REBALANCE, "top": top}
            is_r = run_strat("dualmom", p, is_data)
            oos_r = run_strat("dualmom", p, oos_data)
            has_edge = oos_r["ret"] > 0 and oos_r["sharpe"] > 0
            edge.append(has_edge)
            verdict = "ЭДЖ" if has_edge else ("защита кэшем" if oos_r["ret"] > bh_oos["ret"] else "слив")
            print(f"  lb={lb:<3} top={top:<14}{is_r['ret']:>+9.2f}{oos_r['ret']:>+9.2f}"
                  f"{oos_r['sharpe']:>+8.2f}{oos_r['dd']:>8.2f}{oos_r['trades']:>6}  {verdict}")

    share = sum(edge) / len(edge) * 100
    print(f"\nИтог: устойчивый эдж OOS (доходность>0 И Sharpe>0) в {sum(edge)}/{len(edge)} "
          f"конфигураций ({share:.0f}%).")
    if share >= 70:
        print("→ выглядит устойчиво. СЛЕДУЮЩИЙ шаг: прогнать через backtest study "
              "(walk-forward + Deflated Sharpe), и если переживёт DSR — кандидат в ферму.")
    else:
        print("→ эджа НЕТ (положителен OOS меньше чем в 70% конфигов). dualmom лишь защищает "
              "от просадки кэшем на медвежьем рынке, но НЕ зарабатывает. Как прошлые кандидаты "
              "— без доказанного преимущества, в ферму НЕ берём.")


if __name__ == "__main__":
    main()

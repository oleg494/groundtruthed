# -*- coding: utf-8 -*-
"""Конвейер кандидата INTRADAY_TREND_FUT: внутридневной тренд на фьючерсах после полудня.

Гипотеза (зеркальная к orb_reversal): направление, подтверждённое к 11:30-13:00
(цена vs сессионный VWAP и открытие дня, ADX-фильтр), доживает до вечера.

Пайплайн на 30-мин свечах BMQ6/NGN6/GLDRUBF из кэша backtest/.cache:
grid (27 комбинаций) -> anchored walk-forward (4 окна) -> Deflated Sharpe
(n_trials=27) -> сравнение с buyhold и random на тех же данных.

Запуск: python analysis/botdev_intraday_trend_fut.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest import candles  # noqa: E402
from backtest.core import Instrument  # noqa: E402
from backtest.engine import run  # noqa: E402
from backtest.metrics import metrics  # noqa: E402
from backtest.optimize import grid_search, walk_forward  # noqa: E402
from backtest.robust import assess  # noqa: E402
from backtest.strategies import REGISTRY, build  # noqa: E402
from lab.instruments import INSTRUMENTS  # noqa: E402

CASH = 100_000.0
COMMISSION = 0.0005
SLIPPAGE = 0.0005
TICKERS = ["BMQ6", "NGN6", "GLDRUBF"]
DAYS = 120
INTERVAL = "CANDLE_INTERVAL_30_MIN"

# скромная сетка: 3*3*3 = 27 испытаний на инструмент (фиксируем для DSR)
GRID = {
    "confirm_hour": [11.5, 12.0, 13.0],
    "adx_min": [0.0, 18.0, 25.0],
    "stop_mult": [1.0, 1.5, 2.5],
}
N_TRIALS = 27


def load(ticker: str):
    uid = INSTRUMENTS[ticker]["uid"]
    data = candles.from_tinvest(uid, ticker, days=DAYS, interval=INTERVAL)
    mult = INSTRUMENTS[ticker]["point_rub"]
    inst = {ticker: Instrument(ticker, multiplier=mult, kind="futures")}
    return data, inst


def bench(name: str, data, inst, **params):
    res = run(build(name, **params), data, cash=CASH,
              commission=COMMISSION, slippage=SLIPPAGE, instruments=inst)
    return metrics(res)


def main() -> None:
    for tk in TICKERS:
        data, inst = load(tk)
        bars = data[tk]
        ndays = len({(b.t + 3 * 3600) // 86400 for b in bars})
        print(f"\n{'='*72}\n{tk}: {len(bars)} баров 30-мин, ~{ndays} сессий, "
              f"{bars[0].t}..{bars[-1].t}")

        # 1) grid search (in-sample по всей ленте — только для best_params и DSR)
        pts = grid_search(REGISTRY["intraday_trend"], data, GRID, metric="sharpe",
                          cash=CASH, commission=COMMISSION, slippage=SLIPPAGE,
                          instruments=inst)
        best = pts[0]
        bm = best.metrics
        print(f"IS best {best.params}: ret {bm.total_return*100:+.2f}%  "
              f"Sharpe {bm.sharpe:.2f}  DD {bm.max_drawdown*100:.1f}%  "
              f"trades {bm.num_trades}  PF {bm.profit_factor:.2f}  "
              f"WR {bm.win_rate*100:.0f}%")

        # 2) anchored walk-forward: 4 окна, IS расширяется, OOS нетронутый
        wf = walk_forward(REGISTRY["intraday_trend"], data, GRID, n_splits=4,
                          metric="sharpe", cash=CASH, commission=COMMISSION,
                          slippage=SLIPPAGE, instruments=inst)
        for i, w in enumerate(wf.windows):
            print(f"  WFA окно {i+1}: OOS ret {w.oos.total_return*100:+.2f}%  "
                  f"Sharpe {w.oos.sharpe:.2f}  trades {w.oos.num_trades}  "
                  f"params {w.best_params}")
        print(f"  сквозной OOS: ret {wf.oos_return()*100:+.2f}%")

        # 3) Deflated Sharpe лучшей IS-точки с поправкой на 27 испытаний
        rob = assess(best.result, pts, metric="sharpe")
        print(f"  PSR {rob.psr:.3f}  DSR {rob.deflated_sharpe:.3f} "
              f"(n_trials={rob.n_trials})")

        # 4) бенчмарки на тех же данных/костах
        bh = bench("buyhold", data, inst)
        rnd = bench("random", data, inst, seed=1)
        print(f"  buyhold: ret {bh.total_return*100:+.2f}%  Sharpe {bh.sharpe:.2f}")
        print(f"  random : ret {rnd.total_return*100:+.2f}%  Sharpe {rnd.sharpe:.2f}")


if __name__ == "__main__":
    main()

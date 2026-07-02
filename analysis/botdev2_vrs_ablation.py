"""Абляция VOL_REGIME_SWITCH: сколько «эджа» дал бы кэш-прокси, который мы НЕ начисляли.

Тот же anchored WFA, но свободному кэшу ежедневно капает rate=14.25% годовых
(как в absmom_switch). Разница со штатным прогоном = размер артефакта денежного
рынка, сознательно исключённого из вердикта (диагноз ABSMOM: кэш-прокси ≠ эдж).
Заодно печатает среднюю экспозицию пооконных best-параметров — масштаб мёртвого кэша.

Запуск:  python analysis/botdev2_vrs_ablation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest import strategies                              # noqa: E402
from backtest.engine import run, Context                     # noqa: E402
from analysis.botdev2_vol_regime_switch import (             # noqa: E402
    fetch, wfa_warm, GRID, N_SPLITS, CASH, COMMISSION, SLIPPAGE)


class VRSWithCashRate(strategies.VolRegimeSwitch):
    """То же самое + кэш-прокси absmom'а: свободный кэш под rate% годовых (252 бара)."""
    name = "vol_regime_switch_cashrate"

    def __init__(self, rate: float = 14.25, **kw):
        super().__init__(**kw)
        self.rate = rate

    def on_bar(self, ctx: Context) -> None:
        ctx._b.cash += max(0.0, ctx.cash) * self.rate / 100.0 / 252.0
        super().on_bar(ctx)


def main():
    print("=== фетч корзины (кэш) ===")
    data = fetch()
    kw = dict(cash=CASH, commission=COMMISSION, slippage=SLIPPAGE)

    print("\n=== WFA штатный (кэш мёртвый, 0%) ===")
    w0, oos0 = wfa_warm(data, GRID, N_SPLITS, **kw)
    for i, w in enumerate(w0):
        print(f"  окно {i+1}: OOS ret {w['ret']*100:+7.2f}%  params {w['params']}")
    print(f"  сквозной OOS: {oos0*100:+.2f}%")

    # средняя экспозиция best-параметров окон на полной ленте — масштаб мёртвого кэша
    for p in {tuple(sorted(w["params"].items())) for w in w0}:
        params = dict(p)
        res = run(strategies.VolRegimeSwitch(**params), data, **kw)
        tail = [e for e in res.exposure if e > 0]
        print(f"  экспозиция {params}: средняя {sum(tail)/len(tail):.2f} "
              f"(мёртвый кэш в среднем {1 - sum(tail)/len(tail):.0%})")

    print("\n=== WFA с кэш-прокси 14.25% (абляция, как absmom) ===")
    orig = strategies.VolRegimeSwitch
    strategies.VolRegimeSwitch = VRSWithCashRate          # wfa_warm берёт класс отсюда
    try:
        w1, oos1 = wfa_warm(data, GRID, N_SPLITS, **kw)
    finally:
        strategies.VolRegimeSwitch = orig
    for i, w in enumerate(w1):
        print(f"  окно {i+1}: OOS ret {w['ret']*100:+7.2f}%  params {w['params']}")
    print(f"  сквозной OOS: {oos1*100:+.2f}%")

    print(f"\nвклад кэш-прокси в сквозной OOS: {(oos1-oos0)*100:+.2f} п.п. "
          f"({oos0*100:+.2f}% → {oos1*100:+.2f}%)")


if __name__ == "__main__":
    main()

"""Капстоун: end-to-end исследование одной стратегии за один вызов.

Связывает весь пакет в честный пайплайн «как надо делать»:
  1. grid_search — найти параметры (in-sample);
  2. walk_forward — проверить на нетронутом out-of-sample (анти-оверфит);
  3. robust.assess — PSR/Deflated Sharpe с поправкой на число испытаний сетки;
  4. montecarlo — распределение итогов/просадок при ресэмплинге сделок;
  5. benchmark — alpha/beta к buy&hold.

Вывод — структура Study; report.study_html рендерит её в единый HTML-отчёт.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import strategies as _strat
from .benchmark import Comparison, compare
from .core import Bar, Instrument
from .engine import Result, run
from .metrics import Metrics
from .montecarlo import MCResult, bootstrap_trades
from .optimize import GridPoint, WalkForward, grid_search, walk_forward
from .robust import Robustness, assess, oos_degradation


@dataclass
class Study:
    strategy: str
    grid: dict
    best_params: dict
    best: Result
    best_metrics: Metrics
    grid_points: list[GridPoint]
    walkforward: WalkForward
    robustness: Robustness
    montecarlo: MCResult
    benchmark: Comparison
    benchmark_result: Result
    oos: dict
    metric: str = "sharpe"

    def verdict(self) -> str:
        """Короткий честный вердикт по совокупности проверок."""
        flags = []
        if self.robustness.deflated_sharpe < 0.5:
            flags.append("Deflated Sharpe < 50% — Sharpe вероятно артефакт перебора")
        if self.oos.get("ratio", 0) < 0.5:
            flags.append("OOS деградирует относительно IS (переоптимизация)")
        if self.montecarlo.ret_p5 < 0:
            flags.append("MC p5 < 0 — заметная вероятность убытка")
        if self.benchmark.excess_return <= 0:
            flags.append("не бьёт buy&hold по итоговой доходности")
        if not flags:
            return "✓ проверки пройдены: результат выглядит устойчивым (но это синтетика/история, не гарантия)"
        return "⚠ предупреждения:\n   - " + "\n   - ".join(flags)


def run_study(strategy_name: str, data: dict[str, list[Bar]], grid: dict[str, list],
              metric: str = "sharpe", n_splits: int = 4, mc_n: int = 2000,
              cash: float = 100_000.0, commission: float = 0.0005,
              slippage: float = 0.0,
              instruments: Optional[dict[str, Instrument]] = None) -> Study:
    cls = _strat.REGISTRY[strategy_name]
    pts = grid_search(cls, data, grid, metric=metric, cash=cash,
                      commission=commission, slippage=slippage, instruments=instruments)
    if not pts:
        raise ValueError("сетка не дала валидных комбинаций")
    best = pts[0]
    res = best.result
    wf = walk_forward(cls, data, grid, n_splits=n_splits, metric=metric, cash=cash,
                      commission=commission, slippage=slippage, instruments=instruments)
    rob = assess(res, pts, metric=metric)
    mc = bootstrap_trades(res, n=mc_n)
    bh = run(_strat.BuyHold(), data, cash=cash, commission=commission,
             slippage=slippage, instruments=instruments)
    cmp = compare(res, bh)
    return Study(
        strategy=strategy_name, grid=grid, best_params=best.params, best=res,
        best_metrics=best.metrics, grid_points=pts, walkforward=wf,
        robustness=rob, montecarlo=mc, benchmark=cmp, benchmark_result=bh,
        oos=oos_degradation(wf, metric), metric=metric)


def text_study(study: Study) -> str:
    """Текстовая сводка исследования для консоли."""
    from .report import text_report
    L = [text_report(study.best, study.best_metrics), ""]
    L.append(f"Лучшие параметры (in-sample по {study.metric}): {study.best_params}")
    L.append("")
    L.append("walk-forward (OOS):")
    for i, w in enumerate(study.walkforward.windows):
        L.append(f"  окно {i+1}: OOS ret {w.oos.total_return*100:+.2f}%  "
                 f"Sharpe {w.oos.sharpe:.2f}  параметры {w.best_params}")
    L.append(f"  сквозная OOS-доходность: {study.walkforward.oos_return()*100:+.2f}%")
    L.append(f"  деградация IS→OOS: ratio={study.oos['ratio']:.2f}")
    L.append("")
    L.append(study.robustness.summary())
    L.append("")
    L.append(study.montecarlo.summary())
    L.append("")
    L.append(study.benchmark.summary())
    L.append("")
    L.append("ВЕРДИКТ: " + study.verdict())
    return "\n".join(L)

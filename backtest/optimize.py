"""Подбор параметров: сеточный перебор и walk-forward (IS/OOS).

Walk-forward — главный честный тест: параметры подбираются на in-sample окне,
а оцениваются на следующем out-of-sample, которого оптимизатор не видел. Сшитая
OOS-кривая показывает, как стратегия вела бы себя «вперёд», без подгонки.
"""
from __future__ import annotations

import itertools
import random
from dataclasses import dataclass
from typing import Callable, Optional

from .core import Bar, Instrument
from .engine import Result, Strategy, run
from .metrics import Metrics, metrics


@dataclass
class GridPoint:
    params: dict
    metrics: Metrics
    result: Result


def _score(m: Metrics, metric: str) -> float:
    val = getattr(m, metric)
    return -1e18 if val != val else val   # NaN → в самый низ


def _obj(m: Metrics, metric: str, objective: Optional[Callable] = None) -> float:
    """Значение цели: либо callable(Metrics)->float, либо атрибут metric.

    objective позволяет оптимизировать композит — например штрафовать частые сделки:
        objective=lambda m: m.sharpe - 0.01 * m.num_trades
    или цель, которой нет среди полей Metrics."""
    if objective is not None:
        v = objective(m)
        return -1e18 if v != v else v
    return _score(m, metric)


def grid_search(strategy_cls: type[Strategy], data: dict[str, list[Bar]],
                param_grid: dict[str, list], metric: str = "sharpe",
                instruments: Optional[dict[str, Instrument]] = None,
                objective: Optional[Callable] = None,
                **run_kw) -> list[GridPoint]:
    """Перебрать декартово произведение param_grid, вернуть точки по убыванию цели.

    Цель — атрибут metric (по умолчанию) ИЛИ callable objective(Metrics)->float."""
    keys = list(param_grid)
    points: list[GridPoint] = []
    for combo in itertools.product(*(param_grid[k] for k in keys)):
        params = dict(zip(keys, combo))
        try:
            strat = strategy_cls(**params)
        except (AssertionError, TypeError, ValueError):
            continue                       # несовместимая комбинация (напр. fast>=slow)
        res = run(strat, data, instruments=instruments, **run_kw)
        points.append(GridPoint(params, metrics(res), res))
    points.sort(key=lambda p: _obj(p.metrics, metric, objective), reverse=True)
    return points


# ───────────────────────── walk-forward ─────────────────────────
def _union_times(data: dict[str, list[Bar]]) -> list[int]:
    return sorted({b.t for bars in data.values() for b in bars})


def _slice(data: dict[str, list[Bar]], t0: int, t1: int) -> dict[str, list[Bar]]:
    """Подмножество данных по полуинтервалу времени [t0, t1)."""
    return {tk: [b for b in bars if t0 <= b.t < t1] for tk, bars in data.items()}


@dataclass
class WFWindow:
    is_range: tuple[int, int]
    oos_range: tuple[int, int]
    best_params: dict
    is_metric: float
    oos: Metrics
    oos_result: Result


@dataclass
class WalkForward:
    windows: list[WFWindow]
    equity: list[float]            # сшитая OOS equity-кривая (компаундинг по окнам)
    times: list[int]
    metric: str

    def oos_return(self) -> float:
        return self.equity[-1] / self.equity[0] - 1.0 if len(self.equity) > 1 else 0.0


def random_search(strategy_cls: type[Strategy], data: dict[str, list[Bar]],
                  param_grid: dict[str, list], n_samples: int = 30,
                  metric: str = "sharpe", seed: int = 0,
                  instruments: Optional[dict[str, Instrument]] = None,
                  objective: Optional[Callable] = None,
                  **run_kw) -> list[GridPoint]:
    """Случайная выборка n_samples комбинаций из пространства параметров (без полного перебора).

    Дешевле сетки при многих параметрах и часто не хуже находит хорошие зоны."""
    rng = random.Random(seed)
    keys = list(param_grid)
    seen, points = set(), []
    attempts = 0
    while len(points) < n_samples and attempts < n_samples * 20:
        attempts += 1
        combo = tuple(rng.choice(param_grid[k]) for k in keys)
        if combo in seen:
            continue
        seen.add(combo)
        params = dict(zip(keys, combo))
        try:
            strat = strategy_cls(**params)
        except (AssertionError, TypeError, ValueError):
            continue
        res = run(strat, data, instruments=instruments, **run_kw)
        points.append(GridPoint(params, metrics(res), res))
    points.sort(key=lambda p: _obj(p.metrics, metric, objective), reverse=True)
    return points


@dataclass
class RobustPick:
    params: dict
    neighborhood_score: float       # средняя метрика по комбинации и её соседям
    own_score: float
    n_neighbors: int


def robust_select(grid_points: list[GridPoint], param_grid: dict[str, list],
                  metric: str = "sharpe") -> RobustPick:
    """Выбрать параметры по среднему метрики в ОКРЕСТНОСТИ, а не по одиночному пику.

    Изолированный максимум на сетке — классический признак переоптимизации: рядом
    провал. Берём комбинацию, у которой высока средняя метрика вместе с соседями
    (значения параметров, смежные по сетке) — такое плато устойчивее."""
    keys = list(param_grid)
    axes = {k: sorted(set(param_grid[k])) for k in keys}
    lookup = {tuple(p.params[k] for k in keys): _score(p.metrics, metric)
              for p in grid_points}
    best: Optional[RobustPick] = None
    for combo, own in lookup.items():
        vals = [own]
        for ai, k in enumerate(keys):
            ax = axes[k]
            try:
                pos = ax.index(combo[ai])
            except ValueError:
                continue
            for npos in (pos - 1, pos + 1):
                if 0 <= npos < len(ax):
                    nb = list(combo)
                    nb[ai] = ax[npos]
                    v = lookup.get(tuple(nb))
                    if v is not None:
                        vals.append(v)
        score = sum(vals) / len(vals)
        if best is None or score > best.neighborhood_score:
            best = RobustPick(dict(zip(keys, combo)), score, own, len(vals) - 1)
    return best or RobustPick({}, 0.0, 0.0, 0)


def cost_sensitivity(strategy_factory, data: dict[str, list[Bar]],
                     commissions=(0.0, 0.0005, 0.001, 0.002),
                     slippages=(0.0, 0.0005, 0.001),
                     instruments: Optional[dict[str, Instrument]] = None,
                     cash: float = 100_000.0) -> list[dict]:
    """Прогнать стратегию при разных издержках → таблица деградации.

    strategy_factory — callable БЕЗ аргументов, возвращающий свежий экземпляр стратегии
    (стратегии держат состояние, переиспользовать нельзя). Возвращает список словарей
    {commission, slippage, total_return, sharpe, max_drawdown, num_trades}."""
    out = []
    for comm in commissions:
        for slip in slippages:
            res = run(strategy_factory(), data, cash=cash, commission=comm,
                      slippage=slip, instruments=instruments)
            m = metrics(res)
            out.append({"commission": comm, "slippage": slip,
                        "total_return": m.total_return, "sharpe": m.sharpe,
                        "max_drawdown": m.max_drawdown, "num_trades": m.num_trades})
    return out


def walk_forward(strategy_cls: type[Strategy], data: dict[str, list[Bar]],
                 param_grid: dict[str, list], n_splits: int = 4,
                 metric: str = "sharpe", cash: float = 100_000.0,
                 instruments: Optional[dict[str, Instrument]] = None,
                 objective: Optional[Callable] = None,
                 **run_kw) -> WalkForward:
    """Anchored walk-forward: IS расширяется, OOS — следующий нетронутый кусок.

    Лента делится на n_splits+1 равных сегментов. На шаге k (k=1..n_splits):
    IS = [начало, k*seg), OOS = [k*seg, (k+1)*seg). На IS перебираем сетку, лучший
    набор гоняем на OOS. OOS-кривые компаундятся в одну сквозную.
    """
    times = _union_times(data)
    n = len(times)
    if n < (n_splits + 1) * 5:
        raise ValueError(f"мало баров ({n}) для {n_splits} окон walk-forward")
    seg = n // (n_splits + 1)
    windows: list[WFWindow] = []
    eq_curve: list[float] = [cash]
    eq_times: list[int] = [times[0]]
    capital = cash

    for k in range(1, n_splits + 1):
        is_lo, is_hi = times[0], times[k * seg]
        oos_lo, oos_hi = times[k * seg], (times[(k + 1) * seg] if (k + 1) * seg < n
                                          else times[-1] + 1)
        is_data = _slice(data, is_lo, is_hi)
        oos_data = _slice(data, oos_lo, oos_hi)
        pts = grid_search(strategy_cls, is_data, param_grid, metric=metric,
                          cash=cash, instruments=instruments, objective=objective, **run_kw)
        if not pts:
            continue
        best = pts[0]
        oos_res = run(strategy_cls(**best.params), oos_data, cash=capital,
                      instruments=instruments, **run_kw)
        oos_m = metrics(oos_res)
        # компаундинг: продолжаем сквозную кривую от текущего капитала
        for t, e in zip(oos_res.times, oos_res.equity):
            eq_curve.append(e)
            eq_times.append(t)
        capital = oos_res.final_equity
        windows.append(WFWindow(
            is_range=(is_lo, is_hi), oos_range=(oos_lo, oos_hi),
            best_params=best.params, is_metric=_obj(best.metrics, metric, objective),
            oos=oos_m, oos_result=oos_res))
    return WalkForward(windows=windows, equity=eq_curve, times=eq_times, metric=metric)

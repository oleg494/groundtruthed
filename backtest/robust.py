"""Робастность результата: дефляция Sharpe под множественное тестирование,
чувствительность к параметрам, деградация IS→OOS.

Главная ловушка бэктеста — переоптимизация: перебрав 200 комбинаций, легко найти
красивый Sharpe, который ничего не значит. Probabilistic/Deflated Sharpe (Bailey &
López de Prado) отвечает на вопрос «насколько вероятно, что Sharpe > 0 не случайно,
с поправкой на число испытаний».
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

_N = NormalDist()
_EULER = 0.5772156649015329


def probabilistic_sharpe(sr: float, n_obs: int, sr_benchmark: float = 0.0,
                         skew: float = 0.0, kurt: float = 3.0) -> float:
    """PSR: вероятность, что истинный Sharpe > sr_benchmark. sr — за бар (не годовой).

    Поправка на ненормальность доходностей через skew/kurt (kurt=3 — нормальное)."""
    if n_obs < 2:
        return 0.5
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr * sr))
    z = (sr - sr_benchmark) * math.sqrt(n_obs - 1) / denom
    return _N.cdf(z)


def expected_max_sharpe(n_trials: int, sr_std: float = 1.0) -> float:
    """Ожидаемый максимум Sharpe из n_trials независимых испытаний под нулём (E[max])."""
    if n_trials < 2:
        return 0.0
    a = _N.inv_cdf(1 - 1.0 / n_trials)
    b = _N.inv_cdf(1 - 1.0 / (n_trials * math.e))
    return sr_std * ((1 - _EULER) * a + _EULER * b)


def deflated_sharpe(sr: float, n_obs: int, n_trials: int,
                    sr_std: float = 1.0, skew: float = 0.0, kurt: float = 3.0) -> float:
    """DSR: PSR с порогом = ожидаемый максимум Sharpe из n_trials испытаний.

    Близко к 1 → результат вряд ли артефакт перебора; около 0.5 и ниже → скорее удача."""
    sr_star = expected_max_sharpe(n_trials, sr_std)
    return probabilistic_sharpe(sr, n_obs, sr_benchmark=sr_star, skew=skew, kurt=kurt)


def _bar_sharpe(res) -> tuple[float, int, float, float]:
    """Sharpe за бар, число баров, скос и эксцесс доходностей equity."""
    eq = res.equity
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1]]
    n = len(rets)
    if n < 2:
        return 0.0, n, 0.0, 3.0
    m = sum(rets) / n
    var = sum((r - m) ** 2 for r in rets) / n
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0, n, 0.0, 3.0
    skew = sum((r - m) ** 3 for r in rets) / n / sd ** 3
    kurt = sum((r - m) ** 4 for r in rets) / n / sd ** 4
    return m / sd, n, skew, kurt


@dataclass
class Robustness:
    sharpe_bar: float
    psr: float
    deflated_sharpe: float
    n_trials: int
    sens_mean: float
    sens_std: float
    sens_frac_positive: float
    sens_robustness: float          # mean/std метрики по сетке (чем выше, тем стабильнее)

    def summary(self) -> str:
        return (f"Robustness:\n"
                f"  PSR (SR>0)           = {self.psr*100:.1f}%\n"
                f"  Deflated SR (×{self.n_trials} исп.) = {self.deflated_sharpe*100:.1f}%\n"
                f"  чувствительность: метрика по сетке mean={self.sens_mean:.2f} "
                f"std={self.sens_std:.2f} (>0 у {self.sens_frac_positive*100:.0f}%), "
                f"robustness={self.sens_robustness:.2f}")


def assess(result, grid_points=None, metric: str = "sharpe") -> Robustness:
    """Оценить робастность одного прогона + (опц.) набора grid-точек как испытаний."""
    sr_bar, n_obs, skew, kurt = _bar_sharpe(result)
    psr = probabilistic_sharpe(sr_bar, n_obs, 0.0, skew, kurt)
    sr_std = 1.0
    if grid_points:
        vals = [getattr(p.metrics, metric) for p in grid_points]
        vals = [v for v in vals if v == v and abs(v) != math.inf]
        n_trials = max(len(vals), 1)
        mean = sum(vals) / n_trials if vals else 0.0
        std = (sum((v - mean) ** 2 for v in vals) / n_trials) ** 0.5 if vals else 0.0
        frac_pos = sum(1 for v in vals if v > 0) / n_trials if vals else 0.0
        robustness = mean / std if std else 0.0
        # Корректная спецификация DSR: expected_max_sharpe ожидает sr_std в тех же
        # ПОБАРОВЫХ единицах Sharpe, что и sr_bar — это разброс побарового SR ПО
        # точкам сетки (испытаниям). Дефолт sr_std=1.0 (в побаровых единицах ≈ в 16×
        # больше реального ~1/√n) делал sr_star огромным → DSR≈0 даже у реального
        # скилла. Считаем разброс из самих прогонов сетки.
        sr_bars = [_bar_sharpe(p.result)[0] for p in grid_points]
        if len(sr_bars) > 1:
            mu_sr = sum(sr_bars) / len(sr_bars)
            sr_std = (sum((x - mu_sr) ** 2 for x in sr_bars) / len(sr_bars)) ** 0.5
    else:
        n_trials, mean, std, frac_pos, robustness = 1, sr_bar, 0.0, 1.0, 0.0
    dsr = deflated_sharpe(sr_bar, n_obs, n_trials, sr_std=sr_std, skew=skew, kurt=kurt)
    return Robustness(sharpe_bar=sr_bar, psr=psr, deflated_sharpe=dsr,
                      n_trials=n_trials, sens_mean=mean, sens_std=std,
                      sens_frac_positive=frac_pos, sens_robustness=robustness)


def oos_degradation(wf, metric: str = "sharpe") -> dict:
    """Деградация IS→OOS по окнам walk-forward. Близко к 1 — устойчиво, ≪1 — переобучение."""
    is_vals = [w.is_metric for w in wf.windows]
    oos_vals = [getattr(w.oos, metric) for w in wf.windows]
    is_avg = sum(is_vals) / len(is_vals) if is_vals else 0.0
    oos_avg = sum(oos_vals) / len(oos_vals) if oos_vals else 0.0
    return {"is_avg": is_avg, "oos_avg": oos_avg,
            "ratio": (oos_avg / is_avg) if is_avg else 0.0,
            "windows": len(wf.windows)}

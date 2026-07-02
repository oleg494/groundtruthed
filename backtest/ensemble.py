"""Ансамбль стратегий: портфель из нескольких прогонов с аллокацией капитала.

Часто один сигнал нестабилен, а несколько слабо коррелированных вместе дают более
гладкую кривую. Комбинируем на уровне equity: каждой стратегии выделяется доля w_i
капитала, дальше «рукава» растут независимо (статическая аллокация, buy&hold рукавов).
Результат — обычный Result, поэтому к нему применимы все метрики/отчёты/риск-анализ.
"""
from __future__ import annotations

from typing import Optional

from .engine import Result


def combine_equity(results: list[Result], weights: Optional[list[float]] = None,
                   total_cash: Optional[float] = None,
                   name: str = "ensemble") -> Result:
    """Скомбинировать прогоны в один портфель. weights нормируются к сумме 1.

    Комбинируется по общим меткам времени: combined[t] = Σ w_i · cash · (eq_i[t]/cash0_i).
    Сделки и филлы — объединение (для статистики), комиссии — сумма по рукавам."""
    if not results:
        raise ValueError("нет результатов для ансамбля")
    n = len(results)
    w = weights or [1.0 / n] * n
    s = sum(w) or 1.0
    w = [x / s for x in w]
    cash = total_cash if total_cash is not None else (results[0].cash0 or 100_000.0)

    # общая лента времени
    eqs = [dict(zip(r.times, r.equity)) for r in results]
    common = sorted(set(eqs[0]).intersection(*[set(e) for e in eqs[1:]])) if n > 1 \
        else list(results[0].times)
    times, equity, exposure = [], [], []
    for t in common:
        val = 0.0
        for i, r in enumerate(results):
            base = r.cash0 or 1.0
            val += w[i] * cash * (eqs[i][t] / base)
        times.append(t)
        equity.append(val)
    # экспозиция как взвешенная по рукавам (по индексу бара, грубо)
    for bi in range(len(times)):
        e = 0.0
        for i, r in enumerate(results):
            if bi < len(r.exposure):
                e += w[i] * r.exposure[bi]
        exposure.append(e)

    trades = [t for r in results for t in r.trades]
    fills = [f for r in results for f in r.fills]
    params = {r.strategy: round(w[i], 3) for i, r in enumerate(results)}
    return Result(
        strategy=name, params=params, times=times, equity=equity, cash0=cash,
        trades=trades, fills=fills, exposure=exposure,
        commissions_paid=sum(r.commissions_paid for r in results),
        data_tickers=sorted({tk for r in results for tk in r.data_tickers}),
        bars=len(times))


def _aligned_return_series(results: list[Result]) -> tuple[list[str], list[list[float]]]:
    """Баровые доходности по ОБЩИМ меткам времени для каждого рукава."""
    eqs = [dict(zip(r.times, r.equity)) for r in results]
    common = sorted(set(eqs[0]).intersection(*[set(e) for e in eqs[1:]])) \
        if len(results) > 1 else list(results[0].times)
    series = []
    for e in eqs:
        rets = [e[common[i]] / e[common[i - 1]] - 1.0
                for i in range(1, len(common)) if e[common[i - 1]]]
        series.append(rets)
    labels = [r.strategy for r in results]
    return labels, series


def correlation_matrix(results: list[Result]) -> tuple[list[str], list[list[float]]]:
    """Матрица корреляций баровых доходностей рукавов. Низкая корреляция → лучше диверсификация."""
    labels, series = _aligned_return_series(results)
    n = len(series)
    m = min((len(s) for s in series), default=0)
    series = [s[:m] for s in series]
    mat = [[1.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            a, b = series[i], series[j]
            if len(a) < 2:
                c = 0.0
            else:
                ma, mb = sum(a) / len(a), sum(b) / len(b)
                cov = sum((a[k] - ma) * (b[k] - mb) for k in range(len(a)))
                sa = sum((x - ma) ** 2 for x in a) ** 0.5
                sb = sum((x - mb) ** 2 for x in b) ** 0.5
                c = (cov / (sa * sb)) if sa and sb else 0.0
            mat[i][j] = mat[j][i] = c
    return labels, mat


def correlation_text(results: list[Result]) -> str:
    """Текстовая матрица корреляций + средняя попарная (мера независимости рукавов)."""
    labels, mat = correlation_matrix(results)
    short = [l[:8] for l in labels]
    L = ["Корреляция рукавов (баровые доходности):",
         "          " + "".join(f"{s:>9}" for s in short)]
    for i, lab in enumerate(short):
        L.append(f"  {lab:<8}" + "".join(f"{mat[i][j]:>9.2f}" for j in range(len(labels))))
    n = len(labels)
    pairs = [mat[i][j] for i in range(n) for j in range(i + 1, n)]
    if pairs:
        L.append(f"  средняя попарная корреляция: {sum(pairs)/len(pairs):.2f} "
                 f"(чем ниже, тем сильнее диверсификация)")
    return "\n".join(L)


def risk_parity_weights(results: list[Result]) -> list[float]:
    """Веса ∝ 1/волатильность каждого рукава (грубый risk parity по баровым доходностям)."""
    inv = []
    for r in results:
        eq = r.equity
        rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1]]
        if len(rets) < 2:
            inv.append(0.0)
            continue
        m = sum(rets) / len(rets)
        sd = (sum((x - m) ** 2 for x in rets) / len(rets)) ** 0.5
        inv.append((1.0 / sd) if sd else 0.0)
    s = sum(inv)
    return [x / s for x in inv] if s else [1.0 / len(results)] * len(results)

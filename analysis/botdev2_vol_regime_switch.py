"""Кандидат VOL_REGIME_SWITCH (вторая волна): vol-таргетирование buyhold-корзины.

Инверсия диагноза ABSMOM_SWITCH: переключение экспозиции не по знаку доходности
(и без кэш-прокси!), а по режиму волатильности: позиция = target_vol / RV(N), cap 1.0.
Свободный кэш лежит МЁРТВЫМ — консервативно, кэш-прокси-артефакт absmom исключён.

ЧЕСТНОЕ сравнение: бенчмарк — тот же равновзвешенный buyhold на той же корзине.
Критерий survive: сквозной OOS>0, DSR>0.5, Sharpe И maxDD улучшены против buyhold
при сопоставимой доходности, OOS-устойчивость (Sharpe лучше buyhold в большинстве
окон), и бьёт random по Sharpe.

Полный конвейер: фетч дневных свечей (sandbox, кэш) → grid (in-sample) →
anchored walk-forward c warm-up → Deflated Sharpe → пооконное сравнение с buyhold.
Read-only, заявок не размещает. Запуск:  python analysis/botdev2_vol_regime_switch.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest import candles, strategies  # noqa: E402
from backtest.engine import run  # noqa: E402
from backtest.metrics import metrics  # noqa: E402
from backtest.optimize import grid_search  # noqa: E402
from backtest.robust import assess  # noqa: E402
from lab.instruments import INSTRUMENTS  # noqa: E402

DAYS = 1000            # календарных; старт ~2023-10 — февраль-2022 не пересекаем
CASH = 100_000.0
COMMISSION = 0.0005
SLIPPAGE = 0.0005

# та же корзина 11 бумаг, что у xsec_momentum (кэш тёплый)
EXTRA = {
    "NVTK": "0da66728-6c30-44c4-9264-df8fac2467ee",
    "MGNT": "ca845f68-6c43-44bc-b584-330d2a1e5eb7",
    "TATN": "88468f6c-c67a-4fb4-a006-53eed803883c",
    "VTBR": "8e2b0325-0292-4654-8a18-4f63ed3b0e09",
}
UIDS = {t: m["uid"] for t, m in INSTRUMENTS.items() if m["kind"] == "share"}
UIDS.update(EXTRA)

# скромная сетка: 4×4×2 = 32 испытания (фиксируем для DSR); band/frac не перебираем
GRID = {"target_vol": [0.10, 0.15, 0.20, 0.25],
        "lookback": [10, 20, 40, 60],
        "rebalance": [5, 21]}
N_TRIALS = 4 * 4 * 2
N_SPLITS = 4


def fetch():
    data = {}
    for tk, uid in UIDS.items():
        try:
            d = candles.from_tinvest(uid, tk, days=DAYS)
            data[tk] = d[tk]
            print(f"  {tk}: {len(d[tk])} баров")
        except Exception as e:                               # noqa: BLE001
            print(f"  {tk}: фетч не удался ({e}) — пропуск")
    common = set.intersection(*[{b.t for b in bars} for bars in data.values()])
    return {t: [b for b in sorted(bars, key=lambda x: x.t) if b.t in common]
            for t, bars in data.items()}


def seg_metrics(res, t_lo):
    """Метрики участка equity-кривой от первой метки >= t_lo: ret, Sharpe, maxDD."""
    i0 = next(i for i, tt in enumerate(res.times) if tt >= t_lo)
    eq = res.equity[i0:]
    ret = eq[-1] / eq[0] - 1.0
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1]]
    mu = sum(rets) / len(rets) if rets else 0.0
    sd = (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5 if rets else 0.0
    sh = (mu / sd * 252 ** 0.5) if sd else 0.0
    peak, dd = eq[0], 0.0
    for e in eq:
        peak = max(peak, e)
        dd = min(dd, e / peak - 1.0)
    return ret, sh, dd


def wfa_warm(data, grid, n_splits, **kw):
    """Anchored WFA с прогревом (как в xsec-конвейере): best-параметры с IS гонятся
    на [oos_lo − warm, oos_hi), метрики — только по OOS-участку. Buyhold на том же
    OOS-участке — пооконный бенчмарк."""
    times = sorted({b.t for bars in data.values() for b in bars})
    n = len(times)
    seg = n // (n_splits + 1)
    out = []
    for k in range(1, n_splits + 1):
        is_hi = times[k * seg]
        oos_hi = times[(k + 1) * seg] if (k + 1) * seg < n else times[-1] + 1
        is_data = {t: [b for b in bars if b.t < is_hi] for t, bars in data.items()}
        pts = grid_search(strategies.VolRegimeSwitch, is_data, grid,
                          metric="sharpe", **kw)
        best = pts[0]
        warm = best.params["lookback"] + 5
        lo_idx = max(0, k * seg - warm)
        run_data = {t: [b for b in bars if times[lo_idx] <= b.t < oos_hi]
                    for t, bars in data.items()}
        res = run(strategies.VolRegimeSwitch(**best.params), run_data, **kw)
        ret, sh, dd = seg_metrics(res, is_hi)
        # buyhold на том же OOS-срезе (без прогрева — ему не нужен)
        oos_data = {t: [b for b in bars if is_hi <= b.t < oos_hi]
                    for t, bars in data.items()}
        bh_res = run(strategies.build("buyhold"), oos_data, **kw)
        bret, bsh, bdd = seg_metrics(bh_res, is_hi)
        out.append({"params": best.params, "is_sharpe": best.metrics.sharpe,
                    "ret": ret, "sharpe": sh, "dd": dd,
                    "bh_ret": bret, "bh_sharpe": bsh, "bh_dd": bdd})
    total = 1.0
    for w in out:
        total *= 1 + w["ret"]
    return out, total - 1.0


def show(tag, m):
    print(f"{tag:34} ret {m.total_return*100:+8.2f}%  Sharpe {m.sharpe:+6.2f}  "
          f"dd {m.max_drawdown*100:7.2f}%  PF {m.profit_factor:5.2f}  "
          f"сделок {m.num_trades}")


def main():
    print("=== фетч корзины ===")
    data = fetch()
    n = min(len(b) for b in data.values())
    import datetime
    ts = data[next(iter(data))]
    t0 = datetime.datetime.utcfromtimestamp(min(b.t for b in ts))
    t1 = datetime.datetime.utcfromtimestamp(max(b.t for b in ts))
    print(f"корзина: {len(data)} бумаг × {n} общих баров ({t0:%Y-%m-%d} … {t1:%Y-%m-%d})\n")

    kw = dict(cash=CASH, commission=COMMISSION, slippage=SLIPPAGE)

    # 1. grid in-sample (полная лента — для DSR и ориентира; вердикт по WFA)
    print(f"=== grid {GRID} → {N_TRIALS} испытаний ===")
    pts = grid_search(strategies.VolRegimeSwitch, data, GRID, metric="sharpe", **kw)
    for p in pts[:5]:
        show(str(p.params), p.metrics)
    best = pts[0]

    # мёртвый кэш: средняя экспозиция и средняя доля свободного кэша (ставка НЕ капает)
    exp_tail = [e for e in best.result.exposure if e > 0]
    avg_expo = sum(exp_tail) / len(exp_tail) if exp_tail else 0.0
    print(f"\nсредняя экспозиция best (после входа): {avg_expo:.2f} "
          f"→ в среднем {1-avg_expo:.0%} капитала лежит МЁРТВЫМ кэшем (0%, без прокси)")

    # 2. anchored walk-forward с warm-up + пооконный buyhold
    print(f"\n=== anchored walk-forward (warm-up), {N_SPLITS} окон ===")
    windows, oos_total = wfa_warm(data, GRID, N_SPLITS, **kw)
    better_sh = better_dd = 0
    for i, w in enumerate(windows):
        d_sh = w["sharpe"] - w["bh_sharpe"]
        d_dd = w["dd"] - w["bh_dd"]
        better_sh += d_sh > 0
        better_dd += d_dd > 0
        print(f"  окно {i+1}: OOS ret {w['ret']*100:+7.2f}% Sharpe {w['sharpe']:+5.2f} "
              f"dd {w['dd']*100:6.2f}% | buyhold ret {w['bh_ret']*100:+7.2f}% "
              f"Sharpe {w['bh_sharpe']:+5.2f} dd {w['bh_dd']*100:6.2f}% | "
              f"ΔSharpe {d_sh:+.2f} Δdd {d_dd*100:+.2f}пп | params {w['params']}")
    print(f"  сквозной OOS: {oos_total*100:+.2f}%  |  Sharpe лучше buyhold: "
          f"{better_sh}/{len(windows)} окон, dd мельче: {better_dd}/{len(windows)}")

    # 3. Deflated Sharpe (испытания = 32 точки сетки)
    rob = assess(best.result, pts, metric="sharpe")
    print(f"\n=== робастность ===\n{rob.summary()}")

    # 4. бенчмарки на той же корзине (главный — buyhold БЕЗ таргетирования)
    print("\n=== бенчмарки (полная лента) ===")
    bh_m = metrics(run(strategies.build("buyhold"), data, **kw))
    rnd_m = metrics(run(strategies.build("random", seed=1), data, **kw))
    show("buyhold (та же корзина)", bh_m)
    show("random(seed=1)", rnd_m)
    show(f"best IS {best.params}", best.metrics)

    # 5. вердикт: критерии кандидата (Sharpe и maxDD лучше buyhold при сопоставимом
    # ret) + общие (OOS>0, DSR>0.5, бьёт random)
    m = best.metrics
    oos_pos = oos_total > 0
    dsr_ok = rob.deflated_sharpe > 0.5
    sh_better = m.sharpe > bh_m.sharpe
    dd_better = m.max_drawdown > bh_m.max_drawdown        # dd отрицательные: > = мельче
    ret_comp = m.total_return >= 0.8 * bh_m.total_return if bh_m.total_return > 0 \
        else m.total_return >= bh_m.total_return
    beats_rnd = m.sharpe > rnd_m.sharpe
    oos_stable = better_sh * 2 >= len(windows)            # Sharpe лучше bh в ≥половине окон
    print(f"\nOOS>0: {oos_pos} ({oos_total*100:+.2f}%)   DSR>0.5: {dsr_ok} "
          f"({rob.deflated_sharpe:.3f})")
    print(f"vs buyhold: Sharpe лучше: {sh_better} ({m.sharpe:+.2f} vs {bh_m.sharpe:+.2f})   "
          f"dd мельче: {dd_better} ({m.max_drawdown*100:.2f}% vs {bh_m.max_drawdown*100:.2f}%)   "
          f"ret сопоставим (>=80% bh): {ret_comp} ({m.total_return*100:+.2f}% vs "
          f"{bh_m.total_return*100:+.2f}%)")
    print(f"бьёт random по Sharpe: {beats_rnd}   OOS-устойчивость (ΔSharpe>0 в ≥1/2 окон): "
          f"{oos_stable} ({better_sh}/{len(windows)})")
    survive = all([oos_pos, dsr_ok, sh_better, dd_better, ret_comp, beats_rnd, oos_stable])
    print("ВЕРДИКТ:", "SURVIVE — кандидат в ферму" if survive else "KILL — эдж не доказан")


if __name__ == "__main__":
    main()

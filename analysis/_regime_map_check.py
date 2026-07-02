# -*- coding: utf-8 -*-
"""Временный проверочный скрипт для синтеза deep/REGIME_MAP_2026-07.md.

1) Пересчёт долей режимов из regime_timeline.json (сверка со summary).
2) Крупные эпизоды (>=15 баров) с датами и ценами — для хронологии.
3) Матрица переходов между режимами (персистентность) — для раздела предсказуемости.
4) Независимый перерасчёт 2 ячеек матрицы (SBER TREND_DOWN buyhold; SBER TREND_UP
   trend_follow) из timeline-режимов + свечей кэша — сверка с
   regime_conditional_returns.json.
5) Перепроверка одной строки индикаторов (SBER, случайная дата) через
   backtest.indicators.
"""
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backtest import candles                     # noqa: E402
from backtest.indicators import adx, hurst, sma  # noqa: E402

TL = json.loads((ROOT / "analysis" / "regime_timeline.json").read_text(encoding="utf-8"))
CR = json.loads((ROOT / "analysis" / "regime_conditional_returns.json").read_text(encoding="utf-8"))
REGIMES = ("TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL", "NEUTRAL")

# ── 1. Пересчёт долей ────────────────────────────────────────────────────────
print("=== 1. Пересчёт долей режимов из сырых баров JSON ===")
for tk, rows in TL["instruments"].items():
    n = len(rows)
    c = Counter(r["regime"] for r in rows)
    mine = {rg: round(c.get(rg, 0) / n * 100, 1) for rg in REGIMES}
    ref = TL["summary"][tk]["shares_pct"]
    ok = all(abs(mine[rg] - ref[rg]) < 0.05 for rg in REGIMES)
    print(f"  {tk}: {mine} vs summary {ref} -> {'OK' if ok else 'MISMATCH'}")

# GAZP-2022 TREND_DOWN
g22 = [r for r in TL["instruments"]["GAZP"] if r["date"].startswith("2022")]
td = sum(1 for r in g22 if r["regime"] == "TREND_DOWN") / len(g22) * 100
print(f"  GAZP-2022 TREND_DOWN: {td:.1f}% (заявлено 72.9)")
s26 = [r for r in TL["instruments"]["SBER"] if r["date"].startswith("2026")]
rg26 = sum(1 for r in s26 if r["regime"] == "RANGE") / len(s26) * 100
print(f"  SBER-2026 RANGE: {rg26:.1f}% (заявлено 61.8)")

# ── 2. Крупные эпизоды ───────────────────────────────────────────────────────
print("\n=== 2. Эпизоды >= 15 баров (для хронологии) ===")
for tk, rows in TL["instruments"].items():
    eps = []
    for r in rows:
        if eps and eps[-1]["regime"] == r["regime"]:
            eps[-1]["end"] = r["date"]
            eps[-1]["n"] += 1
            eps[-1]["c1"] = r["close"]
        else:
            eps.append({"regime": r["regime"], "start": r["date"], "end": r["date"],
                        "n": 1, "c0": r["close"], "c1": r["close"]})
    print(f"\n  {tk}:")
    for e in eps:
        if e["n"] >= 15:
            chg = (e["c1"] / e["c0"] - 1) * 100
            print(f"    {e['start']} -> {e['end']}  {e['regime']:<10} {e['n']:>3} бар  "
                  f"close {e['c0']:.1f} -> {e['c1']:.1f} ({chg:+.1f}%)")

# ── 3. Матрица переходов (агрегат по 6 инструментам) ────────────────────────
print("\n=== 3. Матрица переходов P(завтра | сегодня), агрегат ===")
trans = defaultdict(Counter)
for tk, rows in TL["instruments"].items():
    for a, b in zip(rows, rows[1:]):
        trans[a["regime"]][b["regime"]] += 1
hdr_from = "из/в"
print(f"  {hdr_from:<11}" + "".join(f"{rg:>11}" for rg in REGIMES) + f"{'persist':>9}{'1/(1-p)':>9}{'наблюд.ср':>10}")
for rg in REGIMES:
    tot = sum(trans[rg].values())
    row = [trans[rg].get(t, 0) / tot for t in REGIMES]
    p = trans[rg].get(rg, 0) / tot
    implied = 1.0 / (1.0 - p) if p < 1 else float("inf")
    # наблюдаемая средняя длительность эпизода этого режима (агрегат)
    lens = []
    for tk, rows in TL["instruments"].items():
        cur = 0
        for r in rows:
            if r["regime"] == rg:
                cur += 1
            elif cur:
                lens.append(cur); cur = 0
        if cur:
            lens.append(cur)
    obs = sum(lens) / len(lens) if lens else 0
    print(f"  {rg:<11}" + "".join(f"{v:>10.1%} " for v in row) + f"{p:>8.1%}{implied:>9.1f}{obs:>10.1f}")

# ── 4. Независимый перерасчёт 2 ячеек матрицы ────────────────────────────────
print("\n=== 4. Независимый перерасчёт ячеек (SBER) ===")
uid = "e6123145-9665-43e0-8413-cd61b8aa9b13"
bars = candles.from_tinvest(uid, "SBER", days=1040)["SBER"]
from datetime import datetime, timezone
date_of = {datetime.fromtimestamp(b.t, tz=timezone.utc).strftime("%Y-%m-%d"): i
           for i, b in enumerate(bars)}
reg_by_date = {r["date"]: r["regime"] for r in TL["instruments"]["SBER"]}
opens = [b.o for b in bars]
closes = [b.c for b in bars]

def cell(regime, pos_fn):
    rets = []
    prev = 0
    for d, i in date_of.items():
        if i + 2 >= len(bars):
            continue
        rg = reg_by_date.get(d)
        p = pos_fn(i)
        if rg == regime:
            cost = 0.0005 * abs(p - prev)
            rets.append(p * (opens[i + 2] / opens[i + 1] - 1.0) - cost)
        prev = p
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    sd = math.sqrt(var)
    return n, mean * 252, mean / sd * math.sqrt(252)

n, ann, sh = cell("TREND_DOWN", lambda i: 1)  # buyhold
ref = CR["per_instrument"]["SBER"]["TREND_DOWN"]["buyhold"]
print(f"  SBER TREND_DOWN buyhold: n={n} ann={ann:+.4f} sharpe={sh:+.2f} "
      f"vs json bars={ref['bars']} ann={ref['ann_ret']} sharpe={ref['sharpe']}")

def tf_pos(i):
    ma_now = sma(closes[:i + 1], 20)
    ma_prev = sma(closes[:i], 20)
    if ma_now is None or ma_prev is None:
        return 0
    return 1 if ma_now > ma_prev else (-1 if ma_now < ma_prev else 0)

n, ann, sh = cell("TREND_UP", tf_pos)
ref = CR["per_instrument"]["SBER"]["TREND_UP"]["trend_follow"]
print(f"  SBER TREND_UP trend_follow: n={n} ann={ann:+.4f} sharpe={sh:+.2f} "
      f"vs json bars={ref['bars']} ann={ref['ann_ret']} sharpe={ref['sharpe']}")

# ── 5. Перепроверка строки индикаторов ───────────────────────────────────────
print("\n=== 5. Перепроверка индикаторов SBER на 2 датах ===")
highs = [b.h for b in bars]
lows = [b.l for b in bars]
for target in ("2024-06-03", "2025-11-14"):
    i = date_of[target]
    h = hurst(closes[:i + 1], 100)
    lo = max(0, i + 1 - 150)
    a = adx(highs[lo:i + 1], lows[lo:i + 1], closes[lo:i + 1], 14)[2]
    row = next(r for r in TL["instruments"]["SBER"] if r["date"] == target)
    print(f"  {target}: hurst {h:.4f} vs {row['hurst']}, adx {a:.2f} vs {row['adx']}, "
          f"regime json={row['regime']}")

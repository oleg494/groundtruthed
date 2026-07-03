"""Таймлайн рыночных режимов MOEX: TREND_UP / TREND_DOWN / RANGE / HIGH_VOL / NEUTRAL.

Задача #43 (карта режимов). Для 6 инструментов (SBER, GAZP, LKOH, GMKN — акции;
GLDRUBF, IMOEXF — вечные фьючерсы, цена в пунктах) по дневным барам считаем
скользящие Hurst(100), ADX(14), realized vol (20д, аннуализированная), ATR%(14),
наклон MA50 — и классифицируем режим КАЖДОГО бара по правилам из
deep/market_regime_moex.md (§1.2, §4.2) и deep/market_regime_filters.md (§2, §3):

  1) TREND_UP / TREND_DOWN:  H > 0.55  и  ADX > 25, направление — знак наклона MA50;
  2) RANGE:                  H < 0.45  или  ADX < 20;
  3) HIGH_VOL:               RV > её 80%-квантиль (расширяющееся окно, без lookahead) —
                             это «краш/переходная» зона доки: H в индетерминантной
                             полосе 0.45–0.55, ADX средний, вола рвёт квантиль;
  4) NEUTRAL:                всё остальное.

Порядок проверки правил = порядок выше (тренд сильнее HIGH_VOL: по §4.2 краш-режим —
именно неопределённый H при всплеске волы, а не сильный тренд).

Без lookahead: каждый индикатор на баре t считается только по барам ..t включительно
(окна фиксированной длины), RV-квантиль — расширяющийся (только прошлые значения RV).

Данные: backtest/candles.from_tinvest (sandbox-домен, read-only, диск-кэш
backtest/.cache тёплый на дату прогона — сеть не дёргается). Заявок нет.

Выход:
  - analysis/regime_timeline.json — по инструментам [{date, close, hurst, adx, rv,
    atr_pct, ma_slope, regime}];
  - stdout — доли времени в режимах по инструментам и годам, средняя длительность
    эпизода, число переключений.

Запуск:  python analysis/regime_timeline.py
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest import candles  # noqa: E402
from backtest.indicators import adx, atr, hurst, sma, stdev  # noqa: E402

# ─────────────────────────── константы (пороги из deep/) ───────────────────────────
HURST_WIN = 100          # окно Hurst R/S (deep: 100–200, берём чувствительный край)
ADX_N = 14               # классический Wilder ADX(14) (deep/market_regime_filters §2.1)
ADX_LOOKBACK = 150       # фиксированный хвост истории для рекурсии Уайлдера (>2N+1,
                         # одинаковый на каждом баре → значения сопоставимы вдоль ряда)
RV_WIN = 20              # окно realized vol, дневные лог-доходности
ANNUAL = 252             # торговых дней в году для аннуализации RV
ATR_N = 14
ATR_LOOKBACK = 115       # фиксированный хвост для ATR (сглаживание Уайлдера)
MA_N = 50                # базовая скользящая для наклона
SLOPE_LAG = 5            # наклон = MA50(t) − MA50(t−lag), знак задаёт направление тренда

H_TREND = 0.55           # H выше — персистентность/тренд (deep §1.2)
H_RANGE = 0.45           # H ниже — mean-reversion/боковик
ADX_TREND = 25.0         # ADX выше — устойчивый тренд (deep §1.1)
ADX_RANGE = 20.0         # ADX ниже — флэт
RV_QUANTILE = 0.80       # HIGH_VOL: RV выше этого квантиля своей истории
RV_MIN_OBS = 100         # минимум наблюдений RV до того, как квантиль считается валидным

# инструменты: uid из lab/instruments.py (акции, GLDRUBF) и
# scripts/market_context.py IMOEXF_FALLBACK; days подобраны под тёплый кэш backtest/.cache
INSTRUMENTS = {
    "SBER":    {"uid": "e6123145-9665-43e0-8413-cd61b8aa9b13", "days": 1040},
    "GAZP":    {"uid": "962e2a95-02a9-4171-abd7-aa198dbe643a", "days": 1040},
    "LKOH":    {"uid": "02cfdf61-6298-4c0f-a9ca-9cabc82afaf3", "days": 1100},
    "GMKN":    {"uid": "509edd0c-129c-4ee2-934d-7f6246126da1", "days": 1040},
    "GLDRUBF": {"uid": "b347fe28-0d2a-45bf-b3bd-cda8a6ac64e6", "days": 1300},  # пункты
    "IMOEXF":  {"uid": "5bcff194-f10d-4314-b9ee-56b7fdb344fd", "days": 1300},  # пункты
}

REGIMES = ("TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL", "NEUTRAL")
OUT_JSON = Path(__file__).resolve().parent / "regime_timeline.json"


# ─────────────────────────── расчёт по одному инструменту ───────────────────────────
def realized_vol(closes: list[float]) -> float | None:
    """Аннуализированная реализованная вола по последним RV_WIN лог-доходностям."""
    if len(closes) < RV_WIN + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(len(closes) - RV_WIN, len(closes))]
    sd = stdev(rets, RV_WIN, sample=True)
    return sd * math.sqrt(ANNUAL) if sd is not None else None


def quantile(sorted_xs: list[float], q: float) -> float:
    """Квантиль по отсортированному списку (линейная интерполяция)."""
    if not sorted_xs:
        return float("inf")
    pos = q * (len(sorted_xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_xs) - 1)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (pos - lo)


def classify(h, a, rv, slope, rv_thr) -> str | None:
    """Правила режима (порядок — как в шапке). None, пока индикаторы не прогреты."""
    if h is None or a is None or rv is None:
        return None
    if h > H_TREND and a > ADX_TREND and slope is not None:
        return "TREND_UP" if slope > 0 else "TREND_DOWN"
    if h < H_RANGE or a < ADX_RANGE:
        return "RANGE"
    if rv_thr is not None and rv > rv_thr:
        return "HIGH_VOL"
    return "NEUTRAL"


def build_timeline(bars) -> list[dict]:
    """Пройти ряд бар за баром, без lookahead: на баре i видим только бары ..i."""
    highs = [b.h for b in bars]
    lows = [b.l for b in bars]
    closes = [b.c for b in bars]
    out = []
    rv_hist_sorted: list[float] = []       # прошлые RV для расширяющегося квантиля

    for i in range(len(bars)):
        cl = closes[: i + 1]
        h_val = hurst(cl, HURST_WIN)
        lo_a = max(0, i + 1 - ADX_LOOKBACK)
        adx_t = adx(highs[lo_a:i + 1], lows[lo_a:i + 1], closes[lo_a:i + 1], ADX_N)
        adx_val = adx_t[2] if adx_t else None
        rv_val = realized_vol(cl)
        lo_r = max(0, i + 1 - ATR_LOOKBACK)
        atr_val = atr(highs[lo_r:i + 1], lows[lo_r:i + 1], closes[lo_r:i + 1], ATR_N)
        atr_pct = atr_val / closes[i] * 100 if atr_val and closes[i] else None
        ma_now = sma(cl, MA_N)
        ma_then = sma(cl[:-SLOPE_LAG], MA_N) if len(cl) > SLOPE_LAG else None
        slope = (ma_now - ma_then) if (ma_now is not None and ma_then is not None) else None

        # квантиль RV — ТОЛЬКО по прошлым значениям (до текущего бара)
        rv_thr = (quantile(rv_hist_sorted, RV_QUANTILE)
                  if len(rv_hist_sorted) >= RV_MIN_OBS else None)
        regime = classify(h_val, adx_val, rv_val, slope, rv_thr)

        if rv_val is not None:              # пополняем историю ПОСЛЕ классификации
            import bisect
            bisect.insort(rv_hist_sorted, rv_val)

        if regime is None:
            continue                        # прогрев индикаторов — бар не размечаем
        out.append({
            "date": datetime.fromtimestamp(bars[i].t, tz=timezone.utc).strftime("%Y-%m-%d"),
            "close": round(closes[i], 6),
            "hurst": round(h_val, 4),
            "adx": round(adx_val, 2),
            "rv": round(rv_val, 4),
            "atr_pct": round(atr_pct, 3) if atr_pct is not None else None,
            "ma_slope": round(slope, 4) if slope is not None else None,
            "regime": regime,
        })
    return out


# ─────────────────────────── сводки ───────────────────────────
def episodes(timeline: list[dict]) -> list[tuple[str, int]]:
    """Свернуть таймлайн в эпизоды (режим, длительность в барах)."""
    eps = []
    for row in timeline:
        if eps and eps[-1][0] == row["regime"]:
            eps[-1][1] += 1
        else:
            eps.append([row["regime"], 1])
    return [(r, n) for r, n in eps]


def summarize(tk: str, timeline: list[dict]) -> dict:
    n = len(timeline)
    shares = Counter(r["regime"] for r in timeline)
    by_year: dict[str, Counter] = defaultdict(Counter)
    for row in timeline:
        by_year[row["date"][:4]][row["regime"]] += 1
    eps = episodes(timeline)
    switches = len(eps) - 1
    mean_ep = n / len(eps) if eps else 0.0
    mean_by_regime = {}
    for reg in REGIMES:
        lens = [ln for r, ln in eps if r == reg]
        if lens:
            mean_by_regime[reg] = sum(lens) / len(lens)

    print(f"\n=== {tk}: {n} размеченных баров "
          f"({timeline[0]['date']} → {timeline[-1]['date']}) ===")
    print("  доля времени: " + "  ".join(
        f"{reg} {shares.get(reg, 0) / n * 100:5.1f}%" for reg in REGIMES))
    print(f"  эпизодов {len(eps)}, переключений {switches}, "
          f"средняя длительность эпизода {mean_ep:.1f} бар")
    print("  средняя длительность по режимам: " + "  ".join(
        f"{reg} {v:.1f}" for reg, v in mean_by_regime.items()))
    print(f"  {'год':>6} {'баров':>6} " + " ".join(f"{reg:>10}" for reg in REGIMES))
    for year in sorted(by_year):
        cnt = by_year[year]
        tot = sum(cnt.values())
        print(f"  {year:>6} {tot:>6} " + " ".join(
            f"{cnt.get(reg, 0) / tot * 100:9.1f}%" for reg in REGIMES))
    return {
        "bars": n,
        "shares_pct": {reg: round(shares.get(reg, 0) / n * 100, 1) for reg in REGIMES},
        "episodes": len(eps),
        "switches": switches,
        "mean_episode_bars": round(mean_ep, 1),
        "mean_episode_by_regime": {k: round(v, 1) for k, v in mean_by_regime.items()},
        "by_year_pct": {y: {reg: round(c.get(reg, 0) / sum(c.values()) * 100, 1)
                            for reg in REGIMES}
                        for y, c in sorted(by_year.items())},
    }


def main():
    print("=== Таймлайн режимов MOEX (Hurst/ADX/RV, дневки, без lookahead) ===")
    print(f"пороги: H>{H_TREND} & ADX>{ADX_TREND} → TREND; H<{H_RANGE} или "
          f"ADX<{ADX_RANGE} → RANGE; RV > q{int(RV_QUANTILE * 100)} "
          f"(расширяющийся) → HIGH_VOL; иначе NEUTRAL")
    result, summaries = {}, {}
    for tk, spec in INSTRUMENTS.items():
        data = candles.from_tinvest(spec["uid"], tk, days=spec["days"])
        bars = data[tk]
        print(f"\n{tk}: {len(bars)} баров загружено "
              f"({datetime.fromtimestamp(bars[0].t, tz=timezone.utc):%Y-%m-%d} → "
              f"{datetime.fromtimestamp(bars[-1].t, tz=timezone.utc):%Y-%m-%d})", end="")
        timeline = build_timeline(bars)
        result[tk] = timeline
        summaries[tk] = summarize(tk, timeline)

    OUT_JSON.write_text(json.dumps({
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "params": {"hurst_win": HURST_WIN, "adx_n": ADX_N, "rv_win": RV_WIN,
                   "atr_n": ATR_N, "ma_n": MA_N, "slope_lag": SLOPE_LAG,
                   "h_trend": H_TREND, "h_range": H_RANGE, "adx_trend": ADX_TREND,
                   "adx_range": ADX_RANGE, "rv_quantile": RV_QUANTILE},
        "summary": summaries,
        "instruments": result,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"\nJSON: {OUT_JSON} "
          f"({sum(len(v) for v in result.values())} размеченных баров всего)")


if __name__ == "__main__":
    main()

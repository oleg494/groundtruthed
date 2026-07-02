"""Условная доходность торговых примитивов по режимам рынка (MOEX, дневки).

Вопрос: подтверждается ли учебник «в TREND работает тренд-фоллоу, в RANGE —
реверсия»? Или (гипотеза из kill-пула: 4 стратегии фермы и ORB убиты) на MOEX
пост-2022 реверсия не работает нигде, а тренд — только в TREND_UP?

Классификация режимов — ДОСЛОВНАЯ КОПИЯ analysis/regime_timeline.py (задача #43,
константы продублированы и сверены по долям с analysis/regime_timeline.json;
методология deep/market_regime_moex.md §1.2/§4.2 + deep/market_regime_filters.md §2):

  1) TREND_UP / TREND_DOWN:  H(100) > 0.55 и ADX(14) > 25, направление — знак
                             наклона MA50 (t vs t−5);
  2) RANGE:                  H < 0.45 или ADX < 20;
  3) HIGH_VOL:               RV(20д, аннуализ.) > её 80%-квантиль (расширяющееся
                             окно, только прошлые RV, минимум 100 наблюдений);
  4) NEUTRAL:                всё остальное.

Примитивы (по-барные правила, вход по open СЛЕДУЮЩЕГО бара, без lookahead):
  trend_follow: позиция = знак наклона MA20 (±1);
  mean_revert:  против отклонения от MA20 при |z20| > 1 (иначе флэт);
  breakout:     Donchian(20) — лонг выше канала, шорт ниже, между — держим;
  buyhold:      всегда +1.
Издержки 10 б.п. на круг (5 б.п. на сторону × |Δpos|).
Доходность бара приписывается режиму на ДАТУ ВХОДА (бар решения i): позиция
открывается по open[i+1] и переоценивается до open[i+2].

Данные: те же 6 инструментов и days, что в regime_timeline.py (sandbox-домен,
диск-кэш backtest/.cache тёплый — сеть не дёргается). Цены фьючерсов в пунктах,
для относительных доходностей это неважно. Read-only, заявок нет.

Запуск:  python analysis/regime_conditional_returns.py
Выход:   analysis/regime_conditional_returns.json + stdout-таблицы.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest import candles                                        # noqa: E402
from backtest.indicators import adx, donchian, hurst, sma, stdev, zscore  # noqa: E402

OUT = Path(__file__).resolve().parent / "regime_conditional_returns.json"
TIMELINE_JSON = Path(__file__).resolve().parent / "regime_timeline.json"

# ── Константы классификации (продублированы из analysis/regime_timeline.py) ──
HURST_WIN = 100          # окно Hurst R/S
ADX_N = 14               # классический Wilder ADX(14)
ADX_LOOKBACK = 150       # фиксированный хвост истории для рекурсии Уайлдера
RV_WIN = 20              # окно realized vol, дневные лог-доходности
ANNUAL = 252
MA_N = 50                # базовая скользящая для наклона (направление тренда)
SLOPE_LAG = 5            # наклон = MA50(t) − MA50(t−5)
H_TREND = 0.55
H_RANGE = 0.45
ADX_TREND = 25.0
ADX_RANGE = 20.0
RV_QUANTILE = 0.80       # HIGH_VOL: RV выше этого квантиля своей истории
RV_MIN_OBS = 100         # минимум прошлых RV до валидного квантиля

# ── Константы примитивов ─────────────────────────────────────────────────────
MA_WIN = 20              # trend-follow: знак наклона MA20
Z_WIN = 20               # mean-revert: z-score отклонения от MA20
Z_ENTRY = 1.0
DONCH_WIN = 20           # breakout: канал Дончиана 20
COST_ROUND = 0.0010      # 10 б.п. на круг
HALF_COST = COST_ROUND / 2.0
TRADING_DAYS = 252

REGIMES = ("TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL", "NEUTRAL")
PRIMS = ("trend_follow", "mean_revert", "breakout", "buyhold")

# те же инструменты/days, что в regime_timeline.py (кэш тёплый)
INSTRUMENTS = {
    "SBER":    {"uid": "e6123145-9665-43e0-8413-cd61b8aa9b13", "days": 1040},
    "GAZP":    {"uid": "962e2a95-02a9-4171-abd7-aa198dbe643a", "days": 1040},
    "LKOH":    {"uid": "02cfdf61-6298-4c0f-a9ca-9cabc82afaf3", "days": 1100},
    "GMKN":    {"uid": "509edd0c-129c-4ee2-934d-7f6246126da1", "days": 1040},
    "GLDRUBF": {"uid": "b347fe28-0d2a-45bf-b3bd-cda8a6ac64e6", "days": 1300},  # пункты
    "IMOEXF":  {"uid": "5bcff194-f10d-4314-b9ee-56b7fdb344fd", "days": 1300},  # пункты
}


# ── Классификация (копия regime_timeline.py, без lookahead) ─────────────────
def realized_vol(closes: list[float]) -> float | None:
    if len(closes) < RV_WIN + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(len(closes) - RV_WIN, len(closes))]
    sd = stdev(rets, RV_WIN, sample=True)
    return sd * math.sqrt(ANNUAL) if sd is not None else None


def quantile(sorted_xs: list[float], q: float) -> float:
    if not sorted_xs:
        return float("inf")
    pos = q * (len(sorted_xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_xs) - 1)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (pos - lo)


def classify(h, a, rv, slope, rv_thr) -> str | None:
    """Правила режима (порядок важен). None, пока индикаторы не прогреты."""
    if h is None or a is None or rv is None:
        return None
    if h > H_TREND and a > ADX_TREND and slope is not None:
        return "TREND_UP" if slope > 0 else "TREND_DOWN"
    if h < H_RANGE or a < ADX_RANGE:
        return "RANGE"
    if rv_thr is not None and rv > rv_thr:
        return "HIGH_VOL"
    return "NEUTRAL"


def regimes_series(bars) -> list[str | None]:
    """Режим каждого бара (по данным ≤ бара, как в regime_timeline.py)."""
    highs = [b.h for b in bars]
    lows = [b.l for b in bars]
    closes = [b.c for b in bars]
    out: list[str | None] = []
    rv_hist_sorted: list[float] = []
    import bisect
    for i in range(len(bars)):
        cl = closes[: i + 1]
        h_val = hurst(cl, HURST_WIN)
        lo_a = max(0, i + 1 - ADX_LOOKBACK)
        adx_t = adx(highs[lo_a:i + 1], lows[lo_a:i + 1], closes[lo_a:i + 1], ADX_N)
        adx_val = adx_t[2] if adx_t else None
        rv_val = realized_vol(cl)
        ma_now = sma(cl, MA_N)
        ma_then = sma(cl[:-SLOPE_LAG], MA_N) if len(cl) > SLOPE_LAG else None
        slope = (ma_now - ma_then) if (ma_now is not None and ma_then is not None) else None
        rv_thr = (quantile(rv_hist_sorted, RV_QUANTILE)
                  if len(rv_hist_sorted) >= RV_MIN_OBS else None)
        out.append(classify(h_val, adx_val, rv_val, slope, rv_thr))
        if rv_val is not None:
            bisect.insort(rv_hist_sorted, rv_val)
    return out


# ── Позиции примитивов на баре i (решение по close[i]) ──────────────────────
def prim_positions(closes: list[float], highs: list[float], lows: list[float],
                   i: int, prev_breakout: int) -> dict[str, int]:
    pos: dict[str, int] = {}
    ma_now = sma(closes[:i + 1], MA_WIN)
    ma_prev = sma(closes[:i], MA_WIN)
    if ma_now is None or ma_prev is None:
        pos["trend_follow"] = 0
    else:
        pos["trend_follow"] = 1 if ma_now > ma_prev else (-1 if ma_now < ma_prev else 0)
    z = zscore(closes[:i + 1], Z_WIN)
    if z is None:
        pos["mean_revert"] = 0
    else:
        pos["mean_revert"] = -1 if z > Z_ENTRY else (1 if z < -Z_ENTRY else 0)
    dc = donchian(highs[:i + 1], lows[:i + 1], DONCH_WIN)
    if dc is None:
        pos["breakout"] = prev_breakout
    elif closes[i] > dc[1]:
        pos["breakout"] = 1
    elif closes[i] < dc[0]:
        pos["breakout"] = -1
    else:
        pos["breakout"] = prev_breakout      # между границами — держим
    pos["buyhold"] = 1
    return pos


# ── Метрики по пулу дневных доходностей ──────────────────────────────────────
def stats(rets: list[float], active: list[float]) -> dict:
    n = len(rets)
    if n == 0:
        return {"bars": 0, "ann_ret": None, "sharpe": None, "hit_rate": None,
                "active_bars": 0}
    mean = sum(rets) / n
    ann = mean * TRADING_DAYS
    if n >= 20:
        var = sum((r - mean) ** 2 for r in rets) / (n - 1)
        sd = math.sqrt(var)
        sharpe = (mean / sd * math.sqrt(TRADING_DAYS)) if sd > 1e-12 else None
    else:
        sharpe = None
    hit = (sum(1 for r in active if r > 0) / len(active)) if active else None
    return {"bars": n, "ann_ret": round(ann, 4),
            "sharpe": round(sharpe, 2) if sharpe is not None else None,
            "hit_rate": round(hit, 3) if hit is not None else None,
            "active_bars": len(active)}


def run_instrument(bars) -> tuple[dict, dict, dict]:
    """→ (матрица режим×примитив, счётчик режимов, сырые пулы).
    Доходность бара i: pos[i] * (open[i+2]/open[i+1] − 1) − издержки за |Δpos|."""
    closes = [b.c for b in bars]
    highs = [b.h for b in bars]
    lows = [b.l for b in bars]
    opens = [b.o for b in bars]
    regs = regimes_series(bars)
    cells = {rg: {p: {"all": [], "act": []} for p in PRIMS} for rg in REGIMES}
    reg_count = {rg: 0 for rg in REGIMES}
    prev_pos = {p: 0 for p in PRIMS}
    prev_bk = 0
    for i in range(len(bars) - 2):
        rg = regs[i]
        if rg is None:
            continue                          # индикаторы не прогреты
        reg_count[rg] += 1
        pos = prim_positions(closes, highs, lows, i, prev_bk)
        prev_bk = pos["breakout"]
        if opens[i + 1] <= 0:
            prev_pos = pos
            continue
        bar_ret = opens[i + 2] / opens[i + 1] - 1.0
        for p in PRIMS:
            cost = HALF_COST * abs(pos[p] - prev_pos[p])
            net = pos[p] * bar_ret - cost
            cells[rg][p]["all"].append(net)
            if pos[p] != 0:
                cells[rg][p]["act"].append(net)
        prev_pos = pos
    matrix = {rg: {p: stats(cells[rg][p]["all"], cells[rg][p]["act"])
                   for p in PRIMS} for rg in REGIMES}
    return matrix, reg_count, cells


def print_matrix(title: str, matrix: dict) -> None:
    print(f"\n{'=' * 78}\n{title}")
    hdr = f"{'режим':<11} {'примитив':<13} {'барыВсего':>9} {'акт.бары':>8} " \
          f"{'ann.ret':>8} {'Sharpe':>7} {'hit':>6}"
    print(hdr)
    print("-" * len(hdr))
    for rg in REGIMES:
        for p in PRIMS:
            s = matrix[rg][p]
            ar = f"{s['ann_ret'] * 100:+.1f}%" if s["ann_ret"] is not None else "—"
            sh = f"{s['sharpe']:+.2f}" if s["sharpe"] is not None else "—"
            ht = f"{s['hit_rate']:.2f}" if s["hit_rate"] is not None else "—"
            print(f"{rg:<11} {p:<13} {s['bars']:>9} {s['active_bars']:>8} "
                  f"{ar:>8} {sh:>7} {ht:>6}")


def crosscheck_timeline(reg_shares: dict) -> None:
    """Сверка долей режимов с analysis/regime_timeline.json (оракул-дисциплина)."""
    if not TIMELINE_JSON.exists():
        print("\n(regime_timeline.json нет — сверка долей пропущена)")
        return
    t = json.loads(TIMELINE_JSON.read_text(encoding="utf-8"))
    print(f"\nСверка долей режимов с regime_timeline.json (их % / наш %, Δ):")
    worst = 0.0
    for tk, mine in reg_shares.items():
        theirs = t.get("summary", {}).get(tk, {}).get("shares_pct")
        if not theirs:
            continue
        row = []
        for rg in REGIMES:
            a = theirs.get(rg, 0.0)
            b = mine[rg] * 100
            worst = max(worst, abs(a - b))
            row.append(f"{rg}={a:.1f}/{b:.1f}")
        print(f"  {tk}: " + ", ".join(row))
    print(f"  макс. расхождение: {worst:.1f} п.п. "
          f"({'OK — классификация совпадает' if worst < 1.5 else 'ВНИМАНИЕ: правила разошлись'})")


def main() -> None:
    print("Условная доходность примитивов по режимам (MOEX, дневки)")
    print(f"Классификация = regime_timeline.py: Hurst({HURST_WIN}) {H_RANGE}/{H_TREND}, "
          f"ADX({ADX_N}) {ADX_RANGE}/{ADX_TREND}, RANGE=(H< или ADX<), "
          f"HIGH_VOL=RV>q{RV_QUANTILE:.2f}; издержки {COST_ROUND * 1e4:.0f} б.п./круг")
    print("\nФетч свечей (sandbox, кэш):")
    data = {}
    for tk, meta in INSTRUMENTS.items():
        try:
            d = candles.from_tinvest(meta["uid"], tk, days=meta["days"])
            data[tk] = d[tk]
            print(f"  {tk}: {len(d[tk])} баров")
        except Exception as e:                                  # noqa: BLE001
            print(f"  {tk}: фетч не удался ({e}) — пропуск")

    per_instrument = {}
    reg_shares = {}
    agg_cells = {rg: {p: {"all": [], "act": []} for p in PRIMS} for rg in REGIMES}
    agg_regs = {rg: 0 for rg in REGIMES}

    for tk, bars in data.items():
        matrix, reg_count, cells = run_instrument(bars)
        per_instrument[tk] = matrix
        total = sum(reg_count.values()) or 1
        reg_shares[tk] = {rg: c / total for rg, c in reg_count.items()}
        for rg in REGIMES:
            agg_regs[rg] += reg_count[rg]
            for p in PRIMS:
                agg_cells[rg][p]["all"].extend(cells[rg][p]["all"])
                agg_cells[rg][p]["act"].extend(cells[rg][p]["act"])

    agg_matrix = {rg: {p: stats(agg_cells[rg][p]["all"], agg_cells[rg][p]["act"])
                       for p in PRIMS} for rg in REGIMES}
    total_regs = sum(agg_regs.values()) or 1
    agg_shares = {rg: round(c / total_regs, 3) for rg, c in agg_regs.items()}

    for tk in data:
        print_matrix(f"{tk}  (доли режимов: " +
                     ", ".join(f"{rg}={reg_shares[tk][rg]:.0%}" for rg in REGIMES) + ")",
                     per_instrument[tk])
    print_matrix("АГРЕГАТ (пул 6 инструментов; доли режимов: " +
                 ", ".join(f"{rg}={agg_shares[rg]:.0%}" for rg in REGIMES) + ")",
                 agg_matrix)

    crosscheck_timeline(reg_shares)

    # ── ключевой вопрос ──────────────────────────────────────────────────────
    print(f"\n{'=' * 78}\nКлючевой вопрос (по агрегату):")
    tf_up = agg_matrix["TREND_UP"]["trend_follow"]
    tf_dn = agg_matrix["TREND_DOWN"]["trend_follow"]
    mr_rng = agg_matrix["RANGE"]["mean_revert"]
    v_tf_up = (tf_up["sharpe"] or 0) > 0.5
    v_tf_dn = (tf_dn["sharpe"] or 0) > 0.5
    v_mr_rng = (mr_rng["sharpe"] or 0) > 0.5
    mr_where = [rg for rg in REGIMES
                if (agg_matrix[rg]["mean_revert"]["sharpe"] or 0) > 0.5]
    tf_where = [rg for rg in REGIMES
                if (agg_matrix[rg]["trend_follow"]["sharpe"] or 0) > 0.5]
    print(f"  тренд-фоллоу в TREND_UP:  Sharpe={tf_up['sharpe']}, ann={tf_up['ann_ret']}"
          f" → {'РАБОТАЕТ' if v_tf_up else 'не работает'}")
    print(f"  тренд-фоллоу в TREND_DOWN: Sharpe={tf_dn['sharpe']}, ann={tf_dn['ann_ret']}"
          f" → {'РАБОТАЕТ' if v_tf_dn else 'не работает'}")
    print(f"  реверсия в RANGE:         Sharpe={mr_rng['sharpe']}, ann={mr_rng['ann_ret']}"
          f" → {'РАБОТАЕТ' if v_mr_rng else 'не работает'}")
    print(f"  реверсия Sharpe>0.5 в режимах: {mr_where or 'НИГДЕ'}")
    print(f"  тренд-фоллоу Sharpe>0.5 в режимах: {tf_where or 'НИГДЕ'}")
    if v_mr_rng:
        verdict = "учебник подтверждён: в RANGE реверсия работает"
    elif not mr_where and tf_where == ["TREND_UP"]:
        verdict = ("гипотеза kill-пула подтверждена: реверсия не работает нигде, "
                   "тренд — только в TREND_UP")
    elif not mr_where:
        verdict = ("реверсия не работает нигде (kill-пул прав наполовину); "
                   f"тренд-фоллоу жив в {tf_where or 'нигде'}")
    else:
        verdict = "смешанная картина, см. матрицу"
    print(f"  ВЕРДИКТ: {verdict}")

    OUT.write_text(json.dumps({
        "config": {"classification": "copy of analysis/regime_timeline.py",
                   "hurst_win": HURST_WIN, "h_trend": H_TREND, "h_range": H_RANGE,
                   "adx_n": ADX_N, "adx_trend": ADX_TREND, "adx_range": ADX_RANGE,
                   "adx_lookback": ADX_LOOKBACK, "rv_win": RV_WIN,
                   "rv_quantile": RV_QUANTILE, "rv_min_obs": RV_MIN_OBS,
                   "ma_n": MA_N, "slope_lag": SLOPE_LAG,
                   "primitives": {"trend_follow": f"sign(MA{MA_WIN} slope)",
                                  "mean_revert": f"против z{Z_WIN} при |z|>{Z_ENTRY}",
                                  "breakout": f"Donchian {DONCH_WIN}, держим между",
                                  "buyhold": "+1"},
                   "cost_round": COST_ROUND, "entry": "open next bar",
                   "attribution": "regime at decision bar (дата входа)"},
        "universe": {tk: {"uid": m["uid"], "days": m["days"],
                          "bars": len(data.get(tk, []))}
                     for tk, m in INSTRUMENTS.items()},
        "regime_shares": {"per_instrument": {tk: {rg: round(v, 3) for rg, v in s.items()}
                                             for tk, s in reg_shares.items()},
                          "aggregate": agg_shares,
                          "aggregate_bar_counts": agg_regs},
        "per_instrument": per_instrument,
        "aggregate": agg_matrix,
        "verdict": verdict,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nJSON: {OUT}")


if __name__ == "__main__":
    main()

"""Калибровка ATR-фильтра daybot числом: душит ли коридор [0.6, 2.0]×ATR вход в рынок.

    python analysis/atr_filter_calibration.py

daybot/run.py пропускает день, если ширина утреннего диапазона 10:00-10:30 МСК не попадает
в [ATR_MULT_MIN, ATR_MULT_MAX]×ATR(100) по дневным барам. Гипотеза аудита: утренняя 30-мин
свеча редко даёт ≥60% ДНЕВНОГО хода, т.е. нижний порог 0.6 отрежет почти все дни. Проверяем
на реальной истории, а не на глазок.

Оракул — распределение ratio = (high-low утренней 30-мин свечи) / ATR(100) по ~120 торговым
дням. ATR считается ТОЧНО как в daybot.get_atr (простое среднее TR за последние N дневных
баров, TR[0]=h-l), строго по дням ДО текущего — без lookahead. Цены фьючерсов в пунктах,
но ratio безразмерный — пункты сокращаются.

Данные: read-only GetCandles через sandbox-домен с диск-кэшем (backtest.candles.from_tinvest —
тот же путь, что у analysis/orb_backtest.py). Заявок не размещает.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.candles import from_tinvest          # noqa: E402
from lab.instruments import INSTRUMENTS            # noqa: E402

G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"
MSK = timezone(timedelta(hours=3))

TICKERS = ["BMQ6", "NGN6", "GLDRUBF"]   # фьючи daybot (BRM-8.26, NG-7.26, вечное золото)
ATR_N = 100                              # как в daybot.ATR_N
LO_CUR, HI_CUR = 0.6, 2.0                # текущий коридор daybot (ATR_MULT_MIN/MAX)
HISTORY_30M_DAYS = 120                   # календарных дней 30-мин истории (~120 торговых не выйдет, берём что отдаст)
HISTORY_DAY_DAYS = 500                   # дневных нужно с запасом: ATR(100) + история под ratio


def atr_series(day_bars) -> dict:
    """date(MSK) -> ATR(100) по барам СТРОГО до этой даты. Формула = daybot.get_atr:
    TR[0]=h-l, дальше классический TR, ATR = простое среднее последних N."""
    trs, dates = [], []
    prev_c = None
    for b in day_bars:
        d = datetime.fromtimestamp(b.t, tz=timezone.utc).astimezone(MSK).date()
        tr = b.h - b.l if prev_c is None else max(b.h - b.l, abs(b.h - prev_c), abs(b.l - prev_c))
        trs.append(tr)
        dates.append(d)
        prev_c = b.c
    out = {}
    for i in range(ATR_N, len(day_bars)):
        # ATR на утро дня dates[i] знает только бары 0..i-1
        out[dates[i]] = sum(trs[i - ATR_N:i]) / ATR_N
    return out


def quantile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    i = (len(s) - 1) * q
    lo = int(i)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def main():
    print(f"{BOLD}Калибровка ATR-фильтра daybot: ratio = утренний 30-мин диапазон / дневной "
          f"ATR({ATR_N}){X}")
    print(f"{DIM}коридор daybot сейчас: [{LO_CUR}, {HI_CUR}]×ATR; 30-мин история "
          f"~{HISTORY_30M_DAYS} календ. дней{X}\n")

    out = {"generated": datetime.now(timezone.utc).isoformat(), "atr_n": ATR_N,
           "corridor": [LO_CUR, HI_CUR], "results": {}}

    for tk in TICKERS:
        uid = INSTRUMENTS[tk]["uid"]
        print(f"━━ {tk}: тяну дневные и 30-мин свечи (sandbox, кэш backtest/.cache)…")
        day_bars = from_tinvest(uid, tk, days=HISTORY_DAY_DAYS)[tk]
        m30_bars = from_tinvest(uid, tk, days=HISTORY_30M_DAYS,
                                interval="CANDLE_INTERVAL_30_MIN")[tk]
        atr = atr_series(day_bars)

        # утренняя свеча дня = 30-мин бар с меткой 10:00 МСК (окно 10:00-10:30, как у daybot)
        ratios = []       # (date, ratio)
        no_atr = 0
        for b in m30_bars:
            dt = datetime.fromtimestamp(b.t, tz=timezone.utc).astimezone(MSK)
            if (dt.hour, dt.minute) != (10, 0):
                continue
            a = atr.get(dt.date())
            if a is None or a <= 0:
                no_atr += 1   # дневной истории до этого дня < ATR_N баров
                continue
            ratios.append((dt.date(), (b.h - b.l) / a))

        if len(ratios) < 20:
            print(f"    мало дней с ratio ({len(ratios)}) — пропуск\n")
            continue

        vals = [r for _, r in ratios]
        n = len(vals)
        qs = {q: quantile(vals, q / 100) for q in (10, 25, 50, 75, 90)}
        pass_cur = sum(1 for v in vals if LO_CUR <= v <= HI_CUR)
        grid = {}
        for k in range(1, 11):
            lo = k / 10
            grid[lo] = sum(1 for v in vals if lo <= v <= HI_CUR) / n

        print(f"    дневных {len(day_bars)}, 30-мин {len(m30_bars)}, утренних дней с ATR: {n}"
              f"{f' (+{no_atr} без ATR-прогрева)' if no_atr else ''}")
        print(f"    период: {ratios[0][0]} … {ratios[-1][0]}")
        print("    квантили ratio:  " +
              "  ".join(f"p{q}={v:.3f}" for q, v in qs.items()))
        col = R if pass_cur / n < 0.15 else (Y if pass_cur / n < 0.4 else G)
        print(f"    коридор [{LO_CUR}, {HI_CUR}]: проходит {col}{pass_cur}/{n} "
              f"({pass_cur / n * 100:.1f}%){X}")
        print("    сетка нижнего порога (верх 2.0):")
        print("      lo:    " + "  ".join(f"{k/10:>5.1f}" for k in range(1, 11)))
        print("      pass%: " + "  ".join(f"{grid[k/10]*100:>5.1f}" for k in range(1, 11)))
        print()

        out["results"][tk] = {
            "days": n, "period": [str(ratios[0][0]), str(ratios[-1][0])],
            "quantiles": {f"p{q}": round(v, 4) for q, v in qs.items()},
            "pass_current": round(pass_cur / n, 4),
            "grid_lo": {f"{k/10:.1f}": round(grid[k / 10], 4) for k in range(1, 11)},
        }

    # вердикт: фильтр «душит», если текущий коридор пропускает <15% дней
    print(f"{BOLD}══ вердикт ══{X}")
    verdict = []
    for tk, r in out["results"].items():
        p = r["pass_current"]
        med = r["quantiles"]["p50"]
        v = (f"{tk}: медиана ratio {med:.3f}, коридор [{LO_CUR},{HI_CUR}] пропускает "
             f"{p*100:.1f}% дней — " +
             ("фильтр ДУШИТ (почти всё режет)" if p < 0.15 else
              "фильтр жёсткий, но живой" if p < 0.4 else "фильтр не душит"))
        verdict.append(v)
        print("  • " + v)
    out["verdict"] = verdict

    res = ROOT / "analysis" / "atr_filter_calibration.json"
    res.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nрезультат: {res.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

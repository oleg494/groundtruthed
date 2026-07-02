"""Кандидат CALENDAR_CB, этап ИЗМЕРЕНИЕ: событийное исследование вокруг заседаний ЦБ РФ.

Гипотеза: вокруг дат решений по ключевой ставке (8/год, пятницы) есть паттерн —
сжатие волатильности до заседания, расширение после; направленный дрейф в день
решения и 1-3 дня после.

Метод: по дневным свечам SBER/IMOEXF/GLDRUBF для каждого относительного дня
k ∈ [-5..+5] вокруг заседания считаем среднюю доходность close-to-close и
реализованную волатильность (|ret|), сравниваем с безусловными, t-статистика
one-sample: t = (mean_k - mean_uncond) / (sd_k / sqrt(n_k)).

Только чтение кэша backtest/.cache (sandbox-домен). Заявок нет.
Запуск:  python analysis/botdev2_calendar_cb_measure.py
"""
from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest import candles                                  # noqa: E402

# ── календарь заседаний ЦБ РФ (проверен по cbr.ru/dkp/cal_mp, 2026-07-02) ──
# 2026-06-19 — РЕГУЛЯРНОЕ из официального календаря (не внеочередное);
# 2026-07-24 и позже — вне диапазона данных.
CB_MEETINGS = [
    # 2022 (только ПЛАНОВЫЕ в диапазоне данных; внеочередные 28.02, 08.04, 26.05
    # исключены — их нельзя антиципировать, гипотеза календарная)
    "2022-04-29", "2022-06-10", "2022-07-22", "2022-09-16",
    "2022-10-28", "2022-12-16",
    # 2023 (внеочередное 15.08 исключено)
    "2023-02-10", "2023-03-17", "2023-04-28", "2023-06-09",
    "2023-07-21", "2023-09-15", "2023-10-27", "2023-12-15",
    # 2024
    "2024-02-16", "2024-03-22", "2024-04-26", "2024-06-07",
    "2024-07-26", "2024-09-13", "2024-10-25", "2024-12-20",
    # 2025
    "2025-02-14", "2025-03-21", "2025-04-25", "2025-06-06",
    "2025-07-25", "2025-09-12", "2025-10-24", "2025-12-19",
    # 2026 (13.02→15.50%, 20.03→15.00%, 24.04→14.50%, 19.06→14.25%)
    "2026-02-13", "2026-03-20", "2026-04-24", "2026-06-19",
]

# инструменты: тикер -> (uid, days) — days подобраны под тёплый кэш 2026-07-02
UIDS = {
    "SBER":    ("e6123145-9665-43e0-8413-cd61b8aa9b13", 1040),
    "IMOEXF":  ("5bcff194-f10d-4314-b9ee-56b7fdb344fd", 1300),
    "GLDRUBF": ("b347fe28-0d2a-45bf-b3bd-cda8a6ac64e6", 1300),
}
K_RANGE = range(-5, 6)
MSK = 3 * 3600


def bar_date(t: int) -> dt.date:
    """Дата бара по МСК (граница дня свечей = MSK, docs/gotchas.md)."""
    return dt.datetime.utcfromtimestamp(t + MSK).date()


def event_offsets(bars, meetings: list[dt.date]) -> dict[int, int]:
    """Индекс бара дня заседания -> 0. Возвращает {bar_index: 0} по каждому событию.

    Заседания — рабочие пятницы; если даты нет в ленте (нет торгов) — событие
    пропускается с пометкой."""
    idx_by_date = {bar_date(b.t): i for i, b in enumerate(bars)}
    out = {}
    skipped = []
    for m in meetings:
        if m in idx_by_date:
            out[idx_by_date[m]] = 0
        else:
            skipped.append(m)
    return out, skipped


def stats(xs: list[float]):
    n = len(xs)
    if n < 2:
        return (sum(xs) / n if n else 0.0), 0.0, n
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, sd, n


def tstat(mean_k, sd_k, n_k, mean_u):
    if n_k < 2 or sd_k == 0:
        return 0.0
    return (mean_k - mean_u) / (sd_k / math.sqrt(n_k))


def main():
    for tk, (uid, days) in UIDS.items():
        data = candles.from_tinvest(uid, tk, days=days)
        bars = data[tk]
        d0, d1 = bar_date(bars[0].t), bar_date(bars[-1].t)
        rets = [bars[i].c / bars[i - 1].c - 1.0 for i in range(1, len(bars))]
        # ret[i-1] принадлежит бару i; дальше индексируем как ret_of_bar[i] = rets[i-1]
        meet_dates = [dt.date.fromisoformat(s) for s in CB_MEETINGS]
        in_range = [m for m in meet_dates if d0 <= m <= d1]
        ev_idx, skipped = event_offsets(bars, in_range)
        print(f"\n===== {tk}: {len(bars)} баров ({d0} … {d1}), "
              f"заседаний в диапазоне: {len(in_range)}, найдено в ленте: {len(ev_idx)} =====")
        if skipped:
            print(f"  пропущены (нет бара на дату): {skipped}")

        mu_u, sd_u, n_u = stats(rets)
        abs_u = [abs(r) for r in rets]
        amu_u, asd_u, _ = stats(abs_u)
        print(f"  безусловно: mean ret {mu_u*1e4:+.1f} б.п./д  sd {sd_u*100:.2f}%  "
              f"|ret| {amu_u*100:.3f}%  (N={n_u})")

        print(f"  {'k':>3} {'N':>3} {'mean_ret':>9} {'t_ret':>6} "
              f"{'mean|ret|':>9} {'t_rv':>6}  {'RV k / RV uncond':>16}")
        for k in K_RANGE:
            xs, ax = [], []
            for ei in ev_idx:
                j = ei + k
                if 1 <= j < len(bars):
                    xs.append(rets[j - 1])
                    ax.append(abs(rets[j - 1]))
            m_k, sd_k, n_k = stats(xs)
            am_k, asd_k, _ = stats(ax)
            t_r = tstat(m_k, sd_k, n_k, mu_u)
            t_v = tstat(am_k, asd_k, n_k, amu_u)
            ratio = am_k / amu_u if amu_u else 0.0
            print(f"  {k:+3d} {n_k:3d} {m_k*1e4:+8.1f}б {t_r:+6.2f} "
                  f"{am_k*100:8.3f}% {t_v:+6.2f}  {ratio:16.2f}")

        # агрегаты по окнам: до [-5..-1], день 0, после [+1..+3]
        for label, ks in (("до [-5..-1]", range(-5, 0)), ("день 0", [0]),
                          ("после [+1..+3]", range(1, 4))):
            xs, ax = [], []
            for ei in ev_idx:
                for k in ks:
                    j = ei + k
                    if 1 <= j < len(bars):
                        xs.append(rets[j - 1])
                        ax.append(abs(rets[j - 1]))
            m_k, sd_k, n_k = stats(xs)
            am_k, asd_k, _ = stats(ax)
            print(f"  окно {label:15}: N={n_k:3d}  mean {m_k*1e4:+7.1f}б "
                  f"(t={tstat(m_k, sd_k, n_k, mu_u):+.2f})  "
                  f"|ret| {am_k*100:.3f}% (t={tstat(am_k, asd_k, n_k, amu_u):+.2f}, "
                  f"×{am_k/amu_u if amu_u else 0:.2f} безусловной)")


if __name__ == "__main__":
    main()

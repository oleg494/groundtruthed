"""Есть ли в корзине вообще предсказуемость — или это белый шум?

Два стандартных теста на случайное блуждание, оба на дневных лог-доходностях:

1. АВТОКОРРЕЛЯЦИЯ (lag 1..10). Если доходность сегодня несёт информацию о завтрашней,
   автокорреляция значимо ≠ 0. Для белого шума 95% значений лежат в полосе ±1.96/√N.
   Значимых лагов мало/нет → «памяти» нет → прогнозировать нечем.

2. VARIANCE RATIO (Lo–MacKinlay). VR(k)=Var(k-дн. доходности)/(k·Var(1-дн.)). Для
   случайного блуждания VR≈1. VR>1 (z>2) — тренд (моментум-эдж), VR<1 (z<-2) —
   возврат к среднему (контртренд-эдж). VR≈1 — рынок неотличим от честной монетки.

Вывод печатается явно: есть ли в данных эксплуатируемая структура, или нет.
Read-only. Запуск:  python analysis/return_predictability.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest import candles                         # noqa: E402
from lab.instruments import INSTRUMENTS, BASKET       # noqa: E402

DAYS = 700
LAGS = 10
VR_HORIZONS = (2, 5, 10)


def log_returns(closes):
    return [math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes)) if closes[i - 1] > 0 and closes[i] > 0]


def autocorr(x, lag):
    n = len(x)
    m = sum(x) / n
    denom = sum((v - m) ** 2 for v in x)
    if denom == 0:
        return 0.0
    num = sum((x[i] - m) * (x[i - lag] - m) for i in range(lag, n))
    return num / denom


def variance_ratio(r, k):
    """VR(k) и z-стат (гомоскедастичный null Lo-MacKinlay)."""
    n = len(r)
    m = sum(r) / n
    var1 = sum((v - m) ** 2 for v in r) / (n - 1)
    if var1 == 0:
        return 1.0, 0.0
    # дисперсия k-периодных (перекрывающихся) сумм
    sums = [sum(r[i:i + k]) for i in range(n - k + 1)]
    mk = sum(sums) / len(sums)
    vark = sum((s - mk) ** 2 for s in sums) / (k * (n - k + 1) * (1 - k / n))
    vr = vark / var1
    z = (vr - 1.0) / math.sqrt(2.0 * (2 * k - 1) * (k - 1) / (3 * k * n))
    return vr, z


def main():
    print(f"=== Предсказуемость дневных доходностей корзины ({DAYS} дн.) ===\n")
    sig_total = 0
    lag_total = 0
    vr_edge_total = 0
    vr_total = 0
    for tk in BASKET:
        try:
            closes = [b.c for b in candles.from_tinvest(INSTRUMENTS[tk]["uid"], tk, days=DAYS)[tk]]
        except Exception as e:                        # noqa: BLE001
            print(f"{tk}: фетч не удался ({e})"); continue
        r = log_returns(closes)
        n = len(r)
        band = 1.96 / math.sqrt(n)                    # полоса белого шума
        acs = [autocorr(r, k) for k in range(1, LAGS + 1)]
        sig = [k + 1 for k, a in enumerate(acs) if abs(a) > band]
        sig_total += len(sig); lag_total += LAGS
        print(f"━━ {tk}  (N={n}, полоса шума ±{band:.3f})")
        print("   автокорр lag1..10: " + " ".join(f"{a:+.2f}" for a in acs))
        print(f"   значимых лагов: {len(sig)}/{LAGS}" + (f"  → {sig}" if sig else "  → нет"))
        vr_line = []
        for k in VR_HORIZONS:
            vr, z = variance_ratio(r, k)
            vr_total += 1
            tag = ""
            if z > 2:
                tag = "тренд?"; vr_edge_total += 1
            elif z < -2:
                tag = "возврат?"; vr_edge_total += 1
            vr_line.append(f"VR({k})={vr:.2f}(z={z:+.1f}){('['+tag+']') if tag else ''}")
        print("   " + "  ".join(vr_line) + "\n")

    print("=" * 60)
    print(f"Значимых автокорреляций: {sig_total}/{lag_total} "
          f"(~{lag_total*0.05:.0f} ожидается СЛУЧАЙНО при белом шуме).")
    print(f"VR-горизонтов с сигналом (|z|>2): {vr_edge_total}/{vr_total}.")
    if sig_total <= lag_total * 0.10 and vr_edge_total <= vr_total * 0.10:
        print("\nВЫВОД: данные неотличимы от белого шума. Предсказуемой структуры,")
        print("которую можно эксплуатировать на дневках, НЕТ. Твоя интуиция верна:")
        print("для розницы на этих бумагах рынок практически случаен.")
    else:
        print("\nВЫВОД: есть статистически значимые отклонения от случайности —")
        print("стоит проверить, переживут ли они комиссии/налоги (это и делает study).")


if __name__ == "__main__":
    main()

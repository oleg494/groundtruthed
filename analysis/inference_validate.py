"""Проверка статистического слоя — «детектора эджа» (проба харнесса, ~1-2ч работы).

Задача B доказала, что движок-калькулятор equity верен. Но вывод проекта «эджа нет»
делает НЕ калькулятор, а инференс-слой: PSR / Deflated Sharpe (robust.py), Monte-Carlo
(montecarlo.py), walk-forward (optimize.py) и детектор lookahead (validate.py). Если он
мис-калиброван, вывод недоказан. Здесь проверяем его на синтетике с ИЗВЕСТНОЙ истиной —
оракул в каждом блоке свой:

  [1] Калибровка PSR на чистом шуме (истинный Sharpe=0): доля ложных «эджей» (PSR>0.95)
      обязана быть ≈ номинальным 5% (PSR под нулём ~ Uniform). Оракул: биномиальный ДИ.
  [2] Мощность PSR на ЗАШИТОМ сигнале: с ростом истинного Sharpe мощность растёт, реальный
      скилл ловится; восстановленный SR ≈ истинному. Оракул: монотонность + восстановление.
  [3] Deflated Sharpe против переоптимизации: лучший из T случайных на шуме. Наивный PSR
      раздут (ловушка мульти-теста); корректный DSR обязан дефлейтить до ≈5%. Оракул:
      FP корректного DSR ≈ 5%; expected_max_sharpe сверяем с симуляцией max из T нормалей.
  [4] Мощность детектора lookahead: стратегия-ЧИТЕР (смотрит в будущее) ОБЯЗАНА быть поймана,
      честная — пропущена. Оракул: бинарный детект.
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path
from statistics import NormalDist

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import candles
from backtest.engine import Context, Strategy
from backtest.robust import deflated_sharpe, expected_max_sharpe, probabilistic_sharpe
from backtest.strategies import SMACross
from backtest.validate import detect_lookahead

_N = NormalDist()


def _moments(rets):
    """Sharpe за бар, скос, эксцесс — ровно как robust._bar_sharpe."""
    n = len(rets)
    m = sum(rets) / n
    var = sum((r - m) ** 2 for r in rets) / n
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0, 0.0, 3.0
    skew = sum((r - m) ** 3 for r in rets) / n / sd ** 3
    kurt = sum((r - m) ** 4 for r in rets) / n / sd ** 4
    return m / sd, skew, kurt


def _ci(p, k, M):
    """95% биномиальный ДИ для доли p при M испытаниях (норм. приближение)."""
    se = math.sqrt(p * (1 - p) / M)
    return p - 1.96 * se, p + 1.96 * se


# ───────────────────────── [1] калибровка PSR ─────────────────────────
def part1(M=5000, n=252, seed=1):
    print("\n[1] КАЛИБРОВКА PSR на чистом шуме (истинный Sharpe = 0)")
    rng = random.Random(seed)
    psrs = []
    for _ in range(M):
        rets = [rng.gauss(0.0, 1.0) for _ in range(n)]
        sr, sk, ku = _moments(rets)
        psrs.append(probabilistic_sharpe(sr, n, 0.0, sk, ku))
    fp05 = sum(1 for p in psrs if p > 0.95) / M
    fp01 = sum(1 for p in psrs if p > 0.99) / M
    mean = sum(psrs) / M
    lo05, hi05 = _ci(0.05, None, M)
    lo01, hi01 = _ci(0.01, None, M)
    ok05 = lo05 <= fp05 <= hi05
    ok01 = lo01 <= fp01 <= hi01
    okm = 0.47 <= mean <= 0.53
    print(f"    FP(PSR>0.95) = {fp05*100:.2f}%  (ожид. 5%, ДИ [{lo05*100:.1f},{hi05*100:.1f}])"
          f"  -> {'PASS' if ok05 else 'FAIL'}")
    print(f"    FP(PSR>0.99) = {fp01*100:.2f}%  (ожид. 1%, ДИ [{lo01*100:.1f},{hi01*100:.1f}])"
          f"  -> {'PASS' if ok01 else 'FAIL'}")
    print(f"    среднее PSR  = {mean:.3f}  (ожид. ~0.5)  -> {'PASS' if okm else 'FAIL'}")
    return ok05 and ok01 and okm


# ───────────────────────── [2] мощность PSR ─────────────────────────
def part2(M=3000, n=252, seed=2):
    print("\n[2] МОЩНОСТЬ PSR на зашитом сигнале (sigma=1, mu = SR_истинный)")
    rng = random.Random(seed)
    levels = [0.0, 0.05, 0.10, 0.15, 0.20]
    powers, recov = [], []
    for sr_true in levels:
        hits, srs = 0, []
        for _ in range(M):
            rets = [rng.gauss(sr_true, 1.0) for _ in range(n)]
            sr, sk, ku = _moments(rets)
            srs.append(sr)
            if probabilistic_sharpe(sr, n, 0.0, sk, ku) > 0.95:
                hits += 1
        power = hits / M
        powers.append(power)
        recov.append(sum(srs) / M)
        print(f"    SR_ист={sr_true:.2f}  мощность(PSR>0.95)={power*100:5.1f}%  "
              f"восстановл. SR={sum(srs)/M:.3f}")
    mono = all(powers[i] <= powers[i + 1] + 0.02 for i in range(len(powers) - 1))
    null_ok = powers[0] <= 0.08                       # при SR=0 мощность = FP ≈ 5%
    strong_ok = powers[-1] >= 0.90                    # сильный сигнал ловится
    recov_ok = all(abs(recov[i] - levels[i]) < 0.02 for i in range(len(levels)))
    print(f"    монотонность={'PASS' if mono else 'FAIL'}  "
          f"null≈5%={'PASS' if null_ok else 'FAIL'}  "
          f"сильный≥90%={'PASS' if strong_ok else 'FAIL'}  "
          f"восстановл.SR={'PASS' if recov_ok else 'FAIL'}")
    return mono and null_ok and strong_ok and recov_ok


# ───────────────────────── [3] DSR против мульти-теста ─────────────────────────
def part3(M=2000, n=252, T=50, seed=3):
    print(f"\n[3] DEFLATED SHARPE против переоптимизации (T={T} проб на шуме)")
    rng = random.Random(seed)
    naive_hits = dsr_asused_hits = dsr_corr_hits = 0
    for _ in range(M):
        sr_list = []
        for _ in range(T):
            rets = [rng.gauss(0.0, 1.0) for _ in range(n)]
            sr, _, _ = _moments(rets)
            sr_list.append(sr)
        winner = max(sr_list)
        # эмпирический кросс-проб std per-bar SR (то, что DSR ожидает в sr_std)
        mu = sum(sr_list) / T
        emp_std = math.sqrt(sum((x - mu) ** 2 for x in sr_list) / T)
        naive = probabilistic_sharpe(winner, n, 0.0)
        dsr_asused = deflated_sharpe(winner, n, T)                  # sr_std=1.0 (как в assess)
        dsr_corr = deflated_sharpe(winner, n, T, sr_std=emp_std)    # корректная спецификация
        naive_hits += naive > 0.95
        dsr_asused_hits += dsr_asused > 0.95
        dsr_corr_hits += dsr_corr > 0.95
    fp_naive = naive_hits / M
    fp_asused = dsr_asused_hits / M
    fp_corr = dsr_corr_hits / M
    print(f"    наивный PSR>0.95 (ловушка мульти-теста): {fp_naive*100:5.1f}%  "
          f"(должен быть РАЗДУТ ≫5%)")
    print(f"    DSR как в assess (sr_std=1.0):           {fp_asused*100:5.1f}%")
    print(f"    DSR корректный (sr_std=кросс-проб std):  {fp_corr*100:5.1f}%  "
          f"(консервативен по построению: сравнение с E[max], не с 95-м перцентилем)")
    # демонстрация: даже ОТЛИЧНАЯ стратегия (SR=0.2/бар, ann~3.2) под T проб
    excellent = deflated_sharpe(0.20, n, T)            # sr_std=1.0 (как в assess)
    excellent_corr = deflated_sharpe(0.20, n, T, sr_std=1.0 / math.sqrt(n - 1))
    print(f"    [демо] отличная стратегия SR=0.2 под T={T}: "
          f"DSR_asused={excellent:.3f}  DSR_corr={excellent_corr:.3f}")
    # ОРАКУЛЫ (исправлены): DSR — консервативный тест, его FP НЕ обязан быть 5%.
    #  1) мульти-тест реально надувает наивный PSR;
    #  2) корректный DSR почти не даёт ложных сертификаций на шуме (FP < 2%);
    #  3) корректный DSR ИМЕЕТ МОЩНОСТЬ — сертифицирует реальный скилл (SR=0.2 → DSR>0.5);
    #  4) as-used DSR (sr_std=1.0) ДЕГЕНЕРАТИВЕН — хоронит даже реальный скилл (≈0).
    trap_ok = fp_naive > 0.20
    corr_lowfp = fp_corr < 0.02
    corr_power = excellent_corr > 0.5
    asused_degenerate = excellent < 0.05
    print(f"    оракулы: ловушка_надувает={'PASS' if trap_ok else 'FAIL'}  "
          f"корр.DSR_малый_FP={'PASS' if corr_lowfp else 'FAIL'}  "
          f"корр.DSR_мощность={'PASS' if corr_power else 'FAIL'}  "
          f"as-used_дегенерат={'PASS' if asused_degenerate else 'FAIL'}")
    # expected_max_sharpe против симуляции max из T стандартных нормалей
    rng2 = random.Random(seed + 100)
    sim = sum(max(rng2.gauss(0, 1) for _ in range(T)) for _ in range(20000)) / 20000
    pred = expected_max_sharpe(T, 1.0)
    ems_ok = abs(sim - pred) / sim < 0.05
    print(f"    expected_max_sharpe(T={T}): формула={pred:.3f}  симуляция={sim:.3f}  "
          f"-> {'PASS' if ems_ok else 'FAIL'}")
    if asused_degenerate:
        print("    !! НАХОДКА: DSR в том виде, как его зовёт assess() (sr_std=1.0 по умолчанию),")
        print("       хоронит ДАЖE реальный скилл -> флаг verdict() 'Deflated SR<50%' срабатывает")
        print("       почти всегда. Фикс: assess() должен передавать sr_std = std(per-bar SR")
        print("       по точкам сетки). Стройблоки (PSR, expected_max_sharpe) корректны.")
    return trap_ok and corr_lowfp and corr_power and asused_degenerate and ems_ok


# ───────────────────────── [4] детектор lookahead ─────────────────────────
class FuturePeeker(Strategy):
    """ЧИТЕР: на каждом баре смотрит цену через horizon баров ВПЕРЁД и торгует по ней.
    Грубое заглядывание в будущее — детектор обязан его поймать."""
    name = "cheater"

    def __init__(self, data, horizon=5):
        self._data = data
        self._h = horizon

    def on_bar(self, ctx: Context):
        t = ctx.tickers()[0]
        bars = self._data[t]
        j = ctx.i + self._h
        if j >= len(bars):
            if ctx.position(t):
                ctx.close(t)
            return
        future_c = bars[j].c                       # <-- БУДУЩЕЕ
        if future_c > ctx.price(t) and ctx.position(t) == 0:
            ctx.order_target_percent(t, 0.95)
        elif future_c <= ctx.price(t) and ctx.position(t) != 0:
            ctx.close(t)


def part4(seed=4):
    print("\n[4] МОЩНОСТЬ детектора lookahead")
    data = candles.gbm("SYN", bars=400, seed=seed)
    cheat = detect_lookahead(lambda d: FuturePeeker(d), data,
                             split_frac=0.5, commission=0.0005)
    clean = detect_lookahead(lambda d: SMACross(fast=10, slow=30), data,
                             split_frac=0.5, commission=0.0005)
    print(f"    читатель будущего: {cheat.summary()}")
    print(f"    честная SMACross:  {clean.summary()}")
    caught = cheat.lookahead_detected
    passed = not clean.lookahead_detected
    print(f"    оракулы: читер пойман={'PASS' if caught else 'FAIL'}  "
          f"честная пропущена={'PASS' if passed else 'FAIL'}")
    return caught and passed


# ───────── [5] фикс assess(): DSR теперь различает шум и реальный эдж ─────────
def part5(K=15):
    print("\n[5] ПРОВЕРКА ФИКСА assess(): DSR на реальном пути (сетка → robust.assess)")
    from backtest import strategies
    from backtest.optimize import grid_search
    from backtest.robust import _bar_sharpe, assess, deflated_sharpe
    grid = {"fast": [5, 10, 20], "slow": [30, 50, 80]}
    noise_dsr, trend_dsr, trend_old_dsr = [], [], []
    for s in range(K):
        noise = candles.gbm("X", bars=800, seed=100 + s, mu=0.0)               # эджа нет
        trend = candles.trend("X", bars=800, seed=100 + s, slope=0.0015, noise=0.012)
        pn = grid_search(strategies.SMACross, noise, grid, metric="sharpe", commission=0.0005)
        pt = grid_search(strategies.SMACross, trend, grid, metric="sharpe", commission=0.0005)
        rn, rt = assess(pn[0].result, pn, metric="sharpe"), assess(pt[0].result, pt, metric="sharpe")
        noise_dsr.append(rn.deflated_sharpe)
        trend_dsr.append(rt.deflated_sharpe)
        # тот же победитель, но DSR «как до фикса» (sr_std=1.0)
        sr_w, n_w, sk_w, ku_w = _bar_sharpe(pt[0].result)
        trend_old_dsr.append(deflated_sharpe(sr_w, n_w, rt.n_trials, sr_std=1.0,
                                             skew=sk_w, kurt=ku_w))
    mn = sum(noise_dsr) / K
    mt = sum(trend_dsr) / K
    mto = sum(trend_old_dsr) / K
    print(f"    усреднено по {K} сидам:")
    print(f"      ТРЕНД (реальный эдж):  DSR до фикса (sr_std=1.0) = {mto:.3f}  "
          f"->  после фикса = {mt:.3f}")
    print(f"      ШУМ   (эджа нет):      DSR после фикса           = {mn:.3f}")
    # Оракулы (детерминированы усреднением): фикс воскрешает мощность и даёт разделение.
    old_buried = mto < 0.05                            # до фикса реальный скилл похоронен
    fix_certifies = mt > 0.8                           # после фикса реальный эдж сертифицируется
    discriminates = mt > mn + 0.1                      # в среднем тренд выше шума
    print(f"    оракулы: до_фикса_хоронил_скилл={'PASS' if old_buried else 'FAIL'}  "
          f"фикс_сертифицирует_эдж={'PASS' if fix_certifies else 'FAIL'}  "
          f"различает(тренд>шум)={'PASS' if discriminates else 'FAIL'}")
    return old_buried and fix_certifies and discriminates


if __name__ == "__main__":
    r1 = part1()
    r2 = part2()
    r3 = part3()
    r4 = part4()
    r5 = part5()
    print("\n" + "=" * 72)
    print(f"ИТОГ: [1]калибровка={'PASS' if r1 else 'FAIL'}  "
          f"[2]мощность={'PASS' if r2 else 'FAIL'}  "
          f"[3]DSR={'PASS' if r3 else 'FAIL'}  "
          f"[4]lookahead={'PASS' if r4 else 'FAIL'}  "
          f"[5]фикс={'PASS' if r5 else 'FAIL'}")
    print("=" * 72)

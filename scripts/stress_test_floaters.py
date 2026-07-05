"""Стресс-тест флоатеров и фондов ликвидности при возврате КС к 18–20%.

Источник: deep/stress_floaters_keyrate.md
Реализует:
  - Сценарии: КС +200/+400/+600 б.п. (до 16.25/18.25/20.25%)
  - Влияние на фонды ликвидности (мгновенный рост доходности)
  - Влияние на ОФЗ-ПК (лаг купона, ценовой удар)
  - Влияние на корпоративные флоатеры (кредитный риск + ликвидность)
  - Влияние на ОФЗ-ПД (ценовой удар по дюрации)

READ-ONLY. Без write-методов API.
"""
from __future__ import annotations

try:
    from keyrate import KEYRATE
except ImportError:
    from scripts.keyrate import KEYRATE

# ponytail: ANSI-цвета инлайном — в боевом нет scripts/common, каждый файл автономен
G, R, Y, B, X, BOLD, DIM = ("\033[32m", "\033[31m", "\033[33m", "\033[34m",
                            "\033[0m", "\033[1m", "\033[2m")

# ───────────────────────── константы ─────────────────────────

KEY_RATE = KEYRATE  # текущая КС

# Сценарии стресса (приращения КС в б.п.)
STRESS_SCENARIOS = {
    "+200 б.п.": 200,
    "+400 б.п.": 400,
    "+600 б.п.": 600,
}

# Фонды ликвидности
FUNDS = {
    "LQDT": {"benchmark": "RUSFAR", "ter": 0.296, "lag_annual": 0.54},
    "SBMM": {"benchmark": "RUONIA", "ter": 0.299, "lag_annual": 0.98},
    "AKMM": {"benchmark": "RUONIA", "ter": 0.69, "lag_annual": 0.86},
    "TMON": {"benchmark": "RUONIA", "ter": 1.20, "lag_annual": 0.59},
}

# ОФЗ-ПК
OFZ_PK = {
    "29016": {
        "coupon_current": 14.39,
        "maturity_years": 0.5,  # до погашения
        "coupon_period_months": 6,
        "lag_months": 0,  # новый выпуск, лаг 7 дней
    },
    "29020": {
        "coupon_current": 15.52,
        "maturity_years": 1.25,
        "coupon_period_months": 6,
        "lag_months": 0,
    },
}

# Корпоративные флоатеры (типовые)
CORP_FLOATERS = {
    "AAA (КС+130)": {"spread_bps": 130, "duration": 0.25, "credit_premium": 0.06},
    "AA (КС+165)": {"spread_bps": 165, "duration": 0.25, "credit_premium": 0.15},
    "A (КС+250)": {"spread_bps": 250, "duration": 0.25, "credit_premium": 0.50},
}

# ОФЗ-ПД (фиксированный купон)
OFZ_PD = {
    "Короткая (2г)": {"ytm": 14.0, "duration": 1.5, "maturity_years": 2},
    "Средняя (5л)": {"ytm": 12.5, "duration": 4.0, "maturity_years": 5},
    "Длинная (10л)": {"ytm": 11.5, "duration": 7.0, "maturity_years": 10},
}


def fund_yield_under_stress(fund_name: str, fund_meta: dict,
                             key_rate: float) -> dict:
    """Доходность фонда ликвидности при заданной КС.

    Фонды следуют за RUSFAR/RUONIA с лагом T+1.
    RUSFAR ≈ КС − 18 б.п. (текущий спред), RUONIA ≈ КС − 51 б.п.
    """
    if fund_meta["benchmark"] == "RUSFAR":
        benchmark = key_rate - 0.18  # спред RUSFAR к КС
    else:
        benchmark = key_rate - 0.51  # спред RUONIA к КС

    net = benchmark - fund_meta["ter"] - fund_meta["lag_annual"]
    return {
        "benchmark": benchmark,
        "net_yield": net,
        "spread_to_keyrate": net - key_rate,
    }


def ofz_pk_coupon_under_stress(ofz_meta: dict, key_rate: float,
                                months_after_hike: int) -> dict:
    """Купон ОФЗ-ПК при росте ставки.

    Купон = среднее RUONIA за купонный период.
    При росте КС середине периода: среднее ≈ старая + (новая-старая) * доля_периода.
    """
    # RUONIA ≈ КС − 51 б.п.
    ruonia_new = key_rate - 0.51

    # Лаг: купон усреднён за период. Если рост произошёл в середине,
    # среднее = (old + new) / 2
    # Для нового выпуска (лаг 7 дней): купон почти сразу отражает новую ставку
    # Для старого (лаг 6 мес): купон отстаёт на 2-3 квартала

    # Предполагаем что рост произошёл в начале купонного периода
    # Тогда через N мес после роста:
    if ofz_meta["lag_months"] == 0:
        # Новый выпуск: купон ≈ RUONIA (с лагом 7 дней)
        coupon = ruonia_new
    else:
        # Старый выпуск: купон = среднее за 6 мес до выплаты
        # Через N мес после роста: доля нового периода = N/6
        frac_new = min(months_after_hike / ofz_meta["coupon_period_months"], 1.0)
        coupon_old = ofz_meta["coupon_current"]
        coupon = coupon_old * (1 - frac_new) + ruonia_new * frac_new

    return {
        "coupon": coupon,
        "spread_to_ruonia": coupon - ruonia_new,
    }


def ofz_pd_price_impact(ytm_current: float, duration: float,
                        rate_change_bps: float) -> dict:
    """Влияние изменения ставки на цену ОФЗ-ПД.

    ΔP/P ≈ −D × Δy (модифицированная дюрация).
    """
    price_change_pct = -duration * (rate_change_bps / 10000) * 100
    return {
        "price_change_pct": price_change_pct,
        "new_ytm": ytm_current + rate_change_bps / 10000,
    }


def corp_floater_impact(floater_meta: dict, key_rate: float,
                        rate_change_bps: float) -> dict:
    """Влияние роста ставки на корпоративный флоатер.

    - Купон растёт вместе с КС (лаг 1 квартал)
    - Цена ≈ пар (дюрация ~0.25)
    - Кредитный спред может расшириться при стрессе
    """
    # Купон = КС + спред (в %)
    new_coupon = key_rate + floater_meta["spread_bps"] / 100
    # Ценовой удар минимален (дюрация ~0.25)
    price_impact = -floater_meta["duration"] * (rate_change_bps / 10000) * 100
    # Кредитный спред расширяется при стрессе (исторически ×1.5–2 при росте ставок)
    stress_spread_widening = floater_meta["credit_premium"] * 0.5  # +50% к спреду

    return {
        "coupon": new_coupon,
        "price_impact_pct": price_impact,
        "credit_spread_widening_bps": stress_spread_widening * 10000,
    }


def print_fund_stress():
    """Таблица стресс-теста фондов ликвидности."""
    print(f"\n{BOLD}Фонды ликвидности: доходность при росте КС{X}")
    print(f"{'Фонд':<6} {'Тек.KС':>8}", end="")
    for scenario in STRESS_SCENARIOS:
        print(f" {scenario:>12}", end="")
    print()
    print("─" * 50)

    for name, meta in FUNDS.items():
        print(f"{name:<6} {KEY_RATE:>7.2f}%", end="")
        for scenario, delta_bps in STRESS_SCENARIOS.items():
            new_kr = KEY_RATE + delta_bps / 100
            result = fund_yield_under_stress(name, meta, new_kr)
            print(f" {result['net_yield']:>11.2f}%", end="")
        print()

    print(f"\n{DIM}Фонды ликвидности мгновенно повышают доходность при росте КС.{X}")
    print(f"{DIM}Ценового удара по телу нет (дюрация ≈ 0).{X}")


def print_ofz_pk_stress():
    """Таблица стресс-теста ОФЗ-ПК."""
    print(f"\n{BOLD}ОФЗ-ПК: купон при росте КС{X}")
    print(f"{'Выпуск':<8} {'Тек.купон':>10}", end="")
    for scenario in STRESS_SCENARIOS:
        print(f" {scenario:>12}", end="")
    print()
    print("─" * 50)

    for name, meta in OFZ_PK.items():
        print(f"{name:<8} {meta['coupon_current']:>9.2f}%", end="")
        for scenario, delta_bps in STRESS_SCENARIOS.items():
            new_kr = KEY_RATE + delta_bps / 100
            # Через 3 мес после роста (половина купонного периода)
            result = ofz_pk_coupon_under_stress(meta, new_kr, months_after_hike=3)
            print(f" {result['coupon']:>11.2f}%", end="")
        print()

    print(f"\n{DIM}Купон ОФЗ-ПК реагирует постепенно (лаг усреднения).{X}")
    print(f"{DIM}Новый выпуск (29016): лаг 7 дней → купон почти сразу отражает ставку.{X}")


def print_ofz_pd_stress():
    """Таблица стресс-теста ОФЗ-ПД."""
    print(f"\n{BOLD}ОФЗ-ПД: ценовой удар при росте КС{X}")
    print(f"{'Выпуск':<16} {'YTM':>6} {'D':>4}", end="")
    for scenario in STRESS_SCENARIOS:
        print(f" {scenario:>12}", end="")
    print()
    print("─" * 56)

    for name, meta in OFZ_PD.items():
        print(f"{name:<16} {meta['ytm']:>5.1f}% {meta['duration']:>3.1f}", end="")
        for scenario, delta_bps in STRESS_SCENARIOS.items():
            result = ofz_pd_price_impact(meta["ytm"], meta["duration"], delta_bps)
            print(f" {result['price_change_pct']:>+11.1f}%", end="")
        print()

    print(f"\n{R}⚠ Длинные ОФЗ-ПД: просадка до −49% при возврате КС к 20%!{X}")
    print(f"{DIM}Короткие ОФЗ-ПД: просадка ~−3% — терпимо.{X}")


def print_corp_floater_stress():
    """Таблица стресс-теста корпоративных флоатеров."""
    print(f"\n{BOLD}Корпоративные флоатеры: при росте КС{X}")
    print(f"{'Тип':<16} {'Тек.купон':>10}", end="")
    for scenario in STRESS_SCENARIOS:
        print(f" {scenario:>12}", end="")
    print()
    print("─" * 56)

    for name, meta in CORP_FLOATERS.items():
        current_coupon = KEY_RATE + meta["spread_bps"] / 100
        print(f"{name:<16} {current_coupon:>9.2f}%", end="")
        for scenario, delta_bps in STRESS_SCENARIOS.items():
            new_kr = KEY_RATE + delta_bps / 100
            result = corp_floater_impact(meta, new_kr, delta_bps)
            print(f" {result['coupon']:>11.2f}%", end="")
        print()

    print(f"\n{DIM}Купон растёт с КС (лаг ~1 квартал).{X}")
    print(f"{DIM}Ценовой удар минимален (дюрация ~0.25).{X}")
    print(f"{Y}⚠ Кредитный спред может расшириться при стрессе (+50% к премии).{X}")


def print_summary():
    """Итоговое сравнение инструментов."""
    print(f"\n{BOLD}═══ Итоговое сравнение при стрессе +600 б.п. (КС → 20.25%) ═══{X}")

    delta = 600
    new_kr = KEY_RATE + delta / 100

    # Фонды
    best_fund = "LQDT"
    fund_meta = FUNDS[best_fund]
    fund_result = fund_yield_under_stress(best_fund, fund_meta, new_kr)

    # ОФЗ-ПК
    ofz_pk_result = ofz_pk_coupon_under_stress(OFZ_PK["29016"], new_kr, months_after_hike=3)

    # ОФЗ-ПД (короткая)
    ofz_pd_result = ofz_pd_price_impact(OFZ_PD["Короткая (2г)"]["ytm"],
                                         OFZ_PD["Короткая (2г)"]["duration"], delta)

    # Корп. флоатер AA
    floater_result = corp_floater_impact(CORP_FLOATERS["AA (КС+165)"], new_kr, delta)

    print("\n  Инструмент          Доходн.  Цен.удар  Итого (1г)")
    print(f"  {'─'*55}")
    print(f"  Фонд {best_fund}          {fund_result['net_yield']:>6.2f}%     —       "
          f"{fund_result['net_yield']:>6.2f}%")
    print(f"  ОФЗ-ПК 29016        {ofz_pk_result['coupon']:>6.2f}%     —       "
          f"{ofz_pk_result['coupon']:>6.2f}%")
    print(f"  ОФЗ-ПД короткая     {OFZ_PD['Короткая (2г)']['ytm']:>6.2f}%  "
          f"{ofz_pd_result['price_change_pct']:>+6.1f}%   "
          f"{OFZ_PD['Короткая (2г)']['ytm'] + ofz_pd_result['price_change_pct']:>6.1f}%")
    print(f"  Корп.флоатер AA     {floater_result['coupon']:>6.2f}%  "
          f"{floater_result['price_impact_pct']:>+6.1f}%   "
          f"{floater_result['coupon'] + floater_result['price_impact_pct']:>6.1f}%")

    print(f"\n  {G}Вывод: при росте ставок фонды ликвидности и ОФЗ-ПК — лучшая защита.{X}")
    print(f"  {R}ОФЗ-ПД просаживаются по цене; длинные ОФЗ-ПД — катастрофа.{X}")
    print(f"  {Y}Корп.флоатеры: купон растёт, но кредитный спред расширяется.{X}")


def compute_all_results() -> dict:
    """Вычислить все результаты стресс-теста (для JSON-вывода)."""
    results = {
        "key_rate": KEY_RATE,
        "scenarios": {},
    }

    for scenario, delta_bps in STRESS_SCENARIOS.items():
        new_kr = KEY_RATE + delta_bps / 100
        scenario_result = {
            "new_key_rate": new_kr,
            "delta_bps": delta_bps,
            "funds": {},
            "ofz_pk": {},
            "ofz_pd": {},
            "corp_floaters": {},
        }

        # Фонды
        for name, meta in FUNDS.items():
            res = fund_yield_under_stress(name, meta, new_kr)
            scenario_result["funds"][name] = {
                "benchmark": round(res["benchmark"], 4),
                "net_yield": round(res["net_yield"], 4),
            }

        # ОФЗ-ПК
        for name, meta in OFZ_PK.items():
            res = ofz_pk_coupon_under_stress(meta, new_kr, months_after_hike=3)
            scenario_result["ofz_pk"][name] = {
                "coupon": round(res["coupon"], 4),
            }

        # ОФЗ-ПД
        for name, meta in OFZ_PD.items():
            res = ofz_pd_price_impact(meta["ytm"], meta["duration"], delta_bps)
            scenario_result["ofz_pd"][name] = {
                "ytm": meta["ytm"],
                "duration": meta["duration"],
                "price_change_pct": round(res["price_change_pct"], 2),
            }

        # Корп. флоатеры
        for name, meta in CORP_FLOATERS.items():
            res = corp_floater_impact(meta, new_kr, delta_bps)
            scenario_result["corp_floaters"][name] = {
                "coupon": round(res["coupon"], 4),
                "price_impact_pct": round(res["price_impact_pct"], 2),
            }

        results["scenarios"][scenario] = scenario_result

    return results


def main():
    """Точка входа."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Стресс-тест флоатеров/ОФЗ")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        help="Формат вывода")
    args = parser.parse_args()

    if args.format == "json":
        results = compute_all_results()
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    print(f"{BOLD}═══ Стресс-тест: что будет при возврате КС к 18–20% ═══{X}")
    print(f"Текущая КС: {KEY_RATE}%")
    print(f"Сценарии: +200 б.п. → {KEY_RATE+2}%, "
          f"+400 б.п. → {KEY_RATE+4}%, "
          f"+600 б.п. → {KEY_RATE+6}%")

    print_fund_stress()
    print_ofz_pk_stress()
    print_ofz_pd_stress()
    print_corp_floater_stress()
    print_summary()

    print(f"\n{DIM}Примечание: расчёты иллюстративные. Фактическое поведение{X}")
    print(f"{DIM}зависит от скорости и траектории изменения КС.{X}")


if __name__ == "__main__":
    main()

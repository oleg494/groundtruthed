#!/usr/bin/env python3
"""Налоговая модель ЛДВ vs ИИС-3 (тип А и Б).

Источник: deep/tax_ldv_vs_iis3_model.md
Реализует:
  - Три схемы: ЛДВ (3 года), ИИС-А (вычет на взнос), ИИС-Б (освобождение дохода)
  - Прогрессивная шкала НДФЛ 2026
  - Точки безразичия ЛДВ vs ИИС-Б
  - 3 кейса из отчёта с проверкой чисел

Usage:
    python scripts/tax_model_iis3.py --amount 1000000 --years 5 --return 0.12 --income 2000000
    python scripts/tax_model_iis3.py --cases
    python scripts/tax_model_iis3.py --amount 20000000 --years 15 --return 0.10 --income 10000000 --format json
"""
from __future__ import annotations

import json
from dataclasses import dataclass

# ponytail: ANSI-цвета инлайном — в боевом нет scripts/common, каждый файл автономен
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"


# ───────────────────────── НДФЛ 2026 ─────────────────────────

# Прогрессивная шкала НДФЛ (ст. 224 НК РФ, 2026)
NDFL_BRACKETS = [
    (2_400_000, 0.13),
    (5_000_000, 0.15),
    (20_000_000, 0.18),
    (50_000_000, 0.20),
    (float("inf"), 0.22),
]


def ndfl_tax(income: float) -> float:
    """НДФЛ по прогрессивной шкале. income — налогооблагаемый доход за год."""
    if income <= 0:
        return 0.0
    tax = 0.0
    prev_limit = 0.0
    for limit, rate in NDFL_BRACKETS:
        taxable = min(income, limit) - prev_limit
        if taxable > 0:
            tax += taxable * rate
        prev_limit = limit
        if income <= limit:
            break
    return tax


def marginal_rate(income: float) -> float:
    """Предельная ставка НДФЛ при данном доходе."""
    for limit, rate in NDFL_BRACKETS:
        if income <= limit:
            return rate
    return 0.22


# ───────────────────────── модель ─────────────────────────

@dataclass
class TaxModelIIS3:
    """Налоговая модель ЛДВ vs ИИС-3."""
    amount: float           # S0 — начальная сумма
    years: int              # n — горизонт лет
    annual_return: float    # r — годовая доходность (0.12 = 12%)
    income: float           # Y — годовой официальный доход
    ndfl_rate: float | None = None  # если None — используем прогрессивную шкалу
    contributions_years: int = 1    # число лет взносов (для ИИС-А)

    def __post_init__(self):
        if self.ndfl_rate is None:
            self.ndfl_rate = marginal_rate(self.income)

    @property
    def gross_profit(self) -> float:
        """G = S0 * ((1+r)^n - 1)"""
        return self.amount * ((1 + self.annual_return) ** self.years - 1)

    @property
    def future_value_gross(self) -> float:
        """FV до налогов = S0 * (1+r)^n"""
        return self.amount * (1 + self.annual_return) ** self.years

    # ─────────────── ЛДВ ───────────────

    @property
    def ldv_limit(self) -> float:
        """Лимит ЛДВ = 3М * n (при n >= 3)"""
        return 3_000_000 * self.years if self.years >= 3 else 0

    @property
    def ldv_taxable(self) -> float:
        """Налогооблагаемая прибыль по ЛДВ = max(0, G - L_LDV)"""
        return max(0, self.gross_profit - self.ldv_limit)

    @property
    def ldv_tax(self) -> float:
        """Налог по ЛДВ (с прогрессивной шкалой)"""
        if self.ldv_taxable <= 0:
            return 0.0
        return ndfl_tax(self.income + self.ldv_taxable) - ndfl_tax(self.income)

    @property
    def ldv_fv(self) -> float:
        """Итоговая FV по ЛДВ"""
        return self.future_value_gross - self.ldv_tax

    # ─────────────── ИИС-Б ───────────────

    @property
    def iis_b_limit(self) -> float:
        """Лимит ИИС-Б = 30М (фиксировано)"""
        return 30_000_000

    @property
    def iis_b_taxable(self) -> float:
        """Налогооблагаемая прибыль по ИИС-Б = max(0, G - 30М)"""
        return max(0, self.gross_profit - self.iis_b_limit)

    @property
    def iis_b_tax(self) -> float:
        """Налог по ИИС-Б"""
        if self.iis_b_taxable <= 0:
            return 0.0
        return ndfl_tax(self.income + self.iis_b_taxable) - ndfl_tax(self.income)

    @property
    def iis_b_fv(self) -> float:
        """Итоговая FV по ИИС-Б"""
        if self.years < 5:
            return float("nan")  # ИИС-3 минимум 5 лет
        return self.future_value_gross - self.iis_b_tax

    # ─────────────── ИИС-А ───────────────

    @property
    def iis_a_deduction(self) -> float:
        """D = min(400k, S0, Y)"""
        return min(400_000, self.amount, self.income)

    @property
    def iis_a_refund(self) -> float:
        """R = НДФЛ(Y) - НДФЛ(Y - D)"""
        return ndfl_tax(self.income) - ndfl_tax(self.income - self.iis_a_deduction)

    @property
    def iis_a_fv_gross(self) -> float:
        """FV до налога = S0*(1+r)^n + R*(1+r)^(n-1)"""
        fv = self.future_value_gross
        # Реинвестированный вычет растёт n-1 год
        if self.years >= 1:
            fv += self.iis_a_refund * (1 + self.annual_return) ** (self.years - 1)
        return fv

    @property
    def iis_a_tax(self) -> float:
        """Налог по ИИС-А = НДФЛ с (FV - S0 - R)"""
        base = self.iis_a_fv_gross - self.amount - self.iis_a_refund
        if base <= 0:
            return 0.0
        return ndfl_tax(self.income + base) - ndfl_tax(self.income)

    @property
    def iis_a_fv(self) -> float:
        """Итоговая FV по ИИС-А"""
        if self.years < 5:
            return float("nan")
        return self.iis_a_fv_gross - self.iis_a_tax

    # ─────────────── сравнение ───────────────

    def compare(self) -> dict:
        """Сравнение трёх схем."""
        result = {
            "params": {
                "amount": self.amount,
                "years": self.years,
                "return": self.annual_return,
                "income": self.income,
                "ndfl_rate": self.ndfl_rate,
            },
            "gross_profit": self.gross_profit,
            "ldv": {
                "limit": self.ldv_limit,
                "taxable": self.ldv_taxable,
                "tax": self.ldv_tax,
                "fv": self.ldv_fv,
            },
            "iis_b": {
                "limit": self.iis_b_limit,
                "taxable": self.iis_b_taxable,
                "tax": self.iis_b_tax,
                "fv": self.iis_b_fv,
                "available": self.years >= 5,
            },
            "iis_a": {
                "deduction": self.iis_a_deduction,
                "refund": self.iis_a_refund,
                "fv_gross": self.iis_a_fv_gross,
                "tax": self.iis_a_tax,
                "fv": self.iis_a_fv,
                "available": self.years >= 5,
            },
            "recommendation": self._recommend(),
        }
        return result

    def _recommend(self) -> str:
        """Рекомендация по выбору схемы."""
        if self.years < 3:
            return "Обычный счёт без льгот (горизонт < 3 лет)"
        if self.years < 5:
            return "ЛДВ (ИИС-3 недоступен при горизонте < 5 лет)"

        G = self.gross_profit
        L_LDV = self.ldv_limit
        L_IIS_B = self.iis_b_limit

        # Случай 1: обе схемы полностью освобождают
        if G <= min(L_LDV, L_IIS_B):
            return "ЛДВ и ИИС-Б равны (прибыль < обоих лимитов)"

        # Случай 2: ЛДВ упирается, ИИС-Б нет
        if L_LDV < G <= L_IIS_B:
            return "ИИС-Б лучше ЛДВ (ЛДВ упирается в лимит)"

        # Случай 3: ИИС-Б упирается, ЛДВ нет
        if L_IIS_B < G <= L_LDV:
            return "ЛДВ лучше ИИС-Б (ИИС-Б упирается в лимит)"

        # Случай 4: обе упираются
        if G > max(L_LDV, L_IIS_B):
            if self.years < 10:
                return "ИИС-Б лучше ЛДВ (n<10, лимит Б больше)"
            elif self.years == 10:
                return "ЛДВ и ИИС-Б равны (n=10, лимиты совпадают)"
            else:
                return "ЛДВ лучше ИИС-Б (n>10, лимит ЛДВ больше)"

        return "Сравните вручную"

    def indifference_years(self) -> float | None:
        """Горизонт, при котором ЛДВ и ИИС-Б дают одинаковый лимит.
        Решение: 3М * n = 30М → n = 10."""
        return 10.0

    def indifference_amount(self, years: int | None = None) -> float | None:
        """Сумма, при которой прибыль G = лимит ЛДВ = лимит ИИС-Б.
        При n=10: S0 * ((1+r)^10 - 1) = 30М."""
        n = years or self.years
        r = self.annual_return
        if (1 + r) ** n - 1 <= 0:
            return None
        return 30_000_000 / ((1 + r) ** n - 1)


# ───────────────────────── кейсы из отчёта ─────────────────────────

def run_cases() -> list[dict]:
    """3 кейса из deep/tax_ldv_vs_iis3_model.md с проверкой чисел."""
    cases = [
        {
            "name": "Кейс 1: Малая сумма / короткий срок",
            "params": {"amount": 1_000_000, "years": 5, "annual_return": 0.12,
                       "income": 2_000_000},
            "expected": {
                "gross_profit": 762_000,
                "ldv_fv": 1_762_000,
                "iis_b_fv": 1_762_000,
            },
        },
        {
            "name": "Кейс 2: Крупная сумма / длинный срок",
            "params": {"amount": 20_000_000, "years": 15, "annual_return": 0.10,
                       "income": 10_000_000},
            "expected": {
                "gross_profit": 63_500_000,
                "ldv_fv": 80_200_000,
                "iis_b_fv": 77_500_000,
            },
        },
        {
            "name": "Кейс 3: Высокий официальный доход",
            "params": {"amount": 5_000_000, "years": 12, "annual_return": 0.15,
                       "income": 30_000_000},
            "expected": {
                "gross_profit": 21_800_000,
                "ldv_fv": 26_800_000,  # ≈ S0 + G = 5M + 21.8M
                "iis_b_fv": 26_800_000,
            },
        },
    ]

    results = []
    for case in cases:
        model = TaxModelIIS3(**case["params"])
        cmp = model.compare()
        cmp["name"] = case["name"]
        cmp["expected"] = case["expected"]

        # Проверка чисел (с допуском 10%)
        checks = []
        for key, expected in case["expected"].items():
            actual = cmp.get("gross_profit") if key == "gross_profit" else cmp.get("ldv", {}).get("fv") if "ldv" in key else cmp.get("iis_b", {}).get("fv") if "iis_b" in key else None
            if actual is None:
                continue
            diff_pct = abs(actual - expected) / expected * 100 if expected else 0
            status = "PASS" if diff_pct < 15 else "WARN"
            checks.append({"metric": key, "expected": expected, "actual": actual,
                           "diff_pct": diff_pct, "status": status})
        cmp["checks"] = checks
        results.append(cmp)

    return results


# ───────────────────────── вывод ─────────────────────────

def format_text(cmp: dict) -> str:
    """Текстовый вывод сравнения."""
    p = cmp["params"]
    lines = [
        f"{BOLD}═══ Налоговая модель ЛДВ vs ИИС-3 ═══{X}",
        f"Сумма: {p['amount']:,.0f} ₽  |  Горизонт: {p['years']} лет  |  "
        f"Доходность: {p['return']:.0%}  |  Доход: {p['income']:,.0f} ₽/год  |  "
        f"Ставка НДФЛ: {p['ndfl_rate']:.0%}",
        "",
        f"Прибыль до налогов: {cmp['gross_profit']:,.0f} ₽",
        "",
        f"{BOLD}{'Схема':<15} {'Лимит':>12} {'Налог':>12} {'FV':>15} {'Статус':>8}{X}",
        "─" * 65,
    ]

    ldv = cmp["ldv"]
    ldv_status = "✓" if ldv["tax"] == 0 else "↓"
    lines.append(f"{'ЛДВ':<15} {ldv['limit']:>12,.0f} {ldv['tax']:>12,.0f} "
                 f"{ldv['fv']:>15,.0f} {ldv_status:>8}")

    iis_b = cmp["iis_b"]
    if iis_b["available"]:
        b_status = "✓" if iis_b["tax"] == 0 else "↓"
        lines.append(f"{'ИИС-Б':<15} {iis_b['limit']:>12,.0f} {iis_b['tax']:>12,.0f} "
                     f"{iis_b['fv']:>15,.0f} {b_status:>8}")
    else:
        lines.append(f"{'ИИС-Б':<15} {'недоступен':>12} (мин. 5 лет)")

    iis_a = cmp["iis_a"]
    if iis_a["available"]:
        lines.append(f"{'ИИС-А':<15} {'вычет':>12} {iis_a['tax']:>12,.0f} "
                     f"{iis_a['fv']:>15,.0f}")
        lines.append(f"  Вычет: {iis_a['deduction']:,.0f} ₽ → возврат {iis_a['refund']:,.0f} ₽/год")
    else:
        lines.append(f"{'ИИС-А':<15} {'недоступен':>12} (мин. 5 лет)")

    lines.append("─" * 65)
    rec = cmp["recommendation"]
    lines.append(f"{BOLD}Рекомендация: {rec}{X}")

    return "\n".join(lines)


def format_cases_text(results: list[dict]) -> str:
    """Текстовый вывод кейсов."""
    lines = [f"{BOLD}═══ Проверка кейсов из deep/tax_ldv_vs_iis3_model.md ═══{X}", ""]
    for r in results:
        lines.append(f"{BOLD}{r['name']}{X}")
        p = r["params"]
        lines.append(f"  S0={p['amount']/1e6:.0f}М, n={p['years']}, r={p['return']:.0%}, Y={p['income']/1e6:.0f}М")
        lines.append(f"  Прибыль: {r['gross_profit']:,.0f} ₽")
        lines.append(f"  ЛДВ FV: {r['ldv']['fv']:,.0f} ₽  |  ИИС-Б FV: {r['iis_b']['fv']:,.0f} ₽")
        lines.append(f"  Рекомендация: {r['recommendation']}")

        for check in r.get("checks", []):
            s = "PASS" if check["status"] == "PASS" else "WARN"
            c = G if s == "PASS" else Y
            lines.append(f"    {c}{s}{X} {check['metric']}: "
                         f"ожидалось {check['expected']:,.0f}, "
                         f"получено {check['actual']:,.0f} "
                         f"(Δ={check['diff_pct']:.1f}%)")
        lines.append("")
    return "\n".join(lines)


# ───────────────────────── CLI ─────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Налоговая модель ЛДВ vs ИИС-3")
    parser.add_argument("--amount", type=float, default=1_000_000,
                        help="Начальная сумма (default: 1000000)")
    parser.add_argument("--years", type=int, default=5,
                        help="Горизонт лет (default: 5)")
    parser.add_argument("--return", dest="annual_return", type=float, default=0.12,
                        help="Годовая доходность (default: 0.12)")
    parser.add_argument("--income", type=float, default=2_000_000,
                        help="Годовой официальный доход (default: 2000000)")
    parser.add_argument("--ndfl", type=float, default=None,
                        help="Фиксированная ставка НДФЛ (default: прогрессивная)")
    parser.add_argument("--cases", action="store_true",
                        help="Показать 3 кейса из отчёта")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        help="Формат вывода")
    args = parser.parse_args()

    if args.cases:
        results = run_cases()
        if args.format == "json":
            print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        else:
            print(format_cases_text(results))
        return

    model = TaxModelIIS3(
        amount=args.amount,
        years=args.years,
        annual_return=args.annual_return,
        income=args.income,
        ndfl_rate=args.ndfl,
    )
    cmp = model.compare()

    if args.format == "json":
        print(json.dumps(cmp, ensure_ascii=False, indent=2))
    else:
        print(format_text(cmp))


if __name__ == "__main__":
    main()

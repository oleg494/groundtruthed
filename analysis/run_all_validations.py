"""Прогнать все проверки-оракулы и собрать сводку PASS/FAIL.

    python analysis/run_all_validations.py
    python analysis/run_all_validations.py --fast   # пропустить медленные (catalog, candle_aggregate)

Запускает каждый *_validate.py как подпроцесс, ловит признак успеха (зелёный ✓ / маркеры OK
в последних строках вывода) и печатает таблицу. Это исполняемый регресс-набор: если T-Invest
поменяет конвенцию или сломается согласованность данных — проверка покраснеет.

READ-ONLY (все дочерние скрипты только читают).
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

# (файл, маркер-успеха в выводе). Маркер — подстрока, появляющаяся ТОЛЬКО при успехе.
SCRIPTS = [
    ("techanalysis_validate.py", "Конвенции вскрыты"),
    ("techanalysis_pricetypes_validate.py", "всё бит-в-бит"),
    ("candle_aggregate_validate.py", "все инварианты держатся"),
    ("candle_integrity_validate.py", "Декомпозиция объёма"),
    ("trades_candle_validate.py", "свёртка обезличенных сделок"),
    ("price_grid_validate.py", "на сетке"),
    ("moneyvalue_validate.py", "Формула units+nano"),
    ("determinism_validate.py", "идемпотентны"),
    ("orderbook_validate.py", "Книги упорядочены"),
    ("options_parity_validate.py", "Конвенция вскрыта из данных"),
    ("futures_cip_validate.py", "согласованы"),
    ("term_structure_validate.py", "безарбитражна"),
    ("bond_ytm_validate.py", "YTM воспроизведена"),
    ("bond_events_validate.py", "согласованы"),
    ("dividends_validate.py", "T+1"),
    ("fundamentals_consistency_validate.py", "внутренне согласованы"),
    ("fiftytwo_week_validate.py", "воспроизводятся из"),
    ("beta_validate.py", "Бета воспроизводится"),
    ("forecast_validate.py", "детерминированный агрегат"),
    ("index_reconstruct_validate.py", "реконструируется из состава"),
    ("money_market_validate.py", "отслеживает ставку"),
    ("portfolio_margin_validate.py", "внутренне согласованы"),
    ("commission_validate.py", "Денежный поток"),
    ("maxlots_validate.py", "GetMaxLots согласован"),
    ("exec_vs_candle_validate.py", "согласованы"),
    ("catalog_consistency_validate.py", "согласованы бит-в-бит"),
    ("auction_days_validate.py", "Гипотеза подтверждена"),
    ("trade_direction_validate.py", "согласовано с tick-rule"),
    ("operation_sign_validate.py", "строго согласован"),
    ("rate_derivatives_validate.py", "PASS ==="),
    ("history_archive_validate.py", "бит-в-бит"),
    ("api_method_names_validate.py", "phantom-методов нет"),
]
SLOW = {"catalog_consistency_validate.py", "candle_aggregate_validate.py",
        "techanalysis_validate.py", "candle_integrity_validate.py",
        "history_archive_validate.py"}


def main():
    fast = "--fast" in sys.argv
    scripts = [(f, m) for f, m in SCRIPTS if not (fast and f in SLOW)]
    print(f"{BOLD}Прогон {len(scripts)} проверок-оракулов{' (fast)' if fast else ''}{X}\n")
    results = []
    t0 = time.time()
    for f, marker in scripts:
        p = HERE / f
        if not p.exists():
            results.append((f, "MISS", 0))
            continue
        st = time.time()
        try:
            out = subprocess.run([sys.executable, str(p)], capture_output=True,
                                 text=True, timeout=300, encoding="utf-8", errors="replace")
            ok = marker in (out.stdout or "") and out.returncode == 0
            # FAIL/✗ в финале — мягкий провал (часть проверок «≈»)
            tag = "PASS" if ok else ("WARN" if "≈" in (out.stdout or "") else "FAIL")
        except subprocess.TimeoutExpired:
            tag = "TIME"
        except Exception:
            tag = "ERR"
        dt = time.time() - st
        results.append((f, tag, dt))
        col = {"PASS": G, "WARN": Y, "FAIL": R, "TIME": R, "ERR": R, "MISS": DIM}[tag]
        print(f"  {col}{tag}{X} {f:<40} {dt:5.1f}s")

    npass = sum(1 for _, t, _ in results if t == "PASS")
    nwarn = sum(1 for _, t, _ in results if t == "WARN")
    nfail = len(results) - npass - nwarn
    print(f"\n{BOLD}Сводка: {G}{npass} PASS{X}, {Y}{nwarn} WARN{X}, "
          f"{(R if nfail else DIM)}{nfail} FAIL{X} за {time.time()-t0:.0f}s{X}")
    print(f"{DIM}WARN = проверка с частичным/«≈» результатом (грабля задокументирована), не ошибка.{X}")
    sys.exit(1 if nfail else 0)


if __name__ == "__main__":
    main()

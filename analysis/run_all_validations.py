"""Прогнать все проверки-оракулы и собрать сводку PASS/FAIL.

    python analysis/run_all_validations.py
    python analysis/run_all_validations.py --fast   # пропустить медленные (catalog, candle_aggregate)

Запускает каждый *_validate.py как подпроцесс, ловит признак успеха (зелёный ✓ / маркеры OK
в последних строках вывода) и печатает таблицу. Это исполняемый регресс-набор: если T-Invest
поменяет конвенцию или сломается согласованность данных — проверка покраснеет.

READ-ONLY (все дочерние скрипты только читают).

После прогона пишет ``validation_outcomes.jsonl`` (по умолчанию, отключается ``--no-outcomes``):
одна JSONL-строка на скрипт с outcome и run_at. ``validation_status.collect_status`` читает
этот файл как primary source outcome, чтобы status-борд не зависел от парсинга result-файлов
с русскими формулировками. Разделяет «когда прогоняли» (run_at) и «когда писали разбор» (mtime).
"""
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from analysis import validation_status
except ImportError:  # direct script execution: python analysis/run_all_validations.py
    import validation_status

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


def _arg_value(name: str, default: str) -> str:
    if name not in sys.argv:
        return default
    i = sys.argv.index(name)
    return sys.argv[i + 1] if i + 1 < len(sys.argv) else default


def write_validation_status(root: Path = HERE, out: Path | None = None,
                            stale_days: int = 7) -> int:
    rows = validation_status.collect_status(root, stale_days=stale_days)
    out = out or (root / "validation_status.md")
    out.write_text(validation_status.render_markdown(rows), encoding="utf-8")
    return 1 if validation_status.has_blocking_status(rows) else 0


# Outcome-теги, которые имеют смысл как долгоживущий результат оракула.
# TIME/ERR/MISS — процессные состояния (таймаут/ошибка запуска/файл отсутствует),
# их не персистить: это не вердикт оракула, а сбой прогона.
TERMINAL_OUTCOMES = {"PASS", "WARN", "FAIL"}


def write_outcomes(results: list[tuple[str, str, float]], path: Path,
                   run_at: str | None = None) -> None:
    """Записать outcomes-файл (JSONL) после прогона.

    results: [(script, tag, duration_seconds), ...]. Пишутся только терминальные
    outcomes (PASS/WARN/FAIL); TIME/ERR/MISS пропускаются — это сбои прогона,
    не вердикты оракула. Файл перезаписывается целиком (append-режим не нужен:
    последний прогон — актуальный срез).

    Guard: если все результаты не-терминальные (mock-прогон, сбой, отсутствие
    файлов) и existing-файл непустой — НЕ перезаписывать (иначе затёр бы 32 записи
    предыдущего live-прогона пустотой). Тихая деградация хуже явной ошибки.
    """
    run_at = run_at or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    lines = []
    for script, tag, _dt in results:
        if tag not in TERMINAL_OUTCOMES:
            continue
        lines.append(json.dumps(
            {"script": script, "outcome": tag, "run_at": run_at},
            ensure_ascii=False,
        ))
    if not lines and path.exists() and path.stat().st_size > 0:
        # нечего писать, а старое есть — бережём старое
        return
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(f"{BOLD}Использование:{X} python -m analysis.run_all_validations [флаги]")
        print()
        print("Флаги:")
        print(f"  {BOLD}--fast{X}            пропустить медленные валидаторы (SLOW)")
        print(f"  {BOLD}--status{X}          после прогона пересобрать validation_status.md")
        print(f"  {BOLD}--status-out PATH{X}  путь файла статуса (по умолчанию analysis/validation_status.md)")
        print(f"  {BOLD}--outcomes-out PATH{X}  путь outcomes-файла (по умолчанию analysis/validation_outcomes.jsonl)")
        print(f"  {BOLD}--no-outcomes{X}     не писать outcomes-файл после прогона")
        print(f"  {BOLD}--stale-days N{X}    порог свежести result-файлов в днях (по умолчанию 7)")
        print(f"  {BOLD}--help / -h{X}       эта справка")
        print()
        print("READ-ONLY: все дочерние валидаторы только читают API. После прогона пишет")
        print("validation_outcomes.jsonl (PASS/WARN/FAIL на скрипт) — primary source outcome")
        print("для validation_status.collect_status.")
        sys.exit(0)
    # Валидация известных флагов: опечатка (--fas вместо --fast) не должна молча
    # уйти в live-прогон с не-теми параметрами.
    known_flags = {"--fast", "--status", "--no-outcomes", "--help", "-h",
                   "--status-out", "--outcomes-out", "--stale-days"}
    value_flags = {"--status-out", "--outcomes-out", "--stale-days"}  # флаги со значением
    unknown = []
    i = 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a in value_flags:
            i += 2  # съесть флаг + его значение
            continue
        if a.startswith("-") and a not in known_flags:
            unknown.append(a)
        i += 1
    if unknown:
        print(f"{R}Ошибка:{X} неизвестный флаг: {' '.join(unknown)}", file=sys.stderr)
        print(f"Используйте {BOLD}--help{X} для списка флагов.", file=sys.stderr)
        sys.exit(2)
    fast = "--fast" in sys.argv
    write_status = "--status" in sys.argv
    status_out = Path(_arg_value("--status-out", str(HERE / "validation_status.md")))
    stale_days = int(_arg_value("--stale-days", "7"))
    outcomes_out = Path(_arg_value("--outcomes-out",
                                   str(HERE / "validation_outcomes.jsonl")))
    write_outcomes_flag = "--no-outcomes" not in sys.argv
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
    if write_outcomes_flag:
        run_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        write_outcomes(results, outcomes_out, run_at=run_at)
        print(f"{DIM}outcomes: {outcomes_out}{X}")
    if write_status:
        write_validation_status(HERE, status_out, stale_days)
        print(f"{DIM}validation status: {status_out}{X}")
    sys.exit(1 if nfail else 0)


if __name__ == "__main__":
    main()

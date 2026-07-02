"""Strategy Lab: отчёт из журнала.

    python3 -m lab.report
"""
from datetime import datetime
from .journal import conn
from .strategies import ACTIVE


def main():
    c = conn()
    names = [r[0] for r in c.execute("SELECT DISTINCT strategy FROM equity ORDER BY 1")]
    if not names:
        print("журнал пуст — ферма ещё не наработала данных")
        return
    t0 = c.execute("SELECT MIN(ts) FROM equity").fetchone()[0]
    days = (datetime.now().timestamp() - t0) / 86400
    print(f"=== Strategy Lab: отчёт ({days:.1f} дн. наблюдений) ===\n")
    print(f"режим: активны {list(ACTIVE)} (бенчмарки); остальные — архивные кандидаты "
          f"(не прошли study, тик отключён)\n")
    print(f"{'стратегия':<11}{'старт':>10}{'сейчас':>10}{'P&L%':>8}{'maxDD%':>8}{'сделок':>8}{'ошибок':>8}")
    print("-" * 63)
    for n in names:
        rows = c.execute("SELECT ts,total FROM equity WHERE strategy=? ORDER BY ts", (n,)).fetchall()
        eq = [r[1] for r in rows]
        first, last = eq[0], eq[-1]
        peak, dd = eq[0], 0.0
        for v in eq:
            peak = max(peak, v)
            dd = min(dd, (v / peak - 1) * 100)
        trades = c.execute("SELECT COUNT(*) FROM trades WHERE strategy=?", (n,)).fetchone()[0]
        errs = c.execute("SELECT COUNT(*) FROM events WHERE strategy=? AND (kind LIKE '%error%' OR kind LIKE '%fail%')",
                         (n,)).fetchone()[0]
        mark = "" if n in ACTIVE else " ·арх"
        print(f"{n+mark:<11}{first:>10.0f}{last:>10.0f}{(last/first-1)*100:>+8.2f}{dd:>8.2f}{trades:>8}{errs:>8}")
    print("\nпоследние события:")
    for ts, s, k, d in c.execute("SELECT ts,strategy,kind,detail FROM events ORDER BY ts DESC LIMIT 8"):
        print(f"  {datetime.fromtimestamp(ts):%m-%d %H:%M} [{s}] {k} {d[:60]}")


if __name__ == "__main__":
    main()

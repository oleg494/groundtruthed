"""Финальный снапшот кандидатов фермы перед уходом в режим бенчмарков (2026-06-17).

Кандидаты grid/momentum/meanrev/gold_trend не прошли study (Deflated Sharpe = 0%).
Скрипт закрывает их форвард-историю: P&L / maxDD / сделки / ошибки из журнала фермы и
сверка с предсказанием study — подтвердил ли реальный форвард на песочнице вывод DSR
«устойчивого эджа нет». Контроли buyhold/random показаны для опоры.

    python -m analysis.farm_candidates_final [путь_к_lab.db]

Журнал боевой фермы лежит на VPS (lab/lab.db). Скопировать локально:
    scp <user>@<vps-host>:~/tinvest-lab/lab/lab.db /tmp/lab_vps.db
    python -m analysis.farm_candidates_final /tmp/lab_vps.db
"""
import sys
from datetime import datetime

from lab.journal import conn
from lab.strategies import ACTIVE

# Вердикт study (2026-06-16): у всех кандидатов Deflated Sharpe = 0% — эджа нет.
CANDIDATES = ("grid", "momentum", "meanrev", "gold_trend")


def stats(c, name):
    rows = c.execute("SELECT total FROM equity WHERE strategy=? ORDER BY ts", (name,)).fetchall()
    eq = [r[0] for r in rows]
    if not eq:
        return None
    peak, dd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        dd = min(dd, (v / peak - 1) * 100)
    trades = c.execute("SELECT COUNT(*) FROM trades WHERE strategy=?", (name,)).fetchone()[0]
    errs = c.execute(
        "SELECT COUNT(*) FROM events WHERE strategy=? AND (kind LIKE '%error%' OR kind LIKE '%fail%')",
        (name,)).fetchone()[0]
    return {"pnl": (eq[-1] / eq[0] - 1) * 100, "dd": dd, "trades": trades, "errs": errs, "n": len(eq)}


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else None
    c = conn(db)
    t0 = c.execute("SELECT MIN(ts) FROM equity").fetchone()[0]
    if t0 is None:
        print("журнал пуст — нет данных для снапшота")
        return
    days = (datetime.now().timestamp() - t0) / 86400

    print(f"=== Финальный снапшот фермы ({days:.1f} дн. форварда, БД: {db or 'lab/lab.db'}) ===\n")
    print("Кандидаты (study: Deflated Sharpe = 0%, прогноз — эджа нет):")
    print(f"{'стратегия':<12}{'P&L%':>9}{'maxDD%':>9}{'сделок':>9}{'ошибок':>9}  вердикт форварда")
    print("-" * 78)
    for n in CANDIDATES:
        s = stats(c, n)
        if not s:
            print(f"{n:<12}{'—':>9}{'—':>9}{'—':>9}{'—':>9}  нет данных")
            continue
        # форвард «подтвердил отсутствие эджа», если не заработал устойчиво:
        # либо в минусе/около нуля, либо не торговал (0 сделок — сигнал не сработал).
        verdict = "подтвердил (без эджа)" if s["pnl"] <= 1.0 or s["trades"] == 0 else "ВНИМАНИЕ: +эдж?"
        print(f"{n:<12}{s['pnl']:>+9.2f}{s['dd']:>9.2f}{s['trades']:>9}{s['errs']:>9}  {verdict}")

    print("\nКонтроли (остаются активны как бенчмарки исполнения песочницы):")
    print(f"{'стратегия':<12}{'P&L%':>9}{'maxDD%':>9}{'сделок':>9}{'ошибок':>9}")
    print("-" * 60)
    for n in ACTIVE:
        s = stats(c, n)
        if s:
            print(f"{n:<12}{s['pnl']:>+9.2f}{s['dd']:>9.2f}{s['trades']:>9}{s['errs']:>9}")

    print("\nИтог: кандидаты ушли в архив (код сохранён, тик отключён). Вернуть = "
          "дописать имя в lab/strategies.py ACTIVE и задеплоить.")


if __name__ == "__main__":
    main()

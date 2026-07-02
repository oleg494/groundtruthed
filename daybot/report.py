"""Daybot: отчёт по дням. python -m daybot.report"""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path(__file__).resolve().parent / "daybot.db"
MSK = timezone(timedelta(hours=3))


def day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, MSK).strftime("%Y-%m-%d")


def main():
    if not DB.exists():
        print("daybot.db ещё нет - бот не торговал")
        return
    c = sqlite3.connect(DB)
    eq = c.execute("SELECT ts, total FROM equity ORDER BY ts").fetchall()
    tr = c.execute("SELECT ts, side, price, lots FROM trades ORDER BY ts").fetchall()
    ev = c.execute("SELECT ts, kind, detail FROM events ORDER BY ts DESC LIMIT 8").fetchall()

    days: dict = {}
    for ts, total in eq:
        d = days.setdefault(day_of(ts), {"first": total, "last": total, "trades": 0})
        d["last"] = total
    for ts, *_ in tr:
        if day_of(ts) in days:
            days[day_of(ts)]["trades"] += 1

    print("=== daybot: дни ===")
    for day, d in sorted(days.items()):
        pnl = d["last"] - d["first"]
        print("%s  equity %8.0f -> %8.0f  P&L %+8.0f  сделок %d"
              % (day, d["first"], d["last"], pnl, d["trades"]))
    if eq:
        print("\nИтог: %d дней, equity %.0f (старт 100000, %+.1f%%)"
              % (len(days), eq[-1][1], (eq[-1][1] / 100000 - 1) * 100))

    print("\n=== последние сделки ===")
    for ts, side, price, lots in tr[-12:]:
        print("%s  %-12s %10.4f x%d"
              % (datetime.fromtimestamp(ts, MSK).strftime("%m-%d %H:%M"), side, price, lots))

    print("\n=== последние события ===")
    for ts, kind, detail in ev:
        print("%s  %-12s %s"
              % (datetime.fromtimestamp(ts, MSK).strftime("%m-%d %H:%M"), kind, detail[:80]))


if __name__ == "__main__":
    main()

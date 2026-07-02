"""Локальный разбор операций брокерского счёта из сохранённого JSON (без обращений к API).

Считает: пополнения/выводы, чистый ввод капитала, комиссии, налоги,
реализованный P&L по продажам (поле yield в операциях SELL).
"""
import json
import sys
from collections import defaultdict
from pathlib import Path


def to_f(v: dict) -> float:
    if not v:
        return 0.0
    s = v.get("value")
    return float(s) if s not in (None, "") else 0.0


def load_items(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("items", [])


def analyze(items: list) -> None:
    agg = defaultdict(lambda: {"count": 0, "sum": 0.0})
    realized_pl = 0.0
    sell_gross = buy_gross = 0.0
    for op in items:
        t = op.get("type", "UNKNOWN")
        pay = to_f(op.get("payment"))
        agg[t]["count"] += 1
        agg[t]["sum"] += pay
        if t == "OPERATION_TYPE_SELL":
            realized_pl += to_f(op.get("yield"))
            sell_gross += pay
        elif t == "OPERATION_TYPE_BUY":
            buy_gross += pay

    net_in = agg["OPERATION_TYPE_INPUT"]["sum"]
    net_out = agg["OPERATION_TYPE_OUTPUT"]["sum"]
    fees = agg["OPERATION_TYPE_BROKER_FEE"]["sum"]
    taxes = agg["OPERATION_TYPE_TAX"]["sum"] + agg["OPERATION_TYPE_TAX_CORRECTION"]["sum"]

    print(f"Всего операций: {len(items)}")
    print("-" * 64)
    for t, v in sorted(agg.items(), key=lambda kv: kv[1]["sum"]):
        print(f"  {t:34s} n={v['count']:3d}  {v['sum']:+13.2f} ₽")
    print("-" * 64)
    print(f"  Пополнения (INPUT):        {net_in:+13.2f} ₽")
    print(f"  Выводы (OUTPUT):           {net_out:+13.2f} ₽")
    print(f"  = Чистый ввод капитала:    {net_in + net_out:+13.2f} ₽")
    print(f"  Комиссии брокера:          {fees:+13.2f} ₽")
    print(f"  Налоги (удержано):         {taxes:+13.2f} ₽")
    print(f"  Реализованный P&L (yield): {realized_pl:+13.2f} ₽")
    print(f"  Оборот SELL/BUY:           {sell_gross:+.2f} / {buy_gross:+.2f} ₽")


if __name__ == "__main__":
    paths = [Path(p) for p in sys.argv[1:]]
    all_items, seen = [], set()
    for p in paths:
        for it in load_items(p):
            oid = it.get("id")
            if oid not in seen:
                seen.add(oid)
                all_items.append(it)
    analyze(all_items)

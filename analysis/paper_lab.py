"""Offline paper-portfolio report for analysis/GOAL_lab.md.

No broker/API calls. Feed current paper prices explicitly:

    python -m analysis.paper_lab --price TMON=160.1 --price OFZ26238=570 --out analysis/paper_lab_report.md
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class PaperPosition:
    ticker: str
    lesson: str
    qty: float
    entry_price: float
    target_rub: float


DEFAULT_POSITIONS = [
    PaperPosition("TMON", "денежный рынок / база без риска", 50_000 / 158.71, 158.71, 50_000),
    PaperPosition("OFZ26238", "длинная ОФЗ / чувствительность к ставке", 30_000 / 565.0, 565.0, 30_000),
]
STOCK_BUCKET_RUB = 20_000.0


def snapshot(positions: list[PaperPosition], prices: dict[str, float]) -> dict:
    rows = []
    cost_total, value_total = 0.0, 0.0
    for p in positions:
        cost = p.qty * p.entry_price
        price = prices.get(p.ticker)
        if price is None:
            value = cost
            status = "MISSING_PRICE"
        else:
            value = p.qty * price
            status = "OK"
        pnl = value - cost
        rows.append({"ticker": p.ticker, "lesson": p.lesson, "qty": p.qty,
                     "entry_price": p.entry_price, "price": price, "cost": cost,
                     "value": value, "pnl": pnl,
                     "return_pct": pnl / cost * 100 if cost else 0.0,
                     "status": status})
        cost_total += cost
        value_total += value
    pnl_total = value_total - cost_total
    return {"rows": rows, "cost": round(cost_total, 2), "value": round(value_total, 2),
            "pnl": round(pnl_total, 2),
            "return_pct": round(pnl_total / cost_total * 100, 2) if cost_total else 0.0,
            "stock_bucket_rub": STOCK_BUCKET_RUB}


def render_markdown(snap: dict, title: str = "Paper Lab Report") -> str:
    lines = [
        f"# {title}",
        "",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"Tracked value: {snap['value']:.2f} RUB vs cost {snap['cost']:.2f} RUB; "
        f"P&L {snap['pnl']:+.2f} RUB ({snap['return_pct']:+.2f}%).",
        f"Unselected stock bucket: {snap['stock_bucket_rub']:.0f} RUB.",
        "",
        "| ticker | lesson | qty | entry | price | value | P&L | return | status |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in snap["rows"]:
        price = "" if r["price"] is None else f"{r['price']:.2f}"
        lines.append(f"| {r['ticker']} | {r['lesson']} | {r['qty']:.4f} | "
                     f"{r['entry_price']:.2f} | {price} | {r['value']:.2f} | "
                     f"{r['pnl']:+.2f} | {r['return_pct']:+.2f}% | {r['status']} |")
    lines.extend([
        "",
        "## Вопросы недели",
        "",
        "- TMON вёл себя как низкорисковая база?",
        "- ОФЗ 26238 двигалась из-за ожиданий по ставке, а не из-за кредитного риска?",
        "- Незаполненная доля акций всё ещё ждёт горизонта 3+ года, а не краткосрочного азарта?",
    ])
    return "\n".join(lines) + "\n"


def _parse_prices(items: list[str]) -> dict[str, float]:
    out = {}
    for item in items:
        ticker, price = item.split("=", 1)
        out[ticker.strip()] = float(price)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="offline paper-lab report")
    p.add_argument("--price", action="append", default=[], help="TICKER=PRICE, repeatable")
    p.add_argument("--out", default="analysis/paper_lab_report.md")
    args = p.parse_args(argv)

    md = render_markdown(snapshot(DEFAULT_POSITIONS, _parse_prices(args.price)),
                         title="Учебная лаборатория-портфель")
    Path(args.out).write_text(md, encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

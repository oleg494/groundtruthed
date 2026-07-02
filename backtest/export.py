"""Экспорт результата: equity-кривая и сделки в CSV, полная сводка в JSON.

Чтобы прогон можно было утащить во внешний инструмент (Excel, pandas, дашборд) или
сравнить два запуска побайтово. Зависимостей нет — csv/json из stdlib.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from .engine import Result
from .metrics import metrics
from .analytics import trade_analytics


def _iso(t: int) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def equity_csv(res: Result, path: str) -> str:
    """equity-кривая: date, t, equity, exposure."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "t", "equity", "exposure"])
        for i, t in enumerate(res.times):
            exp = res.exposure[i] if i < len(res.exposure) else ""
            w.writerow([_iso(t), t, f"{res.equity[i]:.4f}", f"{exp:.4f}" if exp != "" else ""])
    return path


def trades_csv(res: Result, path: str) -> str:
    """Список сделок: тикер, сторона, qty, вход/выход, бары, P&L, доходность."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "side", "qty", "entry", "exit",
                    "entry_i", "exit_i", "bars", "pnl", "ret"])
        for t in res.trades:
            w.writerow([t.ticker, t.side, f"{t.qty:g}", f"{t.entry:.6f}",
                        f"{t.exit:.6f}", t.entry_i, t.exit_i, t.exit_i - t.entry_i,
                        f"{t.pnl:.4f}", f"{t.ret:.6f}"])
    return path


def to_json(res: Result, path: str) -> str:
    """Полная сводка: параметры, метрики, аналитика сделок, equity-кривая."""
    m = metrics(res)
    ta = trade_analytics(res)
    payload = {
        "strategy": res.strategy,
        "params": res.params,
        "tickers": res.data_tickers,
        "bars": res.bars,
        "cash0": res.cash0,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metrics": m.as_dict(),
        "trade_analytics": {
            "max_consec_wins": ta.max_consec_wins,
            "max_consec_losses": ta.max_consec_losses,
            "avg_holding_bars": ta.avg_holding_bars,
            "payoff_ratio": ta.payoff_ratio if ta.payoff_ratio != float("inf") else None,
            "long_trades": ta.long_trades,
            "short_trades": ta.short_trades,
        },
        "equity": [{"t": t, "equity": round(res.equity[i], 4)}
                   for i, t in enumerate(res.times)],
    }
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    return path

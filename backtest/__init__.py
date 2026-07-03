"""backtest — самодостаточный движок бэктестинга на чистой stdlib (без numpy/pandas).

Отдельный изолированный пакет: НЕ импортирует и НЕ меняет lab/ daybot/ scripts/.
Назначение — гонять торговые стратегии на исторических (или синтетических) свечах
за секунды, считать честные метрики и проверять идеи до любого выхода в песочницу.

Быстрый старт:

    from backtest import candles, strategies, run, text_report
    data = candles.gbm("TEST", bars=750, seed=1)          # синтетика, без сети
    res = run(strategies.SMACross(fast=20, slow=60), data) # прогон
    print(text_report(res))

CLI:

    python -m backtest demo
    python -m backtest run --strategy sma_cross --synthetic gbm:750:1
"""
from .core import Bar, Broker, Instrument, Order  # noqa: F401
from .engine import Context, Result, Strategy, run  # noqa: F401
from .metrics import Metrics, metrics  # noqa: F401
from .report import html_report, text_report  # noqa: F401

__all__ = [
    "Bar", "Instrument", "Order", "Broker",
    "Strategy", "Context", "run", "Result",
    "metrics", "Metrics",
    "text_report", "html_report",
]

__version__ = "0.1.0"

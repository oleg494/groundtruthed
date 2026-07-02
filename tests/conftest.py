"""Общие хелперы тестов backtest. Делают проект импортируемым и строят бары руками."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.core import Bar  # noqa: E402

DAY = 86400


def bars_from_closes(closes, start_t=0, dt=DAY, spread=0.0):
    """Список Bar из цен закрытия. spread задаёт ширину свечи вокруг o/c."""
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        hi = max(o, c) * (1 + spread)
        lo = min(o, c) * (1 - spread)
        out.append(Bar(t=start_t + i * dt, o=o, h=hi, l=lo, c=c, v=1000))
        prev = c
    return out


def bars_from_ohlc(rows, start_t=0, dt=DAY):
    """rows: список (o,h,l,c) → список Bar."""
    return [Bar(t=start_t + i * dt, o=o, h=h, l=l, c=c, v=1000)
            for i, (o, h, l, c) in enumerate(rows)]

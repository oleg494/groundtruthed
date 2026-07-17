"""Key-rate constants and small offline calendar.

Keep current Central Bank key-rate assumptions in one place. This module has no
network access and is safe to import from scripts or analysis code.
"""
from __future__ import annotations

from datetime import date

# Known effective dates used by project models. Values are annual percentages.
KEYRATE_EVENTS: list[tuple[date, float]] = [
    (date(2026, 2, 13), 15.50),
    (date(2026, 3, 20), 15.00),
    (date(2026, 4, 24), 14.50),
    (date(2026, 6, 19), 14.25),
]

KEYRATE = KEYRATE_EVENTS[-1][1]

# CBR board meeting dates, H2 2026 (cbr.ru/dkp/cal_mp). All are FRIDAYS — the
# previous list had Saturdays (+1 day hallucination); test_keyrate guards this.
CB_MEETINGS_2026 = ["2026-07-24", "2026-09-11", "2026-10-23", "2026-12-18"]


def keyrate_on(day: date) -> float:
    """Return the latest known key rate effective on ``day``."""
    current = KEYRATE_EVENTS[0][1]
    for effective, rate in KEYRATE_EVENTS:
        if day < effective:
            break
        current = rate
    return current

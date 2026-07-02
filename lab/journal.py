"""Strategy Lab: журнал в SQLite. Всё, что произошло, — сюда."""
import sqlite3
import time
from pathlib import Path

DB = Path(__file__).resolve().parent / "lab.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades(
  ts REAL, strategy TEXT, side TEXT, price REAL, lots INTEGER, order_id TEXT);
CREATE TABLE IF NOT EXISTS equity(
  ts REAL, strategy TEXT, total REAL, cash REAL, pos_lots INTEGER);
CREATE TABLE IF NOT EXISTS events(
  ts REAL, strategy TEXT, kind TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS i_eq ON equity(strategy, ts);
CREATE INDEX IF NOT EXISTS i_tr ON trades(strategy, ts);
"""


def conn(db=None) -> sqlite3.Connection:
    c = sqlite3.connect(db or DB)
    c.executescript(_SCHEMA)
    return c


class Journal:
    def __init__(self, db=None):
        self.c = conn(db)

    def trade(self, strategy: str, side: str, price: float, lots: int, order_id: str = ""):
        self.c.execute("INSERT INTO trades VALUES(?,?,?,?,?,?)",
                       (time.time(), strategy, side, price, lots, order_id))
        self.c.commit()

    def equity(self, strategy: str, total: float, cash: float, pos_lots: int):
        self.c.execute("INSERT INTO equity VALUES(?,?,?,?,?)",
                       (time.time(), strategy, total, cash, pos_lots))
        self.c.commit()

    def event(self, strategy: str, kind: str, detail: str = ""):
        self.c.execute("INSERT INTO events VALUES(?,?,?,?)",
                       (time.time(), strategy, kind, detail[:500]))
        self.c.commit()

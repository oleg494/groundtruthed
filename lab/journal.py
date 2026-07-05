"""Strategy Lab: журнал в SQLite. Всё, что произошло, — сюда."""
import sqlite3
import time
from pathlib import Path

DB = Path(__file__).resolve().parent / "lab.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades(
  ts REAL, strategy TEXT, side TEXT, price REAL, lots INTEGER, order_id TEXT,
  fill_price REAL, commission REAL, status TEXT);
CREATE TABLE IF NOT EXISTS equity(
  ts REAL, strategy TEXT, total REAL, cash REAL, pos_lots INTEGER);
CREATE TABLE IF NOT EXISTS events(
  ts REAL, strategy TEXT, kind TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS orders(
  ts REAL, strategy TEXT, order_id TEXT, side TEXT, price REAL, lots INTEGER,
  status TEXT, fill_price REAL, commission REAL, detail TEXT);
CREATE TABLE IF NOT EXISTS expectations(
  strategy TEXT, date TEXT, exp_ret REAL,
  PRIMARY KEY(strategy, date));
CREATE TABLE IF NOT EXISTS reconciles(
  ts REAL, strategy TEXT, n_disc INTEGER, detail TEXT);
CREATE INDEX IF NOT EXISTS i_eq ON equity(strategy, ts);
CREATE INDEX IF NOT EXISTS i_tr ON trades(strategy, ts);
CREATE INDEX IF NOT EXISTS i_ord ON orders(strategy, ts);
CREATE INDEX IF NOT EXISTS i_rec ON reconciles(strategy, ts);
"""


def _ensure_columns(c: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns.items():
        if name not in existing:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def conn(db=None) -> sqlite3.Connection:
    c = sqlite3.connect(db or DB)
    c.executescript(_SCHEMA)
    _ensure_columns(c, "trades", {
        "fill_price": "REAL",
        "commission": "REAL",
        "status": "TEXT",
    })
    return c


class Journal:
    def __init__(self, db=None):
        self.c = conn(db)

    def trade(self, strategy: str, side: str, price: float, lots: int, order_id: str = "",
              fill_price: float | None = None, commission: float | None = None,
              status: str = ""):
        self.c.execute("INSERT INTO trades(ts,strategy,side,price,lots,order_id,"
                       "fill_price,commission,status) VALUES(?,?,?,?,?,?,?,?,?)",
                       (time.time(), strategy, side, price, lots, order_id,
                        fill_price, commission, status))
        self.c.commit()

    def order(self, strategy: str, order_id: str, side: str, price: float, lots: int,
              status: str, fill_price: float | None = None,
              commission: float | None = None, detail: str = ""):
        self.c.execute("INSERT INTO orders(ts,strategy,order_id,side,price,lots,status,"
                       "fill_price,commission,detail) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (time.time(), strategy, order_id, side, price, lots, status,
                        fill_price, commission, detail[:500]))
        self.c.commit()

    def equity(self, strategy: str, total: float, cash: float, pos_lots: int):
        self.c.execute("INSERT INTO equity VALUES(?,?,?,?,?)",
                       (time.time(), strategy, total, cash, pos_lots))
        self.c.commit()

    def event(self, strategy: str, kind: str, detail: str = ""):
        self.c.execute("INSERT INTO events VALUES(?,?,?,?)",
                       (time.time(), strategy, kind, detail[:500]))
        self.c.commit()

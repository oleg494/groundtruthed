"""Юнит-тесты forward-test слоя (lab/forward.py) с замоканным call() и фикстурной БД.

Всё офлайн: sqlite in-memory, sandbox-API замокан. Покрывается:
  - reconcile: все три вида расхождений + чистый случай + пустой lab_state;
  - daily_report: P&L/сделки/ошибки на фикстурной БД, reconcile-статус;
  - алерты: просадка, тишина, серия ошибок (и их отсутствие на здоровых данных).

    python -m unittest test_forward -v
"""
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

from lab import forward
from lab.journal import conn

NOW = time.time()


def make_db():
    """Пустая in-memory БД со схемой журнала."""
    return conn(":memory:")


def add_trade(c, strategy, side, price, lots, ts=None):
    c.execute("INSERT INTO trades(ts,strategy,side,price,lots,order_id) VALUES(?,?,?,?,?,?)",
              (ts or NOW, strategy, side, price, lots, "oid"))


def add_equity(c, strategy, total, ts=None):
    c.execute("INSERT INTO equity VALUES(?,?,?,?,?)",
              (ts or NOW, strategy, total, total, 0))


def add_event(c, strategy, kind, detail="", ts=None):
    c.execute("INSERT INTO events VALUES(?,?,?,?)", (ts or NOW, strategy, kind, detail))


def portfolio(positions):
    """Ответ GetSandboxPortfolio: ticker -> лоты (через реестр INSTRUMENTS)."""
    from lab.instruments import INSTRUMENTS
    return {"positions": [{
        "instrumentUid": INSTRUMENTS[t]["uid"],
        "quantityLots": {"units": str(lots), "nano": 0},
        "quantity": {"units": str(lots * INSTRUMENTS[t]["lot"]), "nano": 0},
    } for t, lots in positions.items()]}


def fake_call(portfolios, orders=None):
    """Мок lab.api.call для reconcile: accountId -> позиции; заявок нет по умолчанию."""
    def _call(method, payload):
        if method.endswith("GetSandboxPortfolio"):
            return portfolios[payload["accountId"]]
        if method.endswith("GetSandboxOrders"):
            return {"orders": (orders or {}).get(payload["accountId"], [])}
        raise AssertionError(f"неожиданный вызов {method}")
    return _call


class TestJournalPositions(unittest.TestCase):
    def test_net_lots_and_format(self):
        """buy/sell сальдируются по тикеру; нулевые нетто выкидываются; старый формат без ':' игнорируется."""
        c = make_db()
        add_trade(c, "grid", "buy:SBER", 300, 2)
        add_trade(c, "grid", "sell:SBER", 310, 1)
        add_trade(c, "grid", "buy:GAZP", 120, 1)
        add_trade(c, "grid", "sell:GAZP", 125, 1)   # нетто 0 — не показываем
        add_trade(c, "grid", "buy", 100, 5)          # legacy-формат — пропуск, не гадаем
        self.assertEqual(forward.journal_positions(c, "grid"), {"SBER": 1})

    def test_short_negative(self):
        c = make_db()
        add_trade(c, "day", "sell:BMQ6", 70, 2)
        self.assertEqual(forward.journal_positions(c, "day"), {"BMQ6": -2})


class TestJournalSchemaV2(unittest.TestCase):
    def test_trade_optional_execution_fields_and_forward_tables(self):
        from lab.journal import Journal

        c = make_db()
        trade_cols = {r[1] for r in c.execute("PRAGMA table_info(trades)")}
        self.assertTrue({"fill_price", "commission", "status"} <= trade_cols)
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"orders", "reconciles", "expectations"} <= tables)

        j = Journal(":memory:")
        j.trade("grid", "buy:SBER", 300.0, 1, order_id="oid",
                fill_price=300.1, commission=0.18, status="filled")
        row = j.c.execute("SELECT price,fill_price,commission,status FROM trades").fetchone()
        self.assertEqual(row, (300.0, 300.1, 0.18, "filled"))

    def test_order_helper_records_lifecycle_rows(self):
        from lab.journal import Journal

        j = Journal(":memory:")
        j.order("grid", "oid1", "buy:SBER", 300.0, 1, "submitted")
        j.order("grid", "oid1", "buy:SBER", 300.0, 1, "filled",
                fill_price=300.2, commission=0.18, detail="lotsExecuted=1")

        rows = j.c.execute("SELECT order_id,status,fill_price,commission,detail "
                           "FROM orders ORDER BY ts").fetchall()
        self.assertEqual(rows[0], ("oid1", "submitted", None, None, ""))
        self.assertEqual(rows[1], ("oid1", "filled", 300.2, 0.18, "lotsExecuted=1"))


class TestSlippage(unittest.TestCase):
    def test_uses_fill_price_when_available(self):
        c = make_db()
        c.execute("INSERT INTO trades(ts,strategy,side,price,lots,order_id,fill_price,commission,status) "
                  "VALUES(?,?,?,?,?,?,?,?,?)",
                  (NOW, "grid", "buy:SBER", 300.0, 1, "b1", 300.3, 0.18, "filled"))
        c.execute("INSERT INTO trades(ts,strategy,side,price,lots,order_id,fill_price,commission,status) "
                  "VALUES(?,?,?,?,?,?,?,?,?)",
                  (NOW, "grid", "sell:SBER", 301.0, 1, "s1", 300.8, 0.18, "filled"))

        sl = forward.slippage(c, "grid")

        self.assertTrue(sl["available"])
        self.assertEqual(sl["trades"], 2)
        self.assertAlmostEqual(sl["avg_adverse_price"], 0.25)
        self.assertAlmostEqual(sl["avg_adverse_bps"], 8.32, places=2)


class TestTrackingVsBacktest(unittest.TestCase):
    def test_reads_expectations_table_when_no_manual_expected_return(self):
        c = make_db()
        d0 = datetime.fromtimestamp(NOW).replace(hour=12, minute=0, second=0, microsecond=0)
        d1 = d0 + timedelta(days=1)
        add_equity(c, "grid", 100_000, ts=d0.timestamp())
        add_equity(c, "grid", 103_000, ts=d1.timestamp())
        c.execute("INSERT INTO expectations(strategy,date,exp_ret) VALUES(?,?,?)",
                  ("grid", d1.date().isoformat(), 0.01))

        tr = forward.tracking_vs_backtest(c, "grid")

        self.assertTrue(tr["available"])
        self.assertAlmostEqual(tr["live_pct"], 3.0)
        self.assertAlmostEqual(tr["expected_pct"], 1.0)
        self.assertAlmostEqual(tr["gap_pp"], 2.0)


class TestReconcileHistory(unittest.TestCase):
    def test_record_reconcile_result_and_show_latest_history(self):
        c = make_db()
        add_equity(c, "grid", 100_000, ts=NOW - 60)
        rec = {"ok": False, "strategies": {"grid": {
            "discrepancies": [{"strategy": "grid", "ticker": "SBER",
                               "journal_lots": 2, "account_lots": 0,
                               "issue": "в журнале есть, на счёте нет"}],
            "active_orders": 0, "journal": {"SBER": 2}, "account": {}}}}

        forward.record_reconcile(c, rec, ts=NOW)

        row = c.execute("SELECT strategy,n_disc FROM reconciles").fetchone()
        self.assertEqual(row, ("grid", 1))
        txt = forward.daily_report(db=c, day=time.strftime("%Y-%m-%d", time.localtime(NOW)))
        self.assertIn("история reconcile", txt)
        self.assertIn("[grid] 1 расхождений", txt)


class TestReconcile(unittest.TestCase):
    def _run(self, c, accounts, portfolios, orders=None):
        with mock.patch("lab.api.call", side_effect=fake_call(portfolios, orders)):
            return forward.reconcile(accounts=accounts, db=c)

    def test_journal_has_account_empty(self):
        """Позиция в журнале есть, на счёте нет — расхождение."""
        c = make_db()
        add_trade(c, "grid", "buy:SBER", 300, 2)
        rec = self._run(c, {"grid": "acc1"}, {"acc1": portfolio({})})
        self.assertFalse(rec["ok"])
        d = rec["strategies"]["grid"]["discrepancies"]
        self.assertEqual(len(d), 1)
        self.assertEqual((d[0]["ticker"], d[0]["journal_lots"], d[0]["account_lots"]),
                         ("SBER", 2, 0))
        self.assertIn("на счёте нет", d[0]["issue"])

    def test_account_has_journal_empty(self):
        """На счёте позиция, в журнале нет — расхождение в обратную сторону."""
        c = make_db()
        rec = self._run(c, {"grid": "acc1"}, {"acc1": portfolio({"GAZP": 1})})
        self.assertFalse(rec["ok"])
        d = rec["strategies"]["grid"]["discrepancies"][0]
        self.assertEqual((d["ticker"], d["journal_lots"], d["account_lots"]), ("GAZP", 0, 1))
        self.assertIn("в журнале нет", d["issue"])

    def test_size_mismatch(self):
        c = make_db()
        add_trade(c, "grid", "buy:SBER", 300, 3)
        rec = self._run(c, {"grid": "acc1"}, {"acc1": portfolio({"SBER": 1})})
        d = rec["strategies"]["grid"]["discrepancies"][0]
        self.assertEqual((d["journal_lots"], d["account_lots"]), (3, 1))
        self.assertIn("размер не сходится", d["issue"])

    def test_clean_match(self):
        """Журнал сходится со счётом — ok, активные заявки посчитаны."""
        c = make_db()
        add_trade(c, "grid", "buy:SBER", 300, 2)
        rec = self._run(c, {"grid": "acc1"}, {"acc1": portfolio({"SBER": 2})},
                        orders={"acc1": [{"orderId": "o1"}]})
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["strategies"]["grid"]["discrepancies"], [])
        self.assertEqual(rec["strategies"]["grid"]["active_orders"], 1)
        # текстовый отчёт не падает и содержит вердикт
        self.assertIn("OK", forward.format_reconcile(rec))

    def test_no_accounts(self):
        """Пустой lab_state — вежливая ошибка, не трейсбек."""
        rec = forward.reconcile(accounts={}, db=make_db())
        self.assertFalse(rec["ok"])
        self.assertIn("lab_state.json", rec["error"])
        self.assertIn("ОШИБКА", forward.format_reconcile(rec))


class TestAlerts(unittest.TestCase):
    def test_drawdown_alert(self):
        """Просадка от пика глубже лимита — алерт."""
        c = make_db()
        for i, total in enumerate([100000, 105000, 98000]):  # dd = 98/105-1 = -6.7%
            add_equity(c, "x", total, ts=NOW - 300 + i * 60)
        alerts = forward.check_alerts(c, "x", dd_limit=5.0)
        self.assertTrue(any("ПРОСАДКА" in a for a in alerts), alerts)

    def test_silence_alert(self):
        """Сделок нет, тиков больше порога — алерт тишины."""
        c = make_db()
        for i in range(12):
            add_equity(c, "x", 100000, ts=NOW - 1200 + i * 60)
        alerts = forward.check_alerts(c, "x", silence_ticks=10)
        self.assertTrue(any("ТИШИНА" in a for a in alerts), alerts)

    def test_silence_after_last_trade(self):
        """Сделки были, но давно (тиков > порога) — тоже тишина."""
        c = make_db()
        add_trade(c, "x", "buy:SBER", 300, 1, ts=NOW - 2000)
        for i in range(12):
            add_equity(c, "x", 100000, ts=NOW - 1200 + i * 60)
        alerts = forward.check_alerts(c, "x", silence_ticks=10)
        self.assertTrue(any("ТИШИНА" in a for a in alerts), alerts)

    def test_error_streak_alert(self):
        """5 error/fail подряд в хвосте событий — алерт; разорванная серия — нет."""
        c = make_db()
        add_equity(c, "x", 100000, ts=NOW - 600)
        add_equity(c, "x", 100000, ts=NOW - 540)
        for i in range(5):
            add_event(c, "x", "tick_error", "boom", ts=NOW - 300 + i * 60)
        self.assertTrue(any("СЕРИЯ ОШИБОК" in a
                            for a in forward.check_alerts(c, "x", err_streak=5)))
        # свежее НЕошибочное событие разрывает серию
        add_event(c, "x", "account_open", "acc", ts=NOW)
        self.assertFalse(any("СЕРИЯ ОШИБОК" in a
                             for a in forward.check_alerts(c, "x", err_streak=5)))

    def test_healthy_no_alerts(self):
        """Здоровые данные: ровный equity, свежая сделка, нет ошибок — алертов нет."""
        c = make_db()
        add_equity(c, "x", 100000, ts=NOW - 120)
        add_equity(c, "x", 100500, ts=NOW - 60)
        add_trade(c, "x", "buy:SBER", 300, 1, ts=NOW - 90)
        self.assertEqual(forward.check_alerts(c, "x"), [])


class TestDailyReport(unittest.TestCase):
    def test_report_on_fixture_db(self):
        """Отчёт на фикстурной БД: P&L дня, сделки, ошибки, алерт просадки, офлайн-reconcile."""
        c = make_db()
        day = time.strftime("%Y-%m-%d", time.localtime(NOW))
        add_equity(c, "grid", 100000, ts=NOW - 3600)
        add_equity(c, "grid", 92000, ts=NOW - 60)     # -8% за день -> алерт просадки
        add_trade(c, "grid", "buy:SBER", 300, 1, ts=NOW - 1800)
        add_event(c, "grid", "tick_error", "HTTP 400", ts=NOW - 900)
        txt = forward.daily_report(db=c, day=day)
        self.assertIn("[grid]", txt)
        self.assertIn("-8000", txt)                     # P&L дня в рублях
        self.assertIn("сделок за день: 1", txt)
        self.assertIn("ошибок за день: 1", txt)
        self.assertIn("АЛЕРТ", txt)
        self.assertIn("ПРОСАДКА", txt)
        self.assertIn("слиппедж: н/д", txt)             # честная заглушка видна в отчёте
        self.assertIn("reconcile: не выполнялся", txt)  # офлайн-режим

    def test_report_with_reconcile_result(self):
        """Готовый результат reconcile встраивается в отчёт со счётчиком расхождений."""
        c = make_db()
        day = time.strftime("%Y-%m-%d", time.localtime(NOW))
        add_equity(c, "grid", 100000, ts=NOW - 120)
        add_equity(c, "grid", 100100, ts=NOW - 60)
        rec = {"ok": False, "strategies": {"grid": {
            "discrepancies": [{"strategy": "grid", "ticker": "SBER",
                               "journal_lots": 2, "account_lots": 0,
                               "issue": "в журнале есть, на счёте нет"}],
            "active_orders": 0, "journal": {"SBER": 2}, "account": {}}}}
        txt = forward.daily_report(db=c, day=day, reconcile_result=rec)
        self.assertIn("1 расхождений", txt)

    def test_empty_journal(self):
        txt = forward.daily_report(db=make_db())
        self.assertIn("журнал пуст", txt)

    def test_report_shows_futures_roll_warnings(self):
        c = make_db()
        day = time.strftime("%Y-%m-%d", time.localtime(NOW))
        add_equity(c, "grid", 100000, ts=NOW - 60)
        with mock.patch("lab.forward.futures_roll_warnings", return_value=[
            {"ticker": "NGN6", "status": "ROLL_SOON", "days_to_last_trade": 2,
             "roll_to": "NGQ6", "last_trade": "2026-07-29", "exp": "2026-07-30"}
        ]):
            txt = forward.daily_report(db=c, day=day)
        self.assertIn("rollover", txt)
        self.assertIn("NGN6", txt)
        self.assertIn("NGQ6", txt)

    def test_report_shows_tracking_when_expectations_exist(self):
        c = make_db()
        d0 = datetime.fromtimestamp(NOW).replace(hour=12, minute=0, second=0, microsecond=0)
        d1 = d0 + timedelta(days=1)
        add_equity(c, "grid", 100_000, ts=d0.timestamp())
        add_equity(c, "grid", 102_000, ts=d1.timestamp())
        c.execute("INSERT INTO expectations(strategy,date,exp_ret) VALUES(?,?,?)",
                  ("grid", d1.date().isoformat(), 0.01))

        txt = forward.daily_report(db=c, day=d1.date().isoformat())

        self.assertIn("tracking", txt)
        self.assertIn("gap +1.00 п.п.", txt)

    def test_report_shows_order_fill_rate_from_latest_status(self):
        c = make_db()
        day = time.strftime("%Y-%m-%d", time.localtime(NOW))
        add_equity(c, "grid", 100000, ts=NOW - 60)
        c.execute("INSERT INTO orders(ts,strategy,order_id,side,price,lots,status,fill_price,commission,detail) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (NOW - 30, "grid", "o1", "buy:SBER", 300, 1, "submitted", None, None, ""))
        c.execute("INSERT INTO orders(ts,strategy,order_id,side,price,lots,status,fill_price,commission,detail) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (NOW - 20, "grid", "o1", "buy:SBER", 300, 1, "filled", 300.1, 0.18, ""))
        c.execute("INSERT INTO orders(ts,strategy,order_id,side,price,lots,status,fill_price,commission,detail) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (NOW - 10, "grid", "o2", "buy:SBER", 300, 1, "canceled", None, None, ""))

        txt = forward.daily_report(db=c, day=day)

        self.assertIn("fill-rate: 1/2", txt)
        self.assertIn("filled=1", txt)
        self.assertIn("canceled=1", txt)

    def test_report_shows_partial_rate_from_latest_status(self):
        c = make_db()
        day = time.strftime("%Y-%m-%d", time.localtime(NOW))
        add_equity(c, "grid", 100000, ts=NOW - 60)
        c.execute("INSERT INTO orders(ts,strategy,order_id,side,price,lots,status,fill_price,commission,detail) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (NOW - 30, "grid", "o1", "buy:SBER", 300, 2, "partial", 300.1, 0.18,
                   "lotsExecuted=1"))
        c.execute("INSERT INTO orders(ts,strategy,order_id,side,price,lots,status,fill_price,commission,detail) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (NOW - 20, "grid", "o2", "buy:SBER", 300, 1, "filled", 300.2, 0.18, ""))

        txt = forward.daily_report(db=c, day=day)

        self.assertIn("partial-rate: 1/2", txt)
        self.assertIn("partial=1", txt)


if __name__ == "__main__":
    unittest.main()

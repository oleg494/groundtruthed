"""Юнит-тесты живого торгового слоя (lab/ + daybot/) с замоканным call().

Бэктестер покрыт своими тестами; здесь — самые хрупкие места песочницы, которые
раньше проверялись только вживую и уже не раз стреляли:
  - Grid.on_tick: state-машина уровней (регресс на заморозку error→pending, 2026-06-16);
  - Ctx.equity(): коррекция нотионала фьючерса (баг симулятора T-Invest);
  - daybot.market_exit: обход бага 30034 (регресс на ctx.account_id).

    python -m unittest test_trading -v
"""
import unittest
from unittest import mock

import daybot.run as dr
from lab.strategies import Grid
from lab.strategy import Ctx


class StubJournal:
    def __init__(self):
        self.trades = []

    def trade(self, *a):
        self.trades.append(a)

    def event(self, *a, **k):
        pass


class StubCtx:
    """Лёгкая замена Ctx для Grid: записывает лимитки, отдаёт каноничные позиции/ордера."""
    def __init__(self, positions=None, active=None, filled=None, limit_raises=False):
        self.name = "grid"
        self.state = {}
        self.j = StubJournal()
        self._positions = positions or {}
        self._active = active or {}
        self._filled = filled or set()
        self.limit_raises = limit_raises
        self.placed = []
        self._oid = 0

    def active_orders(self):
        return self._active

    def order_filled(self, oid):
        return oid in self._filled

    def limit(self, ticker, side, lots, price):
        if self.limit_raises:
            raise RuntimeError("HTTP 400 PostSandboxOrder: симуляция")
        self._oid += 1
        oid = f"oid{self._oid}"
        self.placed.append({"ticker": ticker, "side": side, "lots": lots, "price": price, "oid": oid})
        return oid

    def equity(self):
        return {"positions": dict(self._positions)}


class TestGrid(unittest.TestCase):
    def test_unfreezes_error_levels(self):
        """Регресс 2026-06-16: уровень в status=error должен размораживаться и выставляться.
        До фикла error был тупиком — грид замерзал навсегда."""
        ctx = StubCtx(positions={"SBER": 0})
        ctx.state["levels"] = [
            {"side": "buy", "price": 300.0, "order_id": None, "status": "error"}]
        Grid().on_tick(ctx)
        lv = ctx.state["levels"][0]
        self.assertEqual(lv["status"], "active")
        self.assertEqual(len(ctx.placed), 1)
        self.assertEqual(ctx.placed[0]["side"], "buy")

    def test_sell_gated_by_position(self):
        """Sell-уровень выставляется только под имеющуюся позицию (budget = pos - sells)."""
        # позиции нет -> pending sell не уходит
        ctx = StubCtx(positions={"SBER": 0})
        ctx.state["levels"] = [
            {"side": "sell", "price": 320.0, "order_id": None, "status": "pending"}]
        Grid().on_tick(ctx)
        self.assertEqual(ctx.state["levels"][0]["status"], "pending")
        self.assertEqual(ctx.placed, [])
        # появилась позиция -> тот же sell уходит
        ctx = StubCtx(positions={"SBER": 1})
        ctx.state["levels"] = [
            {"side": "sell", "price": 320.0, "order_id": None, "status": "pending"}]
        Grid().on_tick(ctx)
        self.assertEqual(ctx.state["levels"][0]["status"], "active")
        self.assertEqual(len(ctx.placed), 1)

    def test_fill_advances_grid(self):
        """Исполненный уровень -> filled + журналируется + добавляется встречный уровень."""
        ctx = StubCtx(positions={"SBER": 1}, active={}, filled={"oidX"})
        ctx.state["levels"] = [
            {"side": "buy", "price": 300.0, "order_id": "oidX", "status": "active"}]
        Grid().on_tick(ctx)
        self.assertEqual(ctx.state["levels"][0]["status"], "filled")
        self.assertEqual(len(ctx.j.trades), 1)
        # добавился встречный sell-уровень; с позицией на руках он уходит сразу в этом же тике
        self.assertEqual(len(ctx.state["levels"]), 2)
        added = [l for l in ctx.state["levels"] if l["side"] == "sell"]
        self.assertEqual(len(added), 1)
        self.assertAlmostEqual(added[0]["price"], 300.0 * (1 + Grid.STEP), places=6)
        self.assertEqual([p["side"] for p in ctx.placed], ["sell"])

    def test_failed_limit_marks_error(self):
        """Если биржа отбила лимитку — уровень помечается error (и оживёт на след. тике)."""
        ctx = StubCtx(positions={"SBER": 0}, limit_raises=True)
        ctx.state["levels"] = [
            {"side": "buy", "price": 300.0, "order_id": None, "status": "pending"}]
        Grid().on_tick(ctx)
        self.assertEqual(ctx.state["levels"][0]["status"], "error")
        self.assertEqual(ctx.placed, [])


def _portfolio(positions, cash, total, futures):
    return {
        "positions": positions,
        "totalAmountCurrencies": {"units": str(cash), "nano": 0},
        "totalAmountPortfolio": {"units": str(total), "nano": 0},
        "totalAmountFutures": {"units": str(futures), "nano": 0},
    }


class TestEquity(unittest.TestCase):
    def test_futures_notional_corrected(self):
        """Песочница кладёт в total нотионал фьюча; equity() меняет его на P&L от средней.
        BMQ6 point_rub=71.908, поза 2 лота, цена 69->70 -> P&L = (70-69)*71.908*2."""
        bmq6 = "d46436d0-f6c4-43b3-90fc-f93e6330ff1f"
        pf = _portfolio(
            positions=[{
                "instrumentUid": bmq6,
                "quantityLots": {"units": "2", "nano": 0},
                "quantity": {"units": "2", "nano": 0},
                "currentPrice": {"units": "70", "nano": 0},
                "averagePositionPrice": {"units": "69", "nano": 0},
            }],
            cash=100000, total=200000, futures=10000)
        with mock.patch("lab.strategy.call", return_value=pf):
            eq = Ctx("acc", StubJournal(), "x").equity()
        # 200000 - 10000 (нотионал) + 143.816 (P&L) = 190143.816
        self.assertAlmostEqual(eq["total"], 200000 - 10000 + (70 - 69) * 71.908 * 2, places=3)
        self.assertEqual(eq["cash"], 100000)
        self.assertEqual(eq["positions"], {"BMQ6": 2})

    def test_share_no_correction(self):
        """Для акций нотионал не вычитается, лоты считаются из quantity/lot."""
        sber = "e6123145-9665-43e0-8413-cd61b8aa9b13"  # lot=10
        pf = _portfolio(
            positions=[{
                "instrumentUid": sber,
                "quantityLots": {"units": "0", "nano": 0},  # пусто -> считаем из quantity
                "quantity": {"units": "30", "nano": 0},
                "currentPrice": {"units": "300", "nano": 0},
                "averagePositionPrice": {"units": "300", "nano": 0},
            }],
            cash=50000, total=59000, futures=0)
        with mock.patch("lab.strategy.call", return_value=pf):
            eq = Ctx("acc", StubJournal(), "x").equity()
        self.assertEqual(eq["total"], 59000)        # без коррекции
        self.assertEqual(eq["positions"], {"SBER": 3})  # 30 / lot 10


class ExitCtx:
    """Стаб Ctx для daybot.market_exit: первый market падает с 30034, второй проходит."""
    def __init__(self):
        self.account_id = "acc123"
        self.state = {}
        self.j = StubJournal()
        self.market_calls = []
        self._raised = False

    def market(self, t, side, lots):
        self.market_calls.append((t, side, lots))
        if not self._raised:
            self._raised = True
            raise RuntimeError("HTTP 400: code 30034 Not enough balance")


class TestMarketExit(unittest.TestCase):
    def test_30034_bypass_topups_and_retries(self):
        """Обход 30034: долить кэш на СВОЙ счёт (ctx.account_id!) и повторить market."""
        ctx = ExitCtx()
        calls = []
        with mock.patch.object(dr, "call", lambda m, p: calls.append((m, p)) or {}), \
             mock.patch.object(dr, "log", lambda *a, **k: None):
            dr.market_exit(ctx, "BMQ6", "sell", 1)
        self.assertEqual(ctx.market_calls, [("BMQ6", "sell", 1), ("BMQ6", "sell", 1)])
        payins = [p for m, p in calls if m == "SandboxService/SandboxPayIn"]
        self.assertEqual(len(payins), 1)
        # регресс на фикс ctx.acc -> ctx.account_id: при возврате acc упадёт AttributeError
        self.assertEqual(payins[0]["accountId"], "acc123")
        self.assertEqual(ctx.state["extra_cash"], dr.VIRTUAL_CASH)

    def test_other_error_reraised(self):
        """Не-30034 ошибка пробрасывается без доливки."""
        ctx = ExitCtx()
        ctx._raised = False

        def boom(t, side, lots):
            ctx.market_calls.append((t, side, lots))
            raise RuntimeError("HTTP 500: чужая ошибка")
        ctx.market = boom
        calls = []
        with mock.patch.object(dr, "call", lambda m, p: calls.append((m, p)) or {}), \
             mock.patch.object(dr, "log", lambda *a, **k: None):
            with self.assertRaises(RuntimeError):
                dr.market_exit(ctx, "BMQ6", "sell", 1)
        self.assertEqual(calls, [])  # доливки не было
        self.assertNotIn("extra_cash", ctx.state)


if __name__ == "__main__":
    unittest.main()

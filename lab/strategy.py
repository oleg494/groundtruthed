"""Strategy Lab: базовый класс стратегии и торговый контекст (один счёт = одна стратегия).

v3: мульти-инструмент, лонг и шорт. Цены в ctx.prices (обновляет runner).
Фьючерсы: песочница не двигает кэш при сделке (лишь комиссия), а в total кладёт
полный нотионал позиции — честный equity = api_total − нотионал + P&L от средней
(averagePositionPrice/currentPrice портфеля приходят в ПУНКТАХ).
"""
import time
import uuid
from .api import call, to_f, quot
from .instruments import INSTRUMENTS, rub_value


class Ctx:
    def __init__(self, account_id: str, journal, name: str):
        self.account_id = account_id
        self.j = journal
        self.name = name
        self.prices: dict = {}    # ticker -> цена (пункты для фьючей)
        self.candles_cache: dict = {}
        self.state: dict = {}     # персистентное (runner сохраняет)

    # ── чтение ──
    def positions(self) -> dict:
        """ticker -> лоты (шорт < 0). Кэш/тоталы стэшируются для equity()."""
        p = call("SandboxService/GetSandboxPortfolio", {"accountId": self.account_id})
        by_uid = {m["uid"]: t for t, m in INSTRUMENTS.items()}
        out, fut_pnl = {}, 0.0
        for pos in p.get("positions", []):
            t = by_uid.get(pos.get("instrumentUid"))
            if not t:
                continue
            lots = to_f(pos.get("quantityLots"))
            if not lots:
                lots = to_f(pos.get("quantity")) / INSTRUMENTS[t]["lot"]
            out[t] = int(round(lots))
            if INSTRUMENTS[t]["kind"] == "futures":  # цены позиции в пунктах
                fut_pnl += ((to_f(pos.get("currentPrice")) - to_f(pos.get("averagePositionPrice")))
                            * INSTRUMENTS[t]["point_rub"] * to_f(pos.get("quantity")))
        self._cash = to_f(p.get("totalAmountCurrencies"))
        self._api_total = to_f(p.get("totalAmountPortfolio"))
        self._fut_amount = to_f(p.get("totalAmountFutures"))
        self._fut_pnl = fut_pnl
        return out

    def equity(self) -> dict:
        """Честный equity: песочница в total кладёт нотионал фьючей вместо их P&L."""
        pos = self.positions()
        total = self._api_total - self._fut_amount + self._fut_pnl
        return {"total": total, "cash": self._cash,
                "pos_lots": sum(pos.values()), "positions": pos}

    def daily_closes(self, ticker: str, days: int = 90) -> list:
        """Дневные close (кэш на 4 часа — для SMA чаще не нужно)."""
        key = (ticker, days)
        hit = self.candles_cache.get(key)
        if hit and time.time() - hit[0] < 4 * 3600:
            return hit[1]
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        r = call("MarketDataService/GetCandles", {
            "instrumentId": INSTRUMENTS[ticker]["uid"], "interval": "CANDLE_INTERVAL_DAY",
            "from": (now - timedelta(days=int(days * 1.6))).strftime("%Y-%m-%dT00:00:00Z"),
            "to": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
        closes = [to_f(c["close"]) for c in r.get("candles", []) if c.get("isComplete", True)]
        self.candles_cache[key] = (time.time(), closes)
        return closes

    def active_orders(self) -> dict:
        r = call("SandboxService/GetSandboxOrders", {"accountId": self.account_id})
        return {o["orderId"]: o for o in r.get("orders", [])}

    def order_filled(self, order_id: str) -> bool:
        """Отмена != исполнение (урок sandbox_grid)."""
        try:
            r = call("SandboxService/GetSandboxOrderState",
                     {"accountId": self.account_id, "orderId": order_id})
            return int(r.get("lotsExecuted", 0)) > 0
        except RuntimeError:
            return False

    # ── действия ──
    def _post(self, ticker: str, side: str, lots: int, price: float | None) -> str:
        m = INSTRUMENTS[ticker]
        body = {"accountId": self.account_id, "instrumentId": m["uid"],
                "quantity": str(lots), "orderId": str(uuid.uuid4()),
                "direction": "ORDER_DIRECTION_BUY" if side == "buy" else "ORDER_DIRECTION_SELL",
                "orderType": "ORDER_TYPE_LIMIT" if price else "ORDER_TYPE_MARKET"}
        if price:
            body["price"] = quot(price, m["step"])
        r = call("SandboxService/PostSandboxOrder", body)
        return r["orderId"]

    def limit(self, ticker: str, side: str, lots: int, price: float) -> str:
        return self._post(ticker, side, lots, price)

    def market(self, ticker: str, side: str, lots: int) -> str:
        oid = self._post(ticker, side, lots, None)
        px = self.prices.get(ticker, 0.0)
        self.j.trade(self.name, f"{side}:{ticker}", px, lots, oid)
        return oid

    def cancel(self, order_id: str) -> None:
        try:
            call("SandboxService/CancelSandboxOrder",
                 {"accountId": self.account_id, "orderId": order_id})
        except RuntimeError as e:
            self.j.event(self.name, "cancel_fail", str(e))


def sma(xs: list, n: int) -> float:
    return sum(xs[-n:]) / n if len(xs) >= n else 0.0


class Strategy:
    name = "base"
    instruments: list = []  # какие тикеры нужны (runner подтянет цены)

    def on_start(self, ctx: Ctx) -> None:  # noqa: U100
        pass

    def on_tick(self, ctx: Ctx) -> None:
        raise NotImplementedError

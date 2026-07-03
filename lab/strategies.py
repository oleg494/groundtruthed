"""Strategy Lab: стратегии v2.

Контроль: buyhold (корзина) и random — любой «сигнал» обязан бить обе.
Сигнальные: grid (SBER), momentum и meanrev (корзина нефть/газ/металлы),
gold_trend (фьюч GLDRUBF, лонг-онли).
"""
import random
import time

from .instruments import BASKET, rub_value
from .strategy import Ctx, Strategy, sma


def hourly(ctx: Ctx) -> bool:
    """Сигнальные стратегии действуют не чаще раза в час."""
    if time.time() - ctx.state.get("last_act", 0) < 3600:
        return False
    ctx.state["last_act"] = time.time()
    return True


class BuyHold(Strategy):
    """Контроль №1: равные веса корзины, купить и держать. Бенчмарк."""
    name = "buyhold"
    instruments = BASKET

    def on_start(self, ctx: Ctx) -> None:
        if ctx.state.get("bought"):
            return
        eq = ctx.equity()
        per = eq["cash"] * 0.95 / len(BASKET)
        for t in BASKET:
            px = ctx.prices.get(t, 0.0)
            if not px:
                continue
            lots = int(per / rub_value(t, px, 1))
            if lots > 0:
                ctx.market(t, "buy", lots)
        ctx.state["bought"] = True

    def on_tick(self, ctx: Ctx) -> None:
        pass


class RandomTrader(Strategy):
    """Контроль №2: монетка по корзине."""
    name = "random"
    instruments = BASKET

    def on_tick(self, ctx: Ctx) -> None:
        if random.random() > 0.02:
            return
        t = random.choice(BASKET)
        px = ctx.prices.get(t, 0.0)
        if not px:
            return
        eq = ctx.equity()
        if random.random() < 0.5:
            if eq["cash"] > rub_value(t, px, 1):
                ctx.market(t, "buy", 1)
        elif eq["positions"].get(t, 0) > 0:
            ctx.market(t, "sell", 1)


class Grid(Strategy):
    """Грид на SBER (порт sandbox_grid: отмена != исполнение, sell под позицию)."""
    name = "grid"
    instruments = ["SBER"]
    STEP, LEVELS = 0.005, 5

    def _add(self, ctx: Ctx, side: str, price: float) -> None:
        ctx.state["levels"].append(
            {"side": side, "price": round(price, 2), "order_id": None, "status": "pending"})

    def on_start(self, ctx: Ctx) -> None:
        if ctx.state.get("levels"):
            return
        px = ctx.prices["SBER"]
        ctx.state["levels"] = []
        for i in range(1, self.LEVELS + 1):
            self._add(ctx, "buy", px * (1 - self.STEP * i))
            self._add(ctx, "sell", px * (1 + self.STEP * i))

    def on_tick(self, ctx: Ctx) -> None:
        act = ctx.active_orders()
        for lv in ctx.state["levels"]:
            if lv["status"] == "error":  # 400 вне сессии больше не валит — размораживаем залипшие уровни
                lv["status"], lv["order_id"] = "pending", None
            if lv["status"] != "active" or lv["order_id"] in act:
                continue
            if not ctx.order_filled(lv["order_id"]):
                lv["order_id"], lv["status"] = None, "pending"
                continue
            lv["status"] = "filled"
            ctx.j.trade(ctx.name, f"{lv['side']}:SBER", lv["price"], 1, lv["order_id"])
            nxt = lv["price"] * (1 + self.STEP if lv["side"] == "buy" else 1 - self.STEP)
            self._add(ctx, "sell" if lv["side"] == "buy" else "buy", nxt)
        if any(l["status"] == "pending" for l in ctx.state["levels"]):
            pos = ctx.equity()["positions"].get("SBER", 0)
            sells = sum(1 for l in ctx.state["levels"]
                        if l["side"] == "sell" and l["status"] == "active")
            budget = pos - sells
            for lv in ctx.state["levels"]:
                if lv["status"] != "pending":
                    continue
                if lv["side"] == "sell":
                    if budget <= 0:
                        continue
                    budget -= 1
                try:
                    lv["order_id"] = ctx.limit("SBER", lv["side"], 1, lv["price"])
                    lv["status"] = "active"
                except RuntimeError as e:
                    lv["status"] = "error"
                    ctx.j.event(ctx.name, "order_fail", str(e))


class Momentum(Strategy):
    """Тренд: длинная SMA20>SMA60 — в позиции, иначе в кэше. Раз в час, по корзине."""
    name = "momentum"
    instruments = BASKET
    FAST, SLOW = 20, 60

    def on_tick(self, ctx: Ctx) -> None:
        if not hourly(ctx):
            return
        eq = ctx.equity()
        per = eq["total"] * 0.95 / len(BASKET)
        for t in BASKET:
            closes = ctx.daily_closes(t)
            px = ctx.prices.get(t, 0.0)
            if len(closes) < self.SLOW or not px:
                continue
            want = sma(closes, self.FAST) > sma(closes, self.SLOW)
            have = eq["positions"].get(t, 0)
            if want and have == 0:
                lots = int(per / rub_value(t, px, 1))
                if lots > 0 and eq["cash"] > rub_value(t, px, lots):
                    ctx.market(t, "buy", lots)
            elif not want and have > 0:
                ctx.market(t, "sell", have)


class MeanRev(Strategy):
    """Возврат к среднему: z-score(20д) < −2 — купить, z > 0 — продать. Раз в час."""
    name = "meanrev"
    instruments = BASKET
    N = 20

    def on_tick(self, ctx: Ctx) -> None:
        if not hourly(ctx):
            return
        eq = ctx.equity()
        per = eq["total"] * 0.95 / len(BASKET)
        for t in BASKET:
            closes = ctx.daily_closes(t)
            px = ctx.prices.get(t, 0.0)
            if len(closes) < self.N or not px:
                continue
            m = sma(closes, self.N)
            sd = (sum((c - m) ** 2 for c in closes[-self.N:]) / self.N) ** 0.5
            if not sd:
                continue
            z = (px - m) / sd
            have = eq["positions"].get(t, 0)
            if z < -2 and have == 0:
                lots = int(per / rub_value(t, px, 1))
                if lots > 0 and eq["cash"] > rub_value(t, px, lots):
                    ctx.market(t, "buy", lots)
            elif z > 0 and have > 0:
                ctx.market(t, "sell", have)


class GoldTrend(Strategy):
    """Тренд по золоту: фьюч GLDRUBF, SMA10/30, лонг-онли. Цена в пунктах."""
    name = "gold_trend"
    instruments = ["GLDRUBF"]
    FAST, SLOW = 10, 30

    def on_tick(self, ctx: Ctx) -> None:
        if not hourly(ctx):
            return
        closes = ctx.daily_closes("GLDRUBF", 60)
        px = ctx.prices.get("GLDRUBF", 0.0)
        if len(closes) < self.SLOW or not px:
            return
        want = sma(closes, self.FAST) > sma(closes, self.SLOW)
        eq = ctx.equity()
        have = eq["positions"].get("GLDRUBF", 0)
        if want and have == 0:
            lots = int(eq["total"] * 0.9 / rub_value("GLDRUBF", px, 1))
            if lots > 0:
                ctx.market("GLDRUBF", "buy", lots)
        elif not want and have > 0:
            ctx.market("GLDRUBF", "sell", have)


REGISTRY = {s.name: s for s in (BuyHold, RandomTrader, Grid, Momentum, MeanRev, GoldTrend)}

# Активные в ферме стратегии. Режим бенчмарков (2026-06-17): кандидаты grid/momentum/
# meanrev/gold_trend не прошли study (Deflated Sharpe = 0%, устойчивого OOS-эджа нет),
# форвардить нечего до прохождения бэктеста. Их код выше сохранён; вернуть в работу =
# дописать имя сюда. buyhold/random — контроли, живой замер исполнения песочницы.
ACTIVE = ("buyhold", "random")

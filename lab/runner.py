"""Strategy Lab: раннер. Счёт на стратегию, общий тик, изоляция ошибок, graceful stop.

    python3 -m lab.runner
"""
import json
import os
import signal
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .api import call, to_f
from .journal import Journal
from .strategy import Ctx
from .strategies import REGISTRY, ACTIVE
from .instruments import INSTRUMENTS

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "lab_state.json"
LOCK = ROOT / "lab.pid"

VIRTUAL_CASH = 100_000
TICK_OPEN, TICK_CLOSED = 60, 300
MSK = timezone(timedelta(hours=3))


def market_open() -> bool:
    now = datetime.now(MSK)
    return now.weekday() < 5 and 10 <= now.hour < 24


def load_state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def save_state(st: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)


def acquire_lock() -> None:
    if LOCK.exists():
        pid = LOCK.read_text().strip()
        try:
            os.kill(int(pid), 0)
            raise SystemExit(f"lab уже запущена (PID {pid})")
        except (OSError, ValueError):
            pass
    LOCK.write_text(str(os.getpid()))


def ensure_account(st: dict, name: str, log) -> str:
    accs = st.setdefault("accounts", {})
    if name in accs:
        return accs[name]
    acc = call("SandboxService/OpenSandboxAccount", {"name": f"lab-{name}"})["accountId"]
    call("SandboxService/SandboxPayIn", {
        "accountId": acc, "amount": {"units": str(VIRTUAL_CASH), "nano": 0, "currency": "rub"}})
    accs[name] = acc
    log.event(name, "account_open", acc)
    return acc


def last_prices() -> dict:
    """ticker -> цена, одним батч-запросом по всем инструментам реестра."""
    uids = {m["uid"]: t for t, m in INSTRUMENTS.items()}
    r = call("MarketDataService/GetLastPrices", {"instrumentId": list(uids)})
    return {uids[x["instrumentUid"]]: to_f(x["price"])
            for x in r.get("lastPrices", []) if x.get("instrumentUid") in uids}


def main():
    acquire_lock()
    j = Journal()
    st = load_state()
    names = list(ACTIVE)
    print(f"lab v2 [режим бенчмарков]: стратегии {names}, инструментов {len(INSTRUMENTS)}, "
          f"тик {TICK_OPEN}/{TICK_CLOSED}с", flush=True)

    ctxs = {}
    for name in names:
        acc = ensure_account(st, name, j)
        ctx = Ctx(acc, j, name)
        ctx.state = st.setdefault("strategy_state", {}).setdefault(name, {})
        ctxs[name] = ctx
    save_state(st)

    stop = {"flag": False}
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *a: stop.update(flag=True))

    started = {n: False for n in names}
    while not stop["flag"]:
        tick_t0 = time.time()
        is_open = market_open()
        # Вне торговой сессии стратегии НЕ тикаем: песочница отбивает любую заявку
        # с HTTP 400 (code 3), а цены всё равно не меняются. Раньше on_tick звался
        # круглосуточно — отсюда серии 400 у grid/random в 04:00 (биржа закрыта).
        if is_open:
            try:
                prices = last_prices()
            except RuntimeError as e:
                j.event("lab", "price_fail", str(e))
                time.sleep(30)
                continue
            for name, ctx in ctxs.items():
                ctx.prices = prices
                strat = REGISTRY[name]()
                try:
                    if not started[name]:
                        strat.on_start(ctx)
                        started[name] = True
                    strat.on_tick(ctx)
                    eq = ctx.equity()
                    j.equity(name, eq["total"], eq["cash"], eq["pos_lots"])
                except Exception as e:  # стратегия не должна валить ферму
                    j.event(name, "tick_error", repr(e))
                time.sleep(1)  # бережём лимиты
            save_state(st)
        tick = TICK_OPEN if is_open else TICK_CLOSED
        while time.time() - tick_t0 < tick and not stop["flag"]:
            time.sleep(1)

    save_state(st)
    LOCK.unlink(missing_ok=True)
    print("lab: остановлена", flush=True)


if __name__ == "__main__":
    main()

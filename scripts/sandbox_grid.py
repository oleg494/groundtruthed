"""Грид-бот в ПЕСОЧНИЦЕ T-Invest. Учебный эксперимент, не боевая торговля.

Спека: docs/superpowers/specs/2026-06-11-sandbox-grid-design.md
Безопасность: BASE захардкожен на sandbox-домен, токен ТОЛЬКО TINVEST_SANDBOX_KEY
(боевой TINVEST_API_KEY не читается; sandbox-токен на боевых методах не работает).

    python scripts/sandbox_grid.py        # Ctrl+C — снять заявки и выйти с отчётом
"""
import json
import os
import signal
import sys
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime
from pathlib import Path

BASE = "https://sandbox-invest-public-api.tinkoff.ru/rest"  # ТОЛЬКО песочница
ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "analysis" / "sandbox_grid_state.json"

# ── параметры стратегии ──────────────────────────────────────────────
INSTRUMENT_UID = "e6123145-9665-43e0-8413-cd61b8aa9b13"  # SBER
TICKER = "SBER"
LOT = 10                  # акций в лоте SBER
PRICE_STEP = 0.01         # шаг цены SBER
GRID_STEP_PCT = 0.005     # 0.5% между уровнями
LEVELS = 5                # уровней в каждую сторону
LOTS_PER_LEVEL = 1
POLL_SEC = 5
VIRTUAL_CASH = 100_000


def load_token() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_SANDBOX_KEY="):
            tok = line.split("=", 1)[1].strip()
            if tok:
                return tok
    raise SystemExit(
        "Нет TINVEST_SANDBOX_KEY в .env.\n"
        "Выпусти sandbox-токен: Т-Инвестиции → настройки → токены → токен для песочницы,\n"
        "и добавь строку TINVEST_SANDBOX_KEY=<токен> в .env")


TOKEN = load_token()


def call(method: str, payload: dict, retries: int = 5) -> dict:
    url = f"{BASE}/tinkoff.public.invest.api.contract.v1.{method}"
    data = json.dumps(payload).encode()
    delay = 2.0
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {TOKEN}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(float(e.headers.get("Retry-After", delay)))
                delay = min(delay * 2, 20)
                continue
            raise RuntimeError(f"HTTP {e.code} on {method}: {body}") from e
    raise RuntimeError("retries exhausted")


def to_f(v) -> float:
    if not v:
        return 0.0
    if isinstance(v, dict):
        if v.get("value") not in (None, ""):
            return float(v["value"])
        return int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9
    return 0.0


def quot(p: float) -> dict:
    p = round(round(p / PRICE_STEP) * PRICE_STEP, 9)  # к шагу цены
    units = int(p)
    nano = int(round((p - units) * 1e9))
    return {"units": str(units), "nano": nano}


def now_s() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ── состояние ────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(st: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


# ── песочница: счёт ──────────────────────────────────────────────────
def ensure_account(st: dict) -> str:
    accs = call("SandboxService/GetSandboxAccounts", {}).get("accounts", [])
    open_accs = [a for a in accs if a.get("status") == "ACCOUNT_STATUS_OPEN"]
    if st.get("account_id") and any(a["id"] == st["account_id"] for a in open_accs):
        return st["account_id"]
    if open_accs:
        acc = open_accs[0]["id"]
    else:
        acc = call("SandboxService/OpenSandboxAccount", {})["accountId"]
        call("SandboxService/SandboxPayIn", {
            "accountId": acc,
            "amount": {"units": str(VIRTUAL_CASH), "nano": 0, "currency": "rub"},
        })
        print(f"{now_s()} открыт sandbox-счёт {acc}, зачислено {VIRTUAL_CASH} ₽")
    st["account_id"] = acc
    return acc


def last_price() -> float:
    r = call("MarketDataService/GetLastPrices", {"instrumentId": [INSTRUMENT_UID]})
    return to_f(r["lastPrices"][0]["price"])


def active_orders(acc: str) -> dict:
    r = call("SandboxService/GetSandboxOrders", {"accountId": acc})
    return {o["orderId"]: o for o in r.get("orders", [])}


def post_limit(acc: str, side: str, price: float) -> str:
    direction = "ORDER_DIRECTION_BUY" if side == "buy" else "ORDER_DIRECTION_SELL"
    r = call("SandboxService/PostSandboxOrder", {
        "accountId": acc, "instrumentId": INSTRUMENT_UID,
        "quantity": str(LOTS_PER_LEVEL), "price": quot(price),
        "direction": direction, "orderType": "ORDER_TYPE_LIMIT",
        "orderId": str(uuid.uuid4()),
    })
    return r["orderId"]


def cancel_order(acc: str, order_id: str) -> None:
    try:
        call("SandboxService/CancelSandboxOrder", {"accountId": acc, "orderId": order_id})
    except RuntimeError as e:
        print(f"{now_s()} не снялась {order_id[:8]}: {e}")


# ── сетка ────────────────────────────────────────────────────────────
def build_grid(st: dict, base: float) -> None:
    st["base_price"] = base
    st["levels"] = []
    for i in range(1, LEVELS + 1):
        st["levels"].append({"idx": -i, "price": round(base * (1 - GRID_STEP_PCT * i), 2),
                             "side": "buy", "order_id": None, "status": "pending"})
        st["levels"].append({"idx": i, "price": round(base * (1 + GRID_STEP_PCT * i), 2),
                             "side": "sell", "order_id": None, "status": "pending"})
    st.setdefault("fills", [])
    st.setdefault("realized_pnl", 0.0)
    st.setdefault("position_lots", 0)


def place_pending(st: dict, acc: str) -> int:
    """Выставить уровни без активной заявки. Sell — только под имеющуюся позицию."""
    placed = 0
    sells_active = sum(1 for l in st["levels"] if l["side"] == "sell" and l["status"] == "active")
    sell_budget = st["position_lots"] - sells_active
    for lv in sorted(st["levels"], key=lambda l: abs(l["idx"])):
        if lv["status"] != "pending":
            continue
        if lv["side"] == "sell":
            if sell_budget <= 0:
                continue
            sell_budget -= 1
        try:
            lv["order_id"] = post_limit(acc, lv["side"], lv["price"])
            lv["status"] = "active"
            placed += 1
        except RuntimeError as e:
            lv["status"] = "error"
            print(f"{now_s()} уровень {lv['idx']} ({lv['side']} {lv['price']}): {e}")
        time.sleep(0.25)
    return placed


def order_filled(acc: str, order_id: str) -> bool:
    """Заявки нет в активных — исполнена или отменена? Спрашиваем статус."""
    try:
        r = call("SandboxService/GetSandboxOrderState", {"accountId": acc, "orderId": order_id})
        return int(r.get("lotsExecuted", 0)) > 0
    except RuntimeError:
        return False  # не нашли заявку — считаем отменённой, уровень перевыставится


def process_fills(st: dict, acc: str) -> int:
    """Уровень исполнен (подтверждаем статусом, отмена != исполнение) → классика грида."""
    act = active_orders(acc)
    fills = 0
    for lv in st["levels"]:
        if lv["status"] != "active" or lv["order_id"] in act:
            continue
        if not order_filled(acc, lv["order_id"]):
            # отменена снаружи — вернуть в очередь на перевыставление
            print(f"{now_s()} уровень {lv['idx']} отменён снаружи, перевыставлю")
            lv["order_id"] = None
            lv["status"] = "pending"
            continue
        fills += 1
        st["fills"].append({"ts": datetime.now().isoformat(timespec="seconds"),
                            "side": lv["side"], "price": lv["price"], "lots": LOTS_PER_LEVEL})
        if lv["side"] == "buy":
            st["position_lots"] += LOTS_PER_LEVEL
            print(f"{now_s()} ИСПОЛНЕН buy {lv['price']} (уровень {lv['idx']})")
            # парная продажа на шаг выше
            pair = round(lv["price"] * (1 + GRID_STEP_PCT), 2)
            st["levels"].append({"idx": lv["idx"], "price": pair, "side": "sell",
                                 "order_id": None, "status": "pending"})
        else:
            st["position_lots"] -= LOTS_PER_LEVEL
            buys = [f["price"] for f in st["fills"] if f["side"] == "buy"]
            cost = buys[-1] if buys else st["base_price"]
            pnl = (lv["price"] - cost) * LOT * LOTS_PER_LEVEL
            st["realized_pnl"] += pnl
            print(f"{now_s()} ИСПОЛНЕН sell {lv['price']} → P&L {pnl:+.2f} ₽")
            pair = round(lv["price"] * (1 - GRID_STEP_PCT), 2)
            st["levels"].append({"idx": lv["idx"], "price": pair, "side": "buy",
                                 "order_id": None, "status": "pending"})
        lv["status"] = "filled"
    return fills


def shutdown(st: dict, acc: str, started: float) -> None:
    print(f"\n{now_s()} остановка: снимаю заявки...")
    for lv in st["levels"]:
        if lv["status"] == "active" and lv["order_id"]:
            cancel_order(acc, lv["order_id"])
            lv["status"] = "cancelled"
            time.sleep(0.2)
    save_state(st)
    mins = (time.time() - started) / 60
    n = len(st["fills"])
    turn = sum(f["price"] * LOT * f["lots"] for f in st["fills"])
    print(f"── отчёт ──\n  работал: {mins:.1f} мин\n  сделок: {n}\n"
          f"  оборот: {turn:,.0f} ₽\n  позиция: {st['position_lots']} лот.\n"
          f"  реализованный P&L: {st['realized_pnl']:+.2f} ₽\n"
          f"  state: {STATE_FILE}")


LOCK_FILE = ROOT / "analysis" / "sandbox_grid.pid"


def pid_alive(pid: int) -> bool:
    if os.name == "nt":  # os.kill(pid, 0) на Windows не работает (WinError 87)
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock() -> None:
    if LOCK_FILE.exists():
        old = LOCK_FILE.read_text().strip()
        if old.isdigit() and pid_alive(int(old)):
            raise SystemExit(f"Уже запущен (PID {old}). Второй экземпляр сетки = хаос в заявках.")
    LOCK_FILE.write_text(str(os.getpid()))


def main():
    acquire_lock()
    print(f"SANDBOX grid-бот {TICKER}: шаг {GRID_STEP_PCT*100:.1f}%, "
          f"{LEVELS}+{LEVELS} уровней, лот на уровень. Ctrl+C — стоп.")
    st = load_state()
    acc = ensure_account(st)

    price = last_price()
    if not st.get("levels"):
        build_grid(st, price)
        print(f"{now_s()} сетка от {price:.2f}: "
              f"buy {min(l['price'] for l in st['levels']):.2f}..{price:.2f}"
              f"..sell {max(l['price'] for l in st['levels']):.2f}")
    else:
        print(f"{now_s()} продолжаю по state ({len(st['fills'])} сделок, "
              f"P&L {st['realized_pnl']:+.2f})")
        # сверка: заявки, помеченные active, но умершие пока нас не было
        process_fills(st, acc)

    save_state(st)
    started = time.time()
    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *a: stop.update(flag=True))

    last_beat = 0.0
    while not stop["flag"]:
        try:
            fills = process_fills(st, acc)
            placed = place_pending(st, acc)
            if fills or placed:
                save_state(st)
            if fills or placed or time.time() - last_beat > 60:
                price = last_price()
                nb = sum(1 for l in st["levels"] if l["status"] == "active" and l["side"] == "buy")
                ns = sum(1 for l in st["levels"] if l["status"] == "active" and l["side"] == "sell")
                print(f"{now_s()} {TICKER} {price:.2f} | grid {nb}buy/{ns}sell | "
                      f"pos {st['position_lots']} лот | fills {len(st['fills'])} | "
                      f"P&L {st['realized_pnl']:+.2f}")
                last_beat = time.time()
        except (RuntimeError, urllib.error.URLError) as e:
            print(f"{now_s()} ошибка цикла: {e} — продолжаю")
        for _ in range(POLL_SEC * 10):
            if stop["flag"]:
                break
            time.sleep(0.1)

    shutdown(st, acc, started)
    LOCK_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

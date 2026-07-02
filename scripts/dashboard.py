"""Дашборд одной командой: рынок + портфель в один экран.

    python scripts/dashboard.py

READ-ONLY: только get/list-методы T-Invest REST. Никаких заявок.
Источник цен MarketData — для фьючей/облиг пункты, но здесь только индексы/акции/фонд в валюте.
Деньги приходят как {units,nano} или {value} — парсим через to_f().
"""
import json
import time
import sys
import urllib.request
import urllib.error
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent

# ── ANSI ─────────────────────────────────────────────────────────────
G, R, Y, B, DIM, BOLD, X = (
    "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[2m", "\033[1m", "\033[0m"
)


def col(pct: float) -> str:
    return G if pct > 0.05 else R if pct < -0.05 else Y



def load_accounts():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_ACCOUNTS="):
            return [a.strip() for a in line.split("=", 1)[1].split(",") if a.strip()]
    raise SystemExit("Set TINVEST_ACCOUNTS=id1,id2 in .env")

def load_token() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no token in .env")


TOKEN = load_token()


def call(method: str, payload: dict, retries: int = 5) -> dict:
    url = f"{BASE}/tinkoff.public.invest.api.contract.v1.{method}"
    data = json.dumps(payload).encode()
    delay = 1.5
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {TOKEN}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                wait = float(e.headers.get("Retry-After", delay))
                time.sleep(wait)
                delay = min(delay * 2, 20)
                continue
            sys.stderr.write(f"HTTP {e.code} on {method}\n")
            return {}
    return {}


def to_f(v) -> float:
    if not v:
        return 0.0
    if isinstance(v, dict):
        if v.get("value") not in (None, ""):
            return float(v["value"])
        return int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9
    return 0.0


# ── справочники UID ──────────────────────────────────────────────────
# Индексы/ставки-индикативы IMOEX/RTSI/RGBI/RVI/RUSFAR (и USD-индикатив) УДАЛЕНЫ из API
# 2026-07 — GetMarketValues молча их пропускает (см. docs/gotchas.md «Индексы/ставки удалены»).
# Индекс — через перпетуал-фьючерс IMOEXF (цена в ПУНКТАХ); у RTSI/RGBI/RVI/RUSFAR прокси нет.
# Живые проверены 2026-07-01.
INDICES = [
    ("IMOEX(ф)", "5bcff194-f10d-4314-b9ee-56b7fdb344fd", "Фьючерс IMOEXF (прокси индекса, пункты)"),
    ("BRENT",    "046d2dbd-1594-4387-93c2-ed17b067b0f5", "Нефть Brent $"),
]
BLUECHIPS = [
    ("SBER", "e6123145-9665-43e0-8413-cd61b8aa9b13"),
    ("GAZP", "962e2a95-02a9-4171-abd7-aa198dbe643a"),
    ("LKOH", "02cfdf61-6298-4c0f-a9ca-9cabc82afaf3"),
    ("ROSN", "fd417230-19cf-4e7b-9623-f7c9ca18ec6b"),
    ("GMKN", "509edd0c-129c-4ee2-934d-7f6246126da1"),
    ("YDEX", "7de75794-a27f-4d81-a39b-492345813822"),
    ("T",    "87db07bc-0e02-4e29-90bb-05e8ef791d7b"),
    ("OZON", "75e003c2-ca14-4980-8d7b-e82ec6b6ffe1"),
    ("PLZL", "10620843-28ce-44e8-80c2-f26ceb1bd3e1"),
    ("CHMF", "fa6aae10-b8d5-48c8-bbfd-d320d925d096"),
    ("NVTK", "0da66728-6c30-44c4-9264-df8fac2467ee"),
    ("MGNT", "ca845f68-6c43-44bc-b584-330d2a1e5eb7"),
]
ACCOUNTS = load_accounts()
KEYRATE = 14.25  # ключевая ставка ЦБ (снижена 2026-06-19); дубль в scripts/market_context.py:25 — менять синхронно


def mvals(uids):
    r = call("MarketDataService/GetMarketValues", {
        "instrumentId": uids,
        "values": ["INSTRUMENT_VALUE_LAST_PRICE", "INSTRUMENT_VALUE_CLOSE_PRICE"],
    })
    out = {}
    for it in r.get("instruments", []):
        d = {v["type"]: to_f(v["value"]) for v in it.get("values", [])}
        out[it["instrumentUid"]] = (
            d.get("INSTRUMENT_VALUE_LAST_PRICE", 0.0),
            d.get("INSTRUMENT_VALUE_CLOSE_PRICE", 0.0),
        )
    return out


def line(name, last, close, width=14):
    if close:
        pct = (last / close - 1) * 100
        c = col(pct)
        arrow = "▲" if pct > 0.05 else "▼" if pct < -0.05 else "="
        return f"  {name:<16}{last:>11.2f}  {c}{arrow}{pct:+6.2f}%{X}"
    return f"  {name:<16}{last:>11.2f}  {DIM}    —   {X}"


def main():
    now = time.strftime("%Y-%m-%d %H:%M")
    print(f"\n{BOLD}{B}╔══ ДАШБОРД T-INVEST ══ {now} ══╗{X}")

    # схема торгов
    sch = call("InstrumentsService/TradingSchedules", {
        "exchange": "MOEX",
        "from": time.strftime("%Y-%m-%dT00:00:00Z"),
        "to": time.strftime("%Y-%m-%dT23:59:59Z"),
    })
    is_trading = False
    try:
        is_trading = sch["exchanges"][0]["days"][0].get("isTradingDay", False)
    except (KeyError, IndexError):
        pass
    status = f"{G}● ТОРГИ ИДУТ{X}" if is_trading else f"{DIM}○ биржа закрыта{X}"
    print(f"  MOEX: {status}    Ключевая ставка ЦБ: {BOLD}{KEYRATE}%{X}")

    # ── ИНДЕКСЫ ──
    idx = mvals([u for _, u, _ in INDICES])
    print(f"\n{BOLD}── Индексы и макро ──{X}")
    for tk, u, nm in INDICES:
        if u in idx:
            last, close = idx[u]
            print(line(f"{tk} {DIM}{nm}{X}", last, close))

    # ── ГОЛУБЫЕ ФИШКИ ──
    bc = mvals([u for _, u in BLUECHIPS])
    rows = []
    for tk, u in BLUECHIPS:
        if u in bc:
            last, close = bc[u]
            pct = (last / close - 1) * 100 if close else 0.0
            rows.append((tk, last, pct))
    rows.sort(key=lambda x: -x[2])
    print(f"\n{BOLD}── Голубые фишки (по движению) ──{X}")
    for tk, last, pct in rows:
        c = col(pct)
        bar = "█" * min(int(abs(pct) * 2), 12)
        side = f"{c}{bar}{X}" if pct >= 0 else f"{c}{bar}{X}"
        print(f"  {tk:<6}{last:>10.2f}  {c}{pct:+6.2f}%{X}  {side}")

    # ── ПОРТФЕЛЬ ──
    print(f"\n{BOLD}── Твой портфель ──{X}")
    total_all = 0.0
    for acc in ACCOUNTS:
        p = call("OperationsService/GetPortfolio", {"accountId": acc})
        if not p:
            continue
        tot = to_f(p.get("totalAmountPortfolio"))
        dy = to_f(p.get("dailyYield"))
        ey = to_f(p.get("expectedYield"))  # это процент
        total_all += tot
        dyr = to_f(p.get("dailyYieldRelative"))
        cd = col(dy)
        print(f"  счёт {acc}:  {BOLD}{tot:>10.2f} ₽{X}  "
              f"день {cd}{dy:+.2f} ₽ ({dyr:+.2f}%){X}  накопл {ey:+.2f}%")
    print(f"  {DIM}{'─'*44}{X}")
    print(f"  {BOLD}ИТОГО капитал: {total_all:,.2f} ₽{X}".replace(",", " "))
    print()


if __name__ == "__main__":
    main()

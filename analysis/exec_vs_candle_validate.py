"""Цена сделки vs дневная свеча: цена исполнения внутри [low, high] своего дня.

    python analysis/exec_vs_candle_validate.py

Кросс-эндпоинт оракул (Operations ↔ MarketData): цена каждой исполненной сделки обязана лежать
в диапазоне [low, high] дневной свечи того торгового дня. Если сделка вне диапазона свечи —
рассогласование между слоями данных (или сделка вне основной сессии). Проверяем все сделки счёта.

READ-ONLY.
"""
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

def load_accounts():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_ACCOUNTS="):
            return [a.strip() for a in line.split("=", 1)[1].split(",") if a.strip()]
    raise SystemExit("Set TINVEST_ACCOUNTS=id1,id2 in .env")


ACCOUNTS = load_accounts()


def load_token():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no token")


TOKEN = load_token()


def call(method, payload, retries=5):
    url = f"{BASE}/tinkoff.public.invest.api.contract.v1.{method}"
    data = json.dumps(payload).encode()
    delay = 2.0
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {TOKEN}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(float(e.headers.get("Retry-After", delay)))
                delay = min(delay * 2, 20)
                continue
            raise
    raise SystemExit("retries")


def mvf(v):
    if not v:
        return 0.0
    return float(v["value"]) if v.get("value") not in (None, "") else \
        int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9


def trades(acc):
    out, cur = [], ""
    while True:
        r = call("OperationsService/GetOperationsByCursor",
                 {"accountId": acc, "from": "2025-01-01T00:00:00Z", "to": "2026-06-19T00:00:00Z",
                  "operationTypes": ["OPERATION_TYPE_BUY", "OPERATION_TYPE_SELL"],
                  "state": "OPERATION_STATE_EXECUTED",
                  "cursorPagination": {"cursor": cur, "limit": 1000}})
        out += r.get("items", [])
        if not r.get("hasNext") or not r.get("nextCursor") or r.get("nextCursor") == cur:
            break
        cur = r["nextCursor"]
    return out


_ccache = {}


def day_candle(uid, day):
    key = (uid, day)
    if key in _ccache:
        return _ccache[key]
    frm = day + "T00:00:00Z"
    to = day + "T23:59:59Z"
    r = call("MarketDataService/GetCandles",
             {"instrumentId": uid, "interval": "CANDLE_INTERVAL_DAY", "from": frm, "to": to})
    cs = r.get("candles", [])
    res = (mvf(cs[0]["low"]), mvf(cs[0]["high"])) if cs else None
    _ccache[key] = res
    return res


def main():
    print(f"{BOLD}Цена сделки vs дневная свеча: исполнение внутри [low, high]{X}\n")
    gtot = gin = gno = 0
    worst = None
    for acc in ACCOUNTS:
        tr = trades(acc)
        n = inside = nocandle = 0
        for o in tr:
            p = mvf(o.get("price"))
            uid = o.get("instrumentUid")
            day = o.get("date", "")[:10]
            if p == 0 or not uid or not day:
                continue
            dc = day_candle(uid, day)
            n += 1
            if dc is None:
                nocandle += 1
                continue
            lo, hi = dc
            # допуск на тик
            if lo - 0.01 <= p <= hi + 0.01:
                inside += 1
            else:
                dist = min(abs(p - lo), abs(p - hi))
                if worst is None or dist > worst[1]:
                    worst = (f"{acc} {day}", dist, p, lo, hi)
        gtot += n; gin += inside; gno += nocandle
        out = n - inside - nocandle
        c = G if out == 0 else R
        print(f"  {c}счёт {acc}{X}: сделок={n}, внутри свечи={inside}, "
              f"вне={out}, без свечи={nocandle}")

    print(f"\n{BOLD}Итог: {gin}/{gtot-gno} сделок внутри [low,high] своей свечи "
          f"(+{gno} без дневной свечи){X}")
    if worst:
        print(f"  {DIM}worst {worst[0]}: цена {worst[2]} vs [{worst[3]}, {worst[4]}] "
              f"(вне на {worst[1]:.2f}){X}")
    ok = gin == gtot - gno
    print((G + "✓ Каждая сделка исполнена внутри диапазона дневной свечи — слои Operations и "
           "MarketData согласованы." + X) if ok
          else (Y + "≈ есть сделки вне диапазона свечи (внесессионные/аукцион/разные источники) — см. worst" + X))


if __name__ == "__main__":
    main()

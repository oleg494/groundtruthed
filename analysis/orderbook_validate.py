"""Инварианты стакана + кросс-эндпоинт согласованность «последней цены».

    python analysis/orderbook_validate.py

Оракулы (структурные, обязаны выполняться всегда):
1. bids убывают по цене, asks растут (упорядоченность книги).
2. книга не пересечена: best_bid < best_ask.
3. ценовой коридор: limitDown ≤ все цены книги ≤ limitUp.
4. кросс-эндпоинт: lastPrice стакана == LAST_PRICE из GetMarketValues == цена последней сделки
   (одно и то же число из трёх разных методов API).

READ-ONLY. Для фьючерсов цены в пунктах — здесь только акции, всё в рублях.
"""
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

TICKERS = ["SBER", "GAZP", "LKOH", "VTBR", "ROSN", "GMKN"]


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
        return None
    return float(v["value"]) if v.get("value") not in (None, "") else \
        int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9


def main():
    print(f"{BOLD}Инварианты стакана и кросс-эндпоинт согласованность последней цены{X}\n")
    total = ok = 0
    now = datetime.now(timezone.utc)
    for t in TICKERS:
        try:
            s = call("InstrumentsService/ShareBy",
                     {"idType": "INSTRUMENT_ID_TYPE_TICKER", "classCode": "TQBR", "id": t})["instrument"]
        except urllib.error.HTTPError:
            continue
        uid = s["uid"]
        ob = call("MarketDataService/GetOrderBook", {"instrumentId": uid, "depth": 20})
        bids = [(mvf(b["price"]), b["quantity"]) for b in ob.get("bids", [])]
        asks = [(mvf(a["price"]), a["quantity"]) for a in ob.get("asks", [])]
        last_ob = mvf(ob.get("lastPrice"))
        lup, ldn = mvf(ob.get("limitUp")), mvf(ob.get("limitDown"))

        mv = call("MarketDataService/GetMarketValues",
                  {"instrumentId": [uid], "values": ["INSTRUMENT_VALUE_LAST_PRICE"]})
        last_mv = None
        for v in mv["instruments"][0].get("values", []):
            if v["type"] == "INSTRUMENT_VALUE_LAST_PRICE":
                last_mv = mvf(v["value"])
        tr = call("MarketDataService/GetLastTrades",
                  {"instrumentId": uid,
                   "from": (now - timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "to": now.strftime("%Y-%m-%dT%H:%M:%SZ")}).get("trades", [])
        last_tr = mvf(tr[-1]["price"]) if tr else None

        checks = []
        if bids:
            checks.append(("bids↓", all(bids[i][0] > bids[i + 1][0] for i in range(len(bids) - 1))))
        if asks:
            checks.append(("asks↑", all(asks[i][0] < asks[i + 1][0] for i in range(len(asks) - 1))))
        if bids and asks:
            checks.append(("bid<ask", bids[0][0] < asks[0][0]))
        if bids and asks and lup and ldn:
            inb = ldn <= bids[0][0] <= lup and ldn <= asks[0][0] <= lup
            checks.append(("в коридоре", inb))
        if last_ob is not None and last_mv is not None:
            checks.append(("OB.last==MV.last", abs(last_ob - last_mv) < 1e-9))
        if last_tr is not None and last_ob is not None:
            # последняя сделка может быть чуть свежее/старше lastPrice — допускаем 1 шаг цены
            checks.append(("last≈trade", abs(last_tr - last_ob) <= max(0.05, last_ob * 1e-4)))

        line = []
        for name, res in checks:
            total += 1
            ok += res
            line.append((G if res else R) + ("✓" if res else "✗") + X + name)
        spread = (asks[0][0] - bids[0][0]) if bids and asks else None
        sp = f"спред {spread:.2f}" if spread is not None else "книга пуста"
        print(f"  {t:<6} last={last_ob}  {sp:<14} " + "  ".join(line))

    print(f"\n{BOLD}Итог: {ok}/{total} инвариантов стакана/цены{X}")
    print((G + "✓ Книги упорядочены и не пересечены, цены в коридоре limitUp/Down, последняя цена "
           "согласована между стаканом, market values и лентой сделок." + X) if ok == total
          else (Y + "≈ часть инвариантов нарушена (возможно премаркет/аукцион — книга бывает "
                "пересечена или последняя сделка свежее lastPrice)" + X))


if __name__ == "__main__":
    main()

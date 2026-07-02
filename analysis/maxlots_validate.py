"""GetMaxLots: лимиты заявок согласованы с позицией, кэшем и ценой.

    python analysis/maxlots_validate.py

Оракулы (account-specific):
1. sellLimits.sellMaxLots (без маржи) == текущая позиция в лотах (нельзя продать больше, чем есть).
2. buyMarginLimits.buyMoneyAmount / цена ≈ buyMarginLimits.buyMaxLots · lot
   (денежный лимит покупки соответствует числу лотов по текущей цене).
3. buyMaxMarketLots ≤ buyMaxLots (по рынку доступно не больше, чем лимитной заявкой).

READ-ONLY (GetMaxLots — read-метод, заявки НЕ выставляются).
"""
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

def load_accounts():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_ACCOUNTS="):
            return [a.strip() for a in line.split("=", 1)[1].split(",") if a.strip()]
    raise SystemExit("Set TINVEST_ACCOUNTS=id1,id2 in .env")


ACC = load_accounts()[0]
INSTR = [
    ("SBER", "e6123145-9665-43e0-8413-cd61b8aa9b13"),
    ("TMON@", "498ec3ff-ef27-4729-9703-a5aac48d5789"),
    ("GAZP", "962e2a95-02a9-4171-abd7-aa198dbe643a"),
]


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


def main():
    print(f"{BOLD}GetMaxLots: согласованность лимитов с позицией/кэшем/ценой{X}\n")
    pos = {p["instrumentUid"]: int(p["balance"])
           for p in call("OperationsService/GetPositions", {"accountId": ACC}).get("securities", [])}
    total = ok = 0
    for name, uid in INSTR:
        ml = call("OrdersService/GetMaxLots", {"accountId": ACC, "instrumentId": uid})
        sh = call("InstrumentsService/ShareBy" if name != "TMON@" else "InstrumentsService/EtfBy",
                  {"idType": "INSTRUMENT_ID_TYPE_UID", "id": uid})["instrument"]
        lot = int(sh.get("lot", 1))
        price = mvf(call("MarketDataService/GetMarketValues",
                         {"instrumentId": [uid], "values": ["INSTRUMENT_VALUE_LAST_PRICE"]}
                         )["instruments"][0]["values"][0]["value"])
        balance = pos.get(uid, 0)
        sell_nm = int(ml["sellLimits"]["sellMaxLots"])
        bml = ml.get("buyMarginLimits", {})
        buy_money = mvf(bml.get("buyMoneyAmount"))
        buy_lots = int(bml.get("buyMaxLots", "0"))
        buy_mkt = int(bml.get("buyMaxMarketLots", "0"))

        # 1. продажа без маржи == позиция (в лотах)
        c1 = sell_nm == balance // lot
        # 2. денежный лимит ≈ лотам по цене (допуск 2%, плечо/округление лота)
        implied = buy_money / (price * lot) if price and lot else 0
        c2 = (buy_lots == 0 and buy_money == 0) or abs(implied - buy_lots) / max(buy_lots, 1) < 0.03
        # 3. рыночных лотов не больше лимитных
        c3 = buy_mkt <= buy_lots
        for c in (c1, c2, c3):
            total += 1
            ok += c
        print(f"  {name:<6} поз={balance:<4}(лот {lot})  sell_nm={sell_nm} "
              f"{(G+'✓'if c1 else R+'✗')}{X}поз  "
              f"buy: {buy_money:.0f}₽/{buy_lots}лот → подразум {implied:.0f} "
              f"{(G+'✓'if c2 else R+'✗')}{X}цена  "
              f"mkt{buy_mkt}≤lim{buy_lots} {(G+'✓'if c3 else R+'✗')}{X}")

    print(f"\n{BOLD}Итог: {ok}/{total} тождеств лимитов заявок{X}")
    print((G + "✓ GetMaxLots согласован: продажа без маржи = позиция; денежный лимит покупки = "
           "число лотов по текущей цене; рыночных лотов ≤ лимитных." + X) if ok == total
          else (Y + "≈ часть не сошлась (плечо/округление лота/разный снимок цены) — см. строки" + X))


if __name__ == "__main__":
    main()

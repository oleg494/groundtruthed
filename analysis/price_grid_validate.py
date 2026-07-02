"""Сетка цен: все цены свечей кратны minPriceIncrement (шагу цены) инструмента.

    python analysis/price_grid_validate.py

Оракул — minPriceIncrement из карточки инструмента. Каждая цена (open/high/low/close) обязана
лежать на сетке шага цены: price / tick — целое. Проверяем по тысячам свечей; заодно вскрываем
шаг цены по классам (акции, облигации в %, фьючерс в пунктах). READ-ONLY.
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

INSTR = [
    ("SBER акция", "e6123145-9665-43e0-8413-cd61b8aa9b13", "ShareBy"),
    ("GAZP акция", "962e2a95-02a9-4171-abd7-aa198dbe643a", "ShareBy"),
    ("ОФЗ 26238", "92b9e913-d7df-4164-bd83-1013c819bf44", "BondBy"),
    ("ОФЗ 26248", "043ffb17-07e9-4c79-941f-4c5712d133cd", "BondBy"),
    ("SiU6 фьюч", "574d37d8-9de4-423a-9e33-b936002d8bda", "FutureBy"),
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
    return float(v["value"]) if v.get("value") not in (None, "") else \
        int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9


def main():
    print(f"{BOLD}Сетка цен: цены свечей кратны minPriceIncrement{X}\n")
    frm = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%dT00:00:00Z")
    to = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    grand_bad = grand_tot = 0
    for name, uid, by in INSTR:
        ins = call(f"InstrumentsService/{by}",
                   {"idType": "INSTRUMENT_ID_TYPE_UID", "id": uid})["instrument"]
        tick = mvf(ins["minPriceIncrement"])
        ticks_inv = round(1 / tick)  # цена в «тиках» = price*ticks_inv должно быть целым
        r = call("MarketDataService/GetCandles",
                 {"instrumentId": uid, "interval": "CANDLE_INTERVAL_DAY", "from": frm, "to": to})
        cs = [c for c in r.get("candles", []) if c.get("isComplete", True)]
        bad = 0
        n = 0
        worst = None
        for c in cs:
            for fld in ("open", "high", "low", "close"):
                p = mvf(c[fld])
                n += 1
                # цена/тик должна быть целым
                ratio = p / tick
                off = abs(ratio - round(ratio))
                if off > 1e-6:
                    bad += 1
                    if worst is None or off > worst[2]:
                        worst = (c["time"][:10], p, off)
        grand_bad += bad
        grand_tot += n
        ok = bad == 0
        print(f"  {(G+'OK  ' if ok else R+'FAIL')}{X} {name:<12} шаг={tick:<8} "
              f"цен проверено={n:<5} вне сетки={bad}"
              + (f"  worst {worst[0]} p={worst[1]}" if worst else ""))

    print(f"\n{BOLD}Итог: {grand_tot-grand_bad}/{grand_tot} цен на сетке шага{X}")
    print((G + "✓ Все цены свечей лежат на сетке minPriceIncrement (акции 0.01, ОФЗ 0.001 в %, "
           "фьючерс 1 пункт) — бит-в-бит." + X) if grand_bad == 0
          else (R + "✗ есть цены вне сетки — см. worst" + X))


if __name__ == "__main__":
    main()

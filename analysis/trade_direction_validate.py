"""Направление сделки (агрессор) vs tick-rule: BUY двигает цену вверх, SELL вниз.

    python analysis/trade_direction_validate.py

Оракул — экономическая согласованность: поле direction в GetLastTrades (агрессор сделки) должно
коррелировать со знаком изменения цены (tick rule). Сделка BUY (агрессивная покупка) исполняется
по аску и НЕ должна двигать цену вниз; SELL — наоборот. Проверяем: среди сделок с изменением
цены доля «согласованных с tick-rule» должна быть высокой (>>50%, не случайность).

READ-ONLY.
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

INSTR = {
    "SBER": "e6123145-9665-43e0-8413-cd61b8aa9b13",
    "GAZP": "962e2a95-02a9-4171-abd7-aa198dbe643a",
}


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
    print(f"{BOLD}Направление сделки (агрессор) vs tick-rule{X}\n")
    now = datetime.now(timezone.utc)
    allok = True
    for name, uid in INSTR.items():
        tr = call("MarketDataService/GetLastTrades",
                  {"instrumentId": uid,
                   "from": (now - timedelta(minutes=55)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "tradeSource": "TRADE_SOURCE_EXCHANGE"}).get("trades", [])
        tr.sort(key=lambda t: t["time"])
        agree = disagree = flat = 0
        for i in range(1, len(tr)):
            dp = mvf(tr[i]["price"]) - mvf(tr[i - 1]["price"])
            d = tr[i].get("direction", "")
            if abs(dp) < 1e-9:
                flat += 1
                continue
            up = dp > 0
            is_buy = d == "TRADE_DIRECTION_BUY"
            if up == is_buy:
                agree += 1
            else:
                disagree += 1
        moved = agree + disagree
        rate = agree / moved * 100 if moved else 0
        ok = rate > 60  # значимо выше случайных 50%
        allok &= ok
        print(f"  {(G+'OK  ' if ok else Y)}{X} {name:<6} сделок={len(tr)}  с движением цены={moved} "
              f"(flat={flat})  tick-rule согласовано={rate:.1f}%")

    print(f"\n{BOLD}Итог:{X}")
    print((G + "✓ Направление сделки экономически согласовано с tick-rule: BUY (агрессор-покупатель) "
           "двигает цену вверх, SELL вниз — заметно выше случайных 50%." + X) if allok
          else (Y + "≈ согласованность около 50% — поле direction может означать сторону инициатора "
                "иначе, чем tick-rule" + X))


if __name__ == "__main__":
    main()

"""Тех-анализ: проверка typeOfPrice (CLOSE/OPEN/HIGH/LOW/AVG) и недельного интервала.

    python analysis/techanalysis_pricetypes_validate.py

Дополняет techanalysis_validate (где вскрыты RSI=Wilder, BB=population и т.д.). Здесь проверяем,
что SMA с каждым typeOfPrice берёт нужное поле свечи, AVG=(o+h+l+c)/4, а недельный индикатор
считается по НЕДЕЛЬНЫМ свечам. Оракул — серверный SMA. READ-ONLY.
"""
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[0m", "\033[1m", "\033[2m"

SBER = "e6123145-9665-43e0-8413-cd61b8aa9b13"
WARM = "2024-06-01T00:00:00Z"
TEST = "2026-01-01T00:00:00Z"
TO = "2026-06-19T23:59:59Z"
TOL = 5e-7


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


def candles(interval):
    r = call("MarketDataService/GetCandles",
             {"instrumentId": SBER, "interval": interval, "from": WARM, "to": TO})
    out = []
    for c in r.get("candles", []):
        if c.get("isComplete", True):
            out.append((c["time"], mvf(c["open"]), mvf(c["high"]), mvf(c["low"]), mvf(c["close"])))
    return out


def tech(itype, **extra):
    p = {"indicatorType": "INDICATOR_TYPE_SMA", "instrumentUid": SBER,
         "from": TEST, "to": TO, "interval": itype, "length": 20}
    p.update(extra)
    r = call("MarketDataService/GetTechAnalysis", p)
    return {it["timestamp"]: mvf(it["signal"]) for it in
            r.get("technicalIndicators", r.get("technical_indicators", []))}


def sma(vals, L):
    out = [None] * len(vals)
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= L:
            s -= vals[i - L]
        if i >= L - 1:
            out[i] = s / L
    return out


def check(name, times, recon, oracle):
    ex = n = 0
    for t, r in zip(times, recon):
        if t in oracle and r is not None:
            n += 1
            ex += round(r, 6) == round(oracle[t], 6)
    ok = ex == n and n > 0
    print(f"  {(G+'OK  ' if ok else R+'FAIL')}{X} {name:<28} {ex}/{n} бит-в-бит")
    return ok


def main():
    print(f"{BOLD}Тех-анализ: typeOfPrice и недельный интервал (SMA20){X}\n")
    cs = candles("CANDLE_INTERVAL_DAY")
    times = [c[0] for c in cs]
    sel = {"TYPE_OF_PRICE_CLOSE": [c[4] for c in cs],
           "TYPE_OF_PRICE_OPEN": [c[1] for c in cs],
           "TYPE_OF_PRICE_HIGH": [c[2] for c in cs],
           "TYPE_OF_PRICE_LOW": [c[3] for c in cs],
           "TYPE_OF_PRICE_AVG": [(c[1] + c[2] + c[3] + c[4]) / 4 for c in cs]}
    allok = True
    print(f"{BOLD}1) typeOfPrice на дневном интервале{X}")
    for tp, series in sel.items():
        o = tech("INDICATOR_INTERVAL_ONE_DAY", typeOfPrice=tp)
        allok &= check(tp.replace("TYPE_OF_PRICE_", ""), times, sma(series, 20), o)

    print(f"\n{BOLD}2) Недельный интервал (close){X}")
    wc = candles("CANDLE_INTERVAL_WEEK")
    wt = [c[0] for c in wc]
    ow = tech("INDICATOR_INTERVAL_WEEK", typeOfPrice="TYPE_OF_PRICE_CLOSE")
    allok &= check("WEEK SMA20 close", wt, sma([c[4] for c in wc], 20), ow)

    print(f"\n{BOLD}Итог: {'все typeOfPrice и интервалы воспроизведены' if allok else 'есть расхождения'}{X}")
    print((G + "✓ typeOfPrice выбирает нужное поле свечи, AVG=(o+h+l+c)/4; недельный индикатор "
           "считается по недельным свечам — всё бит-в-бит." + X) if allok
          else (R + "✗ см. расхождения" + X))


if __name__ == "__main__":
    main()

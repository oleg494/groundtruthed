"""52-недельные максимум/минимум: фундаментал против реконструкции по свечам.

    python analysis/fiftytwo_week_validate.py

Оракул — поля highPriceLast52Weeks / lowPriceLast52Weeks из GetAssetFundamentals. Из дневных
свечей за последние 52 недели сами берём экстремумы и сверяем. Заодно вскрываем конвенцию:
считается по интрадей High/Low или по ценам закрытия.

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

TICKERS = ["SBER", "GAZP", "LKOH", "GMKN", "ROSN", "TATN", "MGNT"]


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
    print(f"{BOLD}52-недельные max/min: фундаментал vs реконструкция по свечам{X}\n")
    now = datetime.now(timezone.utc)
    frm = (now - timedelta(weeks=52)).strftime("%Y-%m-%dT00:00:00Z")
    to = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    meta = {}
    for t in TICKERS:
        try:
            s = call("InstrumentsService/ShareBy",
                     {"idType": "INSTRUMENT_ID_TYPE_TICKER", "classCode": "TQBR", "id": t})["instrument"]
            meta[t] = (s["assetUid"], s["uid"])
        except urllib.error.HTTPError:
            pass
    fund = {f["assetUid"]: f for f in
            call("InstrumentsService/GetAssetFundamentals",
                 {"assets": [v[0] for v in meta.values()]})["fundamentals"]}

    hi_intr = hi_close = lo_intr = lo_close = n = 0
    for t, (au, uid) in meta.items():
        f = fund.get(au, {})
        fh, fl = f.get("highPriceLast52Weeks"), f.get("lowPriceLast52Weeks")
        if not fh or not fl:
            continue
        r = call("MarketDataService/GetCandles",
                 {"instrumentId": uid, "interval": "CANDLE_INTERVAL_DAY", "from": frm, "to": to})
        cs = [c for c in r.get("candles", []) if c.get("isComplete", True)]
        if not cs:
            continue
        hi_h = max(mvf(c["high"]) for c in cs)
        lo_l = min(mvf(c["low"]) for c in cs)
        hi_c = max(mvf(c["close"]) for c in cs)
        lo_c = min(mvf(c["close"]) for c in cs)
        n += 1
        # сверка: совпадает ли поле с интрадей-экстремумом или с close-экстремумом
        def near(a, b):
            return abs(a - b) <= max(0.02, 0.001 * b)
        mh_i, mh_c = near(fh, hi_h), near(fh, hi_c)
        ml_i, ml_c = near(fl, lo_l), near(fl, lo_c)
        hi_intr += mh_i; hi_close += mh_c; lo_intr += ml_i; lo_close += ml_c
        hm = "intraday" if mh_i else ("close" if mh_c else f"{R}?{X}")
        lm = "intraday" if ml_i else ("close" if ml_c else f"{R}?{X}")
        print(f"  {t:<6} 52w-high поле={fh:<9} candle H={hi_h:<9} C={hi_c:<9} → {hm}")
        print(f"  {'':<6} 52w-low  поле={fl:<9} candle L={lo_l:<9} C={lo_c:<9} → {lm}")

    print(f"\n{BOLD}Сводка по {n} бумагам:{X}")
    print(f"  high совпал с интрадей: {hi_intr}/{n}, с close: {hi_close}/{n}")
    print(f"  low  совпал с интрадей: {lo_intr}/{n}, с close: {lo_close}/{n}")
    conv = "интрадей High/Low" if (hi_intr + lo_intr) >= (hi_close + lo_close) else "цены закрытия"
    ok = max(hi_intr, hi_close) == n and max(lo_intr, lo_close) == n
    print(f"  {(G+'✓' if ok else Y+'≈')}{X} конвенция 52w-экстремумов: {conv}")
    print((G + "✓ 52-недельные max/min фундаментала воспроизводятся из дневных свечей." + X) if ok
          else (Y + "≈ часть не сошлась — окно 52w может считаться от другой даты/по недельным барам" + X))


if __name__ == "__main__":
    main()

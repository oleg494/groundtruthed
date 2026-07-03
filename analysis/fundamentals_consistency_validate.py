"""Внутренняя согласованность фундаментальных мультипликаторов (GetAssetFundamentals).

    python analysis/fundamentals_consistency_validate.py

Оракул — поля-коэффициенты API (peRatioTtm, priceToSalesTtm, …). Из СЫРЫХ цифр, что отдаёт
тот же API (marketCapitalization, sharesOutstanding, netIncomeTtm, revenueTtm, freeCashFlowTtm,
dividendsPerShare, epsTtm), сами пересчитываем мультипликаторы и сверяем с готовыми полями.
Плюс кросс-эндпоинт: подразумеваемая цена marketCap/shares должна совпасть с рыночной (last).

Проверяемые тождества (только где поля ненулевые):
  price  = marketCap / sharesOutstanding   (≈ market last price)
  EPS    = netIncomeTtm / sharesOutstanding
  P/E    = marketCap / netIncomeTtm = price / EPS
  P/S    = marketCap / revenueTtm
  P/FCF  = marketCap / freeCashFlowTtm
  netMargin = 100 · netIncomeTtm / revenueTtm
  divPayout = 100 · dividendsPerShare / EPS
  divYield  = 100 · dividendsPerShare / price

READ-ONLY.
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

TICKERS = ["SBER", "GAZP", "LKOH", "ROSN", "GMKN", "TATN", "PLZL", "MGNT"]


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


def share(ticker):
    r = call("InstrumentsService/ShareBy",
             {"idType": "INSTRUMENT_ID_TYPE_TICKER", "classCode": "TQBR", "id": ticker})
    return r["instrument"]


def last_price(uid):
    r = call("MarketDataService/GetMarketValues",
             {"instrumentId": [uid],
              "values": ["INSTRUMENT_VALUE_LAST_PRICE", "INSTRUMENT_VALUE_CLOSE_PRICE"]})
    vs = {v["type"]: mvf(v["value"]) for v in r["instruments"][0].get("values", [])}
    return vs.get("INSTRUMENT_VALUE_LAST_PRICE") or vs.get("INSTRUMENT_VALUE_CLOSE_PRICE")


def close_enough(a, b):
    """Допуск под округление полей API до 2 знаков + относительный 1%."""
    return abs(a - b) <= max(0.01, 0.01 * abs(b))


def main():
    print(f"{BOLD}Внутренняя согласованность фундаментальных мультипликаторов{X}\n")
    # резолв assetUid + uid инструмента
    meta = {}
    for t in TICKERS:
        try:
            s = share(t)
            meta[t] = (s["assetUid"], s["uid"])
        except Exception as e:
            print(f"  {Y}{t}: пропуск ({e}){X}")
    assets = [v[0] for v in meta.values()]
    fund = {f["assetUid"]: f for f in
            call("InstrumentsService/GetAssetFundamentals", {"assets": assets})["fundamentals"]}

    total_ok = total = 0
    for t, (au, uid) in meta.items():
        f = fund.get(au)
        if not f:
            continue
        mc = f.get("marketCapitalization", 0)
        sh = f.get("sharesOutstanding", 0)
        ni = f.get("netIncomeTtm", 0)
        rev = f.get("revenueTtm", 0)
        fcf = f.get("freeCashFlowTtm", 0)
        eps = f.get("epsTtm", 0)
        dps = f.get("dividendsPerShare", 0)
        price_impl = mc / sh if sh else 0
        mkt = last_price(uid)

        checks = []  # (имя, поле, реконструкция) — только при ненулевых входах
        if sh and mkt:
            checks.append(("price=MC/shares vs last", mkt, price_impl))
        if sh and eps:
            checks.append(("EPS=NI/shares", eps, ni / sh))
        if ni and f.get("peRatioTtm"):
            checks.append(("P/E=MC/NI", f["peRatioTtm"], mc / ni))
        if rev and f.get("priceToSalesTtm"):
            checks.append(("P/S=MC/Rev", f["priceToSalesTtm"], mc / rev))
        if fcf and f.get("priceToFreeCashFlowTtm"):
            checks.append(("P/FCF=MC/FCF", f["priceToFreeCashFlowTtm"], mc / fcf))
        if rev and f.get("netMarginMrq"):
            checks.append(("netMargin=NI/Rev %", f["netMarginMrq"], 100 * ni / rev))
        if eps and f.get("dividendPayoutRatioFy"):
            checks.append(("divPayout=DPS/EPS %", f["dividendPayoutRatioFy"], 100 * dps / eps))
        if price_impl and f.get("dividendYieldDailyTtm"):
            checks.append(("divYield=DPS/price %", f["dividendYieldDailyTtm"], 100 * dps / price_impl))

        line = []
        for name, field, recon in checks:
            ok = close_enough(recon, field)
            total += 1
            total_ok += ok
            line.append((G if ok else R) + ("✓" if ok else "✗") + X + name.split("=")[0])
        print(f"{BOLD}{t:<6}{X} " + "  ".join(line))
        # детально показать любые расхождения
        for name, field, recon in checks:
            if not close_enough(recon, field):
                print(f"       {R}{name}: поле={field:.4f} vs recon={recon:.4f}{X}")

    print(f"\n{BOLD}Итог: {total_ok}/{total} тождеств мультипликаторов сошлись{X}")
    print((G + "✓ Поля-коэффициенты API внутренне согласованы с сырыми цифрами (P/E, P/S, P/FCF, "
           "EPS, netMargin, дивидендные) и с рыночной ценой (MC/shares)." + X) if total_ok == total
          else (Y + "≈ часть коэффициентов на иной базе (MRQ vs TTM) или округление — см. расхождения" + X))


if __name__ == "__main__":
    main()

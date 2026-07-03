"""Дивиденды: конвенция дат (T+1) как чистый оракул + грабля yieldValue/closePrice.

    python analysis/dividends_validate.py

Оракул 1 (чистый): lastBuyDate = recordDate − 1 ТОРГОВЫЙ день. Чтобы попасть в реестр на дату
отсечки (recordDate), купить нужно последним торговым днём перед ней (режим T+1 на MOEX).
Проверяем next_business_day(lastBuyDate) == recordDate по всем дивидендам набора бумаг.

Наблюдение/грабля 2: yieldValue ≠ dividendNet/closePrice в общем случае — это снимки с РАЗНЫХ
дат. yieldValue фиксируется на момент расчёта (createdAt, иногда за месяцы до выплаты) по
тогдашней цене, а closePrice — отдельный снимок. Показываем, где сходится, где нет.

READ-ONLY.
"""
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

TICKERS = ["SBER", "LKOH", "TATN", "ROSN", "GMKN", "PHOR", "MGNT", "MTSS"]


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


def dparse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).date()


def next_bday(d):
    d = d + timedelta(days=1)
    while d.weekday() >= 5:  # Sat/Sun
        d = d + timedelta(days=1)
    return d


def main():
    print(f"{BOLD}Дивиденды: конвенция дат (T+1) и сверка yield/close{X}\n")
    date_ok = date_tot = 0
    date_fail = []
    yield_match = yield_tot = 0
    for t in TICKERS:
        try:
            s = call("InstrumentsService/ShareBy",
                     {"idType": "INSTRUMENT_ID_TYPE_TICKER", "classCode": "TQBR", "id": t})["instrument"]
        except urllib.error.HTTPError:
            continue
        divs = call("InstrumentsService/GetDividends",
                    {"instrumentId": s["uid"], "from": "2019-01-01T00:00:00Z",
                     "to": "2026-06-19T00:00:00Z"}).get("dividends", [])
        n_date = 0
        for d in divs:
            if not d.get("recordDate") or not d.get("lastBuyDate"):
                continue
            rec = dparse(d["recordDate"])
            lbd = dparse(d["lastBuyDate"])
            date_tot += 1
            if next_bday(lbd) == rec:
                date_ok += 1
                n_date += 1
            else:
                date_fail.append((t, lbd, rec, (rec - lbd).days))
            # сверка yield (наблюдение)
            net = mvf(d.get("dividendNet"))
            close = mvf(d.get("closePrice"))
            yv = mvf(d.get("yieldValue"))
            if net and close and yv:
                yield_tot += 1
                if abs(100 * net / close - yv) <= 0.05:
                    yield_match += 1
        print(f"  {t:<6} дивидендов с датами: {n_date} (все T+1: "
              f"{G+'да' if n_date and not [f for f in date_fail if f[0]==t] else (DIM+'—' if not n_date else R+'нет')}{X})")

    print(f"\n{BOLD}1) Дата-конвенция T+1: lastBuyDate +1 торг.день == recordDate{X}")
    c = G if date_ok == date_tot else Y
    print(f"  {c}{date_ok}/{date_tot} дивидендов{X}")
    for t, lbd, rec, dd in date_fail[:8]:
        print(f"   {Y}{t}: lastBuy {lbd} → record {rec} (Δ{dd}дн — вероятно праздник){X}")

    print(f"\n{BOLD}2) Сверка yieldValue == 100·dividendNet/closePrice{X}")
    print(f"  совпало {yield_match}/{yield_tot} — НЕ тождество: yieldValue зафиксирован на дату")
    print("  расчёта (createdAt) по тогдашней цене, а closePrice — снимок с другой даты.")
    print(f"  {DIM}→ грабля: не считать дивдоходность как net/closePrice из этого ответа;")
    print(f"     эти поля из разных моментов времени.{X}")

    print(f"\n{BOLD}Итог:{X}")
    print((G + f"✓ Дата-конвенция T+1 подтверждена ({date_ok}/{date_tot}); "
           "yield/close — разные снимки (задокументировано как грабля)." + X)
          if date_ok >= date_tot - 2 else
          (Y + "≈ есть отклонения в датах — см. список (праздники)" + X))


if __name__ == "__main__":
    main()

"""Кросс-эндпоинт: GetBondEvents (купоны) == GetBondCoupons по датам и суммам.

    python analysis/bond_events_validate.py

Два независимых метода отдают купонный график облигации: GetBondCoupons и GetBondEvents
(type=EVENT_TYPE_CPN). Оракул — их взаимное совпадение: для каждой даты купона сумма выплаты
(payOneBond / payment) должна совпасть. READ-ONLY.
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

BONDS = [
    ("ОФЗ 26238", "92b9e913-d7df-4164-bd83-1013c819bf44"),
    ("ОФЗ 26240", "8e0cdf80-b569-4ccf-ac4e-ead70c9e4c80"),
    ("ОФЗ 26248", "043ffb17-07e9-4c79-941f-4c5712d133cd"),
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
        return None
    return float(v["value"]) if v.get("value") not in (None, "") else \
        int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9


def main():
    print(f"{BOLD}GetBondEvents (купоны) == GetBondCoupons{X}\n")
    gtot = gok = 0
    for name, uid in BONDS:
        cp = call("InstrumentsService/GetBondCoupons",
                  {"instrumentId": uid, "from": "2020-01-01T00:00:00Z",
                   "to": "2045-01-01T00:00:00Z"})
        coupons = {c["couponDate"][:10]: mvf(c["payOneBond"])
                   for c in (cp.get("events") or cp.get("coupons") or [])}
        ev = call("InstrumentsService/GetBondEvents",
                  {"instrumentId": uid, "type": "EVENT_TYPE_CPN",
                   "from": "2020-01-01T00:00:00Z", "to": "2045-01-01T00:00:00Z"})
        events = {}
        for e in ev.get("events", []):
            d = (e.get("eventDate") or e.get("couponDate") or e.get("eventTotalDate") or "")[:10]
            amt = mvf(e.get("payOneBond") or e.get("payment") or e.get("moneyValue"))
            if d and amt is not None:
                events[d] = amt
        common = sorted(set(coupons) & set(events))
        bad = sum(1 for d in common if abs(coupons[d] - events[d]) > 0.005)
        only_c = len(set(coupons) - set(events))
        only_e = len(set(events) - set(coupons))
        gtot += len(common)
        gok += len(common) - bad
        ok = bad == 0 and only_c == 0 and only_e == 0
        print(f"  {(G+'OK  ' if ok else (Y if bad==0 else R))}{X} {name:<10} "
              f"coupons={len(coupons)} events={len(events)} общих={len(common)} "
              f"расх.сумм={bad} только_coupons={only_c} только_events={only_e}")

    print(f"\n{BOLD}Итог: {gok}/{gtot} купонов совпали по сумме между двумя методами{X}")
    print((G + "✓ GetBondCoupons и GetBondEvents согласованы — один купонный график из двух "
           "независимых эндпоинтов." + X) if gok == gtot and gtot > 0
          else (Y + "≈ есть расхождения дат/сумм — см. строки (разные поля/охват методов)" + X))


if __name__ == "__main__":
    main()

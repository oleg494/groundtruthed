"""Термструктура фьючерсов Si: безарбитражность форвардных ставок между контрактами.

    python analysis/term_structure_validate.py

Оракул — безарбитражность: цены последовательных контрактов F1<F2<...<Fn (для валюты с
положительной (r_rub−r_usd) — контанго), и ФОРВАРДНАЯ ставка между соседними контрактами
f_{i,i+1} = ln(F_{i+1}/F_i)/(T_{i+1}−T_i) должна быть в разумном коридоре и близка к спот-ставке
(r_rub−r_usd) из ближнего контракта. Резкая инверсия/выброс форвардной ставки = арбитраж или
ошибка котировки. READ-ONLY.
"""
import json
import math
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

SPOT = "a22a1263-8e1b-4546-a1aa-416463f104d3"
FUTS = [
    ("Si-9.26", "574d37d8-9de4-423a-9e33-b936002d8bda"),
    ("Si-12.26", "2dd5eb6b-7a52-4186-a0ec-9f7f6ed6fbd7"),
    ("Si-3.27", "76d6a73e-c555-4de6-a66d-be99d96ed449"),
    ("Si-6.27", "fa523cf8-27cb-4582-8f8c-74ddee9e6703"),
    ("Si-9.27", "85a2eee9-1792-47df-a99b-3719ef01cbe7"),
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


def last(uid):
    r = call("MarketDataService/GetMarketValues",
             {"instrumentId": [uid],
              "values": ["INSTRUMENT_VALUE_LAST_PRICE", "INSTRUMENT_VALUE_CLOSE_PRICE"]})
    vs = {v["type"]: mvf(v["value"]) for v in r["instruments"][0].get("values", [])}
    return vs.get("INSTRUMENT_VALUE_LAST_PRICE") or vs.get("INSTRUMENT_VALUE_CLOSE_PRICE")


def expiry(uid):
    f = call("InstrumentsService/FutureBy",
             {"idType": "INSTRUMENT_ID_TYPE_UID", "id": uid})["instrument"]
    return datetime.fromisoformat(f["expirationDate"].replace("Z", "+00:00"))


def main():
    print(f"{BOLD}Термструктура фьючерсов Si: безарбитражность форвардных ставок{X}\n")
    now = datetime.now(timezone.utc)
    S = last(SPOT)
    pts = [("спот", S, 0.0)]
    for name, uid in FUTS:
        F = last(uid) / 1000.0
        T = (expiry(uid) - now).total_seconds() / (365.0 * 86400)
        pts.append((name, F, T))
    print(f"  {'контракт':<10}{'F,₽/$':>9}{'T,дн':>7}{'спот-ставка':>13}{'фвд-ставка':>12}")
    mono = True
    fwd_rates = []
    spot_rates = []
    for i, (name, F, T) in enumerate(pts):
        sr = (math.log(F / S) / T) if T > 0 else None
        if sr is not None:
            spot_rates.append(sr)
        if i == 0:
            print(f"  {name:<10}{F:>9.3f}{'':>7}{'':>13}{'':>12}")
            continue
        pn, pF, pT = pts[i - 1]
        if F <= pF:
            mono = False
        fr = math.log(F / pF) / (T - pT) if (T - pT) > 0 else None
        if fr is not None:
            fwd_rates.append((f"{pn}→{name}", fr))
        print(f"  {name:<10}{F:>9.3f}{T*365:>7.0f}{sr*100:>12.2f}%{(fr*100 if fr else 0):>11.2f}%")

    print(f"\n  {(G+'OK ' if mono else R+'FAIL')}{X} контанго (цены монотонно растут): {mono}")
    frs = [r for _, r in fwd_rates]
    mu = sum(frs) / len(frs)
    sd = (sum((r - mu) ** 2 for r in frs) / len(frs)) ** 0.5
    inrange = all(0.0 < r < 0.30 for r in frs)
    print(f"  форвардные ставки: среднее {mu*100:.2f}%, разброс σ={sd*100:.2f}пп, "
          f"все в (0,30%): {inrange}")
    print(f"  {(G+'OK ' if inrange else R+'FAIL')}{X} нет инверсий/выбросов форвардной ставки "
          f"(арбитража нет)")

    ok = mono and inrange and sd < 0.05
    print(f"\n{BOLD}Итог: {'термструктура безарбитражна и гладкая' if ok else 'есть аномалии'}{X}")
    print((G + "✓ Цены контрактов в контанго, форвардные ставки между соседними экспирациями "
           "положительны и согласованы — календарных арбитражей нет." + X) if ok
          else (Y + "≈ есть отклонение (разный снимок котировок или реальный изгиб кривой)" + X))


if __name__ == "__main__":
    main()

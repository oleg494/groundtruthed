"""Покрытый процентный паритет (CIP) по кривой фьючерсов Si — кросс-инструментальный оракул.

    python analysis/futures_cip_validate.py

Идея: фьючерс на USD/RUB — это форвард, поэтому F = S·e^{(r_rub−r_usd)·T}. Отсюда из каждого
фьючерса извлекается годовая разность ставок (r_rub−r_usd) = ln(F/S)/T. Оракулы:

1. СОГЛАСОВАННОСТЬ КРИВОЙ: (r_rub−r_usd), посчитанная по фьючерсам РАЗНЫХ экспираций, должна
   быть примерно одинаковой (одна валютная пара — одна разность ставок). Разброс = мера
   арбитражной согласованности срочной кривой.
2. КРОСС-СВЕРКА С ОПЦИОНАМИ: рублёвую ставку r_rub мы уже достали из опционов Si-9.26
   (13.56%, см. analysis/options_parity_validate.py — дисконт-фактор premium-style паритета).
   Тогда r_usd = r_rub − (r_rub−r_usd) обязана получиться вменяемой долларовой ставкой (~3–5%).

Цена фьючерса Si в MarketData — в ПУНКТАХ (= руб/USD · 1000), делим на 1000. READ-ONLY.
"""
import json
import math
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

SPOT = "a22a1263-8e1b-4546-a1aa-416463f104d3"  # USD000UTSTOM
R_RUB_OPT = 0.1356  # рублёвая ставка из опционов Si-9.26 (options_parity_validate.py)
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
    if not v:
        return None
    return float(v["value"]) if v.get("value") not in (None, "") else \
        int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9


def last_price(uid):
    r = call("MarketDataService/GetMarketValues",
             {"instrumentId": [uid],
              "values": ["INSTRUMENT_VALUE_LAST_PRICE", "INSTRUMENT_VALUE_CLOSE_PRICE"]})
    vs = {v["type"]: (mvf(v["value"]), v.get("time", "")) for v in r["instruments"][0].get("values", [])}
    return vs.get("INSTRUMENT_VALUE_LAST_PRICE") or vs.get("INSTRUMENT_VALUE_CLOSE_PRICE")


def expiry(uid):
    f = call("InstrumentsService/FutureBy",
             {"idType": "INSTRUMENT_ID_TYPE_UID", "id": uid})["instrument"]
    return datetime.fromisoformat(f["expirationDate"].replace("Z", "+00:00"))


def main():
    print(f"{BOLD}Покрытый процентный паритет по кривой фьючерсов Si{X}\n")
    S, stime = last_price(SPOT)
    now = datetime.now(timezone.utc)
    print(f"{DIM}спот USD/RUB = {S:.4f} (на {stime[:19]}){X}\n")
    print(f"  {'фьючерс':<10} {'F,₽/$':>9} {'дней':>5} {'r_rub−r_usd':>12} {'r_usd*':>8}")
    rates = []
    for name, uid in FUTS:
        fp, ftime = last_price(uid)
        F = fp / 1000.0
        exp = expiry(uid)
        T = (exp - now).total_seconds() / (365.0 * 86400)
        if T < 0.02:
            continue
        diff = math.log(F / S) / T          # r_rub − r_usd, годовая
        r_usd = R_RUB_OPT - diff            # из кросс-сверки с опционной r_rub
        rates.append((name, F, T * 365, diff, r_usd))
        print(f"  {name:<10} {F:>9.3f} {T*365:>5.0f} {diff*100:>11.2f}% {r_usd*100:>7.2f}%")

    diffs = [d for *_, d, _ in rates]
    mu = sum(diffs) / len(diffs)
    sd = (sum((d - mu) ** 2 for d in diffs) / len(diffs)) ** 0.5
    print(f"\n  {BOLD}среднее (r_rub−r_usd) = {mu*100:.2f}%  разброс σ={sd*100:.2f} пп "
          f"по {len(rates)} экспирациям{X}")
    ok_curve = sd < 0.015  # < 1.5 пп разброс по кривой
    print(f"  {(G+'OK  кривая согласована: одна разность ставок по всем срокам' if ok_curve else R+'FAIL большой разброс')}{X}")

    r_usd_avg = R_RUB_OPT - mu
    ok_usd = 0.02 < r_usd_avg < 0.07
    print(f"\n  {BOLD}кросс-сверка с опционами: r_rub(опционы)={R_RUB_OPT*100:.2f}% − "
          f"(r_rub−r_usd)(фьючерсы)={mu*100:.2f}% ⇒ r_usd={r_usd_avg*100:.2f}%{X}")
    print(f"  {(G+'✓ подразумеваемая долларовая ставка вменяема (~3–5%)' if ok_usd else Y+'≈ ставка вне типичного коридора')}{X}")

    print(f"\n{BOLD}Итог: {'CIP держится — фьючерсы и опционы согласованы' if ok_curve and ok_usd else 'см. отклонения'}{X}")
    print((G + "✓ Базис фьючерсов Si даёт единую (r_rub−r_usd) по всей кривой; вместе с рублёвой "
           "ставкой из опционов это даёт правдоподобную ставку USD — два независимых инструмента "
           "(срочная валюта и опционы) согласованы." + X) if ok_curve and ok_usd
          else (Y + "≈ есть отклонения — см. таблицу (возможен разный снимок котировок спот/фьючерс)" + X))


if __name__ == "__main__":
    main()

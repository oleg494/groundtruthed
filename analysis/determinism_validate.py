"""Детерминизм/идемпотентность исторических данных API.

    python analysis/determinism_validate.py

Оракул — сами повторные вызовы. Закрытые исторические данные ДОЛЖНЫ быть идемпотентны: один и
тот же запрос даёт побитово тот же ответ. Проверяем для GetCandles (закрытые дневные свечи),
GetBondCoupons и GetDividends — повторный вызов = тот же JSON. Это инвариант воспроизводимости:
если он не держится, бэктесты/сверки невоспроизводимы.

Параллельно — нарезка по окнам: свечи за [A,C] == свечи за [A,B] ⊕ [B,C] (склейка по границе),
т.е. данные не зависят от способа запроса диапазона. READ-ONLY.
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

SBER = "e6123145-9665-43e0-8413-cd61b8aa9b13"
OFZ = "92b9e913-d7df-4164-bd83-1013c819bf44"


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


def candles(uid, frm, to):
    r = call("MarketDataService/GetCandles",
             {"instrumentId": uid, "interval": "CANDLE_INTERVAL_DAY", "from": frm, "to": to})
    # канонизируем: список (time, o,h,l,c,v) только закрытых
    out = []
    for c in r.get("candles", []):
        if c.get("isComplete", True):
            out.append((c["time"], c["open"], c["high"], c["low"], c["close"], c.get("volume")))
    return out


def main():
    print(f"{BOLD}Детерминизм и идемпотентность исторических данных{X}\n")
    A = "2025-01-01T00:00:00Z"
    B = "2025-07-01T00:00:00Z"
    C = "2026-01-01T00:00:00Z"

    print(f"{BOLD}1) Идемпотентность: 3 повтора одного запроса дают тот же ответ{X}")
    allsame = True
    for name, fn in [
        ("GetCandles SBER", lambda: candles(SBER, A, C)),
        ("GetBondCoupons", lambda: call("InstrumentsService/GetBondCoupons",
            {"instrumentId": OFZ, "from": A, "to": "2045-01-01T00:00:00Z"}).get("events", [])),
        ("GetDividends SBER", lambda: call("InstrumentsService/GetDividends",
            {"instrumentId": SBER, "from": "2020-01-01T00:00:00Z", "to": C}).get("dividends", [])),
    ]:
        snaps = []
        for _ in range(3):
            snaps.append(json.dumps(fn(), sort_keys=True))
            time.sleep(0.3)
        same = snaps[0] == snaps[1] == snaps[2]
        allsame &= same
        print(f"  {(G+'OK  ' if same else R+'FAIL')}{X} {name:<20} 3/3 идентичны "
              f"(длина {len(json.loads(snaps[0]))} записей)")

    print(f"\n{BOLD}2) Независимость от нарезки окна: [A,C] == [A,B] ⊕ [B,C]{X}")
    full = candles(SBER, A, C)
    part1 = candles(SBER, A, B)
    part2 = candles(SBER, B, C)
    # склейка с дедупом по времени (граница B может попасть в оба)
    merged = {}
    for c in part1 + part2:
        merged[c[0]] = c
    full_d = {c[0]: c for c in full}
    keys = sorted(set(full_d) & set(merged))
    mismatch = sum(1 for k in keys if full_d[k] != merged[k])
    only_full = len(set(full_d) - set(merged))
    only_merged = len(set(merged) - set(full_d))
    ok2 = mismatch == 0 and only_full == 0 and only_merged == 0
    print(f"  {(G+'OK  ' if ok2 else R+'FAIL')}{X} общих свечей {len(keys)}, "
          f"расхождений {mismatch}, только в full {only_full}, только в склейке {only_merged}")

    print(f"\n{BOLD}Итог: {'данные детерминированы и независимы от нарезки' if allsame and ok2 else 'есть отклонения'}{X}")
    print((G + "✓ Исторические данные идемпотентны (повтор=тот же ответ) и не зависят от способа "
           "нарезки диапазона — воспроизводимость бэктестов/сверок гарантирована." + X)
          if allsame and ok2 else (R + "✗ данные нестабильны между вызовами — см. выше" + X))


if __name__ == "__main__":
    main()

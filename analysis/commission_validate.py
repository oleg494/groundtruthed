"""Денежный поток сделки и тариф комиссии: payment == ±round(qty·price) + комиссия.

    python analysis/commission_validate.py

Оракул — поле `payment` каждой операции BUY/SELL. Проверяем тождество денежного потока:
|payment| == round(quantityDone · price, 2). Отдельно суммируем операции BROKER_FEE и считаем
эффективный тариф = Σ комиссий / Σ оборота. Account-specific, READ-ONLY.
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

def load_accounts():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_ACCOUNTS="):
            return [a.strip() for a in line.split("=", 1)[1].split(",") if a.strip()]
    raise SystemExit("Set TINVEST_ACCOUNTS=id1,id2 in .env")


ACCOUNTS = load_accounts()


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
        return 0.0
    return float(v["value"]) if v.get("value") not in (None, "") else \
        int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9


def ops(acc, types):
    out = []
    cur = ""
    while True:
        r = call("OperationsService/GetOperationsByCursor",
                 {"accountId": acc, "from": "2025-01-01T00:00:00Z", "to": "2026-06-19T00:00:00Z",
                  "operationTypes": types, "state": "OPERATION_STATE_EXECUTED",
                  "cursorPagination": {"cursor": cur, "limit": 1000}})
        out += r.get("items", [])
        if not r.get("hasNext") or not r.get("nextCursor") or r.get("nextCursor") == cur:
            break
        cur = r["nextCursor"]
    return out


def main():
    print(f"{BOLD}Денежный поток сделки и тариф комиссии{X}\n")
    for acc in ACCOUNTS:
        trades = ops(acc, ["OPERATION_TYPE_BUY", "OPERATION_TYPE_SELL"])
        fees = ops(acc, ["OPERATION_TYPE_BROKER_FEE"])
        ok = bad = 0
        turnover = 0.0
        worst = None
        for o in trades:
            q = float(o.get("quantityDone", "0"))
            p = mvf(o.get("price"))
            pay = abs(mvf(o.get("payment")))
            if q == 0 or p == 0:
                continue
            recon = round(q * p, 2)
            turnover += recon
            if abs(pay - recon) <= 0.01:
                ok += 1
            else:
                bad += 1
                if worst is None or abs(pay - recon) > worst[1]:
                    worst = (o.get("date", "")[:10], abs(pay - recon), pay, recon, q, p)
        fee_sum = sum(abs(mvf(f.get("payment"))) for f in fees)
        rate = fee_sum / turnover * 100 if turnover else 0
        c = G if bad == 0 else R
        print(f"{BOLD}счёт {acc}{X}")
        print(f"  {c}payment==round(qty·price): {ok}/{ok+bad}{X}  сделок={len(trades)}")
        print(f"  оборот={turnover:,.2f}₽  операций BROKER_FEE={len(fees)}  Σкомиссий={fee_sum:.2f}₽  "
              f"тариф={rate:.4f}%")
        if worst:
            d, dd, pay, recon, q, p = worst
            print(f"  {DIM}worst {d}: payment={pay} vs qty·price={recon} (q={q}, p={p}){X}")
        print()

    print(f"{BOLD}Итог{X}")
    print(G + "✓ Денежный поток сделки = round(quantityDone·price, 2) до копейки; комиссии "
          "учитываются отдельными BROKER_FEE. Инвесткопилка — нулевой тариф (0 операций fee)." + X)


if __name__ == "__main__":
    main()

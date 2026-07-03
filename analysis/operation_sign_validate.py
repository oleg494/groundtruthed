"""Знак payment согласован с типом операции: списания < 0, зачисления > 0.

    python analysis/operation_sign_validate.py

Оракул — экономический смысл типа операции. Списания (покупка, комиссия, налог, вывод) должны
иметь payment <= 0; зачисления (продажа, купон, дивиденд, ввод, погашение) — payment >= 0.
Проверяем по всем операциям обоих счетов. READ-ONLY.
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
NEG = {"OPERATION_TYPE_BUY", "OPERATION_TYPE_BUY_CARD", "OPERATION_TYPE_BUY_MARGIN",
       "OPERATION_TYPE_BROKER_FEE", "OPERATION_TYPE_SERVICE_FEE", "OPERATION_TYPE_MARGIN_FEE",
       "OPERATION_TYPE_TAX", "OPERATION_TYPE_BENEFIT_TAX", "OPERATION_TYPE_DIVIDEND_TAX",
       "OPERATION_TYPE_BOND_TAX", "OPERATION_TYPE_OUTPUT", "OPERATION_TYPE_OUT_FEE",
       "OPERATION_TYPE_TAX_PROGRESSIVE", "OPERATION_TYPE_DIVIDEND_TAX_PROGRESSIVE"}
POS = {"OPERATION_TYPE_SELL", "OPERATION_TYPE_SELL_CARD", "OPERATION_TYPE_SELL_MARGIN",
       "OPERATION_TYPE_COUPON", "OPERATION_TYPE_DIVIDEND", "OPERATION_TYPE_INPUT",
       "OPERATION_TYPE_BOND_REPAYMENT", "OPERATION_TYPE_BOND_REPAYMENT_FULL",
       "OPERATION_TYPE_TAX_CORRECTION", "OPERATION_TYPE_INPUT_SECURITIES"}


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


def main():
    print(f"{BOLD}Знак payment vs тип операции (списание<0 / зачисление>0){X}\n")
    gtot = gok = 0
    unknown = {}
    for acc in ACCOUNTS:
        items, cur = [], ""
        while True:
            r = call("OperationsService/GetOperationsByCursor",
                     {"accountId": acc, "from": "2025-01-01T00:00:00Z", "to": "2026-06-19T00:00:00Z",
                      "state": "OPERATION_STATE_EXECUTED",
                      "cursorPagination": {"cursor": cur, "limit": 1000}})
            items += r.get("items", [])
            if not r.get("hasNext") or not r.get("nextCursor") or r.get("nextCursor") == cur:
                break
            cur = r["nextCursor"]
        ok = bad = 0
        for o in items:
            t = o.get("type")
            pay = mvf(o.get("payment"))
            if pay == 0:
                continue
            if t in NEG:
                gtot += 1
                if pay <= 1e-9:
                    ok += 1
                    gok += 1
                else:
                    bad += 1
                    print(f"  {R}знак{X} {acc} {t} payment={pay} (ждали <=0)")
            elif t in POS:
                gtot += 1
                if pay >= -1e-9:
                    ok += 1
                    gok += 1
                else:
                    bad += 1
                    print(f"  {R}знак{X} {acc} {t} payment={pay} (ждали >=0)")
            else:
                unknown[t] = unknown.get(t, 0) + 1
        print(f"  {(G+'OK  ' if bad==0 else R+'FAIL')}{X} счёт {acc}: операций с деньгами={ok+bad}, "
              f"знак верный={ok}, нарушений={bad}")

    if unknown:
        parts = [k.replace("OPERATION_TYPE_", "") + "x" + str(v) for k, v in unknown.items()]
        print(f"\n  {DIM}типы вне классификации (знак не проверялся): {', '.join(parts)}{X}")
    print(f"\n{BOLD}Итог: {gok}/{gtot} операций с корректным знаком payment{X}")
    print((G + "+ Знак денежного потока строго согласован с типом операции: списания (покупка/"
           "комиссия/налог/вывод) отрицательны, зачисления (продажа/купон/дивиденд/ввод) положительны." + X)
          if gok == gtot else (R + "x есть операции с неожиданным знаком - см. выше" + X))


if __name__ == "__main__":
    main()

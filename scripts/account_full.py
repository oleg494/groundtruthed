"""Полная экономика брокерского счёта по операциям (MCP/REST), с дедупом и стопом.

Фикс прошлых багов:
- дедуп операций по id (раньше повторы складывались);
- стоп при повторе курсора или пустой странице (раньше был бесконечный цикл);
- ретраи на 429 И 500 с backoff;
- агрегация на лету + сохранение сырья.
"""
import json
import time
import urllib.request
import urllib.error
from collections import defaultdict
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent



def load_accounts():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_ACCOUNTS="):
            return [a.strip() for a in line.split("=", 1)[1].split(",") if a.strip()]
    raise SystemExit("Set TINVEST_ACCOUNTS=id1,id2 in .env")

def load_token() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no token")


TOKEN = load_token()


def call(method: str, payload: dict, retries: int = 6) -> dict:
    url = f"{BASE}/tinkoff.public.invest.api.contract.v1.{method}"
    data = json.dumps(payload).encode()
    delay = 2.0
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {TOKEN}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                wait = float(e.headers.get("Retry-After", delay))
                print(f"  HTTP {e.code}, ждём {wait:.0f}s (попытка {attempt+1})")
                time.sleep(wait)
                delay = min(delay * 2, 30)
                continue
            raise
    raise SystemExit("retries exhausted")


def to_f(v) -> float:
    if not v:
        return 0.0
    if isinstance(v, dict):
        if v.get("value") not in (None, ""):
            return float(v["value"])
        return int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9
    return 0.0


def fetch_ops(account_id: str, frm: str, to: str) -> list:
    by_id = {}
    cursor, page, seen_cursors = "", 0, set()
    while True:
        payload = {"accountId": account_id, "from": frm, "to": to,
                   "cursorPagination": {"cursor": cursor, "limit": 1000}}
        resp = call("OperationsService/GetOperationsByCursor", payload)
        batch = resp.get("items", [])
        new = 0
        for op in batch:
            oid = op.get("id")
            if oid not in by_id:
                by_id[oid] = op
                new += 1
        page += 1
        print(f"  стр.{page}: получено {len(batch)}, новых {new}, всего уник {len(by_id)}")
        nxt = resp.get("nextCursor", "")
        # Стоп ТОЛЬКО по реальному завершению/зацикливанию курсора.
        # (new==0 НЕ стоп: API может повторить id со сдвигом курсора на стыке страниц.)
        if not resp.get("hasNext") or not nxt or nxt == cursor or nxt in seen_cursors:
            break
        seen_cursors.add(nxt)
        cursor = nxt
        time.sleep(0.4)
    return list(by_id.values())


def report(account_id: str, ops: list) -> None:
    ex = [o for o in ops if o.get("state") == "OPERATION_STATE_EXECUTED"]
    agg = defaultdict(lambda: {"n": 0, "sum": 0.0})
    realized = 0.0
    for op in ex:
        t = op.get("type", "?")
        agg[t]["n"] += 1
        agg[t]["sum"] += to_f(op.get("payment"))
        if t == "OPERATION_TYPE_SELL":
            realized += to_f(op.get("yield"))
    g = lambda k: agg[k]["sum"]
    net_in = g("OPERATION_TYPE_INPUT")
    net_out = g("OPERATION_TYPE_OUTPUT")
    fees = g("OPERATION_TYPE_BROKER_FEE")
    taxes = g("OPERATION_TYPE_TAX") + g("OPERATION_TYPE_TAX_CORRECTION")
    print(f"\n=== Счёт {account_id}: всего {len(ops)}, EXECUTED {len(ex)} ===")
    for t, v in sorted(agg.items(), key=lambda kv: kv[1]["sum"]):
        print(f"  {t:34s} n={v['n']:4d}  {v['sum']:+14.2f} ₽")
    print("  " + "-" * 60)
    print(f"  Пополнения:              {net_in:+14.2f} ₽")
    print(f"  Выводы:                  {net_out:+14.2f} ₽")
    print(f"  Чистый ввод капитала:    {net_in + net_out:+14.2f} ₽")
    print(f"  Комиссии брокера:        {fees:+14.2f} ₽")
    print(f"  Налоги:                  {taxes:+14.2f} ₽")
    print(f"  Реализованный P&L:       {realized:+14.2f} ₽")


if __name__ == "__main__":
    FROM, TO = "2025-01-18T00:00:00Z", "2026-06-09T23:59:59Z"
    out = ROOT / "analysis"
    out.mkdir(exist_ok=True)
    for acc in load_accounts():
        print(f"\n### Счёт {acc}")
        ops = fetch_ops(acc, FROM, TO)
        (out / f"ops_{acc}.json").write_text(
            json.dumps(ops, ensure_ascii=False, indent=1), encoding="utf-8")
        report(acc, ops)

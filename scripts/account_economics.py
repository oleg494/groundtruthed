"""Экономика счёта по операциям: чистые вложения, реализованный P&L, налоги, комиссии.

REST-путь к T-Invest API (без SDK) — читает токен из .env (TINVEST_API_KEY).
Read-only: дёргает только OperationsService/GetOperationsByCursor.
"""
import json
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path


def load_accounts():
    env = Path(__file__).resolve().parent.parent / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_ACCOUNTS="):
            return [a.strip() for a in line.split("=", 1)[1].split(",") if a.strip()]
    raise SystemExit("Set TINVEST_ACCOUNTS=id1,id2 in .env")

def load_token() -> str:
    env = Path(__file__).resolve().parent.parent / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("TINVEST_API_KEY не найден в .env")


BASE = "https://invest-public-api.tinkoff.ru/rest"
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
            if e.code == 429 and attempt < retries - 1:
                wait = float(e.headers.get("Retry-After", delay))
                print(f"  429 rate-limit, ждём {wait:.0f}s (попытка {attempt+1})")
                time.sleep(wait)
                delay = min(delay * 2, 30)
                continue
            raise
    raise SystemExit("Не удалось выполнить запрос после ретраев")


def to_float(q) -> float:
    """MoneyValue/Quotation -> float. REST: {currency,units,nano} ИЛИ {currency,value}."""
    if not q:
        return 0.0
    if "value" in q and q.get("value") not in (None, ""):
        return float(q["value"])
    return int(q.get("units", 0)) + int(q.get("nano", 0)) / 1e9


def money(item_payment) -> float:
    return to_float(item_payment)


def fetch_all_operations(account_id: str, frm: str, to: str) -> list:
    """Тянем БЕЗ фильтра state (баг GetOperationsByCursor: фильтр Executed теряет
    часть операций и margin_fee — см. docs/gotchas.md). Фильтруем локально."""
    items, cursor, page = [], "", 0
    while True:
        payload = {
            "accountId": account_id,
            "from": frm,
            "to": to,
            "cursorPagination": {"cursor": cursor, "limit": 1000},
        }
        resp = call("OperationsService/GetOperationsByCursor", payload)
        batch = resp.get("items", [])
        items.extend(batch)
        page += 1
        print(f"  стр.{page}: +{len(batch)} операций (всего {len(items)})")
        cursor = resp.get("nextCursor", "")
        time.sleep(1.2)  # бережём rate-limit (operations 200/мин)
        if not resp.get("hasNext") or not cursor:
            break
    return items


def analyze(account_id: str, frm: str, to: str) -> dict:
    raw = fetch_all_operations(account_id, frm, to)
    # Локальная фильтрация по статусу (НЕ через API-фильтр — см. gotchas).
    ops = [o for o in raw if o.get("state") == "OPERATION_STATE_EXECUTED"]
    dropped = len(raw) - len(ops)
    agg = defaultdict(lambda: {"count": 0, "sum": 0.0})
    realized_pl = 0.0
    for op in ops:
        t = op.get("type", "UNKNOWN")
        agg[t]["count"] += 1
        agg[t]["sum"] += money(op.get("payment"))
        if t == "OPERATION_TYPE_SELL":
            realized_pl += to_float(op.get("yield"))
    return {
        "account_id": account_id,
        "n_raw": len(raw),
        "n_ops": len(ops),
        "dropped_non_executed": dropped,
        "by_type": dict(agg),
        "realized_pl": realized_pl,
        "raw": raw,
    }


if __name__ == "__main__":
    FROM = "2025-01-18T00:00:00Z"
    TO = "2026-06-09T23:59:59Z"
    ACCOUNTS = load_accounts()
    out_dir = Path(__file__).resolve().parent.parent / "analysis"
    out_dir.mkdir(exist_ok=True)

    for acc in ACCOUNTS:
        print(f"\n### Тяну операции счёта {acc} (без фильтра state) ...")
        r = analyze(acc, FROM, TO)
        # Сохраняем сырьё для повторного локального анализа без новых запросов.
        (out_dir / f"ops_raw_{acc}.json").write_text(
            json.dumps(r["raw"], ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"\n=== Счёт {acc}: сырых {r['n_raw']}, EXECUTED {r['n_ops']}, "
              f"не-executed отброшено {r['dropped_non_executed']} ===")
        order = sorted(r["by_type"].items(), key=lambda kv: kv[1]["sum"])
        net_in = net_out = fees = taxes = 0.0
        for t, v in order:
            print(f"  {t:34s} n={v['count']:3d}  {v['sum']:+13.2f} ₽")
            if t == "OPERATION_TYPE_INPUT":
                net_in += v["sum"]
            elif t == "OPERATION_TYPE_OUTPUT":
                net_out += v["sum"]
            elif t == "OPERATION_TYPE_BROKER_FEE":
                fees += v["sum"]
            elif t in ("OPERATION_TYPE_TAX", "OPERATION_TYPE_TAX_CORRECTION"):
                taxes += v["sum"]
        print(f"  {'-'*64}")
        print(f"  Пополнения (INPUT):        {net_in:+13.2f} ₽")
        print(f"  Выводы (OUTPUT):           {net_out:+13.2f} ₽")
        print(f"  = Чистый ввод капитала:    {net_in + net_out:+13.2f} ₽")
        print(f"  Комиссии брокера:          {fees:+13.2f} ₽")
        print(f"  Налоги (удержано):         {taxes:+13.2f} ₽")
        print(f"  Реализованный P&L (yield): {r['realized_pl']:+13.2f} ₽")

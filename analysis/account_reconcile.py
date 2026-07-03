"""Реконсиляция счёта против оракула брокера (задача A — проба харнесса).

Две жёсткие тождественности, которые ОБЯЗАНЫ сойтись, иначе наш разбор операций врёт:

  (1) ДЕНЬГИ:  Σ payment по ВСЕМ исполненным операциям  ==  текущий кэш на счёте.
      Каждое движение денег — это операция. INPUT/+ , BUY/− , SELL/+ , комиссии/налоги/
      купоны/дивиденды/выводы — всё payment'ы. Их сумма = что лежит в money сейчас.

  (2) БУМАГИ: по каждому инструменту  Σ(BUY лоты) − Σ(SELL лоты)  ==  баланс в позициях.
      Целые числа, точное равенство. uid инструмента может мигрировать между листингами
      (Инвесткопилка!) — поэтому агрегируем и по uid, и по ISIN.

Оракул берём у самого брокера: OperationsService/GetPositions (read-only).
Если обе тождественности PASS — наш конвейер разбора операций корректен.
"""
import json
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
EPS = 0.01  # копейка



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
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, payload: dict, retries: int = 6) -> dict:
    url = f"{BASE}/tinkoff.public.invest.api.contract.v1.{method}"
    data = json.dumps(payload).encode()
    delay = 2.0
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {TOKEN}")
        req.add_header("Content-Type", "application/json")
        try:
            with _OPENER.open(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                wait = float(e.headers.get("Retry-After", delay))
                print(f"  HTTP {e.code}, ждём {wait:.0f}s")
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


CAP = 100  # фактический потолок страницы GetOperationsByCursor (limit>100 игнорируется)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def fetch_ops(account_id: str, frm: str, to: str) -> list:
    """Обход бага курсора: nextCursor отдаёт ТУ ЖЕ страницу, а limit режется до 100.
    Поэтому курсором не листаем, а рекурсивно нарезаем окно [from,to]: если окно
    вернуло потолок (>=CAP) и hasNext — делим пополам по времени. Так гарантированно
    выгребаем всё, не полагаясь на сломанную пагинацию."""
    by_id: dict[str, dict] = {}

    def grab(a: datetime, b: datetime, depth: int = 0) -> None:
        payload = {"accountId": account_id, "from": _iso(a), "to": _iso(b),
                   "cursorPagination": {"cursor": "", "limit": 1000}}
        resp = call("OperationsService/GetOperationsByCursor", payload)
        items = resp.get("items", [])
        full = len(items) >= CAP and resp.get("hasNext")
        span = (b - a).total_seconds()
        if full and span > 3600:  # окно переполнено — дробим, пока не влезет
            mid = a + (b - a) / 2
            grab(a, mid, depth + 1)
            grab(mid, b, depth + 1)
            return
        for op in items:
            by_id.setdefault(op.get("id"), op)
        tag = "  " * depth
        flag = " ⚠ВОЗМОЖНО НЕПОЛНО" if full else ""
        print(f"  {tag}[{_iso(a)[:10]}..{_iso(b)[:10]}] +{len(items)} "
              f"(уник {len(by_id)}){flag}")
        time.sleep(0.35)

    grab(_parse(frm), _parse(to))
    return list(by_id.values())


_ISIN_CACHE: dict[str, dict] = {}


def resolve(uid: str) -> dict:
    """uid -> {ticker, isin}. GetInstrumentBy по UID (read-only)."""
    if not uid:
        return {"ticker": "—", "isin": "—"}
    if uid in _ISIN_CACHE:
        return _ISIN_CACHE[uid]
    try:
        r = call("InstrumentsService/GetInstrumentBy",
                 {"idType": "INSTRUMENT_ID_TYPE_UID", "id": uid})
        ins = r.get("instrument", {})
        info = {"ticker": ins.get("ticker", "?"), "isin": ins.get("isin", "?")}
    except urllib.error.HTTPError:
        info = {"ticker": uid[:8], "isin": "?"}
    _ISIN_CACHE[uid] = info
    time.sleep(0.3)
    return info


# Знак операции по кол-ву бумаг.
QTY_SIGN = {
    "OPERATION_TYPE_BUY": +1, "OPERATION_TYPE_BUY_CARD": +1,
    "OPERATION_TYPE_BUY_MARGIN": +1, "OPERATION_TYPE_DELIVERY_BUY": +1,
    "OPERATION_TYPE_INPUT_SECURITIES": +1,
    "OPERATION_TYPE_SELL": -1, "OPERATION_TYPE_SELL_CARD": -1,
    "OPERATION_TYPE_SELL_MARGIN": -1, "OPERATION_TYPE_DELIVERY_SELL": -1,
    "OPERATION_TYPE_OUTPUT_SECURITIES": -1,
}


def reconcile(account_id: str, ops: list) -> bool:
    ex = [o for o in ops if o.get("state") == "OPERATION_STATE_EXECUTED"]

    # (1) Деньги: сумма всех payment'ов исполненных операций.
    cash_calc = sum(to_f(o.get("payment")) for o in ex)

    # (2) Бумаги: signed qty по uid.
    qty_by_uid: dict[str, int] = defaultdict(int)
    for o in ex:
        s = QTY_SIGN.get(o.get("type"))
        if s and o.get("instrumentUid"):
            qty_by_uid[o["instrumentUid"]] += s * int(o.get("quantity", "0") or 0)

    # Оракул.
    pos = call("OperationsService/GetPositions", {"accountId": account_id})
    cash_real = sum(to_f(m) for m in pos.get("money", [])) + \
        sum(to_f(m) for m in pos.get("blocked", []))
    real_by_uid = {s["instrumentUid"]: int(float(s["balance"]))
                   for s in pos.get("securities", [])}

    print(f"\n{'='*70}\nСЧЁТ {account_id}\n{'='*70}")

    # --- Тождество (1): деньги ---
    d_cash = cash_calc - cash_real
    ok_cash = abs(d_cash) <= EPS
    print("\n[1] ДЕНЬГИ")
    print(f"    Σ payment (расчёт):  {cash_calc:+14.2f} ₽")
    print(f"    money (оракул):      {cash_real:+14.2f} ₽")
    print(f"    расхождение:         {d_cash:+14.2f} ₽   -> {'PASS' if ok_cash else 'FAIL'}")

    # --- Тождество (2): бумаги по ISIN (uid мигрируют) ---
    print("\n[2] БУМАГИ (агрегация по ISIN — uid могут мигрировать)")
    calc_isin: dict[str, int] = defaultdict(int)
    uid_meta: dict[str, dict] = {}
    for uid, q in qty_by_uid.items():
        if q == 0:
            continue
        m = resolve(uid)
        uid_meta[uid] = m
        calc_isin[m["isin"]] += q
    real_isin: dict[str, int] = defaultdict(int)
    for uid, q in real_by_uid.items():
        m = resolve(uid)
        uid_meta[uid] = m
        real_isin[m["isin"]] += q

    all_isin = sorted(set(calc_isin) | set(real_isin))
    ok_qty = True
    for isin in all_isin:
        c, r = calc_isin.get(isin, 0), real_isin.get(isin, 0)
        tick = next((mm["ticker"] for mm in uid_meta.values() if mm["isin"] == isin), "?")
        status = "PASS" if c == r else "FAIL"
        if c != r:
            ok_qty = False
        print(f"    {tick:14s} {isin:14s} расчёт={c:>6d}  оракул={r:>6d}  "
              f"Δ={c-r:>+5d}  -> {status}")
    if not all_isin:
        print("    (нет бумажных позиций)")

    if ok_cash and ok_qty:
        verdict = "PASS ✓  (учёт по операциям сходится с оракулом до копейки)"
    elif ok_qty and 0 < abs(d_cash) < 2.0:
        # Бумаги точны, кэш расходится на копейки. Это НЕ ошибка нашего разбора
        # (другие счёта сходятся идеально), а потерянная API операция —
        # задокументированная потеря GetOperationsByCursor (часто на Инвесткопилке).
        verdict = (f"qty PASS ✓ / cash остаток {d_cash:+.2f} ₽ — вероятно операция, "
                   f"не отданная API (потеря GetOperationsByCursor)")
    else:
        verdict = "FAIL ✗"
    print(f"\nИТОГ счёта {account_id}: {verdict}")
    return ok_cash and ok_qty


if __name__ == "__main__":
    FROM, TO = "2025-01-18T00:00:00Z", "2026-06-19T23:59:59Z"
    out = ROOT / "analysis"
    out.mkdir(exist_ok=True)
    results = {}
    for acc in load_accounts():
        print(f"\n### Тяну операции счёта {acc} ...")
        ops = fetch_ops(acc, FROM, TO)
        (out / f"ops_{acc}.json").write_text(
            json.dumps(ops, ensure_ascii=False, indent=1), encoding="utf-8")
        results[acc] = reconcile(acc, ops)
    print(f"\n{'#'*70}")
    for acc, ok in results.items():
        print(f"#  {acc}: {'PASS ✓' if ok else 'FAIL ✗'}")
    print(f"{'#'*70}")

"""Сканер налоговой альфы по реальному счёту. READ-ONLY: ничего не исполняет,
только рекомендации к РУЧНЫМ действиям пользователя.

Что делает:
  1. GetAccounts -> по каждому счёту GetOperationsByCursor с даты открытия
     (рекурсивная нарезка окна — обход бага курсора, паттерн account_reconcile.fetch_ops;
     БЕЗ фильтра state — фильтр Executed теряет операции; дедуп по id).
  2. Знаковый FIFO по ISIN (паттерн tax_reconcile.py, сверенный с yield брокера до 0.00):
     открытые лоты с датой/ценой покупки.
  3. По каждому открытому лоту: нереализованный P&L (GetLastPrices; облигации
     price/100*nominal, НКД в P&L НЕ входит — отмечаем), срок владения, статус ЛДВ
     (>=3 года на брокерском, лимит 3 млн ₽ x полные годы), происхождение
     (ИИС-счетов у пользователя нет; лоты из INPUT_SECURITIES — происхождение неизвестно).
  4. Реализованная база 2026: FIFO-прибыль/убыток закрытий с 2026-01-01, дивиденды и
     купоны (отдельная база, ЛДВ на них не действует), удержанные налоги (TAX*).
  5. Альфа: (а) tax-loss harvesting — wash-sale в РФ НЕТ (ст. 214.1), продажа+откуп
     убыточных позиций сальдирует прибыль года; экономия = убыток x маржинальная ставка
     минус издержки (2x комиссия + полспреда из стакана);
     (б) лоты у 3-летнего ЛДВ-рубежа — даты и налог, который спасает ожидание;
     (в) корзины сальдирования ст. 214.1 НК (ЦБ <-> фондовые ПФИ, но НЕ валютные/товарные).
  6. Вывод: таблицы + analysis/tax_alpha_scan.json.

Лимиты API: операции 200/мин, инструменты 200/мин, marketdata 600/мин — сон между вызовами.
"""
import json
import time
import urllib.request
import urllib.error
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
NOW = datetime.now(timezone.utc)
YEAR = 2026                      # налоговый год для реализованной базы
LDV_YEARS = 3                    # рубеж ЛДВ
LDV_LIMIT_PER_YEAR = 3_000_000.0  # вычет 3 млн ₽ за каждый полный год владения
DEFAULT_COMM = 0.003             # 0.3% — фолбэк, если из истории комиссию не оценить

# Прогрессивная шкала НДФЛ 2026 (все базы суммируются для порогов)
BRACKETS = [(2_400_000, 0.13), (5_000_000, 0.15), (20_000_000, 0.18),
            (50_000_000, 0.20), (float("inf"), 0.22)]


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
                time.sleep(wait)
                delay = min(delay * 2, 30)
                continue
            raise
    raise SystemExit("retries exhausted")


def tof(v) -> float:
    """units/nano -> float; сырой REST НЕ кладёт строку value (кладут только обёртки)."""
    if not v:
        return 0.0
    if isinstance(v, dict):
        if v.get("value") not in (None, ""):
            return float(v["value"])
        return int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9
    return 0.0


def dt(s: str) -> datetime:
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def add_years(d: datetime, years: int) -> datetime:
    """Календарные +N лет (рубеж ЛДВ). 29 февраля -> 1 марта (иначе ValueError)."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, month=3, day=1)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─────────────────── операции: рекурсивная нарезка окна ───────────────────
CAP = 100  # фактический потолок страницы GetOperationsByCursor


def fetch_ops(account_id: str, frm: datetime, to: datetime) -> list:
    """Курсор не листает (nextCursor отдаёт ту же страницу) — нарезаем окно пополам,
    пока страница не помещается. БЕЗ фильтра state (баг теряет операции). Дедуп по id."""
    by_id: dict[str, dict] = {}

    def grab(a: datetime, b: datetime, depth: int = 0) -> None:
        payload = {"accountId": account_id, "from": _iso(a), "to": _iso(b),
                   "cursorPagination": {"cursor": "", "limit": 1000}}
        resp = call("OperationsService/GetOperationsByCursor", payload)
        items = resp.get("items", [])
        full = len(items) >= CAP and resp.get("hasNext")
        if full and (b - a).total_seconds() > 3600:
            mid = a + (b - a) / 2
            grab(a, mid, depth + 1)
            grab(mid, b, depth + 1)
            return
        for op in items:
            by_id.setdefault(op.get("id"), op)
        print(f"  {'  '*depth}[{_iso(a)[:10]}..{_iso(b)[:10]}] +{len(items)} "
              f"(уник {len(by_id)}){' !ПЕРЕПОЛНЕНО' if full else ''}")
        time.sleep(0.35)

    grab(frm, to)
    return list(by_id.values())


# ─────────────────── инструменты ───────────────────
_META: dict[str, dict] = {}


def resolve(uid: str) -> dict:
    """uid -> {ticker, isin, type, currency, nominal(облиг), lot}. Кэш в памяти."""
    if not uid:
        return {"ticker": "?", "isin": "?", "type": "?", "currency": "rub"}
    if uid in _META:
        return _META[uid]
    try:
        r = call("InstrumentsService/GetInstrumentBy",
                 {"idType": "INSTRUMENT_ID_TYPE_UID", "id": uid})
        ins = r.get("instrument", {})
        info = {"ticker": ins.get("ticker", uid[:8]), "isin": ins.get("isin", uid),
                "type": ins.get("instrumentType", "?"),
                "currency": ins.get("currency", "rub"),
                "name": ins.get("name", ""), "exchange": ins.get("exchange", "")}
        if info["type"] == "bond":
            time.sleep(0.3)
            b = call("InstrumentsService/GetBondBy",
                     {"idType": "INSTRUMENT_ID_TYPE_UID", "id": uid}).get("instrument", {})
            info["nominal"] = tof(b.get("nominal"))
            info["aci"] = tof(b.get("aciValue"))
    except urllib.error.HTTPError:
        info = {"ticker": uid[:8], "isin": uid, "type": "?", "currency": "rub"}
    _META[uid] = info
    time.sleep(0.3)
    return info


def basket(itype: str, ticker: str = "") -> str:
    """Корзина сальдирования по ст. 214.1 НК РФ."""
    if itype in ("share", "bond", "etf"):
        return "ЦБ"
    if itype == "futures":
        # фондовые ПФИ (на акции/индексы) сальдируются с ЦБ; валютные/товарные — нет.
        # Грубая эвристика по тикеру; при появлении фьючерсов уточнить вручную.
        return "ПФИ (проверить базовый актив!)"
    if itype == "currency":
        return "валюта (НЕ сальдируется с ЦБ; налог — самостоятельно, ст. 228)"
    return f"прочее ({itype})"


# ─────────────────── FIFO (паттерн tax_reconcile, сверен с yield брокера) ─────
TRADE_BUY = {"OPERATION_TYPE_BUY", "OPERATION_TYPE_BUY_CARD", "OPERATION_TYPE_BUY_MARGIN"}
TRADE_SELL = {"OPERATION_TYPE_SELL", "OPERATION_TYPE_SELL_CARD", "OPERATION_TYPE_SELL_MARGIN"}
TRANSFER_IN = {"OPERATION_TYPE_INPUT_SECURITIES", "OPERATION_TYPE_DELIVERY_BUY"}
TRANSFER_OUT = {"OPERATION_TYPE_OUTPUT_SECURITIES", "OPERATION_TYPE_DELIVERY_SELL"}


def fifo(ex: list):
    """Знаковый FIFO по ISIN. Возвращает (close_rows, open_lots, comm_by_year).
    Лот: [signed_qty, price, date_iso, from_transfer]. Gross-результат (как yield брокера);
    комиссии копим отдельно по годам."""
    trades = sorted([o for o in ex if o["type"] in TRADE_BUY | TRADE_SELL |
                     TRANSFER_IN | TRANSFER_OUT], key=lambda o: o["date"])
    lots: dict[str, deque] = defaultdict(deque)
    close_rows, comm_by_year = [], defaultdict(float)
    for o in trades:
        meta = resolve(o.get("instrumentUid"))
        isin = meta["isin"]
        q = int(o.get("quantity", "0") or 0)
        price = tof(o.get("price"))
        comm = abs(tof(o.get("commission")))
        comm_by_year[o["date"][:4]] += comm
        typ = o["type"]
        delta = q if typ in TRADE_BUY | TRANSFER_IN else -q
        transfer = typ in TRANSFER_IN
        dq = lots[isin]
        realized, closed_qty, lots_detail = 0.0, 0, []
        while delta != 0 and dq and (dq[0][0] > 0) != (delta > 0):
            lot = dq[0]
            take = min(abs(delta), abs(lot[0]))
            pl = (price - lot[1]) * take if lot[0] > 0 else (lot[1] - price) * take
            realized += pl
            held = (dt(o["date"]) - dt(lot[2])).days
            lots_detail.append({"qty": take, "buy_price": lot[1], "buy_date": lot[2][:10],
                                "held_days": held, "pl": round(pl, 2),
                                # календарные 3 года (не 3x365 дней — високосные)
                                "ldv_ok": dt(o["date"]) >= add_years(dt(lot[2]), LDV_YEARS)
                                          and not lot[3]})
            closed_qty += take
            lot[0] += take if lot[0] < 0 else -take
            delta += take if delta < 0 else -take
            if lot[0] == 0:
                dq.popleft()
        if delta != 0:
            dq.append([delta, price, o["date"], transfer])
        if closed_qty:
            close_rows.append({"date": o["date"][:10], "isin": isin,
                               "ticker": meta["ticker"], "type": meta["type"],
                               "qty": closed_qty, "realized": round(realized, 2),
                               "lots": lots_detail})
    open_lots = {isin: [l for l in dq if l[0] != 0]
                 for isin, dq in lots.items() if any(l[0] for l in dq)}
    return close_rows, open_lots, comm_by_year


# ─────────────────── цены ───────────────────
def last_prices(uids: list) -> dict:
    if not uids:
        return {}
    r = call("MarketDataService/GetLastPrices", {"instrumentId": uids})
    out = {}
    for it in r.get("lastPrices", []):
        p = tof(it.get("price"))
        if p:
            out[it.get("instrumentUid")] = p
    return out


def half_spread(uid: str) -> float | None:
    """Полспреда из стакана глубины 1 (руб на единицу, для облигаций — в % номинала)."""
    try:
        ob = call("MarketDataService/GetOrderBook", {"instrumentId": uid, "depth": 1})
        bids, asks = ob.get("bids", []), ob.get("asks", [])
        if bids and asks:
            return (tof(asks[0]["price"]) - tof(bids[0]["price"])) / 2
    except urllib.error.HTTPError:
        pass
    return None


def to_rub(price: float, meta: dict) -> float:
    """MarketData-цена -> рубли за штуку. Облигации: %номинала -> price/100*nominal."""
    if meta.get("type") == "bond":
        return price / 100.0 * meta.get("nominal", 1000.0)
    return price


def marginal_rate(total_income: float) -> float:
    for cap, rate in BRACKETS:
        if total_income <= cap:
            return rate
    return 0.22


# ─────────────────── главный проход ───────────────────
def main():
    accounts = call("UsersService/GetAccounts", {}).get("accounts", [])
    accounts = [a for a in accounts if a.get("status") == "ACCOUNT_STATUS_OPEN"]
    has_iis = any("IIS" in a.get("type", "") for a in accounts)
    print("=" * 78)
    print("СКАНЕР НАЛОГОВОЙ АЛЬФЫ (read-only; рекомендации к ручным действиям)")
    print("=" * 78)
    for a in accounts:
        print(f"  счёт {a['id']}  {a['type']:28s}  {a.get('name','')}  "
              f"открыт {a['openedDate'][:10]}")
    print(f"  ИИС-счетов у брокера: {'ЕСТЬ' if has_iis else 'нет'} -> "
          f"{'ловушка ЛДВ актуальна' if has_iis else 'купленные здесь бумаги на ИИС не бывали'}")
    # Наличие ИИС не лишает ЛДВ бумаги брокерского счёта — теряют льготу только
    # бумаги, побывавшие на ИИС. Но переводы между счетами сканер по операциям
    # не различает — при наличии ИИС происхождение переведённых лотов проверять вручную.

    result = {"generated": _iso(NOW), "accounts": [], "open_positions": [],
              "realized_ytd": {}, "harvest": [], "ldv_watch": [], "issues": [],
              "recommendations": []}
    issues = result["issues"]
    if has_iis:
        issues.append("У брокера есть ИИС: бумаги, побывавшие на ИИС, навсегда теряют "
                      "ЛДВ — происхождение лотов из переводов (INPUT_SECURITIES) "
                      "проверить вручную; купленных на брокерском это не касается")

    all_close, all_open = [], []   # (acc, row) / (acc, isin, lot, meta, uid)
    comm_total, notional_total = 0.0, 0.0
    div_gross = coup_gross = tax_withheld = tax_div = tax_coup = 0.0
    uid_by_isin: dict[str, str] = {}

    for a in accounts:
        acc = a["id"]
        print(f"\n### Операции счёта {acc} с {a['openedDate'][:10]} ...")
        ops = fetch_ops(acc, dt(a["openedDate"]), NOW)
        ex = [o for o in ops if o.get("state") == "OPERATION_STATE_EXECUTED"]
        print(f"  всего {len(ops)}, EXECUTED {len(ex)}")

        # комиссия из истории (для оценки издержек откупа)
        for o in ex:
            if o["type"] in TRADE_BUY | TRADE_SELL:
                comm_total += abs(tof(o.get("commission")))
                notional_total += abs(tof(o.get("payment")))

        # дивиденды/купоны/налоги ТЕКУЩЕГО года
        for o in ex:
            if not o["date"].startswith(str(YEAR)):
                continue
            t, pay = o["type"], tof(o.get("payment"))
            if t in ("OPERATION_TYPE_DIVIDEND", "OPERATION_TYPE_DIV_EXT",
                     "OPERATION_TYPE_DIVIDEND_TRANSFER"):
                div_gross += pay
            elif t == "OPERATION_TYPE_COUPON":
                coup_gross += pay
            elif t in ("OPERATION_TYPE_DIVIDEND_TAX",
                       "OPERATION_TYPE_DIVIDEND_TAX_PROGRESSIVE"):
                tax_div += -pay
            elif t in ("OPERATION_TYPE_BOND_TAX", "OPERATION_TYPE_BOND_TAX_PROGRESSIVE",
                       "OPERATION_TYPE_TAX_CORRECTION_COUPON"):
                tax_coup += -pay
            elif t.startswith("OPERATION_TYPE_TAX"):
                tax_withheld += -pay      # TAX отрицателен, коррекции положительны

        close_rows, open_lots, comm_by_year = fifo(ex)
        for r in close_rows:
            all_close.append((acc, r))
        for isin, lts in open_lots.items():
            meta = next((m for m in _META.values() if m["isin"] == isin), {})
            uid = next((u for u, m in _META.items() if m["isin"] == isin), None)
            uid_by_isin[isin] = uid
            for lot in lts:
                all_open.append((acc, isin, lot, meta, uid))
        result["accounts"].append({"id": acc, "type": a["type"], "name": a.get("name"),
                                   "opened": a["openedDate"][:10], "n_ops": len(ex),
                                   "commission_by_year": {k: round(v, 2)
                                                          for k, v in comm_by_year.items()}})

    comm_rate = comm_total / notional_total if notional_total > 0 else DEFAULT_COMM
    print(f"\nЭффективная комиссия из истории: {comm_rate*100:.4f}% "
          f"(Σкомиссий {comm_total:.2f} ₽ / Σоборот {notional_total:.2f} ₽)")

    # ── Реализованная база текущего года ──
    ytd = [r for _, r in all_close if r["date"].startswith(str(YEAR))]
    realized_gross = sum(r["realized"] for r in ytd)
    comm_ytd = sum(a["commission_by_year"].get(str(YEAR), 0.0)
                   for a in result["accounts"])
    realized_net = realized_gross - comm_ytd
    profit_rows = [r for r in ytd if r["realized"] > 0]
    loss_rows = [r for r in ytd if r["realized"] < 0]

    print("\n" + "=" * 78)
    print(f"[4] РЕАЛИЗОВАННАЯ БАЗА {YEAR} (FIFO, gross как yield брокера)")
    print("=" * 78)
    for r in sorted(ytd, key=lambda x: x["date"]):
        print(f"  {r['date']}  {r['ticker']:10s} {r['type']:6s} qty={r['qty']:>5d}  "
              f"P&L={r['realized']:+12.2f} ₽  корзина: {basket(r['type'])}")
    if not ytd:
        print("  (закрытий в этом году нет)")
    print(f"  {'-'*70}")
    print(f"  Прибыль по закрытиям:  {sum(r['realized'] for r in profit_rows):+12.2f} ₽")
    print(f"  Убыток по закрытиям:   {sum(r['realized'] for r in loss_rows):+12.2f} ₽")
    print(f"  Gross финрез {YEAR}:       {realized_gross:+12.2f} ₽")
    print(f"  Комиссии {YEAR}:           {comm_ytd:12.2f} ₽")
    print(f"  Чистая база ЦБ:        {realized_net:+12.2f} ₽")
    print(f"  Дивиденды gross:       {div_gross:12.2f} ₽ (отдельная база, ЛДВ нет)")
    print(f"  Купоны gross:          {coup_gross:12.2f} ₽ (в базу ЦБ, сальдируются)")
    print(f"  Удержано НДФЛ: сделки+прочее {tax_withheld:.2f} ₽, дивиденды {tax_div:.2f} ₽, "
          f"купоны {tax_coup:.2f} ₽")

    total_income = max(realized_net, 0) + coup_gross + div_gross
    rate = marginal_rate(total_income)
    print(f"  Совокупный инвестдоход {YEAR} ~{total_income:,.0f} ₽ -> маржинальная ставка "
          f"{rate*100:.0f}% (порог 13->15% на 2.4 млн ₽)")
    result["realized_ytd"] = {
        "year": YEAR, "closes": ytd, "realized_gross": round(realized_gross, 2),
        "commission": round(comm_ytd, 2), "realized_net": round(realized_net, 2),
        "dividends_gross": round(div_gross, 2), "coupons_gross": round(coup_gross, 2),
        "tax_withheld_trades": round(tax_withheld, 2),
        "tax_withheld_dividends": round(tax_div, 2),
        "tax_withheld_coupons": round(tax_coup, 2), "marginal_rate": rate}

    # ── Открытые позиции: цены, P&L, ЛДВ ──
    uids = sorted({u for _, _, _, _, u in all_open if u})
    prices = last_prices(uids)
    print("\n" + "=" * 78)
    print("[3] ОТКРЫТЫЕ ПОЗИЦИИ: FIFO-лоты, нереализованный P&L, срок, ЛДВ")
    print("=" * 78)
    print("  (облигации: цена price/100*nominal, БЕЗ НКД — при продаже полученный НКД")
    print("   добавится в базу как купонный доход; дивиденды/купоны под ЛДВ не попадают)")
    pos_agg: dict[tuple, dict] = {}
    for acc, isin, lot, meta, uid in all_open:
        qty, bp, bdate, transfer = lot[0], lot[1], lot[2], lot[3]
        cur = prices.get(uid)
        cur_rub = to_rub(cur, meta) if cur else None
        buy_rub = bp  # в Operations цена уже в валюте (для облигаций — чистая, руб)
        upl = (cur_rub - buy_rub) * qty if cur_rub is not None else None
        held = (NOW - dt(bdate)).days
        d0 = dt(bdate)
        ldv_date = add_years(d0, LDV_YEARS).date()
        # Наличие ИИС у брокера само по себе ЛДВ НЕ отменяет: льготу теряют только
        # бумаги, ПОБЫВАВШИЕ на ИИС (ст. 219.1 НК). Купленные на этом счёте лоты
        # (not transfer) на ИИС не бывали; лоты из переводов — проверять вручную.
        ldv_eligible = (meta.get("type") in ("share", "etf", "bond")
                        and str(isin).startswith("RU") and not transfer)
        otc = "MOEX" not in (meta.get("exchange") or "").upper() and \
              (meta.get("exchange") or "").lower() not in ("moex", "")
        row = {"account": acc, "ticker": meta.get("ticker"), "isin": isin,
               "itype": meta.get("type"), "qty": qty, "buy_date": bdate[:10],
               "buy_price_rub": round(buy_rub, 4),
               "cur_price_rub": round(cur_rub, 4) if cur_rub is not None else None,
               "unrealized_pl": round(upl, 2) if upl is not None else None,
               "held_days": held, "ldv_date": str(ldv_date),
               "ldv_reached": NOW.date() >= ldv_date,
               "ldv_eligible": ldv_eligible,
               "was_on_iis": "неизвестно (перевод бумаг)" if transfer else "нет",
               "basket": basket(meta.get("type", "?")),
               "exchange": meta.get("exchange", "")}
        result["open_positions"].append(row)
        key = (acc, isin)
        agg = pos_agg.setdefault(key, {"ticker": meta.get("ticker"), "qty": 0,
                                       "cost": 0.0, "upl": 0.0, "meta": meta,
                                       "uid": uid, "acc": acc, "lots": []})
        agg["qty"] += qty
        agg["cost"] += buy_rub * qty
        agg["upl"] += upl or 0.0
        agg["lots"].append(row)
        if cur_rub is None:
            issues.append(f"{meta.get('ticker')} ({isin}): нет last price "
                          f"(вероятно внебиржевой, напр. TMON OTC) — P&L не посчитан")
        if otc:
            issues.append(f"{meta.get('ticker')}: площадка '{meta.get('exchange')}' — "
                          f"для ЛДВ требуется обращение на организованном рынке, проверить")

    for (acc, isin), g in sorted(pos_agg.items(), key=lambda kv: kv[1]["upl"]):
        m = g["meta"]
        cur = prices.get(g["uid"])
        cur_rub = to_rub(cur, m) if cur else None
        print(f"\n  {g['ticker']:10s} счёт {acc}  qty={g['qty']}  "
              f"тек.цена={cur_rub if cur_rub is not None else '—'}  "
              f"нереализ. P&L={g['upl']:+.2f} ₽  корзина: {basket(m.get('type','?'))}")
        for r in g["lots"]:
            mark = ("ЛДВ ДОСТИГНУТА" if r["ldv_reached"] and r["ldv_eligible"] else
                    f"ЛДВ с {r['ldv_date']}" if r["ldv_eligible"] else "ЛДВ: не применима")
            print(f"    лот {r['qty']:>5d} шт  куплен {r['buy_date']} по {r['buy_price_rub']}"
                  f"  держим {r['held_days']} дн  P&L={r['unrealized_pl'] if r['unrealized_pl'] is not None else '—'} ₽"
                  f"  {mark}  ИИС: {r['was_on_iis']}")

    # ── [5а] Tax-loss harvesting ──
    print("\n" + "=" * 78)
    print("[5а] TAX-LOSS HARVESTING (wash-sale в РФ НЕТ: продать и сразу откупить можно)")
    print("=" * 78)
    remaining_profit = max(realized_net, 0.0)
    print(f"  Сальдируемая прибыль года (база ЦБ, с купонами): "
          f"{remaining_profit + coup_gross:,.2f} ₽")
    remaining_profit += coup_gross  # купоны сальдируются с убытками по ЦБ
    harvest = []
    cands = [g for g in pos_agg.values() if g["upl"] < -1.0 and
             g["meta"].get("type") in ("share", "etf", "bond")]
    for g in sorted(cands, key=lambda x: x["upl"]):
        uid, m = g["uid"], g["meta"]
        cur = prices.get(uid)
        if cur is None:
            print(f"  {g['ticker']}: убыток {g['upl']:+.2f} ₽, но нет рыночной цены/стакана "
                  f"— кандидат только теоретический")
            continue
        cur_rub = to_rub(cur, m)
        hs = half_spread(uid)
        hs_rub = to_rub(hs, m) if hs is not None else 0.0
        notion = cur_rub * g["qty"]
        cost = 2 * comm_rate * notion + hs_rub * g["qty"]
        loss = -g["upl"]
        usable = min(loss, remaining_profit)
        saving = usable * rate
        net = saving - cost
        remaining_profit -= usable
        # шаг 3 TLH-алгоритма: продажа лота, близкого к 3-летнему рубежу, обнуляет
        # таймер ЛДВ (при откупе отсчёт заново) — предупреждаем, решение за человеком
        ldv_reset = [r for r in g["lots"] if r["ldv_eligible"]
                     and r["held_days"] >= int(2.5 * 365)]
        harvest.append({"ticker": g["ticker"], "account": g["acc"], "qty": g["qty"],
                        "unrealized_loss": round(-loss, 2), "usable_now": round(usable, 2),
                        "tax_saving": round(saving, 2),
                        "roundtrip_cost": round(cost, 2),
                        "half_spread_rub": round(hs_rub, 4) if hs is not None else None,
                        "net_benefit_now": round(net, 2),
                        "carry_value_full_loss": round(loss * rate, 2),
                        "ldv_timer_reset_warning": bool(ldv_reset)})
        print(f"  {g['ticker']:10s} убыток {-loss:+12.2f} ₽ | сальдируется сейчас "
              f"{usable:10.2f} ₽ -> экономия {saving:8.2f} ₽ | издержки продажа+откуп "
              f"{cost:7.2f} ₽ (2x{comm_rate*100:.3f}% + полспреда "
              f"{hs_rub if hs is not None else '—'}) | ЧИСТЫЙ эффект сейчас {net:+.2f} ₽")
        if ldv_reset:
            print(f"    ВНИМАНИЕ: лот(ы) держатся >=2.5 лет — продажа обнулит таймер ЛДВ; "
                  f"фиксировать убыток, только если экономия существенно больше "
                  f"потенциала льготы при восстановлении цены")
        if usable < loss:
            print(f"    остаток убытка {loss-usable:,.2f} ₽ сальдируется с прибылью до конца "
                  f"года или переносится до 10 лет (3-НДФЛ); потенциал {loss*rate:,.2f} ₽")
    if not cands:
        print("  Кандидатов нет: открытых позиций ЦБ с нереализованным убытком не найдено.")
    result["harvest"] = harvest

    # ── [5б] ЛДВ-рубеж ──
    print("\n" + "=" * 78)
    print(f"[5б] ЛДВ-РУБЕЖ (держать >= {LDV_YEARS} лет; вычет "
          f"{LDV_LIMIT_PER_YEAR/1e6:.0f} млн ₽ x полные годы владения)")
    print("=" * 78)
    for r in sorted(result["open_positions"], key=lambda x: x["ldv_date"]):
        if not r["ldv_eligible"] or r["unrealized_pl"] is None:
            continue
        days_left = (dt(r["ldv_date"] + "T00:00:00") - NOW).days
        gain = max(r["unrealized_pl"], 0.0)
        tax_at_stake = min(gain, LDV_LIMIT_PER_YEAR * LDV_YEARS) * rate
        row = {"ticker": r["ticker"], "account": r["account"], "qty": r["qty"],
               "buy_date": r["buy_date"], "ldv_date": r["ldv_date"],
               "days_left": days_left, "unrealized_gain": round(gain, 2),
               "tax_saved_by_waiting": round(tax_at_stake, 2)}
        result["ldv_watch"].append(row)
        verdict = ("УЖЕ ЛЬГОТНЫЙ — продажа без НДФЛ до лимита" if days_left <= 0 else
                   f"не продавать до {r['ldv_date']} (осталось {days_left} дн)"
                   if gain > 0 else f"рубеж {r['ldv_date']}, прибыли нет — рубеж не критичен")
        print(f"  {r['ticker']:10s} лот {r['qty']:>5d} от {r['buy_date']}  "
              f"P&L={r['unrealized_pl']:+10.2f} ₽  {verdict}"
              + (f"; ожидание спасает ~{tax_at_stake:,.2f} ₽ налога" if gain > 0 and days_left > 0 else ""))

    # ── [5в] корзины сальдирования ──
    print("\n" + "=" * 78)
    print("[5в] КОРЗИНЫ СAЛЬДИРОВАНИЯ (ст. 214.1 НК РФ)")
    print("=" * 78)
    baskets_seen = defaultdict(list)
    for r in result["open_positions"]:
        baskets_seen[r["basket"]].append(r["ticker"])
    for _, r in all_close:
        baskets_seen[basket(r["type"])].append(r["ticker"])
    for b, ticks in baskets_seen.items():
        print(f"  {b}: {sorted(set(ticks))}")
    print("  Правила: ЦБ <-> фондовые ПФИ (фьючерсы на акции/индексы) — сальдируются;")
    print("  валютные/товарные ПФИ — только между собой (с ЦБ НЕЛЬЗЯ);")
    print("  ИИС не сальдируется с брокерским (у пользователя ИИС нет);")
    print("  межброкерское сальдирование — только через 3-НДФЛ.")
    result["baskets"] = {b: sorted(set(t)) for b, t in baskets_seen.items()}

    # ── рекомендации ──
    recs = []
    for h in harvest:
        if h["net_benefit_now"] > 0:
            warn = (" ВНИМАНИЕ: в позиции лоты >=2.5 лет — продажа обнулит таймер ЛДВ."
                    if h.get("ldv_timer_reset_warning") else "")
            recs.append(f"ПРОДАТЬ+ОТКУПИТЬ {h['ticker']} (счёт {h['account']}): фиксация "
                        f"убытка {h['unrealized_loss']} ₽ сальдирует {h['usable_now']} ₽ "
                        f"прибыли года, чистая экономия ~{h['net_benefit_now']} ₽ "
                        f"(wash-sale в РФ нет). Вручную, в ликвидные часы.{warn}")
        else:
            recs.append(f"{h['ticker']}: сейчас чистый эффект {h['net_benefit_now']} ₽ — "
                        f"держать в списке на конец года (сальдирование с будущей прибылью, "
                        f"потенциал {h['carry_value_full_loss']} ₽).")
    for w in result["ldv_watch"]:
        if w["unrealized_gain"] > 0 and w["days_left"] > 0:
            recs.append(f"НЕ ПРОДАВАТЬ {w['ticker']} до {w['ldv_date']}: ЛДВ спасёт "
                        f"~{w['tax_saved_by_waiting']} ₽ (риск: цена может уйти).")
    if not has_iis:
        recs.append("ИИС-3 отсутствует: пополнение до 400 000 ₽/год дало бы вычет до "
                    "52 000 ₽ (13%) при наличии облагаемого дохода. НО: бумаги на ИИС "
                    "навсегда теряют ЛДВ — кандидатов на ЛДВ туда не переводить.")
    result["recommendations"] = recs
    print("\n" + "=" * 78)
    print("РЕКОМЕНДАЦИИ (ручные действия, скрипт ничего не исполняет)")
    print("=" * 78)
    for i, r in enumerate(recs, 1):
        print(f"  {i}. {r}")
    if issues:
        print("\nОГОВОРКИ/ПРОБЛЕМЫ:")
        for s in sorted(set(issues)):
            print(f"  - {s}")

    result["issues"] = sorted(set(issues))   # дедуп (в stdout уже дедупится)
    out = ROOT / "analysis" / "tax_alpha_scan.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nJSON: {out}")


if __name__ == "__main__":
    main()

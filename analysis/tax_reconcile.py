"""Реконструкция НДФЛ и ЛДВ по счёту против оракула брокера (большая проба харнесса).

Три сверки с жёстким оракулом:

  [A] FIFO-реализованный P&L == брокерское поле `yield` в каждой операции SELL.
      Это прямой оракул: брокер сам кладёт реализованный результат в каждую продажу.
      Russian tax FIFO: продаём самые старые лоты первыми; финрезультат =
      (выручка − комиссия продажи) − (FIFO-себестоимость + комиссии покупок).
      uid инструмента МИГРИРУЕТ → FIFO ведём по ISIN.

  [B] Удержанный НДФЛ (Σ TAX − Σ TAX_CORRECTION) ≈ ставка × чистый положительный
      финрезультат. Проверяем, какую ставку и базу фактически применил брокер.

  [C] ЛДВ (льгота за 3 года владения): по каждому проданному лоту срок владения;
      право на льготу только при сроке ≥3 года. Прогноз, когда дозреет текущая позиция.

Данные — из задачи A (analysis/ops_*.json, полная выгрузка). Read-only.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://invest-public-api.tinkoff.ru/rest"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))



def load_accounts():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_ACCOUNTS="):
            return [a.strip() for a in line.split("=", 1)[1].split(",") if a.strip()]
    raise SystemExit("Set TINVEST_ACCOUNTS=id1,id2 in .env")

def _token() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no token")


TOKEN = _token()


def call(method: str, payload: dict) -> dict:
    url = f"{BASE}/tinkoff.public.invest.api.contract.v1.{method}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    with _OPENER.open(req, timeout=30) as r:
        return json.loads(r.read())


def tof(v) -> float:
    if not v:
        return 0.0
    if isinstance(v, dict):
        if v.get("value") not in (None, ""):
            return float(v["value"])
        return int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9
    return 0.0


def dt(s: str) -> datetime:
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


_ISIN: dict[str, dict] = {}


def resolve(uid: str) -> dict:
    if uid in _ISIN:
        return _ISIN[uid]
    try:
        r = call("InstrumentsService/GetInstrumentBy",
                 {"idType": "INSTRUMENT_ID_TYPE_UID", "id": uid})
        ins = r.get("instrument", {})
        info = {"ticker": ins.get("ticker", uid[:8]), "isin": ins.get("isin", uid)}
    except urllib.error.HTTPError:
        info = {"ticker": uid[:8], "isin": uid}
    _ISIN[uid] = info
    time.sleep(0.25)
    return info


def load_ops(acc: str) -> list:
    ops = json.loads((ROOT / "analysis" / f"ops_{acc}.json").read_text(encoding="utf-8"))
    return [o for o in ops if o.get("state") == "OPERATION_STATE_EXECUTED"]


# ───────────────────── FIFO по ISIN ─────────────────────
def fifo_realized(ex: list) -> tuple[dict, list]:
    """Вернуть (per_isin, sell_rows). ЗНАКОВЫЙ FIFO (лонг+шорт), gross-результат.

    Брокерское поле `yield` — ГРОССОВЫЙ финрезультат (БЕЗ комиссий), и оно проставлено
    на ЛЮБОЙ сделке, которая что-то закрывает (в т.ч. BUY-покрытие шорта). Поэтому:
      * реализованный считаем БЕЗ комиссий (для сверки с yield);
      * комиссии копим отдельно (для налоговой базы);
      * лоты знаковые: SELL сверх лонга открывает шорт, BUY его покрывает.
    """
    trades = sorted([o for o in ex if o["type"] in (
        "OPERATION_TYPE_BUY", "OPERATION_TYPE_SELL")], key=lambda o: o["date"])
    lots: dict[str, deque] = defaultdict(deque)        # isin -> deque[[signed_qty, price, date]]
    per_isin = defaultdict(lambda: {"realized": 0.0, "broker_yield": 0.0,
                                    "ticker": "", "n_close": 0, "commission": 0.0})
    sell_rows = []
    for o in trades:
        meta = resolve(o.get("instrumentUid"))
        isin, tick = meta["isin"], meta["ticker"]
        q = int(o.get("quantity", "0") or 0)
        price = tof(o.get("price"))
        comm = abs(tof(o.get("commission")))
        delta = q if o["type"] == "OPERATION_TYPE_BUY" else -q
        per_isin[isin]["ticker"] = tick
        per_isin[isin]["commission"] += comm
        per_isin[isin]["broker_yield"] += tof(o.get("yield"))   # yield есть и на BUY-покрытии

        realized = 0.0
        held_w = 0.0
        closed_qty = 0
        dq = lots[isin]
        # закрываем встречные лоты FIFO
        while delta != 0 and dq and (dq[0][0] > 0) != (delta > 0):
            lot = dq[0]
            take = min(abs(delta), abs(lot[0]))
            if lot[0] > 0:                              # закрываем лонг продажей
                realized += (price - lot[1]) * take
            else:                                      # закрываем шорт покупкой
                realized += (lot[1] - price) * take
            held_w += take * (dt(o["date"]) - dt(lot[2])).days
            closed_qty += take
            lot[0] += take if lot[0] < 0 else -take
            delta += take if delta < 0 else -take
            if lot[0] == 0:
                dq.popleft()
        if delta != 0:                                 # остаток открывает новый лот
            dq.append([delta, price, o["date"]])
        if closed_qty:
            per_isin[isin]["realized"] += realized
            per_isin[isin]["n_close"] += 1
            sell_rows.append({"date": o["date"][:10], "isin": isin, "ticker": tick,
                              "qty": closed_qty, "realized": realized,
                              "avg_held_days": held_w / closed_qty})
    # остаток открытых лотов (для ЛДВ-прогноза текущих позиций)
    open_lots = {isin: [(l[0], l[1], l[2]) for l in dq if l[0] != 0]
                 for isin, dq in lots.items() if any(l[0] for l in dq)}
    return per_isin, sell_rows, open_lots


def analyze(acc: str):
    print("\n" + "=" * 72)
    print(f"СЧЁТ {acc}")
    print("=" * 72)
    ex = load_ops(acc)
    per_isin, sell_rows, open_lots = fifo_realized(ex)

    # ── [A] FIFO realized (gross) vs брокерский yield ──
    print("\n[A] Знаковый FIFO gross-результат vs брокерский yield (по ISIN)")
    tot_mine = tot_broker = tot_comm = 0.0
    for isin, d in sorted(per_isin.items(), key=lambda kv: -abs(kv[1]["broker_yield"])):
        if d["n_close"] == 0 and abs(d["broker_yield"]) < 1e-9:
            continue
        diff = d["realized"] - d["broker_yield"]
        tot_mine += d["realized"]
        tot_broker += d["broker_yield"]
        tot_comm += d["commission"]
        flag = "OK" if abs(diff) < 0.01 else f"Δ={diff:+.2f}"
        print(f"    {d['ticker']:12s} {isin:14s} закрытий={d['n_close']:3d}  "
              f"мой={d['realized']:+11.2f}  брокер={d['broker_yield']:+11.2f}  {flag}")
    dt_tot = tot_mine - tot_broker
    okA = abs(dt_tot) < 0.01
    print(f"    {'ИТОГО':12s} {'':14s}              "
          f"мой={tot_mine:+11.2f}  брокер={tot_broker:+11.2f}  "
          f"Δ={dt_tot:+.2f}  -> {'PASS' if okA else 'FAIL'}")

    # ── [B] НДФЛ удержанный vs ставка × чистая база ──
    tax = sum(tof(o.get("payment")) for o in ex if o["type"] == "OPERATION_TYPE_TAX")
    corr = sum(tof(o.get("payment")) for o in ex if o["type"] == "OPERATION_TYPE_TAX_CORRECTION")
    net_tax = -(tax + corr)                              # фактически удержано (>0)
    net_base = tot_broker - tot_comm                     # финрезультат за вычетом комиссий
    implied = 0.13 * max(net_base, 0)
    eff_rate = net_tax / net_base if net_base > 0 else 0.0
    print("\n[B] НДФЛ (база = gross финрез − комиссии; ставка 13%)")
    print(f"    gross финрез={tot_broker:+.2f}  − комиссии={tot_comm:.2f}  = чистая база {net_base:+.2f} ₽")
    print(f"    13% от чистой базы (ожид. налог) = {implied:.2f} ₽")
    print(f"    фактически удержано (TAX − коррекции) = {net_tax:.2f} ₽   "
          f"эфф.ставка={eff_rate*100:.1f}%")

    # ── [C] ЛДВ: проданные лоты + прогноз по текущим позициям ──
    print("\n[C] ЛДВ (льгота за непрерывное владение ≥3 года, обращ. бумаги)")
    max_held = max((r["avg_held_days"] for r in sell_rows), default=0.0)
    qualified = [r for r in sell_rows if r["avg_held_days"] >= 3 * 365]
    print(f"    проданные лоты: макс. срок владения {max_held:.0f} дн ({max_held/365:.2f} г); "
          f"с правом на ЛДВ (≥3 г): {len(qualified)} -> "
          f"{'льгота была применима' if qualified else 'льготы не было (счёт молод)'}")
    if open_lots:
        print("    текущие позиции — когда дозреют до ЛДВ:")
        for isin, lts in open_lots.items():
            tick = per_isin[isin]["ticker"]
            oldest = min(dt(l[2]) for l in lts)
            qty = sum(l[0] for l in lts)
            qual_date = oldest.replace(year=oldest.year + 3)
            print(f"      {tick:10s} {qty:+6.0f} шт, старейший лот {oldest.date()} "
                  f"-> ЛДВ с {qual_date.date()}")
    return {"tot_mine": tot_mine, "tot_broker": tot_broker, "okA": okA,
            "net_tax": net_tax, "eff_rate": eff_rate, "net_base": net_base,
            "implied": implied}


if __name__ == "__main__":
    res = {}
    for acc in load_accounts():
        res[acc] = analyze(acc)
    print("\n" + "#" * 72)
    for acc, r in res.items():
        print(f"#  {acc}: FIFO==брокер {'PASS' if r['okA'] else 'FAIL'}  "
              f"gross={r['tot_broker']:+.2f}  база={r['net_base']:+.2f}  "
              f"налог факт={r['net_tax']:.2f} / ожид={r['implied']:.2f}")
    print("#" * 72)

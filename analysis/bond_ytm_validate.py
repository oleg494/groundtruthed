"""Восстановление доходности облигаций (YTM) против поля `yield` API.

    python analysis/bond_ytm_validate.py

Оракул — поле INSTRUMENT_VALUE_YIELD из MarketDataService (то, что трейдер видит как
«доходность к погашению»). Из графика купонов (GetBondCoupons), НКД (GetAccruedInterests),
номинала и чистой цены сами собираем грязную цену и решаем YTM как IRR денежного потока,
затем сверяем с оракулом. Конвенция (день-в-год, эффективная vs номинальная ставка,
дата расчётов T+1) нигде не задокументирована точно — выводим, какая сходится с числом API.

ВАЖНО: цена облигации в MarketData приходит в % от номинала (см. docs/points.md). READ-ONLY.
"""
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

BONDS = [
    ("ОФЗ 26238", "92b9e913-d7df-4164-bd83-1013c819bf44"),
    ("ОФЗ 26240", "8e0cdf80-b569-4ccf-ac4e-ead70c9e4c80"),
    ("ОФЗ 26248", "043ffb17-07e9-4c79-941f-4c5712d133cd"),
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


def mv(v):
    if not v:
        return None
    return float(v["value"]) if v.get("value") not in (None, "") else \
        int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9


def dparse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).date()


def fetch(uid):
    b = call("InstrumentsService/BondBy",
             {"idType": "INSTRUMENT_ID_TYPE_UID", "id": uid})["instrument"]
    nominal = mv(b["nominal"])
    maturity = dparse(b["maturityDate"])
    cps = call("InstrumentsService/GetBondCoupons",
               {"instrumentId": uid,
                "from": "2020-01-01T00:00:00Z",
                "to": maturity.strftime("%Y-%m-%dT00:00:00Z")}).get("events", []) \
        or call("InstrumentsService/GetBondCoupons",
                {"instrumentId": uid, "from": "2020-01-01T00:00:00Z",
                 "to": maturity.strftime("%Y-%m-%dT00:00:00Z")}).get("coupons", [])
    coupons = [(dparse(c["couponDate"]), mv(c["payOneBond"])) for c in cps]
    today = datetime.now(timezone.utc).date()
    ai = call("InstrumentsService/GetAccruedInterests",
              {"instrumentId": uid,
               "from": (today - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z"),
               "to": (today + timedelta(days=2)).strftime("%Y-%m-%dT00:00:00Z")}
              ).get("accruedInterests", [])
    aci_rows = [(dparse(a["date"]), mv(a["value"])) for a in ai]
    m = call("MarketDataService/GetMarketValues",
             {"instrumentId": [uid],
              "values": ["INSTRUMENT_VALUE_LAST_PRICE", "INSTRUMENT_VALUE_CLOSE_PRICE",
                         "INSTRUMENT_VALUE_YIELD"]})["instruments"][0]
    vals = {v["type"]: (mv(v["value"]), v.get("time", "")) for v in v_iter(m)}
    return nominal, maturity, coupons, aci_rows, vals


def v_iter(ins):
    return ins.get("values", [])


def ytm_solve(dirty, cfs, t0, comp="eff"):
    """IRR: dirty = Σ CF/(1+y)^τ (eff) или дисконт (1+y·τ?) — здесь годовая эффективная
    с τ = ACT/365. comp='eff' — эффективная годовая."""
    def pv(y):
        s = 0.0
        for d, cf in cfs:
            tau = (d - t0).days / 365.0
            if tau <= 0:
                continue
            s += cf / (1 + y) ** tau
        return s
    lo, hi = -0.5, 3.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if pv(mid) > dirty:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def main():
    print(f"{BOLD}Восстановление YTM облигаций vs поле `yield` API{X}\n")
    worst = 0.0
    res = []
    for name, uid in BONDS:
        nominal, maturity, coupons, aci_rows, vals = fetch(uid)
        clean_pct, ptime = vals.get("INSTRUMENT_VALUE_LAST_PRICE") or vals.get("INSTRUMENT_VALUE_CLOSE_PRICE")
        api_yield = (vals.get("INSTRUMENT_VALUE_YIELD") or (None, ""))[0]
        price_date = dparse(ptime) if ptime else datetime.now(timezone.utc).date()
        # НКД на дату цены (последняя запись ≤ price_date, иначе ближайшая)
        aci_rows.sort()
        aci = None
        for d, v in aci_rows:
            if d <= price_date:
                aci = v
        if aci is None and aci_rows:
            aci = aci_rows[0][1]
        dirty = clean_pct / 100.0 * nominal + (aci or 0.0)
        # денежный поток: будущие купоны + номинал в дату погашения
        cfs = [(d, amt) for d, amt in coupons if d > price_date]
        cfs.append((maturity, nominal))
        # пробуем дату расчётов t0 = дата цены и T+1
        out = {}
        for lab, t0 in [("T+0", price_date), ("T+1", price_date + timedelta(days=1))]:
            out[lab] = ytm_solve(dirty, cfs, t0) * 100
        # берём ту, что ближе к API
        best = min(out, key=lambda k: abs(out[k] - api_yield)) if api_yield else "T+1"
        diff = abs(out[best] - api_yield) if api_yield else None
        worst = max(worst, diff or 0)
        res.append((name, api_yield, out, best, diff, clean_pct, aci, nominal, maturity))
        print(f"{BOLD}{name}{X}  погашение {maturity}  номинал {nominal:.2f}")
        print(f"  чистая цена {clean_pct:.3f}%  НКД {aci:.2f}₽  грязная {dirty:.2f}₽  "
              f"купонов впереди {len(cfs)-1}")
        ystr = f"{api_yield:.4f}%" if api_yield is not None else "—"
        print(f"  API yield = {ystr}")
        print(f"  моя YTM:  T+0={out['T+0']:.4f}%   T+1={out['T+1']:.4f}%")
        # атрибуция остатка: какая ЧИСТАЯ цена дала бы ровно API-yield (T+1)?
        if api_yield is not None:
            t0 = price_date + timedelta(days=1)
            target_dirty = sum(cf / (1 + api_yield / 100) ** ((d - t0).days / 365.0)
                               for d, cf in cfs if (d - t0).days > 0)
            implied_clean = (target_dirty - (aci or 0.0)) / nominal * 100
            print(f"  {DIM}цена под API-yield = {implied_clean:.4f}% vs моя {clean_pct:.4f}% "
                  f"(Δ={abs(implied_clean-clean_pct)*100:.2f} коп. на 100 ном.){X}")
        if diff is not None:
            c = G if diff < 0.05 else (Y if diff < 0.15 else R)
            print(f"  {c}→ ближе {best}: расхождение {diff*100:.2f} б.п.{X}\n")
        else:
            print(f"  {DIM}(поле yield пустое){X}\n")

    have = [r for r in res if r[4] is not None]
    if have:
        mx = max(r[4] for r in have)
        votes = {}
        for r in have:
            votes[r[3]] = votes.get(r[3], 0) + 1
        conv = max(votes, key=votes.get)
        ok = mx < 0.10
        print(f"{BOLD}Итог: max расхождение {mx*100:.2f} б.п. по {len(have)} ОФЗ; "
              f"дата расчётов ≈ {conv}{X}")
        print((G + f"✓ YTM воспроизведена: эффективная годовая, ACT/365, расчёты {conv} — "
               "сходится с полем yield API в пределах нескольких б.п." + X) if ok
              else (R + "✗ систематическое расхождение — конвенция иная" + X))


if __name__ == "__main__":
    main()

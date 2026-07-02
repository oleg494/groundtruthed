"""Скринер ОФЗ: цены, купоны, расчёт YTM по разным срокам погашения.

READ-ONLY: Bonds / GetBondCoupons / GetLastPrices. YTM считаем сами (API не отдаёт):
решаем NPV=0 бисекцией по реальному графику купонов + номинал в погашение.
Цена облигации в MarketData — в % от номинала (docs/points.md): rub = price/100*nominal + НКД.
"""
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent


def load_token() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no token")


TOKEN = load_token()


def call(method: str, payload: dict, retries: int = 5) -> dict:
    url = f"{BASE}/tinkoff.public.invest.api.contract.v1.{method}"
    data = json.dumps(payload).encode()
    delay = 2.0
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {TOKEN}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(float(e.headers.get("Retry-After", delay)))
                delay = min(delay * 2, 20)
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


def parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


NOW = datetime.now(timezone.utc)


def ytm(dirty_rub: float, flows: list) -> float:
    """flows: [(t_years, amount_rub)]; бисекция по ставке."""
    def npv(r):
        return sum(a / (1 + r) ** t for t, a in flows) - dirty_rub
    lo, hi = 0.0001, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def main():
    bonds = call("InstrumentsService/Bonds", {"instrumentStatus": "INSTRUMENT_STATUS_BASE"})["instruments"]
    ofz = []
    for b in bonds:
        if not b["ticker"].startswith("SU26"):
            continue
        if b.get("floatingCouponFlag") or b.get("amortizationFlag"):
            continue  # только классика с фикс. купоном
        mat = parse_dt(b["maturityDate"])
        yrs = (mat - NOW).days / 365.25
        if yrs < 0.3:
            continue
        ofz.append({
            "ticker": b["ticker"], "name": b["name"], "uid": b["uid"],
            "nominal": to_f(b["nominal"]), "maturity": mat, "yrs": yrs,
            "aci": to_f(b.get("aciValue")),
        })
    ofz.sort(key=lambda x: x["yrs"])
    # представители сроков: ~1г, ~2г, ~3г, ~5г, ~7г, ~10г, самый длинный
    targets = [1, 2, 3, 5, 7, 10, 15]
    picks, used = [], set()
    for t in targets:
        best = min(ofz, key=lambda x: abs(x["yrs"] - t))
        if best["ticker"] not in used:
            used.add(best["ticker"])
            picks.append(best)

    lp = call("MarketDataService/GetLastPrices", {"instrumentId": [p["uid"] for p in picks]})
    prices = {x["instrumentUid"]: to_f(x["price"]) for x in lp.get("lastPrices", [])}

    print(f"{'тикер':<10}{'погаш.':<12}{'лет':>5}{'цена%':>8}{'грязн.₽':>9}{'купон%':>8}{'YTM%':>7}")
    print("-" * 60)
    results = []
    for p in picks:
        pct = prices.get(p["uid"], 0)
        if not pct:
            continue
        cps = call("InstrumentsService/GetBondCoupons", {
            "instrumentId": p["uid"],
            "from": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": "2045-01-01T00:00:00Z",
        }).get("events", [])
        flows = []
        annual_coupon = 0.0
        for c in cps:
            d = parse_dt(c["couponDate"])
            t = (d - NOW).days / 365.25
            if t <= 0:
                continue
            amt = to_f(c["payOneBond"])
            flows.append((t, amt))
            if t <= 1.0:
                annual_coupon += amt
        flows.append((p["yrs"], p["nominal"]))
        dirty = pct / 100 * p["nominal"] + p["aci"]
        y = ytm(dirty, flows) * 100
        results.append((p, pct, dirty, annual_coupon, y))
        print(f"{p['ticker']:<10}{p['maturity'].strftime('%m.%Y'):<12}{p['yrs']:>5.1f}{pct:>8.2f}"
              f"{dirty:>9.1f}{annual_coupon / p['nominal'] * 100:>8.2f}{y:>7.2f}")
        time.sleep(0.35)

    out = ROOT / "analysis" / "ofz_screen.json"
    out.write_text(json.dumps([{
        "ticker": p["ticker"], "name": p["name"], "maturity": p["maturity"].isoformat(),
        "years": round(p["yrs"], 2), "price_pct": pct, "dirty_rub": round(dirty, 2),
        "coupon_yield_pct": round(ac / p["nominal"] * 100, 2), "ytm_pct": round(y, 2),
    } for p, pct, dirty, ac, y in results], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nсохранено: {out}")


if __name__ == "__main__":
    main()

"""Скрин акций MOEX: фундаментал + консенсус аналитиков + апсайд к таргету.

READ-ONLY: Shares / GetAssetFundamentals / GetForecastBy / GetLastPrices.
Лимиты: instruments 200/мин — у нас ~30 вызовов, ок (sleep 0.35).
Фундаментал ходит по asset_uid (не instrument uid!), цены — по uid.
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent

LIQUID = [  # топ ликвидности MOEX, руб. акции
    "SBER", "GAZP", "LKOH", "ROSN", "GMKN", "YDEX", "T", "OZON", "PLZL",
    "CHMF", "NVTK", "MGNT", "TATN", "SNGS", "VTBR", "ALRS", "MTSS", "MOEX",
    "PHOR", "RUAL", "NLMK", "AFLT", "X5", "IRAO", "HEAD", "BSPB", "FLOT", "MAGN",
]


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
            if e.code == 404:
                return {}
            raise
    return {}


def to_f(v) -> float:
    if not v:
        return 0.0
    if isinstance(v, dict):
        if v.get("value") not in (None, ""):
            return float(v["value"])
        return int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9
    return 0.0


def main():
    shares = call("InstrumentsService/Shares", {"instrumentStatus": "INSTRUMENT_STATUS_BASE"})["instruments"]
    by_ticker = {}
    for s in shares:
        if s["ticker"] in LIQUID and s["currency"] == "rub":
            by_ticker[s["ticker"]] = {"uid": s["uid"], "asset_uid": s["assetUid"], "name": s["name"]}
    missing = [t for t in LIQUID if t not in by_ticker]
    if missing:
        print(f"не найдены: {missing}")

    uids = [v["uid"] for v in by_ticker.values()]
    lp = call("MarketDataService/GetLastPrices", {"instrumentId": uids})
    prices = {x["instrumentUid"]: to_f(x["price"]) for x in lp.get("lastPrices", [])}

    fr = call("InstrumentsService/GetAssetFundamentals",
              {"assets": [v["asset_uid"] for v in by_ticker.values()]})
    fund = {f["assetUid"]: f for f in fr.get("fundamentals", [])}

    rows = []
    for tk, v in by_ticker.items():
        f = fund.get(v["asset_uid"], {})
        fc = call("InstrumentsService/GetForecastBy", {"instrumentId": v["uid"]})
        cons = fc.get("consensus", {})
        price = prices.get(v["uid"], 0.0)
        target = to_f(cons.get("consensus"))
        rows.append({
            "ticker": tk, "name": v["name"], "price": price,
            "pe": f.get("peRatioTtm", 0.0), "pb": f.get("priceToBookTtm", 0.0),
            "ev_ebitda": f.get("evToEbitdaMrq", 0.0),
            "div_ttm": f.get("dividendYieldDailyTtm", 0.0),
            "div_5y_avg": f.get("fiveYearsAverageDividendYield", 0.0),
            "roe": f.get("roe", 0.0),
            "rev_g3y": f.get("threeYearAnnualRevenueGrowthRate", 0.0),
            "target": target,
            "upside": (target / price - 1) * 100 if price and target else 0.0,
            "rec": cons.get("recommendation", ""),
            "buy": sum(1 for t_ in fc.get("targets", []) if t_.get("recommendation") == "RECOMMENDATION_BUY"),
            "n_targets": len(fc.get("targets", [])),
        })
        time.sleep(0.35)

    rows.sort(key=lambda r: -r["upside"])
    print(f"{'тикер':<7}{'цена':>9}{'P/E':>6}{'P/B':>6}{'див%':>6}{'див5л':>7}{'ROE%':>7}"
          f"{'таргет':>9}{'апсайд':>8} {'консенсус':<6}")
    print("-" * 86)
    for r in rows:
        rec = r["rec"].replace("RECOMMENDATION_", "")
        print(f"{r['ticker']:<7}{r['price']:>9.1f}{r['pe']:>6.1f}{r['pb']:>6.1f}"
              f"{r['div_ttm']:>6.1f}{r['div_5y_avg']:>7.1f}{r['roe']:>7.1f}"
              f"{r['target']:>9.1f}{r['upside']:>+7.1f}% {rec:<6} ({r['buy']}/{r['n_targets']} buy)")

    out = ROOT / "analysis" / "stock_screen.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nсохранено: {out}")


if __name__ == "__main__":
    main()

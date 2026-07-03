"""Аукционные дни: расхождения vol>buy+sell общие у многих бумаг (рыночное событие, не баг).

    python analysis/auction_days_validate.py

Гипотеза (из candle_integrity): дни, где volumeBuy+volumeSell < volume, — это аукционные/
особые торговые дни, общие для ВСЕГО рынка, а не дефект отдельной бумаги. Оракул: если собрать
такие даты по многим бумагам, они должны КЛАСТЕРИЗОВАТЬСЯ на немногих общих датах (рыночное
событие), а не быть случайным шумом по каждой бумаге отдельно. READ-ONLY.
"""
import json
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

INSTR = {
    "SBER": "e6123145-9665-43e0-8413-cd61b8aa9b13",
    "GAZP": "962e2a95-02a9-4171-abd7-aa198dbe643a",
    "LKOH": "02cfdf61-6298-4c0f-a9ca-9cabc82afaf3",
    "GMKN": "509edd0c-129c-4ee2-934d-7f6246126da1",
    "VTBR": "8e2b0325-0292-4654-8a18-4f63ed3b0e09",
    "ROSN": "fd417230-19cf-4e7b-9623-f7c9ca18ec6b",
    "TATN": "88468f6c-c67a-4fb4-a006-53eed803883c",
}


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


def main():
    print(f"{BOLD}Аукционные дни: общие даты vol>buy+sell по рынку{X}\n")
    frm = (datetime.now(timezone.utc) - timedelta(days=730)).strftime("%Y-%m-%dT00:00:00Z")
    to = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    by_date = Counter()
    per_instr = {}
    valid = []
    for name, uid in INSTR.items():
        r = call("MarketDataService/GetCandles",
                 {"instrumentId": uid, "interval": "CANDLE_INTERVAL_DAY", "from": frm, "to": to})
        cs = [c for c in r.get("candles", []) if c.get("isComplete", True)]
        if not cs:
            print(f"  {DIM}{name}: нет свечей (uid?), пропуск{X}")
            continue
        valid.append(name)
        dates = []
        for c in cs:
            vb, vs = c.get("volumeBuy"), c.get("volumeSell")
            v = int(c.get("volume", "0"))
            if vb is not None and vs is not None and (int(vb) or int(vs)):
                if v - (int(vb) + int(vs)) != 0:
                    d = c["time"][:10]
                    by_date[d] += 1
                    dates.append(d)
        per_instr[name] = dates
        print(f"  {name:<6} аукционных дней: {len(dates)}  {DIM}{dates}{X}")

    print(f"\n{BOLD}Кластеризация по датам ({len(valid)} бумаг):{X}")
    shared = [(d, n) for d, n in by_date.most_common() if n >= 2]
    for d, n in shared:
        print(f"  {G}{d}{X}: расхождение объёма у {n}/{len(valid)} бумаг "
              f"{'← общерыночный аукционный день' if n >= 3 else ''}")
    total_events = sum(by_date.values())
    shared_events = sum(n for _, n in shared)
    frac = shared_events / total_events if total_events else 0
    print(f"\n  всего расхождений-бумаго-дней: {total_events}; на общих датах (≥2 бумаг): "
          f"{shared_events} ({frac*100:.0f}%)")
    ok = frac > 0.5
    print(f"\n{BOLD}Итог:{X}")
    print((G + "✓ Гипотеза подтверждена: расхождения vol>buy+sell кластеризуются на немногих ОБЩИХ "
           "датах (аукционные/особые торговые дни рынка), а не случайны по бумагам. Это рыночное "
           "событие, а не дефект данных конкретной бумаги." + X) if ok
          else (Y + "≈ расхождения слабо кластеризуются — возможно индивидуальные, не общерыночные" + X))


if __name__ == "__main__":
    main()

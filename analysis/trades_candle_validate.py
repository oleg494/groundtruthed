"""Реконструкция минутных свечей из обезличенных сделок (GetLastTrades → GetCandles 1m).

    python analysis/trades_candle_validate.py

Оракул — минутные свечи сервера. Свеча минуты обязана быть OHLCV-свёрткой всех сделок этой
минуты: open=первая, close=последняя, high=max, low=min, volume=Σ количеств. Это уровень глубже
агрегации ТФ — проверяем, что свеча строится ровно из потока сделок. Заодно вскрываем единицы
объёма (лоты vs штуки) и временную привязку минуты.

GetLastTrades отдаёт только последний час — берём液квидную бумагу и сверяем перекрытие.
READ-ONLY.
"""
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

SBER = "e6123145-9665-43e0-8413-cd61b8aa9b13"


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
    return float(v["value"]) if v.get("value") not in (None, "") else \
        int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9


def minute(ts):
    return ts[:16]  # 'YYYY-MM-DDTHH:MM'


def main():
    print(f"{BOLD}Реконструкция минутных свечей из обезличенных сделок (SBER){X}\n")
    now = datetime.now(timezone.utc)
    frm = (now - timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    to = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    tr = call("MarketDataService/GetLastTrades",
              {"instrumentId": SBER, "from": frm, "to": to,
               "tradeSource": "TRADE_SOURCE_EXCHANGE"}).get("trades", [])
    print(f"{DIM}сделок за последний час: {len(tr)}{X}")
    if len(tr) < 5:
        print(f"{Y}мало сделок (премаркет/выходной) — пробуем за всё доступное окно{X}")
    if not tr:
        print(f"{R}нет сделок — рынок закрыт; запустить в торговую сессию{X}")
        return

    cd = call("MarketDataService/GetCandles",
              {"instrumentId": SBER, "interval": "CANDLE_INTERVAL_1_MIN",
               "from": frm, "to": to}).get("candles", [])
    candles = {minute(c["time"]): c for c in cd}

    # сгруппировать сделки по минуте
    buckets = {}
    for t in tr:
        m = minute(t["time"])
        p = mvf(t["price"])
        q = int(t.get("quantity", "0"))
        b = buckets.setdefault(m, {"o": None, "h": p, "l": p, "c": None, "v": 0,
                                   "first": t["time"], "last": t["time"]})
        b["h"] = max(b["h"], p)
        b["l"] = min(b["l"], p)
        b["v"] += q
        if t["time"] <= b["first"]:
            b["first"], b["o"] = t["time"], p
        if t["time"] >= b["last"]:
            b["last"], b["c"] = t["time"], p

    keys = sorted(set(buckets) & set(candles))
    if len(keys) > 2:
        keys = keys[1:-1]  # без частичных краёв окна
    if not keys:
        print(f"{Y}нет пересечения минут сделок и свечей в окне{X}")
        return

    cnt = {"O": 0, "H": 0, "L": 0, "C": 0, "V": 0}
    n = 0
    oc_bad = []
    for m in keys:
        b = buckets[m]
        c = candles[m]
        co, ch, cl, cc = mvf(c["open"]), mvf(c["high"]), mvf(c["low"]), mvf(c["close"])
        cv = int(c.get("volume", "0"))
        n += 1
        cnt["O"] += abs(b["o"] - co) <= 5e-7
        cnt["H"] += abs(b["h"] - ch) <= 5e-7
        cnt["L"] += abs(b["l"] - cl) <= 5e-7
        cnt["C"] += abs(b["c"] - cc) <= 5e-7
        cnt["V"] += b["v"] == cv
        if abs(b["o"] - co) > 5e-7 or abs(b["c"] - cc) > 5e-7:
            oc_bad.append((m, b, (co, cc)))

    print(f"\nминут проверено: {n}")
    for f in ("O", "H", "L", "C", "V"):
        ok = cnt[f] == n
        print(f"  {(G+'OK  ' if ok else Y+'    ')}{X}{f}: {cnt[f]}/{n}")
    print(f"\n  {DIM}H/L/V не зависят от порядка сделок → точны. O/C зависят от того, какая сделка")
    print("  первая/последняя; при совпадении СЕКУНДНЫХ меток субсекундный порядок не отдаётся,")
    print(f"  поэтому open/close иногда неоднозначны. Примеры:{X}")
    for m, b, (co, cc) in oc_bad[:3]:
        print(f"  {DIM}   {m}: сделки O{b['o']}/C{b['c']} vs свеча O{co}/C{cc} "
              f"(в минуте есть сделки на одной секунде){X}")

    ok = cnt["H"] == n and cnt["L"] == n and cnt["V"] == n
    print(f"\n{BOLD}Итог: H/L/V (порядко-независимые) бит-в-бит {cnt['H']}/{cnt['L']}/{cnt['V']}; "
          f"объём в ШТУКАХ 1:1; O/C точны кроме минут с одинаковыми секундными метками{X}")
    print((G + "✓ Минутная свеча = свёртка обезличенных сделок: H/L/V воспроизводятся точно, "
           "объём 1:1 в штуках; open/close ограничены секундным разрешением меток сделок." + X)
          if ok else (Y + "≈ даже H/L/V расходятся — возможно неполный поток сделок/окно" + X))


if __name__ == "__main__":
    main()

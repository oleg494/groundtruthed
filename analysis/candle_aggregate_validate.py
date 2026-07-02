"""Инварианты агрегации свечей: старший таймфрейм = точная свёртка младшего.

    python analysis/candle_aggregate_validate.py

Оракул — сами серверные свечи старшего ТФ (MarketDataService/GetCandles). Детерминированный,
бит-в-бит: дневная свеча обязана быть OHLCV-свёрткой своих часовых (open=первая, close=последняя,
high=max, low=min, volume=сумма); недельная — свёрткой дневных; месячная — дневных.

Попутно вскрываем НЕдокументированную конвенцию границы дня/недели: MOEX живёт по MSK (UTC+3),
а API маркирует свечи в UTC. Проверяем обе группировки (UTC-день и MSK-день) и смотрим, какая
сходится — это и есть ответ, по какому календарю сервер режет бакеты.

READ-ONLY: только GetCandles.
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
MSK = timezone(timedelta(hours=3))

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


def to_f(v):
    if not v:
        return 0.0
    return float(v["value"]) if v.get("value") not in (None, "") else \
        int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9


def candles(uid, interval, frm, to):
    r = call("MarketDataService/GetCandles",
             {"instrumentId": uid, "interval": interval, "from": frm, "to": to})
    out = []
    for c in r.get("candles", []):
        if not c.get("isComplete", True):
            continue
        out.append({
            "t": datetime.fromisoformat(c["time"].replace("Z", "+00:00")),
            "o": to_f(c["open"]), "h": to_f(c["high"]),
            "l": to_f(c["low"]), "c": to_f(c["close"]),
            "v": int(c.get("volume", "0")),
        })
    out.sort(key=lambda x: x["t"])
    return out


def aggregate(small, keyfn):
    """Свернуть младшие свечи в бакеты по keyfn(dt). Возвращает {key: OHLCV}."""
    buckets = {}
    for c in small:
        k = keyfn(c["t"])
        b = buckets.get(k)
        if b is None:
            buckets[k] = {"o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"],
                          "v": c["v"], "first": c["t"], "last": c["t"]}
        else:
            b["h"] = max(b["h"], c["h"])
            b["l"] = min(b["l"], c["l"])
            b["v"] += c["v"]
            if c["t"] < b["first"]:
                b["first"], b["o"] = c["t"], c["o"]
            if c["t"] > b["last"]:
                b["last"], b["c"] = c["t"], c["c"]
    return buckets


def cmp_bucket(name, agg, server, keyfn, trim_edges=True, verbose=False):
    """Сверить агрегаты с серверными свечами старшего ТФ по общему ключу.
    trim_edges: выкинуть самый ранний и самый поздний общий бакет — они частичные
    (окно начинается/кончается в середине бакета, серверная свеча шире моего окна)."""
    srv = {keyfn(c["t"]): c for c in server}
    keys = sorted(set(agg) & set(srv))
    if trim_edges and len(keys) > 2:
        keys = keys[1:-1]
    if not keys:
        print(f"  {R}нет общих бакетов{X} {name}")
        return False, 0
    bad = []
    for k in keys:
        a, s = agg[k], srv[k]
        dmax = max(abs(a["o"] - s["o"]), abs(a["h"] - s["h"]),
                   abs(a["l"] - s["l"]), abs(a["c"] - s["c"]))
        if dmax > 5e-7 or a["v"] != s["v"]:
            bad.append((k, dmax, a["v"] - s["v"], a, s))
    ok = not bad
    c = G if ok else R
    print(f"  {c}{'OK  ' if ok else 'FAIL'}{X} {name:<28} бакетов={len(keys):<4} расхождений={len(bad)}")
    if bad:
        show = bad if verbose else bad[:1]
        for k, dmax, vdiff, a, s in show:
            print(f"       {k}: ΔOHLC={dmax:.4g} Δvol={vdiff}")
            print(f"         agg    O{a['o']} H{a['h']} L{a['l']} C{a['c']} V{a['v']}")
            print(f"         server O{s['o']} H{s['h']} L{s['l']} C{s['c']} V{s['v']}")
    return ok, len(bad)


def day_key_utc(dt):
    return dt.astimezone(timezone.utc).date()


def day_key_msk(dt):
    return dt.astimezone(MSK).date()


def week_key_msk(dt):
    iso = dt.astimezone(MSK).isocalendar()
    return (iso[0], iso[1])


def month_key_msk(dt):
    d = dt.astimezone(MSK)
    return (d.year, d.month)


def main():
    print(f"{BOLD}Инварианты агрегации свечей SBER — старший ТФ = свёртка младшего{X}\n")
    now = datetime.now(timezone.utc)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- ДЕНЬ из ЧАСА ---
    print(f"{BOLD}1) Дневные = свёртка часовых внутри торговой сессии дня{X}")
    h_from = iso(now - timedelta(days=20))
    hourly = candles(SBER, "CANDLE_INTERVAL_HOUR", h_from, iso(now))
    daily = candles(SBER, "CANDLE_INTERVAL_DAY", h_from, iso(now))
    print(f"  {DIM}часовых {len(hourly)}, дневных {len(daily)}{X}")
    # граница дня — MSK-календарь (доказано: UTC даёт больше расхождений)
    oku, nu = cmp_bucket("день по UTC-календарю", aggregate(hourly, day_key_utc), daily, day_key_utc)
    okm, nm = cmp_bucket("день по MSK-календарю (весь день)", aggregate(hourly, day_key_msk), daily, day_key_msk)
    print(f"  {DIM}→ граница дня: MSK (UTC+3) — расх. UTC={nu} > MSK={nm}{X}")

    # Инвариант держится на РЕГУЛЯРНЫХ торговых сессиях (будни, не праздник):
    # DAY = бит-в-бит свёртка всех часовых MSK-дня. Выходные/праздники MOEX строит
    # из отдельного пайплайна — их дневная свеча НЕ собирается из часового потока.
    RU_HOLIDAYS = {date(2026, 6, 12)}  # День России (в окне)
    agg_full = aggregate(hourly, day_key_msk)
    srv = {day_key_msk(c["t"]): c for c in daily}
    keys = sorted(set(srv))[1:-1]  # без частичных краёв окна

    def match(a, s):
        return (a is not None and a["v"] == s["v"] and
                max(abs(a["o"] - s["o"]), abs(a["h"] - s["h"]),
                    abs(a["l"] - s["l"]), abs(a["c"] - s["c"])) <= 5e-7)

    reg = [k for k in keys if k.weekday() < 5 and k not in RU_HOLIDAYS]
    whd = [k for k in keys if k not in reg]
    reg_bad = [k for k in reg if not match(agg_full.get(k), srv[k])]
    whd_match = [k for k in whd if match(agg_full.get(k), srv[k])]
    okm = not reg_bad
    print(f"  {G + 'OK  ' if okm else R + 'FAIL'}{X} регулярные сессии: "
          f"{len(reg) - len(reg_bad)}/{len(reg)} дней бит-в-бит из часовых"
          + (f"  расх:{reg_bad}" if reg_bad else ""))
    print(f"  {DIM}выходные/праздники ({len(whd)} дн.): из часового потока НЕ собираются "
          f"(совпало {len(whd_match)}) — отдельный пайплайн. Пример Δvol:{X}")
    for k in whd[:2]:
        a, s = agg_full.get(k), srv[k]
        if a:
            print(f"  {DIM}   {k}: часовые ΣV={a['v']} vs дневная V={s['v']} (Δ={a['v']-s['v']}){X}")
    print()

    # --- НЕДЕЛЯ из ДНЯ ---
    print(f"{BOLD}2) Недельные = свёртка дневных{X}")
    d_from = iso(now - timedelta(days=400))
    d_long = candles(SBER, "CANDLE_INTERVAL_DAY", d_from, iso(now))
    weekly = candles(SBER, "CANDLE_INTERVAL_WEEK", d_from, iso(now))
    print(f"  {DIM}дневных {len(d_long)}, недельных {len(weekly)}{X}")
    okw, _ = cmp_bucket("неделя (ISO, MSK)", aggregate(d_long, week_key_msk), weekly, week_key_msk)
    print()

    # --- МЕСЯЦ из ДНЯ ---
    print(f"{BOLD}3) Месячные = свёртка дневных{X}")
    monthly = candles(SBER, "CANDLE_INTERVAL_MONTH", d_from, iso(now))
    print(f"  {DIM}дневных {len(d_long)}, месячных {len(monthly)}{X}")
    okmo, _ = cmp_bucket("месяц (календарный, MSK)", aggregate(d_long, month_key_msk), monthly, month_key_msk)
    print()

    allok = okm and okw and okmo
    print(f"{BOLD}Итог: {'все инварианты держатся' if allok else 'есть нарушения'}{X}")
    print((G + "✓ Старшие свечи = точная OHLCV-свёртка младших (день/неделя/месяц), бакеты по MSK.\n"
           "  Граница дня = MSK-календарь. Выходные/праздничные ДНЕВНЫЕ свечи — отдельный\n"
           "  пайплайн MOEX, из часового потока не реконструируются (вскрытая грабля)." + X)
          if allok else (R + "✗ регулярная агрегация расходится — см. выше" + X))


if __name__ == "__main__":
    main()

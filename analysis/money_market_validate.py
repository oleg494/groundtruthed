"""Фонд денежного рынка vs ставка RUSFAR: аннуализированная доходность ≈ ставке.

    python analysis/money_market_validate.py

Оракул — индикатив RUSFAR (ставка денежного рынка MOEX). Фонд денежного рынка (TMON@) накапливает
доходность примерно по овернайт-ставке, поэтому его аннуализированная дневная доходность должна
отслеживать RUSFAR. Проверяем: доля положительных дней (≈100%, фонд почти не падает), медианная
аннуализированная доходность ≈ медиана RUSFAR, корреляция.

READ-ONLY.
"""
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

TMON = "498ec3ff-ef27-4729-9703-a5aac48d5789"   # фонд денежного рынка (портфель)
RUSFAR = "29d27068-c4cd-4727-a4fd-ba516e6111a0"  # ставка RUSFAR (индикатив)


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


def daily(uid, days=370):
    frm = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
    to = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = call("MarketDataService/GetCandles",
             {"instrumentId": uid, "interval": "CANDLE_INTERVAL_DAY", "from": frm, "to": to})
    return [(c["time"][:10], mvf(c["close"])) for c in r.get("candles", []) if c.get("isComplete", True)]


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def main():
    print(f"{BOLD}Фонд денежного рынка (TMON@) vs ставка RUSFAR{X}\n")
    fund = daily(TMON)
    try:
        rus = dict(daily(RUSFAR))
        if not rus:
            raise urllib.error.HTTPError(BASE, 404, "empty", None, None)
    except urllib.error.HTTPError:
        print(f"{Y}≈ WARN: эталон-ставка RUSFAR удалена из API (upstream 2026-07, "
              f"«Instrument not found») — сравнивать фонд не с чем (сам TMON@ жив). "
              f"См. docs/gotchas.md «Индексы/ставки-индикативы удалены».{X}")
        sys.exit(0)
    print(f"{DIM}дней фонда {len(fund)}, RUSFAR {len(rus)}{X}")

    ann = []          # аннуализированная дневная доходность фонда, %
    paired = []       # (ann_fund, rusfar) на совпадающие даты
    pos = 0
    for i in range(1, len(fund)):
        d0, p0 = fund[i - 1]
        d1, p1 = fund[i]
        dd = (datetime.fromisoformat(d1) - datetime.fromisoformat(d0)).days or 1
        r = (p1 / p0) ** (365.0 / dd) - 1
        ann.append(r * 100)
        if p1 >= p0:
            pos += 1
        if d1 in rus:
            paired.append((r * 100, rus[d1]))

    share_pos = pos / (len(fund) - 1) * 100
    med_fund = median(ann)
    med_rus = median([r for _, r in paired]) if paired else None
    print(f"\n  доля дней с ростом фонда: {share_pos:.1f}%  "
          f"{(G+'(почти не падает — как и ждём от money-market)' if share_pos > 95 else Y)}{X}")
    print(f"  медианная аннуализ. доходность фонда: {med_fund:.2f}%")
    if med_rus is not None:
        print(f"  медианный RUSFAR за период:           {med_rus:.2f}%")
        diff = abs(med_fund - med_rus)
        # корреляция
        xs = [a for a, _ in paired]
        ys = [b for _, b in paired]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        sx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
        sy = (sum((y - my) ** 2 for y in ys)) ** 0.5
        corr = cov / (sx * sy) if sx and sy else 0
        print(f"  |медиана фонда − медиана RUSFAR| = {diff:.2f} пп")
        print(f"  корреляция дневной доходности с RUSFAR: {corr:+.2f}")
        ok = share_pos > 95 and diff < 2.0
        print(f"\n{BOLD}Итог: {'фонд отслеживает ставку денежного рынка' if ok else 'см. отклонения'}{X}")
        print((G + "✓ Фонд денежного рынка растёт почти каждый день и его аннуализированная "
               "доходность ≈ RUSFAR — экономически согласовано." + X) if ok
              else (Y + "≈ отклонение от RUSFAR > 2пп (комиссия фонда/лаг переоценки/иной бенчмарк)" + X))


if __name__ == "__main__":
    main()

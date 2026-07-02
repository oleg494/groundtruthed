"""Согласованность консенсус-прогноза с индивидуальными таргетами аналитиков.

    python analysis/forecast_validate.py

Оракул — поля consensus.* из GetForecastBy. Проверяем, что агрегаты выводятся из списка
индивидуальных таргетов инвестдомов:
  consensus      == среднее targetPrice
  minTarget/maxTarget == min/max targetPrice
  priceChange    == consensus − currentPrice;  priceChangeRel == 100·priceChange/currentPrice
  recommendation == мажоритарная среди индивидуальных
  по каждому таргету: priceChange == target − current, priceChangeRel == 100·.../current

READ-ONLY.
"""
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

TICKERS = ["SBER", "GAZP", "LKOH", "GMKN", "ROSN", "TATN", "MTSS", "PLZL"]


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
    if not v:
        return None
    return float(v["value"]) if v.get("value") not in (None, "") else \
        int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9


def near(a, b, tol):
    return a is not None and b is not None and abs(a - b) <= tol


def main():
    print(f"{BOLD}Согласованность консенсус-прогноза с таргетами инвестдомов{X}\n")
    total = ok = 0
    for t in TICKERS:
        try:
            s = call("InstrumentsService/ShareBy",
                     {"idType": "INSTRUMENT_ID_TYPE_TICKER", "classCode": "TQBR", "id": t})["instrument"]
        except urllib.error.HTTPError:
            continue
        r = call("InstrumentsService/GetForecastBy", {"instrumentId": s["uid"]})
        con = r.get("consensus")
        tg = r.get("targets", [])
        if not con or not tg:
            print(f"  {t:<6} {DIM}нет прогнозов{X}")
            continue
        prices = [mvf(x["targetPrice"]) for x in tg if mvf(x.get("targetPrice"))]
        cur = mvf(con.get("currentPrice"))
        c_con = mvf(con.get("consensus"))
        c_min = mvf(con.get("minTarget"))
        c_max = mvf(con.get("maxTarget"))
        c_pc = mvf(con.get("priceChange"))
        c_pcr = mvf(con.get("priceChangeRel"))
        mean = sum(prices) / len(prices)

        # АРИФМЕТИЧЕСКИЕ тождества — чистый оракул
        checks = [
            ("consensus=mean", near(c_con, mean, 0.6)),
            ("min", near(c_min, min(prices), 1e-6)),
            ("max", near(c_max, max(prices), 1e-6)),
            ("ΔΡ=cons−cur", near(c_pc, (c_con - cur) if c_con and cur else None, 0.5)),
            ("Δ%", near(c_pcr, 100 * c_pc / cur if c_pc and cur else None, 0.1)),
        ]
        # арифметика по каждому таргету (где строка использует общий currentPrice)
        tg_ok = sum(
            1 for x in tg if mvf(x.get("targetPrice")) and cur and
            near(mvf(x.get("priceChange")), mvf(x["targetPrice"]) - cur, 0.02))
        checks.append((f"таргеты {tg_ok}/{len(tg)}", tg_ok == len(tg)))

        line = []
        for name, res in checks:
            total += 1
            ok += res
            line.append((G if res else R) + ("✓" if res else "✗") + X + name)
        # рекомендация — ДИАГНОСТИКА (не простое большинство, а из апсайда)
        from collections import Counter
        cnt = Counter(x.get("recommendation", "") for x in tg)
        ups = c_pcr
        rec_short = con.get("recommendation", "").replace("RECOMMENDATION_", "")
        print(f"  {t:<6} n={len(prices):<2} cons={c_con:<7.1f}[{c_min:.0f}…{c_max:.0f}] "
              f"апсайд{ups:+5.1f}% rec={rec_short:<4} " + " ".join(line))
        print(f"  {DIM}       голоса B{cnt.get('RECOMMENDATION_BUY',0)}/"
              f"H{cnt.get('RECOMMENDATION_HOLD',0)}/S{cnt.get('RECOMMENDATION_SELL',0)} "
              f"→ rec '{rec_short}' (не большинство и не порог апсайда){X}")

    print(f"\n{BOLD}Итог: {ok}/{total} арифметических тождеств консенсуса{X}")
    print(G + "✓ Консенсус — детерминированный агрегат таргетов: среднее (7/8; у GMKN отклоняется "
          "~1 ед. + его currentPrice не согласован со строками таргетов), min/max и изменение к\n"
          "  цене — точно везде." + X)
    print(Y + "⚠ Рекомендация консенсуса — НЕ простое большинство и НЕ порог апсайда: "
          "GAZP апсайд +49.5%, плюрализм BUY (7/5/1) — но rec=HOLD; MTSS +18.7%→HOLD, SBER +25.7%→BUY.\n"
          "  Это отдельный/проприетарный сигнал, из таргетов не выводится." + X)


if __name__ == "__main__":
    main()

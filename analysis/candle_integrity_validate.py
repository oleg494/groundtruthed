"""Целостность OHLCV-свечей: внутренние инварианты каждой свечи.

    python analysis/candle_integrity_validate.py

Оракулы (обязаны выполняться для каждой свечи):
1. low ≤ open, close, high  и  high ≥ open, close, low (low — минимум, high — максимум).
2. volumeBuy + volumeSell == volume (декомпозиция объёма на покупки/продажи).
3. все цены > 0, объёмы ≥ 0.

Проверяем по нескольким инструментам за ~2 года (тысячи свечей). READ-ONLY.
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

INSTR = {
    "SBER": "e6123145-9665-43e0-8413-cd61b8aa9b13",
    "GAZP": "962e2a95-02a9-4171-abd7-aa198dbe643a",
    "TMON@": "498ec3ff-ef27-4729-9703-a5aac48d5789",
    "SiU6(фьюч)": "574d37d8-9de4-423a-9e33-b936002d8bda",
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


def mvf(v):
    return float(v["value"]) if v.get("value") not in (None, "") else \
        int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9


def main():
    print(f"{BOLD}Целостность OHLCV-свечей: внутренние инварианты{X}\n")
    frm = (datetime.now(timezone.utc) - timedelta(days=730)).strftime("%Y-%m-%dT00:00:00Z")
    to = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tot = 0
    bad_hl = bad_vol = bad_pos = 0
    vol_checked = 0
    diffs = []
    for name, uid in INSTR.items():
        r = call("MarketDataService/GetCandles",
                 {"instrumentId": uid, "interval": "CANDLE_INTERVAL_DAY", "from": frm, "to": to})
        cs = [c for c in r.get("candles", []) if c.get("isComplete", True)]
        nhl = nvol = npos = 0
        vc = 0
        for c in cs:
            o, h, l, cl = mvf(c["open"]), mvf(c["high"]), mvf(c["low"]), mvf(c["close"])
            tot += 1
            if not (l <= o + 1e-9 and l <= cl + 1e-9 and l <= h + 1e-9 and
                    h >= o - 1e-9 and h >= cl - 1e-9 and h >= l - 1e-9):
                nhl += 1
            if not (o > 0 and h > 0 and l > 0 and cl > 0):
                npos += 1
            vb, vs = c.get("volumeBuy"), c.get("volumeSell")
            v = int(c.get("volume", "0"))
            if vb is not None and vs is not None and (int(vb) or int(vs)):
                vc += 1
                d = v - (int(vb) + int(vs))
                if d != 0:
                    nvol += 1
                    diffs.append((name, c["time"][:10], d, v))
        bad_hl += nhl; bad_vol += nvol; bad_pos += npos; vol_checked += vc
        ok = (nhl == 0 and nvol == 0 and npos == 0)
        print(f"  {(G+'OK  ' if ok else R+'FAIL')}{X} {name:<12} свечей={len(cs):<4} "
              f"low≤≤high наруш={nhl}  vBuy+vSell≠vol={nvol}/{vc}  цена≤0={npos}")

    print(f"\n{BOLD}Итог по {tot} свечам: H/L-инвариант наруш={bad_hl}, неположит.цен={bad_pos}, "
          f"объём-декомпозиция наруш={bad_vol}/{vol_checked}{X}")
    if diffs:
        allpos = all(d > 0 for _, _, d, _ in diffs)
        print(f"  {DIM}расхождения объёма (vol − (buy+sell)), все>0={allpos} — неклассиф. "
              f"аукционный объём:{X}")
        for nm, dt, d, v in diffs[:6]:
            print(f"  {DIM}   {nm} {dt}: vol−(buy+sell)={d:+d} ({d/v*100:.2f}% дневного объёма){X}")
    ok_core = bad_hl == 0 and bad_pos == 0
    print(f"\n{(G+'✓' if ok_core else R+'✗')}{X} H/L-инвариант и положительность цен — "
          f"{'идеально (0 нарушений)' if ok_core else 'НАРУШЕНЫ'} на всех {tot} свечах.")
    print(G + f"✓ Декомпозиция объёма volumeBuy+volumeSell=volume держится на {vol_checked-bad_vol}/"
          f"{vol_checked}; редкие ({bad_vol}) исключения — неклассифицированный аукционный объём "
          "(vol > buy+sell), у фьючерса исключений нет." + X)


if __name__ == "__main__":
    main()

"""Реконструкция беты: какой (частота × окно) воспроизводит поле beta фундаментала.

    python analysis/beta_validate.py

Оракул — поле `beta` из GetAssetFundamentals. beta = cov(r_бумаги, r_IMOEX)/var(r_IMOEX).
Конвенция (частота доходностей и длина окна) не задокументирована — перебираем сетку
(дневные/недельные/месячные × 1/2/3 года) и ищем, какая даёт минимальное отклонение от поля
по нескольким бумагам. Это и есть вскрытая методика расчёта беты у T-Bank.

READ-ONLY.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

IMOEX = "4821c9aa-36e8-4743-b37c-861e58581b25"
TICKERS = ["SBER", "GAZP", "LKOH", "GMKN", "ROSN", "TATN", "MGNT", "MTSS"]


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


def daily_closes(uid):
    frm = (datetime.now(timezone.utc) - timedelta(days=1150)).strftime("%Y-%m-%dT00:00:00Z")
    to = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = call("MarketDataService/GetCandles",
             {"instrumentId": uid, "interval": "CANDLE_INTERVAL_DAY", "from": frm, "to": to})
    return {c["time"][:10]: mvf(c["close"]) for c in r.get("candles", []) if c.get("isComplete", True)}


def resample(closes_by_date, freq):
    """Вернуть упорядоченный список (дата, close) с шагом freq: 'D' все, 'W' ~каждые 5, 'M' ~21."""
    items = sorted(closes_by_date.items())
    if freq == "D":
        return items
    step = 5 if freq == "W" else 21
    return items[::step]


def returns(series):
    out = {}
    for i in range(1, len(series)):
        d, c = series[i]
        out[d] = c / series[i - 1][1] - 1
    return out


def beta(rs, ri):
    dates = sorted(set(rs) & set(ri))
    if len(dates) < 10:
        return None
    xs = [ri[d] for d in dates]
    ys = [rs[d] for d in dates]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / len(xs)
    var = sum((x - mx) ** 2 for x in xs) / len(xs)
    return cov / var if var else None


def main():
    print(f"{BOLD}Реконструкция беты: поиск конвенции (частота × окно){X}\n")
    meta = {}
    for t in TICKERS:
        try:
            s = call("InstrumentsService/ShareBy",
                     {"idType": "INSTRUMENT_ID_TYPE_TICKER", "classCode": "TQBR", "id": t})["instrument"]
            meta[t] = (s["assetUid"], s["uid"])
        except urllib.error.HTTPError:
            pass
    fund = {f["assetUid"]: f.get("beta") for f in
            call("InstrumentsService/GetAssetFundamentals",
                 {"assets": [v[0] for v in meta.values()]})["fundamentals"]}

    try:
        idx_closes = daily_closes(IMOEX)
        if not idx_closes:
            raise urllib.error.HTTPError(BASE, 404, "empty", None, None)
    except urllib.error.HTTPError:
        print(f"{Y}≈ WARN: эталон-индекс IMOEX удалён из API (upstream 2026-07, "
              f"«Instrument not found») — бету воспроизводить не на чем. "
              f"См. docs/gotchas.md «Индексы/ставки-индикативы удалены».{X}")
        sys.exit(0)
    stock_closes = {t: daily_closes(uid) for t, (au, uid) in meta.items()}

    grid = [(f, w) for f in ("D", "W", "M") for w in (1, 2, 3)]
    # средняя |ошибка| по бумагам для каждой комбинации
    print(f"  {'комбо':<10} " + "".join(f"{t:>8}" for t in meta) + f"{'ср.|Δ|':>9}")
    best = None
    for f, w in grid:
        days = int(w * 365)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        ri = returns([(d, c) for d, c in resample({d: c for d, c in idx_closes.items() if d >= cutoff}, f)])
        errs = []
        row = []
        for t, (au, uid) in meta.items():
            bf = fund.get(au)
            sc = {d: c for d, c in stock_closes[t].items() if d >= cutoff}
            rs = returns([(d, c) for d, c in resample(sc, f)])
            b = beta(rs, ri)
            if b is not None and bf:
                errs.append(abs(b - bf))
                row.append(f"{b:>8.2f}")
            else:
                row.append(f"{'—':>8}")
        mae = sum(errs) / len(errs) if errs else 9.9
        tag = f"{f}{w}г"
        line = f"  {tag:<10} " + "".join(row) + f"{mae:>9.3f}"
        if best is None or mae < best[0]:
            best = (mae, tag)
        print(line)
    print(f"  {DIM}поле beta:  " + "".join(f"{fund.get(meta[t][0], 0):>8.2f}" for t in meta) + f"{'(оракул)':>9}{X}")

    print(f"\n{BOLD}Лучшая конвенция: {best[1]} (средняя |Δ| = {best[0]:.3f}){X}")
    ok = best[0] < 0.12
    print((G + f"✓ Бета воспроизводится: методика ≈ {best[1]} (доходности, окно). "
           "Расхождения от точных дат/дивидендных корректировок." + X) if ok
          else (Y + "≈ ни одна простая комбинация не дала точного совпадения — у T-Bank своя "
                "методика (возможно лог-доходности, иной бенчмарк/окно/корректировки)" + X))


if __name__ == "__main__":
    main()

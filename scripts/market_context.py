"""Срез рыночного контекста одной командой: только вычислимые ФАКТЫ, ноль новостей.

    python scripts/market_context.py

READ-ONLY. Что считает:
- серии красных/зелёных недель и дней IMOEX (прокси: фьючерс IMOEXF; RGBI удалён из API), дистанция от 52w-max;
- мини-кривая ОФЗ (2/5/15 лет, YTM бисекцией) и её сдвиг с прошлого запуска;
- ключевая ставка ЦБ (RUSFAR удалён из API — market-implied ожидание недоступно);
- дней до ближайшего заседания ЦБ.
История срезов копится в analysis/market_context_history.json (дельты день-к-дню).
"""
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "analysis" / "market_context_history.json"

G, R, Y, B, DIM, BOLD, X = "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[2m", "\033[1m", "\033[0m"

KEYRATE = 14.25  # ключевая ставка ЦБ (снижена 2026-06-19), обновлять при изменении; дубль в scripts/dashboard.py:95 — менять синхронно
CB_MEETINGS_2026 = ["2026-07-25", "2026-09-12", "2026-10-24", "2026-12-19"]

# IMOEX/RGBI/RUSFAR-индикативы УДАЛЕНЫ из API 2026-07 (см. docs/gotchas.md «Индексы/ставки
# удалены»). Индекс заменён на фьючерс IMOEXF (перпетуал — резолвим по тикеру, т.к. фьючерс
# экспирируется); у RGBI/RUSFAR прокси нет — блоки деградированы.
IMOEXF_FALLBACK = "5bcff194-f10d-4314-b9ee-56b7fdb344fd"
# мини-кривая ОФЗ: (тикер, uid, номинал) — проверены в ofz_screen 2026-06-11
OFZ_CURVE = [
    ("26236 ~2г", "f0c46d24-b526-4a3a-b6a2-f1e6a6b1f6ad", 1000.0),  # uid уточняется на старте
    ("26239 ~5л", None, 1000.0),
    ("26238 ~15л", None, 1000.0),
]
OFZ_TICKERS = {"SU26236RMFS8": 0, "SU26239RMFS2": 1, "SU26238RMFS4": 2}


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
    raise SystemExit("retries")


def to_f(v) -> float:
    if not v:
        return 0.0
    if isinstance(v, dict):
        if v.get("value") not in (None, ""):
            return float(v["value"])
        return int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9
    return 0.0


def resolve_uid(ticker: str, kind: str) -> str | None:
    """uid по тикеру (фьючерсы экспирируются — не хардкодить). None если не найден."""
    try:
        r = call("InstrumentsService/FindInstrument", {"query": ticker, "instrumentKind": kind})
    except urllib.error.HTTPError:
        return None
    return next((i["uid"] for i in r.get("instruments", []) if i.get("ticker") == ticker), None)


NOW = datetime.now(timezone.utc)


def candles(uid: str, interval: str, days: int) -> list:
    r = call("MarketDataService/GetCandles", {
        "instrumentId": uid, "interval": interval,
        "from": (NOW - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z"),
        "to": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")})
    return [(c["time"][:10], to_f(c["open"]), to_f(c["close"]),
             to_f(c["high"]), to_f(c["low"])) for c in r.get("candles", [])]


def streak(cs: list) -> int:
    """+N зелёных подряд / -N красных подряд (по close vs open)."""
    s = 0
    for _, o, c, _, _ in reversed(cs):
        if c < o and s <= 0:
            s -= 1
        elif c > o and s >= 0:
            s += 1
        else:
            break
    return s


def index_block(name: str, uid: str) -> dict:
    wk = candles(uid, "CANDLE_INTERVAL_WEEK", 370)
    dy = candles(uid, "CANDLE_INTERVAL_DAY", 372)
    last = dy[-1][2] if dy else 0.0
    hi52 = max(c for _, _, c, _, _ in dy) if dy else 0.0
    return {"last": last, "wk_streak": streak(wk), "day_streak": streak(dy),
            "from_52w_high_pct": (last / hi52 - 1) * 100 if hi52 else 0.0}


def ytm(dirty: float, flows: list) -> float:
    def npv(r):
        return sum(a / (1 + r) ** t for t, a in flows) - dirty
    lo, hi = 0.0001, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def ofz_curve() -> dict:
    bonds = call("InstrumentsService/Bonds", {"instrumentStatus": "INSTRUMENT_STATUS_BASE"})["instruments"]
    sel = {}
    for b in bonds:
        if b["ticker"] in OFZ_TICKERS:
            sel[b["ticker"]] = b
    out = {}
    uids = [b["uid"] for b in sel.values()]
    lp = call("MarketDataService/GetLastPrices", {"instrumentId": uids})
    prices = {x["instrumentUid"]: to_f(x["price"]) for x in lp.get("lastPrices", [])}
    for tk, b in sel.items():
        nominal = to_f(b["nominal"])
        mat = datetime.fromisoformat(b["maturityDate"].replace("Z", "+00:00"))
        yrs = (mat - NOW).days / 365.25
        pct = prices.get(b["uid"], 0.0)
        if not pct:
            continue
        cps = call("InstrumentsService/GetBondCoupons", {
            "instrumentId": b["uid"], "from": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": "2045-01-01T00:00:00Z"}).get("events", [])
        flows = [((datetime.fromisoformat(c["couponDate"].replace("Z", "+00:00")) - NOW).days / 365.25,
                  to_f(c["payOneBond"])) for c in cps]
        flows = [(t, a) for t, a in flows if t > 0] + [(yrs, nominal)]
        dirty = pct / 100 * nominal + to_f(b.get("aciValue"))
        out[f"{yrs:.0f}л ({tk[2:7]})"] = round(ytm(dirty, flows) * 100, 2)
        time.sleep(0.3)
    return out


def main():
    today = NOW.strftime("%Y-%m-%d")
    print(f"\n{BOLD}{B}══ РЫНОЧНЫЙ КОНТЕКСТ {today} ══{X}  {DIM}(только факты из API){X}\n")

    snap = {"date": today}

    # индекс IMOEX — через фьючерс IMOEXF (сам индекс-индикатив удалён из API)
    imoexf = resolve_uid("IMOEXF", "INSTRUMENT_TYPE_FUTURES") or IMOEXF_FALLBACK
    d = index_block("IMOEX", imoexf)
    snap["IMOEX"] = d
    ws, ds = d["wk_streak"], d["day_streak"]
    cw = R if ws < 0 else G
    cd = R if ds < 0 else G
    print(f"{BOLD}{'IMOEX':<6}{X}{d['last']:>9.2f}   "
          f"недели: {cw}{ws:+d}{X}  дни: {cd}{ds:+d}{X}   "
          f"от 52w-max: {R if d['from_52w_high_pct']<-1 else Y}{d['from_52w_high_pct']:+.1f}%{X}   "
          f"{DIM}(прокси: фьюч IMOEXF){X}")
    print(f"{DIM}RGBI: индекс удалён из API (прокси нет) — блок пропущен.{X}")

    # RUSFAR удалён из API — рыночное ожидание по ставке недоступно, показываем ключевую
    snap["rusfar"] = None
    print(f"\n{BOLD}Ставка{X} ключевая ЦБ {KEYRATE}%   "
          f"{DIM}(RUSFAR удалён из API — market-implied ожидание недоступно){X}")

    # заседание ЦБ
    nxt = next((m for m in CB_MEETINGS_2026 if m >= today), None)
    if nxt:
        days = (datetime.fromisoformat(nxt).replace(tzinfo=timezone.utc) - NOW).days
        print(f"{BOLD}ЦБ{X}     ближайшее заседание {nxt} — через {BOLD}{days} дн.{X}")
    snap["next_cb"] = nxt

    # кривая ОФЗ + сдвиг
    print(f"\n{BOLD}── Кривая ОФЗ (YTM) ──{X}")
    curve = ofz_curve()
    snap["ofz"] = curve
    hist = json.loads(HIST.read_text(encoding="utf-8")) if HIST.exists() else []
    prev = next((h for h in reversed(hist) if h["date"] < today and h.get("ofz")), None)
    for k, v in curve.items():
        d = ""
        if prev and k in prev["ofz"]:
            dv = v - prev["ofz"][k]
            c = R if dv > 0.02 else G if dv < -0.02 else DIM
            d = f"  {c}{dv:+.2f} п.п. с {prev['date']}{X}"
        print(f"  {k:<14}{v:>6.2f}%{d}")

    # история
    hist = [h for h in hist if h["date"] != today] + [snap]
    HIST.write_text(json.dumps(hist[-90:], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{DIM}срез сохранён в {HIST.name} (хранится 90 дней){X}\n")


if __name__ == "__main__":
    main()

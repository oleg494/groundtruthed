"""Сравнятор фондов денежного рынка: TMON vs LQDT vs SBMM vs AKMM.

READ-ONLY: Etfs / GetCandles (history 30/мин — у нас 4 вызова).
Доходность считаем по дневным свечам (close): она уже ЗА ВЫЧЕТОМ внутренней
комиссии фонда (TER сидит в цене пая). Поверх — брокерская комиссия за вход+выход:
TMON для клиента Т-Банка 0%, чужие фонды 0.3%+0.3% на тарифе «Инвестор».
"""
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent

FUNDS = ["TMON", "LQDT", "SBMM", "AKMM"]
BROKER_FEE = {"TMON": 0.0, "LQDT": 0.006, "SBMM": 0.006, "AKMM": 0.006}  # вход+выход
# в справочнике по несколько uid на тикер (классы листинга) — кандидаты, берём первый с историей
CANDIDATES = {
    "TMON": ["498ec3ff-ef27-4729-9703-a5aac48d5789", "eef07afb-d176-4854-978b-e6820e9a1f66",
             "f4eb313a-09e2-46d7-8e5a-bda411b8ffc1"],
    "LQDT": ["a240edc6-a605-44b3-9801-37b9f7c3d1ff", "e9347c26-5953-4108-929a-86b7a42bcb4d"],
    "SBMM": ["18a1df4c-6cc5-4880-a2a4-874c3f12448a", "df5741f6-8c5d-451a-9f77-292836ce66a7"],
    "AKMM": ["c6b2aec2-74db-477c-9496-c0b535bc769d", "7d6b4c84-00c1-49cc-bc64-7d35c692840f"],
}


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


def main():
    now = datetime.now(timezone.utc)
    frm = (now - timedelta(days=366)).strftime("%Y-%m-%dT00:00:00Z")

    series = {}
    for tk in FUNDS:
        cs = []
        for uid in CANDIDATES[tk]:
            r = call("MarketDataService/GetCandles", {
                "instrumentId": uid, "interval": "CANDLE_INTERVAL_DAY",
                "from": frm, "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            cs = [(c["time"][:10], to_f(c["close"])) for c in r.get("candles", []) if c.get("isComplete", True)]
            time.sleep(2.1)  # getHistory 30/мин
            if len(cs) > 60:
                break
        if len(cs) <= 60:
            print(f"{tk}: нет истории ни по одному uid")
            continue
        series[tk] = (tk, cs)

    def ret(cs, days):
        """доходность за окно days, % годовых (грубая, без сложн. процента внутри окна)"""
        target = (now - timedelta(days=days)).strftime("%Y-%m-%d")
        past = next(((d, p) for d, p in cs if d >= target), None)
        if not past or not cs:
            return None, None
        d0, p0 = past
        d1, p1 = cs[-1]
        real_days = (datetime.fromisoformat(d1) - datetime.fromisoformat(d0)).days or 1
        total = (p1 / p0 - 1) * 100
        return total, total * 365 / real_days

    windows = [30, 91, 182, 365]
    print(f"{'фонд':<6}{'цена':>9}", *(f"{w}д[%год]" for w in windows), sep="  ")
    print("-" * 66)
    out = {}
    for tk in FUNDS:
        if tk not in series:
            continue
        name, cs = series[tk]
        row = []
        for w in windows:
            tot, ann = ret(cs, w)
            row.append(ann)
        out[tk] = {"name": name, "price": cs[-1][1] if cs else 0, "ann": dict(zip(windows, row))}
        cells = "  ".join(f"{a:>9.2f}" if a is not None else f"{'—':>9}" for a in row)
        print(f"{tk:<6}{cs[-1][1]:>9.2f}  {cells}")

    # безубыточный горизонт LQDT vs TMON при комиссии 0.6% входа-выхода
    if "TMON" in out and "LQDT" in out:
        a_l = out["LQDT"]["ann"].get(91)
        a_t = out["TMON"]["ann"].get(91)
        if a_l and a_t and a_l > a_t:
            be_days = 0.6 / (a_l - a_t) * 365
            print(f"\nLQDT − TMON (по 3-мес темпу): {a_l - a_t:+.2f} п.п. годовых")
            print(f"комиссия 0.6% отбивается за ~{be_days:.0f} дней")
        else:
            print(f"\nLQDT не быстрее TMON на 3-мес окне — комиссия 0.6% не отбивается")

    (ROOT / "analysis" / "mm_funds_compare.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nсохранено: analysis/mm_funds_compare.json")


if __name__ == "__main__":
    main()

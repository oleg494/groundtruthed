"""#2 — прокатить daybot-логику (пробой утреннего диапазона) через бэктест.

daybot/run.py торгует пробой утреннего диапазона 10:00-10:30 МСК на фьючах в песочнице —
медленно (недели на статистику) и вслепую к тому, есть ли у идеи эдж вообще. Этот скрипт
гоняет ТУ ЖЕ логику (стратегия `orb` в backtest/strategies.py) на реальной внутридневной
истории за минуты. 30-мин бары: один бар 10:00-10:30 МСК = окно диапазона daybot (range_bars=1).

Данные: 30-мин свечи тянутся мульти-оконным read-only фетчем (лимит окна ~неделя у
GetCandles), склеиваются и кэшируются на диск. Заявок не размещает. Сравнение — против
buy&hold того же фьючерса за тот же период.

Запуск:  python analysis/orb_backtest.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest import candles, strategies          # noqa: E402
from backtest.core import Bar, Instrument          # noqa: E402
from backtest.engine import run                    # noqa: E402
from backtest.metrics import metrics               # noqa: E402
from lab.instruments import INSTRUMENTS            # noqa: E402

CACHE = ROOT / "backtest" / ".cache"
INTERVAL = "CANDLE_INTERVAL_30_MIN"
WINDOW_DAYS = 6           # одно окно фетча (под лимит GetCandles для 30-мин)
HISTORY_DAYS = 120        # сколько истории всего набрать
CASH = 100_000.0
COMMISSION = 0.0005
SLIPPAGE = 0.0005

# фьючи daybot. point_rub — руб/пункт (фьюч → маржинальная модель в движке)
TICKERS = ["BMQ6", "NGN6", "GLDRUBF"]


def fetch_window(uid: str, frm: datetime, to: datetime) -> list[Bar]:
    body = {"instrumentId": uid, "interval": INTERVAL,
            "from": frm.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": to.strftime("%Y-%m-%dT%H:%M:%SZ")}
    url = (candles._SANDBOX +
           "/tinkoff.public.invest.api.contract.v1.MarketDataService/GetCandles")
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {candles._token()}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    out = []
    for c in data.get("candles", []):
        if not c.get("isComplete", True):
            continue
        t = int(datetime.strptime(c["time"][:19], "%Y-%m-%dT%H:%M:%S")
                .replace(tzinfo=timezone.utc).timestamp())
        out.append(Bar(t=t, o=candles._to_f(c["open"]), h=candles._to_f(c["high"]),
                       l=candles._to_f(c["low"]), c=candles._to_f(c["close"]),
                       v=candles._to_f(c.get("volume", 0))))
    return out


def fetch_intraday(ticker: str) -> list[Bar]:
    """Склеить 30-мин историю из окон по WINDOW_DAYS. Кэш на диск (одним файлом)."""
    CACHE.mkdir(exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    key = CACHE / f"{ticker}_30min_{HISTORY_DAYS}d_{today}.json"
    if key.exists():
        raw = json.loads(key.read_text(encoding="utf-8"))
        return [Bar(**b) for b in raw]

    uid = INSTRUMENTS[ticker]["uid"]
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=HISTORY_DAYS)
    seen: dict[int, Bar] = {}
    cur = start
    while cur < now:
        nxt = min(cur + timedelta(days=WINDOW_DAYS), now)
        try:
            for b in fetch_window(uid, cur, nxt):
                seen[b.t] = b
        except urllib.error.HTTPError as e:
            print(f"    окно {cur:%m-%d}..{nxt:%m-%d}: HTTP {e.code} — пропуск")
        cur = nxt
        time.sleep(0.2)                                # бережём marketdata-лимит
    bars = sorted(seen.values(), key=lambda b: b.t)
    key.write_text(json.dumps([b.__dict__ for b in bars]), encoding="utf-8")
    return bars


def run_orb(ticker: str, bars: list[Bar], mult: float) -> dict:
    data = {ticker: bars}
    inst = {ticker: Instrument(ticker, multiplier=mult, kind="futures")}
    orb = run(strategies.build("orb", range_bars=1), data, cash=CASH,
              commission=COMMISSION, slippage=SLIPPAGE, instruments=inst)
    bh = run(strategies.build("buyhold"), data, cash=CASH,
             commission=COMMISSION, slippage=SLIPPAGE, instruments=inst)
    mo, mb = metrics(orb), metrics(bh)
    return {
        "bars": len(bars),
        "orb": {"ret_pct": round(mo.total_return * 100, 2), "sharpe": round(mo.sharpe, 2),
                "maxdd_pct": round(mo.max_drawdown * 100, 2), "trades": mo.num_trades,
                "winrate": round(mo.win_rate * 100, 1), "pf": round(mo.profit_factor, 2)},
        "buyhold": {"ret_pct": round(mb.total_return * 100, 2), "sharpe": round(mb.sharpe, 2),
                    "maxdd_pct": round(mb.max_drawdown * 100, 2)},
    }


def main():
    print(f"#2 ORB (пробой утреннего диапазона) на 30-мин истории, "
          f"~{HISTORY_DAYS} дн., комиссия {COMMISSION*100:.2f}%+слип {SLIPPAGE*100:.2f}%\n")
    out = {"generated": datetime.now(timezone.utc).isoformat(),
           "interval": "30min", "history_days": HISTORY_DAYS, "results": {}}

    for tk in TICKERS:
        print(f"━━ {tk}: тяну 30-мин историю…")
        try:
            bars = fetch_intraday(tk)
        except Exception as e:                          # noqa: BLE001
            print(f"    фетч не удался: {e}")
            continue
        if len(bars) < 100:
            print(f"    мало баров ({len(bars)}) — пропуск")
            continue
        mult = INSTRUMENTS[tk]["point_rub"]
        days = (bars[-1].t - bars[0].t) / 86400
        print(f"    {len(bars)} баров, {days:.0f} календ. дней, "
              f"{bars[0].c:.2f} → {bars[-1].c:.2f} пунктов")
        r = run_orb(tk, bars, mult)
        out["results"][tk] = r
        o, b = r["orb"], r["buyhold"]
        print(f"    ORB      ret {o['ret_pct']:+7.2f}%  Sharpe {o['sharpe']:+5.2f}  "
              f"maxDD {o['maxdd_pct']:7.2f}%  сделок {o['trades']:>3}  "
              f"WR {o['winrate']:4.1f}%  PF {o['pf']}")
        print(f"    buy&hold ret {b['ret_pct']:+7.2f}%  Sharpe {b['sharpe']:+5.2f}  "
              f"maxDD {b['maxdd_pct']:7.2f}%")

    # вердикт
    print("\n══ вердикт ══")
    verdict = []
    for tk, r in out["results"].items():
        o = r["orb"]
        edge = ("есть эдж" if o["ret_pct"] > 0 and o["trades"] >= 5 else
                "нет эджа" if o["trades"] >= 5 else "мало сделок — данных не хватает")
        verdict.append(f"{tk}: ORB {o['ret_pct']:+.2f}% за {o['trades']} сделок — {edge}")
    if not out["results"]:
        verdict.append("данных не набралось — фетч 30-мин не дал истории")
    out["verdict"] = verdict
    for v in verdict:
        print("  • " + v)

    res = ROOT / "analysis" / "orb_backtest.json"
    res.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nрезультат: {res.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

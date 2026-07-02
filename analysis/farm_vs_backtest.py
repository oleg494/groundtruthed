"""#1 — связать бэктест с реальными стратегиями фермы на истории MOEX.

Ферма (lab/) форвард-тестит стратегии в песочнице вслепую: grid/momentum/meanrev/
gold_trend крутятся на живом потоке, но НИКОГДА не проверялись на истории. Этот скрипт
прогоняет их эквиваленты из backtest/ на реальных дневных свечах MOEX и отвечает на
вопрос цифрами, а не догадками: какие из них вообще имеют эдж, и бьют ли они buy&hold.

Соответствие стратегий (ферма → бэктест), выверено по коду lab/strategies.py:
  momentum   SMA20>SMA60 в позиции / иначе кэш   →  sma_cross fast=20 slow=60
  meanrev    z(20)<-2 купить / z>0 продать       →  bollinger n=20 k=2 (нижняя=mean-2σ, выход=mid)
  gold_trend SMA10/30 на GLDRUBF, лонг-онли       →  sma_cross fast=10 slow=30 (futures, пункты)
  grid       сетка лимиток на SBER                →  НЕ портируется на дневной бар-движок
                                                      (нужны лимитные уровни и тиковое исполнение)
  buyhold/random — те же контрольные бенчмарки, обязаны быть побиты.

Гипотеза hermes-агента, которую проверяем: «grid умрёт на тренде, meanrev ловит ножи»
— то есть контртренд (meanrev/bollinger) проигрывает на трендовом рынке, momentum выигрывает.

Read-only: данные тянутся через тот же sandbox-фетч с диск-кэшем, что и весь backtest/.
Заявок не размещает. Запуск:  python analysis/farm_vs_backtest.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest import candles, strategies          # noqa: E402
from backtest.core import Instrument               # noqa: E402
from backtest.engine import run                    # noqa: E402
from backtest.metrics import metrics               # noqa: E402
from lab.instruments import INSTRUMENTS, BASKET    # noqa: E402  (только реестр UID, не сеть)

DAYS = 500
CASH = 100_000.0
COMMISSION = 0.0005      # 5 б.п. — близко к тарифу «Инвестор»
SLIPPAGE = 0.0005        # 5 б.п. против нас: на дневках честнее, чем 0

# эквиваленты фермовых сигналок (имя для отчёта → как строить в backtest)
FARM = {
    "momentum (SMA20/60)":  ("sma_cross", {"fast": 20, "slow": 60}),
    "meanrev (Boll 20/2)":  ("bollinger", {"n": 20, "k": 2.0}),
}
BENCH = {
    "buyhold": ("buyhold", {}),
    "random":  ("random",  {"seed": 1}),
}


def fetch(ticker: str):
    uid = INSTRUMENTS[ticker]["uid"]
    return candles.from_tinvest(uid, ticker, days=DAYS)


def run_one(name, strat_key, params, data, futures=False, mult=1.0):
    strat = strategies.build(strat_key, **params)
    inst = None
    if futures:
        inst = {t: Instrument(t, multiplier=mult, kind="futures") for t in data}
    res = run(strat, data, cash=CASH, commission=COMMISSION, slippage=SLIPPAGE,
              instruments=inst)
    m = metrics(res)
    return {
        "name": name, "ret_pct": round(m.total_return * 100, 2),
        "sharpe": round(m.sharpe, 2), "maxdd_pct": round(m.max_drawdown * 100, 2),
        "trades": m.num_trades, "winrate": round(m.win_rate * 100, 1),
        "final": round(m.final_equity),
    }


def main():
    print(f"#1 ферма vs бэктест — реальная история MOEX, {DAYS} дн., "
          f"комиссия {COMMISSION*100:.2f}% + слиппедж {SLIPPAGE*100:.2f}%\n")

    out = {"generated": datetime.now(timezone.utc).isoformat(),
           "days": DAYS, "commission": COMMISSION, "slippage": SLIPPAGE,
           "basket": {}, "gold": {}}

    # ── корзина акций: каждая сигналка + бенчмарки по каждому тикеру ──
    agg = {n: [] for n in list(FARM) + list(BENCH)}
    for tk in BASKET:
        try:
            data = fetch(tk)
        except Exception as e:                       # noqa: BLE001
            print(f"  {tk}: фетч не удался ({e}) — пропуск")
            continue
        bars = data[tk]
        print(f"━━ {tk}: {len(bars)} баров, {bars[0].c:.2f} → {bars[-1].c:.2f}")
        rows = []
        for n, (k, p) in {**FARM, **BENCH}.items():
            r = run_one(n, k, p, data)
            rows.append(r)
            agg[n].append(r["ret_pct"])
            print(f"    {n:22} ret {r['ret_pct']:+7.2f}%  Sharpe {r['sharpe']:+5.2f}  "
                  f"maxDD {r['maxdd_pct']:7.2f}%  сделок {r['trades']:>3}  WR {r['winrate']:4.1f}%")
        out["basket"][tk] = rows

    # ── gold_trend: фьючерс GLDRUBF, цена в пунктах ──
    print(f"\n━━ GLDRUBF (фьюч, пункты): gold_trend = sma_cross 10/30, лонг-онли")
    try:
        gdata = fetch("GLDRUBF")
        gbars = gdata["GLDRUBF"]
        mult = INSTRUMENTS["GLDRUBF"]["point_rub"]
        print(f"    {len(gbars)} баров, {gbars[0].c:.2f} → {gbars[-1].c:.2f} пунктов")
        for n, (k, p) in {"gold_trend (SMA10/30)": ("sma_cross", {"fast": 10, "slow": 30}),
                          "buyhold": ("buyhold", {})}.items():
            r = run_one(n, k, p, gdata, futures=True, mult=mult)
            out["gold"][n] = r
            print(f"    {n:22} ret {r['ret_pct']:+7.2f}%  Sharpe {r['sharpe']:+5.2f}  "
                  f"maxDD {r['maxdd_pct']:7.2f}%  сделок {r['trades']:>3}")
    except Exception as e:                            # noqa: BLE001
        print(f"    фетч GLDRUBF не удался: {e}")

    # ── средние по корзине: вердикт ──
    print("\n══ средняя доходность по корзине (по тикерам) ══")
    means = {n: (sum(v) / len(v) if v else 0.0) for n, v in agg.items()}
    for n in list(FARM) + list(BENCH):
        print(f"    {n:22} {means[n]:+7.2f}%")
    out["means"] = {n: round(v, 2) for n, v in means.items()}

    mom, mr = means["momentum (SMA20/60)"], means["meanrev (Boll 20/2)"]
    bh = means["buyhold"]
    verdict = []
    verdict.append(f"momentum {'>' if mom > mr else '<'} meanrev "
                   f"({mom:+.2f}% vs {mr:+.2f}%) — "
                   + ("гипотеза hermes подтверждается: на этом периоде тренд бьёт контртренд"
                      if mom > mr else
                      "гипотеза hermes НЕ подтверждается на этом периоде"))
    beats_bh = [n for n in FARM if means[n] > bh]
    verdict.append(f"buy&hold даёт {bh:+.2f}%; сигналки, которые его бьют: "
                   + (", ".join(beats_bh) if beats_bh else "НИ ОДНОЙ"))
    out["verdict"] = verdict
    print("\n══ вердикт ══")
    for v in verdict:
        print("  • " + v)

    res_path = ROOT / "analysis" / "farm_vs_backtest.json"
    res_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nрезультат: {res_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

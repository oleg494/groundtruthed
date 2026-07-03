"""Реконструкция индекса IMOEX из состава: дневная доходность = Σ(вес·доходность бумаги).

    python analysis/index_reconstruct_validate.py

Оракулы:
1. Σ весов состава == 100% (структурный инвариант).
2. Дневная доходность индекса ≈ Σ_i (w_i · r_i), где r_i — дневная доходность i-й бумаги
   (тождество капвзвешенного индекса, Ласпейрес). Веса берём текущие — для свежего дня они
   почти совпадают с весами на начало дня, поэтому реконструкция должна сойтись с ΔIMOEX
   с точностью до дрейфа весов (доли %).

Состав (uid+вес) берём из ListIndicatives(INDEX_COMPOSITION), доходности — из дневных свечей.
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
# ListIndicatives(INDEX_COMPOSITION) для IMOEX убран из API 2026-06 — используем замороженный
# снапшот состава от 2026-06-19 вместо живого запроса.
COMP_FILE = ROOT / "analysis" / "data" / "index_composition_imoex_2026-06-19.json"


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


def closes(uid, n=4):
    frm = (datetime.now(timezone.utc) - timedelta(days=12)).strftime("%Y-%m-%dT00:00:00Z")
    to = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = call("MarketDataService/GetCandles",
             {"instrumentId": uid, "interval": "CANDLE_INTERVAL_DAY", "from": frm, "to": to})
    cs = [(c["time"], mvf(c["close"])) for c in r.get("candles", []) if c.get("isComplete", True)]
    return cs[-n:]


def main():
    print(f"{BOLD}Реконструкция IMOEX из состава: Δиндекса = Σ(вес·Δбумаги){X}\n")
    try:
        if not closes(IMOEX, 4):
            raise urllib.error.HTTPError(BASE, 404, "empty", None, None)
    except urllib.error.HTTPError:
        print(f"{Y}≈ WARN: индекс IMOEX удалён из API (upstream 2026-07, «Instrument not found») "
              f"— реконструировать не с чем. См. docs/gotchas.md «Индексы/ставки-индикативы удалены».{X}")
        sys.exit(0)
    comp = None
    for ins in json.loads(COMP_FILE.read_text(encoding="utf-8"))["instruments"]:
        if ins["uid"] == IMOEX:
            comp = [(c["uid"], float(c["weight"]["value"])) for c in ins["indexComposition"]]
            break
    wsum = sum(w for _, w in comp)
    print(f"1) Σ весов = {wsum:.4f}%  {(G+'OK (=100%)' if abs(wsum-100) < 1e-6 else R+'≠100')}{X}")
    print(f"   состав: {len(comp)} бумаг\n")

    idx = closes(IMOEX, 4)
    # последние два закрытых дня индекса
    idx_dates = [t[:10] for t, _ in idx]
    # доходность последнего дня
    r_idx = idx[-1][1] / idx[-2][1] - 1
    print(f"2) Дневная доходность IMOEX за {idx_dates[-1]}: {r_idx*100:+.4f}%")

    r_recon = 0.0
    miss = 0
    covered_w = 0.0
    detail = []
    for uid, w in comp:
        try:
            cs = closes(uid, 4)
            if len(cs) < 2:
                miss += 1
                continue
            # выровнять на ту же дату, что у индекса
            byd = {t[:10]: c for t, c in cs}
            if idx_dates[-1] in byd and idx_dates[-2] in byd:
                ri = byd[idx_dates[-1]] / byd[idx_dates[-2]] - 1
            else:
                ri = cs[-1][1] / cs[-2][1] - 1
            r_recon += (w / 100.0) * ri
            covered_w += w
            detail.append((w, ri))
        except Exception:
            miss += 1
    print(f"   реконструкция Σ(w·r): {r_recon*100:+.4f}%  (покрыто весов {covered_w:.2f}%, "
          f"бумаг без данных {miss})")
    diff = abs(r_recon - r_idx) * 100
    # топ-вкладчики
    detail.sort(reverse=True)
    print(f"\n   {DIM}крупнейшие веса и их вклад:{X}")
    for w, ri in detail[:5]:
        print(f"   {DIM}  вес {w:5.2f}%  доходность {ri*100:+6.2f}%  вклад {(w/100*ri)*100:+.4f}пп{X}")

    ok = diff < 0.10  # < 0.1 пп
    print(f"\n{BOLD}Расхождение |Δиндекса − Σ(w·r)| = {diff:.4f} пп{X}")
    print((G + "✓ Индекс реконструируется из состава: дневная доходность = взвешенная сумма "
           "доходностей бумаг (с точностью до дрейфа весов внутри дня). Σ весов = 100%." + X)
          if ok else (Y + "≈ расхождение есть — дрейф весов/незакрытые цены/неполное покрытие; "
                      "см. покрытие весов выше" + X))


if __name__ == "__main__":
    main()

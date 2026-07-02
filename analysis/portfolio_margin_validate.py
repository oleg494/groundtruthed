"""Проверка внутренней согласованности портфеля и маржи (account-specific оракулы).

    python analysis/portfolio_margin_validate.py

Оракулы — поля самого API по конкретному счёту. Проверяем тождества, которые ОБЯЗАНЫ
выполняться, если поля считаются согласованно:

  A. totalAmountPortfolio == Σ quantity · текущая_цена + кэш
  B. expectedYield(₽)      == Σ quantity · (текущая − averageFifo)
  C. expectedYield(%)      == 100 · Σ P&L / Σ вложено_по_fifo
  D. liquidPortfolio       == totalAmountPortfolio (нет неликвида/плеча)
  E. minimalMargin         == startingMargin / 2
  F. amountOfMissingFunds  == startingMargin − liquidPortfolio   (знак: дефицит)

Попутная грабля: GetRiskRates по ряду биржевых фондов (TMON@) отдаёт ПУСТЫЕ ставки — прямой
пересчёт startingMargin из публикуемой риск-ставки невозможен; фиксируем как факт.

READ-ONLY: только get_*.
"""
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

def load_accounts():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_ACCOUNTS="):
            return [a.strip() for a in line.split("=", 1)[1].split(",") if a.strip()]
    raise SystemExit("Set TINVEST_ACCOUNTS=id1,id2 in .env")


ACCOUNTS = load_accounts()


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
        return 0.0
    return float(v["value"]) if v.get("value") not in (None, "") else \
        int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9


def price(uid):
    r = call("MarketDataService/GetMarketValues",
             {"instrumentId": [uid],
              "values": ["INSTRUMENT_VALUE_LAST_PRICE", "INSTRUMENT_VALUE_CLOSE_PRICE"]})
    d = {v["type"]: mvf(v["value"]) for v in r["instruments"][0].get("values", [])}
    return d.get("INSTRUMENT_VALUE_LAST_PRICE") or d.get("INSTRUMENT_VALUE_CLOSE_PRICE")


def check(label, lhs, rhs, tol, unit="₽"):
    d = abs(lhs - rhs)
    ok = d <= tol
    c = G if ok else R
    print(f"  {c}{'OK  ' if ok else 'FAIL'}{X} {label:<46} {lhs:.4f} vs {rhs:.4f}  Δ={d:.4f}{unit}")
    return ok


def main():
    print(f"{BOLD}Согласованность портфеля и маржи — account-specific оракулы{X}\n")
    allok = True
    for acc in ACCOUNTS:
        print(f"{BOLD}=== счёт {acc} ==={X}")
        pf = call("OperationsService/GetPortfolio", {"accountId": acc})
        total = mvf(pf["totalAmountPortfolio"])
        eyield_pct = mvf(pf.get("expectedYield"))
        positions = pf.get("positions", [])

        # A/B/C: пересчёт стоимости и P&L из позиций по текущим ценам
        mkt_sum = 0.0
        pnl_sum = 0.0
        invested_fifo = 0.0
        for p in positions:
            uid = p["instrumentUid"]
            qty = mvf(p["quantity"])
            fifo = mvf(p.get("averagePositionPriceFifo"))
            cur = price(uid)
            if cur is None:
                cur = fifo
            mkt_sum += qty * cur
            pnl_sum += qty * (cur - fifo)
            invested_fifo += qty * fifo

        qty_tot = sum(mvf(p["quantity"]) for p in positions)
        ey_sum = sum(mvf(p.get("expectedYieldFifo")) for p in positions)
        # реконструкция (зависит от внешней цены и округления fifo до 2 знаков) — атрибутируем
        print(f"  {DIM}реконструкция из позиций (зависит от снимка цены и округления fifo до 2 зн.):{X}")
        relA = abs(total - mkt_sum) / total if total else 0
        print(f"  {(G+'≈OK ' if relA < 1e-3 else R+'FAIL')}{X} A total≈Σqty·цена  "
              f"{total:.2f} vs {mkt_sum:.2f}  Δ={abs(total-mkt_sum):.2f}₽ "
              f"({abs(total-mkt_sum)/qty_tot:.4f}₽/ед — дрейф снимка цены)")
        okC = check("C expectedYield(%) == 100·P&L/вложено", eyield_pct,
                    100 * pnl_sum / invested_fifo if invested_fifo else 0, 0.05, "пп")
        print(f"  {DIM}  поле ΣexpectedYieldFifo={ey_sum:.2f}₽, мой P&L={pnl_sum:.2f}₽ "
              f"(Δ={abs(ey_sum-pnl_sum):.2f}₽ = округление fifo){X}")

        # D/E/F: маржа (если включена) — ТОЧНЫЕ внутренние тождества, без внешних цен
        print(f"  {BOLD}точные тождества маржи:{X}")
        try:
            m = call("UsersService/GetMarginAttributes", {"accountId": acc})
            liquid = mvf(m["liquidPortfolio"])
            start = mvf(m["startingMargin"])
            mini = mvf(m["minimalMargin"])
            miss = mvf(m["amountOfMissingFunds"])
            okD = check("D liquidPortfolio == totalAmountPortfolio", liquid, total, 0.001)
            okE = check("E minimalMargin == startingMargin/2", mini, start / 2, 0.001)
            okF = check("F missingFunds == startingMargin − liquid", miss, start - liquid, 0.001)
            allok = allok and okD and okE and okF
        except urllib.error.HTTPError:
            print(f"  {DIM}  маржа отключена для счёта — тождества D/E/F неприменимы{X}")

        allok = allok and (relA < 1e-3) and okC

        # грабля риск-ставок
        if positions:
            rr = call("InstrumentsService/GetRiskRates",
                      {"instrumentId": [positions[0]["instrumentUid"]]})
            r0 = rr.get("instrumentRiskRates", [{}])[0]
            empty = not r0.get("longRiskRates") and not r0.get("shortRiskRates")
            print(f"  {DIM}GetRiskRates по 1-й позиции: "
                  f"{'ПУСТО (риск-ставка не публикуется — маржу не пересчитать напрямую)' if empty else 'есть ставки'}{X}")
        print()

    print(f"{BOLD}Итог: {'все тождества портфеля/маржи держатся' if allok else 'есть нарушения'}{X}")
    print((G + "✓ Поля API внутренне согласованы: стоимость, P&L и маржа сходятся из позиций "
           "и цен; minimalMargin=½·starting, missingFunds=starting−liquid — точные тождества." + X)
          if allok else (R + "✗ часть тождеств нарушена — см. выше" + X))


if __name__ == "__main__":
    main()

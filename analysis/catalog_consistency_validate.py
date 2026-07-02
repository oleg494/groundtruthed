"""Согласованность каталога и карточки инструмента (List* ↔ *By).

    python analysis/catalog_consistency_validate.py

Кросс-эндпоинт оракул: поля инструмента из СПИСКА (ListShares/ListBonds/ListFutures) обязаны
совпадать с детальной карточкой (ShareBy/BondBy/FutureBy) по тому же uid. Проверяем ключевые
поля (ticker, lot, isin, nominal, maturity, expiration...) бит-в-бит на выборке инструментов.

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
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(float(e.headers.get("Retry-After", delay)))
                delay = min(delay * 2, 20)
                continue
            raise
    raise SystemExit("retries")


def norm(v):
    """Канонизировать значение поля (MoneyValue/Quotation → число, иначе как есть)."""
    if isinstance(v, dict) and ("units" in v and "nano" in v):
        return round(int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9, 9)
    return v


def main():
    print(f"{BOLD}Согласованность каталога (List*) и карточки (*By){X}\n")
    specs = [
        ("акции", "SharesService" if False else "InstrumentsService/Shares",
         "InstrumentsService/ShareBy", ["ticker", "classCode", "isin", "lot", "currency", "nominal"]),
        ("облигации", "InstrumentsService/Bonds", "InstrumentsService/BondBy",
         ["ticker", "classCode", "isin", "lot", "nominal", "maturityDate"]),
        ("фьючерсы", "InstrumentsService/Futures", "InstrumentsService/FutureBy",
         ["ticker", "classCode", "lot", "expirationDate", "basicAsset"]),
    ]
    gtot = gok = 0
    for label, listm, bym, fields in specs:
        lst = call(listm, {"instrumentStatus": "INSTRUMENT_STATUS_BASE"}).get("instruments", [])
        # выборка: каждый 1/200-й, до 8 штук
        sample = lst[::max(1, len(lst) // 8)][:8]
        nbad = 0
        nfields = 0
        for ins in sample:
            uid = ins["uid"]
            det = call(bym, {"idType": "INSTRUMENT_ID_TYPE_UID", "id": uid})["instrument"]
            for f in fields:
                if f not in ins or f not in det:
                    continue
                nfields += 1
                gtot += 1
                if norm(ins[f]) == norm(det[f]):
                    gok += 1
                else:
                    nbad += 1
                    print(f"  {R}расх{X} {label} {ins.get('ticker')} поле {f}: "
                          f"список={norm(ins[f])} карточка={norm(det[f])}")
        c = G if nbad == 0 else R
        print(f"  {c}{'OK  ' if nbad==0 else 'FAIL'}{X} {label:<10} проверено {len(sample)} инстр., "
              f"{nfields} полей, расхождений {nbad}")

    print(f"\n{BOLD}Итог: {gok}/{gtot} полей совпали между списком и карточкой{X}")
    print((G + "✓ Каталог (List*) и детальная карточка (*By) согласованы бит-в-бит — один источник "
           "истины для метаданных инструментов." + X) if gok == gtot
          else (Y + "≈ есть расхождения каталог/карточка — см. выше" + X))


if __name__ == "__main__":
    main()

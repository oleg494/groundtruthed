#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Загрузчик исторических архивов минутных свечей T-Invest (History MD).

    python scripts/history_archive.py SBER 2025
    python scripts/history_archive.py SBER 2025 --csv out.csv   # склеить в один CSV

READ-ONLY. Качает годовой zip минутных свечей по инструменту с эндпоинта
history-data (отдельный лимит — 30 файлов/мин). Внутри zip: по файлу на торговый
день, формат CSV без заголовка, ';'-разделитель:

    UID; ts(UTC ISO); open; close; high; low; volume; (пусто)

ВАЖНО (грабля, вскрыта оракулом — см. analysis/history_archive_result.md):
OHLC в архиве совпадают с GetCandles бит-в-бит, но КОЛОНКА VOLUME масштабирована
НЕнадёжно — у части инструментов/периодов это объём в ЛОТАХ, у части — в штуках
(напр. SBER до 2025-07-31 включительно — в лотах ×10, после — в штуках). Если
объём критичен — сверяй с GetCandles или домножай на lot осознанно. OHLC доверять
можно.

Домен захардкожен на invest-public-api.tinkoff.ru (REST-слой проекта пока на нём;
host *.tbank.ru отдаёт self-signed МинЦифры-серт и требует SSL_TBANK_VERIFY).
"""
import io
import json
import os
import sys
import urllib.request
import zipfile

BASE = "https://invest-public-api.tinkoff.ru"
SVC = "tinkoff.public.invest.api.contract.v1"


def _key():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for line in open(os.path.join(root, ".env"), encoding="utf-8"):
        if line.startswith("TINVEST_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("TINVEST_API_KEY", "")


KEY = _key()
H = {"Authorization": f"Bearer {KEY}"}


def find_uid(ticker):
    """Тикер -> uid (берём первый матч; для акций предпочитаем TQBR)."""
    for kind in ("INSTRUMENT_TYPE_SHARE", "INSTRUMENT_TYPE_FUTURES",
                 "INSTRUMENT_TYPE_ETF", "INSTRUMENT_TYPE_BOND",
                 "INSTRUMENT_TYPE_CURRENCY"):
        u = f"{BASE}/rest/{SVC}.InstrumentsService/FindInstrument"
        body = json.dumps({"query": ticker, "instrumentKind": kind}).encode()
        req = urllib.request.Request(u, data=body,
                                     headers={**H, "Content-Type": "application/json"},
                                     method="POST")
        ins = json.loads(urllib.request.urlopen(req, timeout=30).read())["instruments"]
        exact = [i for i in ins if i["ticker"] == ticker]
        if exact:
            tqbr = next((i for i in exact if i.get("classCode") == "TQBR"), None)
            return (tqbr or exact[0])["uid"]
    raise SystemExit(f"инструмент {ticker} не найден")


def download_year(uid, year):
    """Скачать годовой zip, вернуть {YYYYMMDD: [rows...]} (rows — списки полей)."""
    url = f"{BASE}/history-data?instrumentId={uid}&year={year}"
    data = urllib.request.urlopen(urllib.request.Request(url, headers=H),
                                  timeout=120).read()
    z = zipfile.ZipFile(io.BytesIO(data))
    out = {}
    for name in z.namelist():
        day = name.split("_")[-1][:8]
        rows = [r.split(";") for r in z.read(name).decode().splitlines() if r.strip()]
        out[day] = rows
    return dict(sorted(out.items()))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(0)
    ticker, year = sys.argv[1], sys.argv[2]
    uid = find_uid(ticker)
    print(f"{ticker} uid={uid}, год {year} …", file=sys.stderr)
    days = download_year(uid, year)
    total = sum(len(v) for v in days.values())
    print(f"{len(days)} торговых дней, {total} минутных свечей", file=sys.stderr)

    if "--csv" in sys.argv:
        path = sys.argv[sys.argv.index("--csv") + 1]
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("uid;ts;open;close;high;low;volume\n")
            for day in sorted(days):
                for r in days[day]:
                    fh.write(";".join(r[:7]) + "\n")
        print(f"-> {path}", file=sys.stderr)
    else:
        # печать первого и последнего дня для проверки
        d0 = sorted(days)[0]
        print(f"первый день {d0}: {len(days[d0])} свечей, пример:")
        for r in days[d0][:2]:
            print("  ", ";".join(r[:7]))


if __name__ == "__main__":
    main()

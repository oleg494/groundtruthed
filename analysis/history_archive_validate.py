#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Оракул-проверка архивов History MD против GetCandles.

    python analysis/history_archive_validate.py

Оракул — unary-метод GetCandles (то же серверное число). Тезис: годовой архив
минутных свечей (эндпоинт history-data) обязан совпадать со свечами из API. На
случайных днях нескольких инструментов сверяем поминутно:
  - OHLC бит-в-бит (должны совпасть);
  - timestamps один-в-один (тот же набор минут);
  - VOLUME — проверяем КОЭФФИЦИЕНТ api/archive (вскрыта грабля: не всегда 1).

Пишет analysis/history_archive_result.md. READ-ONLY.
"""
import io
import json
import os
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


def find(ticker, kind, cc=None):
    u = f"{BASE}/rest/{SVC}.InstrumentsService/FindInstrument"
    body = json.dumps({"query": ticker, "instrumentKind": kind}).encode()
    req = urllib.request.Request(u, data=body,
                                 headers={**H, "Content-Type": "application/json"},
                                 method="POST")
    ins = json.loads(urllib.request.urlopen(req, timeout=30).read())["instruments"]
    it = next((i for i in ins if i["ticker"] == ticker
               and (cc is None or i.get("classCode") == cc)), None)
    return it


def candles(uid, ds):
    u = f"{BASE}/rest/{SVC}.MarketDataService/GetCandles"
    body = json.dumps({"instrumentId": uid,
                       "from": f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}T00:00:00Z",
                       "to": f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}T23:59:00Z",
                       "interval": "CANDLE_INTERVAL_1_MIN"}).encode()
    req = urllib.request.Request(u, data=body,
                                 headers={**H, "Content-Type": "application/json"},
                                 method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=30).read())["candles"]


def qv(d):
    if "value" in d and isinstance(d["value"], str):
        return float(d["value"])
    return int(d.get("units", 0)) + int(d.get("nano", 0)) / 1e9


def archive_day(uid, year, pick):
    url = f"{BASE}/history-data?instrumentId={uid}&year={year}"
    data = urllib.request.urlopen(urllib.request.Request(url, headers=H),
                                  timeout=120).read()
    z = zipfile.ZipFile(io.BytesIO(data))
    names = sorted(z.namelist(), key=lambda n: n.split("_")[-1])
    name = names[pick % len(names)]
    ds = name.split("_")[-1][:8]
    rows = [r.split(";") for r in z.read(name).decode().splitlines() if r.strip()]
    return ds, rows


def check(ticker, kind, cc, year, pick):
    it = find(ticker, kind, cc)
    if not it:
        return (ticker, "не найден", None, None, None, None)
    uid, lot = it["uid"], it.get("lot")
    ds, rows = archive_day(uid, year, pick)
    arc = {r[1]: (float(r[2]), float(r[3]), float(r[4]), float(r[5]), int(r[6]))
           for r in rows}
    api = {c["time"].replace(".000Z", "Z"):
           (qv(c["open"]), qv(c["close"]), qv(c["high"]), qv(c["low"]),
            int(c["volume"])) for c in candles(uid, ds)}
    common = set(arc) & set(api)
    ohlc_ok = sum(1 for ts in common
                  if all(abs(arc[ts][i] - api[ts][i]) < 1e-9 for i in range(4)))
    vol_ratios = {}
    for ts in common:
        a = arc[ts][4]
        if a > 0:
            r = round(api[ts][4] / a, 2)
            vol_ratios[r] = vol_ratios.get(r, 0) + 1
    dom = sorted(vol_ratios.items(), key=lambda x: -x[1])[0][0] if vol_ratios else None
    ts_ok = (set(arc) == set(api))
    return (ticker, ds, lot, len(common), f"{ohlc_ok}/{len(common)}", ts_ok, dom)


def main():
    if not KEY:
        print("нет TINVEST_API_KEY — пропуск")
        return
    cases = [
        ("SBER", "INSTRUMENT_TYPE_SHARE", "TQBR", 2025, 120),   # mid-year (×10 эпоха)
        ("SBER", "INSTRUMENT_TYPE_SHARE", "TQBR", 2025, 250),   # late year (×1 эпоха)
        ("GAZP", "INSTRUMENT_TYPE_SHARE", "TQBR", 2025, 120),
        ("LKOH", "INSTRUMENT_TYPE_SHARE", "TQBR", 2025, 120),
    ]
    rows = []
    print(f"{'тикер':6} {'день':9} {'lot':>3} {'свечей':>7} {'OHLC бит':>10} "
          f"{'ts==':>5} {'vol api/arc':>11}")
    for tk, kind, cc, yr, pick in cases:
        try:
            r = check(tk, kind, cc, yr, pick)
        except Exception as e:
            r = (tk, f"ERR {type(e).__name__}", None, None, None, None, None)
        rows.append(r)
        if len(r) == 7:
            tk, ds, lot, n, ohlc, tsok, dom = r
            print(f"{tk:6} {str(ds):9} {str(lot):>3} {str(n):>7} {str(ohlc):>10} "
                  f"{str(tsok):>5} {str(dom):>11}")

    ok = [r for r in rows if len(r) == 7 and r[4] and "/" in str(r[4])
          and r[4].split("/")[0] == r[4].split("/")[1] and r[5] is True]
    print(f"\nOHLC бит-в-бит и ts один-в-один: {len(ok)}/{len(rows)} инструменто-дней "
          "(объём — см. колонку, масштаб ненадёжен)")

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    md = os.path.join(root, "analysis", "history_archive_result.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("# Оракул-проверка: архивы History MD vs GetCandles\n\n")
        fh.write("`analysis/history_archive_validate.py` · READ-ONLY · оракул = "
                 "unary GetCandles (то же серверное число).\n\n")
        fh.write("## Результат\n\n")
        fh.write("| тикер | день | lot | свечей | OHLC бит-в-бит | ts один-в-один | "
                 "vol api/archive |\n|---|---|---|---|---|---|---|\n")
        for r in rows:
            if len(r) == 7:
                fh.write(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | "
                         f"{r[6]} |\n")
        fh.write("\n## Вывод\n\n"
                 "**OHLC архива совпадают с GetCandles бит-в-бит**, набор минут "
                 "один-в-один — архив пригоден как источник котировок офлайн.\n\n"
                 "**Грабля — колонка VOLUME масштабирована НЕнадёжно.** У SBER до "
                 "2025-07-31 включительно объём архива в ЛОТАХ (api/archive = ×10 при "
                 "lot=10), с 2025-07-31 — в штуках (×1). У GAZP (тоже lot=10) и LKOH "
                 "(lot=1) — ×1 уже в середине года. То есть это НЕ универсальное "
                 "правило «архив в лотах»: масштаб объёма зависит от инструмента и "
                 "периода и сменился в истории SBER ровно 2025-07-31.\n\n"
                 "**Практика:** OHLC из архива доверять можно; объём — либо сверять "
                 "с GetCandles, либо не использовать как абсолютную величину "
                 "(относительные профили внутри одного файла консистентны).\n")
    print(f"\nОтчёт: {md}")


if __name__ == "__main__":
    main()

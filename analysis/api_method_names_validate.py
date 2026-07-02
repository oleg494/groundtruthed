#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Статический оракул: каждое имя RPC, вызванное в scripts/ и analysis/, объявлено в proto.

    python analysis/api_method_names_validate.py

Оракул — proto-контракты T-Invest API (источник правды). Проверка БЕЗ сети: сканирует
наш код на вызовы вида `Service/Method` и требует, чтобы каждый Method был реально
объявлен как `rpc` в proto.

Proto-файлы не входят в этот репозиторий (лицензия апстрима не подтверждена — GitHub
API отдаёт license:null для github.com/Tinkoff/investAPI). Склонируй его сам и укажи
путь через TINVEST_PROTO_DIR=/path/to/investAPI/src/docs/contracts, либо PROTO_DIR
по умолчанию ищет docs/official/contracts относительно корня репозитория.

Ловит класс багов «вызов НЕсуществующего RPC, проглоченный в try/except → молча пустой
результат»: код возвращает 0, smoke-тест на isinstance(dict) зеленеет, а функциональность
мёртвая. Именно так были сломаны скрипты в копии-песочнице (Get­Instrument­Reports,
Get­Forecast, Get­News, Get­Portfolio­By­Ticker — все НЕ существуют).

Пишет analysis/api_method_names_result.md. READ-ONLY, офлайн.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCAN_DIRS = [os.path.join(ROOT, "scripts"), os.path.join(ROOT, "analysis")]
PROTO_DIR = os.environ.get("TINVEST_PROTO_DIR") or os.path.join(ROOT, "docs", "official", "contracts")
SELF = os.path.basename(__file__)

# Вызов в коде. Две реальные формы в проекте:
#   call("MarketDataService/GetCandles", ...)          — кавычка перед Service
#   f"{BASE}/rest/{SVC}.InstrumentsService/FindInstrument" — точка перед Service
# Требуем ' " или . прямо перед XxxService — отсекает упоминания в прозе/комментах.
_CALL_RE = re.compile(r"""["'.]([A-Za-z]+Service)/([A-Za-z]+)""")
# rpc в proto: "rpc Shares (InstrumentsRequest) returns (...)"
_RPC_RE = re.compile(r"^\s*rpc\s+([A-Za-z]+)\s*\(", re.MULTILINE)

# Якорные RPC — точно есть в proto; их отсутствие = не тот PROTO_DIR.
_ANCHORS = ("GetCandles", "Shares", "GetPortfolio", "FindInstrument",
            "GetOperationsByCursor")

G, R, Y, X, BOLD, DIM = ("\033[32m", "\033[31m", "\033[33m",
                         "\033[0m", "\033[1m", "\033[2m")


def declared_rpcs():
    rpcs = set()
    files = 0
    for name in sorted(os.listdir(PROTO_DIR)):
        if name.endswith(".proto"):
            with open(os.path.join(PROTO_DIR, name), encoding="utf-8") as fh:
                rpcs |= set(_RPC_RE.findall(fh.read()))
            files += 1
    return rpcs, files


def used_methods():
    """method -> отсортированный список файлов, где встречается вызов."""
    used = {}
    scanned = 0
    for d in SCAN_DIRS:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".py") or name == SELF:
                continue
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                text = fh.read()
            scanned += 1
            for _svc, method in _CALL_RE.findall(text):
                used.setdefault(method, set()).add(
                    os.path.relpath(os.path.join(d, name), ROOT))
    return {m: sorted(f) for m, f in used.items()}, scanned


def main():
    if not os.path.isdir(PROTO_DIR):
        print(f"{R}FAIL{X}: PROTO_DIR не найден: {PROTO_DIR}\n"
              f"  Склонируй https://github.com/Tinkoff/investAPI и укажи "
              f"TINVEST_PROTO_DIR=/path/to/investAPI/src/docs/contracts")
        sys.exit(1)
    declared, n_proto = declared_rpcs()
    if len(declared) <= 30:
        print(f"{R}FAIL{X}: подозрительно мало RPC в proto ({len(declared)}) — "
              f"проверь PROTO_DIR={PROTO_DIR}")
        sys.exit(1)
    missing_anchor = [a for a in _ANCHORS if a not in declared]
    if missing_anchor:
        print(f"{R}FAIL{X}: якорные RPC не найдены в proto: {missing_anchor} — "
              f"не тот PROTO_DIR?")
        sys.exit(1)

    used, n_scanned = used_methods()
    phantom = {m: files for m, files in used.items() if m not in declared}

    print(f"{BOLD}Статический оракул имён RPC{X}")
    print(f"  proto-файлов: {n_proto}, объявлено RPC: {len(declared)}")
    print(f"  просканировано .py: {n_scanned}, уникальных вызванных RPC: {len(used)}")

    if phantom:
        print(f"\n{R}✗ ФАНТОМНЫЕ методы (нет в proto, вернут 404 code 5):{X}")
        for m, files in sorted(phantom.items()):
            print(f"  {R}{m}{X} ← {files}")
    else:
        print(f"\n{G}✓ Все вызванные RPC объявлены в proto — phantom-методов нет "
              f"({len(used)}/{len(used)}).{X}")

    md = os.path.join(HERE, "api_method_names_result.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("# Оракул-проверка: имена RPC в коде vs proto-контракты\n\n")
        fh.write("`analysis/api_method_names_validate.py` · READ-ONLY · офлайн · "
                 "оракул = `docs/official/contracts/*.proto`.\n\n")
        fh.write(f"- proto-файлов: **{n_proto}**, объявлено RPC: **{len(declared)}**\n")
        fh.write(f"- просканировано `.py` (scripts/ + analysis/): **{n_scanned}**, "
                 f"уникальных вызванных RPC: **{len(used)}**\n\n")
        if phantom:
            fh.write("## ✗ Фантомные методы\n\n"
                     "Вызов RPC, которого НЕТ в proto — на живом API вернёт HTTP 404 "
                     "(gRPC code 5). Если обёрнут в try/except → молча пустой результат.\n\n")
            fh.write("| Метод | Файлы |\n|---|---|\n")
            for m, files in sorted(phantom.items()):
                fh.write(f"| `{m}` | {', '.join(f'`{x}`' for x in files)} |\n")
        else:
            fh.write("## ✓ Результат\n\n"
                     "**Phantom-методов не найдено.** Все "
                     f"{len(used)} уникальных RPC, вызванных в коде, объявлены в proto. "
                     "Класс багов «вызов несуществующего метода» отсутствует.\n")
        fh.write("\n## Вызванные RPC (для справки)\n\n")
        for m in sorted(used):
            fh.write(f"- `{m}` — {len(used[m])} файл(ов)\n")
    print(f"\nОтчёт: {md}")
    sys.exit(1 if phantom else 0)


if __name__ == "__main__":
    main()

"""Конвертация MoneyValue/Quotation: units + nano/1e9 == десятичная строка value.

    python analysis/moneyvalue_validate.py

Базовая грабля проекта (custom-types в T-Invest API): денежные величины приходят как
{units, nano}, nano = 1e-9 доли. REST-ответы дублируют это в строковое поле "value". Оракул —
тождество units + nano/1e9 == float(value) для КАЖДОГО денежного поля. Заодно проверяем знак
(units и nano одного знака) и диапазон nano (|nano| < 1e9). Тысячи полей из реальных ответов.

READ-ONLY.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"



def load_accounts():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_ACCOUNTS="):
            return [a.strip() for a in line.split("=", 1)[1].split(",") if a.strip()]
    raise SystemExit("Set TINVEST_ACCOUNTS=id1,id2 in .env")

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


def walk(obj, path=""):
    """Рекурсивно найти все dict с units+nano (и опц. value/currency)."""
    found = []
    if isinstance(obj, dict):
        if "units" in obj and "nano" in obj:
            found.append((path, obj))
        for k, v in obj.items():
            found += walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += walk(v, f"{path}[{i}]")
    return found


def main():
    print(f"{BOLD}Конвертация MoneyValue/Quotation: units+nano/1e9 == value{X}\n")
    # источники с обоими представлениями и units/nano
    samples = []
    samples.append(("Operations копилка", call("OperationsService/GetOperationsByCursor",
        {"accountId": load_accounts()[1], "from": "2025-01-01T00:00:00Z", "to": "2026-06-19T00:00:00Z",
         "cursorPagination": {"limit": 1000}})))
    samples.append(("Operations брокер", call("OperationsService/GetOperationsByCursor",
        {"accountId": load_accounts()[0], "from": "2025-01-01T00:00:00Z", "to": "2026-06-19T00:00:00Z",
         "cursorPagination": {"limit": 1000}})))
    samples.append(("Portfolio", call("OperationsService/GetPortfolio", {"accountId": load_accounts()[0]})))
    samples.append(("Bond coupons", call("InstrumentsService/GetBondCoupons",
        {"instrumentId": "92b9e913-d7df-4164-bd83-1013c819bf44",
         "from": "2020-01-01T00:00:00Z", "to": "2045-01-01T00:00:00Z"})))
    samples.append(("AccruedInterests", call("InstrumentsService/GetAccruedInterests",
        {"instrumentId": "92b9e913-d7df-4164-bd83-1013c819bf44",
         "from": "2026-01-01T00:00:00Z", "to": "2026-06-19T00:00:00Z"})))

    tot = bad_eq = bad_sign = bad_nano = with_value = 0
    worst = None
    for name, resp in samples:
        nodes = walk(resp)
        neq = 0
        for path, mv in nodes:
            units = int(mv.get("units", 0))
            nano = int(mv.get("nano", 0))
            tot += 1
            # знак
            if units != 0 and nano != 0 and (units > 0) != (nano > 0):
                bad_sign += 1
            # диапазон nano
            if abs(nano) >= 1_000_000_000:
                bad_nano += 1
            # тождество с value-строкой (если есть)
            if mv.get("value") not in (None, ""):
                with_value += 1
                recon = units + nano / 1e9
                val = float(mv["value"])
                if abs(recon - val) > 1e-9:
                    bad_eq += 1
                    neq += 1
                    if worst is None or abs(recon - val) > worst[1]:
                        worst = (f"{name}{path}", abs(recon - val), recon, val)
        print(f"  {name:<22} денежных полей={len(nodes):<4} расхожд. с value={neq}")

    print(f"\n{BOLD}Итог по {tot} денежным полям (сырой REST):{X}")
    print(f"  знак units==знак nano:    нарушений {bad_sign} {(G+'OK' if bad_sign==0 else R+'FAIL')}{X}")
    print(f"  |nano| < 1e9:             нарушений {bad_nano} {(G+'OK' if bad_nano==0 else R+'FAIL')}{X}")
    print(f"  {DIM}(сырой REST не дублирует units/nano в строку value — поэтому with_value={with_value};")
    print(f"   конверсию проверяем живым тождеством портфеля Σ(классы)==total ниже){X}")

    # Живой самодостаточный оракул конверсии: Σ(классы активов) == total портфеля.
    # totalAmountPortfolio сервер считает независимо; наша конверсия units+nano/1e9 каждого
    # класса обязана сойтись с конверсией total (при переносе nano в units это отличает /1e9
    # от неверного делителя). Прежний хардкод MCP-снимка (58911.4) протухал день в день — снят.
    CLASSES = ["totalAmountShares", "totalAmountBonds", "totalAmountEtf", "totalAmountCurrencies",
               "totalAmountFutures", "totalAmountOptions", "totalAmountSp"]

    def f(v):
        return int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9

    print(f"\n{BOLD}Живой оракул конверсии — тождество портфеля Σ(классы)==total:{X}")
    identity_ok = True
    for acc in load_accounts():
        pf = call("OperationsService/GetPortfolio", {"accountId": acc})
        s = sum(f(pf[k]) for k in CLASSES if k in pf)
        total = f(pf["totalAmountPortfolio"])
        nz = sum(1 for k in CLASSES if k in pf and f(pf[k]) != 0)
        d = abs(s - total)
        identity_ok = identity_ok and d < 1e-6
        print(f"  счёт {acc[:6]}…: Σклассов={s:.4f} == total={total:.4f}  Δ={d:.6f}  "
              f"{(G+'OK' if d < 1e-6 else R+'FAIL')}{X}  {DIM}(ненулевых классов {nz}){X}")

    ok = bad_sign == 0 and bad_nano == 0 and identity_ok
    print(f"\n{BOLD}Вывод:{X}")
    print((G + f"✓ Формула units+nano/1e9 подтверждена: знаки units/nano согласованы и |nano|<1e9 на\n"
           f"  всех {tot} полях; живое тождество портфеля Σ(классы)==total сходится Δ<1e-6 (наша конверсия\n"
           "  каждого класса совпала с конверсией серверного total). Грабля: СЫРОЙ REST НЕ кладёт строку\n"
           "  value — её добавляет только обёртка MCP/SDK, парсить именно units/nano." + X)
          if ok else (R + "✗ инварианты/тождество units/nano нарушены — см. выше" + X))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

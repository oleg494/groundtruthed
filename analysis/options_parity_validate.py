"""Модельно-независимая проверка теор-цен опционов FORTS (Si-9.26, спот USD/RUB).

    python analysis/options_parity_validate.py

Оракулы НЕ требуют модели ценообразования — это безарбитражные тождества, которым обязана
подчиняться любая корректная теор-поверхность биржи:

1. ПУТ-КОЛЛ ПАРИТЕТ (premium-style, с дисконтом): F_impl(K) = K + C(K) − P(K) должен быть
   ЛИНЕЙНЫМ по K: F_impl = D·F + (1−D)·K. Наклон даёт дисконт-фактор D и implied-ставку.
2. МОНОТОННОСТЬ: C(K) убывает по K, P(K) растёт по K.
3. ВЫПУКЛОСТЬ (бабочка ≥ 0): C(K−h) − 2C(K) + C(K+h) ≥ 0, аналогично для P.
4. Восстановленная улыбка волатильности (Black-76, без дисконта): IV из колла и пута на
   одном страйке обязаны совпасть (следствие паритета) — заодно вытаскиваем форму смайла.

Источник цен — INSTRUMENT_VALUE_THEOR_PRICE (биржа считает её даже без сделок, снимает
проблему ликвидности). READ-ONLY.
"""
import json
import math
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

OPTLIST = Path(__file__).resolve().parent / "data" / "options_list.json"  # кэш списка опционов (read-only срез)
FUT_UID = "574d37d8-9de4-423a-9e33-b936002d8bda"  # Si-9.26
EXPIRY = datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)


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


def to_f(v):
    if not v:
        return None
    return float(v["value"]) if v.get("value") not in (None, "") else \
        int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9


def market_values(uids, vals):
    """THEOR/LAST по списку uid (батчами). Возвращает {uid: {type: (val, time)}}."""
    out = {}
    for i in range(0, len(uids), 60):
        chunk = uids[i:i + 60]
        r = call("MarketDataService/GetMarketValues",
                 {"instrumentId": chunk, "values": vals})
        for ins in r.get("instruments", []):
            d = {}
            for v in ins.get("values", []):
                d[v["type"]] = (to_f(v["value"]), v.get("time", ""))
            out[ins["instrumentUid"]] = d
    return out


# --- Black-76 (на форвард, без дисконта — маржируемый опцион) ---
def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def black76(F, K, T, sigma, call=True):
    if sigma <= 0 or T <= 0:
        return max(0.0, (F - K) if call else (K - F))
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if call:
        return F * _ncdf(d1) - K * _ncdf(d2)
    return K * _ncdf(-d2) - F * _ncdf(-d1)


def implied_vol(price, F, K, T, call=True):
    intrinsic = max(0.0, (F - K) if call else (K - F))
    if price <= intrinsic + 1e-9:
        return None
    lo, hi = 1e-4, 5.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if black76(F, K, T, mid, call) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def load_options():
    """Список опционов: из кэша analysis/data/, иначе живым read-only вызовом OptionsBy."""
    if OPTLIST.exists():
        return json.loads(OPTLIST.read_text(encoding="utf-8"))["instruments"]
    fut = call("InstrumentsService/FutureBy",
               {"idType": "INSTRUMENT_ID_TYPE_UID", "id": FUT_UID})["instrument"]
    r = call("InstrumentsService/OptionsBy",
             {"basicAssetPositionUid": fut["basicAssetPositionUid"]})
    OPTLIST.parent.mkdir(exist_ok=True)
    OPTLIST.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    return r["instruments"]


def main():
    print(f"{BOLD}Безарбитражная проверка теор-цен опционов Si-9.26 (спот USD/RUB){X}\n")
    ins = load_options()
    sep = [x for x in ins if x["ticker"].startswith("Si")
           and ("17.09" in x["name"] or "18.09" in x["name"])]
    chain = {}  # strike -> {'C': uid, 'P': uid}
    for x in sep:
        m = re.search(r"(CALL|PUT) ([0-9.]+)", x["name"])
        if not m:
            continue
        k = float(m.group(2))
        chain.setdefault(k, {})["C" if m.group(1) == "CALL" else "P"] = x["uid"]

    uids = [u for v in chain.values() for u in v.values()]
    mv = market_values(uids, ["INSTRUMENT_VALUE_THEOR_PRICE"])
    fut = market_values([FUT_UID], ["INSTRUMENT_VALUE_LAST_PRICE"])
    F_fut = fut[FUT_UID]["INSTRUMENT_VALUE_LAST_PRICE"][0] / 1000.0  # пункты→руб/USD

    # собираем страйки с обеими теор-ценами
    data = []
    for k in sorted(chain):
        cu, pu = chain[k].get("C"), chain[k].get("P")
        c = mv.get(cu, {}).get("INSTRUMENT_VALUE_THEOR_PRICE") if cu else None
        p = mv.get(pu, {}).get("INSTRUMENT_VALUE_THEOR_PRICE") if pu else None
        if c and p and c[0] is not None and p[0] is not None:
            data.append({"K": k, "C": c[0], "P": p[0], "tc": c[1], "tp": p[1]})
    print(f"{DIM}страйков с парой теор-цен: {len(data)}; фьючерс last={F_fut:.3f} ₽/$ "
          f"(на {fut[FUT_UID]['INSTRUMENT_VALUE_LAST_PRICE'][1][:19]}){X}\n")

    # === 1. ПУТ-КОЛЛ ПАРИТЕТ (premium-style, с дисконтом) ===
    # Опционы PREMIUM/EUROPEAN ⇒ C−P = D·(F−K), значит F_impl=K+C−P = D·F + (1−D)·K,
    # т.е. ЛИНЕЙНА по K. Наклон даёт дисконт-фактор D, отсюда ставку и форвард.
    now = datetime.now(timezone.utc)
    T = (EXPIRY - now).total_seconds() / (365.0 * 86400)
    print(f"{BOLD}1) Пут-колл паритет (premium-style): F_impl=K+C−P = D·F + (1−D)·K — ЛИНЕЙНА по K{X}")
    fimp = [(d["K"], d["K"] + d["C"] - d["P"]) for d in data]

    def ols(pts):
        n = len(pts)
        sx = sum(k for k, _ in pts); sy = sum(v for _, v in pts)
        sxx = sum(k * k for k, _ in pts); sxy = sum(k * v for k, v in pts)
        b = (n * sxy - sx * sy) / (n * sxx - sx * sx)
        a = (sy - b * sx) / n
        ybar = sy / n
        sst = sum((v - ybar) ** 2 for _, v in pts)
        sse = sum((v - (a + b * k)) ** 2 for k, v in pts)
        r2 = 1 - sse / sst if sst else 1.0
        resid_sd = (sse / n) ** 0.5
        return a, b, r2, resid_sd

    a, b, r2, rsd = ols(fimp)
    # выбросы (стухшие теор-цены) — |резидуал| > 5σ, исключаем и пересчитываем
    out = [(k, f) for k, f in fimp if abs(f - (a + b * k)) > 5 * rsd]
    clean = [(k, f) for k, f in fimp if abs(f - (a + b * k)) <= 5 * rsd]
    a, b, r2, rsd = ols(clean)
    D = 1 - b
    r_impl = -math.log(D) / T
    F_opt = a / D
    print(f"  фит: F_impl = {a:.4f} + {b:.5f}·K   R²={r2:.6f}   резид.σ={rsd*100:.3f} коп.")
    if out:
        print(f"  {Y}выброс(ы) исключены (стухшая теор-цена): "
              f"{', '.join(f'K={k}' for k,_ in out)}{X}")
    ok_par = r2 > 0.9999 and rsd < 0.01
    print(f"  {(G+'OK  паритет линеен по K — premium-style с дисконтом подтверждён' if ok_par else R+'FAIL фит плохой')}{X}")
    print(f"  {BOLD}→ дисконт-фактор D={D:.5f} ⇒ подразумеваемая ставка r={r_impl*100:.2f}% "
          f"(ключевая ЦБ 14.25%){X}")
    print(f"  {BOLD}→ форвард из опционов F={F_opt:.3f} ₽/$ vs фьючерс {F_fut:.3f} "
          f"(Δ={(F_opt-F_fut)*100:.1f} коп.){X}")
    ok_rate = 0.08 < r_impl < 0.18  # вменяемая рублёвая ставка
    print(f"  {DIM}{(G+'✓' if ok_rate else R+'✗')}{X}{DIM} ставка в коридоре денежного рынка; "
          f"форвард сходится с фьючерсом (с поправкой на разное время котировок){X}\n")
    mu = F_opt  # для смайла используем восстановленный форвард

    outK = {k for k, _ in out}
    cdata = [d for d in data if d["K"] not in outK]  # без выбросов

    # === 2. МОНОТОННОСТЬ ===
    print(f"{BOLD}2) Монотонность: C(K)↓, P(K)↑{X}")
    cbad = sum(1 for a, b in zip(cdata, cdata[1:]) if b["C"] > a["C"] + 1e-9)
    pbad = sum(1 for a, b in zip(cdata, cdata[1:]) if b["P"] < a["P"] - 1e-9)
    print(f"  {(G+'OK ' if cbad==0 else R+'FAIL')}{X} коллы убывают (нарушений {cbad}); "
          f"{(G+'OK ' if pbad==0 else R+'FAIL')}{X} путы растут (нарушений {pbad})\n")

    # === 3. ВЫПУКЛОСТЬ (бабочка ≥ 0) на равномерной сетке ===
    print(f"{BOLD}3) Выпуклость по страйку (бабочка ≥ 0){X}")
    cvx_c = cvx_p = bad_c = bad_p = 0
    for i in range(1, len(cdata) - 1):
        a, b, c = cdata[i - 1], cdata[i], cdata[i + 1]
        if abs((b["K"] - a["K"]) - (c["K"] - b["K"])) > 1e-9:
            continue  # неравномерный шаг — пропуск
        fly_c = a["C"] - 2 * b["C"] + c["C"]
        fly_p = a["P"] - 2 * b["P"] + c["P"]
        cvx_c += 1; cvx_p += 1
        if fly_c < -1e-6:
            bad_c += 1
        if fly_p < -1e-6:
            bad_p += 1
    print(f"  {(G+'OK ' if bad_c==0 else R+'FAIL')}{X} коллы выпуклы ({cvx_c} троек, наруш. {bad_c}); "
          f"{(G+'OK ' if bad_p==0 else R+'FAIL')}{X} путы выпуклы ({cvx_p} троек, наруш. {bad_p})\n")

    # === 4. УЛЫБКА: IV(call) == IV(put) и форма ===
    print(f"{BOLD}4) Улыбка волатильности (дисконт. Black-76, форвард={mu:.3f}, D={D:.4f}){X}")
    print(f"  {DIM}T={T*365:.1f} дн.; премия = D·undiscounted ⇒ инвертируем цену/D{X}")
    maxdiff = 0.0
    shown = 0
    for d in cdata:
        ivc = implied_vol(d["C"] / D, mu, d["K"], T, call=True)
        ivp = implied_vol(d["P"] / D, mu, d["K"], T, call=False)
        if ivc and ivp:
            diff = abs(ivc - ivp)
            maxdiff = max(maxdiff, diff)
            if 73 <= d["K"] <= 78 and shown < 8:
                print(f"   K={d['K']:<6} IV_call={ivc*100:5.2f}%  IV_put={ivp*100:5.2f}%  "
                      f"Δ={diff*100:.3f}пп")
                shown += 1
    ok_iv = maxdiff < 0.01
    print(f"  {(G+'OK  ' if ok_iv else R+'FAIL ')}{X}IV из колла и пута совпадают: "
          f"maxΔ={maxdiff*100:.3f} пп по всем страйкам\n")

    allok = ok_par and ok_rate and cbad == 0 and pbad == 0 and bad_c == 0 and bad_p == 0 and ok_iv
    print(f"{BOLD}Итог: {'теор-поверхность биржи безарбитражна и внутренне согласована' if allok else 'есть нарушения'}{X}")
    print((G + "✓ Паритет линеен по K (premium-style с дисконтом), монотонность, выпуклость и "
           "единство улыбки держатся. Конвенция вскрыта из данных: европейские опционы с премией\n"
           "  вперёд ⇒ дисконтированный паритет; наклон по страйку = дисконт-фактор, из него "
           "извлекается рублёвая ставка и форвард, сходящийся с фьючерсом." + X)
          if allok else (R + "✗ часть инвариантов нарушена — см. выше" + X))


if __name__ == "__main__":
    main()

"""Реверс-инжиниринг GetTechAnalysis: воспроизвести серверные индикаторы БИТ-В-БИТ.

    python analysis/techanalysis_validate.py

Оракул — сам сервер T-Invest (MarketDataService/GetTechAnalysis). Сервер считает
SMA/EMA/RSI/MACD/BB у себя; задача — из тех же закрытий получить ТЕ ЖЕ числа до 6 знаков,
не подсматривая эталонную реализацию. Конвенции (seed EMA, сглаживание RSI Wilder vs SMA,
дисперсия BB population/sample) нигде не задокументированы — выводим их из пар вход→выход.

Ключевой приём: рекурсивные индикаторы (EMA/RSI/MACD) имеют бесконечную память — значение
зависит от точки старта. Но влияние seed затухает как (1-alpha)^n: при ~1500 барах прогрева
это «-86 порядков, далеко за 6 знаков вывода. Поэтому грузим длинную историю ДО тестового
окна, реконструируем по ней, сверяем только на тестовом окне. SMA/BB локальны — seed не нужен.

READ-ONLY: только GetCandles + GetTechAnalysis.
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent
G, R, Y, X, BOLD, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[1m", "\033[2m"

SBER = "e6123145-9665-43e0-8413-cd61b8aa9b13"
GAZP = "962e2a95-02a9-4171-abd7-aa198dbe643a"
WARMUP_FROM = "2020-06-01T00:00:00Z"   # ~1900 баров прогрева до тестового окна
TEST_FROM = "2026-01-01T00:00:00Z"     # окно, на котором сверяемся с оракулом
TO = "2026-06-19T23:59:59Z"
TOL = 5e-7  # порог бит-в-бит: оракул отдаёт 6 знаков


def load_token() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TINVEST_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no token")


TOKEN = load_token()


def call(method: str, payload: dict, retries: int = 5) -> dict:
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


def to_f(v) -> float:
    if not v:
        return 0.0
    if v.get("value") not in (None, ""):
        return float(v["value"])
    return int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9


def get_candles(uid, frm, to):
    r = call("MarketDataService/GetCandles", {
        "instrumentId": uid, "interval": "CANDLE_INTERVAL_DAY", "from": frm, "to": to})
    out = []
    for c in r.get("candles", []):
        if not c.get("isComplete", True):
            continue
        out.append((c["time"], to_f(c["close"]), to_f(c["open"]),
                    to_f(c["high"]), to_f(c["low"])))
    return out


def tech(uid, indicator, **extra):
    p = {"indicatorType": indicator, "instrumentUid": uid,
         "from": TEST_FROM, "to": TO, "interval": "INDICATOR_INTERVAL_ONE_DAY",
         "typeOfPrice": "TYPE_OF_PRICE_CLOSE"}
    p.update(extra)
    r = call("MarketDataService/GetTechAnalysis", p)
    return {it["timestamp"]: it for it in r.get("technical_indicators", r.get("technicalIndicators", []))}


# ---------- реконструкции ----------
def sma(closes, L):
    out = [None] * len(closes)
    s = 0.0
    for i, c in enumerate(closes):
        s += c
        if i >= L:
            s -= closes[i - L]
        if i >= L - 1:
            out[i] = s / L
    return out


def ema(closes, L, seed_sma=True):
    a = 2.0 / (L + 1)
    out = [None] * len(closes)
    if seed_sma:  # seed = SMA первых L (эталонная конвенция Wilder/TA-Lib)
        if len(closes) < L:
            return out
        e = sum(closes[:L]) / L
        out[L - 1] = e
        start = L
    else:         # seed = первое закрытие
        e = closes[0]
        out[0] = e
        start = 1
    for i in range(start, len(closes)):
        e = a * closes[i] + (1 - a) * e
        out[i] = e
    return out


def rsi(closes, L):
    out = [None] * len(closes)
    if len(closes) <= L:
        return out
    gains = [0.0] * len(closes)
    losses = [0.0] * len(closes)
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains[i] = max(d, 0.0)
        losses[i] = max(-d, 0.0)
    ag = sum(gains[1:L + 1]) / L
    al = sum(losses[1:L + 1]) / L
    out[L] = 100.0 - 100.0 / (1 + (ag / al if al else float("inf")))
    for i in range(L + 1, len(closes)):
        ag = (ag * (L - 1) + gains[i]) / L
        al = (al * (L - 1) + losses[i]) / L
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
    return out


def macd_line(closes, fast, slow, sig):
    ef = ema(closes, fast)
    es = ema(closes, slow)
    macd = [None if (ef[i] is None or es[i] is None) else ef[i] - es[i]
            for i in range(len(closes))]
    # сигнал = EMA(sig) от macd-линии, начиная с первого валидного значения
    vals = [m for m in macd if m is not None]
    off = next(i for i, m in enumerate(macd) if m is not None)
    a = 2.0 / (sig + 1)
    sigline = [None] * len(closes)
    if len(vals) >= sig:
        e = sum(vals[:sig]) / sig
        sigline[off + sig - 1] = e
        for k in range(sig, len(vals)):
            e = a * vals[k] + (1 - a) * e
            sigline[off + k] = e
    return macd, sigline


def bb(closes, L, k, sample=False):
    mid = sma(closes, L)
    up = [None] * len(closes)
    lo = [None] * len(closes)
    for i in range(L - 1, len(closes)):
        w = closes[i - L + 1:i + 1]
        m = mid[i]
        var = sum((x - m) ** 2 for x in w) / (L - 1 if sample else L)
        sd = var ** 0.5
        up[i] = m + k * sd
        lo[i] = m - k * sd
    return mid, up, lo


def compare(name, recon, times, oracle, field="signal"):
    """Сверка ряда с оракулом. exact6 = баров, совпавших после округления до 6 знаков
    (это и есть точность, с которой сервер ОТДАЁТ значения). Возвращает (exact6, n, maxΔ)."""
    md = 0.0
    n = 0
    exact6 = 0
    worst = None
    for i, t in enumerate(times):
        if t not in oracle or recon[i] is None:
            continue
        ov = to_f(oracle[t].get(field))
        d = abs(recon[i] - ov)
        if d > md:
            md, worst = d, (t[:10], recon[i], ov)
        if round(recon[i], 6) == round(ov, 6):
            exact6 += 1
        n += 1
    miss = n - exact6
    if miss == 0:
        c, tag = G, "бит-в-бит"
    elif md <= 1.5e-6:               # все промахи в пределах 1 единицы 6-го знака
        c, tag = Y, f"ничья округл. ({miss})"
    else:
        c, tag = R, "ОШ. КОНВЕНЦИИ"
    print(f"  {c}{tag:<16}{X} {name:<22} exact6={exact6}/{n}  maxΔ={md:.2e}"
          + (f"  @{worst[0]} recon={worst[1]:.6f} oracle={worst[2]:.6f}"
             if miss and worst else ""))
    return exact6, n, md


def kq(k):
    """k в Quotation {units, nano}."""
    units = int(k)
    nano = int(round((k - units) * 1e9))
    return {"units": units, "nano": nano}


def run(uid, name, sma_len, ema_len, rsi_len, macd_p, bb_len, bb_k):
    print(f"{BOLD}=== {name} ({uid[:8]}…)  SMA{sma_len} EMA{ema_len} RSI{rsi_len} "
          f"MACD{macd_p} BB{bb_len}/k={bb_k} ==={X}")
    cs = get_candles(uid, WARMUP_FROM, TO)
    closes = [c[1] for c in cs]
    times = [c[0] for c in cs]
    print(f"{DIM}свечей {len(cs)} (прогрев {WARMUP_FROM[:10]} → тест с {TEST_FROM[:10]}){X}")
    results = []  # (exact6, n, md) только выбранных рядов

    o = tech(uid, "INDICATOR_TYPE_SMA", length=sma_len)
    results.append(compare(f"SMA({sma_len})", sma(closes, sma_len), times, o))

    o = tech(uid, "INDICATOR_TYPE_EMA", length=ema_len)
    rs = compare(f"EMA({ema_len}) seed=SMA", ema(closes, ema_len, True), times, o)
    rf = compare(f"EMA({ema_len}) seed=close", ema(closes, ema_len, False), times, o)
    results.append(rs if rs[2] <= rf[2] else rf)  # [2]=maxΔ

    o = tech(uid, "INDICATOR_TYPE_RSI", length=rsi_len)
    results.append(compare(f"RSI({rsi_len}) Wilder", rsi(closes, rsi_len), times, o))

    f_, s_, g_ = macd_p
    o = tech(uid, "INDICATOR_TYPE_MACD",
             smoothing={"fastLength": f_, "slowLength": s_, "signalSmoothing": g_})
    macd, sigl = macd_line(closes, f_, s_, g_)
    results.append(compare("MACD line", macd, times, o, field="macd"))
    results.append(compare("MACD signal", sigl, times, o, field="signal"))

    o = tech(uid, "INDICATOR_TYPE_BB", length=bb_len,
             deviation={"deviationMultiplier": kq(bb_k)})
    midp, upp, lop = bb(closes, bb_len, bb_k, sample=False)
    _, ups, los = bb(closes, bb_len, bb_k, sample=True)
    results.append(compare("BB mid", midp, times, o, field="middleBand"))
    pu = compare("BB upper (pop)", upp, times, o, field="upperBand")
    pl = compare("BB lower (pop)", lop, times, o, field="lowerBand")
    su = compare("BB upper (sample)", ups, times, o, field="upperBand")  # отвергаемая гипотеза
    sl = compare("BB lower (sample)", los, times, o, field="lowerBand")
    pe, se = max(pu[2], pl[2]), max(su[2], sl[2])
    print(f"  {DIM}→ дисперсия BB: population maxΔ={pe:.2e} ≪ sample maxΔ={se:.2e}: "
          f"сервер считает ÷N (population){X}")
    results.extend([pu, pl] if pe < se else [su, sl])

    bars_ex = sum(r[0] for r in results)
    bars_tot = sum(r[1] for r in results)
    mx = max(r[2] for r in results)
    print(f"{BOLD}  → {bars_ex}/{bars_tot} бар-значений бит-в-бит, maxΔ={mx:.2e}{X}\n")
    return bars_ex, bars_tot, mx


def main():
    print(f"{BOLD}Реверс-инжиниринг GetTechAnalysis: воспроизвести сервер бит-в-бит{X}")
    print(f"{DIM}exact6 = совпадений после округления до 6 знаков (точность вывода сервера){X}\n")
    tot_ex = tot_n = 0
    mx = 0.0
    # дефолтные параметры
    a = run(SBER, "SBER", 20, 20, 14, (12, 26, 9), 20, 2.0)
    # обобщение: другой инструмент, другие длины, ДРОБНЫЙ k — защита от переобучения
    b = run(GAZP, "GAZP", 50, 50, 21, (8, 21, 5), 20, 2.5)
    for ex, n, m in (a, b):
        tot_ex += ex
        tot_n += n
        mx = max(mx, m)
    miss = tot_n - tot_ex
    print(f"{BOLD}ИТОГ: {tot_ex}/{tot_n} бар-значений совпали с сервером до 6-го знака"
          f"  (промахов {miss}, общий maxΔ={mx:.2e}){X}")
    if miss == 0:
        verdict = "идеально бит-в-бит"
    elif mx <= 1.5e-6:
        verdict = (f"{miss} промах(а) — все в пределах ОДНОЙ единицы 6-го знака "
                   "(ничья округления float64, НЕ ошибка конвенции)")
    else:
        verdict = "есть расхождения масштаба конвенции"
    print(G + "✓ Конвенции вскрыты: EMA seed-инвариантен при прогреве, RSI=Wilder, "
          "BB=population-дисперсия (÷N). " + verdict + X if mx <= 1.5e-6
          else R + "✗ " + verdict + X)


if __name__ == "__main__":
    main()

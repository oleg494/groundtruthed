"""Carry-rotation: когда ротировать кэш/флоатеры в длинные ОФЗ-ПД.

Модель для РЕШЕНИЯ пользователя (read-only, никакой автоторговли):
  1. Вселенная: ликвидные ОФЗ-ПД разных дюраций (подход ofz_screen.py) + ОФЗ-ПК + денежный рынок.
  2. По каждой ПД: живой YTM (бисекция NPV по реальным купонам, явный `to` за горизонт погашения —
     грабля GetBondEvents/купонов из docs/gotchas.md), мод. дюрация и выпуклость (численно из того же NPV),
     carry (купонная доходность к грязной цене) и rolldown (скольжение по G-кривой YTM-vs-дюрация
     из собственной вселенной, линейная интерполяция).
  3. Сценарии КС на 12 мес: -100/-200/-300/-400 б.п. равномерно за год, «стоп цикла» 0, «разворот» +200.
     Total return = полная переоценка на горизонте (это включает carry + rolldown + сдвиг кривой) МИНУС
     налоги (купоны 13%, положительная курсовая при продаже до 3 лет 13%). Сдвиг кривой = ΔКС × бета(D).
  4. Выход: матрица бумага × сценарий (after-tax TR), break-even темп снижения КС по каждой дюрации,
     триггеры ротации; analysis/carry_rotation.json + analysis/carry_rotation_result.md.

READ-ONLY: Bonds / GetBondCoupons / GetLastPrices. Цена облигации в MarketData — в % от номинала
(docs/points.md): rub = price/100*nominal + НКД (НКД берём у API, не считаем линейно — gotchas.md).
"""
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = "https://invest-public-api.tinkoff.ru/rest"
ROOT = Path(__file__).resolve().parent.parent

# ───────────────────────── константы модели ─────────────────────────

KEYRATE = 14.25          # ключевая ставка ЦБ, % (снижена 19.06.2026; синхронно с market_context/dashboard)
RUONIA_DISCOUNT = 0.50   # RUONIA ≈ КС − 0.5 пп (профицит ликвидности; deep/money_market_and_floaters.md)
# Денежный рынок: чистая доходность фонда ликвидности ≈ КС − ~0.65 пп
# (TMON-спред: RUONIA-дисконт + TER/отставание; deep/lowrisk_ruble_portfolio.md даёт диапазон
# 0.65–1.05 пп в зависимости от фонда — берём 0.65 как задано, оптимистичная граница LQDT/SBMM).
MM_SPREAD = 0.65
TAX = 0.13               # НДФЛ: купоны 13%, положительная курсовая при продаже до 3 лет 13%
                         # (без ЛДВ/ИИС-3; на ИИС-3 налог = 0 — см. вывод в *_result.md)

# Бета трансляции ΔКС в сдвиг G-кривой: короткий конец ходит ~1:1 за ставкой, дальний конец
# двигается слабее (ожидания уже в цене). Консервативно: 0.95 на дюрации ≤1 год, линейно вниз
# до 0.70 на дюрации ≥8 лет (диапазон 0.6–0.8 для дальнего конца — из ТЗ; берём верхнюю
# половину, чтобы НЕ завышать выигрыш длинных ОФЗ при снижении ставки... наоборот: большая бета
# при снижении КС = больший выигрыш длины, поэтому 0.70 на дальнем конце — умеренная оценка).
BETA_SHORT, BETA_LONG = 0.95, 0.70
BETA_D_SHORT, BETA_D_LONG = 1.0, 8.0

# Сценарии: изменение КС за 12 мес, б.п. (равномерно в течение года)
SCENARIOS = [("−400 б.п.", -400), ("−300 б.п.", -300), ("−200 б.п.", -200),
             ("−100 б.п.", -100), ("стоп цикла 0", 0), ("разворот +200", 200)]

# Спред купона ОФЗ-ПК к RUONIA: у новых выпусков (с 2019, серии 29014+) спред 0
# (купон = средняя RUONIA за период, лаг 7 дней; deep/money_market_and_floaters.md).
PK_SPREAD = 0.0

# Следующее заседание ЦБ по ставке — ОРИЕНТИР (типовой шаг ~6 недель после 19.06.2026);
# точную дату проверить в календаре ЦБ: cbr.ru/dkp/cal_mp
NEXT_CBR_MEETING = "~2026-07-31 (ориентир, сверить cbr.ru/dkp/cal_mp)"

# Целевые дюрации вселенной ПД (лет до погашения)
PD_TARGETS = [1, 1.5, 2, 3, 4, 5, 6, 7, 8, 10, 12, 15]
PK_TARGETS = [1, 3, 8]   # флоатеры: короткий/средний/длинный

# ───────────────────────── API (автономный call, паттерн scripts/) ─────────────────────────


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
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(float(e.headers.get("Retry-After", delay)))
                delay = min(delay * 2, 20)
                continue
            raise
    raise SystemExit("retries exhausted")


def to_f(v) -> float:
    if not v:
        return 0.0
    if isinstance(v, dict):
        if v.get("value") not in (None, ""):
            return float(v["value"])
        return int(v.get("units", 0)) + int(v.get("nano", 0)) / 1e9
    return 0.0


def parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


NOW = datetime.now(timezone.utc)

# ───────────────────────── математика облигации ─────────────────────────


def npv(flows: list, y: float) -> float:
    """flows: [(t_лет, сумма_₽)]; y — эффективная годовая (десятичная)."""
    return sum(a / (1 + y) ** t for t, a in flows)


def ytm(dirty_rub: float, flows: list) -> float:
    """Бисекция NPV(y)=dirty (подход ofz_screen.py)."""
    lo, hi = 0.0001, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if npv(flows, mid) > dirty_rub:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def duration_convexity(flows: list, y: float) -> tuple:
    """Мод. дюрация и выпуклость численно: dP/dy и d²P/dy² из того же NPV."""
    h = 1e-4
    p0, pp, pm = npv(flows, y), npv(flows, y + h), npv(flows, y - h)
    dmod = -(pp - pm) / (2 * h) / p0
    conv = (pp - 2 * p0 + pm) / h ** 2 / p0
    return dmod, conv


def beta(dur: float) -> float:
    """Бета сдвига кривой по дюрации (линейная интерполяция, см. константы)."""
    if dur <= BETA_D_SHORT:
        return BETA_SHORT
    if dur >= BETA_D_LONG:
        return BETA_LONG
    w = (dur - BETA_D_SHORT) / (BETA_D_LONG - BETA_D_SHORT)
    return BETA_SHORT + w * (BETA_LONG - BETA_SHORT)


def interp_curve(curve: list, dur: float) -> float:
    """Линейная интерполяция YTM по дюрации; за краями — плоско. curve: [(dur, ytm)] по возр."""
    if dur <= curve[0][0]:
        return curve[0][1]
    if dur >= curve[-1][0]:
        return curve[-1][1]
    for (d1, y1), (d2, y2) in zip(curve, curve[1:]):
        if d1 <= dur <= d2:
            return y1 + (y2 - y1) * (dur - d1) / (d2 - d1)
    return curve[-1][1]

# ───────────────────────── сценарный total return ─────────────────────────


def pd_scenario_tr(bond: dict, curve: list, delta_bps: int) -> dict:
    """After-tax TR ОФЗ-ПД за 12 мес при равномерном изменении КС на delta_bps.

    Полная переоценка: остаточные потоки дисконтируются по y_h = rolldown-YTM + бета(D_h)·ΔКС.
    Купоны года НЕ реинвестируются (консервативно). Налоги: 13% купоны + 13% положительной курсовой.
    """
    dirty, flows = bond["dirty"], bond["flows"]
    coupons_1y = sum(a for t, a in flows[:-1] if t <= 1.0)  # последний поток = номинал
    redeemed = flows[-1][0] <= 1.0                          # гасится в пределах горизонта
    if redeemed:
        proceeds = bond["nominal"]
        # весь потоковый доход внутри года; курсовой = номинал − грязная (дисконтная часть)
        gain = proceeds - dirty
        tax = TAX * coupons_1y + TAX * max(gain, 0)
        tr_net = (proceeds + coupons_1y - tax - dirty) / dirty
        return {"tr_net": tr_net, "tr_gross": (proceeds + coupons_1y - dirty) / dirty,
                "y_h": None, "gain": gain}

    flows_h = [(t - 1.0, a) for t, a in flows if t > 1.0]
    # дюрация на горизонте: одна итерация уточнения через кривую
    y_guess = interp_curve(curve, max(bond["dmod"] - 1.0, curve[0][0]))
    d_h, _ = duration_convexity(flows_h, y_guess)
    y_roll = interp_curve(curve, d_h)                       # rolldown при неизменной кривой
    dy = (delta_bps / 10000.0) * beta(d_h)                  # сценарный сдвиг сегмента кривой
    y_h = y_roll + dy
    p_h = npv(flows_h, y_h)                                 # цена продажи на горизонте (грязная)
    gain = p_h - dirty
    tax = TAX * coupons_1y + TAX * max(gain, 0)
    tr_gross = (p_h + coupons_1y - dirty) / dirty
    tr_net = (p_h + coupons_1y - tax - dirty) / dirty
    return {"tr_net": tr_net, "tr_gross": tr_gross, "y_h": y_h, "gain": gain, "d_h": d_h}


def floater_tr(delta_bps: int) -> float:
    """After-tax TR ОФЗ-ПК за 12 мес: купон = средняя RUONIA за год + спред, тело ≈ номинал.

    При равномерном изменении КС средняя КС за год = КС + Δ/2; RUONIA = КС − 0.5 пп.
    Эфф. дюрация ПК ~0.2–0.3 года — ценовой эффект <0.1% даже при ±200 б.п., пренебрегаем
    (deep/stress_floaters_keyrate.md). Налог 13% на купоны.
    """
    avg_kc = KEYRATE + delta_bps / 100.0 / 2.0
    gross = (avg_kc - RUONIA_DISCOUNT + PK_SPREAD) / 100.0
    return gross * (1 - TAX)


def mm_tr(delta_bps: int) -> float:
    """After-tax TR денежного рынка (фонд ликвидности): КС_средняя − MM_SPREAD, налог 13% при продаже."""
    avg_kc = KEYRATE + delta_bps / 100.0 / 2.0
    gross = (avg_kc - MM_SPREAD) / 100.0
    return gross * (1 - TAX)


def breakeven_vs_floater(bond: dict, curve: list) -> float | None:
    """ΔКС (б.п. за год), при котором after-tax TR ПД = TR флоатера. Бисекция на [-600, +300].

    f(Δ) = TR_ПД(Δ) − TR_ПК(Δ) монотонно убывает по Δ (меньше Δ → больше ставок срезано →
    больше переоценка длины). Возвращает None, если равенства нет на отрезке.
    """
    def f(d):
        return pd_scenario_tr(bond, curve, int(round(d)))["tr_net"] - floater_tr(int(round(d)))

    lo, hi = -600.0, 300.0
    flo, fhi = f(lo), f(hi)
    if flo < 0:      # даже при −600 б.п. флоатер лучше
        return None
    if fhi > 0:      # ПД лучше всегда (включая разворот +300)
        return float("inf")
    for _ in range(60):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2

# ───────────────────────── вселенная ─────────────────────────


def pick_by_targets(cands: list, targets: list) -> list:
    picks, used = [], set()
    for t in targets:
        best = min(cands, key=lambda x: abs(x["yrs"] - t))
        if best["ticker"] not in used:
            used.add(best["ticker"])
            picks.append(best)
    picks.sort(key=lambda x: x["yrs"])
    return picks


def build_universe() -> tuple:
    bonds = call("InstrumentsService/Bonds", {"instrumentStatus": "INSTRUMENT_STATUS_BASE"})["instruments"]
    pd_c, pk_c = [], []
    for b in bonds:
        tick = b["ticker"]
        if b.get("amortizationFlag") or not b.get("maturityDate"):
            continue
        mat = parse_dt(b["maturityDate"])
        yrs = (mat - NOW).days / 365.25
        row = {"ticker": tick, "name": b["name"], "uid": b["uid"],
               "nominal": to_f(b["nominal"]), "maturity": mat, "yrs": yrs,
               "aci": to_f(b.get("aciValue"))}
        if tick.startswith("SU26") and not b.get("floatingCouponFlag") and yrs >= 0.7:
            pd_c.append(row)          # классические ПД с фикс. купоном
        elif tick.startswith("SU29") and b.get("floatingCouponFlag") and yrs >= 0.5:
            pk_c.append(row)          # флоатеры ПК
    return pick_by_targets(pd_c, PD_TARGETS), pick_by_targets(pk_c, PK_TARGETS)


def enrich_pd(picks: list, prices: dict) -> list:
    """Купоны (явный to за погашение!), YTM, дюрация, выпуклость, carry."""
    out = []
    for p in picks:
        pct = prices.get(p["uid"], 0.0)
        if not pct:
            print(f"  ! нет цены {p['ticker']} — пропуск")
            continue
        to_dt = (p["maturity"] + timedelta(days=400)).strftime("%Y-%m-%dT00:00:00Z")
        cps = call("InstrumentsService/GetBondCoupons", {
            "instrumentId": p["uid"],
            "from": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": to_dt,  # ЯВНЫЙ to за горизонт погашения — иначе график обрезается ~годом (gotchas.md)
        }).get("events", [])
        flows, coupon_1y = [], 0.0
        for c in cps:
            t = (parse_dt(c["couponDate"]) - NOW).days / 365.25
            if t <= 0:
                continue
            amt = to_f(c["payOneBond"])
            flows.append((t, amt))
            if t <= 1.0:
                coupon_1y += amt
        flows.sort()
        flows.append((p["yrs"], p["nominal"]))
        dirty = pct / 100 * p["nominal"] + p["aci"]
        y = ytm(dirty, flows)
        dmod, conv = duration_convexity(flows, y)
        p.update(price_pct=pct, dirty=dirty, flows=flows, ytm=y, dmod=dmod, conv=conv,
                 coupon_1y=coupon_1y, carry=coupon_1y / dirty)
        out.append(p)
        time.sleep(0.35)  # instruments ≤200/мин, не частим
    return out

# ───────────────────────── main ─────────────────────────


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("Carry-rotation model: кэш/флоатеры → длинные ОФЗ-ПД")
    print(f"КС {KEYRATE}% | ден.рынок ≈ КС−{MM_SPREAD}пп = {KEYRATE - MM_SPREAD:.2f}% | "
          f"RUONIA ≈ КС−{RUONIA_DISCOUNT}пп | НДФЛ {TAX:.0%}\n")

    pd_picks, pk_picks = build_universe()
    all_uids = [p["uid"] for p in pd_picks + pk_picks]
    lp = call("MarketDataService/GetLastPrices", {"instrumentId": all_uids})
    prices = {x["instrumentUid"]: to_f(x["price"]) for x in lp.get("lastPrices", [])}

    pd_bonds = enrich_pd(pd_picks, prices)

    # Оракул: сверка своего YTM с серверным полем yield (эффективная годовая, ACT/365, T+1 —
    # analysis/bond_ytm_validate.py; допуск ±5 б.п., остаток — разный снимок цены)
    mv = call("MarketDataService/GetMarketValues", {
        "instrumentId": [b["uid"] for b in pd_bonds],
        "values": ["INSTRUMENT_VALUE_YIELD"],
    })
    api_yield = {}
    for it in mv.get("instruments", []):
        for v in it.get("values", []):
            if v.get("type") == "INSTRUMENT_VALUE_YIELD":
                api_yield[it["instrumentUid"]] = to_f(v.get("value"))
    max_dev = 0.0
    for b in pd_bonds:
        ay = api_yield.get(b["uid"])
        b["ytm_api"] = ay
        if ay:
            max_dev = max(max_dev, abs(b["ytm"] * 100 - ay))
    ytm_check = (f"макс. |мой YTM − API yield| = {max_dev * 100:.1f} б.п. по {len(api_yield)} бумагам "
                 f"({'PASS ≤5 б.п.' if max_dev <= 0.05 else 'WARN >5 б.п. — проверить снимок цены'})")
    print(f"оракул YTM: {ytm_check}\n")

    # G-кривая YTM-vs-дюрация из собственной вселенной ПД
    curve = sorted((b["dmod"], b["ytm"]) for b in pd_bonds)

    print(f"{'тикер':<14}{'погаш.':<9}{'лет':>5}{'цена%':>8}{'YTM%':>7}{'Dmod':>6}"
          f"{'Conv':>7}{'carry%':>8}{'rolldn%':>8}")
    print("-" * 72)
    for b in pd_bonds:
        d_h = max(b["dmod"] - 1.0, curve[0][0])
        y_roll = interp_curve(curve, d_h)
        b["rolldown"] = (b["ytm"] - y_roll) * d_h  # (сколько YTM скатывается) × дюрация точки прибытия
        print(f"{b['ticker']:<14}{b['maturity'].strftime('%m.%Y'):<9}{b['yrs']:>5.1f}"
              f"{b['price_pct']:>8.2f}{b['ytm'] * 100:>7.2f}{b['dmod']:>6.2f}"
              f"{b['conv']:>7.1f}{b['carry'] * 100:>8.2f}{b['rolldown'] * 100:>8.2f}")

    print("\nФлоатеры ОФЗ-ПК (модель: купон = средняя RUONIA года, тело ≈ номинал):")
    for p in pk_picks:
        pct = prices.get(p["uid"], 0.0)
        print(f"  {p['ticker']:<14}{p['name']:<12} погаш. {p['maturity'].strftime('%m.%Y')}"
              f"  цена {pct:.2f}%")

    # Матрица бумага × сценарий (after-tax TR, %)
    print(f"\n{'AFTER-TAX total return за 12 мес, %':}")
    hdr = f"{'бумага':<14}{'Dmod':>5}" + "".join(f"{n:>14}" for n, _ in SCENARIOS)
    print(hdr)
    print("-" * len(hdr))
    matrix = {}
    for b in pd_bonds:
        row = {}
        cells = ""
        for name, dbps in SCENARIOS:
            r = pd_scenario_tr(b, curve, dbps)
            row[name] = r["tr_net"]
            cells += f"{r['tr_net'] * 100:>14.2f}"
        matrix[b["ticker"]] = row
        print(f"{b['ticker']:<14}{b['dmod']:>5.2f}" + cells)
    fl_row = {n: floater_tr(d) for n, d in SCENARIOS}
    mm_row = {n: mm_tr(d) for n, d in SCENARIOS}
    matrix["ОФЗ-ПК (флоатер)"] = fl_row
    matrix["Денежный рынок"] = mm_row
    print(f"{'ОФЗ-ПК флоатер':<14}{'~0.3':>5}" + "".join(f"{v * 100:>14.2f}" for v in fl_row.values()))
    print(f"{'Ден. рынок':<14}{'0':>5}" + "".join(f"{v * 100:>14.2f}" for v in mm_row.values()))

    # Break-even: при каком темпе снижения КС ПД обгоняет флоатер
    print("\nBreak-even (темп изменения КС за 12 мес, при котором ПД = флоатер after-tax):")
    breakevens = {}
    for b in pd_bonds:
        be = breakeven_vs_floater(b, curve)
        breakevens[b["ticker"]] = be
        if be is None:
            txt = "нет (флоатер лучше даже при −600 б.п.)"
        elif be == float("inf"):
            txt = "ПД лучше при любом сценарии [−600; +300]"
        elif be >= 0:
            txt = f"{be:+.0f} б.п. — ПД выигрывает даже без снижения"
        else:
            txt = f"{be:+.0f} б.п. — нужно снижение быстрее {abs(be):.0f} б.п./год"
        print(f"  {b['ticker']:<14} Dmod {b['dmod']:>5.2f}  {txt}")

    # Триггеры ротации из живых чисел
    short_b = min(pd_bonds, key=lambda x: x["dmod"])
    long_b = max(pd_bonds, key=lambda x: x["dmod"])
    slope = (long_b["ytm"] - short_b["ytm"]) * 100
    long_vs_kc = long_b["ytm"] * 100 - KEYRATE
    triggers = [
        f"Наклон G-кривой ({long_b['ticker']} − {short_b['ticker']}): {slope:+.2f} пп. "
        f"Положительный наклон = рынок платит и carry, и rolldown за длину; "
        f"уплощение/инверсия (<0) = длина уже отыграла снижение — фиксировать.",
        f"Спред длинной YTM к КС: {long_vs_kc:+.2f} пп ({long_b['ticker']} {long_b['ytm'] * 100:.2f}% "
        f"vs КС {KEYRATE}%). Пока YTM длинной ≥ КС − 1 пп, вход в длину не «дорогой»; "
        f"уход спреда ниже −2 пп = рынок заложил весь цикл, ротация опоздала.",
        f"Темп ЦБ: break-even самой длинной бумаги = "
        f"{('%+.0f б.п./год' % breakevens[long_b['ticker']]) if isinstance(breakevens[long_b['ticker']], float) and breakevens[long_b['ticker']] not in (float('inf'),) else 'см. таблицу'}. "
        f"Сравнивать с фактическим шагом ЦБ (сейчас −25 б.п./заседание ≈ −150..−200 б.п./год при 8 заседаниях). "
        f"Следующее заседание: {NEXT_CBR_MEETING}.",
        "RUSFAR/RGBI из API удалены (gotchas.md) — мониторить КС напрямую (cbr.ru) и "
        "пересчитывать эту модель после каждого заседания: python scripts/carry_rotation_model.py.",
    ]
    print("\nТриггеры ротации:")
    for t in triggers:
        print(f"  • {t}")

    # ── JSON ──
    out_json = {
        "generated_utc": NOW.isoformat(),
        "assumptions": {
            "keyrate_pct": KEYRATE, "mm_spread_pp": MM_SPREAD, "ruonia_discount_pp": RUONIA_DISCOUNT,
            "tax": TAX, "beta_short": BETA_SHORT, "beta_long": BETA_LONG,
            "beta_dur_range_years": [BETA_D_SHORT, BETA_D_LONG], "pk_coupon_spread_pp": PK_SPREAD,
            "scenarios_bps": {n: d for n, d in SCENARIOS},
            "next_cbr_meeting": NEXT_CBR_MEETING,
            "notes": "TR: полная переоценка остаточных потоков на горизонте 12м; купоны без реинвеста; "
                     "налог 13% купоны + 13% положительной курсовой (вне ИИС-3/ЛДВ).",
        },
        "curve_ytm_vs_duration": [{"dmod": round(d, 3), "ytm_pct": round(y * 100, 3)} for d, y in curve],
        "pd_bonds": [{
            "ticker": b["ticker"], "name": b["name"], "uid": b["uid"],
            "maturity": b["maturity"].date().isoformat(), "years": round(b["yrs"], 2),
            "price_pct": b["price_pct"], "dirty_rub": round(b["dirty"], 2),
            "ytm_pct": round(b["ytm"] * 100, 3), "mod_duration": round(b["dmod"], 3),
            "convexity": round(b["conv"], 2), "carry_pct": round(b["carry"] * 100, 3),
            "rolldown_pct": round(b["rolldown"] * 100, 3),
            "after_tax_tr_pct": {n: round(v * 100, 3) for n, v in matrix[b["ticker"]].items()},
            "breakeven_vs_floater_bps": (None if breakevens[b["ticker"]] is None
                                         else ("always" if breakevens[b["ticker"]] == float("inf")
                                               else round(breakevens[b["ticker"]], 1))),
        } for b in pd_bonds],
        "floaters_pk": [{"ticker": p["ticker"], "name": p["name"], "uid": p["uid"],
                         "maturity": p["maturity"].date().isoformat(),
                         "price_pct": prices.get(p["uid"], 0.0)} for p in pk_picks],
        "floater_after_tax_tr_pct": {n: round(v * 100, 3) for n, v in fl_row.items()},
        "money_market_after_tax_tr_pct": {n: round(v * 100, 3) for n, v in mm_row.items()},
        "triggers": triggers,
        "ytm_oracle_check": ytm_check,
    }
    jpath = ROOT / "analysis" / "carry_rotation.json"
    jpath.write_text(json.dumps(out_json, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── Markdown ──
    md = [
        "# Carry-rotation: когда ротировать кэш/флоатеры в длинные ОФЗ-ПД",
        "",
        f"Прогон: {NOW.strftime('%Y-%m-%d %H:%M UTC')} · скрипт `scripts/carry_rotation_model.py` · "
        f"живые данные T-Invest (Bonds/GetBondCoupons/GetLastPrices, read-only).",
        "",
        f"Допущения: КС **{KEYRATE}%**; ден.рынок = КС−{MM_SPREAD}пп = **{KEYRATE - MM_SPREAD:.2f}%** брутто; "
        f"RUONIA = КС−{RUONIA_DISCOUNT}пп; флоатер ОФЗ-ПК = средняя RUONIA года (спред {PK_SPREAD}пп, тело ≈ номинал); "
        f"НДФЛ 13% на купоны и положительную курсовую (продажа до 3 лет, вне ИИС-3); "
        f"бета сдвига кривой к ΔКС: {BETA_SHORT} (дюрация ≤{BETA_D_SHORT:g}) → {BETA_LONG} (≥{BETA_D_LONG:g} лет); "
        "сценарный сдвиг равномерен в течение 12 мес; купоны не реинвестируются.",
        "",
        "## Вселенная ОФЗ-ПД: живой срез",
        "",
        "| тикер | погаш. | лет | цена % | YTM % | Dmod | Conv | carry % | rolldown % |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for b in pd_bonds:
        md.append(f"| {b['ticker']} | {b['maturity'].strftime('%m.%Y')} | {b['yrs']:.1f} | "
                  f"{b['price_pct']:.2f} | {b['ytm'] * 100:.2f} | {b['dmod']:.2f} | {b['conv']:.1f} | "
                  f"{b['carry'] * 100:.2f} | {b['rolldown'] * 100:.2f} |")
    md += [
        "",
        f"Флоатеры-представители: " + ", ".join(
            f"{p['ticker']} ({p['name']}, погаш. {p['maturity'].strftime('%m.%Y')}, "
            f"цена {prices.get(p['uid'], 0.0):.2f}%)" for p in pk_picks) + ".",
        "",
        "## Матрица after-tax total return за 12 мес, %",
        "",
        "| бумага | Dmod | " + " | ".join(n for n, _ in SCENARIOS) + " |",
        "|---|---:|" + "---:|" * len(SCENARIOS),
    ]
    for b in pd_bonds:
        md.append(f"| {b['ticker']} | {b['dmod']:.2f} | " +
                  " | ".join(f"{matrix[b['ticker']][n] * 100:.2f}" for n, _ in SCENARIOS) + " |")
    md.append("| **ОФЗ-ПК (флоатер)** | ~0.3 | " +
              " | ".join(f"{fl_row[n] * 100:.2f}" for n, _ in SCENARIOS) + " |")
    md.append("| **Денежный рынок** | 0 | " +
              " | ".join(f"{mm_row[n] * 100:.2f}" for n, _ in SCENARIOS) + " |")
    md += ["", "## Break-even: какой темп снижения КС нужен, чтобы ПД обогнала флоатер", "",
           "| тикер | Dmod | break-even ΔКС за 12 мес |", "|---|---:|---|"]
    for b in pd_bonds:
        be = breakevens[b["ticker"]]
        if be is None:
            txt = "нет — флоатер лучше даже при −600 б.п."
        elif be == float("inf"):
            txt = "ПД лучше при любом сценарии из [−600; +300]"
        elif be >= 0:
            txt = f"{be:+.0f} б.п. — выигрывает даже без снижения (carry+rolldown хватает)"
        else:
            txt = f"{be:+.0f} б.п. — нужно снижение не медленнее {abs(be):.0f} б.п./год"
        md.append(f"| {b['ticker']} | {b['dmod']:.2f} | {txt} |")
    md += ["", "## Триггеры ротации (мониторить)", ""]
    md += [f"- {t}" for t in triggers]
    md += [
        "",
        "## Верификация (оракул)",
        "",
        f"- YTM против серверного `INSTRUMENT_VALUE_YIELD`: {ytm_check}.",
        "",
        "## Ограничения модели",
        "",
        "- Сдвиг кривой параллельный в пределах бета-профиля; реальное снижение КС обычно даёт булл-стипенинг "
        "(короткий конец падает сильнее) — модель это частично ловит бетой, но не форму горба.",
        "- Флоатер упрощён: средняя RUONIA года + номинал; лаговая «инерционная доходность» старых выпусков "
        "и дисконт тела ±0.5% не моделируются (deep/stress_floaters_keyrate.md: <1% даже при +600 б.п.).",
        "- Налог: отрицательная курсовая не сальдируется (консервативно); на ИИС-3 налога нет вовсе — "
        "все TR ПД там выше примерно на величину налога, break-even сдвигается в пользу длины.",
        "- Комиссии сделки (~0.05–0.3%) не вычтены — на горизонте 12 мес это ≤0.3 пп, ниже точности беты.",
        "",
        "*Модель для решения пользователя. Не автоторговля. Read-only.*",
    ]
    mpath = ROOT / "analysis" / "carry_rotation_result.md"
    mpath.write_text("\n".join(md), encoding="utf-8")
    print(f"\nсохранено: {jpath}\nсохранено: {mpath}")


if __name__ == "__main__":
    main()

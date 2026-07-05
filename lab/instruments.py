"""Strategy Lab: реестр инструментов. point_rub — цена пункта в рублях (для фьючей)."""
from datetime import date

# kind: share|futures. step — шаг цены. point_rub = minPriceIncrementAmount / minPriceIncrement.
INSTRUMENTS = {
    "SBER":    {"uid": "e6123145-9665-43e0-8413-cd61b8aa9b13", "kind": "share", "lot": 10,  "step": 0.01, "point_rub": 1.0},
    "GAZP":    {"uid": "962e2a95-02a9-4171-abd7-aa198dbe643a", "kind": "share", "lot": 10,  "step": 0.01, "point_rub": 1.0},
    "LKOH":    {"uid": "02cfdf61-6298-4c0f-a9ca-9cabc82afaf3", "kind": "share", "lot": 1,   "step": 0.5,  "point_rub": 1.0},
    "ROSN":    {"uid": "fd417230-19cf-4e7b-9623-f7c9ca18ec6b", "kind": "share", "lot": 1,   "step": 0.05, "point_rub": 1.0},
    "GMKN":    {"uid": "509edd0c-129c-4ee2-934d-7f6246126da1", "kind": "share", "lot": 10,  "step": 0.02, "point_rub": 1.0},
    "PLZL":    {"uid": "10620843-28ce-44e8-80c2-f26ceb1bd3e1", "kind": "share", "lot": 1,   "step": 0.2,  "point_rub": 1.0},
    "CHMF":    {"uid": "fa6aae10-b8d5-48c8-bbfd-d320d925d096", "kind": "share", "lot": 1,   "step": 0.2,  "point_rub": 1.0},
    # вечный фьючерс на золото в рублях. ВНИМАНИЕ: цена в ПУНКТАХ; в песочнице кэш
    # при покупке НЕ списывается (только комиссия) — equity корректируем на нотионал.
    "GLDRUBF": {"uid": "b347fe28-0d2a-45bf-b3bd-cda8a6ac64e6", "kind": "futures", "lot": 1, "step": 0.1, "point_rub": 1.0},
    # срочные контракты для daybot (intraday, лонг и шорт). После экспирации заменить uid
    # на следующий контракт (InstrumentsService/Futures, basicAsset Brent / "Газ (США)").
    "BMQ6":    {"uid": "d46436d0-f6c4-43b3-90fc-f93e6330ff1f", "kind": "futures", "lot": 1, "step": 0.01,  "point_rub": 71.908, "exp": "2026-08-04", "last_trade": "2026-08-03", "roll_to": "BMU6"},   # BRM-8.26 Brent мини
    "NGN6":    {"uid": "ddd7405e-f3df-4c29-a876-e865013c4e54", "kind": "futures", "lot": 1, "step": 0.001, "point_rub": 7190.77, "exp": "2026-07-30", "last_trade": "2026-07-29", "roll_to": "NGQ6"},  # NG-7.26 природный газ
    # Преемники (сверены с API 2026-06-15, point_rub та же серия). При экспирации NGN6/BMQ6
    # просто заменить uid у строк выше на эти. Заменять ПОСЛЕ lastTradeDate текущего:
    #   NGN6 → NGQ6 "79d16f7f-3ca4-4ae2-aa5b-183ed22618e9"  # NG-8.26, exp 2026-08-28 (lastTrade 08-27)
    #   BMQ6 → BMU6 "83e82b82-3330-4a47-b5cd-e615327d25ed"  # BRM-9.26 Brent мини, exp 2026-09-01 (lastTrade 08-31)
}

# Карта роллов: данные преемников как данные, не хардкод в логике.
# uid/exp/last_trade сверены с InstrumentsService/Futures 2026-06-15.
# point_rub берём от текущего контракта (та же ценовая серия).
ROLLOVERS = {
    "NGN6": {"ticker": "NGQ6", "uid": "79d16f7f-3ca4-4ae2-aa5b-183ed22618e9",
             "exp": "2026-08-28", "last_trade": "2026-08-27"},   # NG-8.26
    "BMQ6": {"ticker": "BMU6", "uid": "83e82b82-3330-4a47-b5cd-e615327d25ed",
             "exp": "2026-09-01", "last_trade": "2026-08-31"},   # BRM-9.26
}

BASKET = ["GAZP", "LKOH", "ROSN", "GMKN", "PLZL", "CHMF"]  # нефть/газ + металлы


def rub_value(ticker: str, price: float, lots: int) -> float:
    m = INSTRUMENTS[ticker]
    return price * m["point_rub"] * m["lot"] * lots


def futures_roll_warnings(today: date | None = None, warn_days: int = 5) -> list[dict]:
    """Вернуть предупреждения по срочным фьючерсам, близким к last_trade/exp.

    Чистая офлайн-проверка реестра: uid не меняет, заявок не делает. Возвращает
    список dict с ticker/status/days_to_last_trade/roll_to.
    """
    today = today or date.today()
    out = []
    for ticker, meta in sorted(INSTRUMENTS.items()):
        if meta.get("kind") != "futures" or "last_trade" not in meta:
            continue
        last_trade = date.fromisoformat(meta["last_trade"])
        exp = date.fromisoformat(meta["exp"])
        days = (last_trade - today).days
        if today > exp:
            status = "EXPIRED"
        elif days < 0:
            status = "PAST_LAST_TRADE"
        elif days <= warn_days:
            status = "ROLL_SOON"
        else:
            continue
        out.append({"ticker": ticker, "status": status, "days_to_last_trade": days,
                    "last_trade": meta["last_trade"], "exp": meta["exp"],
                    "roll_to": meta.get("roll_to", "")})
    return out


def apply_roll(ticker: str) -> dict:
    """Применить офлайн-ролл фьючерса к преемнику из ROLLOVERS.

    Обновляет INSTRUMENTS[ticker] in-place: подставляет uid/exp/last_trade
    преемника и обнуляет roll_to (у свежекатанного контракта преемника пока нет).
    Возвращает новую запись реестра. Заявок не делает, песочницу не трогает.

    Применять ТОЛЬКО после last_trade текущего контракта (см. futures_roll_warnings).
    Повторный ролл невозможен — ValueError, чтобы второй раз не переключить uid вслепую:
    для следующего ролла нужно явно дополнить ROLLOVERS и вернуть roll_to в реестре
    (преемник преемника описывается отдельно, когда биржа опубликует расписание).
    """
    if ticker not in INSTRUMENTS:
        raise KeyError(ticker)
    meta = INSTRUMENTS[ticker]
    if meta.get("kind") != "futures":
        raise ValueError(f"apply_roll: только фьючерсы, {ticker} — {meta.get('kind')}")
    roll_to = meta.get("roll_to", "")
    if not roll_to:
        raise ValueError(f"apply_roll: у {ticker} нет roll_to (уже катан или преемника нет)")
    successor = ROLLOVERS.get(ticker)
    if successor is None:
        raise ValueError(f"apply_roll: преемник для {ticker} не описан в ROLLOVERS")
    meta["uid"] = successor["uid"]
    meta["exp"] = successor["exp"]
    meta["last_trade"] = successor["last_trade"]
    meta["roll_to"] = ""
    return meta

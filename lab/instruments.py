"""Strategy Lab: реестр инструментов. point_rub — цена пункта в рублях (для фьючей)."""

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
    "BMQ6":    {"uid": "d46436d0-f6c4-43b3-90fc-f93e6330ff1f", "kind": "futures", "lot": 1, "step": 0.01,  "point_rub": 71.908},   # BRM-8.26 Brent мини, exp 2026-08-04
    "NGN6":    {"uid": "ddd7405e-f3df-4c29-a876-e865013c4e54", "kind": "futures", "lot": 1, "step": 0.001, "point_rub": 7190.77},  # NG-7.26 природный газ, exp 2026-07-30
    # Преемники (сверены с API 2026-06-15, point_rub та же серия). При экспирации NGN6/BMQ6
    # просто заменить uid у строк выше на эти. Заменять ПОСЛЕ lastTradeDate текущего:
    #   NGN6 → NGQ6 "79d16f7f-3ca4-4ae2-aa5b-183ed22618e9"  # NG-8.26, exp 2026-08-28 (lastTrade 08-27)
    #   BMQ6 → BMU6 "83e82b82-3330-4a47-b5cd-e615327d25ed"  # BRM-9.26 Brent мини, exp 2026-09-01 (lastTrade 08-31)
}

BASKET = ["GAZP", "LKOH", "ROSN", "GMKN", "PLZL", "CHMF"]  # нефть/газ + металлы


def rub_value(ticker: str, price: float, lots: int) -> float:
    m = INSTRUMENTS[ticker]
    return price * m["point_rub"] * m["lot"] * lots

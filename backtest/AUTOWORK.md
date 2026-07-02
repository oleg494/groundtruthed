# AUTOWORK — автономная работа сессии №1 (лейн: backtest/ + tests/)

> Этот файл — координация между параллельными чатами и журнал автономного прогресса.
> **Сессия №1 владеет каталогами `backtest/` и `tests/`.** Другим сессиям сюда не писать,
> чтобы не словить конфликты. Коммиты — только вручную пользователем.

Начато: 2026-06-15 (ночь). Режим: автономный, само-стоп по исчерпании чеклиста.

## Уже готово (база, до автономного режима)
- [x] core / indicators / engine / metrics / strategies / candles
- [x] optimize (grid + walk-forward) / montecarlo / report (text + HTML) / CLI
- [x] 34 теста зелёные, README

## Роадмап автономной фазы (всё ADDITIVE, без правок чужого кода)
- [x] R1. Расширенные индикаторы: MACD, ADX/DMI, Keltner, Supertrend, OBV, ROC, Stoch
- [x] R2. Новые стратегии: MACD-cross, Turtle (Дончиан с ATR-стопом), VolTarget, DualMomentum
- [x] R3. Модуль сайзинга: fixed-fractional, ATR-risk-based, volatility-target, capped-Kelly (+ATRBreakout)
- [x] R4. Портфельный движок: ребаланс корзины (equal/inverse-vol/фикс), drift-band, кэш-буфер
- [x] R5. Робастность: PSR/Deflated Sharpe, param-sensitivity, OOS/IS degradation
- [x] R6. Бенчмарк-сравнение: alpha/beta, корреляция, tracking error, IR, up/down capture
- [x] R7. Стоп-логика (opt-in в Context): стоп/тейк/трейлинг + update_stops
- [x] R8. Тесты R1–R7: 65 тестов, всё зелёное
- [x] R9. README + CLI (robust/bench/rebalance/basket) + финальная проверка

ВОЛНА 1 ЗАВЕРШЕНА: 2514 строк, 8 модулей, 8 CLI-команд, 65 тестов. Чужой код не тронут.

## Волна 2 (придумано автономно, всё ADDITIVE)
- [x] W1. Мультитаймфрейм: ресэмплинг баров (день→неделя) в candles + helper
- [x] W2. Парный трейдинг (стат-арбитраж): z-score спреда двух тикеров, лонг/шорт спреда
- [x] W3. Экспорт: equity/trades в CSV+JSON; статистика серий (стрики, holding period)
- [x] W4. Heatmap оптимизации: 2D-сетка метрики как inline-SVG в HTML
- [x] W5. Режимный фильтр: regime_donchian (ADX>порога + DI)
- [x] W6. Sweep по издержкам: cost_sensitivity → таблица деградации
- [x] W7. Tearsheet: HTML-оверлей нескольких стратегий
- [x] W8. CLI: tearsheet + export + heatmap + costs
- [x] W9. Тесты волны 2 (74 всего) + README + финальная проверка

ВОЛНА 2 ЗАВЕРШЕНА: 13 модулей, 12 CLI-команд, ~16 стратегий, 74 теста, ~3780 строк.

## Волна 3 (капстоун)
- [x] C1. study.py: end-to-end пайплайн (optimize→walk-forward→robust→MC→bench) + вердикт
- [x] C2. report.study_html: единый HTML-отчёт исследования (equity, overlay, heatmap, секции)
- [x] C3. CLI `study` + тесты (78 всего) + README + финальная проверка

ВОЛНА 3 ЗАВЕРШЕНА. ИТОГ: 14 модулей, 13 CLI-команд, ~17 стратегий, 78 тестов.

## Волна 4 (риск-аналитика и валидация — по запросу «продолжай»)
- [x] V1. risk.py: VaR/CVaR, Ulcer, tail ratio, Omega, gain-to-pain, топ-N просадок с восстановлением
- [x] V2. Календарь доходностей: помесячная/погодовая таблица (текст)
- [x] V3. validate.py: детектор lookahead (factory(data), ловит читера) + детектор дыр/дублей
- [x] V4. optimize: random_search + robust_select по окрестности
- [x] V5. Тесты волны 4 (89 всего) + CLI risk + README + проверка

ВОЛНА 4 ЗАВЕРШЕНА. ИТОГ: 16 модулей, 14 CLI-команд, ~17 стратегий, 89 тестов.

## Волна 5 (ансамбли и сценарии — по запросу «продолжай»)
- [x] E1. ensemble.py: combine_equity (равная/risk-parity аллокация), результат как Result
- [x] E2. scenarios.py: across_seeds + across_regimes (4 режима) → распределение метрик
- [x] E3. CLI ensemble/scenarios + тесты (96 всего) + README + проверка

ВОЛНА 5 ЗАВЕРШЕНА. ИТОГ: 18 модулей, 16 CLI-команд, ~17 стратегий, 96 тестов.

## Волна 6 (полировка движка оптимизации — по запросу «continue»)
- [x] F1. optimize: кастомная цель objective(Metrics)->float в grid/random/walk_forward
- [x] F2. report.walkforward_html: сшитая OOS-кривая + таблица окон
- [x] F3. ensemble: correlation_matrix + correlation_text (средняя попарная)
- [x] F4. CLI: --html у walkforward, корреляции в выводе ensemble
- [x] F5. тесты волны 6 (103 всего) + README + проверка

ВОЛНА 6 ЗАВЕРШЕНА. ИТОГ: 18 модулей, 16 CLI-команд, ~17 стратегий, 103 теста.

## Волна 7 (маржинальная модель фьючерса — по запросу «continue»)
- [x] M1. core.Instrument.kind ("cash"|"futures") + is_futures; денежный поток в _apply_fill
- [x] M2. equity для futures = кэш + нереализованный P&L; _update_position возвращает realized→кэш
- [x] M3. CLI флаги --futures/--multiplier (_instruments) на команде run
- [x] M4. тесты (108 всего, вкл. инвариант cash≡futures equity) + README + core docstring

ВОЛНА 7 ЗАВЕРШЕНА. ИТОГ: 18 модулей, 16 CLI-команд, ~17 стратегий, 108 тестов. Старое поведение (cash) не задето.

## Волна 8 (сводный дашборд прогонов)
- [x] D1. dashboard.py: load_results (рекурсивный скан JSON), build_dashboard (HTML-таблица, тепловая карта, спарклайны)
- [x] D2. CLI команда `dashboard --dir <папка> --html` (17-я команда)
- [x] D3. тесты (119 всего) + README + финальная проверка

ВОЛНА 8 ЗАВЕРШЕНА. ИТОГ: 19 модулей, 17 CLI-команд, ~17 стратегий, 119 тестов.

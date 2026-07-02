# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
Актуализирован 2026-07-02 (предыдущая версия — 2026-06-19).

## Контекст
Пользователь: клиент Т-Инвестиций (T-Bank), **read-only токен**.
Цель: анализ портфеля, рынка, инвестиционных идей. Это не приложение, а рабочее
пространство для аналитики — код обслуживает анализ, результаты идут в `analysis/`.
Платформа: Windows 11, оболочка bash (Unix-синтаксис путей и команд). Язык работы — русский.

## Правила (КРИТИЧНО)
- **READ-ONLY**: не размещать заявки, не менять позиции, не совершать сделки. У MCP-сервера
  есть инструменты записи (`invest_create_order`, `invest_cancel_order`, `invest_deposit_*`,
  `invest_transfer_*` и т.п.) — **не вызывать их**. Использовать только `get_*`/`list_*`/`find_*`.
- Эксперименты с заявками — только через sandbox API (`sandbox-invest-public-api.tinkoff.ru:443`), никогда на боевом счёте.
- Показывать, что делаешь, перед выполнением.
- При анализе учитывать комиссии, налоги и инфляцию.
- Лимиты API — **по сервисам, не общий счётчик** (подробно в `docs/limits.md`): котировки/marketdata 600/мин,
  инструменты 200/мин, операции 200/мин, счета (users) 100/мин, getHistory 30/мин, архивы history-data 30 файлов/мин.
  Рекомендация T-Invest — суммарно ≤50 запросов/сек. Группировать запросы, избегать тугих циклов.
- Оракул-дисциплина: зелёный тест ≠ корректность. Любой расчёт/гипотезу сверять числом против
  объективного эталона (серверное значение, безарбитражное тождество, кросс-эндпоинт) — паттерн `analysis/*_validate.py`.
- Из копии-песочницы локальных экспериментов (~333 скрипта от агентов) переносить только то, что
  прошло верификацию числом; слоп (выдуманный Sharpe, фантомные RPC) не тащить.

## Конвейер стратегий (главный процесс)

```
идея → deep/ (ресёрч) → backtest/ (история, walk-forward, DSR) → lab//daybot/ (форвард в песочнице) → деньги
```

Правила конвейера (закреплены в `.agents/AGENTS.md`): любые оптимизированные параметры — только
через walk-forward; при провале OOS — режимные фильтры (Hurst/ADX из `deep/market_regime_moex.md`);
стратегия обязана бить бенчмарки buyhold и random; налоги — российские (ст. 214.1 НК), не US-правила.
Дисциплина работает на практике: ORB-daybot убит по бэктесту, 4 стратегии фермы архивированы по Deflated Sharpe = 0%.

## Доступ к данным: два пути

1. **MCP-сервер `t-invest`** (основной путь для интерактивного анализа) — инструменты
   `mcp__t-invest__invest_*`. Готовые вызовы без кода: портфель, свечи, стаканы, фундаментал, прогнозы.
2. **Python-скрипты** (`scripts/`) — для воспроизводимых прогонов. Большинство ходит в REST напрямую
   через `urllib.request` (свой `call()` в каждом файле — намеренная автономность, «ponytail»:
   НЕ выносить в общий модуль). SDK использует только `explore.py` — мигрирован на `t_tech.invest`
   (пакет `t-tech-investments` — НЕ с pypi, приватный индекс opensource.tbank.ru, см. requirements.txt;
   домен `*.tbank.ru`, серт МинЦифры через `SSL_TBANK_VERIFY=True`; dual-import с откатом на `tinkoff.invest`).

Домены (перепроверено 2026-07-01, `docs/migration-to-t-tech.md`): `*.tinkoff.ru` жив и уже на
публичном CA HARICA (до 2026-11-11) — миграция REST-слоя на tbank.ru НЕ срочная, рабочий код не ломать.

### Два токена, два домена (не путать)
В `.env` лежат **два** ключа, код жёстко разведён по доменам:
- `TINVEST_API_KEY` — **боевой read-only**. Используют MCP `t-invest` и скрипты в `scripts/`
  (домен `invest-public-api.tinkoff.ru`). Только чтение.
- `TINVEST_SANDBOX_KEY` — **песочница**. Используют боты `lab/`+`daybot/`, `scripts/sandbox_grid.py`
  и фетч свечей `backtest/candles.py` (домен `sandbox-invest-public-api.tinkoff.ru`; sandbox-marketdata
  отдаёт реальные котировки MOEX). Здесь разрешены заявки — деньги виртуальные.

`lab/api.py`, `sandbox_grid.py`, `backtest/candles.py` читают ТОЛЬКО `TINVEST_SANDBOX_KEY`;
sandbox-токен на боевых методах не работает и наоборот. Боевой ключ в песочный код не подставлять.

ВАЖНО: цена фьючерсов и облигаций в **MarketData** приходит в **пунктах**, не в валюте
(в Instruments/Operations — уже в валюте; таблица в `docs/points.md`). Пересчёт: облигация —
`price/100*nominal`; фьючерс — `price/min_price_increment*min_price_increment_amount`.
Деньги приходят как `{units, nano}` (nano = 1e-9); **сырой REST НЕ кладёт строку `value`** —
парсить units/nano (поле `value` добавляют только MCP/SDK-обёртки).

## Команды

```bash
pip install -r requirements.txt          # зависимости (t-tech-investments — из приватного индекса, URL в комментарии)
python scripts/explore.py                # проверка подключения + обзор портфеля (SDK-путь)
python analysis/run_all_validations.py   # все проверки-оракулы (PASS/WARN/FAIL); --fast — без 5 медленных
python -m pytest tests/ -q               # 155 pytest (бэктестер + tests/test_audit.py; 3 xfail = известные баги движка)
python -m unittest test_trading -v       # 8 регресс-тестов торгового слоя lab/+daybot (офлайн, всё замокано)
python -m unittest test_forward -v       # 15 тестов forward-слоя lab/forward.py (офлайн, моки)
python -m lab.forward report|reconcile   # дневной отчёт фермы / сверка журнала с фактом счёта
python -m backtest <cmd>                 # бэктестер: run/optimize/walkforward/montecarlo/study/bench/... (17 команд)
```

## Проверки-оракулы (`analysis/*_validate.py`)

Слой верификации: **32 скрипта** `*_validate.py`, каждый сверяет данные/расчёты T-Invest против
**объективного оракула**, где возможно — бит-в-бит. У каждого свой `*_result.md`. Карта —
`analysis/VALIDATION_INDEX.md`; раннер — `analysis/run_all_validations.py` (PASS по маркеру-подстроке
в stdout — при изменении текста успеха обновить маркер в SCRIPTS). Последний прогон 2026-07-01:
**24 PASS / 3 WARN / 0 FAIL** (--fast; WARN — честная деградация: эталон удалён upstream'ом или вне сессии).

Вскрытые недокументированные конвенции (можно считать офлайн, не гадая):
- тех-анализ (`GetTechAnalysis`) воспроизводим бит-в-бит: RSI=сглаживание Уайлдера, BB=популяционная
  дисперсия (÷N), EMA seed-инвариантна при прогреве, AVG=(o+h+l+c)/4 (нужен прогрев историей);
- YTM облигаций = эффективная годовая, ACT/365, T+1 (сходится с полем `yield` ±1.3 б.п.);
- опционы FORTS — premium-style паритет С ДИСКОНТОМ (наклон F_impl по страйку = дисконт-фактор);
- свечи: граница дня = MSK; старший ТФ = бит-в-бит свёртка младшего; цены на сетке шага;
- `direction` обезличенных сделок = сторона агрессора (order-flow);
- архивы History MD (`scripts/history_archive.py`): OHLC бит-в-бит с GetCandles, но **volume
  масштабирован ненадёжно** (SBER в лотах до 2025-07-31, после — в штуках) — объёму не верить;
- `api_method_names_validate.py` — офлайн-оракул: имена RPC в коде vs proto-контракты
  (см. `TINVEST_PROTO_DIR` в докстринге скрипта — контракты не вендорятся, лицензия апстрима
  не подтверждена), ловит фантомные методы (404 → try/except → молча пусто).

Грабли — в `docs/gotchas.md` (обязателен перед работой с API). Свежее и важное:
- **IMOEX/RUSFAR/RGBI удалены из API** (между 2026-06-19 и 07-01, code 5): прокси индекса — вечный
  фьючерс `IMOEXF` (цена в пунктах), у RUSFAR/RGBI прокси нет; оракулы beta/index_reconstruct/money_market → WARN;
- `GetBondEvents` без явного `to` обрезает график до ~года — ломает амортизации/дюрацию;
- НКД не линеен (заморозка на выходных, T+1); ГО фьючерса ≠ нотионал×dlongClient (брать GetFuturesMargin);
- Инвесткопилка — внебиржевой `TMON OTC` без свечей; `volumeBuy+volumeSell≠volume` в аукционные дни.

Ключевая ставка ЦБ — **14.25%** (снижена 2026-06-19). Константа `KEYRATE` живёт в ДВУХ местах:
`scripts/market_context.py` и `scripts/dashboard.py` — менять синхронно.

## Бэктестер (`backtest/`)

Событийный бэктест-движок на чистой stdlib (без numpy/pandas): ~23 модуля, 17 CLI-команд,
**27 стратегий** (реестр в `backtest/strategies.py`: базовые + 12 кандидатов двух botdev-волн
2026-07-02 — ВСЕ убиты конвейером, но код сохранён; сводки `analysis/BOT_DEV*_REPORT.md`),
полный research-пайплайн: grid/random search, walk-forward IS/OOS, Monte-Carlo, PSR/Deflated
Sharpe (Bailey & López de Prado), бенчмарк-сравнение, `study` — end-to-end с автоматическим
вердиктом. Документация — `backtest/README.md`.

**Известные баги движка** (вскрыты CI-аудитом 2026-07-02, `tests/test_audit.py` — 3 честных
xfail, `analysis/engine_audit_result.md`): (1) Broker не проверяет достаточность средств —
кэш может уйти в минус; (2) нет maintenance margin/margin call фьючерсов — просадки занижены;
(3) equity теряет стоимость позиции при дыре в мульти-тикерной ленте. Не чинить молча —
снять xfail после фикса и перепроверить вердикты мульти-тикерных кандидатов.

- Данные: детерминированная синтетика (GBM/trend/OU/sine) + реальные свечи через sandbox-домен
  с диск-кэшем `backtest/.cache/` (ключуется датой, автоочистки нет).
- Исполнение без lookahead: вход по open следующего бара; комиссия и слиппедж моделируются.
- Фьючерсы: `kind='futures'` — маржинальная модель согласована с «честным equity» песочницы,
  результаты бэктеста и форвард-теста сопоставимы напрямую. Не забывать `--futures --multiplier <point_rub>`.
- Движок сверен независимой реализацией до 1e-6 (`analysis/backtest_validate.py`), статслой
  откалиброван на синтетике с известной истиной (`analysis/inference_validate.py`; там же пойман
  и исправлен баг DSR: sr_std=1.0 вместо ~1/√n хоронил даже настоящий скилл — при 100% зелёных юнит-тестах).
- `backtest/AUTOWORK.md` — протокол параллельных Claude-сессий (кто владеет каталогом); коммиты — только пользователь.
- `hurst()` в `indicators.py`: R/S-метод, нужно ≥16 баров окна; пороги из ресёрча — H>0.55 тренд, H<0.45 возврат.

## Deep-research (`deep/`)

Конвейер: сырой txt-дамп веб-ресёрча → разрезка на тематические `deep/*.md` → верификация ключевых
утверждений оракулами в `analysis/` → портирование проверенных моделей в `scripts/`. Кода в deep/ нет.
Работает: 12.txt разрезан 2026-07-02 (12 ресёрчей: микроструктура MOEX, расхождения песочницы,
фреймворк отбора стратегий, модель издержек, аудит движка, forward-слой, 10 идей оракулов...);
портировано в код: `tax_model_iis3.py`, `stress_test_floaters.py`, `tax_alpha_scan.py`,
`carry_rotation_model.py`, `lab/forward.py`, `tests/test_audit.py`.

**Собственные измерения рынка** (2026-07-02, все с оракул-верификацией):
- `deep/REGIME_MAP_2026-07.md` — режимные метки Hurst(100)/ADX(14) на дневках ЗАПАЗДЫВАЮТ и
  работают КОНТРАРНО (buyhold в метке TREND_DOWN +24%, в TREND_UP −12%); тренд-фоллоу прибылен
  в RANGE/NEUTRAL, убыточен в TREND-метках; реверсия убыточна везде кроме HIGH_VOL. Гейты
  Hurst/ADX как включатели тренда — контрпродуктивны (объясняет часть kill-вердиктов botdev);
- overnight-премия на MOEX реальна: +5.74 б.п./ночь брутто, но неторгуема при рознич. комиссиях
  (нужно ≤1.6 б.п./сторона) — `analysis/botdev2_overnight_premium.md`;
- вола-паттерн заседаний ЦБ: сжатие RV до, расширение ×1.9 после — монетизируем опционами,
  не дельта-один; направленного эджа нет (8 событий/год);
- шорт акций MOEX мёртв структурно: убыточен даже при бесплатном шорте + ставка КС+2%;
- итог 2 botdev-волн: 12 классов дельта-один стратегий на дневках/интрадее — 12 kill.

Ключевые отчёты: `orb_moex_analysis.md` (классический ORB на MOEX структурно убыточен — обоснование
контртренд-режима daybot), `market_regime_moex.md` (пороги Hurst/ADX для фильтров), `walk_forward_moex.md`
(февраль 2022 — жёсткая граница, anchored WFA, ≥30 сделок/шаг), налоговые (`tax_ldv_vs_iis3_model.md`,
`tax_optimization_iis3.md`), денежный рынок (`money_market_and_floaters.md`, `stress_floaters_keyrate.md`),
`rusfar_implied_keyrate.md` (закрыт отрицательным результатом).

## Песочные боты (`lab/`, `daybot/`)

Форвард-тест на ПЕСОЧНИЦЕ (виртуальные деньги, реальные котировки). Боевого токена здесь нет.

**Статус (2026-07-02):**
- **Ферма `lab/runner.py`** — на VPS под systemd (`tinvest-lab.service`), с 2026-06-17 в «режиме
  бенчмарков»: `ACTIVE=('buyhold','random')` в `lab/strategies.py`; 4 кандидата (grid/momentum/
  meanrev/gold_trend) архивированы после провала study (Deflated Sharpe = 0%) — код сохранён,
  возврат = дописать имя в ACTIVE (только после прохождения walk-forward!).
- **`daybot/`** — ОСТАНОВЛЕН 2026-06-16 (`daybot.stop` с вердиктом «killed: ORB без эджа на бэктесте»).
  2026-06-23 код переписан под контртренд (REVERSAL=True — вход ПРОТИВ пробоя) + ATR-фильтры ширины
  диапазона, но НЕ перезапускался; ATR-порог (0.6–2.0×ATR(100) дневного) не калиброван на истории.
  Задача Task Scheduler `TinvestDaybot` существует, но Disabled. Перед реанимацией: калибровка ATR,
  свежий бэктест reversal-режима, ролл NGN6.
- **Фьючерсы в `lab/instruments.py`**: ключи `BMQ6`/`NGN6` — стабильные ярлыки реестра, НЕ реальные
  тикеры; uid уже указывают на BRM-8.26 (exp 2026-08-04) и NG-7.26 (exp 2026-07-30). **Ролл NGN6→NGQ6
  нужен к 2026-07-29** — uid преемников готовы в комментариях instruments.py.

Слой `lab/` (стоит под обоими ботами): `api.py` — REST-обёртка песочницы (retry/бэкофф, `to_f`, `quot`;
`ProxyHandler({})` — НАМЕРЕННЫЙ обход системного SOCKS-прокси после 2-дневного ослепления фермы на VPS,
не «чинить»); `strategy.py` — `Ctx` (позиции/equity/ордера) и базовый `Strategy`. **Честный equity**:
песочница кладёт в `totalAmountPortfolio` полный нотионал фьючерса — `Ctx.equity()` вычитает
`totalAmountFutures` и добавляет P&L от средней (цены позиции в ПУНКТАХ × `point_rub`);
`instruments.py` — реестр (uid/lot/step/kind/point_rub); `journal.py` — SQLite (`trades`/`equity`/`events`).

Грабли песочницы, зашитые в код (не «чинить» заново): отмена заявки ≠ исполнение (`Ctx.order_filled`
проверяет `lotsExecuted`); фьючерсный кэш при сделке не списывается (коррекция в `equity`); закрытие
шорта падает с `30034 Not enough balance` при достатке средств — `daybot.market_exit` доливает кэш
и повторяет, доливка вычитается из equity (`extra_cash`/`honest_equity`); PID-лок на Windows через
`OpenProcess` (в `runner.py` — `os.kill(pid,0)`, он для Linux-VPS); вне сессии 10–24 МСК будней
песочница отбивает заявки HTTP 400 — runner не тикает; sandbox-счета живут 3 месяца, лимит ~10 на
токен — переиспользовать из `lab_state.json`. Стратегии инстанцируются заново каждый тик — состояние
только в `ctx.state` (персистится атомарно). Регресс-тесты этих граблей — `test_trading.py`.

```bash
python -m lab.runner          # ферма (Ctrl+C — graceful stop)
python -m lab.report          # отчёт: P&L, просадки, сделки
python -m daybot.run          # daybot (остановлен; стоп-флаг daybot.stop — читать перед запуском)
python -m daybot.report       # отчёт daybot
python scripts/sandbox_grid.py # отдельный грид-бот SBER (не на слое lab/)
```

## Правила агентов

`.agents/AGENTS.md` — поведенческие правила воркспейса (walk-forward обязателен, режимные фильтры,
налоги РФ по умолчанию) — читать при работе со стратегиями.

## Структура проекта
```
docs/          — свои гайды по T-Invest API (README.md — индекс со ссылками на официальные
               источники; quickref.md — шпаргалка; gotchas.md — НЕофициальные грабли, community-чат
               + собственные оракулы, ОБЯЗАТЕЛЕН перед работой с API; api-services.md; mcp-vs-api-
               coverage.md; errors-cheatsheet.md; migration-to-t-tech.md — статус доменов/SDK).
               Proto-контракты не вендорятся (лицензия апстрима не подтверждена) — см.
               TINVEST_PROTO_DIR в analysis/api_method_names_validate.py.
               docs/superpowers/specs/ — дизайн-спеки ботов.
scripts/       — автономные CLI-скрипты к боевому API read-only (свой call() в каждом — не выносить
               в common). explore.py — точка входа; market_context.py + dashboard.py — срез рынка
               (IMOEX через IMOEXF, KEYRATE в обоих); скринеры ofz_screen/stock_screen (фундаментал
               по asset_uid!)/mm_funds_compare; account_full.py — экономика счёта; news_pull.py — RSS
               без API; history_archive.py — bulk-архивы минуток. Офлайн-модели: tax_model_iis3.py,
               stress_test_floaters.py (порты deep/-отчётов). Верифицированные модели 2026-07-02:
               tax_alpha_scan.py (FIFO==yield брокера Δ=0.00; вывод: главная альфа — открыть ИИС-3,
               вычет до 52к/год; перезапустить в декабре) и carry_rotation_model.py (G-кривая live,
               YTM ±1.7 б.п. к API; вывод: дюрация 1.5+ бьёт флоатер уже при стопе цикла; перепрогонять
               после каждого заседания ЦБ). Исключение: sandbox_grid.py — грид-бот в ПЕСОЧНИЦЕ.
analysis/      — слой оракулов (32 *_validate.py + *_result.md, run_all_validations.py,
               VALIDATION_INDEX.md, SESSION_2026-06-19_REPORT.md) + выходные артефакты анализа
               + прикладные прогоны бэктестера (orb_backtest.py, farm_vs_backtest.py) +
               исследовательские скрипты (crosssec_momentum.py, return_predictability.py).
               GOAL_lab.md — целеполагание учебной лаборатории. Сырые выгрузки *.json — в .gitignore.
backtest/      — событийный бэктест-движок (stdlib): core/engine/candles/strategies/indicators/
               optimize/robust/study + README.md. Кэш свечей backtest/.cache/ — в .gitignore.
deep/          — deep-research отчёты (.md); конвейер: txt-дамп → разрезка → оракул → scripts/.
lab/           — торговый слой песочницы (api/strategy/instruments/journal) + ферма runner.py +
               forward.py — forward-test слой (reconcile журнал-vs-счёт, метрики, дневной отчёт,
               алерты; слиппедж/tracking — заглушки, нужные поля журнала в analysis/forward_layer_notes.md).
               Рабочие файлы (lab.db, lab_state.json, lab.pid) — в .gitignore.
daybot/        — интрадей-бот на слое lab/ (ОСТАНОВЛЕН, см. статус). Рабочие файлы — в .gitignore.
tests/         — pytest-тесты бэктестера (~123); запуск из корня (conftest делает sys.path-хак).
test_trading.py — 8 unittest-регрессов торгового слоя lab/+daybot (моки, офлайн); каждый тест
               привязан к реальному датированному багу песочницы.
.agents/       — AGENTS.md: поведенческие правила для ИИ-агентов (walk-forward, налоги РФ).
.env           — TINVEST_API_KEY (боевой read-only) + TINVEST_SANDBOX_KEY (песочница) +
               TINVEST_ACCOUNTS (id1,id2 — свои номера счетов); не коммитить, см. .env.example.
```

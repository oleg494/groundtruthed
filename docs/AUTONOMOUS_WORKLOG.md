# Autonomous Worklog

Дата старта: 2026-07-05
Режим: автономная работа в копии проекта, без вопросов пользователю.
Ограничение: боевой T-Invest счёт только read-only; заявки допустимы только в sandbox-коде и только если задача прямо про песочницу.

## Стратегия итераций

1. Брать сначала инфраструктурные задачи, которые улучшают весь research/forward-конвейер.
2. Для изменений поведения писать failing test до production-кода.
3. После каждой группы изменений запускать узкие тесты, затем широкий офлайн-регресс.
4. Не перетирать пользовательские/untracked файлы. На момент старта `AGENTS.md` уже был untracked.
5. Если очередь кончилась, создавать новую задачу из обнаруженного долга: stale validations, forward gaps, backtest ergonomics, documentation drift.

## Сделано в этом прогоне

### Итерация 1: Walk-forward warm-up

- Добавлен `warmup_bars` в `backtest.optimize.walk_forward`.
- OOS-прогон получает предысторию для индикаторов, но торговые сигналы до OOS блокируются wrapper-стратегией.
- OOS `Result` обрезается до фактического OOS-диапазона, чтобы метрики не считали warm-up.
- CLI `python -m backtest walkforward` получил флаг `--warmup-bars`.
- Тест: `tests/test_robust.py::test_walk_forward_warmup_feeds_history_without_extending_oos_metrics`.

### Итерация 2: Journal v2

- `lab.journal.conn()` теперь аддитивно мигрирует старые БД.
- В `trades` добавлены `fill_price`, `commission`, `status`.
- Добавлены таблицы `orders`, `expectations`, `reconciles`.
- `Journal.trade()` сохранил старую сигнатуру по смыслу и получил необязательные поля исполнения.
- Тест: `test_forward.TestJournalSchemaV2`.

### Итерация 3: Slippage v2

- `lab.forward.slippage()` считает adverse slippage, если заполнен `trades.fill_price`.
- Старые журналы без цены исполнения по-прежнему возвращают `available=False`.
- `daily_report()` показывает средний slippage в цене и б.п., когда данные доступны.
- Тест: `test_forward.TestSlippage`.

### Итерация 4: Validation status dashboard

- Добавлен офлайн-модуль `analysis.validation_status`.
- Он не вызывает API, а сканирует `analysis/*_validate.py` и соответствующие result-файлы.
- Сгенерирован `analysis/validation_status.md`: 3 fresh / 31 stale / 0 missing при пороге 7 дней.
- Тест: `tests/test_validation_status.py`.

### Итерация 5: Tracking expectations

- `lab.forward.tracking_vs_backtest()` теперь читает `expectations(strategy,date,exp_ret)`,
  если ручной `expected_daily_ret` не передан.
- Expected-кривая компаундится по дневным close-датам после стартовой даты.
- При отсутствующих датах функция возвращает `available=False` с явным списком первых пропусков.
- Тест: `test_forward.TestTrackingVsBacktest`.

### Итерация 6: Reconcile history

- Добавлен `lab.forward.record_reconcile()`.
- CLI `python -m lab.forward reconcile` сохраняет краткую историю сверки в `reconciles`.
- `daily_report()` в офлайн-режиме показывает последние 5 записей reconcile history.
- Тест: `test_forward.TestReconcileHistory`.

### Итерация 7: Orders lifecycle helper

- Добавлен `Journal.order()` для записи lifecycle-строк в таблицу `orders`.
- Runner/daybot пока не подключены, чтобы не менять торговое поведение фермы.
- Тест: `test_forward.TestJournalSchemaV2.test_order_helper_records_lifecycle_rows`.

### Итерация 8: Warm-up CLI docs

- `backtest/README.md` теперь содержит пример `--warmup-bars`.
- Документировано, что warm-up бары попадают в `Context.history()`, но сигналы на них заблокированы.
- Документировано, что equity/times/metрики считаются только по OOS-диапазону.

### Итерация 9: Killed strategies index

- Добавлен `analysis/KILLED_STRATEGIES.md`.
- Таблица фиксирует 12 убитых botdev-кандидатов, класс премии, причину kill и ссылки на отчёты.
- Добавлена pre-flight секция для новых стратегий, чтобы не повторять уже убитые классы без нового факта.

### Итерация 10: Validation status integration

- `analysis/run_all_validations.py` получил `write_validation_status()`.
- CLI поддерживает `--status`, `--status-out PATH`, `--stale-days N`.
- Live/API validations не запускались в этом автономном цикле; покрыта офлайн-функция записи статуса.
- Тест: `tests/test_validation_status.py::test_run_all_can_write_validation_status`.

### Итерация 11: Futures rollover guard

- В `lab/instruments.py` добавлены явные `exp`, `last_trade`, `roll_to` для BMQ6/NGN6.
- Добавлена чистая офлайн-функция `futures_roll_warnings()`.
- `lab.forward.daily_report()` показывает rollover-секцию при приближении last_trade/exp.
- Тесты: `tests/test_instruments.py`, `test_forward.TestDailyReport.test_report_shows_futures_roll_warnings`.

### Итерация 12: Key-rate centralization

- Добавлен `scripts/keyrate.py` с `KEYRATE`, `KEYRATE_EVENTS`, `CB_MEETINGS_2026`, `keyrate_on()`.
- Убраны прямые текущие KEYRATE-дубли из `scripts/market_context.py`, `scripts/dashboard.py`,
  `scripts/carry_rotation_model.py`, `scripts/stress_test_floaters.py`,
  `analysis/botdev_absmom_switch_run.py`.
- Для scripts добавлен import fallback: прямой запуск файла и package import из корня оба работают.
- Компиляция изменённых скриптов прошла без сетевых вызовов.
- Тест: `tests/test_keyrate.py`.

### Итерация 13: Tracking line in daily report

- `lab.forward.daily_report()` теперь показывает `tracking: live vs expected, gap`, если
  таблица `expectations` покрывает даты equity.
- Тест: `test_forward.TestDailyReport.test_report_shows_tracking_when_expectations_exist`.

### Итерация 14: Paper-lab report

- Добавлен `analysis/paper_lab.py`, офлайн-генератор отчёта учебного портфеля.
- CLI принимает явные `--price TICKER=PRICE`, API не вызывает.
- Сгенерирован `analysis/paper_lab_report.md`; без текущих цен строки честно помечаются `MISSING_PRICE`.
- Тест: `tests/test_paper_lab.py`.

### Итерация 15: Test-count documentation refresh

- Обновлены stale-счётчики pytest в `README.md`, `backtest/README.md`, `CLAUDE.md`.
- `AGENTS.md` не трогался, потому что уже был untracked до начала работы.

### Итерация 16: Fill-rate from orders lifecycle

- Добавлен `lab.forward.order_stats()`.
- `daily_report()` показывает fill-rate по последнему статусу каждого `order_id`, если таблица `orders` заполнена.
- Тест: `test_forward.TestDailyReport.test_report_shows_order_fill_rate_from_latest_status`.

### Итерация 17: Options CBR-vol pipeline plan

- Добавлен `analysis/OPTIONS_CB_VOL_PIPELINE.md`.
- Зафиксирован путь от подтверждённого vol-паттерна заседаний ЦБ к read-only option snapshots.
- Исправлен устаревший docstring в `analysis/options_parity_validate.py`: паритет premium-style с дисконтом.

### Итерация 18: Options CB snapshot module

- Добавлен `analysis/options_cb_snapshot.py`.
- Модуль разделяет чистые offline helpers (`chain_pairs`, `parity_fit`, `render_summary`) и live read-only CLI.
- CLI делает только read-only REST-вызовы и сохраняет JSON/markdown snapshot; в этом цикле live API не запускался.
- Тест: `tests/test_options_cb_snapshot.py`.

### Итерация 19: Options snapshot edge-case tests

- Добавлен тест на `build_snapshot()` с неполными call/put парами и отсутствующим THEOR_PRICE.
- Текущая реализация уже корректно пропускает такие страйки и не строит parity fit по одной паре.

### Итерация 20: Options snapshot CLI smoke

- Добавлен subprocess smoke-тест `python -m analysis.options_cb_snapshot --help`.
- Проверено, что help/import не требуют токена и не делают сетевых вызовов.

### Итерация 21: Options snapshot live fallback and robust parity

- Live `OptionsBy` для Si-9.26 вернул HTTP 400 `30102`; добавлен fallback на read-only
  `InstrumentsService/Options` с локальной фильтрацией цепочки.
- Сгенерированы `analysis/options_cb_snapshot.json` и `analysis/options_cb_snapshot.md`.
- Live parity показал один неконсистентный страйк (`K=87.5`, residual ~0.66): обычный fit
  `R2~0.80`, MAD-filtered robust fit `R2~0.999994`.
- Добавлен `robust_parity_fit()` и тест на одиночный плохой страйк, чтобы такие сбои не
  маскировались средним `resid_sd`.

### Итерация 22: Options pipeline quality gates

- `analysis/OPTIONS_CB_VOL_PIPELINE.md` обновлён по итогам live snapshot.
- Критерий качества перенесён с raw parity `R2` на robust parity `R2 >= 0.999`,
  `pairs >= 20` и лимит outliers.
- Зафиксировано, что snapshot с хорошим robust fit и плохим raw fit можно использовать
  только после явного исключения outlier-страйков из IV/vol метрик.

### Итерация 23: Machine-readable option snapshot quality

- Добавлен `snapshot_quality()` в `analysis/options_cb_snapshot.py`.
- JSON snapshot теперь содержит поле `quality`, markdown показывает `quality: PASS/FAIL`.
- Gate: минимум 20 пар, robust parity `R2 >= 0.999`, outliers не больше `max(1, 5% пар)`.
- Live Si snapshot классифицирован как `PASS`, потому что robust parity сходится, а выброс один.

### Итерация 24: Explicit option chain date selection

- Добавлен `select_expiry_chain()` для broad `Options()` fallback: из tolerance-окна выбирается
  одна дата цепочки, чтобы `chain_pairs()` не смешивал соседние экспирации.
- Если точная дата есть и даёт полные пары, выбирается она; иначе выбирается соседняя дата с
  максимальным числом полных call/put пар.
- Snapshot теперь пишет `chain_dates`; live Si snapshot показывает фактическую цепочку `17.09`
  при целевой дате события `2026-09-18`.

### Итерация 25: Forward partial-rate

- `lab.forward.order_stats()` теперь считает `partial` и `partial_rate` по последнему статусу
  каждого `order_id`.
- `daily_report` выводит `partial-rate`, если есть частично исполненные заявки.
- Верхний docstring `lab/forward.py` и `analysis/forward_layer_notes.md` обновлены:
  slippage больше не описан как заглушка, partial-rate реализован для заполненной таблицы `orders`.
- Тест: `test_forward.TestDailyReport.test_report_shows_partial_rate_from_latest_status`.

### Итерация 26: Validation status outcomes

- `analysis/validation_status.py` теперь извлекает outcome из result-файлов: `PASS/WARN/FAIL`
  плюс `✓` как `PASS`.
- Парсер берёт последний явный маркер, чтобы исторические FAIL в расследованиях не перекрывали
  финальный PASS.
- `analysis/validation_status.md` получил колонку `outcome` и сводку Outcomes.
- Тест: `tests/test_validation_status.py::test_collect_status_uses_last_outcome_marker`.

### Итерация 27: Validation status exit-code on FAIL

- `analysis/run_all_validations.py::write_validation_status()` теперь возвращает `1` не только
  при `MISSING`, но и при явном `outcome == FAIL`.
- `STALE`, `WARN` и `UNKNOWN` остаются нефатальными: это диагностические состояния, не красный
  статус оракула.
- Тест: `tests/test_validation_status.py::test_run_all_status_fails_on_fail_outcome`.

### Итерация 28: Strategy preflight helper

- Добавлен offline CLI `analysis/strategy_preflight.py`.
- Скрипт парсит `analysis/KILLED_STRATEGIES.md`, ранжирует совпадения новой идеи с убитыми
  классами и печатает pre-flight вопросы.
- `analysis/KILLED_STRATEGIES.md` получил пример команды.
- Тесты: `tests/test_strategy_preflight.py` (парсинг таблицы, ORB-match, report questions).

### Итерация 29: Killed-strategy evidence check

- `analysis.strategy_preflight` получил `missing_evidence()` и CLI `--check-evidence`.
- Проверяются backtick-ссылки в колонке evidence; live check по текущему индексу: `evidence OK`.
- `analysis/KILLED_STRATEGIES.md` получил пример команды проверки.
- Тесты: fake missing path + реальный индекс без missing evidence.

### Итерация 30: Shared validation-status blocking gate

- Добавлен `validation_status.has_blocking_status()`.
- Прямой CLI `python -m analysis.validation_status` и `run_all_validations --status`
  теперь используют один критерий красного статуса: `MISSING` или `outcome == FAIL`.
- `STALE`, `WARN`, `UNKNOWN` остаются диагностическими и не ломают exit-code.
- Тест: `tests/test_validation_status.py::test_has_blocking_status_flags_missing_and_fail`.

### Итерация 31: NGN6→NGQ6 офлайн-ролл (TDD)

- Добавлена `apply_roll(ticker)` в `lab/instruments.py`: in-place обновляет uid/exp/
  last_trade фьючерса на преемника из нового словаря `ROLLOVERS`, обнуляет `roll_to`.
- Преемники вынесены из комментариев в данные: `ROLLOVERS = {NGN6→NGQ6, BMQ6→BMU6}`
  (uid/exp/last_trade сверены с API 2026-06-15, point_rub наследуется от текущего).
- Повторный ролл блокируется `ValueError` (преемник преемника описывается отдельно,
  когда биржа опубликует расписание) — защита от слепого двойного переключения uid.
- Тесты (TDD red→green): `tests/test_instruments.py` — 4 новых + 2 существующих = 6 PASS.
  Autouse-фикстура `_restore_instruments` снимает/восстанавливает глобал для изоляции
  in-place мутаций.
- Ролл **не применён** к runtime-реестру: NGN6 всё ещё указывает на NG-7.26. Применять
  только после last_trade 2026-07-29 (см. `futures_roll_warnings`). Механизм готов.
- Широкий регресс: pytest 199 passed (+4), test_forward 24 OK, test_trading 8 OK.

### Итерация 32: Stale→fresh через outcomes-файл (TDD)

- Проблема: 28 из 31 stale-валидаций имели outcome `UNKNOWN` — result-файлы от
  2026-06-19 с успешными русскими формулировками («бит-в-бит», «совпадение»), но без
  явного маркера PASS/WARN/FAIL, который понимает парсер. Status-борд неотличим от
  непроверенного.
- Решение: персистентный outcomes-кэш `analysis/validation_outcomes.jsonl`. Раннер
  `run_all_validations` после live-прогона пишет JSONL (script/outcome/run_at на
  скрипт). `validation_status.collect_status` читает его как **primary source** outcome,
  с fallback на парсинг result-файла, если записи нет.
- Разделение: `state` (FRESH/STALE) — по mtime result-файла, честный возраст; `outcome`
  (PASS/WARN/FAIL) — из outcomes-кэша, свежий вердикт прогона. Stale ≠ UNKNOWN больше.
- `TERMINAL_OUTCOMES = {PASS, WARN, FAIL}`: TIME/ERR/MISS — сбои прогона, не вердикты
  оракула, в кэш не пишутся (иначе таймаут маскировался бы под свежий PASS-всегда).
- TDD red→green: 5 тестов на чтение outcomes + 2 на запись = 7 новых в
  `tests/test_validation_status.py` (всего 14).
- **Live-прогон выполнен** (read-only, боевой токен, 79с): 29 PASS / 2 WARN / 1 FAIL.
  Status-борд после: `Outcomes: PASS: 31 / WARN: 2 / FAIL: 1 / UNKNOWN: 0` (было 6/0/0/28).
  Exit=1 корректен (1 FAIL → блокирующий статус, итерация 27).
- 1 FAIL — реальная находка, не сбой: `scripts/tax_alpha_scan.py` вызывает `GetBondBy`,
  которого нет в proto-контрактах → 404. Оракул сработал как должен. Долг → очередь.

### Итерация 33: --help в run_all_validations (TDD-регресс)

- Баг: раннер парсил `sys.argv` вручную, `--help` молча игнорировался как неизвестный
  флаг → шёл полный live-прогон всех 32 валидаторов (сеть, ~минуты). Обнаружено при
  smoke-тесте итерации 32.
- TDD red→green: `test_main_help_does_not_run_validators` — monkeypatch `subprocess.run`
  в `_boom`, проверяет, что при `--help`/`-h` раннер печатает справку и выходит с code 0,
  не вызывая валидаторов. 1 новый тест (всего 15 в файле).
- `--help` теперь печатает флаги (--fast/--status/--status-out/--outcomes-out/
  --no-outcomes/--stale-days) и READ-READ инвариант.
- Широкий регресс: pytest 207 passed (+8 к 199), test_forward 24 OK, test_trading 8 OK.

### Итерация 34: GetBondBy → BondBy в tax_alpha_scan.py

- Оракул `api_method_names_validate` поймал фантомный RPC: `scripts/tax_alpha_scan.py:155`
  вызывал `InstrumentsService/GetBondBy` — этого метода нет в proto (правильное имя
  `InstrumentsService/BondBy`, как в `bond_ytm_validate.py:73`). API возвращал 404,
  `except urllib.error.HTTPError` на строке 159 молча ловил → bond-метаданные (nominal,
  aci) терялись, скрипт не падал. Тихая деградация — ровно тот класс багов, под который
  оракул заточён (AGENTS.md: «404 → try/except → молча пусто»).
- Фикс: одна строка, `GetBondBy` → `BondBy`. Verify: `api_method_names_validate.py`
  теперь PASS (38/38 RPC объявлены в proto, phantom-методов нет).
- Outcomes-кэш обновлён, status-борд: FAIL 1 → 0.

### Итерация 35: Guard write_outcomes от затирания пустым (TDD-регресс)

- Регрессия, вскрытая при восстановлении outcomes: `write_outcomes` с `results`, где все
  теги не-терминальные (TIME/ERR/MISS), писал **пустой файл**, затирая 32 записи
  предыдущего live-прогона. Root cause: тест итерации 33 `test_main_help_does_not_run_validators`
  вызывал `rav.main()` под monkeypatch `subprocess.run → _boom` (все 32 валидатора → ERR),
  `--no-outcomes` не стоял → `write_outcomes(32×ERR)` обнулил рабочий outcomes-кэш.
- TDD red→green: `test_write_outcomes_does_not_clobber_existing_with_empty` — если все
  results не-терминальные и existing-файл непустой, не перезаписывать. Второй тест
  гарантирует, что при наличии терминальных outcomes файл перезаписывается (последний
  прогон = актуальный срез).
- Тест итерации 33 изолирован: `--outcomes-out tmp_path/noop.jsonl`, чтобы не мутировать
  рабочий кэш даже при гипотетическом write.
- Восстановление: live-прогон `--fast` (24 PASS / 3 WARN / 0 FAIL, 75с) перегенерил
  outcomes. Status-борд: `PASS: 26 / WARN: 3 / FAIL: 0 / UNKNOWN: 5` (5 — SLOW-валидаторы
  из `--fast`, для них outcomes-записей нет, result-файлы без явного маркера — честная
  деградация).
- Широкий регресс: pytest 209 passed (+2 к 207), test_forward 24 OK, test_trading 8 OK.

### Итерация 36: Валидация неизвестных флагов CLI (TDD)

- Родственный баг итерации 33: раннер парсил `sys.argv` вручную, любой неизвестный флаг
  молча игнорировался → опечатка (`--fas` вместо `--fast`) уходила в полный live-прогон
  с не-теми параметрами без предупреждения.
- TDD red→green: `test_main_unknown_flag_errors_and_does_not_run` — при `--fas` раннер
  выходит с code 2 и сообщением «неизвестный флаг», не вызывая валидаторов.
  Позиционный обход value-флагов (`--status-out`/`--outcomes-out`/`--stale-days`):
  флаг + значение съедаются парами, значение не распознаётся как неизвестный флаг.
- Smoke: `--fas` → exit 2 + «неизвестный флаг: --fas»; `--stale-days 30 --help` →
  справка (значение 30 корректно съедено, не пугает как неизвестный флаг).
- Широкий регресс: pytest 210 passed (+1 к 209), test_forward 24 OK, test_trading 8 OK.

## Проверки

```bash
python -m pytest tests/ -q
# 210 passed

python -m unittest test_forward -v
# 24 tests OK

python -m unittest test_trading -v
# 8 tests OK
```

## Следующая очередь

1. Подключить runner/daybot к `Journal.order()` и заполнению `fill_price` только после отдельного ревью рисков. ПРОПУЩЕНО в этом прогоне по решению пользователя — задача 3 делегирована обратно.
2. Stale validations → fresh: 28 оракулов от 2026-06-19 с outcome UNKNOWN. РЕШЕНО в итерациях 32–35: outcomes-кэш + live-прогон дают `PASS: 26 / WARN: 3 / FAIL: 0 / UNKNOWN: 5` (5 — SLOW из `--fast`, нужен полный live-прогон без `--fast`, чтобы закрыть).
3. Применить `apply_roll("NGN6")` после 2026-07-29 (после last_trade) — механизм готов, ждём дату.
4. Event-study scaffolding после накопления option snapshots.
5. Долг: 5 SLOW-валидаторов не в outcomes (catalog_consistency, candle_aggregate, techanalysis, candle_integrity, history_archive) — полный live-прогон без `--fast` закроет.

## Проверки

```bash
python -m pytest tests/ -q
# 199 passed

python -m unittest test_forward -v
# 24 tests OK

python -m unittest test_trading -v
# 8 tests OK
```

## Следующая очередь

1. Подключить runner/daybot к `Journal.order()` и заполнению `fill_price` только после отдельного ревью рисков.
2. Stale validations → fresh: 28 оракулов от 2026-06-19 с outcome UNKNOWN. Либо запустить live-прогоны (read-only, боевой токен), либо расширить парсер outcome для старых маркеров.
3. Применить `apply_roll("NGN6")` после 2026-07-29 (после last_trade) — механизм готов, ждём дату.
4. Event-study scaffolding после накопления option snapshots.

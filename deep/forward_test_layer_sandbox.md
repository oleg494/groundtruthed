# Проект forward-test слоя для стратегий в T‑Invest Sandbox (lab/daybot)

Ниже — целостный дизайн слоя forward-testing, который накрывает существующий код стратегий в lab/daybot и учитывает:

- один sandbox‑счёт на стратегию (T‑Invest Sandbox)
- SQLite‑журнал
- `state.json` как основной persistent state

Структура ответа: архитектура, журналы, метрики live vs backtest, контрольные стратегии, алерты, reconcile портфеля/заявок, обработка failed/partial fills, блокировки, безопасность рестарта, ежедневные отчёты.

---

## 1. Общая архитектура слоя

### 1.1. Идея

Forward-test слой — это **обёртка над существующей стратегией**, которая:

- перехватывает все действия по выставлению/отмене заявок;
- поддерживает единый формат логов в SQLite;
- следит за консистентностью состояния с sandbox‑счётом;
- считает метрики и формирует ежедневный отчёт;
- обеспечивает безопасный рестарт и единственный экземпляр процесса.

Всё это реализуется без переписывания логики входа/выхода стратегии: стратегия даёт сигналы (buy/sell), слой превращает их в надёжное исполнение и мониторинг.

### 1.2. Структура директории стратегии

Для каждой стратегии:

```text
strategies/
└── {strategy_id}/
    ├── config.yaml              # Настройки стратегии + forward‑test (пороги алертов и т.п.)
    ├── state.json               # Runtime‑состояние
    ├── sandbox_account_id.txt   # ID sandbox‑счёта T‑Invest
    ├── journal.db               # SQLite‑журнал
    ├── daily_reports/           # Ежедневные HTML/PDF отчёты
    ├── logs/                    # Текстовые логи (debug/info/alerts)
    └── bot.py                   # Ваш код стратегии (оборачивается runner’ом)
```

Поверх всего — библиотека `forward_test_layer/` c модулями:

- `order_handler.py` — отправка заявок, слежение за статусами, partial/failed;
- `reconciler.py` — сверка портфеля и ордеров;
- `metrics.py` — метрики live и сравнение с backtest;
- `alert_manager.py` — алерты (Telegram/e‑mail/etc.);
- `process_lock.py` — взаимное исключение (один процесс на стратегию);
- `restart_recovery.py` — восстановление после рестартов;
- `daily_reporter.py` — ежедневные отчёты.

---

## 2. Структура журналов в SQLite

Журналы — сердце forward‑layer’а. Цель: **полный аудит + удобная аналитика**.

Ниже — ключевые таблицы (можно добавить к текущей схеме journal.db).

### 2.1. Таблица запусков `runs`

Описывает каждый непрерывный запуск стратегии.

```sql
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP,
    status TEXT CHECK(status IN ('running', 'stopped', 'crashed')) NOT NULL,
    initial_state_json TEXT NOT NULL,
    final_state_json TEXT,
    exit_reason TEXT
);
```

`run_id` =, например, `"{strategy_id}_{YYYYMMDD_HHMMSS}"`.

### 2.2. Таблица заявок `orders`

Все заявки в едином формате, независимо от логики стратегии.

```sql
CREATE TABLE orders (
    internal_order_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    sandbox_order_id TEXT,
    submission_time TIMESTAMP NOT NULL,
    instrument_uid TEXT NOT NULL,      -- FIGI / UID
    direction TEXT CHECK(direction IN ('buy', 'sell')) NOT NULL,
    order_type TEXT CHECK(order_type IN ('market', 'limit')) NOT NULL,
    quantity INTEGER NOT NULL,
    price DECIMAL(18,6),
    status TEXT CHECK(status IN (
        'pending_submission',
        'submitted',
        'partially_filled',
        'filled',
        'canceled',
        'failed'
    )) NOT NULL,
    filled_quantity INTEGER DEFAULT 0,
    average_fill_price DECIMAL(18,6),
    total_commission DECIMAL(18,6) DEFAULT 0,
    last_status_update TIMESTAMP,
    error_code TEXT,
    error_message TEXT
);
CREATE INDEX idx_orders_status ON orders(status, run_id);
```

### 2.3. История изменений статусов `order_events`

Помогает разбирать проблемы с исполнением.

```sql
CREATE TABLE order_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    internal_order_id TEXT NOT NULL REFERENCES orders(internal_order_id),
    event_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    old_status TEXT,
    new_status TEXT NOT NULL,
    filled_quantity_delta INTEGER DEFAULT 0,
    fill_price DECIMAL(18,6),
    message TEXT
);
```

### 2.4. Снимки портфеля `portfolio_snapshots`

Хранит пары «внутреннее состояние vs фактический sandbox».

```sql
CREATE TABLE portfolio_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    internal_cash DECIMAL(18,2) NOT NULL,
    actual_cash DECIMAL(18,2) NOT NULL,
    cash_difference DECIMAL(18,2) NOT NULL,
    internal_positions_json TEXT NOT NULL, -- {figi: qty}
    actual_positions_json TEXT NOT NULL,   -- то же по данным sandbox
    position_mismatches_json TEXT,         -- детализированные расхождения
    mismatch_flag BOOLEAN DEFAULT FALSE
);
CREATE INDEX idx_snapshots_timestamp ON portfolio_snapshots(timestamp);
```

### 2.5. Журнал актов сверки `reconciliation_runs`

```sql
CREATE TABLE reconciliation_runs (
    reconcile_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    discrepancies_count INTEGER DEFAULT 0,
    discrepancy_details_json TEXT,
    auto_corrected BOOLEAN DEFAULT FALSE,
    alert_triggered BOOLEAN DEFAULT FALSE
);
```

### 2.6. Алерты `alerts`

```sql
CREATE TABLE alerts (
    alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    severity TEXT CHECK(severity IN ('critical', 'warning', 'info')) NOT NULL,
    alert_type TEXT NOT NULL,
    message TEXT NOT NULL,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_time TIMESTAMP,
    resolution_note TEXT
);
```

### 2.7. Ежедневные метрики `daily_metrics`

Для live vs backtest.

```sql
CREATE TABLE daily_metrics (
    date DATE NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    total_return_pct DECIMAL(10,6) NOT NULL,
    daily_pnl DECIMAL(18,2) NOT NULL,
    sharpe_ratio DECIMAL(10,6),
    max_drawdown_pct DECIMAL(10,6),
    backtest_expected_return_pct DECIMAL(10,6),
    return_deviation_pct DECIMAL(10,6),
    orders_submitted INTEGER DEFAULT 0,
    orders_filled_full INTEGER DEFAULT 0,
    orders_filled_partial INTEGER DEFAULT 0,
    orders_failed INTEGER DEFAULT 0,
    fill_rate_pct DECIMAL(10,6),
    partial_fill_rate_pct DECIMAL(10,6),
    failed_order_rate_pct DECIMAL(10,6),
    PRIMARY KEY (date, run_id)
);
```

### 2.8. Версионирование состояния `state_history`

Для разборов и отката.

```sql
CREATE TABLE state_history (
    version_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    state_json TEXT NOT NULL
);
```

---

## 3. State JSON и безопасность рестарта

### 3.1. Структура `state.json`

Минимальный набор, необходимый для:

- корректного продолжения после рестарта;
- избежания дублирующих сигналов/заявок;
- быстрого reconcile.

Пример:

```json
{
  "run_id": "my_strategy_20260625_001",
  "last_processed_bar_timestamp": "2026-06-24T18:59:00Z",
  "internal_portfolio": {
    "cash_rub": 1000000.0,
    "positions": {
      "BBG004730N88": {"quantity": 10, "average_price": 145.2},
      "BBG000GJ3JJ8": {"quantity": 5, "average_price": 2890.5}
    }
  },
  "pending_orders": [
    {
      "internal_order_id": "ord_001",
      "sandbox_order_id": "sandbox_ord_12345",
      "instrument_uid": "BBG004730N88",
      "direction": "buy",
      "quantity": 5,
      "price": 144.8,
      "order_type": "limit"
    }
  ],
  "last_reconciliation_time": "2026-06-24T19:00:00Z",
  "metrics_summary": {
    "total_pnl_rub": 1230.5,
    "start_date": "2026-06-01"
  }
}
```

Обновление — **атомарно**:

1. записать во временный `state.json.tmp`,
2. `fsync`,
3. `rename` → `state.json`.

Каждое значимое изменение дублируется в `state_history` (не часто, например, раз в 5–10 минут или перед закрытием сессии).

### 3.2. Алгоритм рестарта

При старте:

1. **Захват process‑lock** (подробно ниже). Если занят живым процессом — выходим.
2. **Загрузка состояния**:
   - читаем `state.json`;
   - если оно битое/нет — берём последний снапшот из `state_history`.
3. **Синхронизация заявок**:
   - получаем активные заявки из sandbox по счёту;
   - проверяем каждую из `pending_orders`:
     - если нет в sandbox → помечаем как `canceled`, освобождаем ресурсы, лог/алерт;
     - если есть — сверяем объём/цену, правим внутреннее состояние при расхождениях.
   - заявки в sandbox, которых нет в `state.json`:
     - по умолчанию: **отменяем** и логируем как «orphan order».
4. **Сверка портфеля** (см. раздел 6):
   - выравниваем внутренний портфель по фактическому sandbox;
   - мелкие расхождения авто‑корректируем, крупные — в алерты.
5. **Продолжение обработки**:
   - начинаем читать маркет‑данные с `last_processed_bar_timestamp` вперёд (без повторной генерации старых заявок).
6. **Логируем событие рестарта** в `runs` + текстовый лог.

---

## 4. Order handling: failed и partial fills

### 4.1. Обёртка над стратегией

Стратегия не должна напрямую дёргать клиент T‑Invest. Вместо этого:

```python
# вместо прямого client.post_order(...)
forward_layer.buy(figi, qty, price=None, order_type="market")
forward_layer.sell(...)
```

`forward_layer`:

1. генерирует `internal_order_id`;
2. пишет запись в `orders` со статусом `pending_submission`;
3. вызывает T‑Invest Sandbox API (`postSandboxOrder`);
4. сохраняет `sandbox_order_id`;
5. запускает слежение за статусом (polling или stream).

### 4.2. Мониторинг статусов

Два варианта:

- **Streams** (`TradesStream`) — лучше для латентности.
- **Polling** `getSandboxOrderState` каждые N секунд до терминального статуса.

При каждом изменении:

- создаём запись в `order_events`;
- обновляем `orders.status`, `filled_quantity`, `average_fill_price`, `total_commission`;
- правим `state.json` (cash/positions/pending_orders).

### 4.3. Частичные исполнения (partial fills)

Обработка события partial:

1. `filled_quantity_delta` > 0:
   - пересчитываем `filled_quantity`,
   - пересчитываем среднюю цену,
   - изменяем внутренний портфель (добавляем купленные/списанные бумаги, корректируем cash).
2. Вызываем **hook стратегии**, например:

```python
strategy.on_partial_fill(order_info, remaining_quantity)
```

По умолчанию — консервативное поведение:

- оставляем остаток как активную лимитку;
- на уровне стратегии можно задать политику:
  - сразу отменять остатки;
  - или передвигать цену;
  - или ожидать N секунд, затем отменять.

Всё фиксируется в `order_events`.

### 4.4. Ошибочные исполнения (failed)

Классификация ошибок:

- **постоянные**: неверный инструмент, недостаток средств, рынок закрыт;
- **временные**: сетевой сбой, временная недоступность сервиса, редкие глюки sandbox.

Политика:

1. логируем `error_code`/`error_message` в `orders`;
2. решаем, **ретраить** ли:
   - при временных — до 2–3 попыток с backoff (1s, 2s, 4s);
   - при постоянных — не ретраим.
3. освобождаем заблокированные деньги/активы;
4. создаём алерт:
   - единичный fail → warning;
   - высокий процент fail за интервал → critical.

---

## 5. Process locks (взаимное исключение)

Цель — один живой процесс на `strategy_id`, иначе всё ломается.

### 5.1. Таблица блокировок

```sql
CREATE TABLE IF NOT EXISTS process_locks (
    strategy_id TEXT PRIMARY KEY,
    pid INTEGER NOT NULL,
    hostname TEXT NOT NULL,
    acquired_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_heartbeat TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 5.2. Алгоритм

1. При старте:
   - пробуем вставить запись (`INSERT`) по `strategy_id`;
   - если конфликт по PK:
     - читаем `last_heartbeat`;
     - если > X минут (например, 5) — считаем предыдущий процесс мёртвым, удаляем запись и ставим свою;
     - иначе — лог/ошибка и завершаем процесс.
2. Heartbeat:
   - отдельный поток обновляет `last_heartbeat` каждые 30 сек.
3. При штатном завершении:
   - удаляем строку из `process_locks`;
   - удаляем локальный `process.lock` (если используется).

### 5.3. Режим SQLite

Для повышения устойчивости:

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
```

Так журнал поддерживает параллельный доступ (чтение/запись) с минимальными блокировками.

---

## 6. Reconciling portfolio / orders

Ядро надёжности forward‑testing — регулярная сверка с фактическим состоянием sandbox.

### 6.1. Интервал

- типично: **раз в 30–60 секунд** и обязательно:
  - после рестарта;
  - перед закрытием торгового дня (для дневного отчёта).

### 6.2. Алгоритм сверки

1. **Читаем фактическое состояние** из T‑Invest:
   - портфель и позиции (`getSandboxPortfolio`/`getSandboxPositions`);
   - активные заявки (`getSandboxOrders`/`get_orders` analoг).
2. **Считаем фактический cash** из позиций (`money`) в RUB.
3. **Сравниваем**:

   - Cash:
     - `cash_diff = actual_cash - internal_cash`.
     - допускаемая погрешность: max(0.01 RUB, 0.01% от портфеля).
   - Позиции:
     - для каждого FIGI сравниваем количество;
     - позиции, которые есть только в одном из наборов, попадают в `position_mismatches_json`.
   - Активные заявки:
     - каждая `pending_order` из `state.json` должна существовать среди заявок sandbox;
     - заявки в sandbox без пары во внутреннем списке считаем **orphan**.

4. **Записываем**:
   - `portfolio_snapshots` (всё сырьё);
   - `reconciliation_runs` (краткое резюме).

5. **Авто‑коррекция**:

   - небольшие `cash_diff` → мягко выравниваем внутренний cash по факту;
   - orphan‑заявки → отменяем и выкидываем из `pending_orders`;
   - при небольших расхождениях позиций (0–1 лот) можно:
     - либо править внутреннее состояние по факту,
     - либо поставить флаг mismatch и дать алерт warning.

6. **Алерты**:

   - `critical`, если:
     - |cash_diff| > 1% от стоимости портфеля;
     - или по любой бумаге расхождение > 1 лота;
     - или обнаружены неизвестные позиции;
   - `warning`, если:
     - были orphan заявки, но успешно авто‑корректированы;
     - частые мелкие расхождения.

---

## 7. Метрики live vs backtest

### 7.1. Принципы

- Одна и та же формула для live и backtest (общий модуль `metrics.py`).
- Backtest режется ровно на период forward‑test’а для корректного сравнения.
- Комиссия и прочие условия приводятся к условиям sandbox:
  - комиссия: 0.05% от объёма сделки;
  - исполнение — как минимум по последней цене (`last price`) для маркет‑ордеров, упрощённое исполнение лимиток.

### 7.2. Ключевые метрики

**Доходность и риск:**

- дневной P&L (RUB, %);
- кумулятивная доходность с начала запуска;
- волатильность дневной доходности;
- максимум просадки (Max Drawdown) с начала;
- rolling‑Sharpe (например, 30‑дневный, годовая шкала: `Sharpe = mean / std * sqrt(252)`).

**Исполнение:**

- `fill_rate = (полностью + частично исполненные)/все отправленные`;
- `partial_fill_rate = частично исполненные / все`;
- `failed_order_rate = failed / все`;
- среднее время от отправки до полного исполнения.

**Сравнение с backtest:**

- дневная доходность live и ожидаемая по backtest;
- отклонение: `live - backtest` по дню;
- кумулятивный гэп доходности в %;
- отклонение по Max Drawdown.

Всё это дневными агрегатами складывается в `daily_metrics`.

### 7.3. Практическое использование

- **алерт**, если:
  - за 7 дней среднее отклонение доходности > 5 p.p.;
  - или кумулятивный гэп > 10 p.p.;
- ежемесячно можно запускать простые статистические тесты (например, сравнение средних доходностей live vs backtest).

---

## 8. Контрольные стратегии

Для честной оценки качества стратегии forward‑layer поддерживает **контрольные (benchmark) стратегии**, каждая в своём sandbox‑счёте, под тем же слоем.

### 8.1. Набор контролей

1. **Buy & Hold (B&H)**

- Покупаем на 100% капитала один индексный ETF (российский рынок) или набор ликвидных blue‑chips.
- Никакой ребалансировки кроме крайних кейсов (делистинг и т.п.).
- Служит бенчмарком по «рыночной» доходности.

2. **Cash‑only**

- Всегда в кэше, ноль сделок.
- Показывает «нулевую» линию, а также позволяет отследить необычные изменения кэша (ошибки sandbox или вашей логики учёта).

3. **Random strategy**

- Случайные покупки/продажи в рамках заданного risk‑budget.
- Помогает отделить «шум» от реального edge стратегии: рабочая стратегия должна опережать random по доходности/Sharpe.

4. **Inverse strategy**

- Шортит сигналы основной стратегии (там, где возможно).
- Если основная стратегия хорошая, инверсная должна системно проигрывать.

Все контролы используют те же:

- формат журналов;
- алерты;
- ежедневные отчёты.

Сравнение по метрикам делается в отчёте: 1d/7d/30d ПнЛ и т.д.

---

## 9. Алерты

### 9.1. Типы и пороги

Примеры (пороги – в `config.yaml`):

| Severity  | Тип                         | Условие (пример)                                        |
|-----------|-----------------------------|----------------------------------------------------------|
| critical  | reconciliation_mismatch     | cash diff > 1% портфеля или позиции расходятся > 1 лота |
| critical  | failed_order_rate_high      | failed > 10% за последний час                           |
| critical  | orphan_orders_or_positions  | есть неизвестные заявки/позиции в sandbox               |
| critical  | daily_loss_limit            | дневной P&L < −5%                                       |
| warning   | partial_fill_rate_high      | partial > 20% за день                                   |
| warning   | live_vs_backtest_deviation  | 7‑дневный гэп доходности > 5 p.p.                       |
| warning   | order_stuck_pending         | заявка в submitted > 10 минут                           |
| info      | daily_report_ready          | отчёт за день готов                                     |
| info      | strategy_restarted          | стратегия перезапущена                                  |

### 9.2. Каналы доставки

- **Telegram‑бот** — основной канал (чат на стратегию).
- **E‑mail** — детализированные отчёты + критические алерты.
- **Локальный лог** — `logs/alerts.log`.

### 9.3. Дедупликация и эскалация

- Один и тот же алерт с одинаковым типом/сообщением не шлётся чаще чем раз в N минут (например, 30).
- Если critical не отмечен как `resolved` через час:
  - повторный алерт с пометкой «UNRESOLVED»;
  - опционально — отправка другому контакту.

---

## 10. Ежедневные отчёты (daily reports)

Отчёт генерируется после окончания торговой сессии (когда sandbox отменяет неисполненные заявки) и кладётся в `daily_reports/YYYY-MM-DD.html` (и/или PDF).

### 10.1. Содержание отчёта

1. **Общие сведения**
   - стратегия, run_id, дата;
   - статус (running/paused).

2. **Доходность и риск**
   - дневной P&L (RUB и %);
   - кумулятивная доходность с начала запуска;
   - текущая просадка и Max Drawdown;
   - rolling‑Sharpe (30 дней).

3. **Live vs backtest**
   - таблица: `date`, `live_return`, `backtest_expected`, `deviation`;
   - график: кумулятивные кривые live и backtest;
   - 7‑дневное среднее отклонение и максимум отклонения.

4. **Качество исполнения**
   - количество заявок: всего/filled/partial/failed;
   - fill_rate, partial_fill_rate, failed_order_rate;
   - среднее время до исполнения;
   - топ инструментов по обороту.

5. **Сверка и алерты**
   - сколько раз выполнялся reconcile;
   - количество и масштаб расхождений;
   - список critical/warning алертов за день, их статус (resolved/нет).

6. **Сравнение с контрольными стратегиями**
   - таблица 1d/7d/30d доходности:
     - основная стратегия;
     - B&H;
     - cash;
     - random;
   - альфа относительно B&H.

7. **Структура портфеля**
   - текущее распределение по бумагам (% от портфеля);
   - топ‑5 вкладчиков в дневной P&L (и худших).

8. **Здоровье системы**
   - аптайм с последнего рестарта;
   - число рестартов за последние 7 дней;
   - статус process‑lock.

### 10.2. Отправка

- e‑mail с HTML‑отчётом (или ссылкой на него);
- короткое резюме в Telegram:
  - дневной P&L, drawdown, ключевые алерты, ссылки на отчёт/логи.

---

## 11. Особенности T‑Invest Sandbox, которые надо учесть

Слой forward‑test должен быть «наточен» под специфику песочницы:

1. **Жизненный цикл аккаунтов**
   - аккаунты живут до 3 месяцев без активности — нужен планировщик, который раз в пару недель:
     - либо вызывает `getAccount`;
     - либо делает мини‑операцию (пополнить/отменить тестовую заявку), чтобы продлить жизнь счёта.

2. **Пополнение и лимиты**
   - пополнение только в RUB, max 30 000 000 RUB за раз;
   - слой должен:
     - при инициализации создать счёт и пополнить на стартовый капитал;
     - мониторить остаток кэша и слать предупреждения при падении ниже порога.

3. **Комиссия**
   - 0.05% от объёма сделки — учитываем:
     - в P&L и метриках;
     - в расчётах отклонения от backtest (там нужно добавить такую же комиссионную модель).

4. **Сессия песочницы**
   - все неисполненные заявки в конце сессии **автоматически отменяются**:
     - слой должен это ожидать:
       - в дневном отчёте выделять такие отмены;
       - в `state.json` и `orders` отмечать как `canceled_by_exchange`.

5. **Отсутствие дивидендов/купонов/налогов**
   - в sandbox они не начисляются → backtest нужно приводить к тем же условиям (игнорировать дивиденды/купоны), иначе сравнение будет некорректным.

---

## 12. Интеграция с текущим lab/daybot

Как вписать всё это без радикальной ломки существующего кода:

1. **Runner над стратегией**

```python
from forward_test_layer import ForwardTestRunner
from strategies.my_strategy import MyStrategy

strategy = MyStrategy(config=...)
runner = ForwardTestRunner(
    strategy=strategy,
    strategy_id="my_strategy",
    config_path="strategies/my_strategy/config.yaml",
)
runner.run()
```

2. **Перехват ордер‑операций**

Вместо прямого обращения к клиенту T‑Invest, стратегия вызывает методы абстракции (которые runner передаёт в её конструктор):

```python
class MyStrategy:
    def __init__(self, broker):
        self.broker = broker

    def on_signal(self, signal):
        if signal.type == "buy":
            self.broker.buy(figi=signal.figi, qty=signal.qty, price=signal.price)
```

`broker` — это фасад из forward‑layer, реализующий всю описанную выше механику.

3. **Расширение SQLite‑журнала**

Если сейчас journal.db используется только для простого логирования — добавляем новые таблицы без изменения существующих. Старые скрипты отчётности продолжат работать.

4. **Совместное использование `state.json`**

Существующую структуру `state.json` **не ломаем**, а аккуратно дополняем:

- `run_id`,
- `pending_orders`,
- `last_processed_bar_timestamp`,
- агрегированные метрики.

---

## 13. Пошаговый план внедрения

1. **Шаг 1. Инфраструктура**
   - реализовать process‑locks, схему БД, атомарную работу с `state.json`;
   - сделать обёртку `ForwardTestRunner` + `broker`‑фасад.

2. **Шаг 2. Заявки**
   - подключить T‑Invest Sandbox API через обёртку;
   - реализовать логику tracking’а статусов, partial/failed.

3. **Шаг 3. Сверка и рестарт**
   - реализовать `reconciler.py`;
   - отладить сценарии рестарта (выдёргивание процесса, network‑ошибки и т.п.).

4. **Шаг 4. Метрики и отчёты**
   - завести `daily_metrics`;
   - сделать генератор HTML‑отчётов и Telegram‑уведомлений.

5. **Шаг 5. Контрольные стратегии**
   - завести отдельные sandbox‑счета под B&H, cash, random;
   - запускать под тем же слоем и включать в отчёты.

6. **Шаг 6. Боевое «инкубирование»**
   - погонять 2–3 стратегии в песочнице 2–4 недели;
   - смотреть на:
     - расхождения с backtest;
     - частоту алертов;
     - устойчивость к рестартам.

---

Этот дизайн делает forward‑testing для T‑Invest Sandbox максимально близким к «боевому» режиму, даёт полный аудит исполнения и контроль над рисками, а также создаёт надёжную базу для автоматизированного перехода от sandbox к торговле на реальных счётах.

*Источник: deep/12.txt, ресёрч №12 (разрезан 2026-07-02).*

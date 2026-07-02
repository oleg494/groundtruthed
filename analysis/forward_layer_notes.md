# Forward-test слой: что реализовано, что заглушки (2026-07-02)

Реализация дизайна `deep/forward_test_layer_sandbox.md` в объёме, который поддерживает
ТЕКУЩАЯ схема журнала (`lab/journal.py`: trades/equity/events). Новый код: `lab/forward.py`
+ `test_forward.py` (15 unittest-моков, все зелёные). Существующие файлы `lab/` НЕ менялись
(runner/journal/strategy/report нетронуты); `python -m unittest test_trading` — 8/8 OK.

## Реализовано (работает на текущей схеме)

1. **reconcile** (`forward.reconcile`, CLI `python -m lab.forward reconcile`) — dry-run
   сверка: нетто-позиции из `trades` (side формата `buy:SBER`) против факта счёта
   (`GetSandboxPortfolio`, разбор лотов как в `Ctx.positions()`), плюс счётчик активных
   заявок (`GetSandboxOrders`). Три вида расхождений: «в журнале есть, на счёте нет»,
   обратное, «размер не сходится». Счета берутся из `lab_state.json` (пишет runner).
   Никаких корректирующих заявок — только отчёт. Отсутствие ключа/сети/lab_state —
   вежливое сообщение и exit-код, не трейсбек.
2. **Фактическая частота сделок** (`trade_stats`) — сделок/день по trades + возраст
   последней сделки в тиках (прокси тика = записи equity).
3. **Equity-статистика** (`equity_stats`) — доходность с начала, maxDD, текущая просадка,
   дневная волатильность (последний equity каждого дня, stdlib `statistics`).
4. **daily_report** (CLI `python -m lab.forward report [--date YYYY-MM-DD] [--db PATH]`) —
   P&L дня (руб и %), сделки/ошибки за день, накопленные метрики, алерты,
   reconcile-статус (офлайн — честное «не выполнялся»), хвост событий дня.
5. **Алерты** (`check_alerts`, пороги — константы в шапке forward.py):
   - просадка от пика > `DD_LIMIT_PCT` (5%);
   - тишина: > `SILENCE_TICKS` (1000) equity-тиков без сделки (или вообще без сделок);
   - серия ошибок: ≥ `ERR_STREAK` (5) event'ов `%error%/%fail%` подряд в хвосте журнала.

## Честные заглушки (данных в текущей схеме журнала нет)

1. **Слиппедж** (`forward.slippage`) — `available: False`. Причина: `trades.price` для
   market-ордеров — это **last price на момент решения** (`Ctx.market` пишет
   `ctx.prices[ticker]`), а цены ИСПОЛНЕНИЯ в журнале нет нигде; в `events` цен тоже нет.
2. **Tracking live-vs-backtest** (`tracking_vs_backtest`) — гэп считается только если
   ожидаемая дневная доходность передана параметром; источника ожиданий внутри lab.db
   нет. Каркас готов: live-кривая и кумулятивный гэп считаются, не хватает эталона.
3. **Fill-rate / partial fills** из дизайна — не реализуемо: журнал не хранит статусы
   заявок (submitted/filled/canceled), только факт «сделка записана».

## Какие поля добавить в журнал (минимальный набор, без ломки схемы)

Новые колонки/таблицы аддитивны — старые `lab/report.py` и раннер продолжат работать:

| Что | Куда | Откуда брать | Что разблокирует |
|---|---|---|---|
| `fill_price REAL` | trades | `GetSandboxOrderState.averagePositionPrice` (опрос после отправки) или OperationsService | слиппедж = fill_price − price (текущий price оставить как decision price) |
| `commission REAL` | trades | `GetSandboxOrderState.executedCommission` | честный P&L по сделкам, сверка с equity |
| `status TEXT` | trades (или отдельная таблица orders) | lifecycle заявки: submitted/filled/canceled/failed | fill-rate, partial-rate, «заявка зависла» |
| таблица `expectations(strategy, date, exp_ret REAL)` | новая | прогон `python -m backtest study` на том же периоде | tracking_vs_backtest без ручного параметра, алерт «гэп > 5 п.п.» |
| таблица `reconciles(ts, strategy, n_disc, detail)` | новая | результат `forward.reconcile` по расписанию | история расхождений в daily_report, алерт на повторные mismatches |

## Ограничения / примечания

- Нетто-позиция из журнала — best-effort: если процесс упал между fill'ом лимитки и
  записью trade, журнал отстанет от счёта. Это не баг reconcile, а его смысл — расхождение
  будет показано.
- Legacy-записи trades без формата `side:TICKER` игнорируются (не гадаем тикер).
- Прокси «тика» для алерта тишины — количество записей equity (раннер пишет equity каждый
  тик в сессию); вне сессии ферма не тикает, ложных алертов тишины ночью не будет.
- reconcile ходит только в `GetSandboxPortfolio`/`GetSandboxOrders` (sandbox, read-only);
  боевой токен слой не трогает вообще.
- Контрольные стратегии из дизайна (buyhold/random) уже живут в ферме (`ACTIVE`), отчёт
  их покрывает автоматически — отдельный код не нужен; inverse/cash-only не заводились.

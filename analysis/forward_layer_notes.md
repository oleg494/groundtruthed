# Forward-test слой: что реализовано, что заглушки (2026-07-05)

Реализация дизайна `deep/forward_test_layer_sandbox.md`. Схема журнала расширена
аддитивно: старые `trades/equity/events` сохранены, в `trades` добавлены
`fill_price/commission/status`, плюс таблицы `orders`, `expectations`, `reconciles`.

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
4. **Слиппедж v2** (`forward.slippage`) — если `trades.fill_price` заполнен, считает
   adverse slippage: `fill_price-price` для buy и `price-fill_price` для sell, среднее
   в пунктах цены и б.п. Старые журналы без `fill_price` честно дают `available=False`.
5. **Order lifecycle stats** (`forward.order_stats`) — по последнему статусу каждого
   `order_id` считает fill-rate и partial-rate; `daily_report` выводит оба показателя,
   если таблица `orders` заполнена.
6. **daily_report** (CLI `python -m lab.forward report [--date YYYY-MM-DD] [--db PATH]`) —
   P&L дня (руб и %), сделки/ошибки за день, накопленные метрики, алерты,
   reconcile-статус (офлайн — честное «не выполнялся»), хвост событий дня.
7. **Алерты** (`check_alerts`, пороги — константы в шапке forward.py):
   - просадка от пика > `DD_LIMIT_PCT` (5%);
   - тишина: > `SILENCE_TICKS` (1000) equity-тиков без сделки (или вообще без сделок);
   - серия ошибок: ≥ `ERR_STREAK` (5) event'ов `%error%/%fail%` подряд в хвосте журнала.

## Честные заглушки / незавершённые потребители v2-схемы

1. **Partial fills** из дизайна — схема уже добавляет `trades.status`
   и таблицу `orders`; `Journal.order()` умеет писать lifecycle-строки, а `daily_report`
   показывает fill-rate и partial-rate по последнему статусу `order_id`. Runner/daybot пока
   не подключены к этому helper, поэтому на реальном журнале строк может не быть.

## Какие поля добавить в журнал (минимальный набор, без ломки схемы)

Новые колонки/таблицы аддитивны — старые `lab/report.py` и раннер продолжат работать:

| Что | Куда | Откуда брать | Что разблокирует |
|---|---|---|---|
| `fill_price REAL` | trades | `GetSandboxOrderState.averagePositionPrice` (опрос после отправки) или OperationsService | схема есть; slippage считает при заполнении |
| `commission REAL` | trades | `GetSandboxOrderState.executedCommission` | схема есть; P&L по сделкам ещё не использует поле |
| `status TEXT` | trades + `orders` | lifecycle заявки: submitted/partial/filled/canceled/failed | схема и `Journal.order()` есть; fill-rate и partial-rate в отчёте есть |
| таблица `expectations(strategy, date, exp_ret REAL)` | новая | прогон `python -m backtest study` на том же периоде | схема есть; `tracking_vs_backtest` читает её при наличии дат |
| таблица `reconciles(ts, strategy, n_disc, detail)` | новая | результат `forward.reconcile` по расписанию | схема есть; CLI `reconcile` пишет историю, `daily_report` показывает последние 5 записей |

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

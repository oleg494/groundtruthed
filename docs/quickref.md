# Quickref T-Invest API — одна страница

Самое нужное для повседневного анализа, чтобы не открывать много файлов. Полное — по ссылкам
на официальные источники в [`docs/README.md`](README.md).

## Контуры и токены
- Боевой (read-only в этом проекте): `invest-public-api.tbank.ru:443`, токен `TINVEST_API_KEY`.
- Песочница: `sandbox-invest-public-api.tbank.ru:443`, токен `TINVEST_SANDBOX_KEY`.
- Старые `*.tinkoff.ru` пока живы; на `*.tbank.ru` нужен `SSL_TBANK_VERIFY=True` (серт МинЦифры).

## Конвертация денег и котировок

**Quotation / MoneyValue** = `units + nano / 1_000_000_000` (nano: −999999999…999999999).
```python
def to_float(q):  # Quotation или MoneyValue
    return q.units + q.nano / 1e9
# пример: units=114, nano=250000000 → 114.25
```
В SDK: `from t_tech.invest.utils import quotation_to_decimal, decimal_to_quotation`.

**Пункты → валюта** (только MarketData; в Instruments/Operations уже в валюте):
- Облигация: `price / 100 * nominal` (пункт = % номинала).
- Фьючерс: `price / min_price_increment * min_price_increment_amount`.
> ⚠️ Цены фьючерсов/облигаций из MarketData — в **пунктах**, не подписывать как рубли.

## Лимиты (запросов/мин на сервис; суммарно ≤50 req/s)
| Сервис | Лимит | | Метод | Лимит |
|---|---|---|---|---|
| Котировки (MarketData) | 600 | | postOrder | 15/сек |
| Инструменты | 200 | | postOrderAsync | 600 |
| Операции | 200 | | getOrders | 200 |
| Песочница | 200 | | cancelOrder | 100 |
| Ордера | 100 | | getStopOrders | 60 |
| Сигналы / Автоследование | 100 | | getHistory (REST) | 30 |
| Счета | 100 | | Отчёты (операции) | 5 |
| Стоп-ордера | 50 | | | |

## Частые рецепты (MCP `t-invest`, read-only)
| Задача | Инструмент |
|---|---|
| Список счетов | `invest_list_broker_accounts` |
| Портфель / позиции | `invest_get_portfolio` / `invest_get_positions` |
| Операции (cursor) | `invest_get_operations` |
| Свечи | `invest_get_candles` |
| Стакан | `invest_get_orderbook` |
| Последняя цена | `invest_get_market_values` (LAST_PRICE) |
| Поиск инструмента | `invest_find_instrument` |
| Дивиденды / купоны | `invest_get_share_dividends` / `invest_get_bond_events` |
| Фундаментал / прогнозы | `invest_get_asset_fundamentals` / `invest_get_forecast` |
| Теханализ (RSI/EMA/BB/MACD/SMA) | `invest_get_tech_analysis` |
> Стримы, брокерские отчёты, withdraw limits — только через SDK (см. `mcp-vs-api-coverage.md`).

## SDK в двух строках
```python
from t_tech.invest import Client          # pip install t-tech-investments --index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple
with Client(TOKEN) as c: print(c.users.get_accounts())
```

## Идентификация инструментов
- Предпочтительно **UID** (`instrument_uid`) — глобально уникален.
- `ticker` неоднозначен → нужен `class_code` (иначе ошибка 30013).
- **FIGI устарел** — не использовать как основной id (см. официальную документацию по
  идентификации инструментов, ссылка в `docs/README.md`).

## Ретраи (кратко)
Ретраить: `RESOURCE_EXHAUSTED`/`INTERNAL`/`UNAVAILABLE` с бэкоффом. Не ретраить:
`INVALID_ARGUMENT`/`NOT_FOUND`/`PERMISSION_DENIED`/`FAILED_PRECONDITION`. Детали — `errors-cheatsheet.md`.

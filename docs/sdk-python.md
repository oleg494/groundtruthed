# Python SDK `t-tech-investments` (актуальный)

Официальный Python-клиент T-Invest API. Репозиторий: <https://opensource.tbank.ru/invest/invest-python>.
Версия на момент сборки docs — **1.49.1** (2026-06-12). Лицензия Apache 2.0.

> История версий: после ребрендинга нумерация прыгнула `0.3.5` → `1.0.0` (26.05.2026) → `1.49.x`.
> Это тот же по архитектуре SDK, что и старый `tinkoff-invest`, но с новым неймспейсом `t_tech.invest`.

## Установка

Пакет публикуется в приватном PyPI-индексе T-Bank (не на публичном pypi.org):

```bash
pip install t-tech-investments --index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple
```

## Быстрый старт

Синхронный клиент:

```python
import os
from t_tech.invest import Client

TOKEN = os.environ["INVEST_TOKEN"]

with Client(TOKEN) as client:
    print(client.users.get_accounts())
```

Асинхронный клиент:

```python
import asyncio, os
from t_tech.invest import AsyncClient

TOKEN = os.environ["INVEST_TOKEN"]

async def main():
    async with AsyncClient(TOKEN) as client:
        print(await client.users.get_accounts())

asyncio.run(main())
```

## Выбор контура: боевой / песочница

Целевой адрес переключается через `target` и константы из `t_tech.invest.constants`:

```python
from t_tech.invest import Client
from t_tech.invest.constants import INVEST_GRPC_API, INVEST_GRPC_API_SANDBOX

# боевой (по умолчанию)
with Client(TOKEN, target=INVEST_GRPC_API) as client:
    ...

# песочница
with Client(SANDBOX_TOKEN, target=INVEST_GRPC_API_SANDBOX) as client:
    ...
```

Также есть специализированный sandbox-клиент:

```python
from t_tech.invest.sandbox.client import SandboxClient   # или: from t_tech.invest.grpc import SandboxClient
```

Актуальные адреса: боевой `invest-public-api.tbank.ru:443`, песочница `sandbox-invest-public-api.tbank.ru:443`.

## Сервисы клиента

Атрибуты объекта `client` соответствуют сервисам API (полный список методов — в `api-services.md`):

| Атрибут | Сервис | Назначение |
|---|---|---|
| `client.users` | UsersService | счета, тариф, маржа, инфо |
| `client.instruments` | InstrumentsService | справочник инструментов, дивиденды, купоны, фундаментал |
| `client.market_data` | MarketDataService | свечи, стаканы, последние цены, теханализ |
| `client.operations` | OperationsService | операции, портфель, позиции, отчёты |
| `client.orders` | OrdersService | выставление/снятие заявок, состояние |
| `client.stop_orders` | StopOrdersService | стоп-заявки |
| `client.sandbox` | SandboxService | управление песочными счетами |
| `client.signals` | SignalsService | стратегии и торговые сигналы |

## Типы запросов и утилиты

Request-объекты и enum'ы импортируются из `t_tech.invest.schemas` (либо часть — прямо из `t_tech.invest`):

```python
from t_tech.invest import InstrumentStatus, OrderDirection, OrderType
from t_tech.invest.schemas import GetSignalsRequest, SignalState
from t_tech.invest.utils import decimal_to_quotation, now
```

Пример вызова с request-объектом:

```python
from t_tech.invest.schemas import GetSignalsRequest, SignalState
request = GetSignalsRequest(signal_state=SignalState.SIGNAL_STATE_ACTIVE)
r = client.signals.get_signals(request=request)
```

## Возможности SDK

- Синхронный и асинхронный gRPC-клиент.
- Стримы: market data, портфель, позиции, операции, состояние ордеров, сделки
  (`MarketDataStream`, `PortfolioStream`, `PositionsStream`, `OperationsStream`, `OrderStateStream`, `TradesStream`).
- Удобные обёртки стримов (`easy_stream_client`, `easy_async_stream_client`).
- Retry-клиент с бэкоффом (`retrying_client`, `async_retrying_client`).
- Выгрузка истории котировок с диапазоном «от/до» и пагинацией (`download_all_candles`, `all_candles`).
- Кеширование инструментов (`instrument_cache`, `instrument_cache_warmup`).
- Отмена всех заявок (`cancel_orders`), стоп-ордера, трейлинг-стоп.
- Готовые стратегии (`examples/strategies/`).

## Примеры

В репозитории SDK — обширная папка `examples/` (60+ файлов: sync/async варианты).
Категории: `instruments/`, `sandbox/`, `strategies/`, `users/` + плоские примеры по каждому методу.

## Частые проблемы

- **Не публикуй токен.** Храни в env/`.env`, не коммить.
- **SSL ошибка валидации** → выстави env `SSL_TBANK_VERIFY=True`, чтобы использовать встроенный
  сертификат МинЦифры.
- **Денежные значения** приходят как `Quotation`/`MoneyValue` с полями `units`/`nano` (nano = 1e-9).
  Конвертируй через `quotation_to_decimal` / `decimal_to_quotation` (custom-types в официальной документации).
- **Цена фьючерсов и облигаций в MarketData — в пунктах, не в валюте** (см. `docs/README.md`).

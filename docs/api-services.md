# Каталог сервисов T-Invest API

Полный перечень методов получен из официальных proto-контрактов
(<https://opensource.tbank.ru/invest/invest-contracts>, `src/docs/contracts/*.proto`, версия 2026-06).
gRPC-имена методов даны как в контракте; в Python SDK имена в snake_case
(`GetAccounts` → `client.users.get_accounts(...)`).

**Контуры:** боевой `invest-public-api.tbank.ru:443`, песочница `sandbox-invest-public-api.tbank.ru:443`.
**Лимиты** — по сервисам (см. официальную документацию, ссылка выше). Ниже у каждого сервиса указан лимит unary-запросов/мин.

---

## UsersService — счета (`client.users`) · лимит 100/мин
Счета, маржа, тариф, инфо о пользователе.

- `GetAccounts` — список счетов
- `GetMarginAttributes` — маржинальные показатели счёта
- `GetUserTariff` — текущий тариф (лимиты unary/stream)
- `GetInfo` — статус квалифицированного инвестора, флаги
- `GetBankAccounts` — привязанные банковские счета
- `GetAccountValues` — значения по счёту
- `CurrencyTransfer` ⚠️ запись — перевод валюты
- `PayIn` ⚠️ запись — пополнение

---

## InstrumentsService — инструменты (`client.instruments`) · лимит 200/мин
Справочник инструментов, дивиденды, купоны, фундаментал, прогнозы.

**Получение инструмента:** `BondBy`, `CurrencyBy`, `EtfBy`, `FutureBy`, `OptionBy`, `ShareBy`, `DfaBy`, `StructuredNoteBy`, `GetInstrumentBy`, `GetAssetBy`, `GetBrandBy`, `GetForecastBy`
**Списки:** `Bonds`, `Currencies`, `Etfs`, `Futures`, `Options`, `OptionsBy`, `Shares`, `Dfas`, `StructuredNotes`, `Indicatives`, `GetAssets`, `GetBrands`, `GetCountries`
**События/выплаты:** `GetBondCoupons`, `GetBondEvents`, `GetAccruedInterests`, `GetDividends`, `GetFuturesMargin`
**Аналитика:** `GetAssetFundamentals`, `GetAssetReports`, `GetConsensusForecasts`, `GetRiskRates`, `GetInsiderDeals`, `News`
**Расписание/поиск:** `TradingSchedules`, `FindInstrument`
**Избранное:** `GetFavorites`, `EditFavorites`, `CreateFavoriteGroup`, `DeleteFavoriteGroup`, `GetFavoriteGroups`

---

## MarketDataService — котировки (`client.market_data`) · лимит 600/мин
Рыночные данные: свечи, стаканы, цены, теханализ. ⚠️ Цены фьючерсов/облигаций — в пунктах, не в валюте (см. `docs/README.md`).

**Unary:** `GetCandles`, `GetLastPrices`, `GetOrderBook`, `GetTradingStatus`, `GetTradingStatuses`, `GetLastTrades`, `GetClosePrices`, `GetTechAnalysis`, `GetMarketValues`
**Стримы:** `MarketDataStream` (bidi), `MarketDataServerSideStream`
**REST:** метод `getHistory` (выгрузка истории) — отдельный лимит 30/мин.

---

## OperationsService — операции (`client.operations`) · лимит 200/мин (отчёты 5/мин)
Операции, портфель, позиции, отчёты.

**Unary:** `GetOperations`, `GetOperationsByCursor` (рекомендуемый, с пагинацией), `GetPortfolio`, `GetPositions`, `GetWithdrawLimits`, `GetBrokerReport`, `GetDividendsForeignIssuer`
**Стримы:** `PortfolioStream`, `PositionsStream`, `OperationsStream`

> ⚠️ Известный баг фильтра `GetOperationsByCursor` — см. `gotchas.md` и память `lesson-cursor-pagination`.

---

## OrdersService — заявки (`client.orders`) · лимит 100/мин
Выставление и управление биржевыми заявками. ⚠️ Все методы записи — **не вызывать на боевом read-only**.

**Unary:** `PostOrder` ⚠️ (15/сек), `PostOrderAsync` ⚠️ (600/мин), `CancelOrder` ⚠️ (100/мин), `ReplaceOrder` ⚠️, `GetOrderState`, `GetOrders` (200/мин), `GetMaxLots`, `GetOrderPrice`
**Стримы:** `TradesStream`, `OrderStateStream`

---

## StopOrdersService — стоп-заявки (`client.stop_orders`) · лимит 50/мин
- `PostStopOrder` ⚠️ запись
- `GetStopOrders` (лимит 60/мин)
- `CancelStopOrder` ⚠️ запись

---

## SignalService — сигналы (`client.signals`) · лимит 100/мин
- `GetStrategies` — список стратегий
- `GetSignals` — торговые сигналы по фильтру

---

## SandboxService — песочница (`client.sandbox`) · лимит 200/мин
Полный набор методов-зеркал боевых сервисов, но на песочном контуре (виртуальные деньги). Запись здесь разрешена.

**Счета:** `OpenSandboxAccount`, `GetSandboxAccounts`, `CloseSandboxAccount`, `SandboxPayIn`
**Заявки:** `PostSandboxOrder`, `PostSandboxOrderAsync`, `ReplaceSandboxOrder`, `GetSandboxOrders`, `CancelSandboxOrder`, `GetSandboxOrderState`, `GetSandboxOrderPrice`, `GetSandboxMaxLots`
**Стоп-заявки:** `PostSandboxStopOrder`, `GetSandboxStopOrders`, `CancelSandboxStopOrder`
**Операции/портфель:** `GetSandboxPositions`, `GetSandboxOperations`, `GetSandboxOperationsByCursor`, `GetSandboxPortfolio`, `GetSandboxWithdrawLimits`

---

## AutofollowService — автоследование (отдельный SDK `invest-autofollow`) · лимит 100/мин
Не входит в основной gRPC-контракт invest-contracts; публикуется отдельно (см. официальную документацию).

---

### Легенда
⚠️ запись — метод изменяет состояние (заявки/деньги). В этом проекте боевой токен **read-only**:
такие методы вызывать только в песочнице. Полная политика — в `CLAUDE.md`.

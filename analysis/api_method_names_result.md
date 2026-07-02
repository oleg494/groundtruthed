# Оракул-проверка: имена RPC в коде vs proto-контракты

`analysis/api_method_names_validate.py` · READ-ONLY · офлайн · оракул = официальные proto-контракты
T-Invest API (не вендорятся в этом репозитории, см. `TINVEST_PROTO_DIR` в скрипте).

- proto-файлов: **9**, объявлено RPC: **107**
- просканировано `.py` (scripts/ + analysis/): **56**, уникальных вызванных RPC: **35**

## ✓ Результат

**Phantom-методов не найдено.** Все 35 уникальных RPC, вызванных в коде, объявлены в proto. Класс багов «вызов несуществующего метода» отсутствует.

## Вызванные RPC (для справки)

- `BondBy` — 2 файл(ов)
- `Bonds` — 3 файл(ов)
- `CancelSandboxOrder` — 1 файл(ов)
- `EtfBy` — 1 файл(ов)
- `FindInstrument` — 4 файл(ов)
- `FutureBy` — 3 файл(ов)
- `Futures` — 1 файл(ов)
- `GetAccruedInterests` — 2 файл(ов)
- `GetAssetFundamentals` — 4 файл(ов)
- `GetBondCoupons` — 6 файл(ов)
- `GetBondEvents` — 1 файл(ов)
- `GetCandles` — 18 файл(ов)
- `GetDividends` — 2 файл(ов)
- `GetForecastBy` — 2 файл(ов)
- `GetInstrumentBy` — 2 файл(ов)
- `GetLastPrices` — 4 файл(ов)
- `GetLastTrades` — 3 файл(ов)
- `GetMarginAttributes` — 1 файл(ов)
- `GetMarketValues` — 10 файл(ов)
- `GetMaxLots` — 1 файл(ов)
- `GetOperationsByCursor` — 7 файл(ов)
- `GetOrderBook` — 1 файл(ов)
- `GetPortfolio` — 3 файл(ов)
- `GetPositions` — 2 файл(ов)
- `GetRiskRates` — 1 файл(ов)
- `GetSandboxAccounts` — 1 файл(ов)
- `GetSandboxOrderState` — 1 файл(ов)
- `GetSandboxOrders` — 1 файл(ов)
- `GetTechAnalysis` — 2 файл(ов)
- `OpenSandboxAccount` — 1 файл(ов)
- `PostSandboxOrder` — 1 файл(ов)
- `SandboxPayIn` — 1 файл(ов)
- `ShareBy` — 8 файл(ов)
- `Shares` — 2 файл(ов)
- `TradingSchedules` — 1 файл(ов)

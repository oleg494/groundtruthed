# Покрытие: MCP-сервер `t-invest` ↔ методы T-Invest API

Сопоставление инструментов MCP-сервера `t-invest` (основной путь анализа в сессии) с методами
из официальных proto-контрактов T-Invest API. Цель — знать, что доступно прямо через
MCP, а что только через Python SDK / REST.

Дата сверки: 2026-06-15. ✅ есть в MCP · ❌ нет в MCP (только SDK/REST) · ✍️ метод записи
(в боевом read-only не вызывать).

## UsersService
| API метод | MCP-инструмент |
|---|---|
| GetAccounts | ✅ `invest_list_broker_accounts` |
| GetInfo | ✅ `invest_get_user_info` |
| GetMarginAttributes | ✅ `invest_get_broker_account_margin` |
| GetAccountValues | ✅ `invest_get_broker_account_values` |
| GetBankAccounts | ✅ `invest_list_bank_accounts` |
| GetUserTariff | ❌ |
| CurrencyTransfer ✍️ | ✅ `invest_transfer_broker_accounts` (sandbox) |
| PayIn ✍️ | ✅ `invest_deposit_broker_account` (sandbox) |

## InstrumentsService
| API метод | MCP-инструмент |
|---|---|
| ShareBy / Bonds… (получение по id) | ✅ `invest_get_share`/`_bond`/`_etf`/`_currency`/`_future`/`_option`/`_dfa`/`_structured_note` |
| Shares/Bonds/Etfs/… (списки) | ✅ `invest_list_shares`/`_bonds`/`_etfs`/`_currencies`/`_futures`/`_dfas`/`_structured_notes` |
| OptionsBy / Options | ✅ `invest_list_options` |
| Indicatives | ✅ `invest_list_indicatives` |
| FindInstrument | ✅ `invest_find_instrument` |
| GetInstrumentBy | ✅ (через типизированные `invest_get_*`) |
| GetAssets / GetAssetBy | ✅ `invest_list_assets` / `invest_get_asset` |
| GetBrands / GetBrandBy | ✅ `invest_list_brands` / `invest_get_brand` |
| GetCountries | ✅ `invest_list_countries` |
| GetDividends | ✅ `invest_get_share_dividends` |
| GetAccruedInterests | ✅ `invest_get_bond_accrued` |
| GetBondEvents | ✅ `invest_get_bond_events` |
| GetBondCoupons | ⚠️ частично через `invest_get_bond_events` (тип CPN) |
| GetAssetFundamentals | ✅ `invest_get_asset_fundamentals` |
| GetAssetReports | ✅ `invest_get_instrument_reports` |
| GetConsensusForecasts | ✅ `invest_list_consensus_forecasts` |
| GetForecastBy | ✅ `invest_get_forecast` |
| GetRiskRates | ✅ `invest_get_risk_rates` |
| GetInsiderDeals | ✅ `invest_get_insider_deals` |
| News | ✅ `invest_get_news` |
| TradingSchedules | ✅ `invest_get_trading_schedule` |
| Избранное (Get/Edit/…Groups) | ✅ `invest_get_favorite_*` / `invest_edit_favorite_instruments` / `invest_create/delete_favorite_group` |
| GetFuturesMargin | ❌ |

## MarketDataService
| API метод | MCP-инструмент |
|---|---|
| GetCandles | ✅ `invest_get_candles` |
| GetOrderBook | ✅ `invest_get_orderbook` |
| GetLastTrades | ✅ `invest_get_last_trades` |
| GetTradingStatus(es) | ✅ `invest_get_trading_statuses` |
| GetTechAnalysis | ✅ `invest_get_tech_analysis` |
| GetMarketValues | ✅ `invest_get_market_values` |
| GetLastPrices | ⚠️ через `invest_get_market_values` (LAST_PRICE) |
| GetClosePrices | ⚠️ через `invest_get_market_values` (CLOSE_PRICE) |
| MarketDataStream / ServerSideStream | ❌ стримы — только SDK |

## OperationsService
| API метод | MCP-инструмент |
|---|---|
| GetOperations / GetOperationsByCursor | ✅ `invest_get_operations` (cursor) |
| GetPortfolio | ✅ `invest_get_portfolio` |
| GetPositions | ✅ `invest_get_positions` |
| GetWithdrawLimits | ❌ |
| GetBrokerReport | ❌ |
| GetDividendsForeignIssuer | ❌ |
| PortfolioStream / PositionsStream / OperationsStream | ❌ стримы — только SDK |

## OrdersService (✍️ запись — в боевом read-only не вызывать)
| API метод | MCP-инструмент |
|---|---|
| PostOrder ✍️ | `invest_create_order` |
| PostOrderAsync ✍️ | ❌ |
| CancelOrder ✍️ | `invest_cancel_order` |
| ReplaceOrder ✍️ | `invest_replace_order` |
| GetOrderState | ✅ `invest_get_order_state` |
| GetOrders | ✅ `invest_get_orders` |
| GetMaxLots | ✅ `invest_get_max_lots` |
| GetOrderPrice | ✅ `invest_get_order_price` |
| TradesStream / OrderStateStream | ❌ стримы — только SDK |

## StopOrdersService
| API метод | MCP-инструмент |
|---|---|
| PostStopOrder ✍️ | `invest_create_stoporder` |
| CancelStopOrder ✍️ | `invest_cancel_stoporder` |
| GetStopOrders | ✅ `invest_get_stoporders` |

## SignalsService
| API метод | MCP-инструмент |
|---|---|
| GetStrategies | ✅ `invest_list_strategies` |
| GetSignals | ✅ `invest_list_signals` |

## SandboxService
Песочные методы (`PostSandboxOrder`, `GetSandboxPortfolio`…) в MCP отдельно не выделены —
управление песочными счетами идёт через те же `invest_*` + `invest_deposit_broker_account` /
`invest_transfer_broker_accounts`. Полноценная торговля в песочнице — через слой `lab/` (SDK/REST).

## MCP-only удобные инструменты (агрегаты поверх API)
- `invest_get_blocked_guarantee` — заблокированное ГО под фьючерсы (агрегат позиций).

## Главные пробелы MCP (доступно только через SDK/REST)
1. **Все стримы** (market data, портфель, позиции, операции, сделки, состояние ордеров) — для
   реал-тайм нужен Python SDK (`t_tech.invest`, стримовые примеры).
2. **Брокерские отчёты** `GetBrokerReport`, `GetDividendsForeignIssuer`.
3. **Лимиты вывода** `GetWithdrawLimits`.
4. **`GetFuturesMargin`** (ГО по фьючерсу до сделки), **`GetUserTariff`** (детальные лимиты), **`PostOrderAsync`**.

> Вывод для сессии: для разового анализа портфеля/рынка MCP покрывает почти всё. Для стримов,
> отчётов и лимитов вывода — переключаться на SDK-скрипты в `scripts/`.

# Индекс проверок-оракулов (verification probes)

Карта всех валидаций в `analysis/`: каждая проверяет данные/вычисления T-Invest против
**объективного оракула** (внешний эталон, биржевое число или безарбитражное тождество).
Где можно — бит-в-бит. Запуск: `python analysis/<имя>.py`. Подробности — в соседних `*_result.md`.

**Всего за сессию 2026-06-19: 28 проверок** + 6 из прошлой сессии. Покрыты все основные сервисы
T-Invest: MarketData (свечи/индикаторы/стакан/сделки/значения), Instruments (облигации/дивиденды/
фундаментал/прогнозы/каталог), Operations (портфель/операции/комиссии), Users (маржа), Orders
(лимиты заявок), производные (опционы/фьючерсы/CIP/термструктура). Вскрыто ~10 недокументированных
конвенций и ~9 граблей (см. `docs/gotchas.md`).

## Проверки против API/биржи как оракула (эта сессия)

| проверка | оракул | результат | вскрытая конвенция |
|---|---|---|---|
| `techanalysis_validate` | серверные индикаторы | **2463/2464 бит-в-бит** | RSI=Уайлдер, BB=population(÷N), EMA seed-инвариант при прогреве |
| `candle_aggregate_validate` | свечи старшего ТФ | день 12/12, неделя 55/55, месяц 11/11 | граница дня=MSK; выходные дневные — отдельный пайплайн |
| `trades_candle_validate` | минутные свечи | O/H/L/V 29/29, close 24/29 | объём в штуках 1:1; close ограничен секундными метками |
| `options_parity_validate` | THEOR-цены, паритет | R²=0.999996, бабочка≥0 | premium-style паритет С ДИСКОНТОМ ⇒ r_rub≈13.6%, форвард |
| `futures_cip_validate` | базис фьючерсов Si | (r_rub−r_usd)=10.5%, σ=0.27пп | кросс-сверка с опционами ⇒ r_usd≈3% |
| `bond_ytm_validate` | поле `yield` API | совпадение ~1.3 б.п. | эффективная, ACT/365, расчёты T+1 |
| `dividends_validate` | даты дивидендов | T+1: 100/106 (праздники) | lastBuy=record−1 торг.день; yield≠net/close (снимки) |
| `fundamentals_consistency_validate` | поля-коэффициенты | **52/52 тождеств** | P/E=MC/NI, P/S, P/FCF, EPS, netMargin… |
| `index_reconstruct_validate` | дневная Δ IMOEX | Σвесов=100%, Δ 0.067пп | капвзвешенное тождество Δ=Σ(вес·r) |
| `portfolio_margin_validate` | поля портфеля/маржи | тождества до копейки | minimal=½starting; missing=start−liquid; риск-ставки пусты |
| `fiftytwo_week_validate` | 52w high/low фундаментала | **14/14 бит-в-бит** | max(daily High)/min(daily Low), интрадей не close |
| `beta_validate` | поле beta фундаментала | ср.\|Δ\|=0.028 | дневные доходности за 1 год vs IMOEX (cov/var) |
| `forecast_validate` | поля консенсус-прогноза | 46/48 арифм. | consensus=mean таргетов; рекомендация — НЕ из голосов/апсайда |
| `commission_validate` | payment сделки | **106/106 до копейки** | payment=round(qty·price); тариф 0.0593%/копилка 0% |
| `orderbook_validate` | структура стакана | 24/24 структурных | книга упорядочена/не пересечена/в коридоре; last — до снимка |
| `techanalysis_pricetypes_validate` | серверный SMA | CLOSE/OPEN/HIGH/LOW/AVG + week бит-в-бит | typeOfPrice выбирает поле, AVG=(o+h+l+c)/4 |
| `candle_integrity_validate` | инварианты свечей | H/L и цены>0: 0/2104 | vBuy+vSell=vol 2094/2100 (аукционы у акций) |
| `money_market_validate` | ставка RUSFAR | фонд −0.65пп, 97.8% дней↑ | TMON@ = RUSFAR минус комиссия |
| `price_grid_validate` | minPriceIncrement | **6428/6428 на сетке** | шаг: акции 0.01, ОФЗ 0.001, фьючерс 1 пункт |
| `moneyvalue_validate` | тождество Σ(классы)==total (живое) | 1381 поле OK, Δ=0 | сырой REST НЕ кладёт строку value (только MCP/SDK) |
| `determinism_validate` | повторные вызовы | идемпотентно 3/3, нарезка 318/318 | данные воспроизводимы, независимы от окна |
| `term_structure_validate` | безарбитражность | контанго, фвд-ставки >0 | нет календарных арбитражей в линейке Si |
| `maxlots_validate` | GetMaxLots | **9/9 тождеств** | продажа=позиция, лимит покупки=лоты по цене |
| `exec_vs_candle_validate` | Operations↔MarketData | биржевой 67/67 в [low,high] | копилка торгует через OTC TMON (нет свечей) |
| `catalog_consistency_validate` | List* ↔ *By | **136/136 полей** | каталог=карточка, один источник истины |
| `bond_events_validate` | GetBondEvents↔Coupons | **102/102 купона** | два метода дают один купонный график |
| `auction_days_validate` | кластеризация по датам | 95% на общих датах | vol>buy+sell — общерыночный аукционный день |
| `rate_derivatives_validate` | FindInstrument/proto | RUSFAR-фьючерсов нет (find=0), RFU6 OI=80 | 1MFR/RUSFAR отсутствуют в API; RFU6 в пунктах, не 100−ставка |
| `history_archive_validate` | GetCandles | OHLC бит-в-бит 4/4, ts 1:1 | архив history-data=GetCandles по цене; объём в лотах ИЛИ штуках (ненадёжно) |
| `api_method_names_validate` | proto-контракты (статика, офлайн) | 35/35 RPC объявлены, phantom нет | ловит вызов несуществующего RPC (silent-stub: 404→try/except→пусто) |

| `trade_direction_validate` | tick-rule | 72–76% согласовано | direction=сторона агрессора (order-flow) |

Запуск всего набора: `python analysis/run_all_validations.py [--fast]` (прогон 2026-07-01:
24 PASS / 3 WARN / 0 FAIL). WARN у `index_reconstruct` (эталон IMOEX УДАЛЁН из API, проверять нечем)
и у стакан-/маржа-зависимых проверок вечером/в выходные (вне основной сессии данные частичны);
`beta`/`money_market` мигают PASS↔WARN — эталон IMOEX/RUSFAR отвечает не всегда (удалён из API, но
периодически ещё резолвится). `moneyvalue` починен 2026-07-01: снят протухший хардкод MCP-снимка,
поставлено живое тождество портфеля Σ(классы)==total. Редкий транзиентный сетевой FAIL — перезапуск.

## Проверки прошлой сессии (для полноты)

| проверка | оракул | результат |
|---|---|---|
| `account_reconcile` | GetPositions vs поток операций | основной счёт до копейки |
| `tax_reconcile` | поле `yield` брокера (P&L) | знаковый FIFO Δ=0.00 (вскрыт шорт TGLD, гросс-yield) |
| `backtest_validate` | независимая реконструкция + реплей | бит-в-бит maxΔ=0 |
| `backtrader_diff` | сторонний backtrader | 8/8 до машинной точности |
| `inference_validate` | статслой (DSR/PSR/walk-forward) | вскрыта и исправлена ошибка масштабирования DSR |
| `bughunt_result` | инъецированные баги | 6/6 найдено, 0 ложных |

## Принцип
Каждая проверка имеет беспощадный оракул: совпало с эталоном или нет, видно объективно.
Где модель/гипотеза не угадана (futures-style паритет опционов, наивный НКД, sample-дисперсия
BB) — оракул это показал, и конвенция исправлена/задокументирована, а не подогнана под «успех».
Отрицательные и частичные результаты (НКД не наивно-линеен, close из сделок не восстановить,
выходные свечи) зафиксированы честно в `docs/gotchas.md`.

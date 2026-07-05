# backtest — движок бэктестинга (stdlib-only)

Самодостаточный событийный бэктестер на чистом Python (без numpy/pandas). Отдельный
изолированный пакет: **не импортирует и не меняет** `lab/`, `daybot/`, `scripts/`.
Назначение — проверять торговые идеи на истории (или синтетике) за секунды, до любого
выхода в песочницу, с честными метриками и без заглядывания в будущее.

> Это аналитический инструмент, а не торговый бот. Сети касается только опциональный
> read-only фетч свечей (`MarketDataService/GetCandles` через sandbox-домен). Заявок
> не размещает — режим проекта read-only соблюдён.

## Зачем

В `lab/` стратегии форвард-тестятся вживую в песочнице — это правда, но медленно
(дни/недели на один прогон). Бэктестер прокручивает те же идеи на годах истории
мгновенно: подобрать параметры, оценить просадку, отсеять переоптимизацию — и только
стоящее нести в песочницу.

## Быстрый старт

```bash
# демо: 6 стратегий на синтетике + HTML-отчёт
python -m backtest demo --html demo.html

# один прогон на синтетике (без сети)
python -m backtest run --strategy sma_cross --params fast=20,slow=60 --synthetic gbm:750:1

# один прогон на реальных дневных свечах (read-only фетч + кэш)
python -m backtest run --strategy donchian --params n=20,exit_n=10 --ticker SBER --days 500 --html sber.html

# сеточная оптимизация по Sharpe
python -m backtest optimize --strategy sma_cross --grid "fast=10,20,30;slow=50,80,120" --synthetic gbm:1000:3

# walk-forward (IS/OOS, 4 окна) — честная проверка на переоптимизацию
python -m backtest walkforward --strategy donchian --grid "n=10,20,40;exit_n=5,10" --splits 4

# walk-forward с прогревом индикаторов: OOS видит 120 баров истории, но сигналы на warm-up заблокированы
python -m backtest walkforward --strategy xsec_momentum --grid "lookback=63,126;top=3" --splits 4 --warmup-bars 120

# Monte-Carlo устойчивости (ресэмплинг сделок или доходностей)
python -m backtest montecarlo --strategy sma_cross --synthetic gbm:1000:3 --source trades --n 3000

# робастность: PSR / Deflated Sharpe с поправкой на число испытаний сетки
python -m backtest robust --strategy sma_cross --grid "fast=10,20,30;slow=50,80,120" --synthetic gbm:1000:3

# сравнение с buy&hold: alpha/beta, корреляция, up/down capture
python -m backtest bench --strategy macd --synthetic gbm:1000:3

# портфельный ребаланс корзины (5 синтетических тикеров)
python -m backtest run --strategy rebalance --synthetic basket:5:800:2

# HTML-оверлей нескольких стратегий (tearsheet)
python -m backtest tearsheet --strategies "buyhold,sma_cross,macd,donchian" --synthetic gbm:800:3 --html ts.html

# 2D-тепловая карта оптимизации
python -m backtest heatmap --strategy sma_cross --grid "fast=10,20,30;slow=50,80,120" --x fast --y slow --html hm.html

# чувствительность к комиссии/слиппеджу
python -m backtest costs --strategy donchian --params n=20,exit_n=10 --synthetic gbm:1000:3

# выгрузка equity/trades в CSV+JSON
python -m backtest export --strategy macd --synthetic gbm:600:1 --prefix out

# КАПСТОУН: весь пайплайн за раз (optimize→walk-forward→robust→MC→bench) + HTML с вердиктом
python -m backtest study --strategy donchian --grid "n=10,20,40;exit_n=5,10" --synthetic gbm:1200:5 --html study.html

# риск-отчёт: VaR/CVaR/Ulcer/топ-просадки + помесячный календарь доходности
python -m backtest risk --strategy donchian --params n=20,exit_n=10 --synthetic gbm:900:3

# ансамбль нескольких стратегий (risk-parity аллокация)
python -m backtest ensemble --strategies "sma_cross,donchian,macd,rsi_reversion" --risk-parity --synthetic gbm:900:7

# сценарии: стратегия против всех режимов рынка (тренд/блуждание/возврат/цикл)
python -m backtest scenarios --strategy macd --regimes --seeds 50

# сводный дашборд: сравнение всех JSON-прогонов в папке (тепловая карта, спарклайны)
python -m backtest dashboard --dir runs/ --html dashboard.html
```

Источник данных у `run/optimize/walkforward/montecarlo`:
- `--synthetic gbm:<bars>:<seed>` (а также `trend`, `mean_revert`, `sine`) — без сети, детерминированно;
- `--uid <UID> --ticker <T> --days <N>` — реальные свечи (есть пресеты `SBER/GAZP/LKOH/GLDRUBF`).

Для `walkforward` длинные lookback-стратегии можно запускать с `--warmup-bars N`: OOS-окно
получает N предыдущих баров в `Context.history()` для прогрева индикаторов, но `on_bar`
стратегии вызывается только с первой OOS-метки. Equity, times и метрики считаются только
по OOS-диапазону. Это убирает артефакт «холодного старта», когда стратегия молчит первые
десятки/сотни баров каждого OOS-окна из-за нехватки истории.

## API

```python
from backtest import candles, strategies, run, metrics, text_report, html_report

data = candles.gbm("TEST", bars=750, seed=1)            # dict ticker -> list[Bar]
res  = run(strategies.SMACross(fast=20, slow=60), data,  # прогон
           cash=100_000, commission=0.0005, slippage=0.0005)
m = metrics(res)
print(text_report(res, m))
open("report.html", "w", encoding="utf-8").write(html_report(res, m))
```

### Своя стратегия

```python
from backtest.engine import Strategy
from backtest import indicators as ta

class MyStrat(Strategy):
    name = "my"
    def __init__(self, n=20):
        self.n = n
    def on_bar(self, ctx):
        t = ctx.tickers()[0]
        ma = ta.sma(ctx.closes(t), self.n)
        if ma is None:
            return
        if ctx.price(t) > ma and ctx.position(t) == 0:
            ctx.order_target_percent(t, 0.95)   # войти на 95% капитала
        elif ctx.price(t) < ma and ctx.position(t) != 0:
            ctx.close(t)                          # выйти в кэш
```

`Context` (то, что видит стратегия на баре): `ctx.price/bar/closes/highs/lows/opens`,
`ctx.position/cash/equity/tickers`, заявки `ctx.buy/sell/order/close/order_target_percent/cancel`.
Вся история — только прошлое + текущий бар; будущее недоступно по построению.

## Модель исполнения (важно)

- **Нет заглядывания вперёд.** Сигнал считается по закрытию бара `i`, заявка исполняется
  по **открытию бара `i+1`**. Брокер не знает будущего.
- **Комиссия** — доля нотионала в обе стороны (`commission`, дефолт 5 б.п.).
- **Проскальзывание** (`slippage`) — доля цены всегда против нас (покупаем дороже,
  продаём дешевле). Лимиты исполняются, если бар диапазоном `[low, high]` коснулся цены.
- **Две денежные модели** (поле `Instrument.kind`):
  - `"cash"` (по умолчанию, как акции): покупка уводит кэш на нотионал + комиссию,
    продажа возвращает. `equity = кэш + рыночная стоимость позиций`.
  - `"futures"` (маржинальная): кэш НЕ тратится на нотионал (блокируется ГО),
    реализованный P&L кредитуется в кэш при сокращении, `equity = кэш + нереализованный P&L`.
    Согласовано с честным equity песочницы T-Invest (`lab/strategy.py`). Цена фьючерса
    в пунктах → задайте `multiplier=point_rub`. CLI: флаги `--futures --multiplier N`.
- **Шорты** поддержаны (qty < 0), усреднение позиции — по средней цене, реализованный
  P&L пишется в `Trade` при сокращении/закрытии/перевороте.

## Метрики

`total_return, CAGR, годовая волатильность, Sharpe, Sortino, max drawdown (+длительность),
Calmar, число сделок, винрейт, profit factor, средняя сделка, матожидание, экспозиция,
лучший/худший бар, уплаченные комиссии`. Годовая нормировка — по медианному шагу баров
(дневные → 252 торговых дня).

## Состав

| файл | что |
|------|-----|
| `core.py` | `Bar`, `Instrument`, `Order`, `Position`, `Trade`, `Broker` (исполнение, комиссия, слиппедж) |
| `indicators.py` | SMA, EMA, RSI, Bollinger, Donchian, ATR, stdev, z-score, кроссы + MACD, ADX/DMI, Keltner, Supertrend, OBV, ROC, Stochastic |
| `engine.py` | событийный цикл, `Strategy` ABC, `Context` (+ opt-in стопы: `set_stop/set_take/set_trailing/update_stops`), `Result`, `run()` |
| `metrics.py` | метрики из equity-кривой и сделок |
| `sizing.py` | сайзинг позиции: fixed-fractional, ATR-risk (правило 1%), vol-target, capped-Kelly |
| `strategies.py` | классика + macd, turtle, voltarget, dualmom, atr_breakout, sma_trail, pairs (стат-арб), regime_donchian (ADX-фильтр), orb (пробой утреннего диапазона — порт daybot, нужны внутридневные бары) |
| `portfolio.py` | `RebalancePortfolio` — ребаланс корзины (equal / inverse-vol / фикс. веса), drift-band, кэш-буфер |
| `candles.py` | синтетика (GBM/trend/mean-revert/sine/basket) + ресэмплинг таймфреймов + read-only фетч с диск-кэшем |
| `optimize.py` | grid + random search + robust-select + walk-forward IS/OOS + cost-sweep; кастомная цель `objective(Metrics)` |
| `montecarlo.py` | бутстрап сделок/доходностей → распределение итогов и просадок |
| `robust.py` | Probabilistic / Deflated Sharpe, чувствительность по сетке, деградация IS→OOS |
| `benchmark.py` | сравнение с бенчмарком: alpha/beta, корреляция, tracking error, IR, up/down capture |
| `risk.py` | хвостовой риск: VaR/CVaR, Ulcer, tail ratio, Omega, gain-to-pain, топ-просадки, календарь доходности |
| `validate.py` | детектор lookahead (возмущение будущего) + детектор дыр/дублей в данных |
| `ensemble.py` | портфель стратегий: аллокация (равная / risk-parity) + матрица корреляций рукавов; результат как Result |
| `scenarios.py` | прогон стратегии по N синтетическим мирам и по режимам (тренд/блуждание/возврат/цикл) |
| `analytics.py` | аналитика сделок: серии (стрики), периоды удержания, payoff ratio |
| `export.py` | выгрузка equity/trades в CSV, полная сводка в JSON |
| `dashboard.py` | сводный HTML-дашборд: таблица сравнения прогонов из JSON, тепловая карта метрик, мини-спарклайны equity |
| `report.py` | текст + HTML с inline-SVG: отчёт (equity+underwater), tearsheet-оверлей, heatmap, walk-forward, study-отчёт |
| `study.py` | капстоун: end-to-end пайплайн optimize→walk-forward→robust→MC→bench + честный вердикт |
| `__main__.py` | CLI (17 команд: `run/optimize/walkforward/montecarlo/robust/bench/tearsheet/export/heatmap/costs/risk/ensemble/scenarios/study/dashboard/demo/fetch`) |

## Тесты

```bash
python -m pytest tests/ -q
```

208 тестов на синтетике и вручную собранных барах: отсутствие lookahead (исполнение по
следующему открытию), учёт комиссии и слиппеджа, шорты, усреднение позиции и
реализованный P&L, сайзинг `order_target_percent` и риск-сайзинг по ATR, корректность
метрик и просадки, расширенные индикаторы, портфельный ребаланс и drift-band, opt-in
стоп/тейк/трейлинг-логика, PSR/DSR и деградация IS→OOS, alpha/beta к бенчмарку,
детерминизм генераторов и воспроизводимость прогонов, ранжирование grid-поиска,
walk-forward, разброс Monte-Carlo.

## Что осознанно НЕ делает

- Не моделирует дивиденды, проскальзывание по объёму стакана, частичные исполнения по
  ликвидности, внутридневную вариационную маржу и принудительное закрытие по ГО. Для
  учебного движка на дневных барах это шум; при переходе на внутридневку имеет смысл добавить.
  (Базовая маржинальная модель фьючерса — `kind="futures"` — уже есть.)
- Не оптимизирует производительность под миллионы баров (чистый Python). Дневных свечей за
  годы — тысячи, скорость не проблема.
```

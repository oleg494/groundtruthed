# Миграция со старого `tinkoff.invest` на `t_tech.invest`

> ⚠️ Это **план/справка**, а не инструкция «выполнить сейчас». Цель проекта — не ломать работающее.
> Текущий код торгует и анализирует на старом SDK/домене и **работает**. Мигрировать осознанно,
> по одному слою, с проверкой, а не глобальной заменой строк.

## ✅ Статус: SDK-слой мигрирован (2026-06-19)

`scripts/explore.py` переведён на `t_tech.invest` (dual-import с откатом на старый SDK).
Установлен `t-tech-investments 1.49.2` из приватного индекса. Запущен на боевом read-only
токене через новый домен `invest-public-api.tbank.ru` (серт МинЦифры, `SSL_TBANK_VERIFY=True`)
— **вывод сошёлся с независимой реконсиляцией счёта** (`analysis/account_reconcile.py`):
портфель 58911.40 ₽, ETF ×370 @ 159.22, кэш 0. Попутно починены два латентных бага старого
кода, не всплывавших из-за неустановленного SDK: `pos.ticker` (нет такого поля) и
`portfolio.currencies` (у `PortfolioResponse` нет — кэш в `total_amount_currencies`).
REST-слой (`lab/`, `sandbox_grid.py`) сознательно НЕ тронут — он на urllib и домене tinkoff.ru,
работает; мигрировать его отдельно и только при остановленных ботах.

## Что использует проект сейчас (факт на 2026-06-15)

| Место | Что | Тип доступа |
|---|---|---|
| `scripts/explore.py` | `from t_tech.invest import ...` (мигрирован 2026-06-19) | Python SDK (новый) |
| `lab/api.py`, `scripts/sandbox_grid.py` | REST на `*-invest-public-api.tinkoff.ru` | прямой REST через `requests` |
| `requirements.txt` | `tinkoff-invest>=0.3.0` | старый пакет с pypi.org |
| MCP-сервер `t-invest` | боевой домен внутри сервера | инструменты `mcp__t-invest__*` |

Домены в коде: `invest-public-api.tinkoff.ru` (7×), `sandbox-invest-public-api.tinkoff.ru` (3×).

## Таблица соответствий

| Старое | Новое |
|---|---|
| `pip install tinkoff-investments` (pypi.org) | `pip install t-tech-investments --index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple` |
| `from tinkoff.invest import Client, AsyncClient` | `from t_tech.invest import Client, AsyncClient` |
| `from tinkoff.invest.schemas import ...` | `from t_tech.invest.schemas import ...` |
| `from tinkoff.invest.utils import ...` | `from t_tech.invest.utils import ...` |
| `from tinkoff.invest.constants import INVEST_GRPC_API` | `from t_tech.invest.constants import INVEST_GRPC_API` |
| `from tinkoff.invest.sandbox.client import SandboxClient` | `from t_tech.invest.sandbox.client import SandboxClient` |
| Домен `invest-public-api.tinkoff.ru:443` | `invest-public-api.tbank.ru:443` |
| Домен `sandbox-invest-public-api.tinkoff.ru:443` | `sandbox-invest-public-api.tbank.ru:443` |

API сервисов и сигнатуры методов совместимы по смыслу (тот же gRPC-контракт). Имена сервис-атрибутов
клиента те же: `users`, `instruments`, `market_data`, `operations`, `orders`, `stop_orders`, `sandbox`, `signals`.

## Статус доменов — перепроверено 2026-07-01 (живая проба из Windows-стора)

Повод: в чате T-Invest (30.06.2026) объявили отзыв серта **GlobalSign** на `tinkoff.ru`
с 2 июля. Перепробовали живьём — тревога по нам НЕ применима:

| Домен | Результат 2026-07-01 | Вывод |
|---|---|---|
| `invest-public-api.tinkoff.ru` | HTTP 405, `tls_verify=0` (OK), issuer **HARICA DV TLS**, действ. до 2026-11-11 | **жив и доверен**. T-Bank уже перевёл домен с GlobalSign на HARICA (публичный CA, есть в Windows). Отзыв GlobalSign по нам не бьёт — код не пиннит серт |
| `invest-public-api.tbank.ru` | curl rc=60 `SEC_E_UNTRUSTED_ROOT`, issuer `Russian Trusted Sub CA` (МинЦифры), до 2026-10-01 | **жив**, но корень МинЦифры НЕ в Windows-сторе → строгая TLS падает без установки сертов |
| `developer.tbank.ru` / `opensource.tbank.ru` | портал/репозитории доступны | ок |

**Итог:** миграция по-прежнему НЕ срочная, и дедлайн 2 июля к нам не относится — REST-слой
ходит через системный trust store и следует за актуальным сертом `tinkoff.ru` (сейчас HARICA,
валиден до ноября 2026). Переходить на `*.tbank.ru` придётся лишь когда старый домен реально
задекомиссят (признак: 301/410/таймауты). Тогда обязателен серт МинЦифры: установить в Windows
(системно) ЛИБО подключить явным CA-bundle в `urllib` (SDK t_tech везёт его сам, `SSL_TBANK_VERIFY=True`).

**Про токен:** при смене домена токен НЕ меняется и не пересоздаётся — меняется только URL (+серт).
Оба ключа (`TINVEST_API_KEY` боевой, `TINVEST_SANDBOX_KEY` песочница) остаются как есть.

## Рекомендуемый порядок (если решим мигрировать)

1. **Старый домен `*.tinkoff.ru` пока жив** (см. таблицу выше) — миграция не срочная.
   Признак необходимости: ответы 301/410/таймауты на старом домене.
2. **Параллельная установка.** `t-tech-investments` ставится из приватного индекса и не конфликтует
   с `tinkoff-invest` по неймспейсу (`t_tech` vs `tinkoff`) — можно держать оба во время перехода.
3. **Слой REST (`lab/api.py`, `sandbox_grid.py`).** Вынести домен в одну константу (если ещё не),
   переключать через env. Не хардкодить заново.
4. **Слой SDK (`scripts/explore.py`).** Заменить импорт `tinkoff` → `t_tech`, прогнать `explore.py`.
5. **`requirements.txt`.** Добавить новый пакет с `--index-url` (комментарием — приватный индекс).
6. **Проверка после каждого шага** — `python scripts/explore.py` (боевой read-only) и smoke-тест песочницы.

## Риски / на что смотреть

- Приватный индекс PyPI требует доступности `opensource.tbank.ru` при установке (нет offline-кеша → нет установки).
- `SSL_TBANK_VERIFY=True` для встроенного сертификата МинЦифры — может понадобиться на новом домене.
- MCP-сервер `t-invest` инкапсулирует домен внутри — миграция кода его не затрагивает.
- Боты `lab/`/`daybot/` крутятся на песочнице 24/7 — менять их только при остановленном процессе.

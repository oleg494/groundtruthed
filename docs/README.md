# Документация T-Invest API (актуальная, 2026)

Локальный набор документации по **T-Invest API** и официальному Python SDK.
Собран из официальных источников T-Bank (бывш. Tinkoff):

- Org / репозитории SDK: <https://opensource.tbank.ru/invest>
- Python SDK: <https://opensource.tbank.ru/invest/invest-python>
- Proto-контракты (источник правды по сервисам): <https://opensource.tbank.ru/invest/invest-contracts>
- Портал документации: <https://developer.tbank.ru/invest/intro/intro>

> Дата сборки: 2026-06-15. Актуальная версия Python SDK на момент сборки — **1.49.1** (2026-06-12).

## ⚠️ Главное, что изменилось при ребрендинге Tinkoff → T-Bank

| Было (старое) | Стало (актуальное) |
|---|---|
| Домен `invest-public-api.tinkoff.ru:443` | **`invest-public-api.tbank.ru:443`** |
| Sandbox `sandbox-invest-public-api.tinkoff.ru:443` | **`sandbox-invest-public-api.tbank.ru:443`** |
| Пакет PyPI `tinkoff-investments` | **`t-tech-investments`** (через приватный индекс, см. `sdk-python.md`) |
| Импорт `from tinkoff.invest import Client` | **`from t_tech.invest import Client`** |
| Портал `tinkoff.github.io` / `russianinvestments` | **`developer.tbank.ru/invest`** + GitLab `opensource.tbank.ru` |

> ⚠️ **Не ломать рабочий код.** Скрипты в `scripts/`, `lab/`, `daybot/` написаны под СТАРЫЙ
> SDK `tinkoff.invest` и боевой домен. Старый домен `*.tinkoff.ru` пока проксируется/работает.
> Миграция на `t_tech.invest` — отдельная задача (см. `migration-to-t-tech.md`), делать осознанно,
> не автоматической заменой строк.

## Карта документов

### Синтезированные гайды (этот проект)
- [`quickref.md`](quickref.md) — **одна страница**: контуры, конвертация денег/пунктов, лимиты, частые рецепты MCP, ретраи.
- [`sdk-python.md`](sdk-python.md) — установка, импорт, sync/async-клиент, сервисы, sandbox, стримы, кеш.
- [`api-services.md`](api-services.md) — каталог всех 8 сервисов API + полные списки методов из proto-контрактов.
- [`migration-to-t-tech.md`](migration-to-t-tech.md) — план перехода со старого `tinkoff.invest` на `t_tech.invest`.
- [`mcp-vs-api-coverage.md`](mcp-vs-api-coverage.md) — что доступно через MCP `t-invest`, а что только через SDK/REST (стримы, отчёты).
- [`errors-cheatsheet.md`](errors-cheatsheet.md) — частые коды ошибок, что ретраить, политика бэкоффа для ботов.

### Официальные документы
Эта публичная версия репозитория не вендорит зеркало `docs/official/` (лицензия апстрима
`opensource.tbank.ru/invest/invest-contracts` не подтверждена — избегаем редистрибуции без
явных прав). Читай их напрямую по ссылкам в шапке файла: лимиты, типы (`Quotation`/`MoneyValue`),
идентификация инструментов, коды ошибок, песочница, спецификации сервисов — всё там же.

Прокси-контракты нужны офлайн-оракулу `analysis/api_method_names_validate.py` — склонируй
`invest-contracts` и укажи `TINVEST_PROTO_DIR` (см. докстринг скрипта).

### Неофициальное
- [`gotchas.md`](gotchas.md) — подводные камни/баги/лайфхаки из community (не из официальных источников).

## SDK на других языках
Кроме Python, T-Bank публикует официальные SDK: **Java, Kotlin, Go, C#, JS**, плюс `invest-autofollow`
и `invest-contracts` (proto). Все — под <https://opensource.tbank.ru/invest>.

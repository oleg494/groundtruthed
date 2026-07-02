# tinvest-lab — шпаргалка для оператора/агента

Форвард-тест торговых стратегий на ПЕСОЧНИЦЕ T-Invest (виртуальные деньги,
реальные котировки MOEX). Боевого токена здесь НЕТ, рисков для реальных денег нет.

## Что где

| Что | Где |
|---|---|
| Код | `~/tinvest-lab/lab/` |
| Журнал (SQLite: trades/equity/events) | `~/tinvest-lab/lab/lab.db` — НЕ УДАЛЯТЬ, копится месяцами |
| Состояние стратегий | `~/tinvest-lab/lab/lab_state.json` |
| Sandbox-токен | `~/tinvest-lab/.env` (только песочница) |
| Сервис | `tinvest-lab.service` (systemd, Restart=always) |

## Команды

```bash
# текстовый отчёт: P&L, просадки, сделки по каждой стратегии
cd ~/tinvest-lab && python3 -m lab.report

# статус / логи / рестарт
systemctl status tinvest-lab
journalctl -u tinvest-lab -n 50 --no-pager
sudo systemctl restart tinvest-lab
```

## Стратегии (счёт на стратегию, по 100к виртуальных рублей)

С 2026-06-17 режим бенчмарков: ACTIVE=`buyhold`,`random`, остальные 4 (grid,
momentum, meanrev, gold_trend) архивированы после провала study (Deflated Sharpe 0%).

- `buyhold` — контроль: корзина нефть/газ/металлы (GAZP LKOH ROSN GMKN PLZL CHMF) куплена и держится. Это бенчмарк.
- `random` — контроль: случайные сделки. Любая стратегия обязана бить обе контрольные, иначе она мусор.

## Для cron-отчёта (пример)

```cron
# будни в 19:05 МСК после основной сессии — отчёт в файл/телеграм
5 19 * * 1-5 cd ~/tinvest-lab && python3 -m lab.report > /tmp/lab_report.txt 2>&1
```

## Известные особенности песочницы (не баги фермы)

- Фьючерсы: песочница кладёт в `totalAmountPortfolio` полный нотионал фьюча — честный
  equity = api_total − totalAmountFutures + P&L от средней (`lab/strategy.py`, `Ctx.equity()`).
- Нет дивидендов/купонов, комиссия всюду 0.05%, шорты фьючей закрыты.
- Sandbox-счета живут 3 месяца с последнего использования; лимит ~10 счетов на токен —
  не создавать счета сверх фермы.
- Лимитки в песочнице могут исполняться оптимистичнее реального стакана.

Подробности и спека: репозиторий пользователя `tinvest-project`
(docs/superpowers/specs/2026-06-12-strategy-lab-design.md).

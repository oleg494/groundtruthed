# CI-аудит бэктест-движка: результат

Дата: 2026-07-02. Источник чек-листа: `deep/backtest_engine_audit.md` (12 блоков).
Реализация: `tests/test_audit.py` — 35 pytest-тестов, офлайн, детерминированная синтетика.
Прогон: `py -m pytest tests/test_audit.py -v` → **32 PASS / 3 XFAIL** (весь набор
`tests/` после добавления: 155 passed, 3 xfailed).

Дополняет, НЕ дублирует существующие оракулы:
- `analysis/backtest_validate.py` — независимая реконструкция equity (SMACross/BuyHold
  с нуля + переигровка fills для всех 17 стратегий) — здесь эта же идея перенесена в CI
  как инвариант сохранения денег на случайном сценарии;
- `analysis/inference_validate.py` — калибровка/мощность PSR, DSR под мульти-тестом,
  мощность детектора lookahead — здесь только краевые тождества DSR, не калибровка;
- `tests/test_engine.py`, `tests/test_stops.py`, `tests/test_futures_margin.py`,
  `tests/test_robust.py` — базовые случаи, не повторяются.

## Блок → тесты → вердикт

| # | Блок отчёта | Тесты (`tests/test_audit.py`) | Вердикт |
|---|---|---|---|
| 1 | No-lookahead | `test_samebar_cheater_earns_nothing_in_honest_engine` (читер со знанием текущего бара не забирает внутрибарное движение: честный P&L < 35% от same-bar исполнения), `test_context_history_contains_no_future_bars` (Context отдаёт ровно прошлое+текущий бар) | **PASS** |
| 2 | Исполнение next-open | `test_order_on_last_bar_never_fills`, `test_market_fill_at_open_ignores_intrabar_move` (филл по open, внутрибарный обвал честно бьёт по equity) | **PASS** |
| 3 | Лимитные заявки | `test_limit_buy_fills_only_on_touch_and_at_limit`, `test_limit_gap_through_fills_at_open_not_worse` (гэп сквозь лимит → филл по open в нашу пользу), `test_limit_tif_expires_without_fill` | **PASS** (см. упрощения ниже: 100% fill при касании) |
| 4 | Стопы/тейки | `test_gap_through_stop_fills_at_open_not_stop_price` (гэп 100→70: выход по 68, не по стопу 95 — риск не занижен), `test_ambiguous_bar_stop_and_take_close_exactly_once` (бар задел и стоп, и тейк → ровно одно закрытие; исход инвариантен к порядку, т.к. выход всегда по open следующего бара), `test_short_stop_triggers_on_high_and_fills_next_open` | **PASS** |
| 5 | Шорты | `test_short_equity_accounting_on_rising_market` (знак P&L/учёт кэша корректны); `test_insufficient_funds_order_rejected` | **PASS + 1 XFAIL** (баг №1) |
| 6 | Фьючерсная маржа + деньги | `test_money_conservation_long_random_scenario[cash/futures]` (300 баров, 2 тикера, >100 случайных маркет/лимит-филлов обоих знаков: equity движка бит-в-бит с независимой переигровкой на каждом баре; итог = cash0 + Σrealized + unrealized − комиссии; комиссия на каждом филле); `test_futures_margin_call_liquidates_before_negative_equity`; `test_equity_uses_last_known_price_when_bar_missing` | **PASS + 2 XFAIL** (баги №2, №3) |
| 7 | Комиссии | `test_roundtrip_commission_charged_both_sides` (вход И выход), `test_commission_charged_on_stop_exit_too` (стоп-выход не бесплатный), `test_commission_stress_exact_degradation` (при фиксированном qty деградация equity РОВНО равна сумме комиссий — точный breakeven-оракул) | **PASS** |
| 8 | Slippage | `test_slippage_adverse_on_sell` (продажа дешевле — дополняет существующий buy-тест), `test_slippage_monotonic_degradation`, `test_limit_fill_has_no_slippage` (лимит пассивен — слиппедж не применяется, это корректно) | **PASS** |
| 9 | Ребаланс | `test_target_percent_never_overshoots_target` (округление к лоту вниз, цель не перебирается), `test_rebalance_sized_on_close_filled_at_next_open` (сайзинг по close(t), филл по open(t+1) — движок не переисполняет по цене решения) | **PASS** |
| 10 | Walk-forward | `test_walkforward_windows_anchored_and_disjoint` (anchored IS, OOS встык, без перекрытий), `test_walkforward_stitched_equity_is_oos_only` (сшитая кривая = только OOS-бары), `test_walkforward_no_leak_from_future_segments` (возмущение последнего сегмента не меняет best_params/IS-метрику/OOS-результат ранних окон — утечки будущего в подбор нет) | **PASS** |
| 11 | Monte Carlo | `test_montecarlo_deterministic_by_seed` (один сид → бит-в-бит, разные → разные), `test_montecarlo_degenerate_when_all_trades_equal` (одинаковые P&L → распределение схлопывается в точку, dd=0 — аналитический оракул), `test_montecarlo_percentiles_ordered_and_bounds` | **PASS** |
| 12 | DSR | `test_dsr_equals_psr_when_single_trial` (n_trials=1 → sr_star=0 → DSR≡PSR), `test_psr_penalizes_fat_tails_and_negative_skew` (ненормальность снижает уверенность — формула Bailey/López de Prado учтена), `test_psr_guard_on_tiny_sample` (n<2 → 0.5, не сертификация) | **PASS** |
| — | Детерминизм сидов | `test_synthetic_data_deterministic_by_seed`, `test_run_reproducible_bit_for_bit` (двойной прогон движка бит-в-бит, включая RandomTrader с сидом) | **PASS** |

## Вскрытые баги (XFAIL, strict=True — движок НЕ правился, его владеет соседний воркфлоу)

1. **Нет проверки достаточности средств / маржи** (блок 5,
   `test_insufficient_funds_order_rejected`). `Broker._apply_fill` списывает нотионал
   без каких-либо ограничений: покупка на 1000× кэша исполняется, кэш уходит в −99 900;
   шорт неограничен теми же силами. Стратегия с плечом-по-неосторожности (например,
   баг сайзинга) даст в бэктесте «результат», нереализуемый на счёте.
2. **Нет maintenance margin / margin call для фьючерсов** (блок 6,
   `test_futures_margin_call_liquidates_before_negative_equity`). Позиция переживает
   просадку до отрицательного equity без принудительного закрытия. Частично осознанная
   упрощёнка (docstring `core.py` декларирует кэш-модель), но margin call там не
   упомянут; просадки фьючерсных стратегий в бэктесте занижены против реальности.
3. **`Broker.equity` теряет позицию при дыре в ленте тикера** (инвариант учёта,
   `test_equity_uses_last_known_price_when_bar_missing`). Если у тикера нет бара на
   текущей метке объединённой ленты, стоимость его позиции просто исчезает из equity
   на этот бар (ложный провал кривой → фиктивная волатильность/просадка в метриках).
   Ожидаемое поведение — оценка по последней известной цене. На одиночных лентах
   и выровненном `basket()` не проявляется; опасно для мульти-тикерных стратегий
   на реальных свечах с несинхронными пропусками (pairs и т.п.).

## Осознанные упрощения (не баги; учитывать при интерпретации результатов)

- **Лимитки: 100% fill при касании** уровня баром и по цене ровно лимита — нет очереди,
  объёма стакана и adverse selection (отчёт, блок 3). Для стратегий, живущих на пассивных
  лимитках, результат оптимистичен; текущие стратегии фермы — маркет-ордера.
- **Шорты без borrow fee и дивидендов** (блок 5) — для MOEX-фьючерсов (основной шорт-
  инструмент проекта) нерелевантно, для шорта акций P&L будет завышен.
- **Monte Carlo — IID-бутстрэп** (блок 11): `montecarlo.py` ресэмплит сделки/доходности
  с возвращением, block-bootstrap нет → автокорреляция и кластеризация волатильности
  игнорируются, хвост просадок может быть занижен для тренд-following.
- **Walk-forward без purge/embargo** (блок 10): окна встык. Для bar-close сигналов с
  исполнением next-open и стратегий без обучаемых меток это приемлемо (утечки нет —
  подтверждено тестом возмущения), но при появлении признаков с длинным горизонтом
  метки embargo понадобится.

## Как гонять

```bash
py -m pytest tests/test_audit.py -v      # только аудит (32 PASS / 3 XFAIL)
py -m pytest tests/ -q                   # весь набор (155 passed, 3 xfailed)
```

XFAIL — strict: если соседний воркфлоу починит баг, тест начнёт падать как XPASS —
сигнал убрать маркер и перевести строку отчёта в PASS.

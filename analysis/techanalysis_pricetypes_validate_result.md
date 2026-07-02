# Тех-анализ: typeOfPrice и интервалы (дополнение)

**Дата:** 2026-06-19 · `analysis/techanalysis_pricetypes_validate.py` · READ-ONLY · SBER, SMA20

Дополняет `techanalysis_validate` (где вскрыты RSI=Wilder, BB=population, EMA seed-инвариант).

## Результат — всё бит-в-бит
- **typeOfPrice** на дневном интервале: CLOSE/OPEN/HIGH/LOW/AVG — каждый **154/154**. Подтверждено:
  индикатор берёт соответствующее поле свечи, а **AVG = (open+high+low+close)/4**.
- **Недельный интервал** (INDICATOR_INTERVAL_WEEK): SMA20 по недельным closes — **24/24**, т.е.
  недельный индикатор считается по недельным свечам (которые сами = бит-в-бит свёртка дневных,
  см. `candle_aggregate_validate`).

## Вывод
Вместе с `techanalysis_validate` это закрывает реверс `GetTechAnalysis` полностью: тип индикатора,
длина, тип цены (вкл. формулу AVG) и интервал — все параметры воспроизводятся локально бит-в-бит.

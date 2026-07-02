# ORB на срочном рынке MOEX: доказательная база, модификации и параметры для MX/SBER/GOLD

## Section 0 — Evidence Boundaries: What This Report Can and Cannot Claim

**Critical gap.** Targeted searches across Smart-Lab, elibrary.ru, academic databases, and Russian-language broker research returned zero published ORB backtests specific to MOEX futures with numerical performance metrics (win rate, profit factor, Sharpe ratio). This absence is confirmed by multiple independent search passes and is not an artifact of search methodology. No MOEX-specific ORB research exists in publicly accessible sources as of June 2026.

**What does exist:**
- Two peer-reviewed academic ORB studies on non-MOEX markets: Holmberg & Lönnbark (2013, *Finance Research Letters*) on WTI crude oil futures [1], and the Timely ORB (TORB) study published in *IEEE Access* (2019) testing DJIA, S&P 500, NASDAQ, HSI, and TAIEX index futures [2][3].
- MOEX structural data: trading hours, commission schedule, and opening auction mechanics [4][5][6].
- Indirect evidence: a TradingView backtest of unfiltered ORB on RTS futures (profit factor 0.98, losing), Fondograf's test of 40+ mechanical strategies on 5-year MOEX data (nearly all failed out-of-sample), and large-sample US/global ORB statistics from orbsetups.com.

**Consequence for Sections 3–4.** All instrument-specific parameters for MX/SBER/GOLD are analytical extrapolations from international data, not measured MOEX results. They are labeled throughout as *analytical estimate* and require validation on actual MOEX tick data before deployment.

---

## Section 1 — Why Classic ORB on MOEX Has Structurally Negative Expectancy

**Evidence level: partly MOEX-specific (structural), partly transferred from international data.**

### The opening auction changes the baseline

MOEX explicitly designed its derivatives opening auction (8:50–9:00 MSK) to "form a representative opening price and reduce the negative impact of rising volatility at market open" [5][7]. The auction collects limit and iceberg orders for ~10 minutes, then executes a single clearing price that maximizes matched volume. This means the 10:00 MSK main session open is not a cold start — it is a continuation after a price-discovery step. The practical consequence: the first candle at 10:00 carries less raw information than the NYSE open at 9:30 ET, where no pre-open auction exists for most instruments. A Smart-Lab backtest on the MOEX index found that removing the first candle from ORB calculations caused strategy performance to deteriorate 2–3× — the opposite of what you'd want if the first candle were noise.

### Four noise sources compounding at 10:00 MSK

- **Overnight position transfer from the evening session (19:00–23:50).** Participants who held through the night close positions at the main session open, creating directional pressure that has nothing to do with the day's fundamental direction.
- **CME and Asian market reaction.** For GOLD, BR, and MX futures, the 10:00 MSK open incorporates 8–9 hours of CME and Asian price movement. The gap between the previous evening close and the 10:00 open is not random — it is a compressed catch-up. This produces an initial directional move that often exhausts itself within 15–30 minutes, then reverses, triggering stop-outs on breakout entries.
- **Low liquidity at 10:00.** Market makers widen spreads at session open until order flow stabilizes. On MOEX futures, this increases effective slippage for market orders, which is the default entry mechanism in classic ORB.
- **Trend days are rare.** Analysis of open-drive patterns suggests trend days — the condition under which ORB produces its best results — occur in fewer than 10% of sessions. On the remaining 90%+, price revisits both sides of the opening range, generating double-break whipsaws.

### The whipsaw problem quantified (non-MOEX, transferable direction)

On ES futures (15-minute ORB, 6-month sample), price tagged both the ORB high and ORB low in the same session 66.93% of the time, with clean directional breakouts occurring only 16.92% up and 16.15% down. A large-sample analysis of 240,102 ORB trades across 600+ symbols found 65.9% of breakouts hit stop before reaching target with default settings. These figures are US equity market data, not MOEX — but the mechanism (mean reversion dominance on most days) applies wherever liquidity is adequate. MOEX's opening auction may partially reduce this, but it does not eliminate it.

### Commission math for MOEX futures

MOEX taker commissions for futures are contract-type specific: index contracts (MX/RTS) 0.00660%, equity contracts (SBER) 0.01980%, commodity contracts (GOLD) 0.01320% [6]. Maker orders are 0%. For a round trip (entry + exit both as taker), the commission drag is 0.013% for MX, 0.040% for SBER, and 0.026% for GOLD. At a 1:1.5 R/R, a breakeven win rate before commission is 40%; after adding 0.026–0.040% round-trip cost on a typical MOEX futures contract value, the required win rate rises by 1–3 percentage points depending on the instrument and stop size. This is manageable — the futures commission structure is substantially more favorable than the ~0.10% round-trip on MOEX stocks [8]. The real problem is not commission per se but the combination of low trend-day frequency, slippage on market orders, and the fact that unfiltered ORB win rates cluster around 34–38%.

### Comparison: MOEX opening structure vs. major Western markets

| Feature | MOEX Futures (post-ЕТС, 2026) | CME E-mini (S&P/NQ) | Euronext (CAC/DAX) |
|---|---|---|---|
| Pre-open auction | Yes, 8:50–9:00 MSK [5] | Globex pre-open (no price discovery auction) | Yes, call auction |
| Main session open | 10:00 MSK [4] | 9:30 ET | 9:00 CET |
| Overnight session | Yes, 19:00–23:50 MSK [4] | Globex nearly 24h | Limited |
| Intraday clearing breaks | Eliminated (ЕТС, March 2026) [4] | None | None |
| First-candle noise driver | Overnight carry + CME/Asia gap | Pre-market flow | European open |
| Classic ORB range (30 min) | Unvalidated on MOEX | ~40–60% win rate (filtered) | Unvalidated |

---

## Section 2 — International Academic Base: What Transfers to MOEX

**Evidence level: sourced from peer-reviewed studies; transferability is an analytical inference.**

### Holmberg & Lönnbark (2013)

Published in *Finance Research Letters*, this study tested intraday ORB on WTI crude oil futures [1]. The key finding: ORB profitability depends heavily on the specific entry and exit rules, and the strategy lacks robustness across different parameter settings [9]. This is relevant to MOEX not because the instrument matches (it doesn't — crude oil is different from equity index or gold futures) but because it establishes the general fragility of ORB to parameter choice. A strategy that barely works on WTI with optimized parameters will fail out-of-sample when those parameters are misapplied to a different market.

### TORB study (IEEE Access, 2019) — the most relevant academic reference

The Timely ORB study tested five index futures markets (DJIA, S&P 500, NASDAQ, HSI, TAIEX) over 2003–2013 [2][3]. Key findings:

- All five markets achieved statistically significant positive returns (p < 3%) net of transaction costs [2].
- Annual returns: TAIEX 20.28%, NASDAQ 17.51%, S&P 500 / DJIA / HSI all above 8% [2].
- The critical variable is the *probe time* — how long after the open the range is defined. US markets: 1 minute (S&P 500, NASDAQ) or 4 minutes (DJIA). Asian markets: 37 minutes (TAIEX) and 151 minutes (HSI) [2].
- The TORB paper uses time-based exits (close position at end of active trading hours) with no explicit stop-loss price levels [2].

**Why the Asian market results matter more for MOEX than the US results.** The 1–4 minute optimal probe times for US markets reflect the fact that CME index futures have continuous overnight liquidity and the 9:30 ET open is a hard transition from pre-market to full liquidity. The opening range is informative almost immediately. MOEX, like HSI and TAIEX, has an overnight session that feeds into the morning open, a pre-open auction that pre-digests some of the gap, and lower overall liquidity depth at the open relative to the US. These characteristics are structurally closer to Asian markets. The 37–151 minute optimal range for Asian markets suggests that on MOEX, a 15–30 minute range is likely too short — a range defined over 45–90 minutes is probably closer to the structural optimum. *This is an analytical inference, not a measured MOEX result.*

### What the MOEX School's false-breakout course signals

MOEX's official education platform offers a dedicated course on the "false breakout" strategy [10]. The exchange does not offer this course for markets where false breakouts are rare. This is indirect but consistent evidence that false breakouts at the MOEX open are frequent enough to be commercially viable as a trading strategy — which is the mirror image of why classic directional ORB underperforms.

---

## Section 3 — ORB Modifications: Three Filters That Change the Math

**Evidence level: international data sourced; MOEX-specific application is analytical.**

### 3a. ATR and volatility-width filters

**The core mechanism.** Classic ORB enters on any breakout of the opening range regardless of range width. This is the primary source of false breakouts: a narrow range (low volatility morning) produces a breakout that is trivially exceeded by normal price noise, while a very wide range (gap day, news shock) produces an entry with unfavorable R/R because the stop is far away.

A large-sample analysis of 240,102 ORB trades found that tight opening ranges (below \$0.50 for US equities) produced a 51.0% win rate vs. 34.6% for very wide ranges (above \$2.00) — a 16.4 percentage point spread from this single variable. A volatility-regime filter using VIX showed 58% ORB win rate when VIX was in the 16–24 range vs. 48% when VIX was below 13, based on 47,000 setups. These figures are US equity market data and cannot be applied directly to MOEX, but the directional effect is mechanistically sound: moderate volatility produces ranges that are wide enough to filter noise but not so wide that R/R degrades.

**MOEX-specific application (analytical estimate).** For MX (IMOEX futures), the practical filter is: enter only if the 10:00–11:00 range width falls between 0.5× and 1.5× of the 20-day ATR. Below 0.5× ATR: the range is too narrow, breakout is noise. Above 1.5× ATR: the range reflects a gap or news shock — stop distance is too large for a standard 1:2 R/R setup. The RTSVX index (Russia's VIX equivalent) can serve as a regime filter: avoid ORB entries when RTSVX is below its 20-day moving average (low-volatility, mean-reverting regime). For GOLD futures, the CME overnight ATR (measured from CME close to MOEX 10:00 open) is the relevant volatility input, not the MOEX 20-day ATR alone.

*All threshold values above are illustrative estimates pending MOEX-specific backtest validation.*

### 3b. Exit management: time-based vs. trailing stop vs. clearing

The ЕТС implementation on March 23, 2026 eliminated the intraday clearing breaks at 14:00–14:05 and 18:45–19:05 MSK [4]. Pre-ЕТС, these were natural forced-exit points that many MOEX strategies used as default closes. Post-ЕТС, any strategy coded with "exit at 14:00 clearing" now holds the position through what was previously a clearing window.

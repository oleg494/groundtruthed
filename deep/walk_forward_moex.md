# Walk-Forward Analysis for MOEX: Practical Methodology Against a Short, Broken History

## The Core Constraint That Shapes Every Decision Below

MOEX's usable clean history is shorter than it looks. IMOEX has daily data from September 22, 1997 [1], but the February 24 – March 23, 2022 trading suspension (~20 trading days, the longest closure since the Soviet Union's collapse [2]) is not just a data gap — it is a regime break. Volatility, liquidity, and participant composition changed on both sides of it. As of mid-2026, the clean post-suspension history is roughly 3.5 years. The perpetual futures available for intraday work are even shorter: GLDRUBF launched July 2023, IMOEXF November 2023, SBERF October 1, 2024 [3]. Every section below is shaped by this constraint.

---

## 1. IS/OOS Window Sizing: Concrete Bars and Months for MOEX

### Standard methodology

The classical Pardo IS:OOS ratio is 3:1 to 4:1, meaning 20–25% of the combined window is OOS. TradeStation implements 20% OOS as its default, with an acceptable range of 10–25% [4]. The walk-forward framework validated in recent academic work uses a 252-day training window with a 63-day test window and 63-day step size — a 4:1 ratio — across 34 independent folds [5]. The logic: OOS must be long enough to contain several independent trading opportunities but short enough that parameter relevance doesn't decay before the OOS period ends.

**Rolling vs anchored.** Rolling (fixed-length IS window that slides forward) adapts faster to regime changes but discards early data at each step. Anchored (IS always starts at the same origin, OOS steps forward) maximizes data use but risks diluting recent regime signal with distant history. Neither is universally superior.

### MOEX-specific sizing

**Daily strategies.** The table below gives three scenarios. "Post-2022 only" uses clean data from March 24, 2022; "2019–2026 with break marker" uses the longer history but tags the February–March 2022 boundary explicitly as a regime discontinuity so no IS or OOS window straddles it unmarked.

| Scenario | IS length | OOS length | Step | WFA steps to mid-2026 | Anchored vs Rolling |
|---|---|---|---|---|---|
| Post-2022 only | 12 months (~252 days) | 3 months (~63 days) | 3 months | ~10 steps | **Anchored preferred** |
| Post-2022 only | 18 months (~378 days) | 6 months (~126 days) | 6 months | ~4 steps | Anchored preferred |
| 2019–2026 + break marker | 18 months | 6 months | 6 months | ~8 steps | Either viable |
| Intraday 15-min (post-2022) | 3–6 months (~3,000–6,500 bars) | 1–2 months (~1,000–2,200 bars) | 1 month | Monthly re-opt | Rolling acceptable |

**Intraday bar counts.** MOEX's main equity/derivatives session runs 10:00–18:50 plus an evening session 19:05–23:50 [6], totaling roughly 13 trading hours = ~52 bars/day at 15 minutes. For 5-minute bars that is ~156 bars/day. IS of 3 months ≈ 3,000 bars (15-min) or ~9,000 bars (5-min); OOS of 1 month ≈ 1,000 bars (15-min) or ~3,000 bars (5-min).

**Critical caveat on the evening session.** The MOEX equity market evening session started September 12, 2022. Data before that date contains no evening bars. Any IS window that spans pre- and post-September 2022 will mix bar structures. Either exclude the evening session from all windows for consistency, or test the two session types separately.

**Re-optimization frequency.** For daily swing strategies, quarterly re-optimization (4×/year) is the practical default: frequent enough to track the CBR rate cycle (which changes several times a year), infrequent enough to avoid transaction costs from constant parameter churn. For 15-minute intraday strategies, monthly re-optimization is appropriate given faster regime turnover.

**Anchored vs rolling for MOEX.** With post-2022 history only and IS = 18 months, a rolling window would consume the entire available history in the first IS window, leaving no room to step forward. Anchored is the only viable option in that scenario. When using 2019–2026 data with the break marker, rolling becomes feasible but still risks losing the 2019–2021 low-volatility data that may not be representative of the current regime. Practical recommendation: use anchored for daily strategies; use rolling for intraday where the recent microstructure is what matters.

---

## 2. Handling the February 2022 Break and Other Gaps

### The nature of the break

MOEX suspended all markets on February 24, 2022 and resumed limited trading on March 24, 2022 — 28 calendar days, approximately 20 trading days [2]. This is not a missing-data problem. Before the closure: foreign institutional participation, USD/EUR-denominated pricing, pre-sanction liquidity. After: predominantly domestic retail and institutional flow, capital controls, ruble-denominated pricing, structurally higher volatility. The before-and-after distributions of daily returns, spreads, and intraday patterns are different enough that a model fitted on pre-2022 data will have systematically different OOS behavior on post-2022 data.

### Three approaches and when to use each

**Approach 1 — Drop pre-2022 data entirely.** Use only March 24, 2022 onward. Gives the cleanest regime consistency. The cost is ~3.5 years of daily data, which yields only 2–4 WFA steps at standard IS/OOS ratios. Use this for strategies whose edge is explicitly tied to post-sanction microstructure: high-frequency ORB on 5-minute bars, strategies that depend on current bid-ask spreads, or anything that uses GLDRUBF/IMOEXF/SBERF (which have no pre-suspension history anyway).

**Approach 2 — Stitch with a regime marker.** Use the full 2019–2026 history but add a binary variable (0 = pre-March 2022, 1 = post-March 2022) and compute volatility normalizations separately for each regime. Crucially, the February 24 – March 23, 2022 gap must never fall inside an IS or OOS window — it must always be a boundary. If an IS window would otherwise straddle the gap, either truncate it at February 23 or start it at March 24. This approach gives 6–8 WFA steps on daily data and is the practical default for IMOEX and SBER swing strategies.

**Approach 3 — Per-regime WFA.** Run separate WFA passes on pre-2022 data and post-2022 data, then compare parameter stability across regimes. If the same parameter set performs well in both regimes, confidence in robustness is higher. If parameters diverge sharply, the strategy is regime-dependent and must be deployed with an explicit regime switch. The cost: even fewer observations per pass, making statistical tests weaker.

### Instrument-specific notes

- **IMOEX / SBER**: Approach 2 is viable; IMOEX has daily data from 1997 [1], SBER has multi-year history.
- **BR (Brent futures)**: History is longer, but the Urals-Brent discount that emerged post-February 2022 creates a pricing regime shift. BR futures on MOEX now partially reflect domestic pricing dynamics. Treat March 2022 as a regime boundary here too.
- **GLDRUBF, IMOEXF, SBERF**: Launched July 2023, November 2023, October 2024 respectively [3]. With under 3 years of history as of mid-2026, classical WFA with 4+ OOS steps is not feasible. The only honest approach is a preliminary IS fit followed by live forward monitoring. Do not call a backtest on 18 months of history a validated WFA.

### Other gaps to mark

MOEX did not halt during COVID-19 (March 2020), but daily ATR spiked several standard deviations. Mark any day where ATR > 3σ of the trailing 60-day ATR as a "stress day." Then check whether the strategy's OOS profit is concentrated in those days — if it is, the edge may not generalize to normal conditions.

---

## 3. OOS Robustness Metrics: Concrete Pass/Fail Thresholds

### Walk-Forward Efficiency (WFE)

WFE = (annualized OOS return or Sharpe) / (annualized IS return or Sharpe). TradeStation documents WFE ≥ 50% as the threshold for a successful walk-forward test [7]. This is explicitly described as a rule of thumb, not a hard statistical bound. Interpretation tiers:

- WFE 50–80%: normal IS→OOS degradation, acceptable
- WFE > 100%: suspicious — IS optimization was probably too loose, or OOS period happened to be unusually favorable
- WFE 30–50%: marginal — strategy may be over-fit; investigate which OOS steps drove the shortfall
- WFE < 30%: stop — the strategy is not surviving out-of-sample

**MOEX caveat.** With only 2–4 OOS steps, the aggregate WFE is dominated by 1–2 steps and is statistically noisy. Supplement with the median WFE across steps and the fraction of profitable OOS steps.

### Sharpe degradation IS→OOS

A useful empirical benchmark: across quantitative strategies, OOS Sharpe ratios deteriorate by roughly one-third to one-half compared to IS. Acceptable degradation: OOS Sharpe ≥ 50% of IS Sharpe. Hard floor: OOS annualized Sharpe (after costs) ≥ 0.5. An IS Sharpe above 3.0 with fewer than 200 trades is almost always a sign of over-fit, particularly on short MOEX intraday data.

### Complete pass/fail checklist

| Metric | Minimum threshold | Green zone | Red flag |
|---|---|---|---|
| WFE | ≥ 50% [7] | 60–80% | < 30% |
| OOS annualized Sharpe (after costs) | ≥ 0.5 | ≥ 1.0 | < 0 |
| IS→OOS Sharpe degradation | ≤ 50% | ≤ 30% | > 70% |
| Fraction of profitable OOS steps (≥4 steps) | ≥ 60% | ≥ 75% | < 50% |
| OOS max drawdown vs IS max drawdown | ≤ 1.5× | ≤ 1.2× | > 2× |
| Profit concentration in single OOS step | No single step > 60% of total OOS profit | Uniform | > 80% in 1 step |
| Minimum trades per OOS step [4] | ≥ 30 | ≥ 100 | < 20 |
| Minimum total OOS trades across all steps [4] | ≥ 300 | ≥ 500 | < 100 |

**Probabilistic Sharpe Ratio (PSR)** as a complement: PSR estimates the probability that the true Sharpe exceeds a benchmark (e.g., 0), correcting for sample length and distributional moments. It is the single-strategy version of the DSR concept (see Section 4) and can be computed without specifying the number of trials N. Use PSR ≥ 0.95 as an additional gate alongside WFE.

---

## 4. Multiple Testing and Data Snooping: What Actually Works on MOEX's Short History

### The problem is worse than it looks

Data snooping occurs whenever the same data is used more than once for inference or model selection [8]. This includes not just explicit parameter grid search but also the choice of instrument (SBER vs GLDRUBF), timeframe (15-min vs daily), and strategy type (ORB vs mean-reversion) — if those choices were made after looking at the data. The number of independent trials N in the DSR formula should count all configurations examined, not just the final one submitted.

### White's Reality Check

White (2000) tests the null hypothesis that no strategy in a set of N candidates beats a benchmark, using stationary bootstrap with 500–1,000 resamples [8]. The bootstrap requires specifying smoothing parameter q (0 < q ≤ 1): smaller q means longer block lengths for more autocorrelated data; q = 1 is appropriate for martingale-difference sequences. The asymptotic validity conditions require both the number of OOS observations (n) and the initial estimation period (R) to approach infinity, with n/R → 0 [8].

**MOEX applicability.** With 2–3 OOS steps of 3–6 months each, the total OOS observation count may fall well below the hundreds needed for asymptotic validity. White's RC on MOEX data is indicative, not statistically rigorous. Use it as a directional check, not a formal gate.

### Hansen's SPA Test

SPA improves on White's RC by re-centering the bootstrap distribution to exclude clearly inferior strategies, which reduces the conservatism of the null and gives higher power [9]. For a small comparison set (5–20 strategy variants, typical for MOEX work), SPA is preferable to White's RC. The same asymptotic-sample-size limitation applies — the original papers do not specify hard minimum sample sizes, and any T ≥ 500 or T ≥ 750 thresholds in practitioner literature are heuristics, not primary-source requirements.

### Deflated Sharpe Ratio — the most actionable tool for MOEX

The DSR [10] is the most practical tool for limited-history markets because it operates at the level of a single strategy and explicitly penalizes short track records through the √(T−1) term:

$$\text{DSR} = \Phi\!\left[\frac{(\hat{SR} - SR^*)\,\sqrt{T-1}}{\sqrt{1 - \hat{\gamma}_3\,\hat{SR} + \tfrac{\hat{\gamma}_4 - 1}{4}\,\hat{SR}^2}}\right]$$

where T is the number of observations, γ̂₃ is skewness, γ̂₄ is kurtosis, and SR\* is the expected maximum Sharpe across N independent trials (the benchmark that accounts for multiple testing) [10]. DSR ≥ 0.95 → 95% confidence the true Sharpe exceeds SR\*; DSR ≥ 0.90 → 90% confidence [10].

**What the formula implies for MOEX.** With N = 88 independent trials and an annualized SR of 2.5 over 5 years (T = 1,250 daily observations), DSR reaches only 0.90 — failing the 95% threshold [10]. On MOEX's post-2022 history (~880 daily bars as of mid-2026), a strategy with SR = 1.5 and N = 20 trials will struggle to clear DSR ≥ 0.95. The practical implication: keep N small by using Bayesian optimization or sequential search rather than full grid search, and document every configuration tested.

### Minimum Track Record Length (MinTRL)

The MinTRL formula from Bailey & López de Prado [11]:

$$\text{MinTRL} = 1 + \left(1 - \hat{\gamma}_3\,\hat{SR} + \frac{\hat{\gamma}_4 - 1}{4}\,\hat{SR}^2\right)\!\left(\frac{Z_\alpha}{\hat{SR} - SR^*}\right)^2$$

This is not a fixed constant — it depends on the observed SR, skewness, kurtosis, confidence level α, and the benchmark SR\* (which in turn depends on N). For a single trial (N = 1), SR\* = 0, α = 0.05: an annualized SR of 2.0 requires only 0.69 years of daily data; SR = 1.5 vs SR\* = 0 requires 1.21 years [12]. But with multiple trials, SR\* rises — with N = 10 trials, SR\* is roughly 0.5–1.0 depending on the distribution, pushing MinTRL up substantially.

**Illustrative calculation (not a validated risk estimate).** Inputs assumed: SR̂ = 1.0, SR\* = 0.5 (N ≈ 10 trials), γ̂₃ = −0.5, γ̂₄ = 4.0, α = 0.05. Under these inputs, MinTRL ≈ 24–30 months of daily observations. MOEX's post-2022 daily history (~42 months as of mid-2026) can satisfy this — but only barely, and only if N is kept below ~10. Changing SR\* upward (more trials) or SR̂ downward (weaker strategy) moves the required length well beyond available history.

### Practical hierarchy for MOEX

1. **DSR** as the primary gate — computable on a single strategy, explicitly penalizes short T and high N.
2. **MinTRL** as a pre-check — before running WFA, verify that available history satisfies MinTRL under realistic N and SR assumptions.
3. **SPA / White's RC** as a supplementary check when ≥ 5 OOS steps are available and the total OOS observation count exceeds a few hundred.

---

## 5. Embedding Regime Filters in WFA: Fix Thresholds, Don't Optimize Them

### Why joint optimization introduces look-ahead bias

If the Hurst threshold (e.g., H < 0.5 → use mean-reversion strategy) is optimized together with strategy parameters inside each IS window, the optimizer finds the threshold that best separates profitable and unprofitable periods in that IS window retrospectively. In the OOS period, that threshold has no reason to maintain the same separation — the optimizer has effectively used future information to calibrate which past periods were "good." The result is a filter that looks precise in IS and degrades badly in OOS.

This is distinct from but related to the overfitting problem: even without strict look-ahead bias, optimizing a regime threshold that triggers only 10–15 times over a multi-year IS window gives the optimizer very few degrees of freedom to work with, making any fitted threshold statistically meaningless.

### Correct architecture: fix thresholds, optimize lookback

**Step 1.** Fix regime filter thresholds before WFA begins, using literature values or pre-WFA calibration on data that will not be part of any IS or OOS window.

**Step 2.** In each IS window, optimize only the strategy parameters: ORB window length, ATR multiplier, Donchian period, stop-loss distance, etc.

**Step 3.** In OOS, apply the fixed regime thresholds plus the IS-optimized strategy parameters. The regime filter acts as a binary gate (trade / don't trade), not a tunable parameter.

### Fixed threshold values from literature

**Hurst exponent — R/S analysis** [13]: H < 0.56 → mean-reverting, 0.56 ≤ H ≤ 0.64 → random walk, H > 0.64 → trending.

**Hurst exponent — DFA (preferred for financial time series)** [13]: H < 0.42 → mean-reverting, 0.42 ≤ H ≤ 0.58 → random walk, H > 0.58 → trending. DFA thresholds are more conservative because DFA is less biased on short samples.

**ADX (Wilder):** ADX > 25 → trending market, ADX < 20 → sideways. The ADX = 25 threshold is an empirical convention, not a theoretically derived boundary, which makes it more market-dependent than Hurst. On MOEX, where carry-driven trends under high CBR rates can be persistent, the ADX threshold may need to be raised to 30 to avoid false trend signals — but this adjustment should be made once, pre-WFA, on the full available history, not re-fit in each IS window.

**Hurst stability on intraday data.** The Hurst exponent is unreliable on windows shorter than ~500 bars. On 15-minute MOEX data, use a rolling window of ≥ 500 bars (~10 trading days) for Hurst calculation. Different estimators (R/S, DFA, Whittle) give different numerical values on the same data — pick one and use it consistently throughout the WFA.

### Permissible two-level architecture

An annual outer-loop review of regime thresholds is acceptable: once per year, recalibrate the Hurst and ADX thresholds on the full history available at that point, then lock them for the next year's WFA inner loop. The critical rule: this recalibration must happen before the next OOS period starts, not after inspecting OOS results.

---

## 6. Minimum Trade Count for Statistical Significance on Intraday MOEX

### The tiered floor from literature

The statistical floor from the Central Limit Theorem is 30 trades per OOS window [4] — below this, t-tests and confidence intervals are not valid. One hundred trades provides limited but usable reliability for Sharpe estimation. Two hundred to 500 trades across multiple market regimes is the range required for institutional-grade confidence, particularly when applying DSR with multiple-testing correction.

These tiers are not arbitrary. The MinTRL formula shows that the required observation count scales with (Z_α / (SR̂ − SR\*))² — meaning a weaker strategy edge (small SR̂ − SR\*) demands exponentially more trades. A strategy with a genuine annualized SR of 1.5 needs far fewer observations to confirm than one with SR of 0.5.

### Trade frequency by strategy type on MOEX

| Strategy type | Typical frequency | Trades/year | Trades to reach 100 | Trades to reach 300 |
|---|---|---|---|---|
| ORB intraday (1 trade/day) | Daily | ~252 | ~5 months | ~14 months |
| Swing (2–3 trades/week) | Weekly | ~120–150 | ~8–10 months | ~24–30 months |
| Mean-reversion with filter (rare entries) | < 1/week | < 50 | > 2 years | > 6 years |
| Intraday mean-reversion (multiple/day) | 3–5/day | ~600–1,250 | ~1–2 months | ~3–6 months |

**Instrument-specific minimum OOS periods for 100 trades:**

| Strategy type | IMOEX/SBER (liquid) | BR futures | GLDRUBF | IMOEXF | SBERF |
|---|---|---|---|---|---|
| ORB (1/day) | 5 months | 5 months | 5 months (from Jul 2023) | 5 months (from Nov 2023) | 5 months (from Oct 2024) |
| Swing (2–3/week) | 8–10 months | 8–10 months | History may be exhausted | History may be exhausted | **Insufficient history** |
| Mean-reversion with filter | > 2 years | > 2 years | **Insufficient history** | **Insufficient history** | **Insufficient history** |

For GLDRUBF, IMOEXF, and SBERF: a swing strategy generating 2 trades/month produces 20–40 total trades over the entire available history. Any statistical significance claim on these instruments with low-frequency strategies is preliminary at best. The only valid path is to accumulate live forward data while treating the backtest as a prior, not a validated result.

### Applying MinTRL to trade counts

The MinTRL formula produces a number of time-series observations (e.g., daily returns), not a trade count. To convert: if a strategy generates k trades per day, MinTRL days × k = minimum trade count. For an ORB strategy with 1 trade/day, MinTRL of 18 months = ~378 trades — achievable on MOEX post-2022 data. For a swing strategy with 3 trades/week, the same MinTRL = ~234 trades — marginal but achievable over 18 months. For a mean-reversion strategy with 1 trade/week, MinTRL = ~78 trades — insufficient for the 18-month observation window, so the strategy requires either a longer history or aggregation across multiple instruments to reach significance.

---

## 7. CBR Rate Regimes as an External Regime Variable

CBR key rate data is available from September 17, 2013 [14]. The relevant regime sequence: gradual decline 2015–2021 (from 17% down to 4.25%), emergency hike to 20% on February 28, 2022, normalization to 7.5% by late 2022, then a new hiking cycle to 21% by late 2024.

The mechanism: when the CBR rate exceeds ~15%, carry-driven positioning dominates directional flows in currency and commodity futures (BR, GLDRUBF). Trend-following strategies on those instruments tend to have higher Sharpe in high-rate regimes because carry flows are persistent. Mean-reversion strategies on equity futures (IMOEXF, SBERF) tend to underperform in high-rate regimes because discount-rate pressure creates sustained directional drift rather than oscillation.

**How to use this in WFA.** Add the CBR rate level (or its 3-month change) as an external regime variable — not as an optimizable parameter, but as a condition for selecting between a trend-following and a mean-reversion parameter set at each quarterly re-optimization. If the CBR rate is above a fixed threshold (e.g., 10%), deploy the trend-following parameter set; below it, deploy mean-reversion. The threshold is fixed pre-WFA; what gets re-optimized each quarter is the strategy parameters conditional on each regime. This is not a high-frequency filter — CBR rate changes occur a few times per year, making it appropriate only as a quarterly or semi-annual context switch.

---

## 8. Operational WFA Protocol: Step-by-Step Checklist

**Data preparation**
1. Obtain full MOEX history via ISS API [15] or licensed vendor. Verify bar counts against known session hours [6].
2. Tag all regime boundaries as hard splits: February 23, 2022 (pre-suspension end), March 24, 2022 (post-suspension start), September 12, 2022 (evening session start), July 31, 2023 (T+1 settlement).
3. Tag stress days: any day where ATR > 3σ of trailing 60-day ATR. Do not remove them from data; mark them for post-hoc concentration analysis.
4. For intraday data: strip the evening session from all IS windows that predate September 12, 2022, or test with and without evening session separately.

**Pre-WFA setup**
5. Fix regime filter thresholds (Hurst DFA: H < 0.42 / H > 0.58; ADX: 25) using the full pre-WFA dataset. Do not revisit these during the WFA loop.
6. Set IS/OOS ratio: 80/20 or 75/25 [4]. For post-2022-only daily data, use IS = 12 months, OOS = 3 months, anchored. For 2019–2026 data with break markers, IS = 18 months, OOS = 6 months.
7. Count the number of independent configurations you plan to test (N). Compute MinTRL under realistic SR̂ and SR\* = E[max SR across N trials]. If MinTRL > available history, reduce N via Bayesian search or constrain the parameter space.

**WFA execution**
8. In each IS window: optimize strategy parameters only (not regime thresholds). Constrain the number of free parameters to below √(IS window length in bars) to prevent overfitting.
9. In each OOS window: apply fixed regime thresholds + IS-optimized parameters. Record per-step Sharpe, drawdown, and trade count.
10. Verify each OOS step contains ≥ 30 trades [4]; flag any step below this threshold as statistically unreliable.

**Post-WFA evaluation**
11. Compute WFE = annualized OOS return / annualized IS return. Target ≥ 50% [7].
12. Compute IS→OOS Sharpe degradation. Target ≤ 50%.
13. Compute DSR using all N configurations tested. Target DSR ≥ 0.95 [10].
14. Check profit concentration: no single OOS step should account for > 60% of total OOS profit.
15. Check stress-day concentration: if > 40% of OOS profit comes from tagged stress days, the edge may not generalize.

**Red flags — stop and do not deploy**
- IS Sharpe > 3.0 with < 200 total trades
- WFE < 30%
- DSR < 0.90 [10]
- Profit concentrated in 1 OOS step
- OOS drawdown > 2× IS drawdown
- Strategy on GLDRUBF, IMOEXF, or SBERF with < 100 total OOS trades

### Standard methodology vs MOEX-specific adaptations

| Dimension | Standard methodology | MOEX adaptation |
|---|---|---|
| IS:OOS ratio | 3:1 to 4:1 (20–25% OOS) [4] | Same ratio; anchored preferred when post-2022 history only |
| Minimum WFA steps | 10 steps recommended [4] | 4–6 steps realistic on post-2022 daily data; supplement with per-step analysis |
| Data gaps | Exclude or interpolate | Never interpolate Feb–Mar 2022; treat as hard regime boundary |
| Regime filters | Fix thresholds pre-WFA | Same; additionally use CBR rate as quarterly context switch |
| Multiple testing correction | DSR / White's RC / SPA | DSR primary; White's RC / SPA only with ≥ 5 steps and ≥ 500 OOS obs |
| Minimum trades per OOS step | 30 [4] | 30 minimum; target 100+ for reliable metrics |
| Perpetual futures | N/A | GLDRUBF / IMOEXF / SBERF: preliminary analysis only, not full WFA |

---

## Further Directions

**Combinatorial Purged Cross-Validation (CPCV)** — López de Prado's alternative to sequential WFA generates multiple OOS paths from the same history by combining different fold permutations, with purging to prevent leakage. On MOEX's short history, CPCV can produce more OOS paths than sequential WFA from the same data, giving a better-sampled distribution of OOS performance.

**Bayesian parameter optimization** — Gaussian Process search explores the parameter space with far fewer evaluations than grid search. Fewer evaluations means smaller N in the DSR formula, which directly raises DSR for a given observed SR. This is the most direct lever for improving statistical validity on short MOEX history.

**Bootstrap surrogate testing** — Generate synthetic return series with the same mean, variance, skewness, and kurtosis as MOEX data, run WFA on them, and build the null distribution of WFE and DSR under the assumption of no true edge. This calibrates what WFE ≥ 50% actually means on MOEX-length data, rather than relying on thresholds derived from longer Western market histories.

## References

[1] MOEX Russia Index. https://www.moex.com/files/47h5f424r3va853q1z60pkpmwk
[2] MOEX Market Specifics Research Report. https://www.moex.com/n45535
[3] MOEX Market Specifics Research Report. https://fs.moex.com/f/19975/04-mosbirzha-ar2023-eng.pdf
[4] Frequently Asked Questions - TradeStation Platform. https://help.tradestation.com/09_01/tswfo/topics/frequently_asked_questions.htm
[5] A Rigorous Walk-Forward Validation Framework - arXiv. https://arxiv.org/html/2512.12924v1
[6] MOEX Trading Hours. https://www.moex.com/a2732
[7] Walk-forward Summary (Out-Of-Sample) - TradeStation Help. https://help.tradestation.com/10_00/eng/tswfo/topics/walk-forward_summary_out-of-sample.htm
[8] A Reality Check for Data Snooping. https://www.ssc.wisc.edu/~bhansen/718/White2000.pdf
[9] Stepwise SPA Test Paper. https://homepage.ntu.edu.tw/~ckuan/pdf/Step-SPA-20090720.pdf
[10] The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality. https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf
[11] Sample Size Requirements Research Report. https://www.davidhbailey.com/dhbpapers/sharpe-frontier.pdf
[12] Lopez_de_Prado_Sharpe.pdf. http://boston.qwafafew.org/wp-content/uploads/sites/4/2017/01/Lopez_de_Prado_Sharpe.pdf
[13] Regime Filter Integration Research Report. https://apjm.apacific.org/PDFs/12-109.pdf
[14] Key Rate - Bank of Russia. https://www.cbr.ru/eng/hd_base/KeyRate/
[15] Program interface for ISS. https://www.moex.com/a8531

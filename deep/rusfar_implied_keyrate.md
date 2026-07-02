# Extracting CBR Key Rate Trajectory from MOEX Interest Rate Derivatives (June 2026, Key Rate = 14.25%)

## The Instrument Landscape: What Actually Trades and What Does Not

As of June 2026, three exchange-listed interest rate futures exist on MOEX, but only one has a non-zero specification that matters for rate-expectation extraction — and even that one has zero liquidity. A fourth instrument (key rate futures) was announced for 2025 launch but has not traded. The most liquid rate-expectation signal on MOEX comes not from futures at all, but from standardized OTC swaps on the KEYRATE underlying.

**[FACT] Instrument summary table:**

| Instrument | Ticker | Notional | Quotation | Liquidity (Jun 2026) | Settlement | Source |
|---|---|---|---|---|---|---|
| RUSFAR futures | `1MFR-MM.YY` (e.g., `1MFR-6.26`; short code `MF`) | RUB 1,000,000 | 100 − avg RUSFAR (%p.a.) | **Zero OI, zero volume** [1][2] | Cash, last trading day of month | [3][4][5] |
| RUONIA money market futures | `RUON-MM.YY` | RUB 1,000,000 | 100 − avg RUONIA (%p.a.) | Negligible | Cash | [6][7] |
| RUONIA Index futures | `RUONIA-MM.YY` (short code `RF`; e.g., `RFU6` = Sep 2026) | ~RUB 44,500 per contract | Index points (accumulated RUONIA) | Minimal (~80 OI, ~24 contracts/day) [8] | Cash, vs. CBR-calculated index | [8][9] |
| Key Rate futures | Not assigned | — | — | **Not launched** [10] | — | [10] |
| KEYRATE / RUSFAR / RUONIA IRS & OIS swaps | SPFI market | Negotiated | Fixed vs. floating | **Most liquid rate product** | Cash | [11] |

**[FACT] RUSFAR futures** were launched in April 2019 [3] with 12 monthly contracts trading simultaneously. The `1MFR-6.26` contract showed a settlement price of 83.58 on 23 June 2026 with zero open interest and zero trading volume [2][1]. Contracts `1MFR-7.26` through `1MFR-6.27` exist in the FORTS listing but are equally illiquid.

**[FACT] Key rate futures** were planned for 2025 launch with a design concept (direct quotation of the key rate value, contract volume = price × 1,000 RUB) but were not launched as of June 2026 [10]. Anyone using the phrase "MOEX key rate futures" to describe an active instrument as of this date is referring to a non-existent product. Check the current MOEX derivatives listing at [10] before acting on any claims of a launch.

**[FACT] RUONIA Index futures** launched 19 May 2026 with quarterly expiration (September and December 2026; March and June 2027) [9][8]. Their quotation convention is index points (accumulated RUONIA since January 2010), not 100-minus-rate — so direct implied rate extraction requires additional calculation (see Section 2).

**[FACT] MOEX SPFI standardized OTC swaps** — IRS on KEYRATE and RUSFAR3M, OIS on RUSFAR and RUONIA — are the most actively traded rate derivatives on MOEX. IRS trading volume on RUSFAR reached RUB 6.4 trillion in 2025 (up ~80% year-on-year), with total standardized OTC open interest at RUB 13.2 trillion [11]. These instruments provide the only observable market-implied rate signal for horizons beyond one month. However, individual quote data is accessible only to MOEX SPFI participants, not to the general public.

**[ASSESSMENT]** The practical consequence: an analyst wanting to extract CBR rate trajectory expectations in June 2026 cannot rely on futures prices (zero liquidity). The actionable signal comes from KEYRATE swap rates on the SPFI market, which as of 23 June 2026 priced 3M–1Y tenors at 14.12–14.14% and longer tenors (2Y–7Y) at 13.2–13.6% — broadly consistent with the analyst consensus of 14.1% average for 2026 and a gradual decline path thereafter. This data point comes from a single source and should be treated as indicative, not verified.

---

## Building the Implied Rate Curve: Formulas and CBR Meeting Anchors

### Price-to-Rate Conversion

**[FACT]** For RUSFAR futures (1MFR), the conversion is direct [3][5]:

```
Implied RUSFAR (% p.a.) = 100 − Futures Price
```

Example: `1MFR-9.26` quoted at 85.50 → implied average RUSFAR for September 2026 = **14.50% p.a.**

The settlement price at expiration is the arithmetic average of daily RUSFAR fixings over the settlement month [5]:

```
Ps = 100 − (1/T) × Σ(i=1..T) RUSFAR_i
```

where T = number of calendar days in the settlement month, and RUSFAR_i = daily fixing published by MOEX at 12:30 MSK based on CCP-cleared repo in GCC (General Collateral Certificates) [3].

**[FACT]** The tick value (value of a 0.01% price move) scales with the number of calendar days in the settlement month [3]:

```
TickValue = 1,000,000 × 0.0001 × T / 365
```

For September (T = 30): TickValue = 1,000,000 × 0.0001 × 30/365 ≈ **8.22 RUB**. 
For January (T = 31): TickValue ≈ **8.49 RUB**. 
For February (T = 28): TickValue ≈ **7.67 RUB**.

This means a 100-contract position (RUB 100M notional equivalent) gains or loses approximately 822–849 RUB per 0.01% move in the implied rate per month — relevant for sizing hedges.

**[FACT]** The settlement month is defined as the period from the last trading day of the preceding month (inclusive) to the last trading day of the contract's settlement month (exclusive) [3]. This is not a calendar month — it starts one day earlier than the first of the month. For practical purposes, each contract captures the rate environment during its named calendar month.

### Curve Construction: No Bootstrapping Required

Unlike quarterly SOFR futures on CME — where a rate for a specific date requires bootstrapping across overlapping contract periods — each 1MFR contract directly yields the arithmetic average RUSFAR expected for its named month [5][2]. The curve is assembled as a simple table:

| Contract | Settlement Month | Implied RUSFAR | CBR Meeting in Month |
|---|---|---|---|
| `1MFR-7.26` | July 2026 | 100 − P₇ | 24 July 2026 [12] |
| `1MFR-8.26` | August 2026 | 100 − P₈ | — |
| `1MFR-9.26` | September 2026 | 100 − P₉ | 11 September 2026 [12] |
| `1MFR-10.26` | October 2026 | 100 − P₁₀ | 23 October 2026 [12] |
| `1MFR-11.26` | November 2026 | 100 − P₁₁ | — |
| `1MFR-12.26` | December 2026 | 100 − P₁₂ | 18 December 2026 [12] |

**[FACT]** The CBR holds 8 scheduled meetings per year in 2026, with 4 "core" meetings (February, April, July, October) accompanied by a medium-term forecast publication [12]. Remaining 2026 dates after June: 24 July, 11 September, 23 October, 18 December [12].

### Mapping a Rate Change to the Curve

When the CBR cuts the key rate at a meeting mid-month, the effect on a futures contract depends on how many days in the settlement month fall before versus after the decision. For a meeting on day D of month M with T total calendar days:

```
Implied Rate_M = (D/T) × Rate_before + ((T−D)/T) × Rate_after
```

This means a 25 bps cut on 24 July (day 24 of 31) shifts the `1MFR-7.26` implied rate by approximately (31−24)/31 × 25 bps ≈ **5.6 bps** — most of the month's rate is already "locked in" by the time of the meeting. The full cut only appears in the August contract.

### Probabilistic Rate-Change Extraction

**[ANALOGY — developed markets]** By analogy with the Fed Funds futures methodology [13], if two adjacent monthly contracts straddle a CBR meeting date, the implied probability of a cut of X bps can be estimated as:

```
P(cut by X bps) = (Implied Rate_M − Implied Rate_M+1) / X
```

where M is the month containing the meeting and M+1 is the following month. This formula assumes the market prices exactly one possible outcome (cut vs. no cut). For a two-outcome scenario (cut by X or cut by Y), the formula generalizes to a system of two equations.

**Caveat**: this methodology is only meaningful when contracts have observable bid/ask prices from actual trades. With zero OI on 1MFR contracts, any "probability" extracted this way is theoretical — derived from settlement prices that may reflect stale quotes or model-based marks, not real market consensus.

### RUSFAR ↔ Key Rate Relationship

**[ASSESSMENT]** RUSFAR is the secured overnight repo rate for CCP-cleared transactions in GCC [3]. The key rate is an unsecured policy rate. The structural spread is positive (RUSFAR < key rate in normal conditions) because secured borrowing is cheaper than the policy corridor floor. Empirically, the RUSFAR–key rate spread has historically been in the range of −10 to +40 bps, with the RUONIA–key rate spread reaching 40 bps in March 2025 [4]. During periods of liquidity stress, the spread can widen further.

To convert implied RUSFAR to implied key rate:

```
Implied Key Rate ≈ Implied RUSFAR + Historical Spread
```

The historical spread should be estimated from recent RUSFAR and key rate data (available from MOEX and CBR respectively). Using a spread of +10 to +20 bps as a baseline, an implied RUSFAR of 14.30% would translate to an implied key rate of approximately 14.40–14.50%. This conversion is inherently approximate — the spread is not constant and is itself subject to monetary conditions.

---

## Comparing Implied Trajectory Against Consensus and CBR Forecasts

### Three Sources of Rate Expectations

**[FACT]** The CBR publishes a medium-term forecast four times per year following core meetings. The format is annual average ranges for the key rate. The April/May 2026 forecast projected [14][15]:

- 2026 average: **14.0–14.5%**
- 2027 average: **8.0–10.0%**
- 2028 average: **7.5–8.5%**

**[FACT]** The CBR macroeconomic survey (31 economists, surveyed 5–9 June 2026) showed median analyst forecasts of: 2026 average **14.1%**, 2027 **10.6%**, 2028 **9.0%** [16]. Analysts are therefore aligned with the CBR for 2026 but more hawkish for 2027–2028 (10.6% vs. CBR's 8–10%, and 9.0% vs. CBR's 7.5–8.5%).

**[FACT]** The June 2026 meeting case: the CBR cut by 25 bps to 14.25% [17], while the median market consensus expected a 50 bps cut to 14.00% [17]. This 25 bps hawkish surprise is the clearest available illustration of futures-vs-reality divergence.

### Comparison Table

| Meeting Date | Market Consensus Expectation | CBR Forecast Range (2026 avg) | Actual Decision | Divergence |
|---|---|---|---|---|
| 19 Jun 2026 | −50 bps → 14.00% | 14.0–14.5% | **−25 bps → 14.25%** [17] | Market priced more easing; CBR delivered less |

The KEYRATE swap curve (3M–1Y at 14.12–14.14% as of 23 June 2026) aligns closely with the analyst consensus of 14.1% for 2026, suggesting that after the June surprise, the swap market repriced toward the CBR's own guidance rather than maintaining the pre-meeting dovish bias.

### Why Implied Rates and Consensus Systematically Diverge

**[ASSESSMENT]** Five mechanisms drive persistent gaps between the implied rate curve and the CBR's or analysts' forecasts:

- **Liquidity premium in futures prices**: When OI is zero, the settlement price on MOEX reflects the last agreed mark (or a model price), not a traded equilibrium. Any "implied rate" extracted from it is not a market consensus — it is a stale quote. This is the dominant distortion in the current Russian market.

- **No dot plot from CBR**: The CBR does not publish individual policymaker projections in the style of the Federal Reserve's Summary of Economic Projections. The market must infer the reaction function from press conference language and the quarterly forecast ranges, which are wide (e.g., 8–10% for 2027 covers 200 bps of uncertainty). This forces futures prices to embed a wider distribution of outcomes.

- **Convexity adjustment** [ANALOGY — developed markets]: In liquid markets (Eurodollar, SOFR), futures prices are systematically lower than OIS forward rates because futures P&L is settled daily (variation margin) while OIS is settled at maturity. The convexity adjustment grows with tenor and rate volatility. For RUSFAR contracts, this adjustment is theoretically present but immaterial for near-month contracts; for contracts 6–12 months out, it could be on the order of a few basis points at current volatility levels. Without a liquid futures market to observe, this cannot be measured empirically.

- **Geopolitical and sanctions tail risk**: Discrete non-linear events (new sanctions packages, escalation/de-escalation scenarios) cannot be priced as a smooth probability distribution. Futures prices may embed a risk premium for scenarios that are not part of the CBR's baseline.

- **Inflation data surprises**: The CBR's reaction function is explicitly inflation-targeting. Analysts systematically over- or under-estimate monthly CPI prints, causing their rate forecasts to lag actual CBR decisions. The June 2026 episode (market expected 50 bps, got 25 bps) likely reflects underestimation of how much residual inflation concern remained at the June meeting.

---

## Practical Applications: Hedging, Synthetics, and Speculation

### Hedging a Floater Portfolio

**[ASSESSMENT]** A portfolio of floating-rate bonds with coupons indexed to RUSFAR or the key rate faces coupon income risk when rates fall. The hedge is to sell 1MFR futures (short position profits when rates fall → futures prices rise).

The number of contracts required:

```
N = DV01_portfolio / DV01_per_contract
```

**[FACT]** DV01 per 1MFR contract (value of 1 bp move) = TickValue × (1/0.01)... but since TickValue is already the value of a 0.01% (1 bp) move:

```
DV01_per_contract = 1,000,000 × 0.0001 × T/365
```

For a 30-day month: DV01 ≈ **8.22 RUB per contract per basis point**.

Illustrative calculation (not real data): A portfolio of floaters with RUB 100M face value and average remaining coupon term of 6 months has a DV01 of approximately RUB 50,000 (assuming each basis point of rate change affects 6 months of coupon income: 100,000,000 × 0.0001 × 0.5 = 5,000 RUB — this is a rough approximation; actual DV01 depends on coupon frequency and reset dates). To hedge this with 1MFR contracts: N ≈ 5,000 / 8.22 ≈ 608 contracts. At 0.03–0.2% IM per contract, that requires RUB 1.8M–12M in margin — manageable, but the hedge is currently unexecutable because there are no counterparties in the 1MFR market.

**Liquidity constraint**: With zero OI on all 1MFR contracts, the exchange-traded hedge is theoretical. The realistic alternative is an OTC IRS on KEYRATE or RUSFAR via the MOEX SPFI market [11], which carries counterparty risk managed through NCC clearing but has actual trading volume.

### Liquidity Funds (LQDT / SBMM / AKMM / TMON)

**[ASSESSMENT]** These money market ETFs invest in overnight CCP repo using GCC — the same underlying as RUSFAR. Their NAV grows daily by approximately RUSFAR/365. The lag between a CBR rate cut and the fund's yield repricing is 1–2 business days (the time for repo contracts to roll at the new rate).

The implied RUSFAR curve from 1MFR contracts gives a forward estimate of these funds' future daily yield. If the curve prices in RUSFAR declining from 14.3% today to 13.0% by December 2026, the fund's annualized yield will follow that path with the 1–2 day lag. The practical use case: an investor holding LQDT or SBMM as a cash substitute can use the implied curve to decide whether to rotate into fixed-coupon bonds before rate cuts accelerate. The break-even is the point at which the accumulated future RUSFAR yield (from the implied curve) falls below the yield on a comparable-duration fixed coupon bond.

### Synthetic Positions

**[ASSESSMENT + ANALOGY — developed markets]** By analogy with SOFR futures strategies [18]:

- **Synthetic fixed-rate deposit**: Buy 1MFR futures (long position). If rates fall faster than the market prices in, the futures gain value, offsetting lower reinvestment income. Mechanically equivalent to fixing the borrowing/lending rate for the contract month.

- **Synthetic floating-rate funding hedge**: Short 1MFR. A bank or corporate with floating-rate liabilities tied to RUSFAR locks in a maximum funding cost for the contract month. Profit if rates rise above the locked level.

- **Calendar spread (flattener)**: Buy the near-month contract, sell the far-month contract. This profits if the rate curve flattens — i.e., if the market's expectation of rate cuts in later months is reduced. Equivalent to a view that the CBR will slow its easing pace. The reverse (sell near, buy far) is a steepener trade, expressing a view that cuts will accelerate.

All three strategies are currently theoretical given zero liquidity. They become executable if a market maker enters the 1MFR market or if the OTC RUSFAR OIS market becomes accessible to the counterparty in question.

### Positioning Around CBR Meeting Dates

**[ASSESSMENT]** The event-driven approach: take a position in the contract covering the meeting month 1–2 weeks before the decision, close after the announcement. The setup is the same as Fed Funds futures trading around FOMC meetings [ANALOGY — developed markets]. The key variables are:

- **Pre-meeting implied rate** (from the contract covering the meeting month)
- **Consensus expectation** (from CBR analyst survey or Bloomberg poll)
- **Your own view** on the likely decision

If you expect a larger cut than the consensus, buy the contract for the month after the meeting (where the full cut will be reflected). If you expect a pause, sell that contract. The June 2026 episode illustrates the risk: the consensus expected 50 bps, the CBR delivered 25 bps. A long position in `1MFR-7.26` (expecting the cut to reduce July's average RUSFAR) would have lost value as the contract repriced upward (higher implied rate) after the hawkish surprise.

---

## Pitfalls: Basis, Margin, Expiration, and Distant Contracts

### Liquidity: The Dominant Constraint

**[FACT]** Open interest and trading volume on `1MFR-6.26` = 0 as of 23 June 2026 [2][1]. The same applies to forward contracts through at least `1MFR-7.26`. This is not a temporary thin-market condition — the contract has been listed since 2019 and has never developed a liquid secondary market. The structural reasons:

- The universe of natural hedgers (banks, corporate treasurers with RUSFAR-linked liabilities) is small and concentrated among institutions that can access OTC OIS directly.
- No market-maker program with binding quote obligations exists for 1MFR contracts.
- The OTC RUSFAR IRS market (RUB 6.4 trillion annual volume in 2025) absorbs the hedging demand that would otherwise flow to exchange-traded futures.

**[ANALOGY — developed markets]** SOFR futures on CME launched in May 2018 with near-zero liquidity. Meaningful OI only developed after the ARRC formally recommended SOFR as the LIBOR replacement (2021) and after the FCA announced LIBOR cessation dates. The Russian market lacks an equivalent regulatory mandate forcing participants to use exchange-traded instruments. Without such a mandate or a major market-maker commitment, 1MFR liquidity is likely to remain negligible.

### Basis to Spot RUSFAR

**[ASSESSMENT]** The basis between a futures contract and the realized average RUSFAR has two components:

1. **Within-month convergence**: At the start of a contract month, all T daily fixings are unknown — the futures price reflects full uncertainty. By day D of the month, (D−1) fixings are known and locked into the settlement calculation. The basis shrinks monotonically as the month progresses. By the last week of the month, the settlement price is nearly deterministic: a 25 bps surprise cut on day 28 of a 31-day month shifts the settlement price by only 3/31 × 25 bps ≈ 2.4 bps.

2. **Term premium / convexity adjustment** [ANALOGY — developed markets]: For contracts more than 3 months out, the daily mark-to-market of futures (vs. OIS, which settles at maturity) creates a systematic wedge. In liquid markets, this is typically a few basis points for short-dated contracts and can reach 10–20 bps for 1-year contracts at elevated rate volatility. For the Russian market, this adjustment cannot be estimated empirically without historical futures prices, but it should be directionally negative (futures price slightly below fair forward rate).

A practical consequence: if you observe a settlement price of 83.58 on `1MFR-6.26` on 23 June 2026 (implied rate 16.42%), that price is not a market-cleared equilibrium — it is a stale mark. The actual RUSFAR spot rate on that date was materially lower (consistent with the post-cut key rate of 14.25%). The 200+ bps gap between the settlement price and the spot rate reflects the absence of any real trading, not a genuine market expectation.

### Margin Requirements and Variation Margin Risk

**[FACT]** Initial margin (IM) per 1MFR contract: 0.03%–0.2% of RUB 1,000,000 notional = **RUB 300–2,000 per contract** [5][4]. Variation margin is settled daily in cash.

Illustrative calculation (not real data): A 100-contract short hedge (RUB 100M notional exposure) requires IM of RUB 30,000–200,000. If the CBR surprises with a 50 bps hike (rates rise → futures prices fall → short position profits), variation margin flows in. If the CBR surprises with a 100 bps cut (futures prices rise sharply), the short position faces a margin call. For a 100 bps adverse move on 100 contracts with T=30 days: loss ≈ 100 × 1,000,000 × 0.01 × 30/365 ≈ **RUB 82,200** — manageable relative to the IM posted, but the intraday cash requirement must be pre-funded.

### Expiration and Roll Timing

**[FACT]** The last trading day and final settlement date both coincide with the last trading day of the settlement month [3]. Once the settlement price is published, the contract closes and cash is settled. There is no physical delivery.

The roll implication: if you hold a position in `1MFR-7.26` and want to maintain exposure into August, you must close the July position and open `1MFR-8.26` before the last trading day of July. Rolling on the last day is not possible once settlement is triggered. In practice, given zero liquidity, the "roll" problem is academic — there is no position to roll.

In the final 3–5 trading days of a month, the settlement price becomes nearly deterministic (most fixings are known). Price movement effectively stops. If you are trying to close a position in this window, you may find no counterparty willing to trade at a fair price — bid-ask spreads widen even further in an already illiquid market.

### Distant Contracts: Double Uncertainty

**[FACT + ASSESSMENT]** For contracts 6–12 months out (e.g., `1MFR-12.26`, `1MFR-6.27`): zero liquidity is the rule, not the exception. Rate uncertainty is also highest at these horizons — the CBR's own 2027 forecast spans a 200 bps range (8–10%). An implied rate extracted from a stale settlement price on a 12-month contract is essentially meaningless as a market signal.

For any hedging or positioning need with a horizon beyond 3 months, the realistic instrument is an OTC IRS on KEYRATE or RUSFAR via the MOEX SPFI market. These instruments have actual volume, CCP clearing through NCC, and observable (to participants) bid/ask quotes.

---

## Case Study: The June 19, 2026 Hawkish Surprise

**[FACT]** On 19 June 2026, the CBR cut by 25 bps to 14.25% [17]. The median market consensus expected a 50 bps cut to 14.00% [17]. This 25 bps gap is the most recent concrete example of market-CBR divergence.

**Mechanism of the surprise**: The CBR's May 2026 commentary maintained the 14.0–14.5% average 2026 forecast [15], which is consistent with either a 50 bps or 25 bps cut in June. The market priced the lower end of the range (50 bps), while the CBR delivered a pace consistent with the midpoint. The CBR's public communication after the meeting signaled that the July trajectory revision might be upward if fiscal deficits persist — meaning the market's pre-meeting dovish positioning was based on an overly optimistic reading of CBR guidance.

**Illustrative P&L calculation (not real data, not a market estimate):**

Assume a trader held 100 long contracts in `1MFR-7.26` before the June 19 meeting, expecting the 50 bps cut to lower July's average RUSFAR by ~25 bps (the portion of July after the meeting). Assume the contract was marked at 85.60 pre-meeting (implied July RUSFAR = 14.40%).

Post-meeting repricing: with only 25 bps cut delivered and a hawkish signal for July, the July contract reprices to ~85.35 (implied July RUSFAR = 14.65%).

P&L per contract per 0.01% move = TickValue ≈ 8.49 RUB (July, T=31). 
Price move = −0.25% = −25 ticks. 
Loss per contract = 25 × 8.49 = RUB 212. 
Total loss on 100 contracts = **RUB 21,250** — illustrative calculation, inputs assumed.

**[ANALOGY — developed markets]** In the Fed Funds futures market, a comparable 25 bps hawkish surprise on a 30-day contract would move the contract price by approximately (days remaining in month / 30) × 0.25 price points. With a contract value of \$4,167 per basis point on a \$5M notional Fed Funds contract, the same 25-tick move generates approximately \$10,400 per contract. The scale difference (RUB 212 vs. \$10,400) reflects both the smaller notional (RUB 1M vs. \$5M) and the lower tick value — not a difference in methodology.

---

## Further Directions

**OFZ G-curve as an alternative rate-expectation signal**: The MOEX G-curve (government bond yield curve for OFZ fixed-coupon bonds) provides observable market prices across maturities from 1 to 30 years. Zero-coupon bootstrapping of the G-curve yields a term structure of forward rates that is directly comparable to implied RUSFAR from futures. The 1-year forward rate 1 year hence, for example, gives the market's expectation of the average short rate in year 2 — equivalent to what a liquid `1MFR-12.27` contract would provide. Unlike RUSFAR futures, the OFZ market has genuine daily volume and represents a more reliable source of rate expectations at horizons beyond 6 months.

**OTC OIS on RUSFAR/RUONIA**: The MOEX SPFI market provides IRS and OIS with KEYRATE, RUSFAR, and RUONIA as underlyings for tenors from 3 days to 10 years [11]. For participants with SPFI access, these instruments offer the most direct and liquid source of market-implied rate expectations. The NCC acts as central counterparty, eliminating bilateral credit risk. The limitation is that aggregate quote data is not publicly available — only SPFI participants can observe the live curve.

**SOFR futures as a development template**: CME Three-Month SOFR futures [18] went from near-zero OI at launch (May 2018) to the dominant short-rate instrument by 2023. The transition was driven by: (1) the ARRC's formal LIBOR replacement recommendation, (2) the FCA's LIBOR cessation announcement, and (3) CME's market-maker incentive program with binding quote obligations. For MOEX's 1MFR contracts to follow a similar path, a comparable regulatory catalyst (e.g., mandatory RUSFAR referencing in loan contracts, or a CBR-driven transition from RUONIA to RUSFAR as the interbank benchmark) would be the most likely trigger. Without it, the OTC market will continue to absorb the hedging flow that would otherwise support exchange-traded liquidity.

## References

[1] RUSFAR Futures Contract 1MFR-6.26 — Moscow Exchange. https://www.moex.com/en/contract.aspx?code=1mfr-6.26
[2] 1MFR-6.26 contract page. https://www.moex.com/en/contract.aspx?code=1MFR-6.26
[3] RUSFAR FUTURES CONTRACT SPECIFICATION. https://www.moex.com/files/4eaxnrp03ed6p3kbhkhg89nada
[4] RUSFAR Futures — Moscow Exchange. https://www.moex.com/a7245
[5] Фьючерс на ставку RUSFAR — Московская Биржа. https://www.moex.com/msn/ru-futrusfar
[6] MOEX RUONIA Futures. https://www.moex.com/msn/en-ruonia
[7] Фьючерс на ставку RUONIA — Московская Биржа | Рынки. https://www.moex.com/ru/derivatives/money/ruonia/
[8] Фьючерс на индекс RUONIA. https://www.moex.com/media/one-pager-ruonia.pdf
[9] MOEX RUONIA index futures launch announcement. https://www.moex.com/n100018
[10] Interest Rate Futures — Moscow Exchange. https://www.moex.com/s3818
[11] MOEX Standardized Derivatives Products. https://www.moex.com/s942
[12] CBR Key Rate Meeting Calendar 2026. https://www.cbr.ru/eng/dkp/cal_mp/
[13] Fed Funds futures methodology (adapted). https://www.federalreserve.gov/econres/feds/files/2019014pap.pdf
[14] COMMENTARY ON THE BANK OF RUSSIA'S MEDIUM-TERM FORECAST. https://cbr.ru/Content/Document/File/190102/comment_07052026.pdf
[15] Commentary on the Medium-term Forecast - Bank of Russia. https://www.cbr.ru/eng/dkp/mp_dec/decision_key_rate/comment_07052026/
[16] Macroeconomic survey results - Bank of Russia. https://www.cbr.ru/eng/statistics/ddkp/mo_br/
[17] Key rate decision - Bank of Russia. https://www.cbr.ru/eng/press/keypr/
[18] Three-Month SOFR Futures — CME Group. https://www.cmegroup.com/markets/interest-rates/stirs/three-month-sofr.html

# MOEX Market Microstructure: Rules for Backtest, Daybot, and Lab

## Section 1 — Master Checklist (Bottom Line Up Front)

Use the sections that follow as the rationale. The tables below are the primary deliverable.

**Important timing note (as of June 2026):** MOEX derivatives transitioned to a Unified Trading Session (ETS) on March 23, 2026 [1]. This eliminated the 14:00–14:05 intraday clearing pause and the 18:50–19:05 evening clearing pause for FORTS. Any backtest covering data before that date must apply the old clearing schedule; any strategy running live must apply the new one. The tables below reflect the current (post-ETS) state where it differs from the historical one; the historical rules are called out explicitly.

---

### Table 1 — Backtest Engine Rules

| Rule | Microstructure Category | Risk of Ignoring | Priority |
|------|------------------------|------------------|----------|
| **B-01** Equities opening auction ends at a random second between 09:59:31 and 09:59:59 MSK [2]. Do not treat 10:00:00 as a deterministic fill time; model a uniform draw over that 29-second window per security. | Opening auction | False fill at a price that did not exist when the signal fired | **High** |
| **B-02** FORTS opening auction ends at a random second between 08:59:01 and 08:59:50 MSK [3]. Same stochastic modeling applies. | Opening auction (FORTS) | False fill timing for futures strategies | **High** |
| **B-03** Closing auction for equities (T+1 mode) collects orders 18:40:01–18:50:00 MSK; each security's execution time has an additional 0–30 second random offset [4][5]. Closing price ≠ last continuous-session trade; if the auction does not determine a price, the last current price is used [6]. | Closing auction | Close-to-close return calculations carry implicit look-ahead bias | **High** |
| **B-04** During any auction (opening, closing, discrete), the 5% aggressiveness control does NOT apply [7]. Apply auction-specific order validation logic; do not reject orders that would be valid in auction mode. | Order limits | False order rejection during auctions | **High** |
| **B-05** Reject Market, IOC, FOK, and Book-or-Cancel order types during the opening auction period for both equities and FORTS [3][5]. Only Limit and Iceberg are accepted. | Order types / auctions | Simulated fills that the exchange would reject | **High** |
| **B-06** Equities evening session opening auction (19:00:01–19:04:59 MSK) does NOT accept Iceberg orders [8]. Main trading period of evening session does accept Iceberg. Enforce this distinction. | Order types / evening session | Iceberg fills during evening auction that cannot occur | **High** |
| **B-07** FORTS: model two clearing pauses in historical data (pre-March 23, 2026): 14:00–14:05 MSK (intraday) and 18:50–19:05 MSK (evening clearing) [9][1]. No fills can occur in these windows. Post-ETS (≥ March 23, 2026): no intraday pauses; clearing runs 23:50–00:30 MSK [1]. | Clearing / FORTS | Simulated trades during actual trading halts | **High** |
| **B-08** FORTS variation margin is assessed twice per day in historical data: after the 14:00–14:05 intraday clearing and after the 18:50–19:05 evening clearing [9]. Model cash flow at both cut-offs, not only at position close. | Clearing / FORTS | Incorrect P&L and margin-call simulation | **High** |
| **B-09** FORTS evening session trades (19:05–23:50 MSK, pre-ETS) were cleared at 14:00 MSK the next day [10]. This creates a 14+ hour gap between execution and settlement; model this lag separately from equities T+1 clearing. | Settlement / FORTS | Incorrect cash-availability assumptions for overnight futures positions | **High** |
| **B-10** Equities: clearing session at 17:00 MSK; securities obligations fixed at 16:00 MSK, cash obligations at 16:45 MSK [11]. Positions opened after 16:00 cannot be netted same-day. | Clearing / equities | Overstated intraday liquidity near session close | **High** |
| **B-11** Use the official MOEX trading calendar, not a Mon–Fri calendar. In 2026, all markets are closed on 9 dates (Jan 1–4, Jan 7, Feb 23, Mar 8, May 1, May 9, Jun 12, Nov 4, Dec 31) [12]. Some official off-days still have trading (e.g., Jan 5–6, Jan 8–9, Mar 9 in 2026) [12]. One missed day creates a false overnight signal. | Trading calendar | Spurious overnight/gap signals on non-trading days | **High** |
| **B-12** Discrete auctions for IMOEX constituent stocks: triggered when the IMOEX index itself moves ±15% from the previous close over 10 minutes [13], OR when an individual IMOEX stock moves ±20% from the previous close over 10 minutes [2]. Max 2 discrete auction series per stock per day; each series lasts 30 minutes; window 10:14–16:40 MSK main session only [2]. | Discrete auctions | Mean-reversion strategies catch a 30-minute price freeze as a "reversal signal" | **High** |
| **B-13** Discrete auctions for non-IMOEX stocks: triggered at ±20% over 5 minutes; unlimited series per day; main session window 10:09–18:10 MSK; evening session window 19:14–23:20 MSK [2]. IMOEX stocks have no discrete auctions in the evening session. | Discrete auctions | Incorrect halt simulation for small-cap strategies | **High** |
| **B-14** Mark all discrete auction periods in historical data and exclude them from continuous-book signal computation. A strategy that uses bid-ask spread or order-book depth during a DA period will see stale or absent data. | Discrete auctions | False edge in volatility/spread-based strategies | **High** |
| **B-15** Equities evening session (19:00:01–23:49:59 MSK) is a separate trading period, not an extension of the main session [2]. Do not merge it into a single daily OHLCV bar. Model execution parameters (slippage, market impact) separately; liquidity is materially lower. | Evening session / equities | Overstated liquidity and understated slippage for evening trades | **High** |
| **B-16** All active orders in the equities evening session are canceled by the exchange at 23:50:00 MSK [8]. Do not carry resting orders across this boundary in simulation. | Evening session | Phantom fills after session close | **High** |
| **B-17** Tick size for equities follows formula (1,2,5)×10^N with 25 price ranges and 7 liquidity tiers (max relative tick 1%), reviewed quarterly since Q2 2015, with changes effective 10 business days after notification [6]. The methodology was updated in February 2025. Do not use a single fixed tick per instrument across multi-year backtests; load the tick size valid on each trade date. | Tick size | Simulated orders at invalid price increments; false spread calculations | **High** |
| **B-18** Lot sizes for equities range from 1 to 1,000+ shares per lot depending on the instrument [6]. For FORTS single-stock futures, lot sizes range from 1 share (Magnit) to 100,000 shares (VTB) [9]. Round all order quantities to whole lots; fractional lots are impossible. | Lot size | Incorrect position sizing and P&L | **High** |
| **B-19** Do not simulate fills at mid-price. Model crossing the spread: buys fill at ask, sells fill at bid. Minimum spread = 1 tick. | Spread / execution | Overstated returns for any strategy with positive turnover | **High** |
| **B-20** Equities price limits by session: main session quoted stocks — 5% aggressiveness control from best bid/ask (effective Dec 25, 2023) [7]; evening session — ±10% from main session last price [14]; morning session — ±10%; weekend session — ±3% [14]; non-quoted stocks in main session — ±22% [14]. Orders outside these limits will be rejected; do not simulate fills beyond them. | Price limits | Simulated fills at impossible prices | **Med** |
| **B-21** Weekend session (ДСВД, Saturdays and Sundays since March 2025): 09:50–18:59:59 MSK, ±3% price limits, no discrete auctions, no closing auction, settlement T+1 relative to the next regular trading day [12]. Model separately from regular weekday sessions. | Weekend session | Incorrect settlement date and limit assumptions | **Med** |
| **B-22** Market orders on shares of international issuers are subject to a 1% price deviation limit from best bid/offer (reduced from 3% effective April 1, 2021) [15]. This is a separate constraint from the 5% aggressiveness control on limit orders. | Order limits | False fills on international-issuer market orders | **Med** |
| **B-23** For high-frequency or market-impact-sensitive strategies, use a linear impact model at minimum: impact = k × (order size / ADV). Iceberg orders in historical data mean visible depth understates true available liquidity, but do not assume hidden size is always present. | Liquidity / market impact | Overstated capacity for high-turnover strategies | **Med** |
| **B-24** Closing price = auction price if closing auction executed; otherwise = last current price of continuous session [6]. Strategies using close-to-close returns must account for this ambiguity — the price used depends on whether the auction ran successfully. | Closing price definition | Incorrect return series for close-based signals | **Med** |
| **B-25** Equities T+1 settlement: trade date + 1 business day, not T+2 (switched in 2023) [16]. Use business days per MOEX calendar, not calendar days. | Settlement | Wrong settlement date in cash-flow simulation | **Med** |
| **B-26** Morning session (06:50–09:49:59 MSK) is available only for stocks admitted to it; others start directly at the main session opening auction at 09:50 [2]. Do not simulate morning-session fills for non-admitted stocks. | Session availability | Phantom fills in pre-market for ineligible instruments | **Med** |
| **B-27** Bonds have a slightly different closing auction window (18:55:01–18:59:58 MSK) compared to stocks (18:55:00–18:59:30 MSK) [2]. Use instrument-class-specific windows. | Closing auction / bonds | Off-by-seconds fill errors at session close | **Low** |
| **B-28** Lot sizes and tick sizes are reviewed on a schedule (tick sizes quarterly, lot sizes semi-annually in March/September) [6]. Implement version-dated parameter tables; do not hardcode a single value per instrument. | Parameter versioning | Stale parameters creating false edge in older data | **Med** |

---

### Table 2 — Daybot (Live Trading Bot) Rules

| Rule | Microstructure Category | Risk of Ignoring | Priority |
|------|------------------------|------------------|----------|
| **D-01** Block Market, IOC, FOK, and BOC order submission during the opening auction window (equities: 09:50:00–09:59:59 MSK; FORTS: 08:50:00–08:59:59 MSK). These order types are rejected by the exchange [3][5]. | Order types / auctions | Rejected orders, missed fills, error handling overhead | **High** |
| **D-02** Detect discrete auction state via the trading status field in the ASTS/FAST feed. When a stock enters a discrete auction, switch to observation-only mode or auction-compatible order types. Do not interpret the absence of continuous-book quotes as a feed outage. | Discrete auctions | Erroneous order submissions or false reconnect logic | **High** |
| **D-03** (FORTS, pre-March 23, 2026) Block all order submission at 14:00–14:05 MSK (intraday clearing pause) and 18:50–19:05 MSK (evening clearing pause) [9][1]. Do not interpret quote absence as a technical failure. Post-ETS: these pauses no longer exist; remove the blocks. | Clearing / FORTS | Orders queued into a dead zone; clearing confusion | **High** |
| **D-04** Cancel all open equities evening-session orders before 23:49:59 MSK. The exchange cancels at 23:50:00 MSK [8], but relying on exchange cancellation creates a race condition. | Evening session | Stale orders surviving into next-day state | **High** |
| **D-05** Round all target order prices to the nearest valid tick in the direction that does NOT improve your price (i.e., round buys up, sells down to the nearest tick). The tick size is instrument- and price-level-dependent under the (1,2,5)×10^N formula. | Tick size | Order rejected for invalid price increment | **High** |
| **D-06** Validate order price against the session-specific deviation limit before submission: 5% from best bid/ask during continuous main-session trading [7]; ±10% from main-session close during evening session [14]; ±3% during weekend session. Exception: no aggressiveness limit during auctions [7]. | Price limits | Order rejection; wasted latency | **High** |
| **D-07** Apply the 1% market-order deviation limit separately for international issuer shares [15]. This is a different check from the 5% aggressiveness control and must be validated independently. | Order limits / international issuers | Market order rejected on international shares | **High** |
| **D-08** Overnight positions opened in the equities evening session face a gap to the next morning's opening auction. There is no closing auction in the evening session [2]. Size these positions with explicit gap-risk budget. | Evening session / gap risk | Uncontrolled overnight gap exposure | **High** |
| **D-09** FORTS evening session trades (pre-ETS) clear at 14:00 MSK the next day [10]. Margin calls from those trades arrive the following morning. Ensure sufficient collateral is available before 14:00 MSK, not just at trade time. | Settlement / FORTS | Margin call surprise the morning after evening trading | **High** |
| **D-10** Use the MOEX official trading calendar for holiday detection. Treat "official off-days with trading" (e.g., Jan 5–6, Jan 8–9, Mar 9 in 2026) as normal trading days [12]. Do not block trading on these dates. | Trading calendar | Missed trading days; unnecessary position closures | **High** |
| **D-11** Implement adaptive position sizing by time of day: reduce size during the first 5–10 minutes after opening (auction residual, wide spreads), during the 14:00–14:05 FORTS clearing pause (historical), and during the evening session (low liquidity). | Intraday liquidity | Excessive slippage in thin periods | **Med** |
| **D-12** The closing auction for equities ends at a random time within 18:40:01–18:50:00 MSK plus a 0–30 second per-security random offset [4][5]. Do not submit Limit-on-Close or Market-on-Close orders expecting a deterministic execution time. | Closing auction | Fill uncertainty at session close | **Med** |
| **D-13** FORTS opening auction carries over resting Iceberg and Book-or-Cancel orders from the previous evening session [17]. Account for this state dependency when reconstructing the opening order book. | Order state / FORTS | Incorrect opening book reconstruction | **Med** |
| **D-14** Weekend session (ДСВД): price limits are ±3%, no discrete auctions, no closing auction. Do not apply weekday limit logic on Saturdays/Sundays. | Weekend session | Order rejections; missing halt detection | **Med** |

---

### Table 3 — Research Lab Rules

| Rule | Microstructure Category | Risk of Ignoring | Priority |
|------|------------------------|------------------|----------|
| **L-01** Close-to-close returns embed look-ahead bias when the closing price is the auction price: the auction price is determined at a random moment between 18:40:01 and ~18:50:30 MSK [4][5]. A strategy that "uses the close" implicitly knows the price before it is determined. Use the previous close as the signal input and the next open as the execution price to eliminate this bias. | Closing auction / look-ahead | Systematically overstated backtest returns | **High** |
| **L-02** Survivorship bias from 2022–2023 delistings and suspensions is severe on the Russian market. Backtest on a point-in-time index membership file, not on the current constituents. Strategies built on the current IMOEX composition will overstate returns for the 2020–2022 period. | Survivorship bias | Significant alpha inflation | **High** |
| **L-03** Dividend gaps on MOEX are not adjusted automatically in most data vendors. A momentum or mean-reversion signal fired on a dividend ex-date is catching the mechanical gap, not a genuine price signal. Adjust price series for dividends or explicitly exclude ex-date observations from signal training. | Dividend gaps | False factor signals on ex-dates | **High** |
| **L-04** Short-selling equities requires securities borrowing. In backtests, short positions should be flagged with a borrow availability check; from 2022 onward, borrow on many names became restricted or unavailable. Ignoring this overstates short-side returns. | T+1 / short-selling | Unrealizable short returns inflate strategy metrics | **High** |
| **L-05** Discrete auction periods (30-minute halts) create artificial price discontinuities. Exclude these periods from volatility estimation, spread analysis, and any factor that uses intraday price continuity. | Discrete auctions | Inflated volatility estimates; false reversal signals | **High** |
| **L-06** The 14:00 MSK FORTS clearing pause (pre-ETS) causes a sharp volume drop and spread widening. Volume- or spread-based signals computed across this boundary will generate false intraday patterns. Split intraday time series at 14:00 MSK for pre-ETS data. | Clearing pause / FORTS | Spurious intraday signals around 14:00 MSK | **High** |
| **L-07** The equities evening session is a structurally thin market. Prices formed there are not representative of the main-session price discovery process. When training models on closing prices, use the main-session closing auction price, not the last evening-session trade. | Evening session | Noisy training labels | **High** |
| **L-08** When computing "days to event" (earnings, dividend, coupon), use MOEX trading days, not calendar days. The Russian calendar has 8–10 closures per year plus weekend-session anomalies; using calendar days misdates the event window by up to several days. | Trading calendar | Misaligned event windows | **Med** |
| **L-09** Tick size changed in February 2025 (25 price ranges, 7 liquidity tiers). Any spread or price-impact model trained on pre-2025 data will underestimate spreads for instruments whose tick was reduced. Retrain or recalibrate models on post-change data, or include a tick-size dummy variable. | Tick size | Stale spread/impact model | **Med** |
| **L-10** Historical order-book data (Level 2) on MOEX may be incomplete or absent for many periods. OHLCV-based backtests give an optimistic fill estimate because they cannot reflect queue position or hidden Iceberg volume. When reporting strategy capacity, apply a conservative haircut to fill-rate assumptions. | Order book / Iceberg | Overstated fill rates and capacity | **Med** |
| **L-11** The microstructure regime changed materially after February 2022 (exit of foreign participants, sanctions, NSD restrictions). Liquidity, spreads, and market-impact parameters from 2018–2021 are not representative of 2022–present. Train models on regime-segmented data or include a post-2022 indicator variable. | Regime change | Models trained on pre-2022 data will misestimate costs | **High** |
| **L-12** The FORTS ETS transition (March 23, 2026) eliminates the intraday clearing pause and merges the session structure. Any intraday factor that relied on the 14:00 volume pattern will structurally break after this date. Re-evaluate such factors on post-ETS data before deployment. | ETS / FORTS | Factor decay after March 2026 | **High** |

---

## Section 2 — Opening and Closing Auctions: Exact Mechanics

### Equities Opening Auction (Main Session)

Order collection runs 09:50:00–09:59:(31–59) MSK, with the auction ending at a random second in the 29-second window (09:59:31–09:59:59) [5]. The exact end time differs per security. Accepted order types: Limit and Iceberg only. Market, IOC, FOK, and Book-or-Cancel orders are rejected [5]. Price is set to minimize the imbalance between supply and demand; if the best bid remains below the best offer after the collection phase, no auction price is determined and the security opens in continuous mode [3][13].

The morning session has a separate opening auction at 06:50:00–06:59:(31–59) MSK for stocks admitted to it [2]. Stocks not admitted to the morning session skip it entirely and start at 09:50:00 [2].

### FORTS Opening Auction

Order collection runs 08:50:00 to a random second between 08:59:01 and 08:59:50 MSK [3][17]. The same order-type restrictions apply (Limit and Iceberg only; no Market, IOC, FOK, Book-or-Cancel, or negotiated orders) [17]. A critical state dependency: resting Iceberg and Book-or-Cancel orders from the previous evening session carry over into the opening auction book [17]. Price limits during the auction are static — they do not change during the collection window [17]. The FORTS opening auction was reinstated for all futures contracts from October 30, 2023 [17].

### Equities Closing Auction

The closing auction in T+1 mode operates in two configurations [5]:

- **Standard phase**: order collection 18:40:01–18:45:29 MSK, execution at 18:45:00–18:50:00 MSK.
- **Extended phase**: activated if price determination conditions are not met during the standard phase; order collection extends to 18:48:59 MSK.

After the applicable collection phase, each security's execution time has an additional random offset of 0–30 seconds [4]. This randomization prevents last-second order placement from manipulating the auction price [5].

Order priority: Limit-on-Close and Market-on-Close orders have priority over regular Limit and Market orders [5]. Unexecuted limit orders from the continuous session are transferred to the closing auction automatically.

The main session ends at 18:59:30 MSK [2], not 18:50:00. The gap between the closing auction (ending ~18:50:30) and the session close (18:59:30) is a post-auction trading period.

---

## Section 3 — Discrete Auctions: Triggers, Windows, Constraints

A discrete auction (DA) is a 30-minute halt of continuous trading replaced by an auction mechanism. There are three distinct trigger types [2][13]:

**Type 1 — IMOEX index-level trigger**: if the IMOEX index moves ±15% from the previous close over a 10-minute observation window, a DA is launched for all stocks and DRs simultaneously, running 10:10:00–10:14:00 MSK [2][13].

**Type 2 — Individual IMOEX constituent stock trigger**: if an individual IMOEX stock moves ±20% from the previous close over a 10-minute window, a DA is launched for that stock. Window: 10:14:00–16:40:00 MSK (main session only). Maximum 2 series per stock per day [2].

**Type 3 — Individual non-IMOEX stock trigger**: if a non-IMOEX stock moves ±20% from the previous close over a 5-minute window, a DA is launched. Window: 10:09:00–18:10:00 MSK (main session) and 19:14:00–23:20:00 MSK (evening session). No daily frequency limit [2].

Note on trigger windows: one source [13] references the 15% IMOEX index trigger, while the detailed schedule source [2] documents the 20%/10-min and 20%/5-min individual stock triggers separately. These are not contradictory — they describe the two different trigger mechanisms (index-level vs. stock-level).

IMOEX constituent stocks do not have discrete auctions in the evening session. Non-IMOEX stocks can. The 5% aggressiveness control does not apply during a DA [7].

For backtest purposes: any strategy using continuous-book data (spread, depth, VWAP, momentum) must treat DA periods as data gaps. A mean-reversion strategy that sees a 20% intraday move and fires a counter-trend signal is likely entering a DA — the 30-minute "freeze" will look like a reversal in OHLCV data but is a structural halt.

---

## Section 4 — Evening Session: Schedule, Rules, Traps

**Equities evening session**: 19:00:01–23:49:59 MSK [2]. Opening auction 19:00:01–19:04:59 MSK [8]. No closing auction. All active orders are canceled by the exchange at 23:50:00 MSK [8]. Price deviation limit: ±10% from the main session's last current price [14]. Discrete auctions possible for non-IMOEX stocks only (19:14:00–23:20:00 MSK) [2].

The evening session opening auction accepts Market and Limit orders but **not** Iceberg orders [8]. The continuous trading period that follows accepts all three [8].

**FORTS evening session (pre-ETS, i.e., before March 23, 2026)**: 19:05–23:50 MSK for all futures and options [10]. Trades executed here were cleared at 14:00 MSK the next day during the intraday clearing session [10]. This 14+ hour settlement lag means margin calls from evening futures trades arrive the following morning — not overnight.

**Post-ETS (March 23, 2026 onward)**: the separate FORTS evening session was abolished [1]. The derivatives market now runs under a unified schedule without the intraday and evening clearing pauses. Clearing occurs at 23:50–00:30 MSK.

The equities evening session is structurally thin. MOEX's own "Good Evening" market-making program provides obligated liquidity during this window, meaning some of the visible spread tightening reflects market-maker obligations rather than organic order flow. Models trained on main-session spread data will underestimate evening spreads.

---

## Section 5 — Clearing: Equities and FORTS

### Equities (T+1)

Settlement is T+1 (trade date + 1 business day), switched from T+2 in 2023 [16]. NCC acts as CCP and guarantees settlement to all non-defaulting parties [18][19].

Intraday deadlines [11]:
- 16:00 MSK: securities obligations fixed
- 16:45 MSK: cash obligations fixed
- 17:00 MSK: clearing session

Mark-to-market occurs at 09:30 MSK; margin calls must be met by 17:30 MSK [20]. Equities initial margin uses portfolio margining with concentration-based haircuts across three levels — larger positions face non-linearly higher haircuts [21][22]. This means a backtest cannot use a fixed margin percentage; it must scale haircuts with position size.

### FORTS (Derivatives)

**Pre-ETS (before March 23, 2026):**
- Intraday clearing pause: 14:00–14:05 MSK (5 minutes, trading halted) [1][9]
- Evening clearing pause: 18:50–19:05 MSK (15 minutes, trading halted) [9]
- Variation margin assessed at both clearing events [9]
- Evening session trades (19:05–23:50) cleared at 14:00 MSK next day [10]

**Post-ETS (March 23, 2026 onward):**
- No intraday trading halts for clearing [1]
- Clearing at 23:50–00:30 MSK
- Intermediate clearing abolished [1]

For backtests covering historical FORTS data, the pre-ETS clearing structure must be applied. A backtest that ignores the 14:00 and 18:50 pauses will simulate trades during actual trading halts and will misstate variation margin cash flows.

NCC margin rates are set as a percentage of spot price with concentration limits in units of the underlying asset [22]. The Single Limit for initial margin combines three components: portfolio mark-to-market, spot risk, and interest rate risk, with inter-product spread offsets for correlated positions [23].

---

## Section 6 — Price Limits, Tick Size, and Lots

### Price Limits

Session-specific limits for equities [7][14]:

| Session | Limit Type | Limit |
|---------|-----------|-------|
| Main session (continuous) | Aggressiveness control on limit orders | ±5% from best bid/ask [7] |
| Main session (non-quoted stocks) | Hard price band | ±22% from previous close [14] |
| Evening session | Hard price band | ±10% from main-session last price [14] |
| Morning session | Hard price band | ±10% from previous close [14] |
| Weekend session (ДСВД) | Hard price band | ±3% from previous close [14] |
| International issuer market orders | Separate hard limit | ±1% from best bid/offer [15] |

The 5% aggressiveness control does not apply during opening, closing, or discrete auctions [7]. The ±22% and ±10% hard bands are always active. These are separate mechanisms enforced at different layers.

For trading modes (Negotiated Trades, Repo): limits vary by mode and can reach ±70–100% for stocks and ±40% for bonds in negotiated modes [24]. These are not relevant for continuous-book strategies but matter for OTC-style execution simulation.

### Tick Size (Equities)

The tick size formula is (1, 2, 5) × 10^N, where N is an integer. As of February 2025, the methodology uses 25 price ranges and 7 liquidity tiers, with a maximum relative tick of 1% [6]. Tick sizes are reviewed quarterly (February, May, August, November) since Q2 2015; changes take effect on the 10th business day after notification publication. Bond tick size is 0.01% of nominal value [25].

The practical consequence: a stock trading at 100 RUB may have a different tick than the same stock at 50 RUB, and a liquid stock may have a smaller tick than an illiquid one at the same price level. A backtest using a single fixed tick per instrument across multi-year data will simulate orders at invalid prices and generate spurious spread-based edge.

### Tick Size (FORTS)

Derivatives tick sizes are fixed per contract specification and do not follow the equities formula. Single-stock futures: 1 RUB per contract [9]. Sector index futures: 1 point (tick value 1 RUB). FX futures (USD/RUB Si): 1 RUB, contract size 1,000 USD. Each contract type has its own specification; load them individually.

### Lot Sizes

Equities: default is 1 security per lot per Trading Rules [6], but many instruments have lots of 10, 100, or 1,000+ shares. The Exchange may change lot sizes by decision, with semi-annual reviews in March and September. FORTS single-stock futures: lot sizes range from 1 share (Magnit) to 100,000 shares (VTB) [9], directly affecting notional value per contract.

---

## Section 7 — Trading Calendar

MOEX 2026 full closures (all markets): January 1–4, January 7, February 23, March 8, May 1, May 9, June 12, November 4, December 31 [12].

Trading occurs on some official off-days: January 5–6, January 8–9, March 9, and May 11, 2026 [12]. On these dates, FX and Precious Metals markets exclude "today" settlement and swap transactions with same-day first legs [12].

Weekend sessions (ДСВД) were introduced March 1, 2025 for the equities and derivatives markets, running every Saturday and Sunday. The 2026 weekend session schedule is published separately from the main holiday announcement [12].

The calendar may be amended based on NCC correspondent bank holidays or Exchange decisions [26]. Backtest systems should use a versioned calendar snapshot tied to the data vintage, not a static file.

Russian calendar specifics: some Fridays are declared non-working with the preceding Saturday made a working day (transferred working days). A Mon–Fri calendar misses these transfers and generates false overnight signals.

---

## Section 8 — Intraday Liquidity, Spread, and Order Book

### Intraday Liquidity Profile

MOEX equities follow a broadly U-shaped intraday volume profile: elevated volume in the first 15–30 minutes of the main session (auction residual + early order flow), a midday trough, and a pickup ahead of the closing auction. The lowest-liquidity periods are:

1. **First 5–15 minutes after 10:00 MSK**: residual auction uncertainty, wide spreads as the book rebuilds.
2. **14:00–14:05 MSK** (FORTS, pre-ETS): volume collapses, spreads widen on futures; this bleeds into correlated equity names.
3. **Evening session (19:00–23:50 MSK)**: structurally thinner; MOEX derivatives evening session represented roughly 19% of derivatives volume per exchange presentation data, but equities evening volume is a small fraction of the main session.

### Order Book Structure

Iceberg orders specify a total quantity and a visible ("peak") quantity separately; only the peak is displayed in the order book [3]. Refreshes occur automatically when the visible tranche is exhausted. This means the visible depth in the book systematically understates true available liquidity for large Iceberg orders. However, the hidden portion is not guaranteed — it can be canceled before it refreshes.

Order types available in continuous main-session trading [6]:
- **Limit**: DAY, IOC, FOK
- **Market**: DAY, FOK (not IOC)
- **Iceberg**: DAY only

Order types restricted or unavailable in auctions: Market, IOC, FOK, BOC (opening and closing auctions); Iceberg additionally excluded from the evening session opening auction [8].

The MOEX order book is 10×10 (10 bid levels, 10 ask levels) in standard display. Historical Level 2 data availability varies by period and data vendor. Backtests using only OHLCV data cannot account for queue position, which is the primary determinant of fill probability for passive limit orders.

---

## Section 9 — FORTS vs. Equities: Comparison Table

| Parameter | Equities (Main Market, T+1) | FORTS (Derivatives) | Backtest Criticality |
|-----------|----------------------------|---------------------|----------------------|
| Main session | 09:50–18:59:30 MSK [2] | 09:00–18:50 MSK (pre-ETS) [9] | **High** — different session end times |
| Opening auction | 09:50:00–09:59:(31–59) MSK, random 29-sec end [5] | 08:50:00–08:59:(01–50) MSK, random ~49-sec end [3][17] | **High** — different start times and randomization windows |
| Closing auction | 18:40:01–18:50:00 MSK + 0–30 sec random offset [4][5] | None (no closing auction for futures) | **High** — futures use last trade price, not auction price |
| Evening session | 19:00:01–23:49:59 MSK, no closing auction [2] | 19:05–23:50 MSK (pre-ETS) [10]; abolished post-ETS | **High** — different start times; FORTS evening abolished Mar 2026 |
| Clearing | T+1; obligations fixed 16:00/16:45 MSK; clearing 17:00 MSK [11] | Pre-ETS: intraday 14:00–14:05, evening 18:50–19:05 MSK [9]; post-ETS: 23:50–00:30 MSK [1] | **High** — two clearing events vs. one; variation margin mid-day |
| Price limits | 5% aggressiveness control (continuous); session-specific hard bands [7][14] | Instrument-specific per contract specification [22] | **High** — different limit mechanisms |
| Tick size | (1,2,5)×10^N formula, 25 price ranges, 7 liquidity tiers [6] | Fixed per contract spec (e.g., 1 RUB for single-stock futures [9]) | **High** — equities tick is price- and liquidity-dependent |
| Lot size | 1 to 1,000+ shares per lot, semi-annual review [6] | 1 to 100,000 shares of underlying per contract [9] | **High** — lot size determines notional; varies dramatically |
| Discrete auctions | Yes; IMOEX: max 2/day, 10-min window; non-IMOEX: unlimited, 5-min window [2] | No discrete auctions | **High** — equities-only mechanism |
| Margin / settlement | Portfolio margining, concentration haircuts, T+1 cash [16][21] | Variation margin twice daily (pre-ETS); NCC initial margin with concentration limits [22][23] | **High** — variation margin cash flow timing differs |

---

## Section 10 — Non-Obvious Sources of False Edge

### 1. Look-Ahead Bias via Closing Price

The closing price on MOEX equals the auction price if the closing auction ran successfully; otherwise it equals the last continuous-session trade [6]. The auction price is determined at a random moment between roughly 18:45:00 and 18:50:30 MSK. A strategy that generates a signal at 18:40:00 and "uses the close" as the execution price implicitly knows the outcome of an auction that has not yet completed. The correct approach is: signal fires on the previous close; execution is at the next open (or at a price available after the auction completes).

### 2. Survivorship Bias from 2022–2023 Delistings

Multiple large Russian companies were suspended, delisted, or had their shares transferred to restricted status in 2022–2023 following sanctions and corporate restructurings. A backtest using the current IMOEX composition will exclude these names from the historical universe, systematically overstating returns for any strategy that would have held them.

### 3. Dividend Gaps Not Auto-Adjusted

Most Russian data vendors do not apply dividend adjustments to historical price series by default. A momentum signal trained on unadjusted data will fire on ex-dividend dates where the price drop is entirely mechanical. Mean-reversion strategies will similarly see a false "oversold" signal. Explicitly adjust for dividends or exclude ex-date observations from signal training.

### 4. Short-Selling Constraints Under T+1

Short positions require securities borrowing. Since 2022, borrow availability on many Russian names has been restricted or unavailable. A backtest that allows unrestricted short-selling overstates the short-side return contribution. For any strategy with a short component, apply a borrow availability filter using point-in-time data.

### 5. Auction Completion Randomness as Structural Price Uncertainty

The 29-second random window for the equities opening auction (09:59:31–09:59:59) is not a scheduling inconvenience — it means the opening price for any given stock is determined at an unknown moment within that window. A strategy that "trades on the open" faces genuine price uncertainty, not just execution latency. This cannot be hedged by submitting earlier; the price is not known until the auction completes. In backtest, this is correctly modeled by drawing the execution time from a uniform distribution over the window, which widens the confidence interval on any open-price-dependent return.

### 6. Clearing Pause as False Reversal Signal (Pre-ETS FORTS)

At 14:00 MSK, FORTS volume drops to zero for 5 minutes and spreads widen sharply as the book thins ahead of the pause. Volume- and spread-based signals computed on a continuous time series will detect this as a "reversal" or "liquidity event." In reality it is a scheduled structural pause. Any intraday factor trained on pre-ETS FORTS data that shows a 14:00 MSK signal cluster should be treated as a microstructure artifact, not alpha.

### 7. ETS Regime Break (March 23, 2026)

The FORTS Unified Trading Session eliminates the intraday clearing pause, merges the session structure, and changes the clearing time to 23:50 MSK [1]. Any intraday factor that relied on the 14:00 volume pattern, the 18:50 clearing pause, or the evening session as a separate regime will structurally break on this date. This is a hard regime boundary in FORTS data — models must be re-evaluated on post-ETS data before live deployment.

---

## Section 11 — Directions for Further Research

**1. Partial-Fill Simulation in Auction Mode**

The closing and opening auctions on MOEX use batch matching: all orders at the clearing price fill fully; orders at worse prices do not fill at all; orders at the clearing price may fill partially if the book is not fully cleared at that level. With limited historical order-book data, the standard approach (assume full fill at auction price) overstates fill rates. A more rigorous method: use the historical auction volume and estimate fill probability as min(order size, auction volume × share of same-side orders at clearing price). The sensitivity of strategy returns to fill-rate assumptions is worth quantifying explicitly before deployment.

**2. Microstructure Regime Segmentation: Pre- vs. Post-2022**

The exit of foreign participants after February 2022 changed the liquidity regime on MOEX materially — tighter bid-ask spreads in some periods (reduced foreign arbitrage activity) but lower depth and higher impact for large orders. Any model trained on 2018–2021 data and applied to 2022–present will have miscalibrated impact and spread parameters. The specific research question: what is the structural break date for each parameter (spread, depth, market impact coefficient), and how large is the parameter shift? This requires tick-level data segmented by participant category, which MOEX publishes in aggregate form.

**3. MOEX vs. Other Exchange Microstructures: Portability Checklist**

Several MOEX rules are unusual relative to major Western exchanges: the 29-second random auction end (LSE and Euronext also use random ends, but with different windows); the dual discrete-auction trigger system (IMOEX index-level vs. stock-level); the variation margin twice daily (most Western futures exchanges use end-of-day only); and the T+1 settlement for equities (the US moved to T+1 in May 2024, making this less distinctive, but the intraday clearing deadlines at 16:00/16:45/17:00 MSK remain MOEX-specific). Documenting which rules are MOEX-specific vs. common exchange practice allows a strategy team porting a strategy from another market to identify exactly which backtest modules need to be rewritten vs. reused.

## References

[1] Moscow Exchange - About Derivatives Market. https://www.moex.com/s400
[2] Trading Schedule - Moscow Exchange. https://www.moex.com/s1167
[3] Аукцион открытия - Московская Биржа. https://www.moex.com/s3576
[4] Closing auction — Moscow Exchange. https://www.moex.com/s1855
[5] Closing Auction. https://www.moex.com/s1851
[6] ПРАВИЛА проведения торгов на фондовом рынке и рынке депозитов. https://www.moex.com/files/4ftpwwfaaydtbtgny3qqrbcawb
[7] Aggressiveness control for limit orders on the stock market. https://www.moex.com/n66197
[8] Вечерняя торговая сессия на фондовом рынке Московской Биржи. https://www.moex.com/s3083
[9] Futures parameters. https://www.moex.com/a7211
[10] Evening trading session. https://www.moex.com/a2732
[11] Equities market. https://www.moex.com/s424
[12] Moscow Exchange trading schedule for 2026. https://www.moex.com/n94207
[13] Trading and clearing on March 10 | Новости и пресс-релизы. https://www.moex.com/n27231
[14] MOEX evening additional trading session (ВДС) has ±10% price deviation limit from the main trading session's last current price for Russian stocks. https://www.moex.com/a9010
[15] Market order price deviation limit for shares of international issuers is 1% from best bid/offer (reduced from 3% effective April 1, 2021). https://www.moex.com/n33303
[16] Moscow Exchange Annual report 2023. https://report2023.moex.com/en/2/2/index.html
[17] Opening auction. https://www.moex.com/msn/en-derivatives-premarket-session
[18] Repo with Central Counterparty (CCP) - Moscow Exchange. https://www.moex.com/en/markets/money/repock/
[19] NCC | General information - National Clearing Centre. https://www.nationalclearingcentre.com/catalog/530801
[20] T+ Settlement Cycle. https://www.moex.com/a657
[21] Initial Margin Requirements. https://www.moex.com/s1659
[22] NCC | Risk parameters - National Clearing Centre. https://www.nationalclearingcentre.com/catalog/530902
[23] Initial margin calculation methodology. https://www.moex.com/s1660
[24] Order parameters limitation system. https://www.moex.com/s640
[25] Размер лота и шаг цены — Московская Биржа | Рынки. https://www.moex.com/a8520
[26] Trading calendar — Moscow Exchange. https://www.moex.com/msn/en-fx-calendar

---

*Источник: deep/12.txt, ресёрч №3 (разрезан 2026-07-02).*

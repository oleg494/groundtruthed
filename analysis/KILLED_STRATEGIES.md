# Killed Strategies Index

Purpose: prevent re-testing the same dead MOEX delta-one ideas under new names.
Rule: a new strategy in one of these classes needs a new, concrete market fact before code.

Offline helper:

```bash
python -m analysis.strategy_preflight "opening range breakout on Brent futures"
python -m analysis.strategy_preflight --check-evidence
```

It ranks likely killed-class overlaps, prints the pre-flight questions below, and can
validate that evidence links still resolve.

| strategy | class | instruments / horizon | verdict | primary kill reason | evidence |
|---|---|---|---|---|---|
| XSEC_MOMENTUM | cross-sectional momentum | 11 MOEX stocks, daily | KILL | narrow correlated universe; OOS negative after warm-up; edge only in-sample | `analysis/BOT_DEV_2026-07-02_REPORT.md`, `analysis/botdev_xsec_momentum.md` |
| TREND_REGIME_PERP | trend following with ADX/Hurst gates | GLDRUBF/IMOEXF, daily/hour | KILL | positive cases had too few trades or failed buyhold; hourly variants negative | `analysis/BOT_DEV_2026-07-02_REPORT.md`, `analysis/botdev_trend_regime_perp.md` |
| ORB_REVERSAL | opening-range false breakout reversal | BMQ6/NGN6/GLDRUBF, 30-min | KILL | false breakouts did not survive stops/takes/costs; DSR 0 | `analysis/BOT_DEV_2026-07-02_REPORT.md`, `analysis/botdev_orb_reversal.md` |
| PAIRS_ZSCORE | statistical arbitrage / z-score pairs | 7 MOEX stocks, daily | KILL | episodic cointegration; structural pair divergence; short costs not yet included, so real result worse | `analysis/BOT_DEV_2026-07-02_REPORT.md`, `analysis/botdev_pairs_zscore.md` |
| MEANREV_HURST | Bollinger mean reversion gated by anti-persistence | MOEX stocks, daily | KILL | H<0.45 regime is rare; OOS trade count too low and OOS return negative | `analysis/BOT_DEV_2026-07-02_REPORT.md`, `analysis/botdev_meanrev_hurst.md` |
| ABSMOM_SWITCH | absolute momentum with cash proxy | LKOH/SBER/GLDRUBF/portfolio, daily | KILL | apparent survivor was mostly key-rate cash carry and warm-up silence, not asset timing skill | `analysis/BOT_DEV_2026-07-02_REPORT.md`, `analysis/botdev_absmom_switch.md` |
| INTRADAY_TREND_FUT | intraday momentum after midday | BMQ6/NGN6/GLDRUBF, 30-min | KILL | no tradable post-midday inertia; 10/12 OOS windows non-positive | `analysis/BOT_DEV_2026-07-02_REPORT.md`, `analysis/botdev_intraday_trend_fut.md` |
| TREND_LS_STOCKS | long/short trend | 11 MOEX stocks, daily | KILL | short leg loses by signal and by two-digit borrow/key-rate arithmetic | `analysis/BOT_DEV2_2026-07-02_REPORT.md`, `analysis/botdev2_trend_ls_stocks.md` |
| DAILY_BREAKOUT_FUT | positional channel breakout | BMQ6/NGN6/GLDRUBF, 30-min | KILL | Brent variant underperformed buyhold with too few trades; gas/gold negative | `analysis/BOT_DEV2_2026-07-02_REPORT.md`, `analysis/botdev2_daily_breakout_fut.md` |
| OVERNIGHT_PREMIUM | overnight anomaly capture | SBER/GAZP/LKOH, hourly/open-close | KILL | premium is real gross, but retail costs exceed break-even; needs <=1.6 bp per side | `analysis/BOT_DEV2_2026-07-02_REPORT.md`, `analysis/botdev2_overnight_premium.md` |
| CALENDAR_CB | CBR meeting directional drift | SBER/IMOEXF/GLDRUBF, daily events | KILL | event count too low; DSR 18-24%; profits concentrated in single surprises | `analysis/BOT_DEV2_2026-07-02_REPORT.md`, `analysis/botdev2_calendar_cb.md` |
| VOL_REGIME_SWITCH | volatility timing / exposure overlay | MOEX stock basket, daily | KILL | reduces drawdowns but cuts exposure near V-bottoms; no edge carrier without cash proxy | `analysis/BOT_DEV2_2026-07-02_REPORT.md`, `analysis/botdev2_vol_regime_switch.md` |

## Surviving Measurements

These are not deployable strategies, but they are useful facts for future research:

| measurement | status | implication |
|---|---|---|
| MOEX overnight premium | real gross, not retail-tradable | only revisit if execution cost is <=1.6 bp/side or via another instrument structure |
| CBR meeting realized-volatility pattern | confirmed | monetize with options/volatility tools, not delta-one directional bets |
| MOEX stock shorts | structurally poor | short-stock strategies need explicit borrow/key-rate drag and must prove the short leg adds value |
| Key-rate cash proxy | powerful confounder | cash-parking strategies must beat money-market carry, not just buyhold |

## New Strategy Pre-Flight

Before adding code for a new candidate:

1. Name which killed class it is closest to.
2. State the new market fact that invalidates the old kill reason.
3. Estimate whether the gross edge clears 10-20 bp per round trip and current key-rate cash carry.
4. Define the objective oracle: OOS return, DSR, buyhold/random comparison, and minimum trades per WFA window.
5. If it relies on cash, use historical key-rate assumptions rather than a flat current key rate.

# tinvest-project behavioral rules

## Strategy Optimization and Validation (Preventing Overfitting)
- **Walk-Forward Analysis (WFA):** Always validate strategy parameters optimized on historical data using WFA before recommending them. Single-run optimization yields statistical mirages due to curve-fitting.
- **Regime Filters:** If a strategy fails walk-forward validation out-of-sample, suggest introducing market regime filters (Hurst Exponent or ADX) from `deep/market_regime_moex.md` to toggle between trend-following and mean-reversion modes.

## Russian Market Taxation
- When discussing tax-loss harvesting or portfolio rebalancing for this workspace, default to the Russian Federation taxation rules (RF Tax Code Art. 214.1): no wash-sale rule, progressive 13-22% rate, basket-based netting (securities basket nets with securities derivatives, not currency/commodity derivatives), and the ЛДВ/long-term-ownership trap (securities that ever touched an ИИС account permanently lose ЛДВ eligibility).

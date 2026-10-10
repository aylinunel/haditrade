# Legacy daily research and current intraday hypotheses

## Current EUR 500 intraday profile

The new shared portfolio evaluates long and short variants on completed 15-minute bars: SMA 10/30 trend, RSI 14 reversal (30/70 entry; 50 exit), Bollinger 20/2 reversal (exit at mean), prior 20-bar range breakout confirmed with 0.1 ATR, and two-candle Heikin-Ashi direction confirmation. These span trend, reversal and breakout hypotheses. They are not ranked as the best five; no licensed intraday dataset was provided to rank them.

ATR range breakout replaces Parabolic SAR in this profile to add a distinct breakout family. This is a new hypothesis informed by the upstream collection's breakout approaches, not a verbatim upstream strategy. The service also computes trailing momentum, return volatility and volume deviation from observed bars in the last 48 elapsed hours. It does not fill market closures or assume 192 bars are available in every market.

The holding horizon is at most 48 elapsed hours, subject to executable bars. These indicator windows and contextual features are not proof that predictive 48-hour patterns exist. Intraday unit tests use synthetic cases to verify causality and accounting, not profitability. Options, futures, CFDs and forex are rejected by the current intraday simulator until contract-specific models and data are supplied. All five strategies compete for one EUR 500 portfolio, with at most three positions; this differs from the legacy separate-sleeve tests below.

## Earlier daily ORCL profile

Selection favors reproducibility from available daily OHLC bars, interpretable rules and limited dependencies. This is not a claim that these are universally the best strategies. Pair trading requires pair selection and cointegration validation; options need options-chain history; London Breakout and Dual Thrust need suitable intraday data. Those are deferred rather than tested on incompatible daily bars.

| Candidate | Rule | Change from reference |
| --- | --- | --- |
| Moving average | Long when SMA 10 exceeds SMA 30 | Upstream's MACD-labelled script actually uses simple averages. Fixed full warmup and next-open execution. |
| RSI reversal | Wilder RSI 14: buy below 30; exit at 55 | Long/cash state replaces upstream's long/short threshold positions. |
| Bollinger | 20-bar mean and 2 sample SD; buy below lower band; exit at mean | Basic reversion replaces upstream's scale-dependent W-pattern thresholds. |
| Parabolic SAR | .02 acceleration increments up to .20; long in bullish state | Causal standard recursion, prior-bar clamps and a 30-bar warmup. |
| Heikin-Ashi | Two bullish candles, no lower wick and expanding body; exit after two bearish candles | Upstream enters long on bearish candles. Direction is deliberately corrected for a trend-following variant. All fills use real OHLC. |

The parameters were fixed before all three windows. They were not tuned after seeing results. Strategy correlations are exported; three trend-related candidates on one stock do not create broad market diversification. These legacy tests are deterministic and do not evaluate the new online learner.

Reviewed upstream tree: `je-suis-tm/quant-trading` commit `611b73f2c3f577ac5b28aaa19ac8c43d3236c7a5`.

Reviewed scripts: MACD Oscillator, RSI Pattern Recognition, Bollinger Bands Pattern Recognition, Parabolic SAR and Heikin-Ashi. EliteQuant's top-level repository contains references and licensing information rather than a strategy engine.

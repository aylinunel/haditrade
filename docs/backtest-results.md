# Backtest results

Actual historical tests on a pinned public ORCL daily OHLC fixture. Not TrendSpider data.

| Window | Dates | Portfolio | Buy & hold | Max drawdown | Sharpe |
| --- | --- | --- | --- | --- | --- |
| Expansion | 2003-01-02 to 2007-12-31 | 19.77% | 105.99% | 12.59% | 0.38 |
| Financial crisis | 2008-01-02 to 2009-12-31 | 2.72% | 8.56% | 17.35% | 0.16 |
| Recovery | 2010-01-04 to 2014-12-31 | 22.59% | 82.00% | 13.74% | 0.44 |

The combined strategy portfolio underperformed buy-and-hold in all three periods. Bollinger and RSI were positive across all three samples. These observations are not evidence of future performance or an independently validated strategy ranking.

| Strategy | 2003–2007 | 2008–2009 | 2010–2014 |
| --- | --- | --- | --- |
| Moving average | -13.98% | -4.37% | 20.51% |
| RSI reversal | 12.97% | 14.77% | 27.46% |
| Bollinger bands | 81.32% | 18.20% | 64.35% |
| Parabolic SAR | 34.25% | -2.36% | -2.18% |
| Heikin-Ashi | -15.72% | -12.66% | 2.81% |

$10,000 split equally into five independent sleeves. Costs: 5 bps commission plus 5 bps adverse slippage per side. Long/cash only. Close signals fill at the next open. End-of-test positions are liquidated at the final real close with costs. No cash interest, dividends, tax, FX or market-impact modelling. Full trades, daily accounting, input hash and correlations are in `research/backtests.json`.

This is one old historical stock dataset. It does not establish the best five strategies and does not test current execution conditions.

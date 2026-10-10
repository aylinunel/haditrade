# Brokerage choice for EUR 500, assuming Netherlands residency

Official sources checked 2026-10-10. Netherlands residency is an unconfirmed planning assumption; eligibility and costs must be rechecked for the actual country. This repository has no brokerage credentials,
account linking, live order adapter or account eligibility verification. It runs
paper research only. Listed capabilities are not a promise of account approval.

## Recommendation

Use **Interactive Brokers Ireland as the future securities broker** and **Kraken
Pro as the crypto research venue**. Start with a shared EUR 500 paper portfolio;
do not split EUR 500 into five independently funded live bots. These are selected
for programmatic interfaces and broad access, not because frequent trading of a
EUR 500 portfolio has demonstrated positive after-cost returns.

| Market | Future venue | Feasibility at EUR 500 | Repository behavior |
| --- | --- | --- | --- |
| Stocks and ETFs, long | IBKR cash account | Account minimum is zero; instrument prices, trading permissions, settlement and commissions still constrain trades | Research and paper proposals; minimum order and commission checks |
| Stocks and ETFs, short | IBKR margin | Europe margin account minimum is EUR 2,000; cash accounts do not support shorts | Paper only with explicit borrow and short support metadata |
| Crypto spot, long | Kraken Pro | Small orders possible subject to pair-specific minimums; commission and spread can dominate | Paper research; configure the actual current fee tier |
| Crypto short/margin | Kraken Pro where eligible | Netherlands is not named among the general margin geographic exclusions; verified-account restrictions and product terms still apply | Paper only; require borrow availability and carrying costs |
| Forex | IBKR or Kraken fiat pairs for supported research | Unleveraged currency conversion differs from leveraged FX; pair availability, quote size and settlement must be validated | Research only; no leveraged orders |
| Listed options | IBKR subject to permission | Contract multipliers, premium and permissions vary; a cheap option is not a medium-risk substitute for a short stock | Proposals rejected until a payoff-aware risk model exists |
| Futures and CFDs | IBKR; Kraken Derivatives for eligible EEA crypto users | Margin, contract size and overnight losses can consume a small account; not enabled for live execution | Intraday backtests reject these until contract-aware models exist; standalone sizing can inspect linear-contract metadata only |

IBKR individual account minimum/inactivity fee are USD 0. Europe stock margin
requirements explicitly list EUR 2,000 minimum and no cash-account shorting.

- [IBKR required minimums](https://interactivebrokers.ie/en/accounts/required-minimums.php)
- [IBKR Europe stock margin](https://www.interactivebrokers.ie/en/trading/margin-stocks.php?ex=eu&hm=eu&pm=0&rgt=1&rsk=1&rst=121204040808080808)

Kraken's general margin eligibility page says most verified clients outside the
US, UK, Canada and Australia can access margin, subject to restrictions. Its EEA
derivatives requirements separately require full verification, an appropriateness
questionnaire and tax identification number. Personal eligibility has not been
checked, and no effort is made to bypass a broker's assessment.

- [Kraken margin eligibility](https://support.kraken.com/articles/4402532394260-client-eligibility-for-margin-trading-services-)
- [Kraken EEA derivatives requirements](https://support.kraken.com/articles/derivatives-eligibility-requirements-eea)
- [Kraken EEA questionnaire](https://support.kraken.com/articles/questionnaire-for-eea-clients)
- [IBKR CFD margin, including retail-specific constraints](https://www.interactivebrokers.ie/en/trading/margin-cfd.php?ex=eu&hm=eu&pm=0&rgt=0&rsk=1&rst=040404040404040104)

## Costs and the small-account constraint

Kraken updated its fee tiers on July 9, 2026. Tier 1 spot fees are **0.40% maker
and 0.80% taker per side**. A EUR 100 round-trip costs approximately EUR 0.80 if
both fills earn maker pricing, or EUR 1.60 if both are taker fills, before spread,
slippage, carrying costs and any currency conversion. Maker orders are not assured
fills. Historical tutorials quoting 0.25%/0.40% should not be used as current fee
assumptions.

[Official Kraken July 2026 fee table](https://support.kraken.com/articles/cross-platform-fee-tier-changes)

For Netherlands stocks, IBKR shows a EUR 1.25 tiered minimum per order (plus
relevant third-party fees), or EUR 3 fixed SmartRouting minimum. EUR 2.50 minimum
round-trip commissions equal the entire EUR 2.50 initial trade-risk budget before
price movement. This inference explains why the guard rejects many European
stock trades even when the account itself can be opened. US market fees and FX
must be modeled separately rather than borrowing the Dutch schedule.

[Official IBKR commissions, Netherlands section](https://www.interactivebrokers.ie/en/pricing/commissions-stocks.php)

## Hard defaults and sizing contract

The trader uses frozen policy defaults: EUR 500 capital, 0.5% risk per trade
(EUR 2.50), 1.5% daily loss threshold (EUR 7.50), 8% drawdown halt (EUR 40 from
the initial peak), 20% per-position cap (EUR 100 initially), three simultaneous
positions, 100% gross exposure maximum, no leverage, and at most 48 hours holding.
A profitable day does not increase the fixed initial-capital risk ceilings.

Sizing includes estimated stop loss **and round-trip fees/spread/slippage/FX and
carrying costs**, rounds down to the instrument's quantity step, and reserves the
risk already committed to open positions. Proposal confidence does not become a
probability of profit. Approval requires positive estimated net edge backed by at
least 20 observations and an explicitly validated source. This minimum is an input
gate, not proof that an estimator or strategy is statistically reliable.

All `*_pct` and annual rate fields are **fractions**, so `0.01` means 1%. `*_bps`
fields are basis points. Money is EUR; price times FX times contract multiplier
converts instrument quantity to EUR notional. Options require nonlinear payoff
analysis and are currently rejected by sizing. Shorts require explicit support,
borrow availability, borrow cost and funding cost even in simulation.

A stop loss and a paper risk calculation cannot guarantee a maximum realised loss
when markets gap, liquidity vanishes, or execution fails. A 48-hour exit is a
policy instruction; actual execution at that instant is not guaranteed during a
market closure. The trader detects already overdue positions and blocks new
entries; a future scheduler must route exits through a separate execution adapter.

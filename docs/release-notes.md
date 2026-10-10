# Version 1.0: four-agent paper trader, live feed and Strategy City website

- **Researcher evidence:** the quant researcher can now earn eligibility from an empirical, multiple-testing-adjusted edge book built on matured, non-overlapping fresh-signal outcomes. A promoted champion must also agree.
- **Paper ledger:** persistent, with next-open fills, adverse-gap stops, 48-hour exits, borrow accrual and a drawdown halt that needs human acknowledgment.
- **Market profiles:** per-instrument costs and sessions drive both the label costs and the trader's sizing.
- **Settings:** risk can be tightened only below the hard caps, and every change is journaled. The skeptic's conflict policy (`evidence` or `strict`) and shorting are configurable.
- **Runtimes:** an isolated replay sandbox runs on a year of real Binance 15-minute bars. The live loop polls public Binance klines, optionally adds Alpaca IEX for SPY and QQQ, and converts with point-in-time EURUSDT FX.
- **Narration:** optional Claude narration, which cannot change decisions, with a template fallback.
- **Website:** React with a React Three Fiber city, Lightweight Charts, the indicator lab, model explainer, edge heatmap, ledger, backtest library (quant_backtests) and controls, all streamed over an authenticated WebSocket.
- **Exports:** Freqtrade and QuantConnect/LEAN, tested to produce identical signals.
- **Mobile:** the Expo app gains a live agent tab.
- **Performance:** signal and validation code was vectorized without changing results (verified against the original on real bars). Replay runs at about 14 bars per second.
- **Validation:**
  - 110 backend tests pass, and the legacy reports regenerate identically;
  - web typecheck, lint and build pass, plus a headless browser pass;
  - mobile typecheck and lint pass, and the Android and iOS bundles export.

# Version 0.2: shared EUR 500 intraday research

The repository now separates the requested intraday system from its earlier daily evidence. Five long/short candidates share EUR 500, use completed 15-minute bars and have a 48-hour holding horizon with explicit market-gap handling. A read-only native report explains provisional risk defaults, agent roles and supported markets. No website or interactive visualization is added.

Four separate software agents provide implementation checks, bounded learning, independent skepticism and portfolio sizing. Every opinion is audited, including rejected hypotheses. Model probability cannot override missing expected-return evidence or immutable portfolio limits. The API automatically learns from matured outcomes as new authorized completed bars arrive, persists state and retains a frozen champion. There is no active external feed, LLM provider, broker connection or live order submission.

Validation completed:

- 90 backend tests passed, including integration tests for the four-role learning route, persistence, duplicate bars, causal inputs, fees, shorts, shared risk, 48-hour gaps and promotion gates.
- TypeScript and Expo ESLint passed.
- Android and iOS Hermes JavaScript exports passed, approximately 1.5 MB each. Device rendering and signed binaries are not verified.
- All three legacy daily backtests regenerated identically; their checked-in results are unchanged.

| Legacy daily ORCL window | Portfolio return | Buy and hold | Portfolio maximum drawdown |
| --- | ---: | ---: | ---: |
| 2003–2007 | 19.77% | 105.99% | 12.59% |
| 2008–2009 | 2.72% | 8.56% | 17.35% |
| 2010–2014 | 22.59% | 82.00% | 13.74% |

Those long/cash USD 10,000 sleeve tests underperform their benchmark in every window. They do not test the new EUR 500 intraday profile or online model. New intraday unit cases use synthetic data only; there are no fabricated recent-market backtests.

Broker reasoning is documented with official sources in `docs/broker-options.md`. IBKR is recommended for eventual securities integration and Kraken Pro for crypto research, with account eligibility and country unconfirmed. At EUR 500, paper research is the initial mode: EU IBKR stock shorting needs a margin account with a EUR 2,000 minimum, and commissions can exhaust a small trade-risk budget.

All requested markets are retained in planning. Stocks/ETFs/crypto have linear paper models; forex and derivative contracts require additional data and instrument-aware models. Calibrated expected-return evidence remains missing, so the integrated team deliberately rejects trading eligibility. This is a research foundation, not a running profitable bot.

Known validation warning: the installed Starlette test client emits an upstream deprecation notice for HTTPX 0.28.1. All tests pass; replacing that client dependency should be assessed with the framework's compatibility guidance before upgrading the pinned stack.

# Four agent roles and continuous learning

The Quant Researcher is an executable bounded online logistic model. The other three roles evaluate implementation/data validity, portfolio risk and proposed paper execution through explicit rules. These are separate responsibilities, not four autonomous LLMs: no model API keys, LLM calls, hidden deliberations or live broker submission are configured. Evidence describes observed inputs and concrete acceptance/rejection reasons.

`backend/learning.py` implements learning as market observations arrive. It does not fabricate a feed, run a perpetual background service when the API is stopped, or assume that a TrendSpider subscription grants a historical REST data API. Continuous learning requires the service to run and authorized, timestamped market data to arrive. A checkpoint must be persisted after events to survive process restarts.

## What the model learns

For each timestamped long or short proposal, the learner predicts the chance of a positive signed return after supplied total round-trip costs at a nominal 48-hour horizon. This label does not model the actual execution path, stop orders, bid/ask liquidity, portfolio sizing or an options payoff. It therefore cannot prove that the strategy, let alone a €500 live account, will earn money.

Inputs are proposal confidence, long/short side, stop distance expressed as a fraction (`0.01` means 1%), explicitly supplied trailing momentum/volatility/volume features, and fixed strategy indicators for `sma`, `rsi`, `bollinger`, `atr_breakout`, and `heikin`. The upstream producer is responsible for using completed bars only when calculating trailing features. No future-return input is accepted. Missing optional features are zero, explicitly disclosed in evidence, and no market history is inferred.

The shared input clock never goes backwards across symbols. A row is transformed with previous running mean/variance before the normalizer incorporates that row. Its transformed training features are saved in a pending record. The outcome is unavailable until at least 48 real elapsed hours later; the first next valid quote supplies its label. Market closures can delay this beyond 48 hours, and the result reports the actual holding period. No interpolation or forward-filling is used. The outcome costs must include both commissions, adverse slippage/spread and applicable borrow/funding and FX charges in basis points of notional, derived from the applicable product and venue.

The learner performs bounded, regularized online logistic updates with NumPy. Its success probability is not calibrated. The reported confidence is a transparent heuristic based on training count and probability distance from 0.5. The decision layer must reject absent independently supported positive expected return after costs. A large success probability does not imply a positive expectation because wins and losses may differ in size. In the research-only `ResearchService` the researcher therefore returns `eligible=False`, `edge_supported=False` and no invented expected-advantage number. The v1.0 `TradingRuntime` supplies that missing magnitude evidence empirically, through the edge book described below; probability alone still never approves a trade.

## Trainer, shadow challenger and frozen champion

The neutral bootstrap champion returns 0.5 and zero confidence. Training updates never alter the deployed champion or its normalizer. After 100 matured labels, `start_shadow()` explicitly snapshots the trainer and its normalizer as a frozen challenger. Both champion and challenger make predictions prospectively before labels are revealed. Continuing trainer updates change neither frozen model.

Promotion considers only proposals observed after the shadow epoch began. At most one globally nonoverlapping observation interval is counted, conservatively excluding overlapping 48-hour positions across all symbols and strategies. Learning may use the other matured labels, but promotion sample size may not count them. The order in which records are accepted is chronological operational order, not a search for the best realized trade.

All the following paper promotion gates must pass:

- At least 100 initial matured training labels before staging.
- At least 60 prospective, globally nonoverlapping shadow samples.
- At least 60 elapsed days between the first shadow observation and last label. Nonoverlapping 48-hour samples usually require roughly 120 days or more.
- At least 30 challenger proposals above the predeclared probability threshold of 0.55.
- A positive mean signed return after all supplied costs.
- Mean return improvement over the frozen champion of at least 5 basis points per accepted observation.
- A positive approximate 95% lower bound on the mean improvement.
- No active drift flag.
- An explicit `promote(timestamp, reviewed=True)` call after reviewing the prospective report.

The statistical lower bound is a normal approximation, not a guarantee, a full multiple-testing correction or proof of independent returns. Nonoverlap does not remove regime dependence. Running many shadow epochs creates selection risk and requires separate untouched validation before live use. Gates and threshold must be fixed before a shadow epoch, not tuned after inspecting results. The current gate is a conservative research scaffold, not a live allocation approval.

## Drift, restart and persistence

The module flags unusually large feature deviations and a change of at least 40 percentage points between two consecutive 20-label success-rate windows. These are transparent heuristics, not sophisticated drift tests. A drift flag pauses promotion. After investigation, explicit `start_shadow(..., acknowledge_drift=True)` begins a new epoch and discards the old shadow scorecard. It does not erase the champion, trainer or audit history. New prospective gates must pass again.

`save(path)` writes an atomic JSON checkpoint containing versioned feature schema, frozen models, normalizers, pending unlabeled records, prospective predictions, chronological clocks and lifecycle audit records. `load(path)` restores it. Checkpoints include research state, not credentials. Use one writer per checkpoint and one service worker or an external serialized event dispatcher; concurrent mutation of a learner instance is unsupported.

The learner does not retain the full market feed. Pending proposals and audit/scorecard state can grow, so a production deployment needs a durable event ledger and retention policy. Persistence of state is necessary for continuous learning; a running process alone is insufficient.

## Reproducible verification

Run `python -m pytest backend/test_learning.py -q` from the repository root after installing `backend/requirements.txt`. Tests cover unrevealed labels, cost-adjusted short outcomes, past-only transforms, cross-symbol hindsight rejection, duplicate and malformed input, checkpoint equivalence, minimum sample gates, immutable challenger/champion, overlap exclusion, explicit paper promotion, cost/drift blocking and delayed quotes. The deterministic synthetic unit cases verify lifecycle behavior; they are not market backtests or evidence of trading profitability.

## Empirical edge book (v1.0)

`backend/edge.py` measures after-cost expectancy directly from matured outcomes, instead of inferring it from a probability:

- **Buckets:** one per (strategy, side, asset class), so 10 per asset class.
- **Samples:**
  - only outcomes of *fresh* signals count;
  - at most one non-overlapping 48-hour sample per bucket and symbol;
  - a rolling window of 120 samples (configurable) lets old regimes fade;
  - samples are recorded only after they mature, so every estimate uses strictly past information.
- **Support:** requires at least 30 samples (configurable, minimum 20) and a positive one-sided lower bound of the mean *net* return after the current cost estimate. The z value is Bonferroni-adjusted across all buckets with enough samples (family α = 5%).

Limitations: BTC, ETH and SOL outcomes are correlated, and the normal approximation is crude for fat tails. A rolling window can also turn support on and off as regimes change. The bound is a screen against acting on noise, not a guarantee. On real October–December 2025 bars, short-side buckets became supported in late November and later lost support; see the README.

## Replay sandbox and live loop

`backend/replay.py` feeds a year of real Binance 15-minute bars one completed bar at a time. Its simulated receipt clock runs one second after each close, so every freshness gate behaves as it does live. The sandbox writes to its own directory and can be reset from the website. `backend/live.py` polls public Binance klines (and optionally Alpaca IEX), drops late bars that would move the shared clock backwards, and catches up on bars missed while stopped. Both feed the same `TradingRuntime` code path.

## Drift heuristic note

Labels mature in correlated batches (several strategies on one bar). The inherited 40-percentage-point swing heuristic therefore raises the drift flag readily. The flag only pauses promotion; it does not block trading. Review it and acknowledge it when staging the next shadow epoch.

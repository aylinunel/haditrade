# Four-agent research and paper-decision team

This implementation runs four separate logical roles in Python. These are deterministic evaluators with explicit interfaces, not four remotely hosted LLMs. No broker order is sent. Each role emits concise reasons and evidence; the journal records the decision, not private reasoning traces.

| Role | Responsibility | Output and authority |
| --- | --- | --- |
| Quant developer (`developer`) | Produce causal signals from complete market bars; validate strategy and proposal structure. | Proposal and implementation evidence. Cannot set portfolio limits or override evidence gates. |
| Quant researcher (`researcher`) | Evaluate labelled 48-hour outcomes; monitor calibration/drift; train and compare a separate challenger. | Research evidence, model version, supported estimates when actually available. Confidence alone cannot establish expected profit. |
| Senior decision maker/skeptic (`senior skeptic`) | Independently reject stale/invalid data, conflicting directions, unsupported shorts, and unsupported/nonpositive expected advantage after costs. | Mandatory approval or explicit rejection. A positive forecast cannot override missing evidence. |
| Quant trader (`trader`) | Apply the account's fixed capital, position, loss, drawdown, instrument, cost and borrowing constraints. | Final risk veto and a sized paper proposal. Cannot execute live trades. |

The roles share a proposal, not credentials. The decision team passes deep copies so a role cannot change the original market snapshot or another role's decision. Missing evaluators, evaluator failures, missing empirical advantage and unknown costs all fail closed. The current research model must not be described as profitable or live eligible merely because it runs.

## Contract

`DecisionTeam(journal, developer, researcher, trader)` accepts objects with `evaluate(context) -> mapping`. The context contains `proposal`, `market_state`, `portfolio`, `peer_proposals`, timezone-aware `now`, and `mode='paper'`. Researcher also receives the developer output. Trader receives the recorded developer, researcher and skeptic outputs. Existing role methods with different signatures can be wrapped using small adapters; the supplied estimates must retain their provenance.

Proposal fields: `symbol`, `strategy_id`, `side` (`long` or `short`), `confidence` (0–1), `entry_reference_price`, `stop_distance_pct` (fraction, so 0.01 means 1%), `evidence`, and ISO 8601 timezone-aware `observed_at`. Confidence is an indicator/model confidence, never a probability of profit unless separately calibrated and labelled as such.

The market snapshot needs `data_valid=True` and a timezone-aware `observed_at`. Shorts additionally need both `short_supported=True` and `borrow_available=True`. The default data-age gate is 120 seconds with a maximum five-second future clock skew. This concerns freshness of the current snapshot, not the age of historical lookback bars or the 48-hour strategy horizon.

Research approval requires `eligible=True` (or `approved=True`), `edge_supported=True`, finite `expected_advantage_bps` (gross expected trading advantage), and finite nonnegative `roundtrip_cost_bps`. The net advantage must be strictly positive. The cost estimate must include fees, spreads, expected slippage, FX and financing/borrow costs as applicable. Unknown estimates are rejected. A raw signal or LLM opinion cannot supply those fields as invented numerical evidence.

Trader approval requires `approved=True`; all portfolio checks remain its independent responsibility. Trader risk analysis is recorded even on proposals vetoed by the skeptic, but its order and approval are overridden to rejected. The team records `paper_only_approved` only when both skeptic and trader approve. This means a paper proposal, not an execution or fill. In batch evaluation only one proposal may be approved until transactional capital reservations and position netting are implemented; this avoids duplicate cash allocation. Opposite proposals for the same symbol are both rejected.

## Continuous learning

Continuous observation does not mean continuous deployment. Research outcomes should be labelled only after their 48-hour horizon matures, with the actual applicable costs included. Forecasts must be made before labels become available. Save samples and labels, train challengers separately, and keep the active champion frozen while a challenger is prospectively evaluated.

A challenger can become eligible only under the research module's independent sample, chronological holdout, cost and drift checks. Eligibility is a prerequisite for a separately reviewed promotion, not automatic authorization. The system must record model version, training cutoff, evaluation period, sample count and promotion reason. Hard portfolio limits, broker permissions, production strategy code and live-trading mode cannot be rewritten by learning agents.

The old daily ORCL report remains a historical research baseline. It does not validate this new intraday, long/short, 48-hour workflow across all assets. Stocks, crypto, forex and options require separate data quality checks, market hours, lot sizes, spreads, financing and contract accounting. A shared interface does not establish suitability or broker availability for a €500 account.

## Durable decision journal

`DecisionJournal` stores each complete four-role evaluation atomically in SQLite, with linked SHA-256 hashes. The record includes the sanitized proposal, each named role's explanations/evidence, final outcome, timestamp, model evidence and any proposed paper order. Readback verifies the hash chain. This detects edited rows; it does not prove authenticity or protect against an administrator rewriting the entire database.

Use a private local file outside Git, for example `var/decisions.sqlite`, with access limited to the backend. Sensitive credential field names are filtered and payload depth/size is bounded. Never place credentials in free-text reasons or evidence, because field-name redaction cannot detect arbitrary secrets embedded inside prose. API tokens belong only in server-side environment/secret storage. Durable journal failure rejects the proposal and exposes a short audit-error status; no order proceeds without the record.

The journal currently captures proposals and evaluation outcomes. Broker fills, realized positions and execution reconciliation must come from a separate authenticated broker/paper ledger integration; the team does not fabricate them.

## Version 1.0 trading runtime

`backend/runtime.py` (`TradingRuntime`) wires the same four roles to a persistent paper ledger, empirical edge evidence and per-instrument market profiles. Its hook methods replace only the research-only defaults of `ResearchService`; the ingest order, causality checks and atomic checkpointing are unchanged.

- **Which signals are evaluated:** only *fresh* signals, where a strategy's target changed on the latest completed bar, go to the four roles. All signals, fresh or persisting, are still observed by the learner.
- **Researcher eligibility (`EdgeResearcher`):** requires a supported edge-book estimate for the proposal's strategy, side and asset class. It returns `expected_advantage_bps` (mean gross return of matured samples) and `roundtrip_cost_bps` (the current profile cost for that side) together with the estimate's provenance. Once a champion has been promoted, its probability must also be at least the decision threshold.
- **Skeptic conflict policy:** a setting.
  - `evidence` (default): an opposite signal counts only if it is fresh on the same bar or has supported edge evidence.
  - `strict`: any active opposite signal counts.
  The skeptic code is unchanged; the policy only selects which peers it weighs.
- **Trader inputs (`TraderAdapter`):** the trader receives the researcher's expectancy and sample count. Its market snapshot comes from `backend/markets.py`, which covers fees, spread, slippage, FX, borrow, minimum order, quantity step and trading session.
- **Settings:** risk limits can only be tightened below `RiskPolicy`'s hard caps. Every change is appended to the journal as a `settings_change` record.
- **Paper orders:** approved orders are queued in `backend/ledger.py` and fill at the next bar's open. The ledger state is saved in the same atomic checkpoint as the learner; a failed checkpoint rolls back both.

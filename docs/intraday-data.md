# Intraday data and operational decisions

## Data contract

Both intraday routes accept a CSV string with these columns. Each symbol must be chronological; different symbols may be interleaved. Maximum batch: 10,000 rows, 50 symbols, 2 MB CSV. The learning workspace tracks at most 50 symbols and retains the latest 400 observed bars per symbol. Learning and audit records need an additional production retention policy.

| Column | Meaning |
| --- | --- |
| `timestamp` | Timezone-aware bar OPEN, aligned to 15-minute boundaries, e.g. `2026-10-09T10:00:00+00:00` |
| `symbol` | Stable instrument identifier |
| `open,high,low,close` | Actual quote-currency prices; positive and valid OHLC bounds |
| `volume` | Nonnegative observed volume; zero-volume bars cannot open positions |
| `fx_to_eur` | EUR value of one quote-currency unit, observed for that bar |
| `asset_class` | `stock`, `equity`, `etf` or `crypto`; contracts and forex rejected |

OHLC becomes usable 15 minutes after `timestamp`. The learning route rejects incomplete/future bars. The simulator generates a target from the completed previous bar and executes at the following available open, using actual prices rather than synthetic Heikin-Ashi prices. Sparse bars/market closures are not filled. Provider corporate actions, session calendars, exchange timestamps and adjustment policy must be documented by the source adapter; a CSV schema alone cannot certify licensed or accurate data.

## Learning route

Authenticated `POST /api/learning/bars` body:

```json
{
  "csv": "timestamp,symbol,open,high,low,close,volume,fx_to_eur,asset_class\n...",
  "source_label": "Name and version of your authorized feed",
  "roundtrip_cost_bps": {"YOUR_SYMBOL": 100}
}
```

The `100` is only a schema example, not a recommended fee assumption. Supply total entry plus exit commission, spread, slippage, applicable 48-hour borrow/funding and FX costs as basis points of notional. While a symbol has pending labels its cost assumption cannot change silently. Different instruments/venues require different assumptions. Full cost estimation at the actual proposed size is a separate trader responsibility.

This route processes new closes chronologically before queuing signals at the latest completed time. It never retrospectively queues every old historical bar after learning from its future outcome. A shared learner rejects backwards timestamps across symbols; asynchronously arriving older symbols must be held/reordered by a future feed adapter. Current proposals older than the latest processed outcome clock are skipped to avoid hindsight. These are conservative constraints, not a high-throughput event bus.

Trailing momentum is observed EUR close return from the first to last supplied bar within 48 elapsed hours. Volatility is sample standard deviation of observed close-to-close returns in that window, unannualized. Volume deviation uses the latest volume against preceding observed volume in that window. Partial history is not a full 48-hour pattern; closures are not treated as zero-return bars. Optional model features are causal observations, not a validated predictive edge.

Signals create delayed labels even if the trading decision is rejected, because research may learn from hypotheses without risking money. Entry references and outcome closes are converted to EUR so FX affects learning. The label is a signed 48-hour return after supplied costs; it does not model stop hits, actual entry fills, market liquidity or portfolio constraints. A neutral champion and lack of calibrated expectancy cause rejections. This is intentional, explained in all four recorded role opinions.

Checkpoint writes are atomic and serialized within one worker. A failed checkpoint leaves in-memory learning unchanged; rejected audit attempts may still be present in the separate journal. SQLite decision hash links detect some edits, not journal truncation or a malicious party rewriting the entire chain. There is no order submission on either successful or failed writes.

## Intraday backtest route

Authenticated `POST /api/backtests/intraday` body:

```json
{
  "csv": "timestamp,symbol,open,high,low,close,volume,fx_to_eur,asset_class\n...",
  "source_label": "Your authorized historical export",
  "commission_bps": 5,
  "minimum_commission_eur": 0,
  "slippage_bps": 5,
  "short_borrow_annual_pct": 0.03,
  "funding_annual_pct": 0,
  "allow_shorts": true
}
```

These defaults are transparent test assumptions, **not current IBKR or Kraken tariffs**. Change them to the correct instrument, venue and size. The engine uses one common cost model across the batch; separate batches are needed for distinct fee schedules until a per-instrument execution adapter is added. Policy `*_pct` fields are fractions (`0.005 = 0.5%`); reported return/drawdown metrics are percentage points. CSV/data hashes and source labels accompany results.

The simulator supports hypothetical fractional linear stock/ETF/crypto long/short positions, without proving actual borrow or account eligibility. It caps shared capital and risk, accounts for real elapsed carry, and discloses overdue exits, stale marks and terminal positions lacking an executable close. The stricter decision team requires verified shortability and supported expected returns; a backtest simulation is not the same as team approval.

## Promotion and deployment

`GET /api/learning/status` exposes counts, drift and gate reasons. `POST /api/learning/stage` accepts `{}` or an explicit drift acknowledgement after investigation. `POST /api/learning/promote` requires `{"reviewed":true}` and all prospective gates. Both act only on paper models, never risk policy or live execution.

A continuous deployment needs an authorized intraday feed adapter, verified instrument metadata/costs, event ordering, secure persistent storage and process supervision. None has been connected to an account here. The native app displays saved evidence offline; the server learns only while running and receiving accepted events. No website or interactive scene is needed for that pipeline.

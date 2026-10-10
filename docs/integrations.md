# Integration contract

## TrendSpider strategy-bot webhook

Set the Entry or Exit webhook URL to the exposed FastAPI route `/api/trendspider/webhook`. A supported entry body is:

```json
{
  "secret": "YOUR_SERVER_SIDE_WEBHOOK_SECRET",
  "strategy_id": "sma",
  "symbol": "%bot_symbol%",
  "action": "buy",
  "price": "%last_price%",
  "alert_name": "%bot_name%"
}
```

Use `sell` for an exit. For ordinary alerts use documented `%alert_symbol%` and `%alert_name%` variables instead of bot variables. Strategy IDs are `sma`, `rsi`, `bollinger`, `atr_breakout`, `heikin`, plus legacy `sar`. Prices must be finite and positive. The shared secret must match `TRENDSPIDER_WEBHOOK_SECRET`; it is excluded from stored event payloads. Events are saved in SQLite and appear in `/api/events` and WebSocket messages. They never alter historical P&L, train the OHLC learner or submit orders. An alert direction alone is not verified shortability.

An optional `event_id` supplies durable idempotency. The reviewed documentation does not provide an event timestamp or unique ID variable. Without an ID, identical payloads are deduplicated within a 30-second bucket only; a delayed retry may create another event. This is sufficient for a research inbox, not broker execution. A production execution gateway must add robust source authentication, durable unique IDs and stale-event checks before placing orders.

Documentation: https://help.trendspider.com/kb/alerts/webhooks
Catalog: https://trendspider.com/marketdata/

## Historical CSV import

`POST /api/backtests/import` accepts bearer-authenticated JSON:

```json
{
  "csv": "Date,Open,High,Low,Close,Volume\n...",
  "symbol": "SPY",
  "source_label": "Authorized user export",
  "start": "2020-01-01",
  "end": "2022-12-31",
  "capital": 10000,
  "fee_bps": 5,
  "slippage_bps": 5
}
```

Supply at least 260 preceding bars plus the requested interval, increasing unique dates, valid OHLC bounds and nonnegative volume. CSV content is capped at 2 MB. Supply data in a consistent split-adjusted price basis. Dividends are not modelled. The source label and content hash accompany results. This route does not fetch arbitrary URLs or claim that imported data came from TrendSpider.

## Deferred native visualization

Future 3D implementation should use the version-compatible React Three Fiber native renderer and Expo GL. R3F's React-major-version compatibility, native touch dispatch and performance documentation were reviewed. Interactive visualization is intentionally omitted from this version at the user's request. The original image attachments were unavailable, so no exact visual reproduction is claimed.

## Intraday and continuous research

The authenticated OHLC learning and intraday simulation contracts are documented in `docs/intraday-data.md`. The new profile adds a bounded online learner, durable checkpoints and four-role audit decisions. It does not turn TrendSpider alert prices into historical bars or grant external access to the market-data catalog. An authorized feed adapter remains necessary for continuous operational learning.

References: https://github.com/pmndrs/react-three-fiber and https://fastapi.tiangolo.com/advanced/websockets/

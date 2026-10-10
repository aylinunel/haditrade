# Strategy City: a four-agent AI paper-trading system

This merges **strategy-city** (four-role FastAPI research backend and Expo report) and **quant_backtests** (10 strategies across SPY, BTC, ETH, ES, GC and CL) into one runnable system. It has three parts:

- A trading agent that learns continuously from real market data.
- An interactive, self-updating 3D website.
- A mobile companion app.

**It is paper trading only.** There is no broker client, so no order can reach a real account. No result here is evidence of future profitability.

## The four agents

| Agent | Responsibility | Where |
| --- | --- | --- |
| Quant developer | Causal signals from completed 15-minute bars. It checks that each proposal reproduces, and flags *fresh* signals (where a strategy's target changed). | `backend/intraday.py` |
| Quant researcher | Learns from matured 48-hour outcomes. It runs an online logistic model with a frozen champion and shadow challenger, plus an **empirical edge book** (multiple-testing-adjusted lower bound after costs). It is eligible only when that evidence is positive. | `backend/learning.py`, `backend/edge.py`, `backend/runtime.py` |
| Senior decision maker / skeptic | Vetoes stale or invalid data, unsupported shorts, missing after-cost evidence and conflicting signals. Its `evidence` or `strict` conflict policy is configurable. | `backend/decision_team.py` |
| Quant trader | Cost-aware sizing within shared €500 limits: 0.5% trade risk including round-trip costs, 1.5% daily loss, 8% drawdown halt, 20% per position, 3 positions, no leverage. It also checks the market session. | `backend/risk.py`, `backend/markets.py` |

Every fresh signal goes through all four agents. Each opinion and rejection reason is written to a SHA-256 hash-chained SQLite journal. Approved orders become **paper** orders and go to a persistent ledger, `backend/ledger.py`:

- **Fills:** at the next bar's open, with adverse slippage and half the spread.
- **Stops:** fill at the worse of the stop price and the gap open.
- **Exits:** at 48 hours.
- **Borrow:** accrued on shorts.
- **Halt:** the drawdown halt liquidates positions and waits for a human to acknowledge it.

## How it learns (and why it usually says no)

1. Every proposal is stored with its features. Forty-eight hours later, the first completed close reveals the label: the signed EUR return after the symbol's all-in round-trip cost. Nothing is trained before the outcome is known.
2. The **trainer** model updates online. The **champion** that influences decisions stays frozen until a staged challenger passes prospective gates and a human promotes it:
   - at least 60 non-overlapping samples over at least 60 days;
   - at least 30 challenger trades;
   - a positive net result;
   - an improvement of at least 5 bps with a positive 95% lower bound;
   - no drift flag.
3. The **edge book** keeps, for each (strategy, side, asset class), at most one non-overlapping 48-hour outcome per symbol from *fresh* signals. A bucket becomes tradable only when its rolling mean net return has a positive one-sided lower bound, using a Bonferroni-adjusted z across all tested buckets. Evidence that decays switches trading off again on its own.

Observed on 84 days of real Binance bars (Oct to Dec 2025, BTC/ETH/SOL, replay sandbox):

- **Before support:** nothing traded for seven weeks.
- **Support:** in late November, the short buckets for SMA trend and RSI reversal reached positive lower bounds.
- **Trading:** the agents approved 18 paper trades.
- **Stop:** they stopped again when the evidence faded.
- **Result:** €500.00 became €503.44 after €3.84 of fees and borrow, with 3 winning trades out of 18.

That is one small, regime-dependent sample, not a performance claim.

## The website (`web/`)

React, Vite and TypeScript. It is served by FastAPI at `/` after `npm run build`, or run with `npm run dev`.

- **3D Strategy City** (React Three Fiber): one tower per symbol and strategy.
  - **Height:** grows with matured labels.
  - **Glow:** the windows light up when edge evidence is supported.
  - **Signals and positions:** ▲/▼ beacons show live signals, and rings show open paper positions.
  - **Agent plaza:** every decision animates through the four agents' buildings and stops at the one that rejected it. Hover a tower for evidence; click it to inspect.
- **Price & signals:** TradingView Lightweight Charts, with the selected strategy's inputs (SMAs, Bollinger bands, ATR range, RSI pane, Heikin-Ashi), fresh-signal arrows and paper entries and exits.
- **Indicator lab:** an AlgorithmVisualizer-style step-through of each strategy's state machine. It has play, pause, speed and scrub controls, highlights the lookback window and evaluates the rule live.
- **Learning:**
  - a diverging heatmap of the edge book;
  - a strip of recently matured labels;
  - a CNN-Explainer-style model explainer: features × weights → logit → sigmoid, with editable inputs and live weight sparklines;
  - promotion gates with reviewed stage and promote buttons.
- **Paper ledger:** equity curve, risk-utilization meters, positions, per-strategy results and trades.
- **Backtest library:** your quant_backtests results as a Sharpe-versus-buy-and-hold heatmap with drill-down growth curves, plus the legacy ORCL windows.
- **Controls:**
  - replay start, pause, step, reset and speed;
  - live feed start and stop;
  - **risk sliders that can only tighten the hard caps**, the shorts toggle, conflict policy and edge thresholds (every change is journaled);
  - Freqtrade and LEAN exports.

The site updates itself through an authenticated WebSocket (`/ws/agent`) as bars arrive. Dark and light themes, phone width (400 px) and keyboard focus are supported.

## Run it

```bash
# backend (one worker)
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
export STRATEGY_API_TOKEN='a-long-random-token'
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --workers 1

# then open http://127.0.0.1:8000 and sign in with your token
```

The built website ships in `web/dist/`, so Node.js is **not** needed to run it. To change the frontend, install Node.js 22+ (`brew install node` on macOS), then run `cd web && npm ci && npm run build`, or `npm run dev` for hot reload at http://localhost:5173 (it proxies `/api` and `/ws` to :8000). CI fails if the committed build is out of date with the source.

In the site, open **Controls → Run replay** to watch the agents learn on a year of real BTC, ETH and SOL bars (Oct 2025 to Oct 2026). Replay speed goes up to about 14 bars per second, so a month replays in roughly 3.5 minutes. Switch to **Live paper → Start live paper trading** to poll Binance's public klines every 10 seconds; no key is needed. To add SPY and QQQ, set `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY` from your own free Alpaca data account. Set `TRADING_LIVE_AUTOSTART=1` to start the feed on boot.

Optional plain-English narration of decisions by Claude:

```bash
pip install -r backend/requirements-optional.txt
export TRADING_NARRATOR=claude ANTHROPIC_API_KEY=...
```

Narration is opt-in, cached per decision and stored outside the journal. It cannot change any number or verdict. It uses `claude-opus-5-5` with server-side refusal fallbacks enabled, and `TRADING_NARRATOR_MODEL` overrides the model. A deterministic template is used otherwise.

Mobile (`apps/mobile`): `npm ci && npm start`. The **Live agent** tab connects to your backend (the token is held in memory only). **Saved report** is the original offline report.

## Data and assumptions

- **Replay:** `data/replay/*.csv.gz` holds Binance 15-minute klines via [finom/static-klines](https://github.com/finom/static-klines), rebuilt with `scripts/build_replay_data.py`. USDT converts to EUR at a constant 0.86, so FX risk is not simulated in replay.
- **Live:** Binance public market data (read-only), with point-in-time EURUSDT as the USD/USDT→EUR proxy. Alpaca IEX is optional.
- **Costs** (`backend/markets.py`) are editable assumptions, not broker quotes:
  - crypto: 10 bps per side, 1–2 bps spread and 3–5 bps slippage, about 32 bps round trip at 48 hours including 10–12% annual borrow for paper shorts;
  - ETFs: about 9 bps, never shorted.
- **Shorting crypto** assumes margin borrow is available. Turn it off in Controls. US-equity sessions ignore holidays.
- Futures, forex, options and CFDs are not traded: they need contract-aware models.

## Strategy exports

`exports/freqtrade/FourAgentResearchStrategy.py` and `exports/lean/{main,signals}.py` reproduce the five signals, ATR stops and 48-hour exits. A test checks that their targets are identical to the agent's on real bars. The skeptic, edge evidence and shared-risk gates are **not** in the exports. Use them to backtest the raw signals in Freqtrade or QuantConnect, and see each file's header for setup.

## Verification

```bash
python -m pytest backend -q            # 110 tests
python -m backend.run_backtests        # legacy ORCL reports regenerate identically
cd web && npm run typecheck && npm run lint && npm run build
cd apps/mobile && npm run typecheck && npm run lint
```

The tests cover:

- causality, delayed labels and the frozen champion;
- the edge-book statistics;
- ledger fills, stops, borrow and halts;
- approval on real bars only with supported evidence, and veto when a promoted champion disagrees;
- strict versus evidence conflict policies and tighten-only settings;
- checkpoint rollback, and replay restart and reset;
- the live loop against a mocked exchange;
- authenticated API and WebSocket routes, narration fallback, and export equivalence.

The web UI was also driven in headless Chromium (dark, light and 400 px) with no console errors. Android and iOS JavaScript bundles exported. Device rendering is not verified.

## Structure

```text
backend/        FastAPI, four agents, learner, edge book, ledger, runtimes, live feed, replay, narrator, tests
web/            React + R3F + Lightweight Charts website
apps/mobile/    Expo app (live agent view + offline report)
exports/        Freqtrade strategy, QuantConnect/LEAN algorithm
research/       quant_backtests scripts/results, legacy ORCL reports
data/replay/    real 15-minute replay bars
docs/           agent contract, learning, web app, integrations, brokers
```

Licensing: the GPL-3.0-or-later research distribution (it includes a GPLv3 Backtrader fixture); notices are in `backend/third_party/`. Strategy logic is adapted from [je-suis-tm/quant-trading](https://github.com/je-suis-tm/quant-trading) (Apache 2.0). Charts use TradingView Lightweight Charts (Apache 2.0, attribution logo kept).

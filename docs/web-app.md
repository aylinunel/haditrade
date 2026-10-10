# Website and visualization design

The website (`web/`) is a read-and-control client for the FastAPI backend. It never holds market data of its own; every number comes from the agent runtimes. The API token stays in the tab's `sessionStorage` and is sent only to the serving origin. The WebSocket authenticates with its first frame, so tokens stay out of URLs.

## Self-updating

`/ws/agent` streams runtime events (bars ingested, decisions with the blocking agent, outcomes, equity, settings and lifecycle changes). Each event triggers an immediate refresh of the status, decision, model and ledger views. Polling (4–10 s) is the fallback. The city animates each new decision through the agent plaza, and towers grow as labels mature.

## How the referenced projects were used

The references were studied for interaction patterns; none of them is bundled as a dependency.

| Reference | What was taken | Where |
| --- | --- | --- |
| React Three Fiber (pmndrs) | Declarative Three.js scene, pointer events, `useFrame` animation, drei `OrbitControls`/`Html` tooltips | `components/City.tsx` |
| TradingView Lightweight Charts | Candlesticks, indicator overlays, RSI pane, series markers, baseline equity curve | `PriceChart.tsx`, `Ledger.tsx`, `Library.tsx` |
| AlgorithmVisualizer, SortVision, Visualizer, alg0.dev, AlgoFlow | Step-through playback (play/pause/speed/scrub), highlighting the active window, state-machine nodes, live rule narration | `IndicatorLab.tsx`, decision pipeline in `DecisionFeed.tsx` |
| CNN Explainer | Layer-by-layer explanation: input → weight → contribution → logit → sigmoid, with editable inputs | `Learning.tsx` (`ModelExplainer`) |
| c3, britecharts | Reusable chart composition and legends (implemented with Lightweight Charts and SVG instead of D3) | legends and heatmaps |
| fastplotlib | GPU-rendered live updates (the 3D city follows the same "render on data arrival" approach in WebGL) | `City.tsx` |
| QuantConnect/LEAN, Freqtrade/FreqAI | Export targets for the same signals; multi-model framework ideas (separate alpha, risk and execution roles) | `exports/` |
| Spline, Replit | Not used: the scene is generated from data and needs no visual editor; deployment is a plain FastAPI + static build | – |

## Color and accessibility

Colors follow a validated categorical palette, checked with the dataviz validator in both modes:

- **Strategies:** the five strategies use slots 1–5 in a fixed order (blue, orange, aqua, yellow, magenta).
- **Polarity:** long vs short and positive vs negative edge use a blue↔red diverging pair with a gray midpoint.
- **Status:** approved and rejected use the reserved good and critical status colors, always paired with ✔/✖ and a text label.
- **Light mode:** three slots fall below 3:1 contrast, so towers, legends and tables always carry visible text labels.

The 3D scene has an `aria-label`, and the same data is available in tables (Learning, Ledger). The theme toggle is applied before render so the WebGL scene repaints with the matching palette.

## Verified

`npm run typecheck`, `npm run lint` and `npm run build` pass. A Playwright pass in headless Chromium covered:

- login;
- the city, including hover tooltips;
- every tab;
- decision narration;
- the indicator lab;
- running the replay (the learned-label count increased live);
- light theme;
- 400 px width with no horizontal overflow.

No console errors occurred. The only console warning is a three.js deprecation emitted inside React Three Fiber.

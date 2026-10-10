"""FastAPI transport for the four-agent paper-trading runtime, research and alerts.

Two isolated agent runtimes: `live` (public read-only market data) and `replay`
(historical sandbox). Neither can place a broker order: no broker client exists.
TrendSpider webhooks are signals/quotes, never orders. Run ONE worker.
"""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import sqlite3
from typing import Literal
from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from .engine import build_report, load_bars, run_scenario
from .intraday import backtest_intraday, IntradayConfig
from .live import LiveLoop
from .narrator import Narrator
from .replay import ReplayDriver
from .research_service import ResearchService
from .runtime import TradingRuntime
from . import library
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

def authorize(authorization: str | None = Header(default=None)):
    expected = os.getenv('STRATEGY_API_TOKEN', '')
    if not expected: raise HTTPException(503, 'Configure STRATEGY_API_TOKEN before connecting')
    supplied = (authorization or '').removeprefix('Bearer ')
    if not hmac.compare_digest(supplied, expected): raise HTTPException(401, 'Invalid API token')

def event_db():
    path = Path(os.getenv('STRATEGY_EVENT_DB', str(ROOT / '.runtime' / 'events.sqlite')))
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, received TEXT, payload TEXT)')
    return con

def read_events():
    with event_db() as con:
        return [json.loads(r[0]) for r in con.execute('SELECT payload FROM events ORDER BY received DESC LIMIT 20')]

class Control(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['play', 'pause', 'seek', 'scenario']
    index: int | None = Field(default=None, ge=0)
    scenario_id: Literal['expansion', 'crisis', 'recovery'] | None = None

class TrendSpiderEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    secret: str = Field(min_length=16, max_length=256)
    strategy_id: Literal['sma', 'rsi', 'bollinger', 'sar', 'atr_breakout', 'heikin']
    symbol: str = Field(min_length=1, max_length=32, pattern=r'^[A-Za-z0-9._:/-]+$')
    action: Literal['buy', 'sell', 'alert']
    price: float = Field(gt=0, allow_inf_nan=False)
    alert_name: str = Field(default='', max_length=120)
    event_id: str | None = Field(default=None, max_length=128)

class CSVResearch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    csv: str = Field(max_length=2_000_000)
    symbol: str = Field(min_length=1, max_length=32, pattern=r'^[A-Za-z0-9._:/-]+$')
    source_label: str = Field(min_length=1, max_length=120)
    start: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    end: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    capital: float = Field(default=10000, gt=0, le=100000000, allow_inf_nan=False)
    fee_bps: float = Field(default=5, ge=0, le=500)
    slippage_bps: float = Field(default=5, ge=0, le=500)

class IntradayResearch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    csv: str = Field(min_length=20, max_length=2_000_000)
    source_label: str = Field(min_length=1, max_length=120)
    commission_bps: float = Field(default=5, ge=0, le=500, allow_inf_nan=False)
    minimum_commission_eur: float = Field(default=0, ge=0, le=100, allow_inf_nan=False)
    slippage_bps: float = Field(default=5, ge=0, le=500, allow_inf_nan=False)
    short_borrow_annual_pct: float = Field(default=.03, ge=0, le=10, allow_inf_nan=False)
    funding_annual_pct: float = Field(default=0, ge=0, le=10, allow_inf_nan=False)
    allow_shorts: bool = True

class LearningBars(BaseModel):
    model_config = ConfigDict(extra='forbid')
    csv: str = Field(min_length=20, max_length=2_000_000)
    source_label: str = Field(min_length=1, max_length=120)
    roundtrip_cost_bps: dict[str, float] = Field(min_length=1, max_length=50)

class LearningReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reviewed: bool = False
    acknowledge_drift: bool = False

class Replay:
    def __init__(self, report):
        self.report, self.scenario_index, self.index, self.playing = report, 0, 0, False
    @property
    def scenario(self): return self.report['scenarios'][self.scenario_index]
    def state(self):
        return dict(mode='historical_replay', scenario_id=self.scenario['id'], index=self.index,
                    playing=self.playing, snapshot=self.scenario['snapshots'][self.index],
                    trendspider_events=read_events())
    def control(self, command):
        if command.action == 'scenario':
            if command.scenario_id is None: raise ValueError('scenario_id required')
            self.scenario_index = next(i for i, s in enumerate(self.report['scenarios']) if s['id'] == command.scenario_id)
            self.index, self.playing = 0, False
        elif command.action == 'seek':
            if command.index is None or command.index >= len(self.scenario['snapshots']): raise ValueError('index outside scenario')
            self.index = command.index
        elif command.action == 'play':
            if self.index == len(self.scenario['snapshots']) - 1: self.index = 0
            self.playing = True
        else: self.playing = False
        return self.state()
    async def clock(self):
        while True:
            await asyncio.sleep(.75)
            if self.playing:
                self.index = min(self.index + 1, len(self.scenario['snapshots']) - 1)
                if self.index == len(self.scenario['snapshots']) - 1: self.playing = False

@asynccontextmanager
async def lifespan(app):
    path = ROOT / 'research' / 'backtests.json'
    report = json.loads(path.read_text()) if path.exists() else await asyncio.to_thread(build_report)
    app.state.replay = Replay(report)
    app.state.research = ResearchService(os.getenv('STRATEGY_RESEARCH_DIR', str(ROOT / '.runtime' / 'research')))
    agent_dir = Path(os.getenv('TRADING_RUNTIME_DIR', str(ROOT / '.runtime' / 'agent')))
    app.state.live_runtime = TradingRuntime(agent_dir / 'live', name='live')
    app.state.live = LiveLoop(app.state.live_runtime, poll_seconds=float(os.getenv('TRADING_POLL_SECONDS', '10')))
    app.state.sandbox = ReplayDriver(lambda d: TradingRuntime(d, name='replay', checkpoint_every=96), agent_dir / 'replay')
    app.state.narrators = {}
    if os.getenv('TRADING_LIVE_AUTOSTART') == '1':
        app.state.live.start()
    task = asyncio.create_task(app.state.replay.clock())
    yield
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass
    await app.state.live.stop()
    await app.state.sandbox.pause()

app = FastAPI(title='AI Trading Agent (paper research) API', version='1.0.0', lifespan=lifespan)
app.add_middleware(CORSMiddleware,
                   allow_origins=[s.strip() for s in os.getenv('STRATEGY_ALLOWED_ORIGINS', 'http://localhost:3000,http://localhost:5173').split(',')],
                   allow_methods=['GET', 'POST', 'PUT'], allow_headers=['Authorization', 'Content-Type'])

@app.get('/health')
def health(): return dict(status='ok', execution='disabled', mode='research', broker_connected=False)

@app.get('/api/report', dependencies=[Depends(authorize)])
def report(): return app.state.replay.report

@app.get('/api/state', dependencies=[Depends(authorize)])
def state(): return app.state.replay.state()

@app.get('/api/events', dependencies=[Depends(authorize)])
def events(): return read_events()

@app.get('/api/learning/status', dependencies=[Depends(authorize)])
def learning_status(): return app.state.research.status()

@app.get('/api/profile', dependencies=[Depends(authorize)])
def profile():
    return json.loads((ROOT / 'apps/mobile/assets/research-profile.json').read_text())

@app.get('/api/decisions', dependencies=[Depends(authorize)])
def decisions():
    return app.state.research.journal.records()[-100:]

@app.post('/api/learning/bars', dependencies=[Depends(authorize)])
async def learning_bars(request: LearningBars):
    try:
        frame = pd.read_csv(io.StringIO(request.csv))
        return await asyncio.to_thread(app.state.research.ingest, frame, request.source_label, request.roundtrip_cost_bps)
    except (ValueError, TypeError, KeyError) as exc: raise HTTPException(422, str(exc)) from exc

@app.post('/api/learning/stage', dependencies=[Depends(authorize)])
def learning_stage(request: LearningReview):
    try: return app.state.research.stage(acknowledge_drift=request.acknowledge_drift)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc

@app.post('/api/learning/promote', dependencies=[Depends(authorize)])
def learning_promote(request: LearningReview):
    try: return app.state.research.promote(reviewed=request.reviewed)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc

@app.post('/api/backtests/intraday', dependencies=[Depends(authorize)])
async def intraday_backtest(request: IntradayResearch):
    try:
        frame = pd.read_csv(io.StringIO(request.csv))
        if len(frame) > 10000 or 'symbol' not in frame or frame.symbol.nunique() > 50:
            raise ValueError('Batch limit: 10000 bars and 50 symbols')
        config = IntradayConfig(**request.model_dump(exclude={'csv', 'source_label'}))
        result = await asyncio.to_thread(backtest_intraday, frame, config)
        return dict(result=result, source=request.source_label, data_sha256=hashlib.sha256(request.csv.encode()).hexdigest(), execution='disabled')
    except (ValueError, TypeError, KeyError) as exc: raise HTTPException(422, str(exc)) from exc

@app.post('/api/control', dependencies=[Depends(authorize)])
def control(command: Control):
    try: return app.state.replay.control(command)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc

@app.post('/api/trendspider/webhook')
def webhook(event: TrendSpiderEvent):
    secret = os.getenv('TRENDSPIDER_WEBHOOK_SECRET', '')
    if not secret or not hmac.compare_digest(event.secret, secret): raise HTTPException(401, 'Invalid webhook secret')
    safe = event.model_dump(exclude={'secret'}, exclude_none=True)
    now = datetime.now(timezone.utc)
    # Upstream offers no documented timestamp/id variable. Without event_id,
    # dedupe identical payloads within the same 30-second bucket only.
    key = event.event_id or hashlib.sha256((json.dumps(safe, sort_keys=True) + str(int(now.timestamp()) // 30)).encode()).hexdigest()
    safe.update(id=key, received_at=now.isoformat(), source='TrendSpider webhook', execution='not_submitted')
    with event_db() as con:
        before = con.total_changes
        con.execute('INSERT OR IGNORE INTO events VALUES (?, ?, ?)', (key, now.isoformat(), json.dumps(safe)))
        duplicate = con.total_changes == before
    return dict(accepted=True, duplicate=duplicate, id=key, execution='not_submitted')

@app.post('/api/backtests/import', dependencies=[Depends(authorize)])
async def imported_backtest(request: CSVResearch):
    try:
        # No URL fetching: callers supply their authorized data export.
        frame = load_bars(io.StringIO(request.csv))
        if request.start >= request.end: raise ValueError('Start must precede end')
        scenario = dict(id='imported', name='Imported research', label=f'{request.start} to {request.end}',
                        start=request.start, end=request.end, fee_bps=request.fee_bps, slippage_bps=request.slippage_bps)
        result = await asyncio.to_thread(run_scenario, frame, scenario, request.capital)
        result.update(symbol=request.symbol, source=request.source_label)
        return dict(result=result, data_sha256=hashlib.sha256(request.csv.encode()).hexdigest(), execution='disabled')
    except (ValueError, TypeError) as exc: raise HTTPException(422, str(exc)) from exc

@app.websocket('/ws')
async def websocket(ws: WebSocket):
    await ws.accept()
    try:
        # Authenticate as the first frame, keeping tokens out of URLs/logs.
        first = await asyncio.wait_for(ws.receive_json(), timeout=5)
        if not isinstance(first, dict):
            await ws.close(code=1008); return
        expected = os.getenv('STRATEGY_API_TOKEN', '')
        if not expected or not isinstance(first.get('token'), str) or not hmac.compare_digest(first['token'], expected):
            await ws.close(code=1008); return
        while True:
            await ws.send_json(app.state.replay.state())
            await asyncio.sleep(.5)
    except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError): return


# ---------------------------------------------------------------------------
# Four-agent trading runtime (paper only)
# ---------------------------------------------------------------------------
RuntimeName = Literal['live', 'replay']


def runtime_for(name: str) -> TradingRuntime:
    return app.state.live_runtime if name == 'live' else app.state.sandbox.runtime


def narrator_for(name: str) -> Narrator:
    runtime = runtime_for(name)
    key = (name, str(runtime.directory))
    if key not in app.state.narrators:
        app.state.narrators[key] = Narrator(runtime.directory)
    return app.state.narrators[key]


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    risk: dict[str, float] | None = None
    shorts_enabled: bool | None = None
    minimum_edge_samples: int | None = Field(default=None, ge=20, le=1000)
    edge_window: int | None = Field(default=None, ge=20, le=1000)
    conflict_policy: Literal['evidence', 'strict'] | None = None


class ReplayControl(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['start', 'pause', 'reset', 'speed', 'step']
    speed: float | None = Field(default=None, ge=0, le=500, allow_inf_nan=False)
    steps: int | None = Field(default=None, ge=1, le=2000)


class LiveControl(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['start', 'stop']


def _status(name: str) -> dict:
    runtime = runtime_for(name)
    status = runtime.status()
    status['driver'] = app.state.live.status() if name == 'live' else app.state.sandbox.status()
    status['narrator'] = narrator_for(name).status()
    return status


@app.get('/api/agent/{name}/status', dependencies=[Depends(authorize)])
async def agent_status(name: RuntimeName):
    return await asyncio.to_thread(_status, name)


@app.get('/api/agent/{name}/decisions', dependencies=[Depends(authorize)])
async def agent_decisions(name: RuntimeName, limit: int = Query(default=60, ge=1, le=500)):
    try:
        return await asyncio.to_thread(runtime_for(name).journal.tail, limit)
    except ValueError as exc: raise HTTPException(500, str(exc)) from exc


@app.get('/api/agent/{name}/decisions/{decision_id}', dependencies=[Depends(authorize)])
def agent_decision(name: RuntimeName, decision_id: str):
    record = runtime_for(name).journal.find(decision_id)
    if record is None: raise HTTPException(404, 'Unknown decision')
    return record


@app.post('/api/agent/{name}/decisions/{decision_id}/narrate', dependencies=[Depends(authorize)])
async def agent_narrate(name: RuntimeName, decision_id: str):
    record = runtime_for(name).journal.find(decision_id)
    if record is None: raise HTTPException(404, 'Unknown decision')
    return await asyncio.to_thread(narrator_for(name).narrate, record)


@app.get('/api/agent/{name}/ledger', dependencies=[Depends(authorize)])
def agent_ledger(name: RuntimeName):
    runtime = runtime_for(name)
    with runtime.lock:
        ledger = runtime.extra.ledger
        return dict(summary=ledger.summary(), trades=ledger.trades[-200:], curve=ledger.curve,
                    events=ledger.events[-120:], execution='paper_only')


@app.get('/api/agent/{name}/bars/{symbol}', dependencies=[Depends(authorize)])
async def agent_bars(name: RuntimeName, symbol: str):
    try: return await asyncio.to_thread(runtime_for(name).bars_view, symbol)
    except ValueError as exc: raise HTTPException(404, str(exc)) from exc


@app.get('/api/agent/{name}/model', dependencies=[Depends(authorize)])
async def agent_model(name: RuntimeName):
    return await asyncio.to_thread(runtime_for(name).model_view)


@app.get('/api/agent/{name}/settings', dependencies=[Depends(authorize)])
def agent_settings(name: RuntimeName):
    from .risk import RiskPolicy
    from .runtime import RISK_KEYS
    runtime = runtime_for(name)
    return dict(settings=runtime.extra.settings, hard_caps={k: getattr(RiskPolicy(), k) for k in RISK_KEYS},
                audit=runtime.extra.audit[-50:],
                note='Limits can be tightened below the hard caps; they can never be loosened above them.')


@app.put('/api/agent/{name}/settings', dependencies=[Depends(authorize)])
async def agent_update_settings(name: RuntimeName, update: SettingsUpdate):
    try: return await asyncio.to_thread(runtime_for(name).update_settings, update.model_dump(exclude_none=True))
    except (ValueError, TypeError) as exc: raise HTTPException(422, str(exc)) from exc


@app.post('/api/agent/{name}/stage', dependencies=[Depends(authorize)])
async def agent_stage(name: RuntimeName, request: LearningReview):
    runtime = runtime_for(name)
    clock = runtime.researcher.last_processed_at
    now = pd.Timestamp(clock).to_pydatetime() if name == 'replay' and clock else None
    try: return await asyncio.to_thread(runtime.stage, acknowledge_drift=request.acknowledge_drift, now=now)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc


@app.post('/api/agent/{name}/promote', dependencies=[Depends(authorize)])
async def agent_promote(name: RuntimeName, request: LearningReview):
    runtime = runtime_for(name)
    clock = runtime.researcher.last_processed_at
    now = pd.Timestamp(clock).to_pydatetime() if name == 'replay' and clock else None
    try: return await asyncio.to_thread(runtime.promote, reviewed=request.reviewed, now=now)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc


@app.post('/api/agent/{name}/halt/acknowledge', dependencies=[Depends(authorize)])
async def agent_ack_halt(name: RuntimeName):
    runtime = runtime_for(name)
    clock = runtime.researcher.last_processed_at
    now = pd.Timestamp(clock).to_pydatetime() if name == 'replay' and clock else None
    try: return await asyncio.to_thread(runtime.acknowledge_halt, now=now)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc


@app.post('/api/replay/control', dependencies=[Depends(authorize)])
async def replay_control(command: ReplayControl):
    driver = app.state.sandbox
    try:
        if command.action == 'start': driver.start()
        elif command.action == 'pause': await driver.pause()
        elif command.action == 'reset': await driver.reset()
        elif command.action == 'speed':
            if command.speed is None: raise ValueError('speed required')
            driver.set_speed(command.speed)
        else:
            if driver.running: raise ValueError('Pause the replay before stepping manually')
            await asyncio.to_thread(driver.step, command.steps or 1)
            await asyncio.to_thread(driver.runtime.flush)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    return driver.status()


@app.post('/api/live/control', dependencies=[Depends(authorize)])
async def live_control(command: LiveControl):
    if command.action == 'start': app.state.live.start()
    else: await app.state.live.stop()
    return app.state.live.status()


@app.get('/api/library/summary', dependencies=[Depends(authorize)])
def library_summary(): return library.summary()


@app.get('/api/library/curves/{market}', dependencies=[Depends(authorize)])
def library_curves(market: str):
    try: return library.curves(market)
    except ValueError as exc: raise HTTPException(404, str(exc)) from exc


@app.get('/api/library/orcl', dependencies=[Depends(authorize)])
def library_orcl(): return library.legacy_orcl()


EXPORTS = {'freqtrade': ROOT / 'exports' / 'freqtrade' / 'FourAgentResearchStrategy.py',
           'lean-main': ROOT / 'exports' / 'lean' / 'main.py',
           'lean-signals': ROOT / 'exports' / 'lean' / 'signals.py'}


@app.get('/api/exports', dependencies=[Depends(authorize)])
def exports_index(): return {k: v.name for k, v in EXPORTS.items()}


@app.get('/api/exports/{name}', dependencies=[Depends(authorize)], response_class=PlainTextResponse)
def exports_file(name: str):
    if name not in EXPORTS: raise HTTPException(404, 'Unknown export')
    return EXPORTS[name].read_text()


@app.websocket('/ws/agent')
async def agent_socket(ws: WebSocket):
    """First frame: {"token": "...", "runtime": "live"|"replay", "after": 0}. Streams runtime events."""
    await ws.accept()
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=5)
        expected = os.getenv('STRATEGY_API_TOKEN', '')
        if (not isinstance(first, dict) or not expected or not isinstance(first.get('token'), str)
                or not hmac.compare_digest(first['token'], expected) or first.get('runtime') not in ('live', 'replay')):
            await ws.close(code=1008); return
        name = first['runtime']
        seq = int(first.get('after', 0)) if isinstance(first.get('after', 0), int) else 0
        runtime = runtime_for(name)
        ticks = 0
        while True:
            current = runtime_for(name)
            if current is not runtime:  # replay reset replaced the sandbox
                runtime, seq = current, 0
                await ws.send_json(dict(type='reset', runtime=name))
            events = runtime.events_after(seq)
            if events:
                seq = events[-1]['seq']
                await ws.send_json(dict(type='events', runtime=name, events=events[-50:]))
            if ticks % 10 == 0:
                driver = app.state.live.status() if name == 'live' else app.state.sandbox.status()
                await ws.send_json(dict(type='heartbeat', runtime=name, driver=driver,
                                        clock=runtime.researcher.last_processed_at))
            ticks += 1
            await asyncio.sleep(.5)
    except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError): return


WEB_DIST = ROOT / 'web' / 'dist'
if (WEB_DIST / 'index.html').exists():
    app.mount('/', StaticFiles(directory=WEB_DIST, html=True), name='web')
else:
    @app.get('/', response_class=HTMLResponse, include_in_schema=False)
    def website_missing():
        return ('<!doctype html><meta charset="utf-8"><title>Strategy City</title>'
                '<body style="font-family:system-ui;max-width:640px;margin:10vh auto;padding:0 16px">'
                '<h1>The API is running, but the website is not built</h1>'
                '<p><code>web/dist/</code> is missing. Restore the prebuilt site with <code>git checkout -- web/dist</code> '
                '(or <code>git pull</code>), or rebuild it with Node.js 22+: <code>cd web &amp;&amp; npm ci &amp;&amp; npm run build</code>. '
                'Then restart the server.</p><p>API docs: <a href="/docs">/docs</a></p></body>')

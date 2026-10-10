"""Live paper loop: public read-only market data -> four roles -> paper ledger.

Crypto: Binance public klines (no key, read-only, market data only).
ETFs: Alpaca IEX bars, enabled only when ALPACA_API_KEY_ID/ALPACA_API_SECRET_KEY
are set (your own free data account). EUR conversion uses the Binance EURUSDT
pair as a point-in-time proxy for USD and USDT (disclosed in every source label).
Nothing here can place an order; there is no broker client in this codebase.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from typing import Any

import httpx
import pandas as pd

from .intraday import BAR
from .markets import DEFAULT_PROFILES

BINANCE_BASES = ('https://data-api.binance.vision', 'https://api.binance.com')
ALPACA_BARS = 'https://data.alpaca.markets/v2/stocks/bars'
CRYPTO = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT')
ETFS = ('SPY', 'QQQ')


async def binance_klines(client: httpx.AsyncClient, symbol: str, limit: int) -> list[list[Any]]:
    last_error: Exception | None = None
    for base in BINANCE_BASES:
        try:
            response = await client.get(f'{base}/api/v3/klines', params=dict(symbol=symbol, interval='15m', limit=limit))
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
    raise RuntimeError(f'Binance klines unavailable for {symbol}: {type(last_error).__name__}')


def _closed(rows: list[list[Any]], now: datetime) -> pd.DataFrame:
    now_ms = now.timestamp() * 1000
    data = [dict(timestamp=pd.Timestamp(int(r[0]), unit='ms', tz='UTC'), open=float(r[1]), high=float(r[2]),
                 low=float(r[3]), close=float(r[4]), volume=float(r[5]))
            for r in rows if int(r[6]) < now_ms and int(r[0]) % 900_000 == 0]
    return pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])


def attach_fx(bars: pd.DataFrame, eurusdt: pd.DataFrame) -> pd.DataFrame:
    """EUR per quote unit = 1 / EURUSDT close of the same or most recent earlier completed bar."""
    if bars.empty or eurusdt.empty:
        return bars.iloc[0:0].assign(fx_to_eur=pd.Series(dtype=float))
    fx = eurusdt[['timestamp', 'close']].rename(columns={'close': 'eurusdt'}).sort_values('timestamp')
    merged = pd.merge_asof(bars.sort_values('timestamp'), fx, on='timestamp', direction='backward')
    merged = merged.dropna(subset=['eurusdt'])
    return merged.assign(fx_to_eur=1 / merged.eurusdt).drop(columns='eurusdt')


async def alpaca_bars(client: httpx.AsyncClient, symbols: tuple[str, ...], now: datetime, lookback_hours: int) -> pd.DataFrame:
    key, secret = os.getenv('ALPACA_API_KEY_ID'), os.getenv('ALPACA_API_SECRET_KEY')
    if not key or not secret:
        return pd.DataFrame()
    params = dict(symbols=','.join(symbols), timeframe='15Min', feed='iex', limit=10000, adjustment='raw',
                  start=(now - timedelta(hours=lookback_hours)).strftime('%Y-%m-%dT%H:%M:%SZ'))
    response = await client.get(ALPACA_BARS, params=params,
                                headers={'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret})
    response.raise_for_status()
    rows = []
    for symbol, bars in (response.json().get('bars') or {}).items():
        for b in bars:
            start = pd.Timestamp(b['t']).tz_convert('UTC')
            if start + BAR <= pd.Timestamp(now) and start.value % BAR.value == 0:
                rows.append(dict(timestamp=start, symbol=symbol, open=b['o'], high=b['h'], low=b['l'], close=b['c'], volume=b['v']))
    return pd.DataFrame(rows)


async def fetch_completed_bars(client: httpx.AsyncClient, now: datetime, *, limit: int, etfs: bool = True) -> tuple[pd.DataFrame, list[str]]:
    """Completed bars for all enabled symbols, with point-in-time EUR conversion."""
    notes: list[str] = []
    fx = _closed(await binance_klines(client, 'EURUSDT', min(1000, limit + 8)), now)
    frames = []
    for symbol in CRYPTO:
        bars = attach_fx(_closed(await binance_klines(client, symbol, limit), now), fx)
        frames.append(bars.assign(symbol=symbol, asset_class='crypto'))
    if etfs:
        try:
            equities = await alpaca_bars(client, ETFS, now, lookback_hours=max(6, limit // 4 * 3))
            if not equities.empty:
                for symbol, group in equities.groupby('symbol'):
                    frames.append(attach_fx(group.drop(columns='symbol'), fx).assign(symbol=symbol, asset_class='etf'))
            elif not os.getenv('ALPACA_API_KEY_ID'):
                notes.append('SPY/QQQ disabled: set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY to enable IEX bars.')
        except httpx.HTTPError as exc:
            notes.append(f'Alpaca unavailable ({type(exc).__name__}); crypto continues.')
    frame = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(not f.empty for f in frames) else pd.DataFrame()
    return frame, notes


class LiveLoop:
    """Polls every `poll_seconds`; ingests each newly completed bar once, in clock order."""
    def __init__(self, runtime, *, poll_seconds: float = 10., transport: httpx.AsyncBaseTransport | None = None):
        self.runtime = runtime
        self.poll_seconds = poll_seconds
        self.transport = transport
        self.task: asyncio.Task | None = None
        self.state: dict[str, Any] = dict(running=False, last_poll=None, last_ingest=None, last_error=None,
                                          notes=[], dropped_late_bars=0, polls=0, accepted_bars=0)

    def status(self) -> dict:
        return dict(self.state, poll_seconds=self.poll_seconds,
                    sources=dict(crypto='Binance public klines (read-only, no key)',
                                 etf='Alpaca IEX 15-minute bars' if os.getenv('ALPACA_API_KEY_ID') else 'disabled (no Alpaca keys)',
                                 fx='Binance EURUSDT as USD/USDT->EUR proxy'),
                    execution='paper_only')

    def start(self) -> None:
        if self.task and not self.task.done():
            return
        self.state.update(running=True, last_error=None)
        self.task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self.state['running'] = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.task = None
        await asyncio.to_thread(self.runtime.flush)

    def _new_rows(self, frame: pd.DataFrame) -> pd.DataFrame:
        learner = self.runtime.researcher
        if frame.empty:
            return frame
        end = frame.timestamp + BAR
        keep = pd.Series(True, index=frame.index)
        for symbol, last in learner.last_quote.items():
            keep &= ~((frame.symbol == symbol) & (end <= pd.Timestamp(last)))
        late = pd.Series(False, index=frame.index)
        if learner.last_processed_at:
            late = keep & (end < pd.Timestamp(learner.last_processed_at))
        self.state['dropped_late_bars'] += int(late.sum())
        return frame[keep & ~late].sort_values(['timestamp', 'symbol']).reset_index(drop=True)

    async def poll_once(self, client: httpx.AsyncClient, *, now: datetime | None = None) -> dict | None:
        now = now or datetime.now(timezone.utc)
        last = self.runtime.researcher.last_processed_at
        # After a restart, catch up on missed bars (bounded); otherwise fetch a small overlap.
        missed = int((pd.Timestamp(now) - pd.Timestamp(last)) / BAR) + 2 if last else 400
        frame, notes = await fetch_completed_bars(client, now, limit=max(6, min(1000, missed)))
        self.state.update(last_poll=now.isoformat(), notes=notes, polls=self.state['polls'] + 1)
        rows = self._new_rows(frame)
        if rows.empty:
            return None
        unknown = set(rows.symbol) - set(DEFAULT_PROFILES)
        if unknown:
            raise ValueError(f'No market profile for {sorted(unknown)}')
        label = 'live: Binance public klines + EURUSDT FX' + (' + Alpaca IEX' if (rows.asset_class == 'etf').any() else '')
        result = await asyncio.to_thread(self.runtime.ingest_bars, rows, label, now=now)
        self.state.update(last_ingest=now.isoformat(), accepted_bars=self.state['accepted_bars'] + result['accepted_bars'])
        return result

    async def _run(self) -> None:
        async with httpx.AsyncClient(timeout=15., transport=self.transport, headers={'User-Agent': 'ai-trading-agent/1.0 research'}) as client:
            while self.state['running']:
                try:
                    await self.poll_once(client)
                    self.state['last_error'] = None
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # keep polling; surface the class only (messages may hold secrets)
                    self.state['last_error'] = f'{type(exc).__name__}: {str(exc)[:200]}' if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
                    self.runtime.publish('feed_error', dict(error=self.state['last_error']))
                await asyncio.sleep(self.poll_seconds)

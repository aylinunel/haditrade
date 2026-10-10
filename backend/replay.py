"""Historical replay sandbox: watch the agent learn on real bars, fast.

Bars come from `data/replay/*_15m.csv.gz` (Binance 15-minute klines via the
public finom/static-klines mirror). Each simulated step feeds one completed bar
per symbol with a simulated receipt clock one second after the bar closes, so
every causality gate behaves exactly as in live mode. Replay runs in its own
runtime directory: it never touches the live model, ledger or journal.

USDT is converted to EUR with a constant rate (REPLAY_USDT_TO_EUR). Returns are
therefore identical to USDT returns; FX risk is not simulated in replay.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import time
from typing import Any

import numpy as np
import pandas as pd

from .intraday import BAR

REPLAY_USDT_TO_EUR = 0.86
DATA = Path(__file__).resolve().parents[1] / 'data' / 'replay'


def load_replay_data(directory: Path = DATA, symbols: tuple[str, ...] = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT')) -> pd.DataFrame:
    frames = []
    for symbol in symbols:
        path = directory / f'{symbol}_15m.csv.gz'
        if not path.exists():
            raise FileNotFoundError(f'Missing replay file {path.name}; run scripts/build_replay_data.py')
        frame = pd.read_csv(path)
        frame['timestamp'] = pd.to_datetime(frame.timestamp, utc=True)
        frames.append(frame.assign(symbol=symbol, fx_to_eur=REPLAY_USDT_TO_EUR, asset_class='crypto'))
    data = pd.concat(frames, ignore_index=True).sort_values(['timestamp', 'symbol']).reset_index(drop=True)
    return data[['timestamp', 'symbol', 'open', 'high', 'low', 'close', 'volume', 'fx_to_eur', 'asset_class']]


class ReplayDriver:
    WARMUP_BARS = 60

    def __init__(self, runtime_factory, directory: Path, data: pd.DataFrame | None = None):
        self.runtime_factory = runtime_factory
        self.directory = Path(directory)
        self.data = data if data is not None else load_replay_data()
        self.data = self.data.sort_values(['timestamp', 'symbol']).reset_index(drop=True)
        self.times = self.data.timestamp.drop_duplicates().reset_index(drop=True)
        stamps = self.data.timestamp.to_numpy()
        self.bounds = np.searchsorted(stamps, self.times.to_numpy(), side='left').tolist() + [len(stamps)]
        self.runtime = runtime_factory(self.directory)
        self.speed = 8.0  # simulated bars per second; 0 = as fast as possible
        self.task: asyncio.Task | None = None
        self.running = False
        self.last_error: str | None = None
        self.steps_per_second = 0.

    def position(self) -> int:
        """Index of the next bar timestamp to feed, recovered from the runtime clock."""
        last = self.runtime.researcher.last_processed_at
        if not last:
            return 0
        return int(self.times.searchsorted(pd.Timestamp(last) - BAR, side='right'))

    def status(self) -> dict[str, Any]:
        index = self.position()
        return dict(running=self.running, speed=self.speed, position=index, total=len(self.times),
                    progress=index / len(self.times) if len(self.times) else 0.,
                    first=self.times.iloc[0].isoformat() if len(self.times) else None,
                    last=self.times.iloc[-1].isoformat() if len(self.times) else None,
                    clock=self.runtime.researcher.last_processed_at, steps_per_second=self.steps_per_second,
                    last_error=self.last_error, symbols=sorted(self.data.symbol.unique()),
                    fx_assumption=f'constant {REPLAY_USDT_TO_EUR} EUR per USDT',
                    source='Binance 15m klines via github.com/finom/static-klines')

    def step(self, count: int = 1) -> int:
        """Feed up to `count` bar timestamps synchronously. Returns bars fed."""
        index = self.position()
        fed = 0
        if index == 0 and count > 0:
            warm = self.data.iloc[:self.bounds[self.WARMUP_BARS]]
            now = (warm.timestamp.max() + BAR + pd.Timedelta(seconds=1)).to_pydatetime()
            self.runtime.ingest_bars(warm, 'replay warm-up: Binance 15m klines (finom/static-klines)', now=now)
            index, fed = self.WARMUP_BARS, 1
        while fed < count and index < len(self.times):
            stamp = self.times.iloc[index]
            now = (stamp + BAR + pd.Timedelta(seconds=1)).to_pydatetime()
            self.runtime.ingest_bars(self.data.iloc[self.bounds[index]:self.bounds[index + 1]],
                                     'replay: Binance 15m klines (finom/static-klines)', now=now)
            index += 1
            fed += 1
        return fed

    async def _run(self) -> None:
        try:
            while self.running:
                started = time.perf_counter()
                batch = 1 if self.speed and self.speed <= 20 else 4
                fed = await asyncio.to_thread(self.step, batch)
                elapsed = time.perf_counter() - started
                self.steps_per_second = fed / elapsed if elapsed > 0 else 0.
                if fed == 0:
                    self.running = False
                    self.runtime.publish('replay', dict(action='finished'))
                    break
                if self.speed:
                    await asyncio.sleep(max(0., fed / self.speed - elapsed))
                else:
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = f'{type(exc).__name__}: {str(exc)[:300]}'
            self.running = False
            self.runtime.publish('replay', dict(action='error', error=self.last_error))
        finally:
            await asyncio.to_thread(self.runtime.flush)

    def start(self) -> None:
        if self.running:
            return
        self.running, self.last_error = True, None
        self.task = asyncio.create_task(self._run())
        self.runtime.publish('replay', dict(action='started', speed=self.speed))

    async def pause(self) -> None:
        self.running = False
        if self.task:
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.task = None
        self.runtime.publish('replay', dict(action='paused'))

    def set_speed(self, speed: float) -> None:
        if not 0 <= speed <= 500:
            raise ValueError('Speed must be 0 (max) to 500 bars per second')
        self.speed = float(speed)

    async def reset(self) -> None:
        await self.pause()
        # Only the replay sandbox directory owned by this driver is removed.
        shutil.rmtree(self.directory, ignore_errors=True)
        self.runtime = self.runtime_factory(self.directory)
        self.runtime.publish('replay', dict(action='reset'))

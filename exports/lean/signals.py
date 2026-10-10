"""Signal definitions shared by the LEAN algorithm; identical to backend/intraday.py."""
import numpy as np
import pandas as pd
from pandas import DataFrame

FAMILIES = ('sma', 'rsi', 'bollinger', 'atr_breakout', 'heikin')


def wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    values = close.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    if len(values) <= period:
        return pd.Series(out, index=close.index)
    diffs = np.diff(values)
    gain, loss = np.maximum(diffs, 0), np.maximum(-diffs, 0)
    avg_gain, avg_loss = gain[:period].mean(), loss[:period].mean()

    def value(g, l):
        return 50.0 if g == 0 and l == 0 else (100.0 if l == 0 else 100 - 100 / (1 + g / l))
    out[period] = value(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        avg_gain = ((period - 1) * avg_gain + gain[i - 1]) / period
        avg_loss = ((period - 1) * avg_loss + loss[i - 1]) / period
        out[i] = value(avg_gain, avg_loss)
    return pd.Series(out, index=close.index)


def atr(frame: DataFrame, period: int = 14) -> pd.Series:
    previous = frame['close'].shift(1)
    tr = pd.concat([frame['high'] - frame['low'], (frame['high'] - previous).abs(),
                    (frame['low'] - previous).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def targets(frame: DataFrame, family: str) -> np.ndarray:
    c = frame['close']
    close = c.to_numpy(dtype=float)
    out = np.zeros(len(frame), dtype=int)
    if family == 'sma':
        fast, slow = c.rolling(10).mean(), c.rolling(30).mean()
        return np.where(slow.notna(), np.sign(fast - slow), 0).astype(int)
    if family == 'atr_breakout':
        hi = frame['high'].shift(1).rolling(20).max().to_numpy(dtype=float)
        lo = frame['low'].shift(1).rolling(20).min().to_numpy(dtype=float)
        rng = atr(frame).to_numpy(dtype=float)
        mid, state = (hi + lo) / 2, 0
        for i in range(len(frame)):
            if not np.isfinite(hi[i]):
                continue
            if close[i] > hi[i] + .1 * rng[i]: state = 1
            elif close[i] < lo[i] - .1 * rng[i]: state = -1
            elif (state == 1 and close[i] < mid[i]) or (state == -1 and close[i] > mid[i]): state = 0
            out[i] = state
        return out
    if family in ('rsi', 'bollinger'):
        osc = wilder_rsi(c).to_numpy(dtype=float)
        mean = c.rolling(20).mean().to_numpy(dtype=float)
        sd = c.rolling(20).std(ddof=1).to_numpy(dtype=float)
        state = 0
        for i in range(len(frame)):
            if family == 'rsi':
                low, high = osc[i] < 30, osc[i] > 70
                exit_ = (state == 1 and osc[i] >= 50) or (state == -1 and osc[i] <= 50)
            else:
                low, high = close[i] < mean[i] - 2 * sd[i], close[i] > mean[i] + 2 * sd[i]
                exit_ = (state == 1 and close[i] >= mean[i]) or (state == -1 and close[i] <= mean[i])
            if exit_: state = 0
            elif state == 0:
                if low: state = 1
                elif high: state = -1
            out[i] = state
        return out
    if family == 'heikin':
        hc = frame[['open', 'high', 'low', 'close']].mean(axis=1).to_numpy()
        ho = np.zeros(len(frame))
        if len(frame):
            ho[0] = (frame['open'].iloc[0] + c.iloc[0]) / 2
        for i in range(1, len(frame)):
            ho[i] = (ho[i - 1] + hc[i - 1]) / 2
        body = hc - ho
        for i in range(30, len(frame)):
            if body[i] > 0 and body[i - 1] > 0: out[i] = 1
            elif body[i] < 0 and body[i - 1] < 0: out[i] = -1
        return out
    raise ValueError(f'Unknown signal family {family}')

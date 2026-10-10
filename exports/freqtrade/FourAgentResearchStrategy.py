"""Freqtrade export of the five ai-trading-agent signals (research use, dry-run first).

Copy into `user_data/strategies/` and backtest, e.g.:
    freqtrade backtesting --strategy FourAgentResearchStrategy --timeframe 15m \
        --timerange 20251001- -c config.json
Set `signal_family` (sma | rsi | bollinger | atr_breakout | heikin) per bot
instance to run one hypothesis per bot, as Freqtrade recommends.

Matches backend/intraday.py: entries on a fresh target change of the completed
candle, stop = clamp(2 * ATR14 / close, 0.5%, 5%), exit at 48 hours or stop.
NOT included: the skeptic's evidence and conflict vetoes, the researcher's
empirical edge gate and the trader's shared EUR 500 risk budget. Those live in
the agent; this file only lets you test the raw signals in Freqtrade.
Shorts require trading_mode futures/margin; on spot set can_short = False.
"""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.strategy import IStrategy, stoploss_from_open

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


class FourAgentResearchStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = '15m'
    can_short = True
    minimal_roi = {'0': 100}  # disabled: exits are stop or 48h horizon only
    stoploss = -0.05          # hard backstop; custom_stoploss tightens per entry
    use_custom_stoploss = True
    process_only_new_candles = True
    startup_candle_count = 400  # the agent evaluates the trailing 400 bars
    signal_family = 'sma'

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['target'] = targets(dataframe, self.signal_family)
        dataframe['stop_pct'] = (2 * atr(dataframe) / dataframe['close']).clip(.005, .05)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        fresh = dataframe['target'] != dataframe['target'].shift(1)
        dataframe.loc[fresh & (dataframe['target'] == 1), ['enter_long', 'enter_tag']] = (1, f'{self.signal_family}_long')
        dataframe.loc[fresh & (dataframe['target'] == -1), ['enter_short', 'enter_tag']] = (1, f'{self.signal_family}_short')
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['exit_long'] = 0
        dataframe['exit_short'] = 0
        return dataframe

    def custom_stoploss(self, pair: str, trade, current_time: datetime, current_rate: float,
                        current_profit: float, after_fill: bool, **kwargs) -> float:
        frame, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        before = frame.loc[frame['date'] <= trade.open_date_utc]
        if before.empty or not np.isfinite(before['stop_pct'].iloc[-1]):
            return None
        # Anchored at the entry price (the agent's stop), not trailing the current price.
        return stoploss_from_open(-float(before['stop_pct'].iloc[-1]), current_profit,
                                  is_short=trade.is_short, leverage=trade.leverage)

    def custom_exit(self, pair: str, trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        if current_time - trade.open_date_utc >= timedelta(hours=48):
            return '48h_horizon'
        return None

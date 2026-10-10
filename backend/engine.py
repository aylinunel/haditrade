"""Causal daily-bar research engine. No broker orders or machine learning claims."""
from __future__ import annotations
from pathlib import Path
import hashlib
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
DATA = ROOT / 'data' / 'orcl.csv'
STRATEGIES = [
    dict(id='sma', name='Moving average', short='SMA 10 / 30', family='Momentum', color='#a78bfa', description='Long when the 10-bar simple average exceeds the 30-bar average; otherwise cash.', adaptation='Upstream MACD script actually uses simple moving averages. Fixed 10/30 windows, full warmup and next-open execution.'),
    dict(id='rsi', name='RSI reversal', short='RSI 14', family='Mean reversion', color='#45dfad', description='Wilder RSI: enter below 30, hold until RSI reaches 55, otherwise cash.', adaptation='Uses the upstream RSI idea; replaces short/flat threshold positions with a long-only stateful 30/55 rule.'),
    dict(id='bollinger', name='Bollinger bands', short='BB 20 / 2', family='Mean reversion', color='#f7bf69', description='Enter below the 20-bar mean minus two sample standard deviations; exit at the mean.', adaptation='Uses upstream bands; replaces scale-dependent W-pattern thresholds with a basic mean-reversion rule. Not the original W-pattern script.'),
    dict(id='sar', name='Parabolic SAR', short='SAR .02 / .20', family='Trend following', color='#56bcff', description='Long during the bullish SAR state; acceleration rises from .02 to .20.', adaptation='Causal recursive SAR with previous-bar clamps, 30-bar warmup and next-open fills.'),
    dict(id='heikin', name='Heikin-Ashi', short='HA confirmation', family='Trend following', color='#f38ab9', description='Enter after two bullish candles with no lower wick and expanding body; exit after two bearish candles.', adaptation='Upstream enters long on bearish HA candles. This variant deliberately reverses that direction, caps exposure at one long position and trades real OHLC prices.'),
]
SCENARIOS = [
    dict(id='expansion', name='Expansion', label='2003–2007', start='2003-01-01', end='2007-12-31', fee_bps=5, slippage_bps=5),
    dict(id='crisis', name='Financial crisis', label='2008–2009', start='2008-01-01', end='2009-12-31', fee_bps=5, slippage_bps=5),
    dict(id='recovery', name='Recovery', label='2010–2014', start='2010-01-01', end='2014-12-31', fee_bps=5, slippage_bps=5),
]

def load_bars(path=DATA):
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    if not {'Date', 'Open', 'High', 'Low', 'Close', 'Volume'}.issubset(df.columns):
        raise ValueError('Expected Date,Open,High,Low,Close,Volume CSV columns')
    df['Date'] = pd.to_datetime(df['Date'], errors='raise')
    if df['Date'].duplicated().any() or not df['Date'].is_monotonic_increasing:
        raise ValueError('Dates must be unique and increasing')
    cols = ['Open', 'High', 'Low', 'Close', 'Volume']
    if not np.isfinite(df[cols].to_numpy(dtype=float)).all():
        raise ValueError('Non-finite market data')
    if (df[['Open', 'High', 'Low', 'Close']] <= 0).any().any() or (df.Volume < 0).any():
        raise ValueError('Prices must be positive and volume nonnegative')
    if (df.High < df[['Open', 'Close', 'Low']].max(axis=1) - 1e-5).any() or (df.Low > df[['Open', 'Close', 'High']].min(axis=1) + 1e-5).any():
        raise ValueError('Invalid OHLC bounds')
    return df.reset_index(drop=True)

def wilder_rsi(close, period=14):
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

def target_signals(df, strategy):
    c, n = df.Close, len(df)
    target = np.zeros(n, dtype=int)
    if strategy == 'sma':
        return (c.rolling(10).mean() > c.rolling(30).mean()).to_numpy(dtype=int)
    if strategy in ('rsi', 'bollinger'):
        rsi = wilder_rsi(c)
        mean, sd = c.rolling(20).mean(), c.rolling(20).std(ddof=1)
        active = 0
        for i in range(n):
            enter = rsi.iloc[i] < 30 if strategy == 'rsi' else c.iloc[i] < mean.iloc[i] - 2 * sd.iloc[i]
            exit_ = rsi.iloc[i] >= 55 if strategy == 'rsi' else c.iloc[i] >= mean.iloc[i]
            if not active and enter: active = 1
            elif active and exit_: active = 0
            target[i] = active
        return target
    if strategy == 'heikin':
        hc = df[['Open', 'High', 'Low', 'Close']].mean(axis=1).to_numpy()
        ho = np.zeros(n)
        if n: ho[0] = (df.Open.iloc[0] + c.iloc[0]) / 2
        for i in range(1, n): ho[i] = (ho[i - 1] + hc[i - 1]) / 2
        body = hc - ho
        active = 0
        for i in range(1, n):
            no_lower_wick = df.Low.iloc[i] >= ho[i] - 1e-9
            if not active and body[i] > 0 and body[i - 1] > 0 and no_lower_wick and body[i] > body[i - 1]: active = 1
            elif active and body[i] < 0 and body[i - 1] < 0: active = 0
            target[i] = active if i >= 30 else 0
        return target
    if strategy == 'sar':
        if n < 2: return target
        bull = c.iloc[1] > c.iloc[0]
        sar = df.Low.iloc[0] if bull else df.High.iloc[0]
        ep = df.High.iloc[1] if bull else df.Low.iloc[1]
        af = .02
        for i in range(2, n):
            sar += af * (ep - sar)
            if bull:
                sar = min(sar, df.Low.iloc[i - 1], df.Low.iloc[i - 2])
                if df.Low.iloc[i] < sar:
                    bull, sar, ep, af = False, ep, df.Low.iloc[i], .02
                elif df.High.iloc[i] > ep:
                    ep, af = df.High.iloc[i], min(.2, af + .02)
            else:
                sar = max(sar, df.High.iloc[i - 1], df.High.iloc[i - 2])
                if df.High.iloc[i] > sar:
                    bull, sar, ep, af = True, ep, df.High.iloc[i], .02
                elif df.Low.iloc[i] < ep:
                    ep, af = df.Low.iloc[i], min(.2, af + .02)
            target[i] = int(bull) if i >= 30 else 0
        return target
    raise ValueError('Unknown strategy')

def metrics(equity, initial, trades):
    values = np.asarray(equity, dtype=float)
    with_initial = np.r_[initial, values]
    returns = np.diff(with_initial) / with_initial[:-1]
    peaks = np.maximum.accumulate(with_initial)
    dd = 1 - with_initial / peaks
    stdev = returns.std(ddof=1) if len(returns) > 1 else 0
    closed = [t for t in trades if t['side'] == 'SELL']
    return dict(return_pct=float((values[-1] / initial - 1) * 100),
                max_drawdown_pct=float(dd.max() * 100),
                sharpe=float(np.sqrt(252) * returns.mean() / stdev) if stdev > 1e-12 else 0,
                trades=len(closed), win_rate_pct=float(np.mean([t['pnl'] > 0 for t in closed]) * 100) if closed else None,
                final_equity=float(values[-1]))

def simulate(df, signals, indices, initial=2000., fee_bps=5., slippage_bps=5.):
    """Close[t-1] signal -> Open[t] fill. Fractional shares, long/cash, zero leverage."""
    if len(indices) == 0: raise ValueError('Empty test interval')
    fee, slip = fee_bps / 10000, slippage_bps / 10000
    cash, shares, entry_cost, realized, fees, peak = initial, 0., 0., 0., 0., initial
    trace, trades = [], []
    for k, i in enumerate(indices):
        date = df.Date.iloc[i].strftime('%Y-%m-%d')
        desired = int(signals[i - 1]) if i > 0 else 0
        def sell(price, forced=False):
            nonlocal cash, shares, entry_cost, realized, fees
            execution = float(price * (1 - slip))
            commission = shares * execution * fee
            proceeds = shares * execution - commission
            pnl = proceeds - entry_cost
            trades.append(dict(date=date, side='SELL', price=execution, quantity=shares, fee=commission, pnl=pnl, forced=forced))
            cash += proceeds; realized += pnl; fees += commission
            shares = entry_cost = 0.
        if shares > 0 and not desired: sell(df.Open.iloc[i])
        if shares == 0 and desired:
            execution = float(df.Open.iloc[i] * (1 + slip))
            shares = cash / (execution * (1 + fee))
            commission = shares * execution * fee
            entry_cost = cash
            fees += commission
            trades.append(dict(date=date, side='BUY', price=execution, quantity=shares, fee=commission, pnl=0., forced=False))
            cash = 0.
        # Real-price terminal liquidation prevents uncounted closing costs.
        if k == len(indices) - 1 and shares > 0: sell(df.Close.iloc[i], forced=True)
        marked = shares * float(df.Close.iloc[i])
        equity = cash + marked
        peak = max(peak, equity)
        trace.append(dict(date=date, equity=equity, cash=cash, shares=shares, realized=realized,
                          unrealized=marked - entry_cost if shares else 0., drawdown_pct=(1 - equity / peak) * 100,
                          fees=fees, position='LONG' if shares else 'CASH'))
        if cash < -1e-6 or shares < 0 or marked > equity + 1e-6: raise AssertionError('Invalid accounting')
    return dict(metrics=metrics([x['equity'] for x in trace], initial, trades), trace=trace, trades=trades)

def run_scenario(df, scenario, capital=10000.):
    mask = (df.Date >= scenario['start']) & (df.Date <= scenario['end'])
    indices = np.flatnonzero(mask)
    if not len(indices) or indices[0] < 260: raise ValueError('Need 260 prior bars and a nonempty test window')
    # Use an independent causal indicator warmup for each scenario, never future bars.
    window = df.iloc[indices[0] - 260:indices[-1] + 1].reset_index(drop=True)
    local_indices = np.arange(260, len(window))
    sleeves = []
    for meta in STRATEGIES:
        result = simulate(window, target_signals(window, meta['id']), local_indices, initial=capital / 5,
                          fee_bps=scenario['fee_bps'], slippage_bps=scenario['slippage_bps'])
        sleeves.append(dict(**meta, **result))
    benchmark = simulate(window, np.ones(len(window), dtype=int), local_indices, initial=capital,
                         fee_bps=scenario['fee_bps'], slippage_bps=scenario['slippage_bps'])
    aggregate = np.array([[t['equity'] for t in s['trace']] for s in sleeves]).sum(axis=0)
    all_trades = [dict(**t, strategy_id=s['id']) for s in sleeves for t in s['trades']]
    snapshots = []
    for j, equity in enumerate(aggregate):
        bots = [dict(id=s['id'], name=s['name'], color=s['color'], **s['trace'][j]) for s in sleeves]
        snapshots.append(dict(date=bots[0]['date'], equity=float(equity), pnl=float(equity - capital),
                              benchmark_equity=benchmark['trace'][j]['equity'],
                              price=float(window.Close.iloc[local_indices[j]]), bots=bots))
    series = pd.DataFrame({s['id']: [x['equity'] for x in s['trace']] for s in sleeves})
    corr = series.pct_change().iloc[1:].corr().replace([np.inf, -np.inf], np.nan).fillna(0)
    return dict(**scenario, symbol='ORCL', initial_capital=capital, start_actual=snapshots[0]['date'],
                end_actual=snapshots[-1]['date'], bars=len(snapshots),
                portfolio=metrics(aggregate, capital, all_trades), benchmark=benchmark['metrics'],
                strategies=sleeves, snapshots=snapshots, trades=all_trades,
                return_correlations=corr.to_dict())

def build_report(path=DATA):
    df = load_bars(path)
    results = [run_scenario(df, scenario) for scenario in SCENARIOS]
    return dict(version=1, mode='historical_replay', generated_at='2026-10-09',
                provenance=dict(provider='Public Backtrader historical fixture, not TrendSpider',
                                source_url='https://github.com/mementum/backtrader/blob/master/datas/orcl-1995-2014.txt',
                                source_blob_sha='d0c873578b0ff8cf460406db92ca64f57c49da5e',
                                sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                                first_date=df.Date.iloc[0].strftime('%Y-%m-%d'), last_date=df.Date.iloc[-1].strftime('%Y-%m-%d'), rows=len(df)),
                methodology=dict(capital=10000, allocation='Five independent $2,000 sleeves; no sleeve rebalancing',
                    execution='Previous completed close signal; following open fills; terminal close liquidation',
                    costs='5 bps commission + 5 bps adverse slippage on each side', warmup_bars=260,
                    cash_interest=0, leverage=1, shorting=False, dividends='Excluded; reported split-adjusted OHLC used as supplied',
                    parameters='Fixed before tests; no tuning on test windows',
                    limitations=['One historical stock and old data only', 'No licensed TrendSpider historical feed connected',
                                 'Not proof of profitability or independent out-of-sample validation',
                                 'Fractional shares; no minimum fees, taxes, FX costs or market-impact model',
                                 'No trained AI model and no live brokerage execution']),
                strategies=STRATEGIES, scenarios=results)

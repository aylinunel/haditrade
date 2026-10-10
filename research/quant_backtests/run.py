import pandas as pd, numpy as np, json, time
import strategies as S

D = '/home/claude/bt/data'
def load(name):
    return pd.read_csv(f'{D}/{name}.csv', index_col=0, parse_dates=True)

# cost per side as fraction of notional (spread + fees + slippage)
MARKETS = {
    'SPY': dict(label='S&P 500 ETF (SPY)', group='S&P 500 ETF', file='SPY_1d', ann=252, cost=0.0005,
                pair=('DIA', 'SPY vs DIA (Dow ETF)'), hourly=None),
    'BTC': dict(label='Bitcoin (BTC/USDT)', group='Crypto', file='BTC_1d', ann=365, cost=0.0010,
                pair=('ETH', 'BTC vs ETH'), hourly='BTC_1h'),
    'ETH': dict(label='Ethereum (ETH/USDT)', group='Crypto', file='ETH_1d', ann=365, cost=0.0010,
                pair=('BTC', 'ETH vs BTC'), hourly='ETH_1h'),
    'ES':  dict(label='S&P 500 E-mini futures (ES)', group='Futures', file='ES_1d', ann=252, cost=0.0003,
                pair=('NQ', 'ES vs NQ'), hourly='ES_1h'),
    'GC':  dict(label='Gold futures (GC)', group='Futures', file='GC_1d', ann=252, cost=0.0003,
                pair=None, hourly=None),
    'CL':  dict(label='Crude oil futures (CL)', group='Futures', file='CL_1d', ann=252, cost=0.0005,
                pair=None, hourly=None),
}
STRATS = ['Pair trading', 'London Breakout', 'Dual Thrust', 'MACD oscillator', 'Options straddle',
          'Bollinger Bands W', 'Parabolic SAR', 'Heikin-Ashi', 'RSI pattern (H&S)', 'Shooting Star']


def metrics(r, ann):
    r = r.dropna()
    if len(r) < 2: return None
    eq = (1 + r).cumprod()
    yrs = len(r) / ann
    tot = eq.iloc[-1] - 1
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if eq.iloc[-1] > 0 else -1.0
    sd = r.std()
    sharpe = r.mean() / sd * np.sqrt(ann) if sd > 0 else 0.0
    mdd = (eq / eq.cummax() - 1).min()
    return dict(total=tot, cagr=cagr, sharpe=sharpe, mdd=mdd, vol=sd * np.sqrt(ann),
                start=str(r.index[0].date()), end=str(r.index[-1].date()), days=len(r))


def weekly(eq):
    w = eq.resample('W').last().dropna()
    return [[str(d.date()), round(float(v), 4)] for d, v in w.items()]


results = []; curves = {}
for key, m in MARKETS.items():
    t0 = time.time()
    df = load(m['file']); cost = m['cost']; ann = m['ann']
    bh_full = df['Close'].pct_change().fillna(0)
    curves[key] = {}

    def record(name, r, trades, exposure=None, note='', start=None):
        r = r.copy()
        if start is not None:
            r = r[r.index >= start]
        # benchmark over the identical window
        bh = df['Close'].reindex(r.index).pct_change().fillna(0)
        bh.iloc[0] -= cost
        mt = metrics(r, ann); mb = metrics(bh, ann)
        row = dict(market=key, market_label=m['label'], group=m['group'], strategy=name,
                   trades=trades, exposure=exposure, note=note, **mt,
                   bh_total=mb['total'], bh_cagr=mb['cagr'], bh_sharpe=mb['sharpe'], bh_mdd=mb['mdd'])
        results.append(row)
        curves[key][name] = weekly((1 + r).cumprod())
        if 'Buy & hold' not in curves[key] or start is None:
            pass

    def from_pos(name, pos, note='', start=None):
        r, held = S.pos_to_returns(pos, df, cost)
        st = start if start is not None else pos.index[0]
        expo = float((held[held.index >= st] != 0).mean())
        record(name, r, S.n_trades(pos[pos.index >= st]), expo, note, start)

    # Buy & hold (full window)
    bh = bh_full.copy(); bh.iloc[0] -= cost
    record('Buy & hold', bh, 1, 1.0)

    from_pos('MACD oscillator', S.macd(df), 'Long when 10-day avg ≥ 21-day avg, else cash')
    from_pos('Parabolic SAR', S.parabolic_sar(df), 'Long while SAR is below price, else cash')
    from_pos('Heikin-Ashi', S.heikin_ashi(df), 'Scales into up to 3 longs on repo signal')
    from_pos('Bollinger Bands W', S.bollinger_w(df), 'Long on double-bottom breakout')
    from_pos('RSI pattern (H&S)', S.rsi_head_shoulders(df), 'Short on head-and-shoulders top')
    from_pos('Shooting Star', S.shooting_star(df), 'Short after confirmed shooting star')

    dt, ntr = S.dual_thrust(df, cost)
    first = dt.index[5]
    record('Dual Thrust', dt, ntr, float((dt != 0).mean()), 'Intraday long/short, flat at close',
           start=first)

    if m['hourly']:
        h1 = load(m['hourly'])
        lb, ntr = S.london_breakout(h1, cost)
        lb = lb.reindex(df.index).fillna(0)
        lb = lb[lb.index >= h1.index[0].normalize()]
        record('London Breakout', lb, ntr, float((lb != 0).mean()),
               'Hourly data; trades the 08:00 UTC London-open breakout', start=lb.index[0])
    else:
        results.append(dict(market=key, market_label=m['label'], group=m['group'],
                            strategy='London Breakout', na='No intraday data' if key != 'SPY'
                            else 'Needs intraday data; US ETF market is closed at the London open'))

    if m['pair']:
        other = load(f"{m['pair'][0]}_1d")
        idx = df.index.intersection(other.index)
        if len(idx) < 250 + 120:
            results.append(dict(market=key, market_label=m['label'], group=m['group'],
                                strategy='Pair trading',
                                na=f'Only {len(idx)} days of data; needs 250 to fit the model plus a test period'))
        else:
            a1 = df['Close'].reindex(idx); a2 = other['Close'].reindex(idx)
            p1, p2 = S.pair_trading(a1, a2)
            r1 = a1.pct_change().fillna(0); r2 = a2.pct_change().fillna(0)
            h1_, h2_ = p1.shift(1).fillna(0), p2.shift(1).fillna(0)
            turn = (p1.diff().abs().fillna(0) + p2.diff().abs().fillna(0)).shift(1).fillna(0)
            r = h1_ * r1 + h2_ * r2 - turn * cost
            r = r.reindex(df.index).fillna(0)
            st = idx[250]
            record('Pair trading', r, S.n_trades(p1[p1.index >= st]),
                   float((h1_[h1_.index >= st] != 0).mean()),
                   f"Market-neutral: {m['pair'][1]}", start=st)
    else:
        results.append(dict(market=key, market_label=m['label'], group=m['group'],
                            strategy='Pair trading', na='No obvious cointegrated partner in the free data'))

    results.append(dict(market=key, market_label=m['label'], group=m['group'],
                        strategy='Options straddle', na='Needs historical option prices (not freely available)'))
    print(key, 'done in %.0fs' % (time.time() - t0), flush=True)

json.dump(dict(results=results, curves=curves,
               markets={k: {kk: v for kk, v in m.items() if kk in ('label', 'group', 'cost', 'ann')}
                        for k, m in MARKETS.items()}),
          open('/home/claude/bt/results.json', 'w'), default=float)

tab = pd.DataFrame([r for r in results if 'na' not in r])
pd.set_option('display.width', 250)
for k in MARKETS:
    t = tab[tab.market == k][['strategy', 'start', 'cagr', 'sharpe', 'mdd', 'total', 'trades', 'exposure',
                              'bh_cagr', 'bh_sharpe']].sort_values('sharpe', ascending=False)
    print('\n==', MARKETS[k]['label']); print(t.round(3).to_string(index=False))

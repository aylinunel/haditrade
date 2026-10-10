"""
Faithful re-implementations of the je-suis-tm/quant-trading strategies, run under one
common set of rules so results are comparable across strategies and markets.

Common rules
- Signals use only information available at the bar close; a position decided at the
  close of day t earns the return of day t+1 (no look-ahead).
- Trading costs are charged on every change in position (per side, as a fraction of notional).
- Idle cash earns 0%. Shorts pay no borrow/funding cost (stated limitation).
- Position size is 100% of capital per strategy (pair trading: 50% per leg).

Departures from the repo, all to remove look-ahead bias or rescale FX-specific
absolute thresholds to percentages, are marked with  # FIX / # SCALE.
"""
import numpy as np, pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.stattools import adfuller


# ---------------------------------------------------------------- helpers
def pos_to_returns(pos, df, cost):
    """position decided at close t -> earns close-to-close return t+1, minus costs."""
    r = df['Close'].pct_change().fillna(0)
    held = pos.shift(1).fillna(0)
    turnover = pos.diff().abs().fillna(pos.abs())
    return held * r - turnover.shift(1).fillna(0) * cost, held


def n_trades(pos):
    """number of round-trip entries (position goes from 0/opposite to non-zero)."""
    p = np.sign(pos.fillna(0))
    return int(((p != 0) & (p != p.shift(1).fillna(0))).sum())


# ---------------------------------------------------------------- 1. MACD oscillator
def macd(df, ma1=10, ma2=21):
    # repo: long when short SMA >= long SMA, flat otherwise; author recommends 10/21
    s = df['Close'].rolling(ma1).mean()
    l = df['Close'].rolling(ma2).mean()
    pos = pd.Series(np.where(s >= l, 1.0, 0.0), index=df.index)
    pos[l.isna()] = 0
    return pos


# ---------------------------------------------------------------- 2. Pair trading
def eg_method(X, Y):
    m1 = sm.OLS(Y, sm.add_constant(X)).fit()
    eps = m1.resid
    if adfuller(eps)[1] > 0.05:
        return False, m1
    Xd = sm.add_constant(pd.concat([X.diff(), eps.shift(1)], axis=1).dropna())
    Yd = Y.diff().dropna()
    m2 = sm.OLS(Yd, Xd).fit()
    return (list(m2.params)[-1] <= 0), m1


def pair_trading(a1, a2, bandwidth=250, z_thr=1.0):
    """a1, a2: Close series on a common index. Returns (pos1, pos2) in units of capital.
    repo: re-test cointegration daily on a rolling 250-day window; when it starts,
    freeze the model and trade the z-score of the residual; hold only while |z| > 1;
    close everything when cointegration breaks."""
    idx = a1.index
    s1 = np.zeros(len(idx))
    prev = False
    model = None; mu = sd = None
    x = a1.values; y = a2.values
    for i in range(bandwidth, len(idx)):
        X = pd.Series(x[i - bandwidth:i]); Y = pd.Series(y[i - bandwidth:i])
        status, m = eg_method(X, Y)
        if not prev and status:
            model, mu, sd = m, m.resid.mean(), m.resid.std()
        if status and model is not None:
            fitted = model.params.iloc[0] + model.params.iloc[1] * x[i]
            z = (y[i] - fitted - mu) / sd
            # FIX: repo sets thresholds at z(start) +/- std(resid) on an already-normalised z,
            # which mixes units; we use the documented intent: one sigma (z = +/-1)
            if z > z_thr:
                s1[i] = 1     # asset2 rich vs asset1 -> long asset1, short asset2
            elif z < -z_thr:
                s1[i] = -1
        if not status:
            model = None
        prev = status
    s1 = pd.Series(s1, index=idx)
    return 0.5 * s1, -0.5 * s1


# ---------------------------------------------------------------- 3. Heikin-Ashi
def heikin_ashi(df, stls=3):
    o, h, l, c = (df[k].values for k in ['Open', 'High', 'Low', 'Close'])
    n = len(df)
    hc = (o + h + l + c) / 4
    ho = np.zeros(n); ho[0] = o[0]
    for i in range(1, n):
        ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    hh = np.maximum.reduce([ho, hc, l, h])
    hl = np.minimum.reduce([ho, hc, l, h])
    units = np.zeros(n); u = 0
    for i in range(1, n):
        # repo long trigger (as written): strong bearish HA candle with no upper wick,
        # larger body than the previous bearish candle -> accumulate up to 3 longs
        if (ho[i] > hc[i] and ho[i] == hh[i] and
                abs(ho[i] - hc[i]) > abs(ho[i - 1] - hc[i - 1]) and ho[i - 1] > hc[i - 1]):
            u = min(u + 1, stls)
        # exit: bullish HA candle with no lower wick after a bullish candle -> clear all
        elif ho[i] < hc[i] and ho[i] == hl[i] and ho[i - 1] < hc[i - 1]:
            u = 0
        units[i] = u
    return pd.Series(units / stls, index=df.index)


# ---------------------------------------------------------------- 4. London Breakout (hourly)
def london_breakout(h1, cost, risky_stop=0.0075, tokyo_hour=7, london_hour=8, close_hour=17):
    """h1: hourly OHLC indexed by UTC bar-open time.
    repo: thresholds = high/low of the last hour before London open (07:00-07:59 GMT);
    trade the first breakout in the opening 30 min; skip if price is already more than
    `risky_stop` beyond the threshold; take profit/stop at +/- risky_stop/2; flat at close.
    SCALE: repo uses 0.01 absolute on GBP/USD (~0.77%); we use 0.75% of price.
    Hourly bars can't see inside the first 30 minutes, so the 08:00 bar is the entry bar;
    if both thresholds break inside that bar the day is skipped (order unknown); if stop and
    target are both inside a later bar we assume the stop hit first (conservative).
    Returns a daily Series of net strategy returns and a daily trade count."""
    out = {}; trades = 0
    for day, g in h1.groupby(h1.index.normalize()):
        hrs = g.index.hour
        tk = g[hrs == tokyo_hour]; ld = g[hrs == london_hour]
        if tk.empty or ld.empty:
            out[day] = 0.0; continue
        up, lo = tk['High'].iloc[0], tk['Low'].iloc[0]
        b = ld.iloc[0]
        brk_up, brk_dn = b['High'] > up, b['Low'] < lo
        if brk_up == brk_dn:            # no breakout, or both (ambiguous) -> no trade
            out[day] = 0.0; continue
        side = 1 if brk_up else -1
        entry = max(up, b['Open']) if side == 1 else min(lo, b['Open'])
        lvl = up if side == 1 else lo
        if abs(entry - lvl) / lvl > risky_stop:
            out[day] = 0.0; continue
        tgt = entry * (1 + side * risky_stop / 2); stp = entry * (1 - side * risky_stop / 2)
        later = g[(hrs > london_hour) & (hrs < close_hour)]
        exit_px = later['Close'].iloc[-1] if len(later) else b['Close']
        for _, bar in later.iterrows():
            hit_stop = bar['Low'] <= stp if side == 1 else bar['High'] >= stp
            hit_tgt = bar['High'] >= tgt if side == 1 else bar['Low'] <= tgt
            if hit_stop:
                exit_px = stp; break
            if hit_tgt:
                exit_px = tgt; break
        out[day] = side * (exit_px / entry - 1) - 2 * cost
        trades += 1
    return pd.Series(out).sort_index(), trades


# ---------------------------------------------------------------- 5. Dual Thrust (daily bars)
def dual_thrust(df, cost, rg=5, param=0.5):
    """repo: range from the previous rg days; upper = open + param*range,
    lower = open - (1-param)*range; long above, short below, reverse on the opposite
    break, flat at the end of the day.
    FIX: repo's range window includes the current day (look-ahead); we use the prior rg days.
    Daily OHLC can't show the intraday order when both thresholds break on one day,
    so we assume the worse of the two possible orders."""
    H = df['High'].shift(1).rolling(rg).max(); L = df['Low'].shift(1).rolling(rg).min()
    HC = df['Close'].shift(1).rolling(rg).max(); LC = df['Close'].shift(1).rolling(rg).min()
    rng = np.maximum(H - LC, HC - L)
    o, h, l, c = df['Open'], df['High'], df['Low'], df['Close']
    up = o + param * rng; lo = o - (1 - param) * rng
    ret = pd.Series(0.0, index=df.index); trades = 0
    for i in range(len(df)):
        if np.isnan(rng.iloc[i]) or rng.iloc[i] <= 0:
            continue
        U, D, C, O = up.iloc[i], lo.iloc[i], c.iloc[i], o.iloc[i]
        bu, bd = h.iloc[i] > U, l.iloc[i] < D
        if bu and bd:
            a = (D - U) + (D - C)            # long first, reversed short
            b_ = (D - U) + (C - U)           # short first, reversed long
            ret.iloc[i] = min(a, b_) / O - 4 * cost
            trades += 2
        elif bu:
            ret.iloc[i] = (C - U) / O - 2 * cost; trades += 1
        elif bd:
            ret.iloc[i] = (D - C) / O - 2 * cost; trades += 1
    return ret, trades


# ---------------------------------------------------------------- 6. Parabolic SAR
def parabolic_sar(df, af0=0.02, step=0.02, afmax=0.2):
    h, l, c = df['High'].values, df['Low'].values, df['Close'].values
    n = len(df); sar = np.zeros(n); trend = np.zeros(n)
    trend[1] = 1 if c[1] > c[0] else -1
    sar[1] = l[0] if trend[1] == 1 else h[0]
    ep = h[1] if trend[1] == 1 else l[1]; af = af0
    for i in range(2, n):
        s = sar[i - 1] + af * (ep - sar[i - 1])
        if trend[i - 1] == 1:
            s = min(s, l[i - 1], l[i - 2])
            if l[i] < s:
                trend[i] = -1; s = ep; ep = l[i]; af = af0
            else:
                trend[i] = 1
                if h[i] > ep: ep = h[i]; af = min(af + step, afmax)
        else:
            s = max(s, h[i - 1], h[i - 2])
            if h[i] > s:
                trend[i] = 1; s = ep; ep = h[i]; af = af0
            else:
                trend[i] = -1
                if l[i] < ep: ep = l[i]; af = min(af + step, afmax)
        sar[i] = s
    # repo: long while SAR is below price, flat otherwise
    pos = pd.Series(np.where(sar < c, 1.0, 0.0), index=df.index); pos.iloc[:2] = 0
    return pos


# ---------------------------------------------------------------- 7. Bollinger Bands bottom-W
def bollinger_w(df, period=75, tol=0.01):
    """repo node logic (k bottom touches lower band, j middle peak at mid band ~ current
    upper band, m second bottom above lower band but below k, i breakout above upper band).
    SCALE: repo alpha=0.0001 absolute on GBP/USD; we use 1% of price as the 'touch' tolerance.
    FIX: repo exits when band std < 0.0001 absolute, which never happens on these assets
    (positions would be held forever); we exit on 'contraction' defined as bandwidth falling
    below its own 75-day average, keeping the repo's idea of exiting when momentum fades."""
    p = df['Close']; std = p.rolling(20).std(); mid = p.rolling(20).mean()
    upb = mid + 2 * std; lob = mid - 2 * std
    bw = (upb - lob) / mid; bw_avg = bw.rolling(period).mean()
    P, M, U, Lw = p.values, mid.values, upb.values, lob.values
    pos = np.zeros(len(p)); holding = 0
    for i in range(period + 20, len(p)):
        entered = False
        if holding == 0 and P[i] > U[i]:
            j = k = None
            for jj in range(i, i - period, -1):
                if abs(M[jj] - P[jj]) < tol * P[jj] and abs(M[jj] - U[i]) < tol * P[i]:
                    j = jj; break
            if j is not None:
                for kk in range(j, i - period, -1):
                    if abs(Lw[kk] - P[kk]) < tol * P[kk]:
                        k = kk; break
            if k is not None:
                thr = P[k]
                for mm in range(i, j, -1):
                    if (P[mm] - Lw[mm] < tol * P[mm]) and P[mm] > Lw[mm] and P[mm] < thr:
                        holding = 1; entered = True; break
        if holding and not entered and bw.iloc[i] < bw_avg.iloc[i]:
            holding = 0
        pos[i] = holding
    return pd.Series(pos, index=df.index)


# ---------------------------------------------------------------- 8. RSI pattern recognition
def rsi(close, n=14):
    d = close.diff(); up = d.clip(lower=0); dn = -d.clip(upper=0)
    au = up.ewm(alpha=1 / n, adjust=False).mean(); ad = dn.ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + au / ad)


def rsi_head_shoulders(df, lag=14, period=25, delta=0.01, head=1.1, shoulder=1.1,
                       exit_rsi=4, exit_days=5):
    """repo pattern_recognition(): finds a head-and-shoulders top in the last 25 days
    and goes SHORT at the neckline; exits when RSI rises 4 points above the entry RSI
    or after 5 days.  SCALE: repo delta=0.2 in price units; we use 1% of price."""
    C = df['Close'].values; R = rsi(df['Close'], lag).values
    n = len(C); pos = np.zeros(n); entry_rsi = 0.0; counter = 0
    for i in range(period + lag, n):
        moveon = False; d = delta * C[i]
        win = C[i - period:i]
        if entry_rsi == 0 and C[i] != win.max():
            j = i - period + int(np.argmax(win))
            if abs(C[j] - C[i]) > head * d:
                bottom = C[i]; moveon = True
            if moveon:
                moveon = False
                for k in range(j, i):
                    if abs(C[k] - bottom) < d: moveon = True; break
            if moveon:
                moveon = False
                for l in range(j, i - period + 1, -1):
                    if abs(C[l] - bottom) < d: moveon = True; break
            if moveon:
                moveon = False
                for m in range(i - period, l):
                    if abs(C[m] - bottom) < d: moveon = True; break
            if moveon:
                moveon = False
                if l > m:
                    nn = m + int(np.argmax(C[m:l]))
                    if C[nn] - bottom > shoulder * d and C[j] - C[nn] > shoulder * d:
                        top = C[nn]; moveon = True
            if moveon:
                moveon = False
                for o in range(k, i):
                    if abs(C[o] - top) < d:
                        entry_rsi = R[i]; counter = 0; moveon = True; break
        if entry_rsi != 0 and not moveon:
            counter += 1
            if R[i] - entry_rsi > exit_rsi or counter > exit_days:
                entry_rsi = 0; counter = 0
        pos[i] = -1.0 if entry_rsi != 0 else 0.0
    return pd.Series(pos, index=df.index)


# ---------------------------------------------------------------- 9. Shooting Star
def shooting_star(df, lower_bound=0.2, body_size=0.5, stop_threshold=0.05, holding_period=7):
    """repo: 8-condition shooting star -> SHORT; exit at +/-5% or after 7 days.
    FIX: conditions 7-8 use the NEXT day's high/close, so the signal is only knowable at
    the next day's close -> we enter then.
    FIX: condition 3 compares the body with abs(mean(open-close)), which is ~0 because up and
    down days cancel, so the pattern almost never fires (0 trades on every market here). We use
    the intended 'small real body' test: body < half the average absolute body, to date only."""
    o, h, l, c = df['Open'], df['High'], df['Low'], df['Close']
    mean_body = (o - c).abs().expanding().mean()
    cond = ((o >= c) & ((c - l) < lower_bound * (c - o).abs()) &
            ((o - c).abs() < mean_body * body_size) & ((h - o) >= 2 * (o - c)) &
            (c >= c.shift(1)) & (c.shift(1) >= c.shift(2)) &
            (h.shift(-1) <= h) & (c.shift(-1) <= c))
    sig = cond.shift(1, fill_value=False)        # FIX: known at close of t+1
    C = c.values; pos = np.zeros(len(c)); i = 0
    while i < len(c):
        if sig.iloc[i]:
            entry = C[i]; t = i
            while True:
                t += 1
                if t >= len(c): break
                pos[t - 1] = -1
                if abs(C[t] / entry - 1) > stop_threshold or (t - i) >= holding_period:
                    break
            i = t
        i += 1
    return pd.Series(pos, index=df.index)

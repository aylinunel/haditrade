import pandas as pd, json, glob, os
S='/tmp/claude-0/data_src'; O='/home/claude/bt/data'
p=pd.read_parquet(f'{S}/prices.parquet')
for t in ['SPY','DIA']:
    d=p[p.ticker==t].sort_values('date').set_index('date')
    f=d.adj_close/d.close                       # dividend/split adjustment factor
    out=pd.DataFrame({c.capitalize():d[c]*f for c in ['open','high','low','close']})
    out['Volume']=d.volume
    out.index.name='Date'; out.to_csv(f'{O}/{t}_1d.csv')
    print(t,out.index.min().date(),out.index.max().date(),len(out))
def kl(sym,iv):
    rows=[]
    for fn in sorted(glob.glob(f'{S}/sk/.klines-cache/{sym}/{iv}/*.json')):
        rows+=json.load(open(fn))
    d=pd.DataFrame([r[:6] for r in rows],columns=['t','Open','High','Low','Close','Volume']).astype(float)
    d.index=pd.to_datetime(d.t,unit='ms',utc=True).dt.tz_localize(None); d.index.name='Date'
    d=d.drop(columns='t'); d=d[~d.index.duplicated()].sort_index()
    d.to_csv(f'{O}/{sym[:3]}_{iv}.csv'); print(sym,iv,d.index.min(),d.index.max(),len(d))
for s in ['BTCUSDT','ETHUSDT']:
    for iv in ['1d','1h']: kl(s,iv)
for sym in ['ES','GC','CL','NQ']:
    for fn in glob.glob(f'{S}/cme/{sym}/{sym}_*.csv'):
        d=pd.read_csv(fn); d.columns=[c.lower() for c in d.columns]
        d.index=pd.to_datetime(d.datetime,utc=True).dt.tz_localize(None); d.index.name='Date'
        d=d[['open','high','low','close','volume']]; d.columns=['Open','High','Low','Close','Volume']
        iv='1h' if '_1h_' in fn else '1d'
        d.sort_index().to_csv(f'{O}/{sym}_{iv}.csv')
        r=d.Close.pct_change().abs()
        print(sym,iv,d.index.min(),d.index.max(),len(d),'max |daily move|=%.3f'%r.max(), r.idxmax())

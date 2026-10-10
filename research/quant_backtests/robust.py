import pandas as pd, numpy as np, json, strategies as S
L=lambda n: pd.read_csv(f'data/{n}.csv',index_col=0,parse_dates=True)
out={}
def stats(r,ann):
    eq=(1+r).cumprod(); yrs=len(r)/ann
    return dict(cagr=eq.iloc[-1]**(1/yrs)-1, sharpe=r.mean()/r.std()*np.sqrt(ann), mdd=(eq/eq.cummax()-1).min())
for k,ann,cost in [('SPY',252,0.0005),('BTC',365,0.001),('ETH',365,0.001)]:
    d=L(f'{k}_1d'); bh=d.Close.pct_change().fillna(0)
    grid=[]
    for a,b in [(5,20),(10,21),(12,26),(20,50),(50,200)]:
        r,_=S.pos_to_returns(S.macd(d,a,b),d,cost); s=stats(r,ann); s['params']=f'{a}/{b}'; grid.append(s)
    # cost sensitivity for default
    cs=[]
    for m in [1,2,3]:
        r,_=S.pos_to_returns(S.macd(d),d,cost*m); s=stats(r,ann); s['cost']=cost*m; cs.append(s)
    r,_=S.pos_to_returns(S.macd(d),d,cost)
    yr=pd.DataFrame({'MACD':(1+r).groupby(r.index.year).prod()-1,'Buy & hold':(1+bh).groupby(bh.index.year).prod()-1})
    # halves
    mid=d.index[len(d)//2]
    halves=[]
    for name,msk in [('first half',r.index<mid),('second half',r.index>=mid)]:
        s1=stats(r[msk],ann); s2=stats(bh[msk],ann)
        halves.append(dict(period=f"{name} ({r.index[msk][0].year}–{r.index[msk][-1].year})",macd_cagr=s1['cagr'],macd_sharpe=s1['sharpe'],bh_cagr=s2['cagr'],bh_sharpe=s2['sharpe']))
    out[k]=dict(grid=grid,cost=cs,years=[[int(y),float(a),float(b)] for y,(a,b) in yr.iterrows()],halves=halves)
    print('\n==',k); print(pd.DataFrame(grid).round(3).to_string(index=False)); print(pd.DataFrame(cs).round(3).to_string(index=False))
    print(pd.DataFrame(halves).round(3).to_string(index=False)); print(yr.round(2).T.to_string())
json.dump(out,open('robust.json','w'),default=float)

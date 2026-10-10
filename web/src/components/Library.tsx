import { useEffect, useState } from 'react'
import { LineSeries } from 'lightweight-charts'
import { api, cssVar, usePoll } from '../api'
import { removeSeries, useChart } from './charts'

interface ResultRow {
  market: string; market_label: string; strategy: string; tested: string; reason: string | null; note: string | null
  years: number | null; cagr: number | null; sharpe: number | null; max_drawdown: number | null; trades: number | null
  bh_cagr: number | null; bh_sharpe: number | null; sharpe_vs_bh: number | null
}
interface Summary { results: ResultRow[]; markets: { market: string; label: string; source: string; start: string; end: string; years: number; cost_per_side: number }[]; caveats: string[] }
interface Orcl { scenarios: { id: string; name: string; label: string; portfolio: Record<string, number>; benchmark: Record<string, number>; strategies: { id: string; name: string; metrics: Record<string, number> }[] }[] }

function heat(v: number | null, span = 1) {
  if (v == null) return 'var(--surface-3)'
  const t = Math.max(-1, Math.min(1, v / span))
  return `color-mix(in srgb, ${t >= 0 ? 'var(--div-pos)' : 'var(--div-neg)'} ${Math.round(Math.abs(t) * 85)}%, var(--div-mid))`
}

function CurveChart({ market, strategy, theme }: { market: string; strategy: string; theme: string }) {
  const { container, chart } = useChart(theme)
  useEffect(() => {
    if (!chart) return
    let alive = true
    const created: ReturnType<typeof chart.addSeries>[] = []
    api<Record<string, [string, number][]>>(`/api/library/curves/${market}`).then(curves => {
      if (!alive) return
      const pairs: [string, string][] = [[strategy, cssVar('--s1')], ['Buy & hold', cssVar('--s2')]]
      for (const [name, color] of pairs) {
        if (!curves[name]) continue
        const series = chart.addSeries(LineSeries, { color, lineWidth: 2, title: name, priceLineVisible: false })
        series.setData(curves[name].map(([d, v]) => ({ time: d, value: v })))
        created.push(series)
      }
      chart.timeScale().fitContent()
    }).catch(() => undefined)
    return () => { alive = false; removeSeries(chart, created) }
  }, [chart, market, strategy])
  return <div ref={container} className="chart-box" style={{ height: 260 }} />
}

export default function Library({ theme }: { theme: string }) {
  const { data } = usePoll<Summary>('/api/library/summary', 600000)
  const { data: orcl } = usePoll<Orcl>('/api/library/orcl', 600000)
  const [pick, setPick] = useState<{ market: string; strategy: string }>({ market: 'BTC', strategy: 'MACD oscillator' })
  if (!data) return <p className="muted">Loading research library…</p>
  const markets = data.markets.map(m => m.market)
  const strategies = [...new Set(data.results.map(r => r.strategy))].filter(s => s !== 'Buy & hold')
  const cell = (m: string, s: string) => data.results.find(r => r.market === m && r.strategy === s)
  const picked = cell(pick.market, pick.strategy)
  return (
    <div className="stack">
      <p className="small secondary" style={{ margin: 0 }}>Your <b>quant_backtests</b> results: daily long/cash backtests with fixed costs. Cell = strategy Sharpe minus buy-and-hold Sharpe (blue beats, red trails). Click a cell for its growth curve.</p>
      <div className="table-wrap">
        <div className="heat" style={{ gridTemplateColumns: `minmax(130px, 1.3fr) repeat(${markets.length}, minmax(64px, 1fr))`, minWidth: 560 }}>
          <div className="head" />
          {markets.map(m => <div key={m} className="head">{m}</div>)}
          {strategies.map(s => [
            <div key={`${s}-l`} className="small" style={{ alignSelf: 'center' }}>{s}</div>,
            ...markets.map(m => {
              const r = cell(m, s)
              const tested = r?.tested === 'yes'
              return (
                <button key={`${s}${m}`} className="cell" onClick={() => tested && setPick({ market: m, strategy: s })} disabled={!tested}
                  title={tested ? `${r!.market_label}: Sharpe ${r!.sharpe?.toFixed(2)} vs B&H ${r!.bh_sharpe?.toFixed(2)} · CAGR ${((r!.cagr ?? 0) * 100).toFixed(1)}% · max DD ${((r!.max_drawdown ?? 0) * 100).toFixed(0)}% · ${r!.years}y` : r?.reason ?? 'n/a'}
                  style={{ background: tested ? heat(r!.sharpe_vs_bh) : 'var(--surface-3)', border: pick.market === m && pick.strategy === s ? '2px solid var(--text-primary)' : '0', borderRadius: 4, padding: 4 }}>
                  <span className="num" style={{ fontWeight: 600 }}>{tested ? (r!.sharpe_vs_bh! >= 0 ? '+' : '') + r!.sharpe_vs_bh!.toFixed(2) : 'n/a'}</span>
                </button>
              )
            }),
          ])}
        </div>
      </div>
      {picked && (
        <div>
          <h3>{picked.market_label}: {picked.strategy} vs buy & hold</h3>
          <div className="legend"><span><span className="swatch" style={{ background: 'var(--s1)' }} />{picked.strategy}</span><span><span className="swatch" style={{ background: 'var(--s2)' }} />Buy & hold</span><span className="muted">growth of 1, weekly</span></div>
          <CurveChart market={pick.market} strategy={pick.strategy} theme={theme} />
          <p className="small secondary">CAGR {((picked.cagr ?? 0) * 100).toFixed(1)}% vs {((picked.bh_cagr ?? 0) * 100).toFixed(1)}% · Sharpe {picked.sharpe?.toFixed(2)} vs {picked.bh_sharpe?.toFixed(2)} · max drawdown {((picked.max_drawdown ?? 0) * 100).toFixed(0)}% · {picked.trades} trades over {picked.years} years. {picked.note}</p>
        </div>
      )}
      <ul className="small muted" style={{ margin: 0 }}>{data.caveats.map(c => <li key={c}>{c}</li>)}</ul>
      {orcl && (
        <div>
          <h3>Strategy City v0.2 legacy ORCL windows (separate $10k sleeves)</h3>
          <div className="table-wrap"><table>
            <thead><tr><th>Window</th><th>Portfolio return</th><th>Buy & hold</th></tr></thead>
            <tbody>{orcl.scenarios.map(s => <tr key={s.id}><td>{s.name} · {s.label}</td><td>{(s.portfolio.return_pct ?? 0).toFixed(1)}%</td><td>{(s.benchmark.return_pct ?? 0).toFixed(1)}%</td></tr>)}</tbody>
          </table></div>
        </div>
      )}
    </div>
  )
}

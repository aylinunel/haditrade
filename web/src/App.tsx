import { useCallback, useEffect, useState } from 'react'
import { fmt, getToken, setToken, usePoll, useAgentEvents, STRATEGIES, STRATEGY_LABEL, STRATEGY_VAR,
  type AgentEvent, type Bars, type Decision, type ModelView, type RuntimeName, type Status, type StrategyId } from './api'
import City, { type Pulse } from './components/City'
import DecisionFeed from './components/DecisionFeed'
import PriceChart from './components/PriceChart'
import IndicatorLab from './components/IndicatorLab'
import { EdgeHeatmap, Gates, ModelExplainer, OutcomeStrip } from './components/Learning'
import Ledger, { type LedgerView } from './components/Ledger'
import Library from './components/Library'
import Controls from './components/Controls'

type Tab = 'decisions' | 'learning' | 'ledger' | 'library' | 'controls'

function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState<string>(() => {
    let initial = 'dark'
    try { initial = localStorage.getItem('agent-theme') ?? 'dark' } catch { /* storage unavailable */ }
    document.documentElement.dataset.theme = initial  // before first render: canvases read CSS tokens while rendering
    return initial
  })
  const toggle = () => {
    const next = theme === 'dark' ? 'light' : 'dark'
    document.documentElement.dataset.theme = next
    try { localStorage.setItem('agent-theme', next) } catch { /* storage unavailable */ }
    setTheme(next)
  }
  return [theme, toggle]
}

function Login({ onDone, error }: { onDone: () => void; error?: string | null }) {
  const [value, setValue] = useState('')
  return (
    <div className="login panel">
      <h1 style={{ fontSize: 20 }}>Strategy City · AI trading agent</h1>
      <p className="secondary">Enter the <code>STRATEGY_API_TOKEN</code> configured on your backend. It stays in this browser tab's session storage and is sent only to your own server.</p>
      {error && <div className="banner">{error}</div>}
      <form onSubmit={e => { e.preventDefault(); setToken(value.trim()); onDone() }} className="stack" style={{ marginTop: 12 }}>
        <input type="password" autoComplete="off" value={value} onChange={e => setValue(e.target.value)} placeholder="API token" aria-label="API token" />
        <button className="primary" type="submit" disabled={!value.trim()}>Connect</button>
      </form>
      <p className="muted small">Paper research only: no broker account, no live orders, no investment advice.</p>
    </div>
  )
}

export default function App() {
  const [theme, toggleTheme] = useTheme()
  const [authed, setAuthed] = useState(Boolean(getToken()))
  const [authError, setAuthError] = useState<string | null>(null)
  const [runtime, setRuntime] = useState<RuntimeName>('replay')
  const [tab, setTab] = useState<Tab>('decisions')
  const [detailTab, setDetailTab] = useState<'chart' | 'lab'>('chart')
  const [selected, setSelected] = useState<{ symbol: string; strategy: StrategyId }>({ symbol: 'BTCUSDT', strategy: 'sma' })
  const [version, setVersion] = useState(0)
  const [pulses, setPulses] = useState<Pulse[]>([])
  const bump = useCallback(() => setVersion(v => v + 1), [])

  const base = authed ? `/api/agent/${runtime}` : null
  const status = usePoll<Status>(base && `${base}/status`, 4000, version)
  const decisions = usePoll<Decision[]>(base && `${base}/decisions?limit=80`, 6000, version)
  const model = usePoll<ModelView>(base && `${base}/model`, 8000, version)
  const ledger = usePoll<LedgerView>(base && `${base}/ledger`, 6000, version)
  const bars = usePoll<Bars>(base && `${base}/bars/${selected.symbol}`, 10000, version)

  useEffect(() => {
    if (status.error && /token|API token|401/i.test(status.error)) { setAuthed(false); setAuthError(status.error) }
  }, [status.error])

  const onEvents = useCallback((events: AgentEvent[]) => {
    if (events.some(e => e.type === 'reset')) setPulses([])
    const fresh: Pulse[] = events.flatMap(e => (e.decisions ?? []).map(d => ({ key: d.decision_id, symbol: d.symbol, strategy_id: d.strategy_id, outcome: d.outcome, blocked_by: d.blocked_by, at: performance.now() })))
    if (fresh.length) setPulses(p => [...p, ...fresh].slice(-40))
    if (events.length) bump()
  }, [bump])
  const { connected } = useAgentEvents(runtime, onEvents)

  useEffect(() => { setPulses([]) }, [runtime])
  useEffect(() => {
    const symbols = status.data?.symbols ?? []
    if (symbols.length && !symbols.includes(selected.symbol)) setSelected(s => ({ ...s, symbol: symbols[0] }))
  }, [status.data?.symbols, selected.symbol])

  if (!authed) return <Login onDone={() => { setAuthed(true); setAuthError(null); bump() }} error={authError} />
  const s = status.data
  const driver = s?.driver
  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">Strategy City <small>four-agent paper trader</small></span>
        <div className="seg" role="group" aria-label="Runtime">
          <button aria-pressed={runtime === 'replay'} onClick={() => setRuntime('replay')}>Replay sandbox</button>
          <button aria-pressed={runtime === 'live'} onClick={() => setRuntime('live')}>Live paper</button>
        </div>
        {s && (
          <div className="chips">
            <span className="chip">{driver?.running ? '● running' : '○ stopped'} <b>{connected ? 'stream on' : 'stream off'}</b></span>
            <span className="chip">clock <b>{fmt.time(s.clock)}</b></span>
            <span className="chip">equity <b>{fmt.eur(s.ledger.equity_eur)}</b></span>
            <span className="chip">labels learned <b>{s.training_labels.toLocaleString()}</b></span>
            <span className="chip">supported edges <b>{s.edge_supported_buckets}</b></span>
            <span className="chip">champion <b>v{s.champion_version}</b></span>
            {s.ledger.halted && <span className="chip" style={{ borderColor: 'var(--critical)' }}>⛔ <b>halted</b></span>}
          </div>
        )}
        <span className="spacer" />
        <button className="ghost" onClick={toggleTheme} aria-label="Toggle light and dark theme">{theme === 'dark' ? '☀ Light' : '☾ Dark'}</button>
        <button className="ghost" onClick={() => { setToken(''); setAuthed(false) }}>Sign out</button>
      </header>
      {!s ? <p className="muted" style={{ padding: 16 }}>{status.error ?? 'Connecting to the agent…'}</p> : (
        <main className="layout">
          <section className="stack">
            <div className="panel">
              <h2>Strategy City · {runtime === 'replay' ? 'replay sandbox' : 'live paper'}</h2>
              <City status={s} edge={model.data?.edge ?? []} positions={ledger.data?.summary.positions ?? []} pulses={pulses}
                selected={selected} onSelect={setSelected} theme={theme} />
              {!s.training_labels && <div className="banner" style={{ marginTop: 8 }}>
                {runtime === 'replay' ? 'Nothing learned yet. Open Controls → Run replay to watch the agents learn on a year of real BTC/ETH/SOL bars.' : 'Live feed idle. Open Controls → Start live paper trading.'}
              </div>}
            </div>
            <div className="panel">
              <div className="row" style={{ marginBottom: 8 }}>
                <h2 style={{ margin: 0 }}>{fmt.short(selected.symbol)} · <span style={{ color: `var(${STRATEGY_VAR[selected.strategy]})` }}>■</span> {STRATEGY_LABEL[selected.strategy]}</h2>
                <span className="spacer" />
                <select value={selected.symbol} onChange={e => setSelected({ ...selected, symbol: e.target.value })} aria-label="Symbol">
                  {(s.symbols.length ? s.symbols : ['BTCUSDT']).map(x => <option key={x} value={x}>{fmt.short(x)}</option>)}
                </select>
                <select value={selected.strategy} onChange={e => setSelected({ ...selected, strategy: e.target.value as StrategyId })} aria-label="Strategy">
                  {STRATEGIES.map(x => <option key={x} value={x}>{STRATEGY_LABEL[x]}</option>)}
                </select>
              </div>
              <div className="tabs" role="tablist">
                <button role="tab" aria-selected={detailTab === 'chart'} onClick={() => setDetailTab('chart')}>Price & signals</button>
                <button role="tab" aria-selected={detailTab === 'lab'} onClick={() => setDetailTab('lab')}>Indicator lab (step-through)</button>
              </div>
              {bars.data ? (detailTab === 'chart'
                ? <PriceChart bars={bars.data} strategy={selected.strategy} theme={theme} />
                : <IndicatorLab bars={bars.data} strategy={selected.strategy} />)
                : <p className="muted">No bars for this symbol yet.</p>}
            </div>
          </section>
          <section className="panel">
            <div className="tabs" role="tablist">
              {([['decisions', 'Agent decisions'], ['learning', 'Learning'], ['ledger', 'Paper ledger'], ['library', 'Backtest library'], ['controls', 'Controls']] as [Tab, string][]).map(([id, label]) => (
                <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{label}</button>
              ))}
            </div>
            {tab === 'decisions' && <DecisionFeed decisions={decisions.data ?? []} runtime={runtime} />}
            {tab === 'learning' && (model.data ? (
              <div className="stack">
                <div><h3 style={{ marginTop: 0 }}>Empirical edge (what the researcher learned from matured 48h outcomes)</h3><EdgeHeatmap rows={model.data.edge} /></div>
                <OutcomeStrip model={model.data} />
                <div><h3>Model explainer</h3><ModelExplainer model={model.data} /></div>
                <div><h3>Champion / challenger promotion gates</h3><Gates model={model.data} runtime={runtime} onChanged={bump} /></div>
              </div>
            ) : <p className="muted">Loading model…</p>)}
            {tab === 'ledger' && (ledger.data ? <Ledger view={ledger.data} policy={s.policy} theme={theme} /> : <p className="muted">Loading ledger…</p>)}
            {tab === 'library' && <Library theme={theme} />}
            {tab === 'controls' && <Controls runtime={runtime} status={s} onChanged={bump} />}
          </section>
        </main>
      )}
    </div>
  )
}



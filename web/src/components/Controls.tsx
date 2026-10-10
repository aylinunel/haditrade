import { useEffect, useState } from 'react'
import { api, fmt, post, put, usePoll, type RuntimeName, type Settings, type Status } from '../api'

const RISK_FIELDS: { key: string; label: string; pct: boolean; step: number; min: number }[] = [
  { key: 'risk_per_trade_pct', label: 'Risk per trade', pct: true, step: 0.0005, min: 0.0005 },
  { key: 'daily_loss_pct', label: 'Daily loss limit', pct: true, step: 0.0005, min: 0.001 },
  { key: 'max_drawdown_pct', label: 'Drawdown halt', pct: true, step: 0.005, min: 0.01 },
  { key: 'max_position_pct', label: 'Max position size', pct: true, step: 0.01, min: 0.02 },
  { key: 'max_gross_exposure_pct', label: 'Max gross exposure', pct: true, step: 0.05, min: 0.1 },
  { key: 'max_concurrent_positions', label: 'Max open positions', pct: false, step: 1, min: 1 },
]

interface SettingsView { settings: Settings; hard_caps: Record<string, number>; audit: { at: string; before: Settings; after: Settings }[]; note: string }

function RiskSettings({ runtime, onSaved }: { runtime: RuntimeName; onSaved: () => void }) {
  const [version, setVersion] = useState(0)
  const { data } = usePoll<SettingsView>(`/api/agent/${runtime}/settings`, 30000, version)
  const [draft, setDraft] = useState<Settings | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  useEffect(() => { if (data) setDraft(structuredClone(data.settings)) }, [data])
  if (!data || !draft) return <p className="muted">Loading settings…</p>
  const save = async () => {
    try {
      await put(`/api/agent/${runtime}/settings`, draft)
      setMessage('Saved. Applies to new paper decisions only; the change is journaled.')
      setVersion(v => v + 1)
      onSaved()
    } catch (e) { setMessage((e as Error).message) }
  }
  return (
    <div className="stack" style={{ gap: 8 }}>
      <p className="small secondary" style={{ margin: 0 }}>{data.note} Hard caps = the original mid-risk profile (€500, 0.5% trade risk, 1.5% daily loss, 8% drawdown halt, 3 positions, no leverage).</p>
      {RISK_FIELDS.map(f => {
        const cap = data.hard_caps[f.key]
        const value = draft.risk[f.key]
        return (
          <div className="slider-row" key={f.key}>
            <label htmlFor={`r-${f.key}`}>{f.label}</label>
            <input id={`r-${f.key}`} type="range" min={f.min} max={cap} step={f.step} value={value}
              onChange={e => setDraft({ ...draft, risk: { ...draft.risk, [f.key]: f.pct ? Number(e.target.value) : Math.round(Number(e.target.value)) } })} />
            <span className="num">{f.pct ? fmt.pct(value * 100, f.step < 0.001 ? 2 : 1) : value} <span className="muted">/ {f.pct ? fmt.pct(cap * 100, 1) : cap}</span></span>
          </div>
        )
      })}
      <div className="slider-row">
        <label htmlFor="edge-min">Min edge samples</label>
        <input id="edge-min" type="range" min={20} max={Math.min(200, draft.edge_window)} step={5} value={draft.minimum_edge_samples}
          onChange={e => setDraft({ ...draft, minimum_edge_samples: Number(e.target.value) })} />
        <span className="num">{draft.minimum_edge_samples}</span>
      </div>
      <div className="slider-row">
        <label htmlFor="edge-window">Edge memory (samples)</label>
        <input id="edge-window" type="range" min={Math.max(40, draft.minimum_edge_samples)} max={500} step={10} value={draft.edge_window}
          onChange={e => setDraft({ ...draft, edge_window: Number(e.target.value) })} />
        <span className="num">{draft.edge_window}</span>
      </div>
      <label className="row small"><input type="checkbox" checked={draft.shorts_enabled} onChange={e => setDraft({ ...draft, shorts_enabled: e.target.checked })} />
        Allow paper shorts on crypto (assumes margin borrow at the profile's rate; ETFs never short)</label>
      <div className="row small">
        <span>Skeptic conflict policy:</span>
        <div className="seg" role="group" aria-label="Conflict policy">
          <button aria-pressed={draft.conflict_policy === 'evidence'} onClick={() => setDraft({ ...draft, conflict_policy: 'evidence' })}>Evidence-weighted</button>
          <button aria-pressed={draft.conflict_policy === 'strict'} onClick={() => setDraft({ ...draft, conflict_policy: 'strict' })}>Strict</button>
        </div>
      </div>
      <p className="muted small" style={{ margin: 0 }}>Evidence-weighted: an opposite signal vetoes only if it is fresh on the same bar or has supported edge. Strict: any active opposite signal vetoes (trend and reversal strategies then rarely agree).</p>
      <div className="row"><button className="primary" onClick={save}>Save settings</button>{message && <span className="small secondary">{message}</span>}</div>
      {data.audit.length > 0 && <details className="small"><summary>Settings history ({data.audit.length})</summary>
        <ul>{data.audit.slice().reverse().map((a, k) => <li key={k}>{fmt.time(a.at)}: risk/trade {(a.after.risk.risk_per_trade_pct * 100).toFixed(2)}%, shorts {a.after.shorts_enabled ? 'on' : 'off'}, conflicts {a.after.conflict_policy}</li>)}</ul>
      </details>}
    </div>
  )
}

function Exports() {
  const [name, setName] = useState<'freqtrade' | 'lean-main' | 'lean-signals'>('freqtrade')
  const [text, setText] = useState('')
  useEffect(() => { api<string>(`/api/exports/${name}`).then(setText).catch(e => setText(String(e))) }, [name])
  return (
    <div>
      <div className="row" style={{ marginBottom: 6 }}>
        <div className="seg" role="group" aria-label="Export">
          <button aria-pressed={name === 'freqtrade'} onClick={() => setName('freqtrade')}>Freqtrade strategy</button>
          <button aria-pressed={name === 'lean-main'} onClick={() => setName('lean-main')}>LEAN main.py</button>
          <button aria-pressed={name === 'lean-signals'} onClick={() => setName('lean-signals')}>LEAN signals.py</button>
        </div>
        <button onClick={() => navigator.clipboard?.writeText(text)}>Copy</button>
      </div>
      <p className="small muted" style={{ marginTop: 0 }}>Same five signals, entries, ATR stops and 48h exits (tested for identical targets). The skeptic and evidence gates stay in this agent. Files also live in <code>exports/</code>.</p>
      <pre className="code">{text}</pre>
    </div>
  )
}

export default function Controls({ runtime, status, onChanged }: { runtime: RuntimeName; status: Status; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [speed, setSpeed] = useState<number>(status.driver.speed ?? 8)
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null)
    try { await fn(); onChanged() } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  const d = status.driver
  return (
    <div className="stack">
      {runtime === 'replay' ? (
        <div>
          <h3 style={{ marginTop: 0 }}>Replay sandbox</h3>
          <p className="small secondary" style={{ marginTop: 0 }}>Real Binance 15-minute bars ({String(d.first ?? '').slice(0, 10)} → {String(d.last ?? '').slice(0, 10)}), fed one completed bar at a time with a simulated clock. The sandbox has its own model, ledger and journal; it never touches live.</p>
          <div className="row">
            {d.running ? <button onClick={() => run(() => post('/api/replay/control', { action: 'pause' }))} disabled={busy}>Pause</button>
              : <button className="primary" onClick={() => run(() => post('/api/replay/control', { action: 'start' }))} disabled={busy}>▶ Run replay</button>}
            <button onClick={() => run(() => post('/api/replay/control', { action: 'step', steps: 96 }))} disabled={busy || d.running}>Step 1 day</button>
            <button className="danger" onClick={() => window.confirm('Erase the replay sandbox (model, ledger, journal) and start over?') && run(() => post('/api/replay/control', { action: 'reset' }))} disabled={busy}>Reset sandbox</button>
          </div>
          <div className="slider-row" style={{ marginTop: 8 }}>
            <label htmlFor="speed">Speed (bars/second, 0 = max)</label>
            <input id="speed" type="range" min={0} max={60} value={speed} onChange={e => setSpeed(Number(e.target.value))}
              onMouseUp={() => run(() => post('/api/replay/control', { action: 'speed', speed }))} onKeyUp={() => run(() => post('/api/replay/control', { action: 'speed', speed }))} onTouchEnd={() => run(() => post('/api/replay/control', { action: 'speed', speed }))} />
            <span className="num">{speed || 'max'}</span>
          </div>
          <div className="row small secondary" style={{ marginTop: 6 }}>
            <div className="progress" style={{ flex: 1 }}><div style={{ width: `${((d.progress ?? 0) * 100).toFixed(1)}%` }} /></div>
            <span className="num">{d.position}/{d.total} bars · {(d.steps_per_second ?? 0).toFixed(1)} bars/s</span>
          </div>
          {d.last_error && <div className="banner">Replay stopped: {d.last_error}</div>}
        </div>
      ) : (
        <div>
          <h3 style={{ marginTop: 0 }}>Live paper feed</h3>
          <p className="small secondary" style={{ marginTop: 0 }}>Polls public, read-only market data every {String(d.poll_seconds)}s and runs the four agents on each newly completed bar. Paper only: there is no broker client in this codebase.</p>
          <ul className="small secondary">{Object.entries(d.sources ?? {}).map(([k, v]) => <li key={k}><b>{k}</b>: {v}</li>)}</ul>
          <div className="row">
            {d.running ? <button onClick={() => run(() => post('/api/live/control', { action: 'stop' }))} disabled={busy}>Stop feed</button>
              : <button className="primary" onClick={() => run(() => post('/api/live/control', { action: 'start' }))} disabled={busy}>▶ Start live paper trading</button>}
            <span className="small muted">Polls: {String(d.polls ?? 0)} · last ingest {fmt.time(d.last_ingest as string | null)}</span>
          </div>
          {d.last_error && <div className="banner" style={{ marginTop: 8 }}>Feed error: {d.last_error}. The loop keeps retrying.</div>}
          {(d.notes ?? []).map(n => <p key={n} className="small muted">{n}</p>)}
        </div>
      )}
      {status.ledger.halted && <button className="danger" onClick={() => run(() => post(`/api/agent/${runtime}/halt/acknowledge`))}>Acknowledge drawdown halt (after liquidation)</button>}
      {error && <div className="banner">{error}</div>}
      <div><h3>Risk & evidence settings ({runtime})</h3><RiskSettings runtime={runtime} onSaved={onChanged} /></div>
      <div><h3>Strategy exports</h3><Exports /></div>
    </div>
  )
}

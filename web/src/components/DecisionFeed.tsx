import { useState } from 'react'
import { fmt, post, ROLE_TITLE, STRATEGY_LABEL, STRATEGY_VAR, type Decision, type RuntimeName } from '../api'

function passed(d: { approved?: boolean; eligible?: boolean }) {
  return (d.approved ?? d.eligible) === true
}

function Narration({ runtime, id }: { runtime: RuntimeName; id: string }) {
  const [text, setText] = useState<string | null>(null)
  const [source, setSource] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const load = async () => {
    setBusy(true)
    try {
      const result = await post<{ text: string; source: { kind: string; model?: string; error?: string } }>(`/api/agent/${runtime}/decisions/${id}/narrate`)
      setText(result.text)
      setSource(result.source.kind === 'claude' ? `Claude (${result.source.model})` : `Template${result.source.error ? ` · ${result.source.error}` : ''}`)
    } catch (e) { setText((e as Error).message) } finally { setBusy(false) }
  }
  return text ? (
    <p className="small secondary" style={{ margin: '8px 0 0' }}>{text} <span className="muted">— {source}; narration cannot change the decision.</span></p>
  ) : <button className="ghost small" onClick={load} disabled={busy} style={{ marginTop: 6 }}>{busy ? 'Explaining…' : 'Explain in plain English'}</button>
}

export function DecisionCard({ d, runtime, open, onToggle }: { d: Decision; runtime: RuntimeName; open: boolean; onToggle: () => void }) {
  if (d.kind === 'settings_change') {
    return (
      <div className="decision">
        <header><span className="status">⚙ Settings changed</span><span className="muted small">{fmt.time(d.evaluated_at)}</span></header>
      </div>
    )
  }
  const approved = d.outcome === 'paper_only_approved'
  const p = d.proposal
  const researcher = d.roles.find(r => r.role === 'researcher')?.decision
  const estimate = researcher?.edge_estimate
  return (
    <div className="decision">
      <header onClick={onToggle} role="button" tabIndex={0} aria-expanded={open} onKeyDown={e => (e.key === 'Enter' || e.key === ' ') && onToggle()}>
        <span className={`status ${approved ? 'good' : 'critical'}`}>{approved ? '✔ Paper approved' : '✖ Rejected'}</span>
        <span><span className="swatch" style={{ background: `var(${STRATEGY_VAR[p.strategy_id]})` }} />{fmt.short(p.symbol)} · {STRATEGY_LABEL[p.strategy_id]} · <b>{p.side}</b></span>
        <span className="spacer" />
        <span className="muted small">{fmt.time(p.observed_at)}</span>
      </header>
      <div className="flow" aria-label="Agent pipeline">
        {d.roles.map(r => (
          <div key={r.role} className="node" style={{ borderColor: passed(r.decision) ? 'var(--good)' : 'var(--critical)' }}>
            {passed(r.decision) ? '✔' : '✖'} {ROLE_TITLE[r.role] ?? r.role}
          </div>
        ))}
        <div className="node" style={{ borderColor: approved ? 'var(--good)' : 'var(--border)' }}>{approved ? `Order ${fmt.eur(d.order?.notional_eur)}` : 'No order'}</div>
      </div>
      {open && (
        <>
          <div className="roles">
            {d.roles.map(r => (
              <div key={r.role} className={`role ${passed(r.decision) ? 'pass' : 'fail'}`}>
                <b>{ROLE_TITLE[r.role] ?? r.role}</b>
                <ul>{(r.decision.reasons ?? []).slice(0, 4).map((reason, i) => <li key={i}>{String(reason)}</li>)}</ul>
              </div>
            ))}
          </div>
          {estimate && (
            <p className="small secondary" style={{ margin: '8px 0 0' }}>
              Evidence: {estimate.samples}/{estimate.minimum_samples} matured samples · mean net {fmt.bps(estimate.mean_net_bps)} ·
              adjusted lower bound {fmt.bps(estimate.lower_net_bps)} after {estimate.roundtrip_cost_bps.toFixed(1)} bps costs ·
              model P(positive) {researcher?.probability != null ? (researcher.probability as number).toFixed(2) : '–'}
            </p>
          )}
          <Narration runtime={runtime} id={d.decision_id} />
        </>
      )}
    </div>
  )
}

export default function DecisionFeed({ decisions, runtime }: { decisions: Decision[]; runtime: RuntimeName }) {
  const [open, setOpen] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'approved' | 'rejected'>('all')
  const shown = decisions.filter(d => filter === 'all' || (filter === 'approved') === (d.outcome === 'paper_only_approved')).slice().reverse()
  const approvals = decisions.filter(d => d.outcome === 'paper_only_approved').length
  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <div className="seg" role="group" aria-label="Filter decisions">
          {(['all', 'approved', 'rejected'] as const).map(f => <button key={f} aria-pressed={filter === f} onClick={() => setFilter(f)}>{f}</button>)}
        </div>
        <span className="muted small">{decisions.length} recent · {approvals} approved. Each fresh signal is judged by all four agents; every reason is journaled with a hash chain.</span>
      </div>
      <div className="feed">
        {shown.length ? shown.map(d => <DecisionCard key={d.decision_id} d={d} runtime={runtime} open={open === d.decision_id} onToggle={() => setOpen(open === d.decision_id ? null : d.decision_id)} />)
          : <p className="muted">No decisions yet. Start the replay or the live feed.</p>}
      </div>
    </div>
  )
}

import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { fmt, post, STRATEGIES, STRATEGY_LABEL, STRATEGY_VAR, type EdgeRow, type ModelView, type RuntimeName } from '../api'

const FEATURE_LABEL: Record<string, string> = {
  signal_confidence: 'Signal confidence', is_short: 'Is short', stop_distance_pct: 'Stop distance',
  momentum_48h: '48h momentum', volatility_48h: '48h volatility', volume_zscore: 'Volume z-score',
  sma: 'Strategy = SMA', rsi: 'Strategy = RSI', bollinger: 'Strategy = Bollinger', atr_breakout: 'Strategy = ATR breakout', heikin: 'Strategy = Heikin-Ashi',
}

function divergingColor(value: number, span: number) {
  const t = Math.max(-1, Math.min(1, value / span))
  const pole = t >= 0 ? 'var(--div-pos)' : 'var(--div-neg)'
  return `color-mix(in srgb, ${pole} ${Math.round(Math.abs(t) * 85)}%, var(--div-mid))`
}

function Spark({ values }: { values: number[] }) {
  if (values.length < 2) return <span className="muted small">–</span>
  const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1
  const d = values.map((v, i) => `${i ? 'L' : 'M'}${(i / (values.length - 1) * 60).toFixed(1)},${(14 - (v - lo) / span * 12).toFixed(1)}`).join(' ')
  return <svg width={60} height={16} aria-hidden="true"><path d={d} stroke="var(--accent)" fill="none" strokeWidth={1.5} /></svg>
}

export function ModelExplainer({ model }: { model: ModelView }) {
  const [which, setWhich] = useState<'champion' | 'trainer' | 'challenger'>('trainer')
  const snapshot = model[which] ?? model.trainer
  const example = model.example?.transformed_features ?? model.feature_names.map(() => 0)
  const [inputs, setInputs] = useState<number[]>(example)
  const exampleKey = model.example ? `${model.example.symbol}${model.example.observed_at}${model.example.strategy_id}` : 'none'
  useEffect(() => { setInputs(model.example?.transformed_features ?? model.feature_names.map(() => 0)) }, [exampleKey]) // eslint-disable-line react-hooks/exhaustive-deps
  const history = useRef<number[][]>([])
  useEffect(() => {
    const last = history.current[history.current.length - 1]
    if (!last || last.some((w, k) => w !== model.trainer.weights[k])) history.current = [...history.current.slice(-59), model.trainer.weights]
  }, [model.trainer.weights])
  const contributions = inputs.map((v, k) => v * snapshot.weights[k])
  const logit = contributions.reduce((a, b) => a + b, 0) + snapshot.bias
  const probability = 1 / (1 + Math.exp(-Math.max(-30, Math.min(30, logit))))
  const maxAbs = Math.max(0.05, ...contributions.map(Math.abs))
  return (
    <div>
      <div className="row" style={{ marginBottom: 6 }}>
        <div className="seg" role="group" aria-label="Model">
          {(['trainer', 'champion', 'challenger'] as const).map(w => (
            <button key={w} aria-pressed={which === w} disabled={w === 'challenger' && !model.challenger} onClick={() => setWhich(w)}>
              {w === 'champion' ? `Champion v${model.champion_version} (frozen)` : w === 'trainer' ? 'Trainer (learning)' : 'Challenger (shadow)'}
            </button>
          ))}
        </div>
        <span className="muted small">{snapshot.updates.toLocaleString()} matured labels trained</span>
      </div>
      <p className="small secondary" style={{ marginTop: 0 }}>
        Online logistic model: each normalized input × its weight adds to the logit; the sigmoid turns it into P(positive 48h return after costs).
        Drag an input to see how the {which} would respond. Example: {model.example ? `${model.example.symbol} ${model.example.strategy_id} ${model.example.side}` : 'none yet'}.
      </p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Feature</th><th>Normalized input</th><th className="num">Weight</th><th>Contribution</th><th>Trainer weight history</th></tr></thead>
          <tbody>
            {model.feature_names.map((name, k) => (
              <tr key={name}>
                <td>{FEATURE_LABEL[name] ?? name}</td>
                <td><input type="range" min={-5} max={5} step={0.1} value={inputs[k]} aria-label={`${name} input`}
                  onChange={e => setInputs(v => v.map((x, j) => j === k ? Number(e.target.value) : x))} style={{ width: 110 }} /> <span className="num small">{(Math.abs(inputs[k]) < 0.05 ? 0 : inputs[k]).toFixed(1)}</span></td>
                <td className="num">{snapshot.weights[k].toFixed(3)}</td>
                <td style={{ minWidth: 120 }}>
                  <div style={{ position: 'relative', height: 10, background: 'var(--surface-3)', borderRadius: 3 }}>
                    <div style={{ position: 'absolute', top: 0, bottom: 0, left: contributions[k] >= 0 ? '50%' : `${50 - Math.abs(contributions[k]) / maxAbs * 50}%`, width: `${Math.abs(contributions[k]) / maxAbs * 50}%`, background: contributions[k] >= 0 ? 'var(--div-pos)' : 'var(--div-neg)', borderRadius: 3 }} />
                  </div>
                </td>
                <td><Spark values={history.current.map(w => w[k])} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flow" style={{ gridTemplateColumns: 'repeat(3, minmax(0,1fr))', marginTop: 8 }}>
        <div className="node">Σ contributions + bias {snapshot.bias.toFixed(3)}</div>
        <div className="node num">logit {logit.toFixed(3)}</div>
        <div className="node on num">P = {(probability * 100).toFixed(1)}% {probability >= model.decision_threshold ? `≥ ${model.decision_threshold}` : `< ${model.decision_threshold}`}</div>
      </div>
      <p className="muted small">The probability is uncalibrated and says nothing about win size. Trades additionally need the empirical edge below; a promoted champion must also agree (P ≥ {model.decision_threshold}).</p>
    </div>
  )
}

export function EdgeHeatmap({ rows }: { rows: EdgeRow[] }) {
  const classes = [...new Set(rows.map(r => r.asset_class))]
  const columns = classes.flatMap(a => (['long', 'short'] as const).map(side => ({ a, side })))
  const span = 60
  return (
    <div>
      <div className="heat" style={{ gridTemplateColumns: `minmax(120px, 1.4fr) repeat(${columns.length}, minmax(70px, 1fr))` }} role="table" aria-label="Empirical edge by strategy and side">
        <div className="head" />
        {columns.map(c => <div key={`${c.a}${c.side}`} className="head" role="columnheader">{c.a} · {c.side}</div>)}
        {STRATEGIES.map(s => (
          <Fragment key={s}>
            <div className="small" style={{ alignSelf: 'center' }} role="rowheader"><span className="swatch" style={{ background: `var(${STRATEGY_VAR[s]})` }} />{STRATEGY_LABEL[s]}</div>
            {columns.map(c => {
              const r = rows.find(x => x.strategy_id === s && x.side === c.side && x.asset_class === c.a)
              const enough = r && r.samples >= r.minimum_samples && r.lower_net_bps != null
              return (
                <div key={`${s}${c.a}${c.side}`} className="cell" role="cell"
                  title={r ? `${r.bucket}\n${r.samples}/${r.minimum_samples} samples · mean net ${fmt.bps(r.mean_net_bps)} · lower bound ${fmt.bps(r.lower_net_bps)}\n${r.reason ?? ''}` : ''}
                  style={{ background: enough ? divergingColor(r!.lower_net_bps!, span) : 'var(--surface-3)', outline: r?.supported ? '2px solid var(--good)' : undefined }}>
                  <span className="num" style={{ fontWeight: 600 }}>{r?.supported ? '✔ ' : ''}{enough ? fmt.bps(r!.lower_net_bps) : `${r?.samples ?? 0}/${r?.minimum_samples ?? 30}`}</span>
                  <span className="small secondary">{enough ? `n=${r!.samples}` : 'collecting'}</span>
                </div>
              )
            })}
          </Fragment>
        ))}
      </div>
      <div className="row small secondary" style={{ marginTop: 8 }}>
        <span>Adjusted lower bound of mean net return:</span>
        <span className="swatch" style={{ background: divergingColor(-span, span) }} />−{span}+ bps
        <span className="swatch" style={{ background: 'var(--div-mid)' }} />0
        <span className="swatch" style={{ background: divergingColor(span, span) }} />+{span}+ bps
        <span>· ✔ green outline = supported (tradable evidence)</span>
      </div>
    </div>
  )
}

export function Gates({ model, runtime, onChanged }: { model: ModelView; runtime: RuntimeName; onChanged: () => void }) {
  const [reviewed, setReviewed] = useState(false)
  const [ackDrift, setAckDrift] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const gate = model.promotion_gate
  const act = async (path: 'stage' | 'promote') => {
    try {
      await post(`/api/agent/${runtime}/${path}`, { reviewed, acknowledge_drift: ackDrift })
      setMessage(path === 'stage' ? 'Frozen challenger staged; a new prospective shadow epoch began.' : 'Challenger promoted to paper champion (live orders remain impossible).')
      onChanged()
    } catch (e) { setMessage((e as Error).message) }
  }
  const checks: [string, boolean][] = [
    ['Frozen challenger staged', !!model.challenger],
    ['No drift flag', !model.drift_flag],
    [`≥ 60 non-overlapping prospective samples (${gate.samples})`, gate.samples >= 60],
    [`≥ 60 days of shadow observations (${gate.shadow_days.toFixed(1)})`, gate.shadow_days >= 60],
    [`≥ 30 challenger trades (${gate.challenger_trades})`, gate.challenger_trades >= 30],
    [`Positive mean net return (${(gate.mean_net_return * 1e4).toFixed(1)} bps)`, gate.mean_net_return > 0],
    [`Improvement ≥ 5 bps over champion (${(gate.mean_improvement * 1e4).toFixed(1)} bps)`, gate.mean_improvement >= 0.0005],
    [`95% lower bound of improvement > 0 (${gate.samples > 1 ? `${(gate.approximate_improvement_lower_bound * 1e4).toFixed(1)} bps` : 'needs 2+ samples'})`, gate.approximate_improvement_lower_bound > 0],
  ]
  return (
    <div>
      <ul className="gate">{checks.map(([label, ok]) => <li key={label}><span className={`status ${ok ? 'good' : 'critical'}`}>{ok ? '✔' : '✖'}</span> {label}</li>)}</ul>
      {model.drift_flag && <div className="banner">Drift flag: {model.drift_reasons.join('; ')}. Promotion is paused until you review and acknowledge it when staging a new shadow epoch.</div>}
      <div className="row" style={{ marginTop: 8 }}>
        {model.drift_flag && <label className="small row"><input type="checkbox" checked={ackDrift} onChange={e => setAckDrift(e.target.checked)} /> I reviewed the drift</label>}
        <button onClick={() => act('stage')} disabled={model.trainer.updates < 100}>Stage challenger</button>
        <label className="small row"><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} /> I reviewed the prospective report</label>
        <button className="primary" onClick={() => act('promote')} disabled={!gate.eligible || !reviewed}>Promote to paper champion</button>
      </div>
      {message && <p className="small secondary">{message}</p>}
    </div>
  )
}

export function OutcomeStrip({ model }: { model: ModelView }) {
  const outcomes = model.recent_outcomes
  const rate = useMemo(() => outcomes.length ? outcomes.filter(o => o.label === 1).length / outcomes.length : null, [outcomes])
  return (
    <div>
      <div className="row small secondary" style={{ marginBottom: 4 }}>
        Last {outcomes.length} matured 48h labels · {rate == null ? '–' : `${(rate * 100).toFixed(0)}% positive after costs`} · ◆ = counted as edge sample
      </div>
      <div style={{ display: 'flex', gap: 2, flexWrap: 'wrap' }} aria-label="Recent outcomes">
        {outcomes.map((o, k) => (
          <span key={k} title={`${o.symbol} ${o.strategy_id} ${o.side}: ${(o.net_return * 100).toFixed(2)}% net`}
            style={{ width: 10, height: 18, borderRadius: 3, background: o.label ? 'var(--div-pos)' : 'var(--div-neg)', opacity: o.edge_sample ? 1 : 0.45, display: 'inline-grid', placeItems: 'center', fontSize: 8, color: '#fff' }}>
            {o.edge_sample ? '◆' : ''}
          </span>
        ))}
      </div>
    </div>
  )
}

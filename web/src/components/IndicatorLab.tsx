import { useEffect, useMemo, useState } from 'react'
import { STRATEGY_LABEL, STRATEGY_VAR, type Bars, type StrategyId } from '../api'

const WINDOW: Record<StrategyId, number> = { sma: 30, rsi: 14, bollinger: 20, atr_breakout: 20, heikin: 2 }
const STATE_NAME: Record<number, string> = { 1: 'Long', 0: 'Flat', [-1]: 'Short' }

function n(v: number | null | undefined, d = 2) { return v == null || !Number.isFinite(v) ? '–' : v.toFixed(d) }

/** Human-readable reason for the target at bar i, recomputed from the same inputs the agent used. */
function explain(b: Bars, s: StrategyId, i: number): { rule: string; values: string } {
  const prev = i > 0 ? b.targets[s][i - 1] : 0, cur = b.targets[s][i]
  const changed = cur !== prev
  const c = b.close[i]
  switch (s) {
    case 'sma': {
      const f = b.sma10[i], sl = b.sma30[i]
      return { rule: f == null || sl == null ? 'Warm-up: need 30 completed bars.' : `SMA10 ${f > sl ? '>' : '<'} SMA30 → target ${STATE_NAME[cur]}${changed ? ' (crossover: fresh signal)' : ''}`, values: `SMA10 ${n(f)} · SMA30 ${n(sl)}` }
    }
    case 'rsi': {
      const r = b.rsi[i]
      const rule = r == null ? 'Warm-up: need 15 bars.' : changed
        ? (cur === 1 ? `RSI ${n(r, 1)} < 30 → enter long (oversold reversal)` : cur === -1 ? `RSI ${n(r, 1)} > 70 → enter short (overbought reversal)` : `RSI ${n(r, 1)} crossed 50 → exit to flat`)
        : `Hold ${STATE_NAME[cur]}: ${cur === 0 ? 'waiting for RSI < 30 or > 70' : 'waiting for RSI to cross 50'}`
      return { rule, values: `RSI ${n(r, 1)} · thresholds 30 / 50 / 70` }
    }
    case 'bollinger': {
      const lo = b.bb_lower[i], hi = b.bb_upper[i], mid = b.bb_mid[i]
      const rule = lo == null ? 'Warm-up: need 20 bars.' : changed
        ? (cur === 1 ? `Close ${n(c)} < lower band ${n(lo)} → enter long` : cur === -1 ? `Close ${n(c)} > upper band ${n(hi)} → enter short` : `Close returned to mean ${n(mid)} → exit`)
        : `Hold ${STATE_NAME[cur]}: ${cur === 0 ? 'waiting for a close outside the 2σ bands' : 'waiting for a return to the mean'}`
      return { rule, values: `Lower ${n(lo)} · mean ${n(mid)} · upper ${n(hi)} · close ${n(c)}` }
    }
    case 'atr_breakout': {
      const hi = b.range_high[i], lo = b.range_low[i], a = b.atr[i]
      const rule = hi == null || a == null ? 'Warm-up: need 21 bars.' : changed
        ? (cur === 1 ? `Close ${n(c)} > prior high ${n(hi)} + 0.1·ATR → breakout long` : cur === -1 ? `Close ${n(c)} < prior low ${n(lo)} − 0.1·ATR → breakout short` : 'Close crossed back through the range midpoint → exit')
        : `Hold ${STATE_NAME[cur]}`
      return { rule, values: `Prior 20-bar high ${n(hi)} · low ${n(lo)} · ATR14 ${n(a)}` }
    }
    case 'heikin': {
      const body = b.ha_close[i] - b.ha_open[i], before = i > 0 ? b.ha_close[i - 1] - b.ha_open[i - 1] : 0
      return { rule: `Two ${cur === 1 ? 'bullish' : cur === -1 ? 'bearish' : 'mixed'} Heikin-Ashi bodies → target ${STATE_NAME[cur]}${changed ? ' (fresh)' : ''}`, values: `HA body now ${n(body)} · previous ${n(before)}` }
    }
  }
}

export default function IndicatorLab({ bars, strategy }: { bars: Bars; strategy: StrategyId }) {
  const total = bars.time.length
  const [i, setI] = useState(total - 1)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(6)
  useEffect(() => { setI(total - 1) }, [total, strategy])
  useEffect(() => {
    if (!playing) return
    const id = window.setInterval(() => setI(v => Math.min(total - 1, v + 1)), 1000 / speed)
    return () => window.clearInterval(id)
  }, [playing, speed, total])
  useEffect(() => { if (playing && i >= total - 1) setPlaying(false) }, [playing, i, total])
  const view = 80
  const start = Math.max(0, Math.min(i - view + 10, total - view))
  const slice = useMemo(() => Array.from({ length: Math.min(view, total) }, (_, k) => start + k), [start, total])
  const prices = slice.flatMap(k => [bars.high[k], bars.low[k], bars.bb_upper[k] ?? bars.high[k], bars.bb_lower[k] ?? bars.low[k]])
  const lo = Math.min(...prices), hi = Math.max(...prices)
  const W = 640, H = 200
  const x = (k: number) => ((k - start) / Math.max(1, view - 1)) * (W - 20) + 10
  const y = (v: number) => H - 14 - ((v - lo) / Math.max(1e-9, hi - lo)) * (H - 28)
  const path = (vals: (number | null)[], scale = y) => {
    let d = '', pen = false
    for (const k of slice) {
      const v = vals[k]
      if (v == null || !Number.isFinite(v)) { pen = false; continue }
      d += `${pen ? 'L' : 'M'}${x(k).toFixed(1)},${scale(v).toFixed(1)} `
      pen = true
    }
    return d
  }
  const color = `var(${STRATEGY_VAR[strategy]})`
  const target = bars.targets[strategy][i]
  const { rule, values } = explain(bars, strategy, i)
  const win = WINDOW[strategy]
  return (
    <div>
      <p className="small secondary" style={{ marginTop: 0 }}>Step through <b>{STRATEGY_LABEL[strategy]}</b> exactly as the quant developer computes it: only completed bars up to the cursor are visible to the rule (no look-ahead).</p>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`${STRATEGY_LABEL[strategy]} computation at bar ${i}`} style={{ background: 'var(--surface-2)', borderRadius: 8 }}>
        <rect x={x(Math.max(start, i - win + 1))} y={4} width={Math.max(2, x(i) - x(Math.max(start, i - win + 1)))} height={H - 8} fill={color} opacity={0.12} />
        {slice.map(k => (
          <line key={k} x1={x(k)} x2={x(k)} y1={y(bars.high[k])} y2={y(bars.low[k])} stroke={bars.close[k] >= bars.open[k] ? 'var(--div-pos)' : 'var(--div-neg)'} strokeWidth={k <= i ? 2 : 1} opacity={k <= i ? 0.9 : 0.25} />
        ))}
        {strategy === 'sma' && <><path d={path(bars.sma10)} stroke={color} fill="none" strokeWidth={2} /><path d={path(bars.sma30)} stroke="var(--text-muted)" fill="none" strokeWidth={2} /></>}
        {strategy === 'bollinger' && <><path d={path(bars.bb_upper)} stroke={color} fill="none" strokeWidth={2} /><path d={path(bars.bb_lower)} stroke={color} fill="none" strokeWidth={2} /><path d={path(bars.bb_mid)} stroke="var(--text-muted)" fill="none" strokeWidth={1} /></>}
        {strategy === 'atr_breakout' && <><path d={path(bars.range_high)} stroke={color} fill="none" strokeWidth={2} /><path d={path(bars.range_low)} stroke={color} fill="none" strokeWidth={2} /></>}
        {strategy === 'heikin' && <path d={path(bars.ha_close)} stroke={color} fill="none" strokeWidth={2} />}
        <line x1={x(i)} x2={x(i)} y1={2} y2={H - 2} stroke="var(--text-primary)" strokeWidth={1} />
        {slice.filter(k => k > 0 && k <= i && bars.targets[strategy][k] !== 0 && bars.targets[strategy][k] !== bars.targets[strategy][k - 1]).map(k => (
          <text key={`m${k}`} x={x(k)} y={bars.targets[strategy][k] > 0 ? H - 2 : 12} textAnchor="middle" fontSize={11} fill={bars.targets[strategy][k] > 0 ? 'var(--div-pos)' : 'var(--div-neg)'}>{bars.targets[strategy][k] > 0 ? '▲' : '▼'}</text>
        ))}
      </svg>
      {strategy === 'rsi' && (
        <svg viewBox={`0 0 ${W} 60`} width="100%" aria-hidden="true" style={{ background: 'var(--surface-2)', borderRadius: 8, marginTop: 4 }}>
          {[30, 50, 70].map(l => <line key={l} x1={0} x2={W} y1={60 - l * 0.6} y2={60 - l * 0.6} stroke="var(--grid)" />)}
          <path d={path(bars.rsi, v => 60 - v * 0.6)} stroke={color} fill="none" strokeWidth={2} />
          <line x1={x(i)} x2={x(i)} y1={0} y2={60} stroke="var(--text-primary)" />
        </svg>
      )}
      <div className="flow" style={{ gridTemplateColumns: 'repeat(3, minmax(0,1fr))' }} aria-label="State machine">
        {[-1, 0, 1].map(s => <div key={s} className={`node ${target === s ? 'on' : ''}`}>{s === 1 ? '▲ ' : s === -1 ? '▼ ' : ''}{STATE_NAME[s]}</div>)}
      </div>
      <p style={{ margin: '6px 0 2px' }}><b>Rule:</b> {rule}</p>
      <p className="small secondary num" style={{ margin: 0 }}>{values} · bar {i + 1}/{total} · {new Date(bars.time[i] * 1000).toISOString().slice(0, 16).replace('T', ' ')} UTC close</p>
      <div className="row" style={{ marginTop: 8 }}>
        <button onClick={() => setI(v => Math.max(0, v - 1))} aria-label="Previous bar">◀</button>
        <button className="primary" onClick={() => { if (i >= total - 1) setI(Math.max(0, total - view)); setPlaying(p => !p) }}>{playing ? 'Pause' : 'Play'}</button>
        <button onClick={() => setI(v => Math.min(total - 1, v + 1))} aria-label="Next bar">▶</button>
        <label className="small row" style={{ gap: 6 }}>Speed <input type="range" min={1} max={30} value={speed} onChange={e => setSpeed(Number(e.target.value))} style={{ width: 120 }} /> {speed} bars/s</label>
        <input type="range" min={0} max={total - 1} value={i} onChange={e => { setPlaying(false); setI(Number(e.target.value)) }} aria-label="Scrub bars" style={{ flex: 1, minWidth: 140 }} />
      </div>
    </div>
  )
}

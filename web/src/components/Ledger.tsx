import { useEffect } from 'react'
import { BaselineSeries } from 'lightweight-charts'
import { cssVar, fmt, STRATEGY_LABEL, type LedgerSummary, type StrategyId, type Trade } from '../api'
import { isoTs, removeSeries, useChart } from './charts'

export interface LedgerView { summary: LedgerSummary; trades: Trade[]; curve: [string, number][]; events: Record<string, unknown>[] }

function Meter({ label, used, limit, unit = '€' }: { label: string; used: number; limit: number; unit?: string }) {
  const share = limit > 0 ? Math.min(1, Math.max(0, used / limit)) : 0
  const level = share >= 0.9 ? 'bad' : share >= 0.6 ? 'warn' : ''
  const text = unit === '%' ? `${used.toFixed(2)}% of ${limit.toFixed(2)}%` : `${fmt.eur(used)} of ${fmt.eur(limit)}`
  return (
    <div className="meter">
      <div className="row small" style={{ justifyContent: 'space-between' }}><span className="secondary">{label}</span><span className="num">{text}</span></div>
      <div className="track" role="meter" aria-valuemin={0} aria-valuemax={limit} aria-valuenow={used} aria-label={label}>
        <div className={`fill ${level}`} style={{ width: `${share * 100}%` }} />
      </div>
    </div>
  )
}

function EquityChart({ curve, initial, theme }: { curve: [string, number][]; initial: number; theme: string }) {
  const { container, chart } = useChart(theme, { rightPriceScale: { scaleMargins: { top: 0.1, bottom: 0.1 } } })
  useEffect(() => {
    if (!chart) return
    const series = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: initial }, lineWidth: 2, priceLineVisible: false,
      topLineColor: cssVar('--div-pos'), topFillColor1: 'transparent', topFillColor2: 'transparent',
      bottomLineColor: cssVar('--div-neg'), bottomFillColor1: 'transparent', bottomFillColor2: 'transparent',
    })
    const seen = new Set<number>()
    series.setData(curve.flatMap(([t, v]) => { const time = isoTs(t); if (seen.has(time)) return []; seen.add(time); return [{ time, value: v }] }))
    chart.timeScale().fitContent()
    return () => removeSeries(chart, [series])
  }, [chart, curve, initial])
  return <div ref={container} className="chart-box small" aria-label="Paper equity curve in EUR" />
}

export default function Ledger({ view, policy, theme }: { view: LedgerView; policy: Record<string, number | boolean | string>; theme: string }) {
  const s = view.summary
  const equity = s.equity_eur
  const base = Math.min(equity, s.initial_eur)
  const dayLimit = Math.min(s.day_start_equity_eur, s.initial_eur) * (policy.daily_loss_pct as number)
  const dayLoss = Math.max(0, s.day_start_equity_eur - equity)
  const strategies = Object.entries(s.stats.by_strategy)
  return (
    <div className="stack">
      {s.halted && <div className="banner" style={{ borderLeftColor: 'var(--critical)' }}>⛔ Drawdown halt: {s.halt_reason}. Positions liquidate at the next open; new orders are blocked until a human acknowledges the halt in Controls.</div>}
      <div className="row" style={{ gap: 18 }}>
        <div><div className="muted small">Paper equity</div><div style={{ fontSize: 24, fontWeight: 650 }}>{fmt.eur(equity)}</div></div>
        <div><div className="muted small">Return</div><div className="num" style={{ fontSize: 18, color: s.return_pct >= 0 ? 'var(--good-text)' : 'var(--critical)' }}>{s.return_pct >= 0 ? '▲' : '▼'} {fmt.pct(s.return_pct)}</div></div>
        <div><div className="muted small">Closed trades</div><div className="num" style={{ fontSize: 18 }}>{s.stats.closed}</div></div>
        <div><div className="muted small">Win rate</div><div className="num" style={{ fontSize: 18 }}>{fmt.pct(s.win_rate_pct, 0)}</div></div>
        <div><div className="muted small">Fees + carry</div><div className="num" style={{ fontSize: 18 }}>{fmt.eur(s.stats.fees_eur + s.stats.carry_eur)}</div></div>
      </div>
      <div>
        <div className="legend"><span><span className="swatch" style={{ background: 'var(--div-pos)' }} />Above €{s.initial_eur}</span><span><span className="swatch" style={{ background: 'var(--div-neg)' }} />Below €{s.initial_eur}</span></div>
        {view.curve.length > 1 ? <EquityChart curve={view.curve} initial={s.initial_eur} theme={theme} /> : <p className="muted">Equity curve appears after the first bars.</p>}
      </div>
      <div className="stack" style={{ gap: 8 }}>
        <Meter label="Gross exposure" used={s.gross_exposure_eur} limit={equity * (policy.max_gross_exposure_pct as number)} />
        <Meter label="Daily loss used" used={dayLoss} limit={dayLimit} />
        <Meter label="Drawdown from peak" used={s.drawdown_pct} limit={(policy.max_drawdown_pct as number) * 100} unit="%" />
        <div className="small secondary">Per-trade risk budget {fmt.eur(base * (policy.risk_per_trade_pct as number))} (stop + round-trip costs) · max position {fmt.eur(base * (policy.max_position_pct as number))} · max {String(policy.max_concurrent_positions)} positions · no leverage</div>
      </div>
      <div>
        <h3>Open positions & pending orders</h3>
        {(s.positions?.length || s.pending?.length) ? (
          <div className="table-wrap"><table>
            <thead><tr><th>Symbol</th><th>Strategy</th><th>Side</th><th>Notional</th><th>Entry</th><th>Stop</th><th>Unrealized</th><th>Since</th></tr></thead>
            <tbody>
              {s.positions?.map(p => <tr key={`${p.symbol}${p.entered_at}`}><td>{fmt.short(p.symbol)}</td><td>{STRATEGY_LABEL[p.strategy_id]}</td><td>{p.side}</td><td>{fmt.eur(p.notional_eur)}</td><td>{p.entry_price_eur.toFixed(2)}</td><td>{p.stop_price_eur.toFixed(2)}</td><td style={{ color: (p.unrealized_eur ?? 0) >= 0 ? 'var(--good-text)' : 'var(--critical)' }}>{fmt.eur(p.unrealized_eur)}</td><td>{fmt.time(p.entered_at)}</td></tr>)}
              {s.pending?.map(o => <tr key={`${o.symbol}${o.created_at}`}><td>{fmt.short(o.symbol)}</td><td>{STRATEGY_LABEL[o.strategy_id as StrategyId]}</td><td>{o.side}</td><td>{fmt.eur(o.notional_eur)}</td><td colSpan={3} className="muted">pending: fills at next bar open</td><td>{fmt.time(o.created_at)}</td></tr>)}
            </tbody>
          </table></div>
        ) : <p className="muted small">No open paper positions. The agents only trade when evidence supports an after-cost edge.</p>}
      </div>
      {strategies.length > 0 && (
        <div>
          <h3>Results by strategy</h3>
          <div className="table-wrap"><table>
            <thead><tr><th>Strategy</th><th>Trades</th><th>Wins</th><th>P&L</th></tr></thead>
            <tbody>{strategies.map(([id, v]) => <tr key={id}><td>{STRATEGY_LABEL[id as StrategyId] ?? id}</td><td>{v.trades}</td><td>{v.wins}</td><td style={{ color: v.pnl_eur >= 0 ? 'var(--good-text)' : 'var(--critical)' }}>{fmt.eur(v.pnl_eur)}</td></tr>)}</tbody>
          </table></div>
        </div>
      )}
      <div>
        <h3>Closed paper trades</h3>
        {view.trades.length ? (
          <div className="table-wrap" style={{ maxHeight: 260, overflow: 'auto' }}><table>
            <thead><tr><th>Exit</th><th>Symbol</th><th>Strategy</th><th>Side</th><th>Held</th><th>P&L</th><th>Return</th><th>Reason</th></tr></thead>
            <tbody>{view.trades.slice().reverse().map((t, k) => (
              <tr key={k}><td>{fmt.time(t.exited_at)}</td><td>{fmt.short(t.symbol)}</td><td>{STRATEGY_LABEL[t.strategy_id]}</td><td>{t.side}</td><td>{t.held_hours.toFixed(1)}h</td>
                <td style={{ color: t.pnl_eur >= 0 ? 'var(--good-text)' : 'var(--critical)' }}>{fmt.eur(t.pnl_eur)}</td><td>{fmt.pct(t.return_pct)}</td><td className="secondary">{t.reason.replaceAll('_', ' ')}</td></tr>
            ))}</tbody>
          </table></div>
        ) : <p className="muted small">No closed trades yet.</p>}
      </div>
    </div>
  )
}

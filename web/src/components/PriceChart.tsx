import { useEffect } from 'react'
import { CandlestickSeries, LineSeries, createSeriesMarkers, type SeriesMarker, type UTCTimestamp } from 'lightweight-charts'
import { cssVar, fmt, STRATEGY_LABEL, STRATEGY_VAR, type Bars, type StrategyId } from '../api'
import { isoTs, removeSeries, ts, useChart } from './charts'

function line(values: (number | null)[], time: number[]) {
  return values.flatMap((v, i) => (v == null ? [] : [{ time: ts(time[i]), value: v }]))
}

export default function PriceChart({ bars, strategy, theme }: { bars: Bars; strategy: StrategyId; theme: string }) {
  const { container, chart } = useChart(theme)
  useEffect(() => {
    if (!chart) return
    const up = cssVar('--div-pos'), down = cssVar('--div-neg'), muted = cssVar('--text-muted'), accent = cssVar(STRATEGY_VAR[strategy])
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: up, downColor: down, borderVisible: false, wickUpColor: up, wickDownColor: down, priceLineVisible: false,
    })
    candles.setData(bars.time.map((t, i) => ({ time: ts(t), open: bars.open[i], high: bars.high[i], low: bars.low[i], close: bars.close[i] })))
    const overlays: [string, (number | null)[], string, number][] = []
    if (strategy === 'sma') overlays.push(['SMA 10', bars.sma10, accent, 2], ['SMA 30', bars.sma30, muted, 2])
    if (strategy === 'bollinger') overlays.push(['Mean 20', bars.bb_mid, muted, 1], ['Upper band', bars.bb_upper, accent, 2], ['Lower band', bars.bb_lower, accent, 2])
    if (strategy === 'atr_breakout') overlays.push(['Prior 20-bar high', bars.range_high, accent, 2], ['Prior 20-bar low', bars.range_low, accent, 2])
    if (strategy === 'heikin') overlays.push(['Heikin-Ashi close', bars.ha_close, accent, 2])
    for (const [title, values, color, width] of overlays) {
      chart.addSeries(LineSeries, { color, lineWidth: width as 1 | 2, title, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
        .setData(line(values, bars.time))
    }
    if (strategy === 'rsi') {
      const rsi = chart.addSeries(LineSeries, { color: accent, lineWidth: 2, title: 'RSI 14', priceLineVisible: false }, 1)
      rsi.setData(line(bars.rsi, bars.time))
      for (const level of [30, 50, 70]) rsi.createPriceLine({ price: level, color: muted, lineWidth: 1, lineStyle: 0, axisLabelVisible: true, title: '' })
      chart.panes()[1]?.setHeight(90)
    }
    const markers: SeriesMarker<UTCTimestamp>[] = []
    const targets = bars.targets[strategy]
    for (let i = 1; i < targets.length; i++) {
      if (targets[i] !== 0 && targets[i] !== targets[i - 1]) {
        markers.push({ time: ts(bars.time[i]), position: targets[i] > 0 ? 'belowBar' : 'aboveBar', shape: targets[i] > 0 ? 'arrowUp' : 'arrowDown', color: targets[i] > 0 ? up : down, size: 1 })
      }
    }
    const first = bars.time[0]
    for (const t of bars.trades) {
      if (t.strategy_id !== strategy) continue
      const entry = isoTs(t.entered_at), exit = isoTs(t.exited_at)
      if (entry >= first) markers.push({ time: entry, position: 'inBar', shape: 'circle', color: cssVar('--good'), text: `paper ${t.side}`, size: 1 })
      if (exit >= first) markers.push({ time: exit, position: 'inBar', shape: 'square', color: t.pnl_eur >= 0 ? cssVar('--good') : cssVar('--critical'), text: fmt.eur(t.pnl_eur), size: 1 })
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number))
    createSeriesMarkers(candles, markers)
    chart.timeScale().fitContent()
    return () => removeSeries(chart, chart.panes().flatMap(p => p.getSeries()))
  }, [chart, bars, strategy])
  return (
    <div>
      <div className="legend">
        <span><span className="swatch" style={{ background: 'var(--div-pos)' }} />Up candle / long signal ▲</span>
        <span><span className="swatch" style={{ background: 'var(--div-neg)' }} />Down candle / short signal ▼</span>
        <span><span className="swatch" style={{ background: `var(${STRATEGY_VAR[strategy]})` }} />{STRATEGY_LABEL[strategy]} inputs</span>
        <span><span className="swatch" style={{ background: 'var(--good)' }} />● paper entry ■ exit</span>
      </div>
      <div ref={container} className="chart-box" style={{ height: strategy === 'rsi' ? 400 : 320 }} />
      <p className="muted small" style={{ margin: '6px 0 0' }}>Last {bars.time.length} completed 15-minute bars the agent holds in memory. Arrows mark where the selected strategy's target changed (a fresh signal the four agents evaluate).</p>
    </div>
  )
}

import { useEffect, useRef, useState } from 'react'
import { createChart, type DeepPartial, type ChartOptions, type IChartApi, type ISeriesApi, type SeriesType, type UTCTimestamp } from 'lightweight-charts'
import { cssVar } from '../api'

export const ts = (seconds: number) => seconds as UTCTimestamp
export const isoTs = (iso: string) => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp

export function baseOptions(): DeepPartial<ChartOptions> {
  return {
    autoSize: true,
    layout: { background: { color: cssVar('--surface-1') }, textColor: cssVar('--text-muted'), fontFamily: 'system-ui, -apple-system, Segoe UI, sans-serif', attributionLogo: true },
    grid: { vertLines: { color: cssVar('--grid') }, horzLines: { color: cssVar('--grid') } },
    rightPriceScale: { borderColor: cssVar('--axis') },
    timeScale: { borderColor: cssVar('--axis'), timeVisible: true, secondsVisible: false },
    crosshair: { mode: 0 },
  }
}

const disposed = new WeakSet<IChartApi>()

/** Series cleanup that is safe after the chart itself was removed (hook cleanups run in declaration order). */
export function removeSeries(chart: IChartApi, series: ISeriesApi<SeriesType>[]) {
  if (disposed.has(chart)) return
  for (const s of series) chart.removeSeries(s)
}

/** Creates a lightweight-chart in a div and recreates it when the theme changes. */
export function useChart(theme: string, options: DeepPartial<ChartOptions> = {}) {
  const container = useRef<HTMLDivElement>(null)
  const [chart, setChart] = useState<IChartApi | null>(null)
  const optionsKey = JSON.stringify(options)
  useEffect(() => {
    if (!container.current) return
    const instance = createChart(container.current, { ...baseOptions(), ...JSON.parse(optionsKey) })
    setChart(instance)
    return () => { disposed.add(instance); instance.remove(); setChart(null) }
  }, [theme, optionsKey])
  return { container, chart }
}

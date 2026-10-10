import { useEffect, useRef, useState } from 'react'

export type RuntimeName = 'live' | 'replay'
export const STRATEGIES = ['sma', 'rsi', 'bollinger', 'atr_breakout', 'heikin'] as const
export type StrategyId = (typeof STRATEGIES)[number]
export const STRATEGY_LABEL: Record<StrategyId, string> = {
  sma: 'SMA 10/30 trend', rsi: 'RSI 14 reversal', bollinger: 'Bollinger 20/2 reversal',
  atr_breakout: 'ATR range breakout', heikin: 'Heikin-Ashi confirm',
}
export const STRATEGY_VAR: Record<StrategyId, string> = {
  sma: '--s1', rsi: '--s2', bollinger: '--s3', atr_breakout: '--s4', heikin: '--s5',
}
export const ROLE_TITLE: Record<string, string> = {
  developer: 'Quant developer', researcher: 'Quant researcher', 'senior skeptic': 'Senior skeptic', trader: 'Quant trader',
}

export interface RoleDecision {
  approved?: boolean
  eligible?: boolean
  reasons?: string[]
  evidence?: unknown
  probability?: number
  edge_estimate?: EdgeRow
  audit?: Record<string, unknown>
  [key: string]: unknown
}
export interface Decision {
  decision_id: string
  kind?: string
  evaluated_at: string
  outcome: string
  live_orders_enabled: boolean
  proposal: { symbol: string; strategy_id: StrategyId; side: 'long' | 'short'; confidence: number; stop_distance_pct: number; observed_at: string; entry_reference_price: number; fresh_signal?: boolean }
  roles: { role: string; decision: RoleDecision }[]
  order: { notional_eur: number; quantity: number; side: string } | null
  settings?: { before: unknown; after: unknown; at: string }
}
export interface EdgeRow {
  bucket: string; strategy_id: StrategyId; side: 'long' | 'short'; asset_class: string; samples: number; minimum_samples: number
  mean_gross_bps: number | null; mean_net_bps: number | null; lower_net_bps: number | null; sd_bps: number | null
  roundtrip_cost_bps: number; supported: boolean; reason?: string; outcomes_seen?: number; tested_buckets: number; z: number
}
export interface Gate {
  eligible: boolean; reasons: string[]; samples: number; shadow_days: number; challenger_trades: number
  mean_net_return: number; mean_improvement: number; approximate_improvement_lower_bound: number
}
export interface LedgerSummary {
  initial_eur: number; equity_eur: number; cash_eur: number; peak_eur: number; return_pct: number; drawdown_pct: number
  day_start_equity_eur: number; gross_exposure_eur: number; halted: boolean; halt_reason: string | null; clock: string | null
  stats: { closed: number; wins: number; pnl_eur: number; fees_eur: number; carry_eur: number; by_strategy: Record<string, { trades: number; wins: number; pnl_eur: number }> }
  win_rate_pct: number | null
  positions?: Position[]; pending?: { symbol: string; strategy_id: string; side: string; notional_eur: number; created_at: string }[]
}
export interface Position {
  symbol: string; strategy_id: StrategyId; side: 'long' | 'short'; quantity: number; entry_price_eur: number; entered_at: string
  stop_price_eur: number; notional_eur: number; mark_eur?: number; unrealized_eur?: number; estimated_stop_risk_eur: number
}
export interface Trade {
  symbol: string; strategy_id: StrategyId; side: string; entered_at: string; exited_at: string; held_hours: number
  entry_price_eur: number; exit_price_eur: number; pnl_eur: number; return_pct: number; reason: string; fees_eur: number
}
export interface Signal { symbol: string; strategy_id: StrategyId; side: 'long' | 'short'; observed_at: string; fresh_signal: boolean }
export interface Status {
  runtime: RuntimeName; execution: string; training_labels: number; pending_labels: number; champion_version: number
  drift_flag: boolean; drift_reasons: string[]; promotion_gate: Gate; policy: Record<string, number | boolean | string>
  settings: Settings; ledger: LedgerSummary; edge_supported_buckets: number; active_signals: Signal[]
  decision_count: number; clock: string | null
  symbols: string[]; outcome_counters: Record<string, [number, number, number]>
  driver: Record<string, unknown> & { running: boolean; last_error?: string | null; notes?: string[]; progress?: number; speed?: number; position?: number; total?: number; steps_per_second?: number; polls?: number; sources?: Record<string, string> }
  narrator: { llm_enabled: boolean; model: string | null }
}
export interface Settings {
  risk: Record<string, number>; shorts_enabled: boolean; minimum_edge_samples: number; edge_window: number
  conflict_policy: 'evidence' | 'strict'
}
export interface ModelSnapshot { weights: number[]; bias: number; updates: number; normalizer: { count: number; mean: number[]; scale: number[] } }
export interface ModelView {
  feature_names: string[]; decision_threshold: number; trainer: ModelSnapshot; champion: ModelSnapshot; challenger: ModelSnapshot | null
  champion_version: number; shadow_epoch: number; shadow_records: number; promotion_gate: Gate; drift_flag: boolean; drift_reasons: string[]
  audit: Record<string, unknown>[]
  example: { symbol: string; strategy_id: string; side: string; observed_at: string; transformed_features: number[] } | null
  recent_outcomes: { symbol: string; strategy_id: string; side: string; label: number; net_return: number; gross_return: number; label_at: string; fresh: boolean; edge_sample: boolean }[]
  recent_success_rate: number | null; edge: EdgeRow[]
}
export interface Bars {
  symbol: string; time: number[]; open: number[]; high: number[]; low: number[]; close: number[]; volume: number[]
  sma10: (number | null)[]; sma30: (number | null)[]; bb_mid: (number | null)[]; bb_upper: (number | null)[]; bb_lower: (number | null)[]
  rsi: (number | null)[]; atr: (number | null)[]; range_high: (number | null)[]; range_low: (number | null)[]
  ha_open: number[]; ha_close: number[]; targets: Record<StrategyId, number[]>; trades: Trade[]; positions: Position[]
  profile: Record<string, unknown>
}
export interface AgentEvent {
  seq: number; type: string; runtime: RuntimeName; published_at: string; clock?: string; accepted_bars?: number; outcomes?: number
  edge_samples?: number; training_labels?: number; equity_eur?: number; positions?: number; pending_orders?: number; halted?: boolean
  decisions?: { decision_id: string; symbol: string; strategy_id: StrategyId; side: string; outcome: string; blocked_by: string | null }[]
  active_signals?: Signal[]; action?: string; error?: string
}

const TOKEN_KEY = 'agent-token'
export function getToken(): string {
  try { return sessionStorage.getItem(TOKEN_KEY) ?? '' } catch { return '' }
}
export function setToken(token: string) {
  try { sessionStorage.setItem(TOKEN_KEY, token) } catch { /* storage unavailable */ }
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}`, ...(init.headers ?? {}) },
  })
  if (!response.ok) {
    let detail = response.statusText
    try { detail = (await response.json()).detail ?? detail } catch { /* not json */ }
    throw new ApiError(response.status, typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  const type = response.headers.get('content-type') ?? ''
  return (type.includes('application/json') ? response.json() : response.text()) as Promise<T>
}

export const post = <T>(path: string, body: unknown = {}) => api<T>(path, { method: 'POST', body: JSON.stringify(body) })
export const put = <T>(path: string, body: unknown) => api<T>(path, { method: 'PUT', body: JSON.stringify(body) })

/** Poll a JSON endpoint; `version` forces an immediate refresh when it changes. */
export function usePoll<T>(path: string | null, intervalMs: number, version = 0) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!path) return
    let alive = true
    const load = () => api<T>(path).then(d => { if (alive) { setData(d); setError(null) } })
      .catch((e: Error) => { if (alive) setError(e.message) })
    load()
    const id = window.setInterval(load, intervalMs)
    return () => { alive = false; window.clearInterval(id) }
  }, [path, intervalMs, version])
  return { data, error }
}

/** Authenticated runtime event stream with automatic reconnect. */
export function useAgentEvents(runtime: RuntimeName, onEvents: (events: AgentEvent[]) => void) {
  const [connected, setConnected] = useState(false)
  const [heartbeat, setHeartbeat] = useState<Record<string, unknown> | null>(null)
  const handler = useRef(onEvents)
  useEffect(() => { handler.current = onEvents }, [onEvents])
  useEffect(() => {
    let socket: WebSocket | null = null
    let closed = false
    let retry: number | undefined
    let after = 0
    const connect = () => {
      const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
      socket = new WebSocket(`${scheme}://${location.host}/ws/agent`)
      socket.onopen = () => { socket?.send(JSON.stringify({ token: getToken(), runtime, after })); setConnected(true) }
      socket.onmessage = message => {
        const data = JSON.parse(message.data)
        if (data.type === 'events') {
          const events = data.events as AgentEvent[]
          if (events.length) after = events[events.length - 1].seq
          handler.current(events)
        } else if (data.type === 'heartbeat') setHeartbeat(data)
        else if (data.type === 'reset') { after = 0; handler.current([{ seq: 0, type: 'reset', runtime, published_at: new Date().toISOString() }]) }
      }
      socket.onclose = () => { setConnected(false); if (!closed) retry = window.setTimeout(connect, 2000) }
    }
    connect()
    return () => { closed = true; window.clearTimeout(retry); socket?.close() }
  }, [runtime])
  return { connected, heartbeat }
}

export const fmt = {
  eur: (v: number | null | undefined) => v == null ? '–' : `€${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
  pct: (v: number | null | undefined, digits = 2) => v == null ? '–' : `${v.toFixed(digits)}%`,
  bps: (v: number | null | undefined) => v == null ? '–' : `${v >= 0 ? '+' : ''}${v.toFixed(1)} bps`,
  time: (v: string | null | undefined) => v ? new Date(v).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' UTC' : '–',
  short: (s: string) => s.replace('USDT', ''),
}

export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888'
}

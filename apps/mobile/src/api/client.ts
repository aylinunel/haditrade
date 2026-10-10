/** Server tokens are passed at runtime. Never put secrets in EXPO_PUBLIC_* variables. */
export type RuntimeName = 'live' | 'replay';

export interface RoleVerdict { role: string; decision: { approved?: boolean; eligible?: boolean; reasons?: string[] } }
export interface AgentDecision {
  decision_id: string; kind?: string; evaluated_at: string; outcome: string;
  proposal: { symbol: string; strategy_id: string; side: string; observed_at: string };
  roles: RoleVerdict[]; order: { notional_eur: number } | null;
}
export interface AgentStatus {
  runtime: RuntimeName; clock: string | null; training_labels: number; pending_labels: number; champion_version: number;
  edge_supported_buckets: number; decision_count: number;
  ledger: { equity_eur: number; return_pct: number; drawdown_pct: number; halted: boolean; stats: { closed: number; wins: number; pnl_eur: number } };
  driver: { running: boolean; last_error?: string | null };
}

export function createResearchClient(origin: string, getToken: () => string) {
  const parsed = new URL(origin);
  if (parsed.protocol !== 'https:' && !['localhost', '127.0.0.1', '10.0.2.2'].includes(parsed.hostname)) {
    throw new Error('Use HTTPS for non-local research servers');
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) throw new Error('Use a plain API origin');
  async function get<T>(path: string): Promise<T> {
    const response = await fetch(parsed.origin + path, { headers: { Authorization: `Bearer ${getToken()}` } });
    if (!response.ok) throw new Error(`Research API returned ${response.status}`);
    return response.json() as Promise<T>;
  }
  return {
    report: <T>() => get<T>('/api/report'),
    events: <T>() => get<T>('/api/events'),
    status: (runtime: RuntimeName) => get<AgentStatus>(`/api/agent/${runtime}/status`),
    decisions: (runtime: RuntimeName, limit = 30) => get<AgentDecision[]>(`/api/agent/${runtime}/decisions?limit=${limit}`),
  };
}

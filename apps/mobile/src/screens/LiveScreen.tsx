import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { createResearchClient, type AgentDecision, type AgentStatus, type RuntimeName } from '../api/client';

const ROLE: Record<string, string> = { developer: 'Developer', researcher: 'Researcher', 'senior skeptic': 'Skeptic', trader: 'Trader' };
const passed = (d: { approved?: boolean; eligible?: boolean }) => (d.approved ?? d.eligible) === true;
const eur = (n: number) => `€${n.toFixed(2)}`;

function DecisionRow({ d }: { d: AgentDecision }) {
  if (d.kind === 'settings_change') return <Text style={s.caption}>⚙ Settings changed · {d.evaluated_at.slice(0, 16).replace('T', ' ')} UTC</Text>;
  const approved = d.outcome === 'paper_only_approved';
  const blocker = d.roles.find(r => !passed(r.decision));
  return (
    <View style={s.decision}>
      <Text style={approved ? s.good : s.bad}>{approved ? '✔ Paper approved' : '✖ Rejected'}</Text>
      <Text style={s.botTitle}>{d.proposal.symbol.replace('USDT', '')} · {d.proposal.strategy_id} · {d.proposal.side}</Text>
      <View style={s.roles}>
        {d.roles.map(r => <Text key={r.role} style={[s.role, passed(r.decision) ? s.rolePass : s.roleFail]}>{passed(r.decision) ? '✔' : '✖'} {ROLE[r.role] ?? r.role}</Text>)}
      </View>
      {blocker && <Text style={s.caption}>{ROLE[blocker.role] ?? blocker.role}: {(blocker.decision.reasons ?? [])[0]}</Text>}
      {approved && d.order && <Text style={s.caption}>Paper order {eur(d.order.notional_eur)} queued for the next bar open. No real order exists.</Text>}
    </View>
  );
}

export function LiveScreen() {
  const [origin, setOrigin] = useState('http://10.0.2.2:8000');
  const [tokenInput, setTokenInput] = useState('');
  const token = useRef('');
  const [runtime, setRuntime] = useState<RuntimeName>('replay');
  const [connected, setConnected] = useState(false);
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [decisions, setDecisions] = useState<AgentDecision[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const client = createResearchClient(origin, () => token.current);
      const [nextStatus, nextDecisions] = await Promise.all([client.status(runtime), client.decisions(runtime)]);
      setStatus(nextStatus);
      setDecisions(nextDecisions.slice().reverse());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [origin, runtime]);

  useEffect(() => {
    if (!connected) return;
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, [connected, load]);

  const ledger = status?.ledger;
  return (
    <ScrollView style={s.root} contentContainerStyle={s.content} keyboardShouldPersistTaps="handled">
      <Text style={s.eyebrow}>STRATEGY CITY · LIVE AGENT</Text>
      <Text style={s.title}>Four agents, one paper portfolio.</Text>
      <View style={s.badge}><Text style={s.badgeText}>PAPER ONLY · NO BROKER · READ-ONLY VIEW</Text></View>
      <View style={s.card}>
        <Text style={s.heading}>Connect to your backend</Text>
        <TextInput style={s.input} value={origin} onChangeText={setOrigin} autoCapitalize="none" autoCorrect={false} placeholder="https://your-server" placeholderTextColor="#6b7891" accessibilityLabel="API origin" />
        <TextInput style={s.input} value={tokenInput} onChangeText={setTokenInput} secureTextEntry autoCapitalize="none" placeholder="STRATEGY_API_TOKEN" placeholderTextColor="#6b7891" accessibilityLabel="API token" />
        <Text style={s.caption}>The token is kept in memory only and sent only to this origin. Android emulator: 10.0.2.2 reaches your computer.</Text>
        <View style={s.row}>
          <Pressable style={s.button} onPress={() => { token.current = tokenInput.trim(); setConnected(true); load(); }} accessibilityRole="button"><Text style={s.buttonText}>{connected ? 'Refresh' : 'Connect'}</Text></Pressable>
          {(['replay', 'live'] as RuntimeName[]).map(r => (
            <Pressable key={r} style={[s.chip, runtime === r && s.chipOn]} onPress={() => setRuntime(r)} accessibilityRole="button" accessibilityState={{ selected: runtime === r }}>
              <Text style={s.chipText}>{r === 'replay' ? 'Replay sandbox' : 'Live paper'}</Text>
            </Pressable>
          ))}
        </View>
        {error && <Text style={s.bad}>{error}</Text>}
      </View>
      {status && ledger && (
        <View style={s.card}>
          <Text style={s.heading}>{runtime === 'replay' ? 'Replay sandbox' : 'Live paper'} · {status.driver.running ? 'running' : 'stopped'}</Text>
          <View style={s.stats}>
            <View><Text style={s.label}>Paper equity</Text><Text style={s.value}>{eur(ledger.equity_eur)}</Text></View>
            <View><Text style={s.label}>Return</Text><Text style={ledger.return_pct >= 0 ? s.gain : s.loss}>{ledger.return_pct >= 0 ? '▲' : '▼'} {ledger.return_pct.toFixed(2)}%</Text></View>
            <View><Text style={s.label}>Closed trades</Text><Text style={s.value}>{ledger.stats.closed}</Text></View>
          </View>
          <Text style={s.body}>Labels learned: {status.training_labels.toLocaleString()} · pending: {status.pending_labels.toLocaleString()} · supported edges: {status.edge_supported_buckets} · champion v{status.champion_version}</Text>
          <Text style={s.caption}>Clock {status.clock ? status.clock.slice(0, 16).replace('T', ' ') : '–'} UTC · drawdown {ledger.drawdown_pct.toFixed(2)}%{ledger.halted ? ' · HALTED' : ''}</Text>
          {status.driver.last_error ? <Text style={s.bad}>Feed: {status.driver.last_error}</Text> : null}
        </View>
      )}
      {connected && (
        <View style={s.card}>
          <Text style={s.heading}>Latest decisions</Text>
          {decisions.length ? decisions.map(d => <DecisionRow key={d.decision_id} d={d} />) : <Text style={s.body}>No decisions yet. Start the replay or live feed from the web app.</Text>}
        </View>
      )}
    </ScrollView>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#080d1a' }, content: { padding: 22, paddingTop: 26, paddingBottom: 45, gap: 18, maxWidth: 880, width: '100%', alignSelf: 'center' },
  eyebrow: { color: '#72d6be', fontSize: 10, fontWeight: '700', letterSpacing: 1.4 }, title: { color: '#f4f6fd', fontSize: 30, fontWeight: '700', lineHeight: 37 },
  badge: { alignSelf: 'flex-start', backgroundColor: '#16342d', paddingHorizontal: 12, paddingVertical: 8, borderRadius: 20 }, badgeText: { color: '#80e4c5', fontSize: 10, fontWeight: '700', letterSpacing: 0.5 },
  card: { backgroundColor: '#111a2a', padding: 19, borderRadius: 16, borderWidth: 1, borderColor: '#243147', gap: 10 }, heading: { color: '#eff3fc', fontSize: 19, fontWeight: '600' },
  body: { color: '#a5b1c6', fontSize: 13, lineHeight: 21 }, caption: { color: '#8695ad', fontSize: 12, lineHeight: 18 },
  input: { borderWidth: 1, borderColor: '#2d3b55', borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10, color: '#eff3fc', backgroundColor: '#0c1322' },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, alignItems: 'center' },
  button: { backgroundColor: '#3987e5', paddingHorizontal: 16, paddingVertical: 10, borderRadius: 10 }, buttonText: { color: '#fff', fontWeight: '600' },
  chip: { borderWidth: 1, borderColor: '#2d3b55', paddingHorizontal: 12, paddingVertical: 9, borderRadius: 10 }, chipOn: { backgroundColor: '#1d2b44', borderColor: '#3987e5' }, chipText: { color: '#dfe6f3' },
  stats: { flexDirection: 'row', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }, label: { color: '#8695ad', fontSize: 10, marginBottom: 6 },
  value: { color: '#e2e9f6', fontSize: 20, fontWeight: '600' }, gain: { color: '#0ca30c', fontSize: 20, fontWeight: '600' }, loss: { color: '#e66767', fontSize: 20, fontWeight: '600' },
  decision: { borderTopWidth: 1, borderTopColor: '#243147', paddingTop: 10, gap: 4 }, botTitle: { color: '#e5ebf6', fontSize: 14, fontWeight: '600' },
  roles: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 }, role: { fontSize: 11, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 8, borderWidth: 1, color: '#dfe6f3' },
  rolePass: { borderColor: '#0ca30c' }, roleFail: { borderColor: '#d03b3b' }, good: { color: '#0ca30c', fontWeight: '700' }, bad: { color: '#e66767', fontWeight: '700' },
});

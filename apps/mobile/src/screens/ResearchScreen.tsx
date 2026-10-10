import React from 'react';
import { ScrollView, View, Text, StyleSheet } from 'react-native';
import report from '../../assets/research-summary.json';
import profile from '../../assets/research-profile.json';
import { EquityCurve } from '../components/EquityCurve';
const pct = (n: number) => `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
const money = (n: number) => `$${n.toFixed(2)}`;
export function ResearchScreen() {
  return <ScrollView style={s.root} contentContainerStyle={s.content}>
    <Text style={s.eyebrow}>STRATEGY CITY · RESEARCH</Text>
    <Text style={s.title}>Evidence before execution.</Text>
    <Text style={s.intro}>€500 research portfolio. Long and short hypotheses, a 48-hour context and four decision roles.</Text>
    <View style={s.badge}><Text style={s.badgeText}>HISTORICAL DATA · NO LIVE ORDERS</Text></View>
    <View style={s.card}><Text style={s.heading}>Your research profile</Text>
      <Text style={s.body}>15-minute completed bars · At most 48 elapsed hours holding · Shared capital across five strategies.</Text>
      <Text style={s.body}>Candidates: {profile.candidates.join(' · ')}.</Text>
      {profile.risk.map(rule => <View key={rule.label}><Text style={s.botTitle}>{rule.label}: {rule.value}</Text><Text style={s.caption}>{rule.reason}</Text></View>)}
      <Text style={s.body}>These conservative defaults make “mid risk” measurable. They are policy thresholds; actual losses can exceed estimates.</Text>
    </View>
    <View style={s.card}><Text style={s.heading}>Four separate roles</Text>
      {profile.agents.map(agent => <View key={agent.name}><Text style={s.botTitle}>{agent.name}</Text><Text style={s.body}>{agent.task}</Text></View>)}
      <Text style={s.caption}>Separate software agents with rule checks and bounded online learning. No LLM provider is configured. All opinions and vetoes are recorded by FastAPI.</Text>
      <Text style={s.body}>{profile.learning}</Text>
      <Text style={s.body}>A new model&apos;s probability alone cannot approve a trade. Positive expected returns after costs need independent evidence.</Text>
    </View>
    <View style={s.card}><Text style={s.heading}>Market coverage</Text><Text style={s.body}>Requested: {profile.requested_markets.join(' · ')}.</Text>
      <Text style={s.body}>Current intraday simulations: {profile.supported_intraday_models.join(' · ')}. Options, futures, forex and CFDs require contract-specific execution and risk models.</Text>
      <Text style={s.body}>{profile.limitations}</Text></View>
    <Text style={s.heading}>Legacy daily backtests</Text><Text style={s.body}>The following three historical tests use the earlier long/cash strategy set and $10,000 profile. They are retained as evidence, separate from the €500 intraday hypotheses.</Text>
    <View style={s.note}><Text style={s.noteTitle}>The portfolio trails buy & hold in all three windows.</Text>
      <Text style={s.body}>Bollinger and RSI were positive across these samples. These are research candidates, not proven best strategies.</Text></View>
    {report.scenarios.map(window => <View key={window.id} style={s.card}>
      <Text style={s.eyebrow}>{window.label} · ORCL · DAILY</Text><Text style={s.heading}>{window.name}</Text>
      <View style={s.stats}><View><Text style={s.label}>Portfolio return</Text><Text style={s.gain}>{pct(window.portfolio.return_pct)}</Text></View>
        <View><Text style={s.label}>Buy & hold</Text><Text style={s.value}>{pct(window.benchmark.return_pct)}</Text></View>
        <View><Text style={s.label}>Max drawdown</Text><Text style={s.value}>{window.portfolio.max_drawdown_pct.toFixed(2)}%</Text></View></View>
      <EquityCurve points={window.curve}/><Text style={s.legend}>Mint: five-strategy portfolio · Grey: buy & hold</Text>
      <Text style={s.caption}>{window.start_actual} → {window.end_actual} · {window.bars} bars · Sharpe {window.portfolio.sharpe.toFixed(2)}</Text>
      {window.strategies.map(bot => <View key={bot.id} style={s.botRow}><View style={[s.dot,{backgroundColor:bot.color}]}/><View style={s.botName}><Text style={s.botTitle}>{bot.name}</Text><Text style={s.caption}>{bot.family} · {bot.metrics.trades} closed trades</Text></View>
        <Text style={bot.metrics.return_pct >= 0 ? s.positive : s.negative}>{pct(bot.metrics.return_pct)}</Text></View>)}
    </View>)}
    <View style={s.card}><Text style={s.heading}>Test contract</Text><Text style={s.body}>$10,000 starting capital split into five independent $2,000 sleeves. Long or cash, fractional shares, no leverage, no allocation rebalancing.</Text>
      <Text style={s.body}>{report.methodology.execution}. {report.methodology.costs}. Indicators use 260 preceding bars. Fixed parameters, no test-window tuning.</Text>
      <Text style={s.body}>Initial capital: {money(10000)}. Buy & hold uses the same transaction-cost model. Drawdown includes the initial capital baseline; Sharpe assumes zero risk-free return.</Text></View>
    <View style={s.card}><Text style={s.heading}>Connections</Text><Text style={s.body}>FastAPI supplies authenticated research endpoints and a WebSocket stream. It is a backend framework, not a market-data provider.</Text>
      <Text style={s.body}>TrendSpider webhook receiver: implemented and tested locally. Your TrendSpider account and a licensed historical-price feed are not connected.</Text>
      <Text style={s.body}>The bundled app works offline from saved reports. The backend has an untrained neutral champion and bounded learning pipeline; a feed must supply outcomes before training. Broker execution and an interactive 3D scene are deferred.</Text></View>
    <View style={s.card}><Text style={s.heading}>Source and limits</Text><Text selectable style={s.body}>{report.provenance.source_url}</Text><Text style={s.caption}>Pinned source blob: {report.provenance.source_blob_sha}</Text>
      {report.methodology.limitations.map(limit => <Text key={limit} style={s.body}>• {limit}</Text>)}</View>
    <Text style={s.footer}>React Native · Expo SDK 57 · Historical research only</Text>
  </ScrollView>;
}
const s=StyleSheet.create({
  root:{flex:1,backgroundColor:'#080d1a'},content:{padding:22,paddingTop:26,paddingBottom:45,gap:18,maxWidth:880,width:'100%',alignSelf:'center'},
  eyebrow:{color:'#72d6be',fontSize:10,fontWeight:'700',letterSpacing:1.4},title:{color:'#f4f6fd',fontSize:34,fontWeight:'700',lineHeight:41},
  intro:{color:'#9ca9bf',fontSize:15,lineHeight:23},badge:{alignSelf:'flex-start',backgroundColor:'#16342d',paddingHorizontal:12,paddingVertical:8,borderRadius:20},badgeText:{color:'#80e4c5',fontSize:10,fontWeight:'700',letterSpacing:.5},
  note:{backgroundColor:'#1a1931',padding:18,borderRadius:14,borderWidth:1,borderColor:'#383251',gap:10},noteTitle:{color:'#ddd6ff',fontSize:17,fontWeight:'600'},
  card:{backgroundColor:'#111a2a',padding:19,borderRadius:16,borderWidth:1,borderColor:'#243147',gap:10},heading:{color:'#eff3fc',fontSize:21,fontWeight:'600'},
  body:{color:'#a5b1c6',fontSize:13,lineHeight:21},stats:{flexDirection:'row',justifyContent:'space-between',gap:10,flexWrap:'wrap',paddingVertical:10},
  label:{color:'#8695ad',fontSize:10,marginBottom:6},gain:{color:'#66dfbb',fontSize:24,fontWeight:'600'},value:{color:'#e2e9f6',fontSize:19,fontWeight:'600'},
  legend:{color:'#92a4ba',fontSize:10},caption:{color:'#8495ae',fontSize:10,lineHeight:17},botRow:{flexDirection:'row',alignItems:'center',gap:10,paddingVertical:10,borderTopWidth:1,borderTopColor:'#263246'},
  dot:{width:7,height:7,borderRadius:4},botName:{flex:1},botTitle:{color:'#e6edf9',fontSize:13,fontWeight:'600'},positive:{color:'#66dfbb',fontSize:13,fontWeight:'600'},negative:{color:'#f58d9b',fontSize:13,fontWeight:'600'},footer:{color:'#65758e',fontSize:11,textAlign:'center'},
});

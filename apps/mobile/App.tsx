import React, { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { ResearchScreen } from './src/screens/ResearchScreen';
import { LiveScreen } from './src/screens/LiveScreen';

export default function App() {
  const [tab, setTab] = useState<'live' | 'report'>('live');
  return <SafeAreaProvider><SafeAreaView style={{ flex: 1, backgroundColor: '#080d1a' }}><StatusBar style="light" />
    <View style={{ flexDirection: 'row', gap: 8, paddingHorizontal: 22, paddingTop: 10 }}>
      {([['live', 'Live agent'], ['report', 'Saved report']] as const).map(([id, label]) => (
        <Pressable key={id} onPress={() => setTab(id)} accessibilityRole="tab" accessibilityState={{ selected: tab === id }}
          style={{ paddingHorizontal: 14, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: tab === id ? '#3987e5' : '#2d3b55', backgroundColor: tab === id ? '#1d2b44' : 'transparent' }}>
          <Text style={{ color: '#eff3fc', fontWeight: '600' }}>{label}</Text>
        </Pressable>
      ))}
    </View>
    {tab === 'live' ? <LiveScreen /> : <ResearchScreen />}
  </SafeAreaView></SafeAreaProvider>;
}

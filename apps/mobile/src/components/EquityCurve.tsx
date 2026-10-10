import React, { useMemo } from 'react';
import { View, StyleSheet } from 'react-native';
type Point = { equity: number; benchmark_equity: number };
/** Static chart using native views: no gesture handlers, WebView or GPU dependency. */
export function EquityCurve({ points }: { points: Point[] }) {
  const [width, setWidth] = React.useState(0);
  const segments = useMemo(() => {
    const values = points.flatMap(p => [p.equity, p.benchmark_equity]);
    const lo = Math.min(10000, ...values), hi = Math.max(10000, ...values);
    const height = 110, range = hi - lo || 1;
    return (['benchmark_equity', 'equity'] as const).flatMap(key =>
      points.slice(1).map((p, i) => {
        const x1 = i / (points.length - 1) * width;
        const x2 = (i + 1) / (points.length - 1) * width;
        const y1 = height - (points[i][key] - lo) / range * height;
        const y2 = height - (p[key] - lo) / range * height;
        const dx = x2 - x1, dy = y2 - y1;
        return { key: `${key}-${i}`, color: key === 'equity' ? '#60dfbf' : '#66748a',
          x: (x1 + x2) / 2, y: (y1 + y2) / 2,
          length: Math.hypot(dx, dy), angle: Math.atan2(dy, dx) };
      }));
  }, [points, width]);
  return <View accessibilityRole="image" accessibilityLabel="Static equity chart. Mint is portfolio, grey is buy and hold. Exact returns are shown above."
    style={styles.chart} onLayout={e => setWidth(e.nativeEvent.layout.width)}>
    {[25, 55, 85].map(y => <View key={y} style={[styles.grid, { top: y }]} />)}
    {width > 0 && segments.map(s => <View key={s.key} style={{ position: 'absolute', left: s.x - s.length / 2,
      top: s.y, width: s.length, height: 1.5, backgroundColor: s.color,
      transform: [{ rotate: `${s.angle}rad` }] }} />)}
  </View>;
}
const styles = StyleSheet.create({ chart: { height: 115, marginVertical: 16 }, grid: { position: 'absolute', left: 0, right: 0, height: 1, backgroundColor: '#202c40' } });

import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { Html, OrbitControls } from '@react-three/drei'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import * as THREE from 'three'
import { cssVar, fmt, STRATEGIES, STRATEGY_LABEL, STRATEGY_VAR, type EdgeRow, type Position, type Signal, type Status, type StrategyId } from '../api'

export interface Pulse { key: string; symbol: string; strategy_id: StrategyId; outcome: string; blocked_by: string | null; at: number }

interface Palette { [k: string]: string }
const ROLE_ORDER = ['developer', 'researcher', 'senior skeptic', 'trader']
const ROLE_SHORT: Record<string, string> = { developer: 'Developer', researcher: 'Researcher', 'senior skeptic': 'Skeptic', trader: 'Trader' }

/** Text drawn into a canvas texture on a camera-facing sprite: no DOM overlay, no web font download. */
function Label({ text, position, color, height = 0.42 }: { text: string; position: [number, number, number]; color: string; height?: number }) {
  const { texture, aspect } = useMemo(() => {
    const canvas = document.createElement('canvas')
    const ctx = canvas.getContext('2d')!
    const font = '600 48px system-ui, -apple-system, "Segoe UI", sans-serif'
    ctx.font = font
    const width = Math.ceil(ctx.measureText(text).width) + 16
    canvas.width = width
    canvas.height = 64
    ctx.font = font
    ctx.fillStyle = color
    ctx.textBaseline = 'middle'
    ctx.fillText(text, 8, 34)
    const map = new THREE.CanvasTexture(canvas)
    map.colorSpace = THREE.SRGBColorSpace
    map.anisotropy = 4
    return { texture: map, aspect: width / 64 }
  }, [text, color])
  useEffect(() => () => texture.dispose(), [texture])
  return (
    <sprite position={position} scale={[height * aspect, height, 1]} renderOrder={10}>
      <spriteMaterial map={texture} transparent depthWrite={false} depthTest={false} />
    </sprite>
  )
}

/** Pull the camera back on narrow (portrait) canvases so the whole grid stays in view. */
function ResponsiveCamera() {
  const { camera, size } = useThree()
  const aspect = size.width / Math.max(1, size.height)
  useEffect(() => {
    const k = aspect < 1 ? 1.35 : aspect < 1.25 ? 1.15 : 1
    camera.position.set(0, 12.5 * k, 14.5 * k)
    camera.updateProjectionMatrix()
  }, [camera, aspect])
  return null
}
const COL_GAP = 2.3
const ROW_GAP = 2.5

function usePalette(theme: string): Palette {
  // Read CSS tokens once per theme so the 3D scene follows light/dark.
  return useMemo(() => {
    void theme
    const keys = ['--s1', '--s2', '--s3', '--s4', '--s5', '--div-pos', '--div-neg', '--good', '--critical', '--surface-2', '--surface-3', '--grid', '--text-muted', '--text-primary', '--axis']
    return Object.fromEntries(keys.map(k => [k, cssVar(k)]))
  }, [theme])
}

function Tower({ x, z, height, color, lit, signal, position, pulse, selected, hovered, onHover, onSelect, palette, tooltip }: {
  x: number; z: number; height: number; color: string; lit: boolean; signal?: Signal; position?: Position; pulse?: Pulse
  selected: boolean; hovered: boolean; onHover: (on: boolean) => void; onSelect: () => void; palette: Palette; tooltip: ReactNode
}) {
  const mesh = useRef<THREE.Mesh>(null)
  const beacon = useRef<THREE.Mesh>(null)
  const ring = useRef<THREE.Mesh>(null)
  const flash = useRef<THREE.Mesh>(null)
  const current = useRef(0.4)
  useFrame((state, delta) => {
    // Ease toward the target height so the city visibly grows as labels mature.
    current.current += (height - current.current) * Math.min(1, delta * 3)
    if (mesh.current) {
      mesh.current.scale.y = current.current
      mesh.current.position.y = current.current / 2
    }
    const t = state.clock.elapsedTime
    if (beacon.current) {
      beacon.current.position.y = current.current + 0.45 + (signal?.fresh_signal ? Math.sin(t * 6) * 0.12 : 0)
      beacon.current.rotation.y = t
    }
    if (ring.current) ring.current.rotation.z = t * 0.8
    if (flash.current) {
      const age = pulse ? (performance.now() - pulse.at) / 1000 : 9
      const visible = age < 2.2
      flash.current.visible = visible
      if (visible) {
        const s = 1 + age * 0.35
        flash.current.scale.set(s, current.current * (1 + age * 0.1), s)
        flash.current.position.y = current.current / 2
        ;(flash.current.material as THREE.MeshBasicMaterial).opacity = Math.max(0, 0.45 - age * 0.2)
      }
    }
  })
  const signalColor = signal?.side === 'long' ? palette['--div-pos'] : palette['--div-neg']
  return (
    <group position={[x, 0, z]}>
      <mesh ref={mesh} castShadow onPointerOver={e => { e.stopPropagation(); onHover(true) }} onPointerOut={() => onHover(false)}
        onClick={e => { e.stopPropagation(); onSelect() }}>
        <boxGeometry args={[1.15, 1, 1.15]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={lit ? 0.75 : 0.06}
          roughness={0.55} metalness={0.1} transparent opacity={selected || hovered ? 1 : 0.9} />
      </mesh>
      {(selected || hovered) && (
        <mesh position={[0, 0.01, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[0.95, 1.05, 48]} />
          <meshBasicMaterial color={palette['--text-muted']} />
        </mesh>
      )}
      {signal && (
        <mesh ref={beacon} rotation={[signal.side === 'long' ? 0 : Math.PI, 0, 0]}>
          <coneGeometry args={[0.28, 0.5, 4]} />
          <meshStandardMaterial color={signalColor} emissive={signalColor} emissiveIntensity={signal.fresh_signal ? 1.2 : 0.35} />
        </mesh>
      )}
      {position && (
        <mesh ref={ring} position={[0, 0.08, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <torusGeometry args={[0.9, 0.06, 8, 48]} />
          <meshStandardMaterial color={palette['--good']} emissive={palette['--good']} emissiveIntensity={0.9} />
        </mesh>
      )}
      <mesh ref={flash} visible={false}>
        <boxGeometry args={[1.25, 1, 1.25]} />
        <meshBasicMaterial color={pulse?.outcome === 'paper_only_approved' ? palette['--good'] : palette['--critical']} transparent opacity={0} depthWrite={false} />
      </mesh>
      {hovered && (
        <Html position={[0, current.current + 1.1, 0]} center zIndexRange={[20, 0]}>
          <div className="tooltip3d">{tooltip}</div>
        </Html>
      )}
    </group>
  )
}

function AgentPlaza({ z, latest, palette }: { z: number; latest?: Pulse; palette: Palette }) {
  const packet = useRef<THREE.Mesh>(null)
  const xs = ROLE_ORDER.map((_, i) => (i - 1.5) * 2.6)
  const stopIndex = latest ? (latest.outcome === 'paper_only_approved' ? 3 : Math.max(0, ROLE_ORDER.indexOf(latest.blocked_by ?? 'trader'))) : -1
  useFrame(() => {
    if (!packet.current || !latest) return
    const age = (performance.now() - latest.at) / 1000
    const travel = Math.min(age / 0.6, stopIndex)  // 0.6s per agent hop
    const i = Math.floor(travel)
    const f = travel - i
    const x = i >= stopIndex ? xs[stopIndex] : xs[i] + (xs[i + 1] - xs[i]) * f
    packet.current.position.set(x, 1.6 + Math.sin(age * 8) * 0.05, z)
    packet.current.visible = age < stopIndex * 0.6 + 3
  })
  return (
    <group>
      {ROLE_ORDER.map((role, i) => {
        const reached = latest && i <= stopIndex
        const blocked = latest && i === stopIndex && latest.outcome !== 'paper_only_approved'
        const color = blocked ? palette['--critical'] : reached ? palette['--good'] : palette['--surface-3']
        return (
          <group key={role} position={[xs[i], 0, z]}>
            <mesh position={[0, 0.55, 0]}>
              <cylinderGeometry args={[0.55, 0.7, 1.1, 6]} />
              <meshStandardMaterial color={palette['--text-muted']} emissive={color} emissiveIntensity={reached ? 0.7 : 0.1} />
            </mesh>
            <Label text={ROLE_SHORT[role]} position={[0, 1.55, 0]} color={palette['--text-primary']} height={0.38} />
          </group>
        )
      })}
      {latest && (
        <mesh ref={packet}>
          <sphereGeometry args={[0.2, 16, 16]} />
          <meshStandardMaterial color={latest.outcome === 'paper_only_approved' ? palette['--good'] : palette['--div-pos']} emissive={latest.outcome === 'paper_only_approved' ? palette['--good'] : palette['--div-pos']} emissiveIntensity={1} />
        </mesh>
      )}
    </group>
  )
}

export default function City({ status, edge, positions, pulses, selected, onSelect, theme }: {
  status: Status; edge: EdgeRow[]; positions: Position[]; pulses: Pulse[]
  selected: { symbol: string; strategy: StrategyId }; onSelect: (s: { symbol: string; strategy: StrategyId }) => void; theme: string
}) {
  const palette = usePalette(theme)
  const [hovered, setHovered] = useState<string | null>(null)
  const symbols = status.symbols.length ? status.symbols : ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
  const zOf = (row: number) => (row - (symbols.length - 1) / 2) * ROW_GAP
  const plazaZ = zOf(symbols.length - 1) + ROW_GAP * 1.35
  const latest = pulses.length ? pulses[pulses.length - 1] : undefined
  const edgeFor = (strategy: string, assetClass: string) => edge.filter(r => r.strategy_id === strategy && r.asset_class === assetClass)
  const assetOf = (symbol: string) => (symbol.endsWith('USDT') ? 'crypto' : 'etf')
  return (
    <div className="city" role="img" aria-label="3D strategy city: one tower per symbol and strategy. Table views of the same data are in the Learning and Ledger tabs.">
      <Canvas shadows="percentage" camera={{ position: [0, 12.5, 14.5], fov: 44 }} dpr={[1, 2]} onPointerMissed={() => setHovered(null)}>
        <color attach="background" args={[palette['--surface-2']]} />
        <ambientLight intensity={0.55} />
        <directionalLight position={[6, 12, 6]} intensity={1.1} castShadow />
        <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow position={[0, -0.001, 0]}>
          <planeGeometry args={[40, 40]} />
          <meshStandardMaterial color={palette['--surface-3']} />
        </mesh>
        <gridHelper args={[40, 40, palette['--axis'], palette['--grid']]} position={[0, 0.002, 0]} />
        {symbols.map((symbol, row) => (
          <group key={symbol}>
            <Label text={fmt.short(symbol)} position={[-(STRATEGIES.length / 2) * COL_GAP - 0.9, 0.35, zOf(row)]} color={palette['--text-primary']} height={0.5} />
            {STRATEGIES.map((strategy, col) => {
              const key = `${symbol}|${strategy}`
              const counter = status.outcome_counters?.[key] ?? [0, 0, 0]
              const rows = edgeFor(strategy, assetOf(symbol))
              const lit = rows.some(r => r.supported)
              const signal = status.active_signals.find(s => s.symbol === symbol && s.strategy_id === strategy)
              const position = positions.find(p => p.symbol === symbol && p.strategy_id === strategy)
              const pulse = [...pulses].reverse().find(p => p.symbol === symbol && p.strategy_id === strategy)
              const height = 0.35 + Math.log1p(counter[0]) * 0.55
              return (
                <Tower key={key} x={(col - (STRATEGIES.length - 1) / 2) * COL_GAP} z={zOf(row)} height={height}
                  color={palette[STRATEGY_VAR[strategy]]} lit={lit} signal={signal} position={position} pulse={pulse}
                  selected={selected.symbol === symbol && selected.strategy === strategy} hovered={hovered === key}
                  onHover={on => setHovered(on ? key : null)} onSelect={() => onSelect({ symbol, strategy })} palette={palette}
                  tooltip={<>
                    <div style={{ fontWeight: 600 }}>{fmt.short(symbol)} · {STRATEGY_LABEL[strategy]}</div>
                    <div className="secondary">{counter[0]} matured 48h labels · {counter[0] ? Math.round(counter[1] / counter[0] * 100) : 0}% positive after costs</div>
                    {rows.map(r => <div key={r.side} className="secondary">{r.side}: {r.samples}/{r.minimum_samples} edge samples · lower bound {fmt.bps(r.lower_net_bps)} {r.supported ? '✓ supported' : ''}</div>)}
                    <div className="secondary">{signal ? `Signal: ${signal.side}${signal.fresh_signal ? ' (fresh)' : ''}` : 'No active signal'}</div>
                    {position && <div>Open paper {position.side} · {fmt.eur(position.unrealized_eur)} unrealized</div>}
                    <div className="muted">Click to inspect</div>
                  </>} />
              )
            })}
          </group>
        ))}
        <AgentPlaza z={plazaZ} latest={latest} palette={palette} />
        <ResponsiveCamera />
        <OrbitControls enableDamping makeDefault maxPolarAngle={Math.PI / 2.15} minDistance={6} maxDistance={32} target={[0, 0.4, 1.4]} />
      </Canvas>
      <div className="city-legend" aria-hidden="true">
        {STRATEGIES.map(s => <span key={s}><span className="swatch" style={{ background: `var(${STRATEGY_VAR[s]})` }} />{STRATEGY_LABEL[s]}</span>)}
        <span className="muted">Height = matured labels · glow = supported edge · ▲/▼ signal · ring = open position</span>
      </div>
    </div>
  )
}

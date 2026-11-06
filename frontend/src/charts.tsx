import { useEffect, useRef, useState, type ReactNode } from 'react'

/** Measure an element's width so SVG text stays at real pixel size. */
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [w, setW] = useState(600)
  useEffect(() => {
    if (!ref.current) return
    const ro = new ResizeObserver(([e]) => setW(Math.max(200, e.contentRect.width)))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  return [ref, w] as const
}

function niceTicks(max: number, count = 4, binary = false): number[] {
  if (max <= 0) return [0, 1]
  if (binary) {
    // pick ticks that are round in KB/MB/GB rather than in raw bytes
    const unit = 1024 ** Math.max(0, Math.floor(Math.log(max) / Math.log(1024)))
    return niceTicks(max / unit, count).map((t) => t * unit)
  }
  const raw = max / count
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw
  const ticks: number[] = []
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(v)
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step)
  return ticks
}

interface Tip { x: number; y: number; content: ReactNode }

function Tooltip({ tip }: { tip: Tip | null }) {
  if (!tip) return null
  return <div className="tooltip" style={{ left: tip.x, top: tip.y }}>{tip.content}</div>
}

export interface Point { label: string; value: number | null; tipLabel?: string }

const M = { top: 10, right: 12, bottom: 26, left: 52 }

function Axes({ w, h, ticks, max, fmt, labels }: {
  w: number; h: number; ticks: number[]; max: number; fmt: (v: number) => string
  labels: { x: number; text: string }[]
}) {
  const y = (v: number) => M.top + (h - M.top - M.bottom) * (1 - v / max)
  return (
    <g>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={M.left} x2={w - M.right} y1={y(t)} y2={y(t)} stroke={t === 0 ? 'var(--axis)' : 'var(--grid)'} strokeWidth={1} />
          <text x={M.left - 8} y={y(t)} dy="0.32em" textAnchor="end">{fmt(t)}</text>
        </g>
      ))}
      {labels.map((l, i) => (
        <text key={i} x={l.x} y={h - 8} textAnchor="middle">{l.text}</text>
      ))}
    </g>
  )
}

/** Pick x labels that won't collide (~70px apart). */
function spacedLabels(points: Point[], xOf: (i: number) => number, plotW: number) {
  const every = Math.max(1, Math.ceil(points.length / Math.max(1, Math.floor(plotW / 70))))
  return points.flatMap((p, i) => (i % every === 0 ? [{ x: xOf(i), text: p.label }] : []))
}

export function ColumnChart({ points, fmt, tipFmt, height = 220, ariaLabel, binary = false }: {
  points: Point[]; fmt: (v: number) => string; tipFmt?: (v: number) => string; height?: number; ariaLabel: string; binary?: boolean
}) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const [tip, setTip] = useState<Tip | null>(null)
  const [hover, setHover] = useState<number | null>(null)
  const max = Math.max(0, ...points.map((p) => p.value ?? 0))
  const ticks = niceTicks(max, 4, binary)
  const top = ticks[ticks.length - 1]
  const plotW = w - M.left - M.right
  const plotH = height - M.top - M.bottom
  const band = plotW / Math.max(1, points.length)
  const barW = Math.max(2, Math.min(24, band - 2))
  const xOf = (i: number) => M.left + band * i + band / 2
  return (
    <div className="chart" ref={ref}>
      <svg height={height} role="img" aria-label={ariaLabel} onMouseLeave={() => { setTip(null); setHover(null) }}>
        <Axes w={w} h={height} ticks={ticks} max={top} fmt={fmt} labels={spacedLabels(points, xOf, plotW)} />
        {points.map((p, i) => {
          const v = p.value ?? 0
          const bh = top ? (plotH * v) / top : 0
          const x = xOf(i) - barW / 2
          const y = M.top + plotH - bh
          const r = Math.min(4, bh, barW / 2)
          return (
            <g key={i}>
              {bh > 0 && (
                <path
                  d={`M${x},${y + bh} V${y + r} Q${x},${y} ${x + r},${y} H${x + barW - r} Q${x + barW},${y} ${x + barW},${y + r} V${y + bh} Z`}
                  fill="var(--series-1)" opacity={hover === null || hover === i ? 1 : 0.55}
                />
              )}
              <rect x={M.left + band * i} y={M.top} width={band} height={plotH} fill="transparent"
                    onMouseMove={() => {
                      setHover(i)
                      setTip({ x: xOf(i), y: Math.min(y, M.top + plotH - 4), content: <><strong>{(tipFmt ?? fmt)(v)}</strong><span>{p.tipLabel ?? p.label}</span></> })
                    }} />
            </g>
          )
        })}
      </svg>
      <Tooltip tip={tip} />
    </div>
  )
}

export function LineChart({ points, fmt, height = 220, ariaLabel, binary = false }: {
  points: Point[]; fmt: (v: number) => string; height?: number; ariaLabel: string; binary?: boolean
}) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const max = Math.max(0, ...points.map((p) => p.value ?? 0))
  const ticks = niceTicks(max, 4, binary)
  const top = ticks[ticks.length - 1]
  const plotW = w - M.left - M.right
  const plotH = height - M.top - M.bottom
  const step = points.length > 1 ? plotW / (points.length - 1) : 0
  const xOf = (i: number) => (points.length > 1 ? M.left + step * i : M.left + plotW / 2)
  const yOf = (v: number) => M.top + plotH * (1 - v / top)

  // Break the line where there's no data.
  const segments: number[][] = []
  let cur: number[] = []
  points.forEach((p, i) => {
    if (p.value == null) { if (cur.length) segments.push(cur); cur = [] } else cur.push(i)
  })
  if (cur.length) segments.push(cur)

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - box.left
    const i = Math.round((x - M.left) / (step || 1))
    setHover(Math.max(0, Math.min(points.length - 1, i)))
  }
  const hp = hover !== null ? points[hover] : null
  return (
    <div className="chart" ref={ref}>
      <svg height={height} role="img" aria-label={ariaLabel} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <Axes w={w} h={height} ticks={ticks} max={top} fmt={fmt} labels={spacedLabels(points, xOf, plotW)} />
        {segments.map((seg, k) => (
          <g key={k}>
            <path d={`M${xOf(seg[0])},${M.top + plotH} ${seg.map((i) => `L${xOf(i)},${yOf(points[i].value!)}`).join(' ')} L${xOf(seg[seg.length - 1])},${M.top + plotH} Z`}
                  fill="var(--series-1)" opacity={0.1} />
            <path d={seg.map((i, j) => `${j ? 'L' : 'M'}${xOf(i)},${yOf(points[i].value!)}`).join(' ')}
                  fill="none" stroke="var(--series-1)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          </g>
        ))}
        {hover !== null && (
          <g>
            <line x1={xOf(hover)} x2={xOf(hover)} y1={M.top} y2={M.top + plotH} stroke="var(--axis)" strokeWidth={1} />
            {hp?.value != null && <circle cx={xOf(hover)} cy={yOf(hp.value)} r={4} fill="var(--series-1)" stroke="var(--surface-1)" strokeWidth={2} />}
          </g>
        )}
      </svg>
      {hover !== null && hp && (
        <Tooltip tip={{ x: xOf(hover), y: hp.value != null ? yOf(hp.value) : M.top + plotH / 2,
          content: <><strong>{hp.value != null ? fmt(hp.value) : 'No data'}</strong><span>{hp.tipLabel ?? hp.label}</span></> }} />
      )}
    </div>
  )
}

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const STEPS = 8

/** Weekday × hour grid. cells[d][h], d = 0 for Monday. */
export function Heatmap({ cells, fmt }: { cells: number[][]; fmt: (v: number) => string }) {
  const [ref, w] = useWidth<HTMLDivElement>()
  const [tip, setTip] = useState<Tip | null>(null)
  const max = Math.max(0, ...cells.flat())
  const left = 40
  const gap = 2
  const cw = (w - left) / 24
  const ch = Math.min(28, Math.max(14, cw * 0.9))
  const height = ch * 7 + 22
  const step = (v: number) => (v <= 0 || !max ? 0 : Math.min(STEPS - 1, 1 + Math.floor(((STEPS - 1) * v) / (max + 1))))
  return (
    <div className="chart" ref={ref}>
      <svg height={height} role="img" aria-label="Uploads by day of week and hour" onMouseLeave={() => setTip(null)}>
        {DAYS.map((d, r) => <text key={d} x={0} y={r * ch + ch / 2} dy="0.32em">{d}</text>)}
        {[0, 3, 6, 9, 12, 15, 18, 21].map((h) => <text key={h} x={left + h * cw} y={7 * ch + 14}>{String(h).padStart(2, '0')}:00</text>)}
        {cells.map((row, r) => row.map((v, h) => (
          <rect key={`${r}-${h}`} x={left + h * cw + gap / 2} y={r * ch + gap / 2} width={Math.max(1, cw - gap)} height={ch - gap} rx={3}
                fill={`var(--seq-${step(v)})`}
                onMouseMove={() => setTip({ x: left + h * cw + cw / 2, y: r * ch, content: <><strong>{fmt(v)}</strong><span>{DAYS[r]} {String(h).padStart(2, '0')}:00–{String(h + 1).padStart(2, '0')}:00</span></> })} />
        )))}
      </svg>
      <div className="legend-scale">
        <span>Fewer</span>
        {Array.from({ length: STEPS }, (_, i) => <i key={i} style={{ background: `var(--seq-${i})` }} />)}
        <span>More</span>
      </div>
      <Tooltip tip={tip} />
    </div>
  )
}

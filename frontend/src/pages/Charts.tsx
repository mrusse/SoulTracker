import { useMemo, useState } from 'react'
import { api, type SeriesPoint } from '../api'
import { ColumnChart, Heatmap, LineChart, type Point } from '../charts'
import { ClientFilter, Seg } from '../components'
import { bytes, compact, count, speed } from '../format'
import { useClient, useData } from '../state'

type Range = '7' | '30' | '90' | '365' | 'all'
type Bucket = 'day' | 'week' | 'month'

function sinceFor(range: Range): string | undefined {
  if (range === 'all') return undefined
  const d = new Date(Date.now() - Number(range) * 86400000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} 00:00:00`
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function label(bucket: Bucket, key: string): { short: string; long: string } {
  if (bucket === 'day') {
    const [y, m, d] = key.split('-').map(Number)
    return { short: `${MONTHS[m - 1]} ${d}`, long: `${MONTHS[m - 1]} ${d}, ${y}` }
  }
  if (bucket === 'month') {
    const [y, m] = key.split('-').map(Number)
    return { short: `${MONTHS[m - 1]} ${String(y).slice(2)}`, long: `${MONTHS[m - 1]} ${y}` }
  }
  const [y, w] = key.split('-W')
  return { short: `W${w}`, long: `Week ${Number(w)}, ${y}` }
}

/** Fill gaps so quiet days/months show as zero instead of disappearing. */
function fill(bucket: Bucket, rows: SeriesPoint[]): SeriesPoint[] {
  if (rows.length < 2 || bucket === 'week') return rows
  const byKey = new Map(rows.map((r) => [r.bucket, r]))
  const out: SeriesPoint[] = []
  const empty = (k: string): SeriesPoint => ({ bucket: k, uploads: 0, bytes: 0, users: 0, avg_speed: null })
  if (bucket === 'day') {
    const d = new Date(rows[0].bucket + 'T00:00:00')
    const end = new Date(rows[rows.length - 1].bucket + 'T00:00:00')
    while (d <= end) {
      const k = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
      out.push(byKey.get(k) ?? empty(k))
      d.setDate(d.getDate() + 1)
    }
  } else {
    let [y, m] = rows[0].bucket.split('-').map(Number)
    const [ey, em] = rows[rows.length - 1].bucket.split('-').map(Number)
    while (y < ey || (y === ey && m <= em)) {
      const k = `${y}-${String(m).padStart(2, '0')}`
      out.push(byKey.get(k) ?? empty(k))
      if (++m > 12) { m = 1; y++ }
    }
  }
  return out
}

export default function Charts() {
  const [range, setRange] = useState<Range>('30')
  const [bucket, setBucket] = useState<Bucket>('day')
  const [table, setTable] = useState(false)
  const since = sinceFor(range)
  const client = useClient()
  const series = useData(() => api.timeseries(bucket, since, client), [bucket, range, client])
  const heat = useData(() => api.heatmap(since, client), [range, client])

  const rows = useMemo(() => fill(bucket, series.data ?? []), [bucket, series.data])
  const pts = (f: (r: SeriesPoint) => number | null): Point[] =>
    rows.map((r) => ({ label: label(bucket, r.bucket).short, tipLabel: label(bucket, r.bucket).long, value: f(r) }))
  const haveBytes = rows.some((r) => r.bytes > 0)
  const per = { day: 'day', week: 'week', month: 'month' }[bucket]

  const cells = useMemo(() => {
    const grid = Array.from({ length: 7 }, () => Array(24).fill(0) as number[])
    for (const c of heat.data ?? []) grid[(c.weekday + 6) % 7][c.hour] = c.uploads  // SQLite: 0 = Sunday
    return grid
  }, [heat.data])

  return (
    <>
      <div className="page-head">
        <h1>Charts</h1>
        <div className="controls">
          <ClientFilter />
          <Seg value={range} onChange={setRange} options={[['7', '7 days'], ['30', '30 days'], ['90', '90 days'], ['365', '1 year'], ['all', 'All time']]} />
          <Seg value={bucket} onChange={setBucket} options={[['day', 'Daily'], ['week', 'Weekly'], ['month', 'Monthly']]} />
          <label className="check"><input type="checkbox" checked={table} onChange={(e) => setTable(e.target.checked)} />Show as table</label>
        </div>
      </div>

      {series.data && rows.length === 0 ? (
        <div className="card empty">No uploads in this period.</div>
      ) : table ? (
        <div className="card">
          <table>
            <thead><tr><th>Period</th><th className="num">Uploads</th><th className="num">Data</th><th className="num">Users</th><th className="num">Avg speed</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.bucket}><td>{label(bucket, r.bucket).long}</td><td className="num">{count(r.uploads)}</td><td className="num">{bytes(r.bytes)}</td><td className="num">{count(r.users)}</td><td className="num">{speed(r.avg_speed)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="grid two">
          <div className="card">
            <h2>Uploads per {per}</h2>
            <ColumnChart points={pts((r) => r.uploads)} fmt={compact} tipFmt={(v) => `${count(v)} uploads`} ariaLabel={`Uploads per ${per}`} />
          </div>
          <div className="card">
            <h2>Data uploaded per {per}</h2>
            {haveBytes
              ? <ColumnChart points={pts((r) => r.bytes)} fmt={(v) => bytes(v)} binary ariaLabel={`Data uploaded per ${per}`} />
              : <div className="empty">File sizes aren't known yet. Mount your shared folders so SoulTracker can look them up.</div>}
          </div>
          <div className="card">
            <h2>Users per {per}</h2>
            <ColumnChart points={pts((r) => r.users)} fmt={compact} tipFmt={(v) => `${count(v)} users`} ariaLabel={`Users per ${per}`} />
          </div>
          <div className="card">
            <h2>Average upload speed</h2>
            <LineChart points={pts((r) => r.avg_speed)} fmt={(v) => `${bytes(v)}/s`} binary ariaLabel="Average upload speed" />
          </div>
        </div>
      )}

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Busiest times</h2>
        <div className="sub" style={{ marginTop: -8, marginBottom: 12 }}>Completed uploads by day of week and hour (the client's local time)</div>
        <Heatmap cells={cells} fmt={(v) => `${count(v)} uploads`} />
      </div>
    </>
  )
}

import { api, type Client } from './api'
import { STATUS } from './format'
import { useData, useSettings } from './state'

/** Status pill: color dot + label (never color alone). A Nicotine+ upload that hasn't finished yet
 *  really is in progress (SoulseekQt never logs a finish, so its "uploading" counts as completed). */
export function Status({ status, client }: { status: string; client?: Client }) {
  const s = status === 'uploading' && client === 'nicotine'
    ? { label: 'Uploading', color: 'var(--accent)' }
    : STATUS[status] ?? { label: status, color: 'var(--text-muted)' }
  return <span className="status"><i style={{ background: s.color }} />{s.label}</span>
}

export const CLIENT_LABEL: Record<Client, string> = { soulseekqt: 'SoulseekQt', nicotine: 'Nicotine+' }
const CLIENT_SHORT: Record<Client, string> = { soulseekqt: 'Qt', nicotine: 'N+' }

/** The clients that are set up (sources). Shared by every component that asks. */
let sourcesPromise: ReturnType<typeof api.sources> | null = null
export function useSources() {
  return useData(() => (sourcesPromise ??= api.sources().finally(() => setTimeout(() => { sourcesPromise = null }, 30000))), [], 60000)
}

/** Small "Qt" / "N+" tag on an upload; only shown when more than one client is set up. */
export function ClientTag({ client }: { client?: Client }) {
  const { data } = useSources()
  if (!client || !data || data.length < 2) return null
  return <span className={`client-tag ${client}`} title={CLIENT_LABEL[client]}>{CLIENT_SHORT[client]}</span>
}

/** All / SoulseekQt / Nicotine+ filter for a page; hidden unless both clients are set up. */
export function ClientFilter() {
  const { client, set } = useSettings()
  const { data } = useSources()
  if (!data || data.length < 2) return null
  return (
    <Seg<'all' | Client> value={client} onChange={(c) => set({ client: c })}
         options={[['all', 'All clients'], ...data.map((s) => [s.client, s.label] as ['all' | Client, string])]} />
  )
}

export function Seg<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: [T, string][] }) {
  return (
    <div className="seg" role="group">
      {options.map(([v, label]) => (
        <button key={v} className={v === value ? 'on' : ''} aria-pressed={v === value} onClick={() => onChange(v)}>{label}</button>
      ))}
    </div>
  )
}

import { STATUS } from './format'

/** Status pill: color dot + label (never color alone). */
export function Status({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, color: 'var(--text-muted)' }
  return <span className="status"><i style={{ background: s.color }} />{s.label}</span>
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

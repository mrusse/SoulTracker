export function bytes(n: number | null | undefined): string {
  if (n == null) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`
}

/** Like bytes(), but a 0 total means "unknown" (no sizes found on the shares). */
export function size(n: number | null | undefined): string {
  return n ? bytes(n) : '—'
}

export function count(n: number | null | undefined): string {
  return n == null ? '—' : n.toLocaleString()
}

export function compact(n: number): string {
  if (Math.abs(n) >= 1e6) return `${+(n / 1e6).toFixed(1)}M`
  if (Math.abs(n) >= 1e4) return `${+(n / 1e3).toFixed(0)}K`
  return n.toLocaleString()
}

export function speed(bps: number | null | undefined): string {
  return bps ? `${bytes(bps)}/s` : '—'
}

function parse(ts: string): Date {
  return new Date(ts.replace(' ', 'T'))
}

export function date(ts: string | null | undefined): string {
  if (!ts) return '—'
  return parse(ts).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function ago(ts: string | null | undefined): string {
  if (!ts) return '—'
  const s = (Date.now() - parse(ts).getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} d ago`
}

/** "@@tok\Music\Artist\Album" -> "Album", or the full path without the share token. */
export function folderName(folder: string | null | undefined, full: boolean): string {
  if (!folder) return '(unknown folder)'
  const parts = folder.split('\\')
  if (parts[0].startsWith('@@')) parts.shift()
  return full ? parts.join(' / ') : parts[parts.length - 1] || parts.join(' / ')
}

/** Deterministic stand-in name for privacy mode. */
export function pseudonym(name: string): string {
  let h = 2166136261
  for (let i = 0; i < name.length; i++) h = Math.imul(h ^ name.charCodeAt(i), 16777619)
  return `user-${(h >>> 0).toString(36).slice(0, 5).padStart(5, '0')}`
}

export const STATUS: Record<string, { label: string; color: string }> = {
  completed: { label: 'Completed', color: 'var(--good)' },
  uploading: { label: 'Completed', color: 'var(--good)' },
  failed: { label: 'Failed', color: 'var(--critical)' },
  local_error: { label: 'Local error', color: 'var(--critical)' },
  cancelled: { label: 'Cancelled', color: 'var(--serious)' },
  denied: { label: 'Denied', color: 'var(--warning)' },
  queued: { label: 'Queued', color: 'var(--text-muted)' },
}

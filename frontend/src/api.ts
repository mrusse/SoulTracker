export interface Summary {
  uploads: number
  bytes: number
  uploads_unknown_size: number
  users: number
  files: number
  failed: number
  cancelled: number
  denied: number
  queued: number
  avg_speed: number | null
  first_seen: string | null
  last_seen: string | null
  last_upload: { user: string; file: string | null; folder: string | null; time: string } | null
  log: { path: string; exists: boolean; last_line_at: string | null }
}

export interface UserRow { user: string; uploads: number; bytes: number; first_upload?: string; last_upload: string }
export interface FolderRow { folder: string; users: number; uploads: number; bytes: number; last_upload: string; last_user: string | null }

export interface UserFile {
  file_id: number; vpath: string; folder: string; name: string; size: number | null
  time: string; speed: number | null; status: string
}
export interface UserDetail {
  user: string; uploads: number; bytes: number; first_upload: string | null; last_upload: string | null
  failed: number; cancelled: number; denied: number; queued: number; avg_speed: number | null
  folders: { folder: string | null; uploads: number; bytes: number; last_upload: string; files: UserFile[] }[]
}
export interface FolderDetail {
  folder: string; users: number; uploads: number; bytes: number; first_upload: string; last_upload: string
  last_user: string | null
  by_user: { user: string; uploads: number; last_upload: string }[]
  files: { file_id: number; name: string; size: number | null; uploads: number; last_upload: string }[]
}
export interface FileDetail {
  file_id: number; vpath: string; folder: string; name: string; size: number | null
  uploads: number; users: number; last_user: string | null; last_upload: string | null
  history: { user: string; status: string; reason: string | null; requested_at: string | null; started_at: string | null; ended_at: string | null; speed: number | null }[]
}
export interface RecentRow {
  user: string; file_id: number | null; name: string | null; folder: string | null; size: number | null
  status: string; reason: string | null; time: string; ended_at: string | null; speed: number | null
}
export interface SearchResult {
  users: { user: string; uploads: number }[]
  folders: { folder: string; uploads: number }[]
  files: { file_id: number; name: string; folder: string; uploads: number }[]
}
export interface SeriesPoint { bucket: string; uploads: number; bytes: number; users: number; avg_speed: number | null }
export interface HeatCell { weekday: number; hour: number; uploads: number }
export interface Outcomes {
  by_reason: { status: string; reason: string; count: number }[]
  problem_files: { file_id: number; name: string; folder: string; failures: number; users: number }[]
}
export interface Diagnostics extends Outcomes {
  counts: { failed: number; cancelled: number; denied: number; queued: number; uploads: number }
  unknown_sizes: {
    unknown_file: { uploads: number; by_user: { user: string; uploads: number; first: string; last: string }[] }
    not_found: { uploads: number; files: number; examples: { file_id: number; name: string; folder: string; uploads: number }[] }
  }
  log: { path: string; exists: boolean; last_line_at: string | null; first_seen: string | null; last_seen: string | null }
  shares: string[]
}
export interface HiddenItem { kind: 'user' | 'folder' | 'file'; value: string }

async function get<T>(path: string, params: Record<string, string | number | undefined> = {}): Promise<T> {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') qs.set(k, String(v))
  const res = await fetch(`/api/${path}${qs.size ? `?${qs}` : ''}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function send(method: string, path: string, body?: unknown, params: Record<string, string> = {}) {
  const qs = new URLSearchParams(params)
  const res = await fetch(`/api/${path}${qs.size ? `?${qs}` : ''}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
}

export const api = {
  summary: () => get<Summary>('summary'),
  topUsers: (by: 'bytes' | 'uploads', limit = 100) => get<UserRow[]>('top/users', { by, limit }),
  topFolders: (by: 'users' | 'uploads' | 'bytes', limit = 100) => get<FolderRow[]>('top/folders', { by, limit }),
  users: (sort: string, search?: string) => get<UserRow[]>('users', { sort, search }),
  user: (name: string) => get<UserDetail>('user', { name }),
  folder: (path: string) => get<FolderDetail>('folder', { path }),
  file: (id: number) => get<FileDetail>(`file/${id}`),
  search: (q: string) => get<SearchResult>('search', { q }),
  recent: (limit = 30) => get<RecentRow[]>('recent', { limit }),
  timeseries: (bucket: string, since?: string) => get<SeriesPoint[]>('charts/timeseries', { bucket, since }),
  heatmap: (since?: string) => get<HeatCell[]>('charts/heatmap', { since }),
  outcomes: () => get<Outcomes>('outcomes'),
  diagnostics: () => get<Diagnostics>('diagnostics'),
  hidden: () => get<HiddenItem[]>('hidden'),
  hide: (kind: HiddenItem['kind'], value: string) => send('POST', 'hidden', { kind, value }),
  unhide: (kind: HiddenItem['kind'], value: string) => send('DELETE', 'hidden', undefined, { kind, value }),
}

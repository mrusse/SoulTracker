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
  transfer_id: number; client: Client; file_id: number; vpath: string; folder: string; name: string; size: number | null
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
  history: { user: string; client: Client; status: string; reason: string | null; requested_at: string | null; started_at: string | null; ended_at: string | null; speed: number | null }[]
}
export interface RecentRow {
  user: string; client: Client; file_id: number | null; name: string | null; folder: string | null; size: number | null
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
  sources: Source[]
}
export interface TrackMeta {
  title: string | null; artist: string | null; album: string | null; albumartist: string | null
  year: string | null; genre: string | null; track: string | null
  duration: number | null; bitrate: number | null; sample_rate: number | null; bits_per_sample: number | null
  channels: number | null; playable: boolean; format: string; size: number
}
export interface LastFMStatus {
  configured: boolean; connected: boolean; user: string | null; enabled: boolean
  sent: number; pending: number
  now_playing: { at: number; artist: string | null; title: string | null; ok: boolean; error: string | null } | null
  recent: { artist: string | null; title: string | null; album: string | null; played_at: number; status: string; error: string | null }[]
}
export type Client = 'soulseekqt' | 'nicotine'
export interface Source {
  client: Client; label: string; path: string; exists: boolean; last_line_at: string | null
  importing?: { done: number; total: number } | null   // Nicotine+ history import in progress
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

// c = which client to show ('soulseekqt' / 'nicotine'), or undefined for all of them
type C = Client | undefined
export const api = {
  sources: () => get<Source[]>('sources'),
  summary: (c?: C) => get<Summary>('summary', { client: c }),
  topUsers: (by: 'bytes' | 'uploads', limit = 100, c?: C) => get<UserRow[]>('top/users', { by, limit, client: c }),
  topFolders: (by: 'users' | 'uploads' | 'bytes', limit = 100, c?: C) => get<FolderRow[]>('top/folders', { by, limit, client: c }),
  users: (sort: string, search?: string, c?: C) => get<UserRow[]>('users', { sort, search, client: c }),
  user: (name: string, c?: C) => get<UserDetail>('user', { name, client: c }),
  folder: (path: string, c?: C) => get<FolderDetail>('folder', { path, client: c }),
  file: (id: number, c?: C) => get<FileDetail>(`file/${id}`, { client: c }),
  search: (q: string, c?: C) => get<SearchResult>('search', { q, client: c }),
  recent: (limit = 30, c?: C) => get<RecentRow[]>('recent', { limit, client: c }),
  timeseries: (bucket: string, since?: string, c?: C) => get<SeriesPoint[]>('charts/timeseries', { bucket, since, client: c }),
  heatmap: (since?: string, c?: C) => get<HeatCell[]>('charts/heatmap', { since, client: c }),
  outcomes: () => get<Outcomes>('outcomes'),
  diagnostics: () => get<Diagnostics>('diagnostics'),
  meta: (id: number) => get<TrackMeta>(`file/${id}/meta`),
  nowPlaying: (np: { file_id: number | null; play_id?: string; state: 'playing' | 'paused' | 'stopped'; position?: number; duration?: number }) =>
    send('POST', 'nowplaying', np),
  shuffle: async (body: { mode: string; users: number; window: string; exclude: number[]; count: number }) => {
    const res = await fetch('/api/shuffle', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    return (await res.json()) as { id: number; name: string; folder: string | null }[]
  },
  scrobble: (file_id: number, played_at: number) => send('POST', 'scrobble', { file_id, played_at }),
  lastfm: () => get<LastFMStatus>('lastfm'),
  lastfmDisconnect: () => send('POST', 'lastfm/disconnect', {}),
  lastfmEnabled: (enabled: boolean) => send('POST', 'lastfm/enabled', { enabled }),
  hidden: () => get<HiddenItem[]>('hidden'),
  hide: (kind: HiddenItem['kind'], value: string) => send('POST', 'hidden', { kind, value }),
  unhide: (kind: HiddenItem['kind'], value: string) => send('DELETE', 'hidden', undefined, { kind, value }),
}

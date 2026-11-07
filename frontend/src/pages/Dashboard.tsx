import { useState } from 'react'
import { api } from '../api'
import type { RecentRow as Recent } from '../api'
import { ClientFilter, ClientTag, Seg, Status } from '../components'
import { FileMenu, isPlayable, useClickOrDouble, usePlayer, type Track } from '../player'
import { ago, bytes, count, date, folderName, size, speed } from '../format'
import { navigate, useClient, useData, useName, useSettings } from '../state'

const REFRESH = 15000

export default function Dashboard() {
  const name = useName()
  const { fullPath, maxUnit } = useSettings()
  const [userBy, setUserBy] = useState<'bytes' | 'uploads'>('bytes')
  const [folderBy, setFolderBy] = useState<'users' | 'uploads'>('users')
  const client = useClient()
  const summary = useData(() => api.summary(client), [client], REFRESH)
  const users = useData(() => api.topUsers(userBy, 15, client), [userBy, client], REFRESH)
  const folders = useData(() => api.topFolders(folderBy, 15, client), [folderBy, client], REFRESH)
  const recent = useData(() => api.recent(15, client), [client], REFRESH)

  const s = summary.data
  if (summary.error && !s) return <div className="empty">Couldn't load stats: {summary.error}</div>
  if (!s) return <div className="empty">Loading…</div>
  const haveSizes = s.bytes > 0

  return (
    <>
      <div className="page-head">
        <h1>Dashboard</h1>
        <div className="controls">
          <span className="sub">{s.first_seen ? `Tracking since ${date(s.first_seen)}` : 'Waiting for log data'}</span>
          <ClientFilter />
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="hero">
          <div>
            <div className="label">{haveSizes ? 'Total uploaded' : 'Completed uploads'}</div>
            <div className="value">{haveSizes ? bytes(s.bytes, maxUnit) : count(s.uploads)}</div>
          </div>
          {s.last_upload && (
            <div className="sub" style={{ paddingBottom: 6 }}>
              Last upload {ago(s.last_upload.time)} to <a href={`#/users?user=${encodeURIComponent(s.last_upload.user)}`}>{name(s.last_upload.user)}</a>
              {s.last_upload.file ? <> · {s.last_upload.file}</> : null}
            </div>
          )}
        </div>
        <div className="tiles">
          <Tile label="Uploads" value={count(s.uploads)} />
          <Tile label="Users" value={count(s.users)} />
          <Tile label="Different files" value={count(s.files)} />
          <Tile label="Average speed" value={speed(s.avg_speed)} />
        </div>
      </div>

      <div className="grid two" style={{ marginBottom: 16 }}>
        <div className="card">
          <div className="page-head" style={{ marginBottom: 8 }}>
            <h2 style={{ margin: 0 }}>Top users</h2>
            <Seg value={userBy} onChange={setUserBy} options={[['bytes', 'By size'], ['uploads', 'By uploads']]} />
          </div>
          <table>
            <thead><tr><th className="rank">#</th><th>User</th><th className="num">Uploads</th><th className="num">Size</th></tr></thead>
            <tbody>
              {users.data?.map((u, i) => (
                <tr key={u.user} className="clickable" onClick={() => navigate('users', { user: u.user })}>
                  <td className="rank">{i + 1}</td>
                  <td className="clip">{name(u.user)}</td>
                  <td className="num">{count(u.uploads)}</td>
                  <td className="num">{size(u.bytes, maxUnit)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {users.data?.length === 0 && <div className="empty">No uploads yet</div>}
        </div>

        <div className="card">
          <div className="page-head" style={{ marginBottom: 8 }}>
            <h2 style={{ margin: 0 }}>Top folders</h2>
            <Seg value={folderBy} onChange={setFolderBy} options={[['users', 'By users'], ['uploads', 'By files']]} />
          </div>
          <table>
            <thead><tr><th className="rank">#</th><th>Folder</th><th className="num">Users</th><th className="num">Files</th></tr></thead>
            <tbody>
              {folders.data?.map((f, i) => (
                <tr key={f.folder} className="clickable" onClick={() => navigate('users', { folder: f.folder })}
                    title={`Last downloaded by ${name(f.last_user)}, ${date(f.last_upload)}`}>
                  <td className="rank">{i + 1}</td>
                  <td className="clip">{folderName(f.folder, fullPath)}</td>
                  <td className="num">{count(f.users)}</td>
                  <td className="num">{count(f.uploads)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {folders.data?.length === 0 && <div className="empty">No uploads yet</div>}
        </div>
      </div>

      <div className="card">
        <h2>Recent activity</h2>
        <table>
          <thead><tr><th>When</th><th>User</th><th>File</th><th>Outcome</th><th className="num">Size</th></tr></thead>
          <tbody>
            {recent.data?.map((r, i) => <RecentRow key={i} r={r} />)}
          </tbody>
        </table>
      </div>
    </>
  )
}

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {note && <div className="note">{note}</div>}
    </div>
  )
}

function RecentRow({ r }: { r: Recent }) {
  const name = useName()
  const { maxUnit } = useSettings()
  const player = usePlayer()
  const t: Track | null = r.file_id && r.name ? { id: r.file_id, name: r.name, folder: r.folder } : null
  const clicks = useClickOrDouble(
    () => { if (r.file_id) navigate('users', { file: String(r.file_id) }) },
    () => { if (t && isPlayable(t.name)) player.play([t]) },
  )
  const row = (
    <tr className={`${r.file_id ? 'clickable' : ''} ${t && player.current?.id === t.id ? 'now-playing' : ''}`} {...clicks}>
      <td style={{ whiteSpace: 'nowrap' }} title={date(r.time)}>{ago(r.time)}</td>
      <td className="clip" style={{ width: '22%' }}>{name(r.user)}</td>
      <td className="clip" style={{ width: '48%' }} title={r.folder ? folderName(r.folder, true) : undefined}>{r.name ?? <span className="sub">unknown file (requested before tracking began)</span>}<ClientTag client={r.client} /></td>
      <td title={r.reason ?? undefined}><Status status={r.status} client={r.client} /></td>
      <td className="num">{size(r.size, maxUnit)}</td>
    </tr>
  )
  return t ? <FileMenu track={t}>{row}</FileMenu> : row
}

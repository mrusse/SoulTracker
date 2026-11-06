import { useEffect, useMemo, useState } from 'react'
import { api, type UserDetail, type UserFile } from '../api'
import { Status } from '../components'
import { bytes, count, date, folderName, size, speed } from '../format'
import { Cover, FileMenu, FolderMenu, isAudio, isPlayable, usePlayer, type Track } from '../player'
import { navigate, useData, useName, useSettings, type FileSort } from '../state'

type Sel = { kind: 'user'; value: string } | { kind: 'folder'; value: string; user?: string } | { kind: 'file'; value: number; user?: string; row?: number }

const SORTS: [string, string][] = [
  ['size', 'Total size'],
  ['uploads', 'Number of uploads'],
  ['last_desc', 'Last download (newest first)'],
  ['last_asc', 'Last download (oldest first)'],
  ['alpha', 'Alphabetical'],
]
const EXPAND_ALL_LIMIT = 200

function selFromParams(p: URLSearchParams): Sel | null {
  if (p.get('file')) return { kind: 'file', value: Number(p.get('file')), user: p.get('user') ?? undefined, row: p.get('row') ? Number(p.get('row')) : undefined }
  if (p.get('folder')) return { kind: 'folder', value: p.get('folder')!, user: p.get('user') ?? undefined }
  if (p.get('user')) return { kind: 'user', value: p.get('user')! }
  return null
}

export default function Users({ params }: { params: URLSearchParams }) {
  const name = useName()
  const { fullPath, set, userSort: sort, expandAll } = useSettings()
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [details, setDetails] = useState<Record<string, UserDetail>>({})
  const [version, setVersion] = useState(0)
  const sel = useMemo(() => selFromParams(params), [params])

  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), 250)
    return () => clearTimeout(t)
  }, [query])

  const users = useData(() => api.users(sort), [sort, version])
  const results = useData(() => (debounced ? api.search(debounced) : Promise.resolve(null)), [debounced, version])

  const loadUser = (u: string) => {
    if (details[u]) return
    api.user(u).then((d) => setDetails((prev) => ({ ...prev, [u]: d }))).catch(() => {})
  }

  // Open the selected user's branch when arriving from a link.
  useEffect(() => {
    const u = sel?.kind === 'user' ? sel.value : sel?.user
    if (u) {
      setOpen((prev) => new Set(prev).add(u))
      loadUser(u)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel])

  // Bring the selected user's row into view once the list (and their branch) has rendered.
  const selUser = sel?.kind === 'user' ? sel.value : sel?.user
  useEffect(() => {
    if (!selUser || debounced) return
    const id = requestAnimationFrame(() => {
      const el = [...document.querySelectorAll<HTMLElement>('[data-user]')].find((e) => e.dataset.user === selUser)
      el?.scrollIntoView({ block: 'start', behavior: 'smooth' })
    })
    return () => cancelAnimationFrame(id)
  }, [selUser, debounced, users.data, details[selUser ?? '']])

  useEffect(() => {
    if (!expandAll || !users.data) return
    users.data.slice(0, EXPAND_ALL_LIMIT).forEach((u) => loadUser(u.user))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandAll, users.data])

  const toggle = (u: string) => {
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(u)) next.delete(u)
      else { next.add(u); loadUser(u) }
      return next
    })
  }

  const select = (s: Sel) => {
    // Picking a user (from search, "Downloaded by", etc.) shows them in the full list.
    if (s.kind === 'user') { setQuery(''); setDebounced('') }
    const p: Record<string, string> = {}
    if (s.kind === 'user') p.user = s.value
    else {
      if (s.user) p.user = s.user
      p[s.kind] = String(s.value)
      if (s.kind === 'file' && s.row) p.row = String(s.row)  // which of several identical rows (repeat downloads)
    }
    navigate('users', p)
  }

  const removed = () => {
    setDetails({})
    setVersion((v) => v + 1)
    navigate('users')
  }

  const isOpen = (u: string) => open.has(u) || (expandAll && !!details[u])

  return (
    <>
      <div className="page-head">
        <h1>Users &amp; files</h1>
        <div className="controls">
          <input type="search" placeholder="Search users, folders, files…" value={query} onChange={(e) => setQuery(e.target.value)} />
          {!debounced && (
            <select value={sort} onChange={(e) => set({ userSort: e.target.value })} aria-label="Sort users by">
              {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          )}
          {!debounced && <label className="check"><input type="checkbox" checked={expandAll} onChange={(e) => set({ expandAll: e.target.checked })} />Expand all</label>}
          <label className="check"><input type="checkbox" checked={fullPath} onChange={(e) => set({ fullPath: e.target.checked })} />Show full path</label>
        </div>
      </div>

      <div className="browser">
        <div className="card tree">
          {debounced ? (
            <SearchResults results={results.data} select={select} />
          ) : (
            <>
              {users.data?.length === 0 && <div className="empty">No uploads yet</div>}
              {users.data?.map((u) => (
                <div key={u.user} data-user={u.user}>
                  <div className={`node ${sel?.kind === 'user' && sel.value === u.user ? 'sel' : ''}`}
                       onClick={() => select({ kind: 'user', value: u.user })}>
                    <span className="tw" onClick={(e) => { e.stopPropagation(); toggle(u.user) }}>{isOpen(u.user) ? '▼' : '▶'}</span>
                    <span className="name">{name(u.user)}</span>
                    <span className="meta">{count(u.uploads)}{u.bytes ? ` · ${bytes(u.bytes)}` : ''}</span>
                  </div>
                  {isOpen(u.user) && (
                    <div className="children">
                      {!details[u.user] && <div className="node"><span className="sub">Loading…</span></div>}
                      {details[u.user]?.folders.map((f) => (
                        <FolderNode key={f.folder ?? ''} user={u.user} folder={f} sel={sel} select={select} fullPath={fullPath} />
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </>
          )}
        </div>

        <div className="card detail">
          <Detail sel={sel} onRemoved={removed} select={select} />
        </div>
      </div>
    </>
  )
}

const byName = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

/** Files by name in natural order ("2 x" before "10 x"), or newest download first. */
function sortFiles(files: UserFile[], sort: FileSort): UserFile[] {
  if (sort === 'time') return files  // the API returns newest first
  return [...files].sort((a, b) => byName.compare(a.name ?? '', b.name ?? ''))
}

function FolderNode({ user, folder, sel, select, fullPath }: {
  user: string; folder: UserDetail['folders'][number]; sel: Sel | null; select: (s: Sel) => void; fullPath: boolean
}) {
  const { fileSort } = useSettings()
  const [open, setOpen] = useState(false)
  const key = folder.folder ?? ''
  const selected = sel?.kind === 'folder' && sel.value === key && sel.user === user
  const hasSelectedFile = sel?.kind === 'file' && sel.user === user && folder.files.some((f) => f.file_id === sel.value)
  const isOpen = open || hasSelectedFile
  const player = usePlayer()
  const files = sortFiles(folder.files, fileSort)
  const seen = new Set<number>()
  const tracks: Track[] = files.flatMap((f) => (f.file_id && !seen.has(f.file_id) && seen.add(f.file_id)
    ? [{ id: f.file_id, name: f.name, folder: f.folder }] : []))
  return (
    <div>
      <FolderMenu tracks={tracks}>
        <div className={`node ${selected ? 'sel' : ''}`} onClick={() => folder.folder && select({ kind: 'folder', value: key, user })}
             onDoubleClick={() => setOpen(!isOpen)}>
          <span className="tw" onClick={(e) => { e.stopPropagation(); setOpen(!isOpen) }}>{isOpen ? '▼' : '▶'}</span>
          <span className="name" title={folderName(folder.folder, true)}>{folderName(folder.folder, fullPath)}</span>
          <span className="meta">{count(folder.uploads)}{folder.bytes ? ` · ${bytes(folder.bytes)}` : ''}</span>
        </div>
      </FolderMenu>
      {isOpen && (
        <div className="children">
          {files.map((f, i) => {
            const t: Track = { id: f.file_id, name: f.name, folder: f.folder }
            // A file downloaded many times appears once per download; mark only one row.
            const firstCopy = files.findIndex((g) => g.file_id === f.file_id) === i
            const nowPlaying = player.current?.id === f.file_id && firstCopy
            const selected = sel?.kind === 'file' && sel.value === f.file_id && sel.user === user &&
              (sel.row ? sel.row === f.transfer_id : firstCopy)
            return (
              <FileMenu key={f.transfer_id} track={t}>
                <div className={`node ${selected ? 'sel' : ''} ${nowPlaying ? 'now-playing' : ''}`}
                     onClick={() => f.file_id && select({ kind: 'file', value: f.file_id, user, row: f.transfer_id })}
                     onDoubleClick={() => isPlayable(f.name) && player.play([t])}
                     title={isPlayable(f.name) ? 'Double-click to play, right-click for more' : undefined}>
                  <span className="tw">{nowPlaying ? (player.playing ? '♪' : '‖') : ''}</span>
                  <span className="name">{f.name ?? 'unknown file'}</span>
                  <span className="meta">{size(f.size)}</span>
                </div>
              </FileMenu>
            )
          })}
        </div>
      )}
    </div>
  )
}

function SearchResults({ results, select }: { results: Awaited<ReturnType<typeof api.search>> | null; select: (s: Sel) => void }) {
  const name = useName()
  const player = usePlayer()
  const { fullPath } = useSettings()
  if (!results) return <div className="empty">Searching…</div>
  const none = !results.users.length && !results.folders.length && !results.files.length
  if (none) return <div className="empty">Nothing matches</div>
  return (
    <>
      {results.users.length > 0 && <div className="sub" style={{ padding: '4px 8px' }}>Users</div>}
      {results.users.map((u) => (
        <div key={u.user} className="node" onClick={() => select({ kind: 'user', value: u.user })}>
          <span className="name">{name(u.user)}</span><span className="meta">{count(u.uploads)}</span>
        </div>
      ))}
      {results.folders.length > 0 && <div className="sub" style={{ padding: '10px 8px 4px' }}>Folders</div>}
      {results.folders.map((f) => (
        <div key={f.folder} className="node" onClick={() => select({ kind: 'folder', value: f.folder })}>
          <span className="name" title={folderName(f.folder, true)}>{folderName(f.folder, fullPath)}</span><span className="meta">{count(f.uploads)}</span>
        </div>
      ))}
      {results.files.length > 0 && <div className="sub" style={{ padding: '10px 8px 4px' }}>Files</div>}
      {results.files.map((f) => {
        const t: Track = { id: f.file_id, name: f.name, folder: f.folder }
        return (
          <FileMenu key={f.file_id} track={t}>
            <div className="node" onClick={() => select({ kind: 'file', value: f.file_id })}
                 onDoubleClick={() => isPlayable(f.name) && player.play([t])}>
              <span className="name" title={folderName(f.folder, true)}>{f.name}</span><span className="meta">{count(f.uploads)}</span>
            </div>
          </FileMenu>
        )
      })}
    </>
  )
}

function Detail({ sel, onRemoved, select }: { sel: Sel | null; onRemoved: () => void; select: (s: Sel) => void }) {
  if (!sel) return <div className="empty">Select a user, folder or file to see its stats.</div>
  if (sel.kind === 'user') return <UserInfo name={sel.value} onRemoved={onRemoved} />
  if (sel.kind === 'folder') return <FolderInfo path={sel.value} onRemoved={onRemoved} select={select} />
  return <FileInfo id={sel.value} onRemoved={onRemoved} select={select} />
}

function RemoveButton({ kind, value, label, onRemoved }: { kind: 'user' | 'folder' | 'file'; value: string; label: string; onRemoved: () => void }) {
  return (
    <button className="btn danger" style={{ marginTop: 16 }} onClick={async () => {
      if (!confirm(`Remove ${label} from all stats? You can bring it back from Settings.`)) return
      await api.hide(kind, value)
      onRemoved()
    }}>Remove from stats</button>
  )
}

function UserInfo({ name: user, onRemoved }: { name: string; onRemoved: () => void }) {
  const name = useName()
  const { data, error } = useData(() => api.user(user), [user])
  if (error) return <div className="empty">{error.startsWith('404') ? 'No uploads for this user.' : error}</div>
  if (!data) return <div className="empty">Loading…</div>
  return (
    <>
      <h2>User</h2>
      <dl className="kv">
        <dt>Username</dt><dd>{name(data.user)}</dd>
        <dt>Uploads</dt><dd>{count(data.uploads)}</dd>
        <dt>Total size</dt><dd>{size(data.bytes)}</dd>
        <dt>Folders</dt><dd>{count(data.folders.length)}</dd>
        <dt>First upload</dt><dd>{date(data.first_upload)}</dd>
        <dt>Last upload</dt><dd>{date(data.last_upload)}</dd>
        <dt>Average speed</dt><dd>{speed(data.avg_speed)}</dd>
        <dt>Failed</dt><dd>{count(data.failed)}</dd>
        <dt>Cancelled</dt><dd>{count(data.cancelled)}</dd>
        <dt>Denied</dt><dd>{count(data.denied)}</dd>
        <dt>Still queued</dt><dd>{count(data.queued)}</dd>
      </dl>
      <RemoveButton kind="user" value={user} label={`user "${name(user)}"`} onRemoved={onRemoved} />
    </>
  )
}

function FolderInfo({ path, onRemoved, select }: { path: string; onRemoved: () => void; select: (s: Sel) => void }) {
  const name = useName()
  const player = usePlayer()
  const { fileSort } = useSettings()
  const { data, error } = useData(() => api.folder(path), [path])
  if (error) return <div className="empty">{error}</div>
  if (!data) return <div className="empty">Loading…</div>
  const files = fileSort === 'time'
    ? [...data.files].sort((a, b) => (b.last_upload ?? '').localeCompare(a.last_upload ?? ''))
    : [...data.files].sort((a, b) => byName.compare(a.name, b.name))
  const tracks: Track[] = files.map((f) => ({ id: f.file_id, name: f.name, folder: data.folder }))
  const playable = tracks.filter((t) => isPlayable(t.name))
  return (
    <>
      <div className="info-head" style={{ marginBottom: 14 }}>
        {playable.length > 0 && <Cover id={playable[0].id} size={96} />}
        <div style={{ minWidth: 0 }}>
          <h2 style={{ marginBottom: 6 }}>Folder</h2>
          {playable.length > 0 && (
            <div className="controls">
              <button className="btn" onClick={() => player.playAll(playable)}>Play folder</button>
              <button className="btn" onClick={() => player.playNext(playable)}>Play next</button>
              <button className="btn" onClick={() => player.enqueue(playable)}>Add to queue</button>
            </div>
          )}
        </div>
      </div>
      <dl className="kv">
        <dt>Folder</dt><dd>{folderName(data.folder, false)}</dd>
        <dt>Path</dt><dd>{folderName(data.folder, true)}</dd>
        <dt>Users</dt><dd>{count(data.users)}</dd>
        <dt>Files uploaded</dt><dd>{count(data.uploads)}</dd>
        <dt>Total size</dt><dd>{size(data.bytes)}</dd>
        <dt>Last user</dt><dd>{data.last_user ? <a href="#" onClick={(e) => { e.preventDefault(); select({ kind: 'user', value: data.last_user! }) }}>{name(data.last_user)}</a> : '—'}</dd>
        <dt>First upload</dt><dd>{date(data.first_upload)}</dd>
        <dt>Last upload</dt><dd>{date(data.last_upload)}</dd>
      </dl>
      <h2 style={{ marginTop: 18 }}>Downloaded by</h2>
      <table>
        <tbody>
          {data.by_user.map((u) => (
            <tr key={u.user} className="clickable" onClick={() => select({ kind: 'user', value: u.user })}>
              <td className="clip">{name(u.user)}</td><td className="num">{count(u.uploads)} {u.uploads === 1 ? 'file' : 'files'}</td><td className="num">{date(u.last_upload)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2 style={{ marginTop: 18 }}>Files</h2>
      <table>
        <tbody>
          {files.map((f, i) => (
            <FileMenu key={f.file_id} track={tracks[i]}>
              <tr className={`clickable ${player.current?.id === f.file_id ? 'now-playing' : ''}`}
                  onClick={() => select({ kind: 'file', value: f.file_id })}
                  onDoubleClick={() => isPlayable(f.name) && player.play([tracks[i]])}>
                <td className="clip">{f.name}</td>
                <td className="num">{count(f.uploads)}×</td>
                <td className="num">{size(f.size)}</td>
              </tr>
            </FileMenu>
          ))}
        </tbody>
      </table>
      <RemoveButton kind="folder" value={path} label={`folder "${folderName(path, false)}"`} onRemoved={onRemoved} />
    </>
  )
}

function FileInfo({ id, onRemoved, select }: { id: number; onRemoved: () => void; select: (s: Sel) => void }) {
  const name = useName()
  const player = usePlayer()
  const { data, error } = useData(() => api.file(id), [id])
  if (error) return <div className="empty">{error}</div>
  if (!data) return <div className="empty">Loading…</div>
  const t: Track = { id: data.file_id, name: data.name, folder: data.folder }
  return (
    <>
      {isAudio(data.name) ? (
        <div className="info-head" style={{ marginBottom: 14 }}>
          <Cover id={data.file_id} size={96} />
          <div style={{ minWidth: 0 }}>
            <h2 style={{ marginBottom: 6 }}>File</h2>
            <div className="controls">
              {isPlayable(data.name) ? (
                <>
                  <button className="btn" onClick={() => player.play([t])}>Play</button>
                  <button className="btn" onClick={() => player.playNext([t])}>Play next</button>
                  <button className="btn" onClick={() => player.enqueue([t])}>Add to queue</button>
                </>
              ) : <span className="sub">This format can't be played in the browser.</span>}
              <button className="btn" onClick={() => player.showInfo(t)}>File info</button>
            </div>
          </div>
        </div>
      ) : <h2>File</h2>}
      <dl className="kv">
        <dt>File</dt><dd>{data.name}</dd>
        <dt>Folder</dt><dd><a href="#" onClick={(e) => { e.preventDefault(); select({ kind: 'folder', value: data.folder }) }}>{folderName(data.folder, true)}</a></dd>
        <dt>Size</dt><dd>{size(data.size)}</dd>
        <dt>Times uploaded</dt><dd>{count(data.uploads)}</dd>
        <dt>Different users</dt><dd>{count(data.users)}</dd>
        <dt>Last user</dt><dd>{data.last_user ? <a href="#" onClick={(e) => { e.preventDefault(); select({ kind: 'user', value: data.last_user! }) }}>{name(data.last_user)}</a> : '—'}</dd>
        <dt>Last upload</dt><dd>{date(data.last_upload)}</dd>
      </dl>
      <h2 style={{ marginTop: 18 }}>History</h2>
      <table>
        <tbody>
          {data.history.map((h, i) => (
            <tr key={i} title={h.reason ?? undefined} className="clickable" onClick={() => select({ kind: 'user', value: h.user })}>
              <td className="clip">{name(h.user)}</td>
              <td><Status status={h.status} /></td>
              <td className="num">{date(h.started_at ?? h.requested_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <RemoveButton kind="file" value={data.vpath} label={`file "${data.name}"`} onRemoved={onRemoved} />
    </>
  )
}

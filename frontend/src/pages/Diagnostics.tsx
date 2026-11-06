import { api } from '../api'
import { Status } from '../components'
import { ago, count, date, folderName } from '../format'
import { navigate, useData, useName, useSettings } from '../state'

/** The "under the hood" numbers: failures, uploads without sizes, log status. Lives on Settings. */
export default function Diagnostics() {
  const name = useName()
  const { fullPath } = useSettings()
  const { data: d, error } = useData(api.diagnostics, [], 30000)
  if (error) return <div className="card"><h2>Diagnostics</h2><div className="sub">Couldn't load: {error}</div></div>
  if (!d) return <div className="card"><h2>Diagnostics</h2><div className="sub">Loading…</div></div>
  const { unknown_file: uf, not_found: nf } = d.unknown_sizes

  return (
    <div className="card">
      <h2>Diagnostics</h2>

      <div className="tiles" style={{ marginBottom: 20 }}>
        <Tile label="Failed" value={count(d.counts.failed)} note="aborted, errors, timeouts" />
        <Tile label="Cancelled" value={count(d.counts.cancelled)} note="by the downloader" />
        <Tile label="Denied" value={count(d.counts.denied)} note="queue limit etc." />
        <Tile label="Still queued" value={count(d.counts.queued)} />
      </div>

      <h3 className="section">Uploads without a known size</h3>
      <dl className="kv">
        <dt>File not known</dt>
        <dd>
          {count(uf.uploads)} uploads
          <div className="sub">
            These started before their request was in the log (e.g. the user queued them before tracking began),
            so the file can't be identified. They still count toward each user's uploads.
          </div>
          {uf.by_user.length > 0 && (
            <div className="sub" style={{ marginTop: 4 }}>
              {uf.by_user.slice(0, 8).map((u, i) => (
                <span key={u.user}>{i ? ', ' : ''}<a href="#" onClick={(e) => { e.preventDefault(); navigate('users', { user: u.user }) }}>{name(u.user)}</a> ({u.uploads})</span>
              ))}
              {uf.by_user.length > 8 && <span> and {uf.by_user.length - 8} more</span>}
            </div>
          )}
        </dd>
        <dt>Not found on shares</dt>
        <dd>
          {count(nf.uploads)} uploads of {count(nf.files)} files
          <div className="sub">
            {nf.files
              ? 'The file is known, but it wasn\'t found in the mounted shares (moved, renamed, deleted, or a share isn\'t mounted).'
              : d.shares.length ? 'Every known file was found on the shares.' : 'No shares are mounted, so sizes can\'t be looked up.'}
          </div>
        </dd>
      </dl>
      {nf.examples.length > 0 && (
        <table style={{ marginTop: 8 }}>
          <tbody>
            {nf.examples.map((f) => (
              <tr key={f.file_id} className="clickable" onClick={() => navigate('users', { file: String(f.file_id) })}>
                <td className="clip" title={folderName(f.folder, true)}>{f.name}</td>
                <td className="num">{count(f.uploads)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {d.by_reason.length > 0 && (
        <>
          <h3 className="section">Why uploads didn't finish</h3>
          <table>
            <tbody>
              {d.by_reason.map((r, i) => (
                <tr key={i}>
                  <td style={{ width: 110 }}><Status status={r.status} /></td>
                  <td className="clip" title={r.reason}>{r.reason || '—'}</td>
                  <td className="num">{count(r.count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {d.problem_files.length > 0 && (
        <>
          <h3 className="section">Files that fail most</h3>
          <table>
            <thead><tr><th>File</th><th className="num">Failures</th><th className="num">Users</th></tr></thead>
            <tbody>
              {d.problem_files.map((f) => (
                <tr key={f.file_id} className="clickable" onClick={() => navigate('users', { file: String(f.file_id) })}>
                  <td className="clip" title={folderName(f.folder, true)}>{f.name} <span className="sub">· {folderName(f.folder, fullPath)}</span></td>
                  <td className="num">{count(f.failures)}</td>
                  <td className="num">{count(f.users)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h3 className="section">Log</h3>
      <dl className="kv">
        <dt>File</dt><dd>{d.log.path}{!d.log.exists && <span className="sub"> (not found)</span>}</dd>
        <dt>Last new line</dt><dd>{d.log.last_line_at ? `${ago(d.log.last_line_at)} (${date(d.log.last_line_at)})` : '—'}</dd>
        <dt>History covers</dt><dd>{d.log.first_seen ? `${date(d.log.first_seen)} – ${date(d.log.last_seen)}` : '—'}</dd>
        <dt>Shares</dt><dd>{d.shares.length ? d.shares.join(', ') : 'none'}</dd>
      </dl>
    </div>
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

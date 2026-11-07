import { useState } from 'react'
import { api } from '../api'
import { ago } from '../format'
import { useData } from '../state'

const fromUnix = (t: number) => {
  const d = new Date(t * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

const STATUS: Record<string, string> = { sent: 'Scrobbled', pending: 'Waiting to send', skipped: 'Skipped' }

export default function LastFM() {
  const { data: s, error, reload } = useData(api.lastfm, [], 10000)
  const [busy, setBusy] = useState(false)
  const failed = new URLSearchParams(window.location.hash.split('?')[1] || '').has('lastfm_error')

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try { await fn() } finally { setBusy(false); reload() }
  }

  return (
    <div className="card">
      <h2>Last.fm</h2>
      {error && <div className="sub">Couldn't load: {error}</div>}
      {s && !s.configured && (
        <div className="sub">
          To scrobble what you play here, create a free API account at{' '}
          <a href="https://www.last.fm/api/account/create" target="_blank" rel="noreferrer">last.fm/api/account/create</a>,
          then set <code>SOULTRACKER_LASTFM_KEY</code> and <code>SOULTRACKER_LASTFM_SECRET</code> on the container and restart it.
        </div>
      )}
      {s && s.configured && !s.connected && (
        <>
          <div className="sub" style={{ marginBottom: 12 }}>
            Connect your Last.fm account to scrobble songs played in SoulTracker.
            {failed && <span style={{ color: 'var(--critical)' }}> Connecting didn't work, try again.</span>}
          </div>
          <a className="btn" href="/api/lastfm/connect" style={{ textDecoration: 'none' }}>Connect Last.fm</a>
        </>
      )}
      {s && s.connected && (
        <>
          <dl className="kv" style={{ alignItems: 'center', rowGap: 12 }}>
            <dt>Account</dt>
            <dd>
              <a href={`https://www.last.fm/user/${encodeURIComponent(s.user ?? '')}`} target="_blank" rel="noreferrer">{s.user}</a>
              <button className="btn" style={{ marginLeft: 12 }} disabled={busy}
                      onClick={() => { if (confirm('Disconnect Last.fm? Scrobbling stops until you connect again.')) act(api.lastfmDisconnect) }}>
                Disconnect
              </button>
            </dd>
            <dt>Scrobbling</dt>
            <dd>
              <label className="check">
                <input type="checkbox" checked={s.enabled} disabled={busy} onChange={(e) => act(() => api.lastfmEnabled(e.target.checked))} />
                Scrobble songs played here
              </label>
              <div className="sub">A song scrobbles once you've listened to half of it or 4 minutes (songs over 30 seconds).</div>
            </dd>
            <dt>Now playing</dt>
            <dd>
              {!s.now_playing ? <span className="sub">Nothing sent yet. It's sent as soon as a song starts playing here.</span>
                : s.now_playing.ok
                  ? <span>Sent for {s.now_playing.title} · {s.now_playing.artist} <span className="sub">({ago(fromUnix(s.now_playing.at))})</span></span>
                  : <span style={{ color: 'var(--critical)' }}>Failed{s.now_playing.title ? ` for ${s.now_playing.title}` : ''}: {s.now_playing.error} <span className="sub">({ago(fromUnix(s.now_playing.at))})</span></span>}
            </dd>
            <dt>Sent</dt>
            <dd>{s.sent.toLocaleString()}{s.pending > 0 && <span className="sub"> · {s.pending} waiting to send (retried every few minutes)</span>}</dd>
          </dl>
          {s.recent.length > 0 && (
            <>
              <h3 className="section">Recent scrobbles</h3>
              <table>
                <tbody>
                  {s.recent.map((r, i) => (
                    <tr key={i} title={r.error ?? undefined}>
                      <td className="clip">{r.title ?? '—'} <span className="sub">· {r.artist ?? '—'}</span></td>
                      <td className="num sub">{STATUS[r.status] ?? r.status}</td>
                      <td className="num sub">{ago(fromUnix(r.played_at))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
    </div>
  )
}

import { ago } from './format'
import Charts from './pages/Charts'
import Dashboard from './pages/Dashboard'
import Settings from './pages/Settings'
import Users from './pages/Users'
import { PlayerBar, usePlayer } from './player'
import { useSources } from './components'
import { href, useRoute } from './state'

const PAGES = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'users', label: 'Users & files' },
  { id: 'charts', label: 'Charts' },
  { id: 'settings', label: 'Settings' },
]

// The client writes constantly while it's running, so a quiet log usually means trouble.
// Minutes without new log lines before a client counts as quiet. Nicotine+ buffers its log and,
// when busy, only writes it out every several minutes, so it gets more slack.
const QUIET_MINUTES: Record<string, number> = { soulseekqt: 15, nicotine: 60 }

/** One line per client: is its log there, and is it still being written to? */
function Health() {
  const { data, error } = useSources()
  if (error) return <div className="health"><span className="dot" style={{ background: 'var(--critical)' }} />Can't reach the server</div>
  if (!data) return null
  if (data.length === 0) return <div className="health"><span className="dot" style={{ background: 'var(--critical)' }} />No client logs found</div>
  return (
    <div>
      {data.map((src) => {
        const last = src.last_line_at
        const quiet = !last || Date.now() - new Date(last.replace(' ', 'T')).getTime() > (QUIET_MINUTES[src.client] ?? 15) * 60000
        let color = 'var(--good)'
        let text = `active, ${ago(last)}`
        if (src.importing) { color = 'var(--accent)'; text = `importing history ${Math.floor((100 * src.importing.done) / Math.max(1, src.importing.total))}%` }
        else if (!src.exists) { color = 'var(--critical)'; text = 'log not found' }
        else if (quiet) { color = 'var(--warning)'; text = `quiet ${last ? ago(last) : 'so far'}` }
        return (
          <div key={src.client} className="health" title={src.path}>
            <span className="dot" style={{ background: color }} />
            <span>{data.length > 1 ? `${src.label}: ${text}` : `Log ${text}`}</span>
          </div>
        )
      })}
    </div>
  )
}

export default function App() {
  const route = useRoute()
  const page = PAGES.some((p) => p.id === route.page) ? route.page : 'dashboard'
  const player = usePlayer()
  return (
    <div className={`app ${player.current ? 'has-player' : ''}`}>
      <nav className="nav">
        <div className="brand"><img className="logo-light" src="/logo_dark.png" alt="" /><img className="logo-dark" src="/logo_light.png" alt="" />SoulTracker</div>
        {PAGES.map((p) => (
          <a key={p.id} href={href(p.id)} className={page === p.id ? 'active' : ''}>{p.label}</a>
        ))}
        <div className="spacer" />
        <Health />
      </nav>
      <main className="main">
        {page === 'dashboard' && <Dashboard />}
        {page === 'users' && <Users params={route.params} />}
        {page === 'charts' && <Charts />}
        {page === 'settings' && <Settings />}
      </main>
      <PlayerBar />
    </div>
  )
}

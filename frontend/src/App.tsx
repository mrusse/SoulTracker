import { api } from './api'
import { ago } from './format'
import Charts from './pages/Charts'
import Dashboard from './pages/Dashboard'
import Settings from './pages/Settings'
import Users from './pages/Users'
import { href, useData, useRoute } from './state'

const PAGES = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'users', label: 'Users & files' },
  { id: 'charts', label: 'Charts' },
  { id: 'settings', label: 'Settings' },
]

// The client writes constantly while it's running, so a quiet log usually means trouble.
const QUIET_MINUTES = 15

function Health() {
  const { data, error } = useData(api.summary, [], 30000)
  if (error) return <div className="health"><span className="dot" style={{ background: 'var(--critical)' }} />Can't reach the server</div>
  if (!data) return null
  const last = data.log.last_line_at
  const quiet = !last || Date.now() - new Date(last.replace(' ', 'T')).getTime() > QUIET_MINUTES * 60000
  let color = 'var(--good)'
  let text = `Log active, ${ago(last)}`
  if (!data.log.exists) { color = 'var(--critical)'; text = 'Log file not found' }
  else if (quiet) { color = 'var(--warning)'; text = `No new log lines ${last ? ago(last) : 'yet'}` }
  return (
    <div className="health" title={data.log.path}>
      <span className="dot" style={{ background: color }} />
      <span>{text}</span>
    </div>
  )
}

export default function App() {
  const route = useRoute()
  const page = PAGES.some((p) => p.id === route.page) ? route.page : 'dashboard'
  return (
    <div className="app">
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
    </div>
  )
}

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import type { Client } from './api'
import { pseudonym } from './format'

export type Theme = 'system' | 'light' | 'dark'
export type FileSort = 'name' | 'time'

interface Settings {
  privacy: boolean
  fullPath: boolean
  fileSort: FileSort
  userSort: string      // Users & files: how the user list is sorted
  client: 'all' | Client  // which client's uploads to show
  expandAll: boolean    // Users & files: expand every user
  queueHistory: boolean // player queue: show songs already played
  theme: Theme
  set: (patch: Partial<Omit<Settings, 'set'>>) => void
}

const KEY = 'soultracker.settings'
const Ctx = createContext<Settings | null>(null)

function load(): Partial<Settings> {
  try {
    return JSON.parse(localStorage.getItem(KEY) || '{}')
  } catch {
    return {}
  }
}

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [s, setS] = useState(() => ({ privacy: false, fullPath: false, fileSort: 'name' as FileSort, userSort: 'size', expandAll: false, queueHistory: false, client: 'all' as 'all' | Client, theme: 'system' as Theme, ...load() }))
  const set = useCallback((patch: Partial<Omit<Settings, 'set'>>) => {
    setS((prev) => {
      const next = { ...prev, ...patch }
      try {
        localStorage.setItem(KEY, JSON.stringify(next))
      } catch {
        /* storage unavailable: settings just won't persist */
      }
      return next
    })
  }, [])
  useEffect(() => {
    if (s.theme === 'system') document.documentElement.removeAttribute('data-theme')
    else document.documentElement.setAttribute('data-theme', s.theme)
  }, [s.theme])
  return <Ctx.Provider value={{ ...s, set }}>{children}</Ctx.Provider>
}

export function useSettings(): Settings {
  const c = useContext(Ctx)
  if (!c) throw new Error('SettingsProvider missing')
  return c
}

/** Display a username, scrambled when privacy mode is on. */
export function useName(): (name: string | null | undefined) => string {
  const { privacy } = useSettings()
  return (name) => (name == null ? '—' : privacy ? pseudonym(name) : name)
}

/** Fetch data, refetch when deps change and every `refreshMs` (keeps the old data while loading). */
export function useData<T>(fn: () => Promise<T>, deps: unknown[], refreshMs = 0) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let alive = true
    fn().then((d) => { if (alive) { setData(d); setError(null) } })
      .catch((e) => { if (alive) setError(String(e.message || e)) })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])
  useEffect(() => {
    if (!refreshMs) return
    const id = setInterval(() => setTick((t) => t + 1), refreshMs)
    return () => clearInterval(id)
  }, [refreshMs])
  return { data, error, reload: () => setTick((t) => t + 1) }
}

// ---- tiny hash router: #/page?key=value ----

export interface Route { page: string; params: URLSearchParams }

function parseHash(): Route {
  const h = window.location.hash.replace(/^#\/?/, '')
  const [page, qs] = h.split('?')
  return { page: page || 'dashboard', params: new URLSearchParams(qs || '') }
}

export function useRoute(): Route {
  const [route, setRoute] = useState(parseHash)
  useEffect(() => {
    const on = () => setRoute(parseHash())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return route
}

export function href(page: string, params: Record<string, string> = {}): string {
  const qs = new URLSearchParams(params).toString()
  return `#/${page}${qs ? `?${qs}` : ''}`
}

export function navigate(page: string, params: Record<string, string> = {}) {
  window.location.hash = href(page, params)
}

/** The client filter as an API argument: undefined means all clients. */
export function useClient(): Client | undefined {
  const { client } = useSettings()
  return client === 'all' ? undefined : client
}

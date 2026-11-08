import * as ContextMenu from '@radix-ui/react-context-menu'
import * as Dialog from '@radix-ui/react-dialog'
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, type TrackMeta } from './api'
import { bytes, folderName } from './format'
import { navigate, SHUFFLE_DEFAULTS, useClient, useSettings, type ShuffleSettings } from './state'
import { Seg } from './components'

// user: whose branch it was played from (the player bar links back there); shuffle: picked by shuffle
export interface Track { id: number; name: string; folder: string | null; user?: string | null; shuffle?: boolean }

// Formats browsers play natively; anything else isn't offered (matches the backend's list).
const PLAYABLE = ['.mp3', '.flac', '.m4a', '.mp4', '.aac', '.ogg', '.oga', '.opus', '.wav']
export const isPlayable = (name: string | null | undefined) =>
  !!name && PLAYABLE.some((ext) => name.toLowerCase().endsWith(ext))
// Audio formats browsers can't play: shown with a "can't be played" note instead of player options.
const OTHER_AUDIO = ['.wma', '.ape', '.aif', '.aiff', '.alac', '.wv', '.dsf', '.dff', '.mpc', '.tta', '.mka', '.ac3', '.dts', '.amr', '.mid', '.midi', '.au']
/** Is this a music file at all (playable or not)? Non-audio files (ROMs, images...) get no player UI. */
export const isAudio = (name: string | null | undefined) =>
  isPlayable(name) || (!!name && OTHER_AUDIO.some((ext) => name.toLowerCase().endsWith(ext)))

const streamUrl = (id: number) => `/api/file/${id}/stream`
export const coverUrl = (id: number) => `/api/file/${id}/cover`
const KEY = 'soultracker.player'
const SEEN_KEY = 'soultracker.shuffleSeen'   // recently played file ids, for "no repeats"
const SEEN_MAX = 500
const SHUFFLE_AHEAD = 3                       // keep this many songs lined up while shuffling

interface Player {
  queue: Track[]
  index: number
  current: Track | null
  meta: TrackMeta | null
  playing: boolean
  time: number
  duration: number
  volume: number
  error: string | null
  play: (tracks: Track[]) => void          // play now; the rest of the queue continues after
  playAll: (tracks: Track[], start?: number) => void  // replace the queue
  playNext: (tracks: Track[]) => void
  enqueue: (tracks: Track[]) => void
  jump: (i: number) => void
  next: () => void
  prev: () => void
  toggle: () => void
  seek: (t: number) => void
  setVolume: (v: number) => void
  remove: (i: number) => void
  clear: () => void
  clearHistory: () => void           // drop the songs already played
  shuffleQueue: () => void           // shuffle the songs you queued (shuffle picks stay after them)
  shuffleEmpty: boolean              // shuffle found nothing to play with its settings
  showInfo: (t: Track) => void
}

const Ctx = createContext<Player | null>(null)
export function usePlayer(): Player {
  const c = useContext(Ctx)
  if (!c) throw new Error('PlayerProvider missing')
  return c
}

function loadSaved(): { queue: Track[]; index: number; volume: number; time: number } {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) || '{}')
    return { queue: s.queue ?? [], index: s.index ?? 0, volume: s.volume ?? 1, time: s.time ?? 0 }
  } catch {
    return { queue: [], index: 0, volume: 1, time: 0 }
  }
}

const metaCache = new Map<number, Promise<TrackMeta | null>>()
export function trackMeta(id: number): Promise<TrackMeta | null> {
  if (!metaCache.has(id)) metaCache.set(id, api.meta(id).catch(() => null))
  return metaCache.get(id)!
}

export function PlayerProvider({ children }: { children: ReactNode }) {
  const saved = useMemo(loadSaved, [])
  const audio = useRef<HTMLAudioElement>(null)
  const [queue, setQueue] = useState<Track[]>(saved.queue)
  const [index, setIndex] = useState(Math.min(saved.index, Math.max(0, saved.queue.length - 1)))
  const [playing, setPlaying] = useState(false)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolumeState] = useState(saved.volume)
  const [error, setError] = useState<string | null>(null)
  const [meta, setMeta] = useState<TrackMeta | null>(null)
  const [info, setInfo] = useState<Track | null>(null)
  const autoplay = useRef(false)          // start playing when the source changes
  // Bumped whenever the player moves to a different queue entry. Loading is keyed on this rather
  // than on the file, so the same song queued twice in a row still restarts on "next".
  const [loadToken, setLoadToken] = useState(0)
  const reload = useCallback(() => setLoadToken((t) => t + 1), [])
  // One "play" = one load of a queue entry. Last.fm needs to know how long it was actually listened to.
  const play_ = useRef({ id: '', startedAt: 0, listened: 0, lastT: 0, scrobbled: false })
  const resumeAt = useRef(saved.time)     // restore position after a page reload
  const { shuffle: sh } = useSettings()
  const seen = useRef<number[]>((() => { try { return JSON.parse(localStorage.getItem(SEEN_KEY) || '[]') } catch { return [] } })())

  const current = queue[index] ?? null

  // Load the current queue entry into the audio element.
  useEffect(() => {
    const el = audio.current
    if (!el) return
    setError(null)
    if (!current) { el.removeAttribute('src'); el.load(); return }
    el.src = streamUrl(current.id)  // also restarts when it's the same file as before
    play_.current = { id: `${current.id}-${loadToken}-${Date.now()}`, startedAt: 0, listened: 0, lastT: 0, scrobbled: false }
    if (autoplay.current) el.play().catch(() => {})
    seen.current = [...seen.current.filter((x) => x !== current.id), current.id].slice(-SEEN_MAX)
    try { localStorage.setItem(SEEN_KEY, JSON.stringify(seen.current)) } catch { /* storage unavailable */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadToken])

  // Tags for the bar and media controls.
  useEffect(() => {
    if (!current) { setMeta(null); return }
    let alive = true
    trackMeta(current.id).then((m) => { if (alive) setMeta(m) })
    return () => { alive = false }
  }, [current?.id])

  // Remember the queue (and roughly where we were) across reloads.
  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify({ queue, index, volume, time: audio.current?.currentTime ?? 0 }))
    } catch { /* storage unavailable */ }
  }, [queue, index, volume, Math.floor(time / 5)])

  useEffect(() => { if (audio.current) audio.current.volume = volume }, [volume])

  const go = useCallback((i: number) => { autoplay.current = true; setIndex(i); reload() }, [reload])
  const next = useCallback(() => {
    if (index + 1 < queue.length) go(index + 1)
    else { audio.current?.pause(); audio.current && (audio.current.currentTime = 0) }
  }, [index, queue.length, go])
  const prev = useCallback(() => {
    const el = audio.current
    if (el && el.currentTime > 3) el.currentTime = 0
    else if (index > 0) go(index - 1)
  }, [index, go])
  const toggle = useCallback(() => {
    const el = audio.current
    if (!el || !current) return
    if (el.paused) el.play().catch(() => {})
    else el.pause()
  }, [current])

  const play = useCallback((tracks: Track[]) => {
    const ts = tracks.filter((t) => isPlayable(t.name))
    if (!ts.length) return
    if (!queue.length) { setQueue(ts); go(0); return }
    const at = index + 1
    setQueue((q) => [...q.slice(0, at), ...ts, ...q.slice(at)])
    go(at)
  }, [queue.length, index, go])

  const playAll = useCallback((tracks: Track[], start = 0) => {
    const ts = tracks.filter((t) => isPlayable(t.name))
    if (!ts.length) return
    const first = tracks[start]
    setQueue(ts)
    const i = Math.max(0, ts.indexOf(first))
    go(i)
  }, [go])

  const playNext = useCallback((tracks: Track[]) => {
    const ts = tracks.filter((t) => isPlayable(t.name))
    if (!ts.length) return
    if (!queue.length) { setQueue(ts); setIndex(0); reload(); return }
    setQueue((q) => [...q.slice(0, index + 1), ...ts, ...q.slice(index + 1)])
  }, [queue.length, index, reload])

  const enqueue = useCallback((tracks: Track[]) => {
    const ts = tracks.filter((t) => isPlayable(t.name))
    if (!ts.length) return
    if (!queue.length) { setQueue(ts); setIndex(0); reload(); return }
    // your songs go before anything shuffle has lined up
    setQueue((q) => {
      let at = q.findIndex((t, i) => i > index && t.shuffle)
      if (at < 0) at = q.length
      return [...q.slice(0, at), ...ts, ...q.slice(at)]
    })
  }, [queue.length, index, reload])

  const remove = useCallback((i: number) => {
    setQueue((q) => q.filter((_, j) => j !== i))
    if (i < index) setIndex(index - 1)
    else if (i === index) {
      // the next entry slides into this slot (or step back if this was the last one)
      autoplay.current = playing
      if (index >= queue.length - 1) setIndex(Math.max(0, index - 1))
      reload()
    }
  }, [index, playing, queue.length, reload])

  // Clear everything except the song that's playing; it carries on uninterrupted.
  // Remove what's been played; the current song stays first and keeps playing (no reload).
  const clearHistory = useCallback(() => {
    setQueue((q) => q.slice(index))
    setIndex(0)
  }, [index])

  const clear = useCallback(() => {
    setQueue(current ? [current] : [])
    setIndex(0)
  }, [current])
  const shuffleQueue = useCallback(() => {
    setQueue((q) => {
      const ahead = q.slice(index + 1)
      const mine = ahead.filter((t) => !t.shuffle)
      for (let i = mine.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [mine[i], mine[j]] = [mine[j], mine[i]]
      }
      return [...q.slice(0, index + 1), ...mine, ...ahead.filter((t) => t.shuffle)]
    })
  }, [index])

  // ---- shuffle: keep a few random songs lined up after yours ----
  const [shuffleBusy, setShuffleBusy] = useState(false)
  const [shuffleEmpty, setShuffleEmpty] = useState(false)
  const shuffleGen = useRef(0)            // bumped when the settings change: ignore answers for the old ones
  const pickKey = `${sh.on}|${sh.mode}|${sh.users}|${sh.window}`
  const lastPickKey = useRef(pickKey)
  useEffect(() => {
    if (lastPickKey.current === pickKey) return
    lastPickKey.current = pickKey
    shuffleGen.current++
    setShuffleEmpty(false)
    // settings changed or shuffle turned off: drop the picks that haven't played yet
    setQueue((q) => q.filter((t, i) => i <= index || !t.shuffle))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickKey])
  useEffect(() => { setShuffleEmpty(false) }, [index, sh.noRepeats])
  useEffect(() => {
    if (!sh.on || shuffleBusy || shuffleEmpty) return
    if (queue.length - index - 1 >= SHUFFLE_AHEAD) return   // your own songs (or enough picks) still to come
    const gen = shuffleGen.current
    const ask = (noRepeats: boolean) => api.shuffle({
      mode: sh.mode, users: sh.users, window: sh.window, count: SHUFFLE_AHEAD + 2,
      exclude: [...new Set([...queue.slice(index).map((t) => t.id), ...(noRepeats ? seen.current : [])])],
    })
    setShuffleBusy(true)
    ;(async () => {
      let ts = await ask(sh.noRepeats)
      if (!ts.length && sh.noRepeats && seen.current.length) {
        // everything's been played recently: start the "no repeats" memory over
        seen.current = []
        try { localStorage.removeItem(SEEN_KEY) } catch { /* storage unavailable */ }
        ts = await ask(true)
      }
      if (gen !== shuffleGen.current) return
      if (!ts.length) { setShuffleEmpty(true); return }
      const picks = ts.map((t) => ({ ...t, shuffle: true }))
      if (!queueRef.current.length) { setQueue(picks); setIndex(0); reload(); return }
      setQueue((q) => [...q, ...picks])
    })().catch(() => setShuffleEmpty(true)).finally(() => setShuffleBusy(false))
  }, [sh.on, sh.mode, sh.users, sh.window, sh.noRepeats, queue, index, shuffleBusy, shuffleEmpty, reload])

  const seek = useCallback((t: number) => { if (audio.current) audio.current.currentTime = t }, [])
  const setVolume = useCallback((v: number) => setVolumeState(Math.max(0, Math.min(1, v))), [])

  // Tell the server what's playing (Last.fm "now playing", the Discord helper).
  const report = useCallback((state: 'playing' | 'paused' | 'stopped') => {
    const el = audio.current
    const cur = queueRef.current[indexRef.current]
    if (!cur || state === 'stopped') { api.nowPlaying({ file_id: null, state: 'stopped' }).catch(() => {}); return }
    api.nowPlaying({ file_id: cur.id, play_id: play_.current.id, state,
      position: el?.currentTime ?? 0, duration: el?.duration || 0 }).catch(() => {})
  }, [])
  const queueRef = useRef(queue); queueRef.current = queue
  const indexRef = useRef(index); indexRef.current = index

  useEffect(() => { if (!current) report('stopped') }, [current, report])
  useEffect(() => {                                   // heartbeat while playing
    if (!playing) return
    const id = setInterval(() => report('playing'), 30000)
    return () => clearInterval(id)
  }, [playing, report])
  useEffect(() => {                                   // closing the tab stops it
    const bye = () => navigator.sendBeacon?.('/api/nowplaying', new Blob([JSON.stringify({ state: 'stopped' })], { type: 'application/json' }))
    window.addEventListener('pagehide', bye)
    return () => window.removeEventListener('pagehide', bye)
  }, [])

  // Count real listening time (seeking doesn't count) and scrobble at half the track or 4 minutes.
  const onTime = (el: HTMLAudioElement) => {
    const pl = play_.current
    const d = el.currentTime - pl.lastT
    if (!el.paused && d > 0 && d < 2) pl.listened += d
    pl.lastT = el.currentTime
    const dur = el.duration
    if (!pl.scrobbled && cur() && isFinite(dur) && dur > 30 && pl.listened >= Math.min(dur / 2, 240)) {
      pl.scrobbled = true
      api.scrobble(cur()!.id, Math.floor(pl.startedAt || Date.now() / 1000)).catch(() => {})
    }
  }
  const cur = () => queueRef.current[indexRef.current]

  // Show the track in the browser tab only while it's actually playing.
  useEffect(() => {
    if (!current || !playing) { document.title = 'SoulTracker'; return }
    const title = meta?.title || current.name.replace(/\.[^.]+$/, '')
    document.title = meta?.artist ? `${title} · ${meta.artist}` : title
  }, [current, meta, playing])

  // OS media controls (keyboard media keys, lock screen, notification).
  useEffect(() => {
    if (!('mediaSession' in navigator)) return
    const ms = navigator.mediaSession
    if (!current) { ms.metadata = null; return }
    ms.metadata = new MediaMetadata({
      title: meta?.title || current.name,
      artist: meta?.artist || '',
      album: meta?.album || folderName(current.folder, false),
      artwork: [{ src: coverUrl(current.id) }],
    })
    ms.setActionHandler('play', () => audio.current?.play())
    ms.setActionHandler('pause', () => audio.current?.pause())
    ms.setActionHandler('nexttrack', next)
    ms.setActionHandler('previoustrack', prev)
    ms.setActionHandler('seekto', (d) => { if (d.seekTime != null) seek(d.seekTime) })
  }, [current, meta, next, prev, seek])

  const value: Player = {
    queue, index, current, meta, playing, time, duration, volume, error,
    play, playAll, playNext, enqueue, jump: go, next, prev, toggle, seek, setVolume, remove, clear, clearHistory,
    shuffleQueue, shuffleEmpty,
    showInfo: setInfo,
  }

  return (
    <Ctx.Provider value={value}>
      {children}
      <audio
        ref={audio}
        preload="metadata"
        onPlay={() => {
          setPlaying(true)
          if (!play_.current.startedAt) play_.current.startedAt = Date.now() / 1000
          report('playing')
        }}
        onPause={(e) => { setPlaying(false); if (!e.currentTarget.ended) report('paused') }}
        onSeeked={(e) => { play_.current.lastT = e.currentTarget.currentTime; report(e.currentTarget.paused ? 'paused' : 'playing') }}
        onTimeUpdate={(e) => { setTime(e.currentTarget.currentTime); onTime(e.currentTarget) }}
        onDurationChange={(e) => setDuration(e.currentTarget.duration || 0)}
        onLoadedMetadata={(e) => {
          if (resumeAt.current) { e.currentTarget.currentTime = resumeAt.current; resumeAt.current = 0 }
        }}
        onEnded={next}
        onError={() => {
          if (!current) return
          setError("Couldn't play this file")
          if (current.shuffle) next()   // a shuffle pick that's gone from the shares: just move on
        }}
      />
      <FileInfoDialog track={info} onClose={() => setInfo(null)} />
    </Ctx.Provider>
  )
}

// ---------- icons ----------

const I = ({ d, size = 18 }: { d: string; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d={d} /></svg>
)
const PLAY = 'M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z'
const PAUSE = 'M7 5h3.5v14H7zM13.5 5H17v14h-3.5z'
const PREV = 'M6 5h2v14H6zM20 6.2v11.6a1 1 0 0 1-1.55.83L10 13a1 1 0 0 1 0-1.66l8.45-5.97A1 1 0 0 1 20 6.2z'
const NEXT = 'M16 5h2v14h-2zM4 6.2v11.6a1 1 0 0 0 1.55.83L14 13a1 1 0 0 0 0-1.66L5.55 5.37A1 1 0 0 0 4 6.2z'
const SHUFFLE = 'M10.59 9.17 5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z'
const QUEUE = 'M4 6h12v2H4zm0 5h12v2H4zm0 5h8v2H4zm14-3v6l5-3z'
const VOL = 'M4 9h4l5-4v14l-5-4H4zm12.5 3a4.5 4.5 0 0 0-2.5-4v8a4.5 4.5 0 0 0 2.5-4z'
const MUTE = 'M4 9h4l5-4v14l-5-4H4zm12.3.3 1.4-1.4L20 10.2l2.3-2.3 1.4 1.4L21.4 11.6l2.3 2.3-1.4 1.4L20 13l-2.3 2.3-1.4-1.4 2.3-2.3z'
const CLOSE = 'M6.4 5 12 10.6 17.6 5 19 6.4 13.4 12l5.6 5.6-1.4 1.4-5.6-5.6L6.4 19 5 17.6l5.6-5.6L5 6.4z'

function clock(s: number): string {
  if (!isFinite(s) || s < 0) s = 0
  const m = Math.floor(s / 60)
  return `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}`
}

// ---------- cover image (falls back to a plain tile when there's no art) ----------

/** Cover art tile. With `onClick`, it's a button; `clickableOnlyWithArt` disables it when there's no art. */
export function Cover({ id, size, onClick, label, clickableOnlyWithArt = false }: {
  id: number | null; size: number; onClick?: () => void; label?: string; clickableOnlyWithArt?: boolean
}) {
  const [state, setState] = useState<'loading' | 'ok' | 'none'>('loading')
  useEffect(() => setState(id == null ? 'none' : 'loading'), [id])
  const img = id != null && state !== 'none' &&
    <img src={coverUrl(id)} alt="" onLoad={() => setState('ok')} onError={() => setState('none')} />
  const clickable = !!onClick && (!clickableOnlyWithArt || state === 'ok')
  if (!clickable) return <div className="cover" style={{ width: size, height: size }}>{img}</div>
  return (
    <button type="button" className="cover clickable-cover" style={{ width: size, height: size }}
            onClick={onClick} aria-label={label} title={label}>{img}</button>
  )
}

// ---------- the bar at the bottom of every page ----------

export function PlayerBar() {
  const p = usePlayer()
  const [panel, setPanel] = useState<'queue' | 'shuffle' | null>(null)
  const { shuffle: sh } = useSettings()
  const [lastVolume, setLastVolume] = useState(1)
  const client = useClient()
  if (!p.current) return null
  // Open the tree under the user it was played from, so their branch and the folder expand to it.
  // Songs queued without one (search results, older queues) use whoever downloaded it last.
  const whose = (fallback: () => Promise<string | null | undefined>) =>
    t.user ? Promise.resolve(t.user) : fallback().catch(() => null)
  const showFile = (id: number) => whose(() => api.file(id, client).then((d) => d?.last_user))
    .then((user) => navigate('users', { ...(user ? { user } : {}), file: String(id) }))
  const showFolder = (folder: string) => whose(() => api.folder(folder, client).then((d) => d?.last_user))
    .then((user) => navigate('users', { ...(user ? { user } : {}), folder }))
  const t = p.current
  const title = p.meta?.title || t.name
  const artist = p.meta?.artist
  const album = p.meta?.album || folderName(t.folder, false)
  const sub = [artist, album].filter(Boolean).join(' · ')
  const dur = p.duration || p.meta?.duration || 0

  return (
    <div className="player">
      <div className="player-track">
        <Cover id={t.id} size={56} onClick={() => p.showInfo(t)} label="File info" />
        <div className="player-text">
          <div className="player-title">
            <button className="player-link" title="Show in Users & files" onClick={() => showFile(t.id)}>{title}</button>
          </div>
          <div className="player-sub" title={sub}>
            {p.error ?? <>
              {artist}{artist && album ? ' · ' : ''}
              {album && (t.folder
                ? <button className="player-link" title="Show folder in Users & files" onClick={() => showFolder(t.folder!)}>{album}</button>
                : album)}
            </>}
          </div>
        </div>
      </div>

      <div className="player-center">
        <div className="player-buttons">
          <button className="icon-btn" onClick={p.prev} aria-label="Previous"><I d={PREV} /></button>
          <button className="icon-btn play" onClick={p.toggle} aria-label={p.playing ? 'Pause' : 'Play'}>
            <I d={p.playing ? PAUSE : PLAY} size={20} />
          </button>
          <button className="icon-btn" onClick={p.next} disabled={p.index + 1 >= p.queue.length} aria-label="Next"><I d={NEXT} /></button>
        </div>
        <div className="player-seek">
          <span>{clock(p.time)}</span>
          <input type="range" min={0} max={dur || 1} step={0.5} value={Math.min(p.time, dur || 1)}
                 onChange={(e) => p.seek(Number(e.target.value))} aria-label="Seek"
                 style={{ ['--pct' as string]: `${dur ? (100 * p.time) / dur : 0}%` }} />
          <span>{clock(dur)}</span>
        </div>
      </div>

      <div className="player-right">
        <button className="icon-btn" aria-label={p.volume ? 'Mute' : 'Unmute'}
                onClick={() => { if (p.volume) { setLastVolume(p.volume); p.setVolume(0) } else p.setVolume(lastVolume || 1) }}>
          <I d={p.volume ? VOL : MUTE} />
        </button>
        <input className="volume" type="range" min={0} max={1} step={0.01} value={p.volume}
               onChange={(e) => p.setVolume(Number(e.target.value))} aria-label="Volume"
               style={{ ['--pct' as string]: `${p.volume * 100}%` }} />
        <button className={`icon-btn ${panel === 'shuffle' || sh.on ? 'on' : ''}`} aria-label="Shuffle"
                title={sh.on ? 'Shuffle is on' : 'Shuffle'} onClick={() => setPanel(panel === 'shuffle' ? null : 'shuffle')}>
          <I d={SHUFFLE} />
          {sh.on && <span className="shuffle-dot" />}
        </button>
        <button className={`icon-btn ${panel === 'queue' ? 'on' : ''}`} onClick={() => setPanel(panel === 'queue' ? null : 'queue')} aria-label="Queue">
          <I d={QUEUE} />
        </button>
      </div>

      {panel === 'queue' && <QueuePanel onClose={() => setPanel(null)} />}
      {panel === 'shuffle' && <ShufflePanel onClose={() => setPanel(null)} />}
    </div>
  )
}

function QueuePanel({ onClose }: { onClose: () => void }) {
  const p = usePlayer()
  const { queueHistory: showHistory, set } = useSettings()
  const cur = useRef<HTMLDivElement>(null)
  useEffect(() => { cur.current?.scrollIntoView({ block: 'center' }) }, [showHistory])
  const played = p.index
  const upNext = Math.max(0, p.queue.length - p.index - 1)
  return (
    <div className="queue-panel">
      <div className="queue-head">
        <strong>Queue</strong>
        <span className="sub">{upNext} up next{played > 0 ? ` · ${played} played` : ''}</span>
        <span style={{ flex: 1 }} />
        <button className="icon-btn" onClick={onClose} aria-label="Close queue"><I d={CLOSE} size={16} /></button>
      </div>
      <div className="queue-actions">
        <button className={`btn small ${showHistory ? 'on' : ''}`} aria-pressed={showHistory} disabled={played === 0 && !showHistory}
                onClick={() => set({ queueHistory: !showHistory })} title="Show or hide songs you've already played">
          {showHistory ? 'Hide history' : 'Show history'}{played > 0 ? ` (${played})` : ''}
        </button>
        <button className="btn small" onClick={p.clearHistory} disabled={played === 0} title="Remove the songs you've already played">
          Clear history
        </button>
        <span style={{ flex: 1 }} />
        <button className="btn small" onClick={p.clear} disabled={p.queue.length <= 1} title="Remove everything except the current song">
          Clear all
        </button>
      </div>
      <div className="queue-list">
        {played > 0 && !showHistory && (
          <button className="queue-hidden" onClick={() => set({ queueHistory: true })}>
            {played} played {played === 1 ? 'song' : 'songs'} hidden
          </button>
        )}
        {p.queue.map((t, i) => i < p.index && !showHistory ? null : (
          <div key={`${t.id}-${i}`} ref={i === p.index ? cur : undefined}
               className={`queue-row ${i === p.index ? 'now' : ''} ${i < p.index ? 'past' : ''}`}
               onDoubleClick={() => p.jump(i)}>
            <span className="queue-num">{i === p.index && p.playing ? '▶' : i + 1}</span>
            <span className="queue-name" onClick={() => p.jump(i)} title={folderName(t.folder, true)}>
              {t.name}<span className="sub"> · {folderName(t.folder, false)}</span>
            </span>
            {t.shuffle && <span className="queue-shuffle" title="Picked by shuffle"><I d={SHUFFLE} size={13} /></span>}
            <button className="icon-btn small" onClick={() => p.remove(i)} aria-label="Remove from queue"><I d={CLOSE} size={14} /></button>
          </div>
        ))}
      </div>
    </div>
  )
}

const WINDOWS: [ShuffleSettings['window'], string][] = [['any', 'Any time'], ['24h', '24 hours'], ['7d', '7 days'], ['30d', '30 days']]

function ShufflePanel({ onClose }: { onClose: () => void }) {
  const p = usePlayer()
  const { shuffle: sh, set } = useSettings()
  const change = (patch: Partial<ShuffleSettings>) => set({ shuffle: { ...sh, ...patch } })
  const [users, setUsers] = useState(String(sh.users))
  useEffect(() => setUsers(String(sh.users)), [sh.users])
  const commitUsers = () => {
    const n = Math.round(Number(users))
    if (n >= 1 && n <= 1000) change({ users: n })
    else setUsers(String(sh.users))
  }
  const mine = p.queue.slice(p.index + 1).filter((t) => !t.shuffle).length
  return (
    <div className="queue-panel">
      <div className="queue-head">
        <strong>Shuffle</strong>
        <span className="sub">{sh.on ? 'On' : 'Off'}</span>
        <span style={{ flex: 1 }} />
        <button className="icon-btn" onClick={onClose} aria-label="Close shuffle"><I d={CLOSE} size={16} /></button>
      </div>
      <div className="shuffle-body">
        <div className="shuffle-row">
          <span>Shuffle</span>
          <Seg value={sh.on ? 'on' : 'off'} onChange={(v) => change({ on: v === 'on' })} options={[['off', 'Off'], ['on', 'On']]} />
        </div>
        <p className="sub shuffle-note">
          Plays random songs from Users &amp; files once your queue runs out. Songs you queue still play first.
        </p>
        <div className="shuffle-row">
          <span>Pick from</span>
          <Seg value={sh.mode} onChange={(v) => change({ mode: v })} options={[['recent', 'Recent users'], ['random', 'Totally random']]} />
        </div>
        {sh.mode === 'recent' && (
          <div className="shuffle-row">
            <span>Users</span>
            <label className="shuffle-users">
              The
              <input type="number" min={1} max={1000} value={users} onChange={(e) => setUsers(e.target.value)}
                     onBlur={commitUsers} onKeyDown={(e) => { if (e.key === 'Enter') commitUsers() }} aria-label="Number of recent users" />
              most recent
            </label>
          </div>
        )}
        <div className="shuffle-row">
          <span>Uploaded in</span>
          <Seg value={sh.window} onChange={(v) => change({ window: v })} options={WINDOWS} />
        </div>
        <div className="shuffle-row">
          <span>Repeats</span>
          <label className="check"><input type="checkbox" checked={sh.noRepeats} onChange={(e) => change({ noRepeats: e.target.checked })} />
            Skip songs played recently (last {SEEN_MAX})</label>
        </div>
        {sh.on && p.shuffleEmpty && <p className="shuffle-warn">No more songs to pick with these settings.</p>}
      </div>
      <div className="queue-actions">
        <button className="btn small" onClick={p.shuffleQueue} disabled={mine < 2} title="Put the songs you've queued in random order">
          Shuffle my queue{mine >= 2 ? ` (${mine})` : ''}
        </button>
        <span style={{ flex: 1 }} />
        <button className="btn small" onClick={() => change(SHUFFLE_DEFAULTS)}>Reset</button>
      </div>
    </div>
  )
}

// ---------- right-click menus ----------

/** Right-click (or long-press) menu for one file. */
export function FileMenu({ track, children }: { track: Track; children: ReactNode }) {
  const p = usePlayer()
  const ok = isPlayable(track.name)
  if (!isAudio(track.name)) return <>{children}</>
  return (
    <ContextMenu.Root>
      <ContextMenu.Trigger asChild>{children}</ContextMenu.Trigger>
      <ContextMenu.Portal>
        <ContextMenu.Content className="menu">
          <ContextMenu.Item className="menu-item" disabled={!ok} onSelect={() => p.play([track])}>Play</ContextMenu.Item>
          <ContextMenu.Item className="menu-item" disabled={!ok} onSelect={() => p.playNext([track])}>Play next</ContextMenu.Item>
          <ContextMenu.Item className="menu-item" disabled={!ok} onSelect={() => p.enqueue([track])}>Add to queue</ContextMenu.Item>
          {!ok && <div className="menu-note">This format can't be played in the browser</div>}
          <ContextMenu.Separator className="menu-sep" />
          <ContextMenu.Item className="menu-item" onSelect={() => p.showInfo(track)}>File info</ContextMenu.Item>
        </ContextMenu.Content>
      </ContextMenu.Portal>
    </ContextMenu.Root>
  )
}

/** Right-click menu for a folder (album): its playable files, in the order given. */
export function FolderMenu({ tracks, children }: { tracks: Track[]; children: ReactNode }) {
  const p = usePlayer()
  const playable = tracks.filter((t) => isPlayable(t.name))
  const none = playable.length === 0
  if (!tracks.some((t) => isAudio(t.name))) return <>{children}</>
  return (
    <ContextMenu.Root>
      <ContextMenu.Trigger asChild>{children}</ContextMenu.Trigger>
      <ContextMenu.Portal>
        <ContextMenu.Content className="menu">
          <ContextMenu.Item className="menu-item" disabled={none} onSelect={() => p.playAll(playable)}>Play folder</ContextMenu.Item>
          <ContextMenu.Item className="menu-item" disabled={none} onSelect={() => p.playNext(playable)}>Play folder next</ContextMenu.Item>
          <ContextMenu.Item className="menu-item" disabled={none} onSelect={() => p.enqueue(playable)}>Add folder to queue</ContextMenu.Item>
          <div className="menu-note">{none ? 'No playable files' : `${playable.length} ${playable.length === 1 ? 'track' : 'tracks'}`}</div>
        </ContextMenu.Content>
      </ContextMenu.Portal>
    </ContextMenu.Root>
  )
}

// ---------- file info ----------

function FileInfoDialog({ track, onClose }: { track: Track | null; onClose: () => void }) {
  const p = usePlayer()
  const [meta, setMeta] = useState<TrackMeta | null | undefined>(undefined)
  const [zoom, setZoom] = useState(false)
  useEffect(() => setZoom(false), [track])
  useEffect(() => {
    if (!track) return
    setMeta(undefined)
    let alive = true
    trackMeta(track.id).then((m) => { if (alive) setMeta(m) })
    return () => { alive = false }
  }, [track])
  const row = (label: string, value: ReactNode) => (value == null || value === '' ? null : <><dt>{label}</dt><dd>{value}</dd></>)
  const quality = meta && [
    meta.format,
    meta.bitrate ? `${Math.round(meta.bitrate / 1000)} kbps` : null,
    meta.sample_rate ? `${(meta.sample_rate / 1000).toFixed(1)} kHz` : null,
    meta.bits_per_sample ? `${meta.bits_per_sample}-bit` : null,
  ].filter(Boolean).join(' · ')
  return (
    <Dialog.Root open={!!track} onOpenChange={(o) => { if (!o) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog" aria-describedby={undefined}>
          {track && (
            <>
              <div className="info-head">
                <Cover id={track.id} size={140} onClick={() => setZoom(true)} label="Show cover art" clickableOnlyWithArt />
                <div style={{ minWidth: 0 }}>
                  <Dialog.Title className="info-title">{meta?.title || track.name}</Dialog.Title>
                  <div className="sub">{[meta?.artist, meta?.album].filter(Boolean).join(' · ') || folderName(track.folder, false)}</div>
                  <div className="controls" style={{ marginTop: 14 }}>
                    {isPlayable(track.name) && <button className="btn" onClick={() => { p.play([track]); onClose() }}>Play</button>}
                    <button className="btn" onClick={() => { onClose(); navigate('users', { file: String(track.id) }) }}>Show in Users &amp; files</button>
                  </div>
                </div>
              </div>
              {meta === undefined && <div className="sub" style={{ marginTop: 16 }}>Reading tags…</div>}
              {meta === null && <div className="sub" style={{ marginTop: 16 }}>Couldn't read this file from the shares.</div>}
              {meta && (
                <dl className="kv" style={{ marginTop: 18 }}>
                  {row('File', track.name)}
                  {row('Folder', folderName(track.folder, true))}
                  {row('Track', meta.track)}
                  {row('Album artist', meta.albumartist)}
                  {row('Year', meta.year)}
                  {row('Genre', meta.genre)}
                  {row('Length', meta.duration ? clock(meta.duration) : null)}
                  {row('Quality', quality)}
                  {row('Size', bytes(meta.size))}
                </dl>
              )}
              <Dialog.Close className="icon-btn dialog-close" aria-label="Close"><I d={CLOSE} size={16} /></Dialog.Close>
              {/* full-size cover: a nested dialog, so Esc or a click closes just this layer */}
              <Dialog.Root open={zoom} onOpenChange={setZoom}>
                <Dialog.Portal>
                  <Dialog.Overlay className="dialog-overlay art-overlay" onClick={() => setZoom(false)} />
                  <Dialog.Content className="art-view" aria-describedby={undefined} onClick={() => setZoom(false)}>
                    <Dialog.Title className="visually-hidden">Cover art</Dialog.Title>
                    <img src={coverUrl(track.id)} alt={`Cover art for ${meta?.album || track.name}`} />
                  </Dialog.Content>
                </Dialog.Portal>
              </Dialog.Root>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

// ---------- click vs double-click on things that also navigate ----------

/** Delay a single-click action so a double-click can cancel it (e.g. row click navigates, double-click plays). */
export function useClickOrDouble(single: () => void, double: () => void, delay = 230) {
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  return {
    onClick: () => { window.clearTimeout(timer.current); timer.current = window.setTimeout(single, delay) },
    onDoubleClick: () => { window.clearTimeout(timer.current); double() },
  }
}

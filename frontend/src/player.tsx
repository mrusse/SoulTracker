import * as ContextMenu from '@radix-ui/react-context-menu'
import * as Dialog from '@radix-ui/react-dialog'
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, type TrackMeta } from './api'
import { bytes, folderName } from './format'
import { navigate } from './state'

export interface Track { id: number; name: string; folder: string | null }

// Formats browsers play natively; anything else isn't offered (matches the backend's list).
const PLAYABLE = ['.mp3', '.flac', '.m4a', '.mp4', '.aac', '.ogg', '.oga', '.opus', '.wav']
export const isPlayable = (name: string | null | undefined) =>
  !!name && PLAYABLE.some((ext) => name.toLowerCase().endsWith(ext))

const streamUrl = (id: number) => `/api/file/${id}/stream`
export const coverUrl = (id: number) => `/api/file/${id}/cover`
const KEY = 'soultracker.player'

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
  const resumeAt = useRef(saved.time)     // restore position after a page reload

  const current = queue[index] ?? null

  // Load the current track into the audio element.
  useEffect(() => {
    const el = audio.current
    if (!el) return
    setError(null)
    if (!current) { el.removeAttribute('src'); el.load(); setMeta(null); return }
    if (el.dataset.id !== String(current.id)) {
      el.dataset.id = String(current.id)
      el.src = streamUrl(current.id)
      if (autoplay.current) el.play().catch(() => {})
    }
    let alive = true
    trackMeta(current.id).then((m) => { if (alive) setMeta(m) })
    return () => { alive = false }
  }, [current])

  // Remember the queue (and roughly where we were) across reloads.
  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify({ queue, index, volume, time: audio.current?.currentTime ?? 0 }))
    } catch { /* storage unavailable */ }
  }, [queue, index, volume, Math.floor(time / 5)])

  useEffect(() => { if (audio.current) audio.current.volume = volume }, [volume])

  const go = useCallback((i: number) => { autoplay.current = true; setIndex(i) }, [])
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

  const restart = (el: HTMLAudioElement) => { el.currentTime = 0; el.play().catch(() => {}) }

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
    if (i === index && audio.current && current?.id === ts[i].id) restart(audio.current)
    go(i)
  }, [index, current, go])

  const playNext = useCallback((tracks: Track[]) => {
    const ts = tracks.filter((t) => isPlayable(t.name))
    if (!ts.length) return
    if (!queue.length) { setQueue(ts); setIndex(0); return }
    setQueue((q) => [...q.slice(0, index + 1), ...ts, ...q.slice(index + 1)])
  }, [queue.length, index])

  const enqueue = useCallback((tracks: Track[]) => {
    const ts = tracks.filter((t) => isPlayable(t.name))
    if (ts.length) setQueue((q) => [...q, ...ts])
  }, [])

  const remove = useCallback((i: number) => {
    setQueue((q) => q.filter((_, j) => j !== i))
    if (i < index) setIndex(index - 1)
    else if (i === index) autoplay.current = !!playing
  }, [index, playing])

  const clear = useCallback(() => { audio.current?.pause(); setQueue([]); setIndex(0) }, [])
  const seek = useCallback((t: number) => { if (audio.current) audio.current.currentTime = t }, [])
  const setVolume = useCallback((v: number) => setVolumeState(Math.max(0, Math.min(1, v))), [])

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
    play, playAll, playNext, enqueue, jump: go, next, prev, toggle, seek, setVolume, remove, clear,
    showInfo: setInfo,
  }

  return (
    <Ctx.Provider value={value}>
      {children}
      <audio
        ref={audio}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
        onDurationChange={(e) => setDuration(e.currentTarget.duration || 0)}
        onLoadedMetadata={(e) => {
          if (resumeAt.current) { e.currentTarget.currentTime = resumeAt.current; resumeAt.current = 0 }
        }}
        onEnded={next}
        onError={() => { if (current) setError("Couldn't play this file") }}
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

export function Cover({ id, size }: { id: number | null; size: number }) {
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [id])
  return (
    <div className="cover" style={{ width: size, height: size }}>
      {id != null && !failed && <img src={coverUrl(id)} alt="" onError={() => setFailed(true)} />}
    </div>
  )
}

// ---------- the bar at the bottom of every page ----------

export function PlayerBar() {
  const p = usePlayer()
  const [showQueue, setShowQueue] = useState(false)
  const [lastVolume, setLastVolume] = useState(1)
  if (!p.current) return null
  const t = p.current
  const title = p.meta?.title || t.name
  const sub = [p.meta?.artist, p.meta?.album || folderName(t.folder, false)].filter(Boolean).join(' · ')
  const dur = p.duration || p.meta?.duration || 0

  return (
    <div className="player">
      <div className="player-track">
        <Cover id={t.id} size={48} />
        <div className="player-text">
          <div className="player-title" title={title}>{title}</div>
          <div className="player-sub" title={sub}>{p.error ?? sub}</div>
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
        <button className={`icon-btn ${showQueue ? 'on' : ''}`} onClick={() => setShowQueue(!showQueue)} aria-label="Queue">
          <I d={QUEUE} />
        </button>
      </div>

      {showQueue && <QueuePanel onClose={() => setShowQueue(false)} />}
    </div>
  )
}

function QueuePanel({ onClose }: { onClose: () => void }) {
  const p = usePlayer()
  const cur = useRef<HTMLDivElement>(null)
  useEffect(() => { cur.current?.scrollIntoView({ block: 'center' }) }, [])
  return (
    <div className="queue-panel">
      <div className="queue-head">
        <strong>Queue</strong>
        <span className="sub">{p.queue.length} {p.queue.length === 1 ? 'track' : 'tracks'}</span>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={p.clear}>Clear</button>
        <button className="icon-btn" onClick={onClose} aria-label="Close queue"><I d={CLOSE} size={16} /></button>
      </div>
      <div className="queue-list">
        {p.queue.map((t, i) => (
          <div key={`${t.id}-${i}`} ref={i === p.index ? cur : undefined}
               className={`queue-row ${i === p.index ? 'now' : ''} ${i < p.index ? 'past' : ''}`}
               onDoubleClick={() => p.jump(i)}>
            <span className="queue-num">{i === p.index && p.playing ? '▶' : i + 1}</span>
            <span className="queue-name" onClick={() => p.jump(i)} title={folderName(t.folder, true)}>
              {t.name}<span className="sub"> · {folderName(t.folder, false)}</span>
            </span>
            <button className="icon-btn small" onClick={() => p.remove(i)} aria-label="Remove from queue"><I d={CLOSE} size={14} /></button>
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------- right-click menus ----------

/** Right-click (or long-press) menu for one file. */
export function FileMenu({ track, children }: { track: Track; children: ReactNode }) {
  const p = usePlayer()
  const ok = isPlayable(track.name)
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
                <Cover id={track.id} size={140} />
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

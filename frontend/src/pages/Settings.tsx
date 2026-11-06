import { api } from '../api'
import { Seg } from '../components'
import { folderName } from '../format'
import { useData, useName, useSettings, type FileSort, type Theme } from '../state'

export default function Settings() {
  const s = useSettings()
  const name = useName()
  const hidden = useData(api.hidden, [])

  const label = (kind: string, value: string) =>
    kind === 'user' ? name(value) : kind === 'folder' ? folderName(value, true) : value.split('\\').pop()

  return (
    <>
      <div className="page-head"><h1>Settings</h1></div>
      <div className="grid" style={{ maxWidth: 760 }}>
        <div className="card">
          <h2>Display</h2>
          <dl className="kv" style={{ alignItems: 'center', rowGap: 14 }}>
            <dt>Privacy mode</dt>
            <dd>
              <label className="check"><input type="checkbox" checked={s.privacy} onChange={(e) => s.set({ privacy: e.target.checked })} />Replace usernames with stand-ins</label>
              <div className="sub">Handy for screenshots. Each user always gets the same stand-in. Only affects this browser.</div>
            </dd>
            <dt>Folder names</dt>
            <dd><label className="check"><input type="checkbox" checked={s.fullPath} onChange={(e) => s.set({ fullPath: e.target.checked })} />Show full path</label></dd>
            <dt>Files in a folder</dt>
            <dd><Seg<FileSort> value={s.fileSort} onChange={(fileSort) => s.set({ fileSort })} options={[['name', 'By name'], ['time', 'Newest download first']]} /></dd>
            <dt>Theme</dt>
            <dd><Seg<Theme> value={s.theme} onChange={(theme) => s.set({ theme })} options={[['system', 'System'], ['light', 'Light'], ['dark', 'Dark']]} /></dd>
          </dl>
        </div>

        <div className="card">
          <h2>Removed from stats</h2>
          {hidden.data?.length === 0 && <div className="sub">Nothing removed. Use "Remove from stats" on a user, folder or file to hide it here.</div>}
          {!!hidden.data?.length && (
            <table>
              <tbody>
                {hidden.data.map((h) => (
                  <tr key={`${h.kind}:${h.value}`}>
                    <td style={{ width: 70 }} className="sub">{h.kind}</td>
                    <td className="clip">{label(h.kind, h.value)}</td>
                    <td className="num"><button className="btn" onClick={async () => { await api.unhide(h.kind, h.value); hidden.reload() }}>Restore</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </>
  )
}

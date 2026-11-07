<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo_light.png">
    <img src="assets/logo_dark.png" width="120" alt="SoulTracker logo">
  </picture>
</p>

<h1 align="center">SoulTracker</h1>

Upload stats for SoulseekQt. It reads the `transfers.log` written by [soulseekqt-transfer-logging](https://github.com/mrusse/soulseekqt-transfer-logging) and shows who downloaded what from you: top users and folders, every file a user grabbed, failures and cancels, and charts over time.

Unlike my old [Slsk-Upload-Stats-Tracker](https://github.com/mrusse/Slsk-Upload-Stats-Tracker), there's no copy-pasting from the Diagnostics window, and it knows whether an upload actually finished.

## Setup

1. Set up the [logging hook](https://github.com/mrusse/soulseekqt-transfer-logging) and turn on **Options > UI > Show diagnostics** in SoulseekQt.
2. Run SoulTracker next to it:

```yaml
services:
  soultracker:
    image: ghcr.io/mrusse/soultracker:latest
    ports:
      - 8484:8484
    volumes:
      - /mnt/user/appdata/soulseek:/soulseek:ro   # the host folder transfers.log ends up in
      - /mnt/user/data/Files:/shares:ro           # the folder that holds your shared folders
      - /mnt/user/appdata/soultracker:/config     # SoulTracker's database
    environment:
      SOULTRACKER_PASSWORD: ""                     # optional
```

Then open `http://<server>:8484`. On first start it reads whatever is already in the log.

The `/shares` mount is only used for file sizes. Soulseek doesn't log them, so SoulTracker looks each file up on disk. Mount the folder that *contains* your shared folders (e.g. a shared folder called `Music` should end up at `/shares/Music`). Leave it out and you still get everything except sizes.

If a shared folder is nested (Soulseek shares `.../Archive/Archive`), or you'd rather not expose the parent folder, mount each shared folder by name instead, e.g. `/mnt/user/media/Music:/shares/Music:ro`. The folder name under `/shares` has to match the shared folder's name in Soulseek.

| Variable | Default | |
|---|---|---|
| `SOULTRACKER_LOG` | `/soulseek/transfers.log` | the hook's log file (also found at `/soulseek/.SoulseekQt/transfers.log`) |
| `SOULTRACKER_SHARES` | `/shares` | where to look up file sizes (comma separated for more than one) |
| `SOULTRACKER_PASSWORD` | | set it to require a password |
| `SOULTRACKER_POLL` | `2` | seconds between log reads |
| `SOULTRACKER_LASTFM_KEY` / `SOULTRACKER_LASTFM_SECRET` | | to scrobble songs played in SoulTracker ([get an API account](https://www.last.fm/api/account/create)), then **Settings → Connect Last.fm** |

## Discord status

Discord only accepts "Listening to…" status from programs on the same computer, so this part runs on your PC, not in the container.

1. Create an application at https://discord.com/developers/applications (call it SoulTracker; that name is what Discord shows) and copy its **Application ID**.
2. **Windows:** download `SoulTracker-Discord.exe` from the [releases](https://github.com/mrusse/SoulTracker/releases) and run it. It lives in the tray; right-click it to set the app ID and your SoulTracker address from the clipboard, and to turn on *Launch with Windows*.
3. **Elsewhere:** `uv run tools/discord_presence.py --app-id <id> --url http://<server>:8484` (or `pip install pypresence` and use `python`).

It shows the song, artist, album art (from Last.fm, if connected) and a progress bar while something's playing in SoulTracker, and clears when you pause.

## How accurate is it?

SoulseekQt doesn't log which file finished, so SoulTracker works it out from the order the client serves each user's queue. Checked against the client's own Uploads list over a few hours of real traffic, it got the right user and file for 99.3% of uploads.

## Development

```sh
uv sync
cd frontend && npm install && npm run dev   # UI on :5173, expects the API on :8484
SOULTRACKER_LOG=transfers.log SOULTRACKER_DB=data/dev.db uv run uvicorn soultracker.app:app --port 8484
```

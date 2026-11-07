<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo_light.png">
    <img src="assets/logo_dark.png" width="120" alt="SoulTracker logo">
  </picture>
</p>

<h1 align="center">SoulTracker</h1>

Upload stats for SoulseekQt and Nicotine+. It reads their transfer logs and shows who downloaded what from you: top users and folders, every file a user grabbed, failures and cancels, and charts over time.

Unlike my old [Slsk-Upload-Stats-Tracker](https://github.com/mrusse/Slsk-Upload-Stats-Tracker), there's no copy-pasting from the Diagnostics window, and it knows whether an upload actually finished.

## Setup

1. Turn on logging in your client. Use either one, or both at the same time:
   - **SoulseekQt:** set up the [logging hook](https://github.com/mrusse/soulseekqt-transfer-logging) and turn on **Options > UI > Show diagnostics**.
   - **Nicotine+:** turn on **Settings > Logging > Log transfers**.
2. Run SoulTracker next to it, mounting the logs of the client(s) you use:

```yaml
services:
  soultracker:
    image: ghcr.io/mrusse/soultracker:latest
    ports:
      - 8484:8484
    volumes:
      - /mnt/user/appdata/soulseek:/soulseek:ro        # SoulseekQt: the folder transfers.log ends up in
      - /mnt/user/appdata/nicotine/logs:/nicotine:ro   # Nicotine+: its logs folder
      - /mnt/user/data/Files:/shares:ro                # the folder that holds your shared folders
      - /mnt/user/appdata/soultracker:/config          # SoulTracker's database
    environment:
      SOULTRACKER_PASSWORD: ""                          # optional
```

Leave out the line for a client you don't use. Then open `http://<server>:8484`.

On first start SoulTracker reads whatever is already in the logs, including all of Nicotine+'s history (it imports in the background; Diagnostics shows the progress). Set `SOULTRACKER_NICOTINE_HISTORY=false` to start from now instead. With both clients, everything is combined, with a filter to see each one on its own.

> **Heads up:** a long Nicotine+ history can be big. Roughly, every 1 GB of upload logs is about 3.5 million uploads, which takes around 15 minutes to import and adds about 1.4 GB to SoulTracker's database. Check the size of Nicotine+'s `logs/transfers` folder first if you're not sure.

The `/shares` mount is only used for file sizes. Soulseek doesn't log them, so SoulTracker looks each file up on disk. Mount the folder that *contains* your shared folders (e.g. a shared folder called `Music` should end up at `/shares/Music`). Leave it out and you still get everything except sizes.

If a shared folder is nested (Soulseek shares `.../Archive/Archive`), or you'd rather not expose the parent folder, mount each shared folder by name instead, e.g. `/mnt/user/media/Music:/shares/Music:ro`. The folder name under `/shares` has to match the shared folder's name in Soulseek.

| Variable | Default | |
|---|---|---|
| `SOULTRACKER_LOG` | `/soulseek/transfers.log` | SoulseekQt: the hook's log file (also found at `/soulseek/.SoulseekQt/transfers.log`) |
| `SOULTRACKER_NICOTINE` | `/nicotine` | Nicotine+: its `logs` (or `logs/transfers`) folder |
| `SOULTRACKER_NICOTINE_HISTORY` | `true` | import Nicotine+'s existing logs on first start (`false` = only track from now on) |
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

Nicotine+ logs every finished upload with its user and file, so its numbers are exact.

SoulseekQt doesn't log which file finished, so SoulTracker works it out from the order the client serves each user's queue. Checked against the client's own Uploads list over a few hours of real traffic, it got the right user and file for 99.3% of uploads.

## Development

```sh
uv sync
cd frontend && npm install && npm run dev   # UI on :5173, expects the API on :8484
SOULTRACKER_LOG=transfers.log SOULTRACKER_DB=data/dev.db uv run uvicorn soultracker.app:app --port 8484
```

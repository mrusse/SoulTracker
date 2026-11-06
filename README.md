<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/logo_light.png">
  <img src="assets/logo_dark.png" width="96" align="right" alt="">
</picture>

# SoulTracker

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
      - /mnt/user/appdata/soulseek:/soulseek:ro   # the folder that has .SoulseekQt/transfers.log
      - /mnt/user/data/Files:/shares:ro           # the folder that holds your shared folders
      - /mnt/user/appdata/soultracker:/config     # SoulTracker's database
    environment:
      SOULTRACKER_PASSWORD: ""                     # optional
```

Then open `http://<server>:8484`. On first start it reads whatever is already in the log.

The `/shares` mount is only used for file sizes. Soulseek doesn't log them, so SoulTracker looks each file up on disk. Mount the folder that *contains* your shared folders (e.g. `Music (FLAC)` should end up at `/shares/Music (FLAC)`). Leave it out and you still get everything except sizes.

| Variable | Default | |
|---|---|---|
| `SOULTRACKER_LOG` | `/soulseek/.SoulseekQt/transfers.log` | the hook's log file |
| `SOULTRACKER_SHARES` | `/shares` | where to look up file sizes (comma separated for more than one) |
| `SOULTRACKER_PASSWORD` | | set it to require a password |
| `SOULTRACKER_POLL` | `2` | seconds between log reads |

## How accurate is it?

SoulseekQt doesn't log which file finished, so SoulTracker works it out from the order the client serves each user's queue. Checked against the client's own Uploads list over a few hours of real traffic, it got the right user and file for 99.3% of uploads.

## Development

```sh
uv sync
cd frontend && npm install && npm run dev   # UI on :5173, expects the API on :8484
SOULTRACKER_LOG=transfers.log SOULTRACKER_DB=data/dev.db uv run uvicorn soultracker.app:app --port 8484
```

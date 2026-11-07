# /// script
# requires-python = ">=3.9"
# dependencies = ["pypresence>=4.6"]
# ///
"""Show what you're playing in SoulTracker as Discord Rich Presence ("Listening to ...").

Discord only accepts Rich Presence from programs on the same computer as the Discord app, so
this runs on your PC (not in the SoulTracker container). It asks SoulTracker what's playing
every few seconds and updates your Discord status.

On Windows, use the tray app (SoulTracker-Discord.exe from the releases, or discord_tray.py).
Elsewhere, run this script:
    uv run tools/discord_presence.py --app-id <id> --url http://<server>:8484
or: pip install pypresence && python tools/discord_presence.py --app-id <id> --url ...

--app-id comes from a Discord application you create at https://discord.com/developers/applications
(its name is what Discord shows, e.g. "Listening to SoulTracker"). Options can also come from
the environment: SOULTRACKER_URL, SOULTRACKER_DISCORD_APP_ID, SOULTRACKER_PASSWORD.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Callable

# Shown when Last.fm has no album art (Discord fetches images itself, so it must be a public URL).
DEFAULT_IMAGE = "https://raw.githubusercontent.com/mrusse/SoulTracker/main/assets/logo_light.png"
RECONNECT_EVERY = 15  # seconds between attempts to reach Discord


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def fetch_now_playing(url: str, password: str | None) -> dict:
    req = urllib.request.Request(url.rstrip("/") + "/api/nowplaying")
    if password:
        token = base64.b64encode(f"discord:{password}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def text(s: str | None, fallback: str = "") -> str | None:
    """Discord wants 2-128 characters per text field."""
    s = (s or fallback).strip()
    if not s:
        return None
    if len(s) < 2:
        s += " "
    return s[:128]


def build_activity(np: dict) -> dict:
    name = np.get("name") or ""
    title = np.get("title") or name.rsplit(".", 1)[0] or "Unknown track"
    activity = {
        "details": text(title),
        "state": text(np.get("artist"), "Unknown artist"),
        "large_image": np.get("art_url") or DEFAULT_IMAGE,
        "large_text": text(np.get("album")),
    }
    duration, position = np.get("duration") or 0, np.get("position") or 0
    if duration:
        start = time.time() - position
        activity["start"] = int(start)
        activity["end"] = int(start + duration)
    return activity


class PresenceUpdater:
    """Keeps Discord in sync with SoulTracker. Call step() every few seconds."""

    def __init__(self, url: str, app_id: str | None, password: str | None = None,
                 dry_run: bool = False, on_log: Callable[[str], None] = log) -> None:
        self.url, self.app_id, self.password, self.dry_run, self.log = url, app_id, password, dry_run, on_log
        self.rpc = None
        self.last_key = None     # what Discord currently shows: (file_id, title) or None
        self.last_start = 0      # its progress-bar start time
        self.next_connect = 0.0
        self.status = "Starting"
        self.now_playing: dict = {"state": "stopped"}

    def step(self) -> None:
        try:
            np = fetch_now_playing(self.url, self.password)
            reachable = True
        except (urllib.error.URLError, OSError, ValueError) as e:
            np, reachable = {"state": "stopped"}, False
            if self.last_key is not None:
                self.log(f"can't reach SoulTracker ({e}); clearing status")
        self.now_playing = np

        playing = np.get("state") == "playing"
        activity = build_activity(np) if playing else None
        # Only update when the track changes or the position jumps (a seek), to stay well
        # inside Discord's rate limit (5 updates per 20 seconds).
        key = (np.get("file_id"), np.get("title")) if playing else None
        start = (activity or {}).get("start", 0)
        changed = key != self.last_key or (playing and abs(start - self.last_start) > 4)

        if self.dry_run:
            if changed:
                self.log(f"would show: {json.dumps(activity)}" if activity else "would clear status")
                self.last_key, self.last_start = key, start
            self.status = self._describe(reachable, True)
            return

        if self.rpc is None and self.app_id and time.time() >= self.next_connect:
            try:
                from pypresence import Presence
                self.rpc = Presence(self.app_id)
                self.rpc.connect()
                self.log("connected to Discord")
                self.last_key = "reconnected"  # force a refresh
                changed = True
            except Exception as e:  # Discord not running yet, etc.
                self.rpc = None
                self.next_connect = time.time() + RECONNECT_EVERY
                self.log(f"waiting for Discord ({type(e).__name__}: {e})")

        if self.rpc is not None and changed:
            try:
                if activity:
                    from pypresence import ActivityType, StatusDisplayType
                    self.rpc.update(activity_type=ActivityType.LISTENING,
                                    status_display_type=StatusDisplayType.DETAILS,  # member list: "Listening to <title>"
                                    **{k: v for k, v in activity.items() if v is not None})
                    self.log(f"showing: {activity['details']} - {activity['state']}")
                else:
                    self.rpc.clear()
                    self.log("cleared status")
                self.last_key, self.last_start = key, start
            except Exception as e:  # Discord closed or restarted
                self.log(f"lost Discord ({type(e).__name__}); reconnecting")
                self._drop()
        self.status = self._describe(reachable, self.rpc is not None)

    def _describe(self, reachable: bool, discord: bool) -> str:
        if not self.app_id and not self.dry_run:
            return "Set your Discord app ID"
        if not reachable:
            return f"Can't reach SoulTracker at {self.url}"
        if not discord:
            return "Waiting for Discord"
        np = self.now_playing
        if np.get("state") == "playing":
            title = np.get("title") or (np.get("name") or "").rsplit(".", 1)[0]
            return f"Playing: {title}" + (f" – {np['artist']}" if np.get("artist") else "")
        return "Paused" if np.get("state") == "paused" else "Nothing playing"

    def _drop(self) -> None:
        try:
            self.rpc.close()
        except Exception:
            pass
        self.rpc = None

    def close(self) -> None:
        """Clear the status and disconnect (e.g. on quit or when settings change)."""
        if self.rpc is not None:
            try:
                self.rpc.clear()
            except Exception:
                pass
            self._drop()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default=os.environ.get("SOULTRACKER_URL", "http://localhost:8484"),
                    help="SoulTracker address (default http://localhost:8484)")
    ap.add_argument("--app-id", default=os.environ.get("SOULTRACKER_DISCORD_APP_ID"),
                    help="Discord application ID")
    ap.add_argument("--password", default=os.environ.get("SOULTRACKER_PASSWORD"),
                    help="SoulTracker password, if you set one")
    ap.add_argument("--interval", type=float, default=5, help="seconds between checks (default 5)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be shown instead of talking to Discord")
    args = ap.parse_args()
    if not args.app_id and not args.dry_run:
        ap.error("--app-id is required (create an application at https://discord.com/developers/applications)")

    updater = PresenceUpdater(args.url, args.app_id, args.password, args.dry_run)
    log(f"watching {args.url}")
    try:
        while True:
            updater.step()
            time.sleep(args.interval)
    finally:
        updater.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)

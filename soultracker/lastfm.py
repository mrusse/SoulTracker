"""Last.fm scrobbling (one Last.fm account per SoulTracker install).

Setup: create an API account at https://www.last.fm/api/account/create, then set
SOULTRACKER_LASTFM_KEY and SOULTRACKER_LASTFM_SECRET and click "Connect Last.fm" in Settings.
The session key lives in the state table. Scrobbles are stored first and sent from there, so a
Last.fm outage just means they go out later.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time

import pylast

from .db import get_state, set_state

log = logging.getLogger("soultracker.lastfm")

KEY = os.environ.get("SOULTRACKER_LASTFM_KEY", "")
SECRET = os.environ.get("SOULTRACKER_LASTFM_SECRET", "")
MAX_ATTEMPTS = 50

_art_cache: dict[tuple[str, str], str | None] = {}
_art_lock = threading.Lock()


def configured() -> bool:
    return bool(KEY and SECRET)


def network(session_key: str | None = None) -> pylast.LastFMNetwork:
    """Separate function so tests can replace it."""
    return pylast.LastFMNetwork(api_key=KEY, api_secret=SECRET, session_key=session_key or "")


def _session(conn: sqlite3.Connection) -> str | None:
    return get_state(conn, "lastfm_session")


def enabled(conn: sqlite3.Connection) -> bool:
    return configured() and bool(_session(conn)) and get_state(conn, "lastfm_enabled") != "0"


def status(conn: sqlite3.Connection) -> dict:
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM scrobbles GROUP BY status").fetchall())
    recent = [dict(r) for r in conn.execute(
        "SELECT artist, title, album, played_at, status, error FROM scrobbles ORDER BY played_at DESC, id DESC LIMIT 10")]
    return {
        "configured": configured(),
        "connected": bool(_session(conn)),
        "user": get_state(conn, "lastfm_user"),
        "enabled": get_state(conn, "lastfm_enabled") != "0",
        "sent": counts.get("sent", 0),
        "pending": counts.get("pending", 0),
        "recent": recent,
    }


def auth_url(callback: str) -> str:
    return f"https://www.last.fm/api/auth/?api_key={KEY}&cb={callback}"


def finish_auth(conn: sqlite3.Connection, token: str) -> str:
    key, user = pylast.SessionKeyGenerator(network()).get_web_auth_session_key_username(None, token)
    set_state(conn, "lastfm_session", key)
    set_state(conn, "lastfm_user", user)
    set_state(conn, "lastfm_enabled", "1")
    return user


def disconnect(conn: sqlite3.Connection) -> None:
    for k in ("lastfm_session", "lastfm_user"):
        set_state(conn, k, None)


def set_enabled(conn: sqlite3.Connection, on: bool) -> None:
    set_state(conn, "lastfm_enabled", "1" if on else "0")


def _int(v) -> int | None:
    """200.0 -> 200, "3/12" (track 3 of 12) -> 3, junk -> None."""
    try:
        return int(float(str(v).split("/")[0]))
    except (TypeError, ValueError):
        return None


def now_playing(conn: sqlite3.Connection, tags: dict) -> None:
    """Tell Last.fm what's playing. Best effort: failures are only logged."""
    if not enabled(conn) or not tags.get("artist") or not tags.get("title"):
        return
    try:
        network(_session(conn)).update_now_playing(
            artist=tags["artist"], title=tags["title"], album=tags.get("album"),
            album_artist=tags.get("albumartist"), duration=_int(tags.get("duration")),
            track_number=_int(tags.get("track")))
    except Exception as e:  # network trouble, bad session...
        log.warning("now playing update failed: %s", e)


def record_scrobble(conn: sqlite3.Connection, file_id: int, played_at: int, tags: dict) -> str:
    """Store a scrobble and try to send it. Returns its status (sent/pending/skipped)."""
    if not enabled(conn):
        return "disabled"
    status, error = "pending", None
    if not tags.get("artist") or not tags.get("title"):
        status, error = "skipped", "no artist/title tags"
    cur = conn.execute(
        "INSERT INTO scrobbles(file_id, played_at, artist, title, album, albumartist, duration, status, error) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (file_id, played_at, tags.get("artist"), tags.get("title"), tags.get("album"),
         tags.get("albumartist"), _int(tags.get("duration")), status, error))
    conn.commit()
    if status == "pending":
        flush(conn, only_id=cur.lastrowid)
        status = conn.execute("SELECT status FROM scrobbles WHERE id=?", (cur.lastrowid,)).fetchone()[0]
    return status


def flush(conn: sqlite3.Connection, only_id: int | None = None) -> int:
    """Send pending scrobbles (oldest first). Returns how many were sent."""
    if not enabled(conn):
        return 0
    rows = conn.execute(
        "SELECT * FROM scrobbles WHERE status='pending' AND attempts < ? " +
        ("AND id=? " if only_id else "") + "ORDER BY played_at LIMIT 50",
        (MAX_ATTEMPTS, only_id) if only_id else (MAX_ATTEMPTS,)).fetchall()
    if not rows:
        return 0
    net = network(_session(conn))
    sent = 0
    for r in rows:
        try:
            net.scrobble(artist=r["artist"], title=r["title"], timestamp=r["played_at"],
                         album=r["album"], album_artist=r["albumartist"], duration=r["duration"])
            conn.execute("UPDATE scrobbles SET status='sent', error=NULL, attempts=attempts+1 WHERE id=?", (r["id"],))
            sent += 1
        except Exception as e:
            conn.execute("UPDATE scrobbles SET error=?, attempts=attempts+1 WHERE id=?", (str(e)[:300], r["id"]))
            log.warning("scrobble failed (will retry): %s", e)
            break  # probably offline: stop and try again later
    conn.commit()
    return sent


def album_art(artist: str | None, album: str | None) -> str | None:
    """Public cover image URL from Last.fm (used by the Discord helper). Cached; needs only the API key."""
    if not KEY or not artist or not album:
        return None
    k = (artist.lower(), album.lower())
    with _art_lock:
        if k in _art_cache:
            return _art_cache[k]
    try:
        url = network().get_album(artist, album).get_cover_image(pylast.SIZE_EXTRA_LARGE) or None
    except Exception:
        url = None
    with _art_lock:
        _art_cache[k] = url
    return url


def retry_loop_step(conn: sqlite3.Connection, last: list[float], every: float = 300) -> None:
    """Called from the background loop: retry pending scrobbles every few minutes."""
    if time.time() - last[0] >= every:
        last[0] = time.time()
        try:
            flush(conn)
        except Exception:
            log.exception("scrobble retry failed")

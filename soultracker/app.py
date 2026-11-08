"""FastAPI app: JSON API + the built frontend, with the log reader running in the background.

Configuration (environment variables):
  SOULTRACKER_LOG       path to transfers.log   (default: /soulseek/transfers.log, or
                        /soulseek/.SoulseekQt/transfers.log if that is where it is)
  SOULTRACKER_DB        SQLite database path    (default /config/soultracker.db)
  SOULTRACKER_SHARES    shared folder root(s), comma separated, for file sizes (default /shares)
  SOULTRACKER_PASSWORD  if set, the UI/API require this password (HTTP basic auth, any username)
  SOULTRACKER_POLL      seconds between log reads (default 2)
"""

from __future__ import annotations

import logging
import mimetypes
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from . import lastfm, media, queries
from .db import CLIENTS, connect, get_state, set_state
from pydantic import BaseModel
from .ingest import Ingestor
from . import aggregates
from .nicotine import NicotineFollower, NicotineTracker, catch_up_progress
from .tracker import Tracker
from .shares import Shares

log = logging.getLogger("soultracker")

# Python reads MIME types from the OS (on Windows, the registry may say .js is text/plain,
# which browsers refuse to run as a module), so pin the ones the frontend needs.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")

# Mount the folder that holds transfers.log at /soulseek. Depending on how the soulseek
# container's appdata is mapped, the log is either directly in it or in .SoulseekQt/ inside it.
LOG_CANDIDATES = ["/soulseek/transfers.log", "/soulseek/.SoulseekQt/transfers.log"]
LOG_ENV = os.environ.get("SOULTRACKER_LOG", "")


def log_path() -> str:
    if LOG_ENV:
        return LOG_ENV
    return next((c for c in LOG_CANDIDATES if Path(c).exists()), LOG_CANDIDATES[0])


# Nicotine+: mount its logs folder (or logs/transfers) at /nicotine, or point SOULTRACKER_NICOTINE at either.
NICOTINE_ENV = os.environ.get("SOULTRACKER_NICOTINE", "")
# Import Nicotine+'s existing logs the first time they're found (set to false to start from "now").
NICOTINE_HISTORY = os.environ.get("SOULTRACKER_NICOTINE_HISTORY", "true").strip().lower() not in ("0", "false", "no", "off")


def nicotine_path() -> str | None:
    """The folder holding Nicotine+'s uploads_*.log files (prefers a transfers/ subfolder)."""
    root = Path(NICOTINE_ENV or "/nicotine")
    candidates = [root / "transfers", root]
    for c in candidates:
        if c.is_dir() and any(c.glob("uploads_*.log")):
            return str(c)
    for c in candidates:  # set up, but nothing logged yet
        if c.is_dir():
            return str(c)
    return None


def sources(conn) -> list[dict]:
    """The clients SoulTracker reads from, with their log health."""
    out = []
    qt = log_path()
    if Path(qt).exists() or LOG_ENV or Path("/soulseek").exists():
        out.append({"client": "soulseekqt", "label": CLIENTS["soulseekqt"], "path": qt,
                    "exists": Path(qt).exists(), "last_line_at": get_state(conn, "last_line_at")})
    nic = nicotine_path()
    if nic or NICOTINE_ENV or Path("/nicotine").exists():
        out.append({"client": "nicotine", "label": CLIENTS["nicotine"], "path": nic or NICOTINE_ENV or "/nicotine",
                    "exists": bool(nic), "last_line_at": get_state(conn, "last_line_at_nicotine"),
                    "importing": catch_up_progress(conn, nic) if nic else None})
    return out


DB_PATH = os.environ.get("SOULTRACKER_DB", "/config/soultracker.db")
SHARE_ROOTS = [s for s in os.environ.get("SOULTRACKER_SHARES", "/shares").split(",") if s.strip()]
PASSWORD = os.environ.get("SOULTRACKER_PASSWORD", "")
POLL = float(os.environ.get("SOULTRACKER_POLL", "2"))
STATIC = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ingest_loop(stop: threading.Event) -> None:
    conn = connect(DB_PATH)
    qt: Ingestor | None = None
    nic: NicotineFollower | None = None
    shares = Shares(SHARE_ROOTS)
    last_share_scan = 0.0
    last_scrobble_retry = [0.0]
    while not stop.is_set():
        try:
            # Each client is optional and picked up as soon as its log appears.
            if qt is None and Path(log_path()).exists():
                qt = Ingestor(conn, log_path(), shares.size_of)
                log.info("reading SoulseekQt log %s", log_path())
            if nic is None and nicotine_path():
                nic = NicotineFollower(conn, nicotine_path(), NicotineTracker(conn, Tracker(conn, shares.size_of)),
                                       import_history=NICOTINE_HISTORY)
                log.info("reading Nicotine+ logs in %s", nicotine_path())
            for source, state_key in ((qt, "last_line_at"), (nic, "last_line_at_nicotine")):
                if source is None:
                    continue
                before = source.lines
                if isinstance(source, Ingestor):
                    source.poll()  # its own transaction (and bulk handling)
                else:
                    with conn:
                        source.poll()
                if source.lines > before:
                    with conn:
                        set_state(conn, state_key, _now())
            with conn:
                if time.time() - last_share_scan > 3600:  # pick up newly added share folders
                    shares.refresh()
                    last_share_scan = time.time()
                while shares.update_sizes(conn) == 500:
                    pass
            aggregates.refresh(conn)  # if something marked the totals for a rebuild
            lastfm.retry_loop_step(conn, last_scrobble_retry)
        except Exception:
            log.exception("ingest failed")
        stop.wait(POLL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    connect(DB_PATH).close()  # create the schema before anyone queries
    stop = threading.Event()
    t = threading.Thread(target=ingest_loop, args=(stop,), daemon=True, name="ingest")
    if os.environ.get("SOULTRACKER_NO_INGEST") != "1":
        t.start()
    yield
    stop.set()


security = HTTPBasic(auto_error=False)


def auth(request: Request, creds: HTTPBasicCredentials | None = Depends(security)) -> None:
    if not PASSWORD or request.url.path == "/api/health":
        return
    if creds is None or not secrets.compare_digest(creds.password.encode(), PASSWORD.encode()):
        raise HTTPException(401, "password required", headers={"WWW-Authenticate": 'Basic realm="SoulTracker"'})


def db():
    conn = connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


app = FastAPI(title="SoulTracker", lifespan=lifespan, dependencies=[Depends(auth)])


@app.get("/api/health")
def api_health():
    return {"ok": True}


@app.get("/api/sources")
def api_sources(conn=Depends(db)):
    """Which clients SoulTracker reads (only the ones that are set up), with their log health."""
    return sources(conn)


@app.get("/api/summary")
def api_summary(client: str | None = None, conn=Depends(db)):
    s = queries.summary(conn, client=client)
    path = log_path()
    s["log"] = {"path": path, "exists": Path(path).exists(),
                "last_line_at": get_state(conn, "last_line_at")}
    return s


@app.get("/api/top/users")
def api_top_users(by: str = "bytes", limit: int = 100, client: str | None = None, conn=Depends(db)):
    return queries.top_users(conn, by, limit, client=client)


@app.get("/api/top/folders")
def api_top_folders(by: str = "users", limit: int = 100, client: str | None = None, conn=Depends(db)):
    return queries.top_folders(conn, by, limit, client=client)


@app.get("/api/users")
def api_users(sort: str = "size", search: str | None = None, client: str | None = None, conn=Depends(db)):
    return queries.users(conn, sort, search, client=client)


@app.get("/api/user")
def api_user(name: str, client: str | None = None, conn=Depends(db)):
    d = queries.user_detail(conn, name, client=client)
    if d is None:
        raise HTTPException(404, "no such user")
    return d


@app.get("/api/folder")
def api_folder(path: str, client: str | None = None, conn=Depends(db)):
    d = queries.folder_detail(conn, path, client=client)
    if d is None:
        raise HTTPException(404, "no such folder")
    return d


@app.get("/api/file/{file_id}")
def api_file(file_id: int, client: str | None = None, conn=Depends(db)):
    d = queries.file_detail(conn, file_id, client=client)
    if d is None:
        raise HTTPException(404, "no such file")
    return d


# ---- playback ----

_shares = Shares(SHARE_ROOTS)
_shares_scanned = 0.0


def _real_file(conn, file_id: int) -> Path:
    """The file on the shares for a tracked file id (only files SoulTracker knows can be played)."""
    global _shares_scanned
    row = conn.execute("SELECT vpath FROM files WHERE id=?", (file_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "no such file")
    path = _shares.resolve(row[0])
    if path is None and time.time() - _shares_scanned > 60:  # maybe a share was added since
        _shares.refresh()
        _shares_scanned = time.time()
        path = _shares.resolve(row[0])
    if path is None:
        raise HTTPException(404, "file not found on the shares")
    return path


@app.get("/api/file/{file_id}/stream")
def api_stream(file_id: int, conn=Depends(db)):
    path = _real_file(conn, file_id)
    mime = media.mime_type(path)
    if mime is None:
        raise HTTPException(415, "this format can't be played in the browser")
    # FileResponse handles Range requests, so the player can seek without downloading everything.
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/file/{file_id}/meta")
def api_meta(file_id: int, conn=Depends(db)):
    path = _real_file(conn, file_id)
    return {**media.tags(path), "playable": media.mime_type(path) is not None,
            "format": path.suffix.lstrip(".").upper(), "size": path.stat().st_size}


@app.get("/api/file/{file_id}/cover")
def api_cover(file_id: int, conn=Depends(db)):
    found = media.cover(_real_file(conn, file_id))
    if found is None:
        raise HTTPException(404, "no cover art")
    data, mime = found
    return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})


# ---- now playing (read by Last.fm here and by the Discord helper) ----

class NowPlayingIn(BaseModel):
    file_id: int | None = None
    play_id: str | None = None      # changes on every new play, even of the same file
    state: str = "stopped"          # playing | paused | stopped
    position: float = 0
    duration: float = 0


class ScrobbleIn(BaseModel):
    file_id: int
    played_at: int                  # unix time the track started


_np_lock = threading.Lock()
_np: dict = {"state": "stopped"}
NP_STALE = 90  # seconds without a heartbeat before a "playing" track counts as stopped


def _track_tags(conn, file_id: int) -> dict:
    try:
        return media.tags(_real_file(conn, file_id))
    except HTTPException:
        return {}


@app.post("/api/nowplaying")
def api_nowplaying_set(np: NowPlayingIn, conn=Depends(db)):
    with _np_lock:
        new_play = np.state == "playing" and np.play_id and np.play_id != _np.get("play_id")
        same_file = np.file_id == _np.get("file_id")
        tags = _np.get("tags") if same_file else None
    if np.file_id is not None and tags is None:
        tags = _track_tags(conn, np.file_id)
    row = conn.execute("SELECT name, folder FROM files WHERE id=?", (np.file_id,)).fetchone() if np.file_id else None
    with _np_lock:
        _np.clear()
        _np.update(np.model_dump(), tags=tags or {}, updated=time.time(),
                   name=row["name"] if row else None, folder=row["folder"] if row else None)
    if new_play and tags:
        threading.Thread(target=_lastfm_now_playing, args=(tags,), daemon=True).start()
    return {"ok": True}


def _lastfm_now_playing(tags: dict) -> None:
    c = connect(DB_PATH)
    try:
        lastfm.now_playing(c, tags)
    finally:
        c.close()


@app.get("/api/nowplaying")
def api_nowplaying_get():
    with _np_lock:
        np = dict(_np)
    if np.get("state") == "stopped" or not np.get("file_id"):
        return {"state": "stopped"}
    age = time.time() - np.get("updated", 0)
    state = np["state"]
    position = np.get("position", 0)
    if state == "playing":
        if age > NP_STALE:
            return {"state": "stopped"}
        position = min(position + age, np.get("duration") or position + age)
    tags = np.get("tags") or {}
    return {
        "state": state, "file_id": np["file_id"], "name": np.get("name"), "folder": np.get("folder"),
        "title": tags.get("title"), "artist": tags.get("artist"), "album": tags.get("album"),
        "position": round(position, 1), "duration": np.get("duration") or tags.get("duration"),
        "art_url": lastfm.album_art(tags.get("albumartist") or tags.get("artist"), tags.get("album")),
    }


@app.post("/api/scrobble")
def api_scrobble(s: ScrobbleIn, conn=Depends(db)):
    return {"status": lastfm.record_scrobble(conn, s.file_id, s.played_at, _track_tags(conn, s.file_id))}


# ---- Last.fm account ----

@app.get("/api/lastfm")
def api_lastfm(conn=Depends(db)):
    return lastfm.status(conn)


@app.get("/api/lastfm/connect")
def api_lastfm_connect(request: Request):
    if not lastfm.configured():
        raise HTTPException(400, "set SOULTRACKER_LASTFM_KEY and SOULTRACKER_LASTFM_SECRET first")
    return RedirectResponse(lastfm.auth_url(str(request.url_for("api_lastfm_callback"))))


@app.get("/api/lastfm/callback")
def api_lastfm_callback(token: str = "", conn=Depends(db)):
    try:
        lastfm.finish_auth(conn, token)
        conn.commit()
        return RedirectResponse("/#/settings")
    except Exception as e:
        log.warning("last.fm connect failed: %s", e)
        return RedirectResponse("/#/settings?lastfm_error=1")


@app.post("/api/lastfm/disconnect")
def api_lastfm_disconnect(conn=Depends(db)):
    with conn:
        lastfm.disconnect(conn)
    return {"ok": True}


@app.post("/api/lastfm/enabled")
def api_lastfm_enabled(body: dict, conn=Depends(db)):
    with conn:
        lastfm.set_enabled(conn, bool(body.get("enabled")))
    return {"ok": True}


@app.get("/api/search")
def api_search(q: str = "", client: str | None = None, conn=Depends(db)):
    return queries.search(conn, q, client=client) if q.strip() else {"users": [], "folders": [], "files": []}


@app.get("/api/recent")
def api_recent(limit: int = 50, client: str | None = None, conn=Depends(db)):
    return queries.recent(conn, limit, client=client)


@app.get("/api/charts/timeseries")
def api_timeseries(bucket: str = "day", since: str | None = None, client: str | None = None, conn=Depends(db)):
    return queries.timeseries(conn, bucket, since, client=client)


@app.get("/api/charts/heatmap")
def api_heatmap(since: str | None = None, client: str | None = None, conn=Depends(db)):
    return queries.heatmap(conn, since, client=client)


SHUFFLE_WINDOWS = {"24h": 1, "7d": 7, "30d": 30}


@app.post("/api/shuffle")
def api_shuffle(body: dict, conn=Depends(db)):
    """A few random songs for shuffle play. Body: mode (recent|random), users (N most recent),
    window (any|24h|7d|30d), exclude (file ids), count."""
    days = SHUFFLE_WINDOWS.get(body.get("window") or "")
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S") if days else None
    try:
        users, count = int(body.get("users") or 10), int(body.get("count") or 5)
        exclude = [int(x) for x in (body.get("exclude") or [])][-2000:]
    except (TypeError, ValueError):
        raise HTTPException(400, "users, count and exclude must be numbers")
    return queries.shuffle(conn, "random" if body.get("mode") == "random" else "recent",
                           users=users, since=since, exclude=exclude, count=count)


@app.get("/api/outcomes")
def api_outcomes(client: str | None = None, conn=Depends(db)):
    return queries.outcomes(conn, client=client)


@app.get("/api/diagnostics")
def api_diagnostics(client: str | None = None, conn=Depends(db)):
    s = queries.summary(conn, client=client)
    path = log_path()
    return {
        "counts": {k: s[k] for k in ("failed", "cancelled", "denied", "queued", "uploads")},
        **queries.outcomes(conn, limit=10, client=client),
        "unknown_sizes": queries.unknown_sizes(conn, client=client),
        "log": {"path": path, "exists": Path(path).exists(), "last_line_at": get_state(conn, "last_line_at"),
                "first_seen": s["first_seen"], "last_seen": s["last_seen"]},
        "shares": SHARE_ROOTS,
        "sources": sources(conn),
    }


KINDS = {"user", "folder", "file"}


@app.get("/api/hidden")
def api_hidden(conn=Depends(db)):
    return queries.hidden(conn)


@app.post("/api/hidden")
def api_hide(item: dict, conn=Depends(db)):
    if item.get("kind") not in KINDS or not item.get("value"):
        raise HTTPException(400, "kind must be user/folder/file and value is required")
    with conn:
        queries.hide(conn, item["kind"], item["value"])
    aggregates.refresh_soon(conn)
    return {"ok": True}


@app.delete("/api/hidden")
def api_unhide(kind: str, value: str, conn=Depends(db)):
    with conn:
        queries.unhide(conn, kind, value)
    aggregates.refresh_soon(conn)
    return {"ok": True}


# ---- frontend ----

if STATIC.exists():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = STATIC / path
        if path and f.is_file() and STATIC in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(STATIC / "index.html")

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
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from . import queries
from .db import connect, get_state, set_state
from .ingest import Ingestor
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


DB_PATH = os.environ.get("SOULTRACKER_DB", "/config/soultracker.db")
SHARE_ROOTS = [s for s in os.environ.get("SOULTRACKER_SHARES", "/shares").split(",") if s.strip()]
PASSWORD = os.environ.get("SOULTRACKER_PASSWORD", "")
POLL = float(os.environ.get("SOULTRACKER_POLL", "2"))
STATIC = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ingest_loop(stop: threading.Event) -> None:
    conn = connect(DB_PATH)
    # Wait for the log to appear (e.g. the hook hasn't written anything yet).
    while not Path(log_path()).exists() and not stop.wait(POLL):
        pass
    ing = Ingestor(conn, log_path())
    log.info("reading %s", log_path())
    shares = Shares(SHARE_ROOTS)
    last_share_scan = 0.0
    while not stop.is_set():
        try:
            before = ing.lines
            ing.poll()
            with conn:
                if ing.lines > before:
                    set_state(conn, "last_line_at", _now())
                if time.time() - last_share_scan > 3600:  # pick up newly added share folders
                    shares.refresh()
                    last_share_scan = time.time()
                while shares.update_sizes(conn) == 500:
                    pass
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


@app.get("/api/summary")
def api_summary(conn=Depends(db)):
    s = queries.summary(conn)
    path = log_path()
    s["log"] = {"path": path, "exists": Path(path).exists(),
                "last_line_at": get_state(conn, "last_line_at")}
    return s


@app.get("/api/top/users")
def api_top_users(by: str = "bytes", limit: int = 100, conn=Depends(db)):
    return queries.top_users(conn, by, limit)


@app.get("/api/top/folders")
def api_top_folders(by: str = "users", limit: int = 100, conn=Depends(db)):
    return queries.top_folders(conn, by, limit)


@app.get("/api/users")
def api_users(sort: str = "size", search: str | None = None, conn=Depends(db)):
    return queries.users(conn, sort, search)


@app.get("/api/user")
def api_user(name: str, conn=Depends(db)):
    d = queries.user_detail(conn, name)
    if d is None:
        raise HTTPException(404, "no such user")
    return d


@app.get("/api/folder")
def api_folder(path: str, conn=Depends(db)):
    d = queries.folder_detail(conn, path)
    if d is None:
        raise HTTPException(404, "no such folder")
    return d


@app.get("/api/file/{file_id}")
def api_file(file_id: int, conn=Depends(db)):
    d = queries.file_detail(conn, file_id)
    if d is None:
        raise HTTPException(404, "no such file")
    return d


@app.get("/api/search")
def api_search(q: str = "", conn=Depends(db)):
    return queries.search(conn, q) if q.strip() else {"users": [], "folders": [], "files": []}


@app.get("/api/recent")
def api_recent(limit: int = 50, conn=Depends(db)):
    return queries.recent(conn, limit)


@app.get("/api/charts/timeseries")
def api_timeseries(bucket: str = "day", since: str | None = None, conn=Depends(db)):
    return queries.timeseries(conn, bucket, since)


@app.get("/api/charts/heatmap")
def api_heatmap(since: str | None = None, conn=Depends(db)):
    return queries.heatmap(conn, since)


@app.get("/api/outcomes")
def api_outcomes(conn=Depends(db)):
    return queries.outcomes(conn)


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
    return {"ok": True}


@app.delete("/api/hidden")
def api_unhide(kind: str, value: str, conn=Depends(db)):
    with conn:
        queries.unhide(conn, kind, value)
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

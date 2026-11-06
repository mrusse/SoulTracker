"""Stats queries used by the API.

"Completed" = status completed or uploading (an uploading row with no failure is a completed
upload, see db.py). Hidden users/folders/files ("Remove selected") are excluded everywhere.
"""

from __future__ import annotations

import re
import sqlite3

DONE = "t.status IN ('completed','uploading')"

VISIBLE = """
    u.name NOT IN (SELECT value FROM hidden WHERE kind='user')
    AND (f.id IS NULL OR (
        f.folder NOT IN (SELECT value FROM hidden WHERE kind='folder')
        AND f.vpath NOT IN (SELECT value FROM hidden WHERE kind='file')))
"""

BASE = f"""
    FROM transfers t
    JOIN users u ON u.id = t.user_id
    LEFT JOIN files f ON f.id = t.file_id
    WHERE {VISIBLE}
"""

# time of an upload: when it started, or when it was requested (denied/cancelled/queued)
WHEN = "COALESCE(t.started_at, t.requested_at)"


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def summary(conn: sqlite3.Connection) -> dict:
    s = dict(conn.execute(f"""
        SELECT
          SUM({DONE})                                          AS uploads,
          SUM(CASE WHEN {DONE} THEN COALESCE(f.size, 0) END)    AS bytes,
          SUM(CASE WHEN {DONE} AND f.size IS NULL THEN 1 END)   AS uploads_unknown_size,
          COUNT(DISTINCT CASE WHEN {DONE} THEN t.user_id END)  AS users,
          COUNT(DISTINCT CASE WHEN {DONE} THEN t.file_id END)  AS files,
          SUM(t.status IN ('failed','local_error'))            AS failed,
          SUM(t.status = 'cancelled')                          AS cancelled,
          SUM(t.status = 'denied')                             AS denied,
          SUM(t.status = 'queued')                             AS queued,
          AVG(CASE WHEN t.status='completed' AND t.speed > 0 THEN t.speed END) AS avg_speed,
          MIN({WHEN}) AS first_seen,
          MAX({WHEN}) AS last_seen
        {BASE}""").fetchone())
    last = conn.execute(f"""
        SELECT u.name AS user, f.name AS file, f.folder, t.started_at AS time
        {BASE} AND {DONE} ORDER BY t.started_at DESC, t.id DESC LIMIT 1""").fetchone()
    s["last_upload"] = dict(last) if last else None
    return {k: (v if v is not None else 0) if k not in ("first_seen", "last_seen", "last_upload", "avg_speed") else v
            for k, v in s.items()}


def top_users(conn: sqlite3.Connection, by: str = "bytes", limit: int = 100) -> list[dict]:
    order = "bytes DESC, uploads DESC" if by == "bytes" else "uploads DESC, bytes DESC"
    return _rows(conn, f"""
        SELECT u.name AS user, COUNT(*) AS uploads, COALESCE(SUM(f.size), 0) AS bytes,
               MAX(t.started_at) AS last_upload
        {BASE} AND {DONE}
        GROUP BY u.id ORDER BY {order} LIMIT ?""", (limit,))


def top_folders(conn: sqlite3.Connection, by: str = "users", limit: int = 100) -> list[dict]:
    order = {"users": "users DESC, uploads DESC", "uploads": "uploads DESC, users DESC",
             "bytes": "bytes DESC"}.get(by, "users DESC, uploads DESC")
    return _rows(conn, f"""
        SELECT f.folder, COUNT(DISTINCT t.user_id) AS users, COUNT(*) AS uploads,
               COALESCE(SUM(f.size), 0) AS bytes, MAX(t.started_at) AS last_upload,
               (SELECT u2.name FROM transfers t2 JOIN users u2 ON u2.id = t2.user_id
                JOIN files f2 ON f2.id = t2.file_id
                WHERE f2.folder = f.folder AND t2.status IN ('completed','uploading')
                ORDER BY t2.started_at DESC LIMIT 1) AS last_user
        {BASE} AND {DONE} AND f.id IS NOT NULL
        GROUP BY f.folder ORDER BY {order} LIMIT ?""", (limit,))


USER_SORTS = {
    "size": "bytes DESC, uploads DESC",
    "last_desc": "last_upload DESC",
    "last_asc": "last_upload ASC",
    "alpha": "user COLLATE NOCASE ASC",
    "uploads": "uploads DESC",
}


def users(conn: sqlite3.Connection, sort: str = "size", search: str | None = None) -> list[dict]:
    where, args = "", ()
    if search:
        where = """ AND (u.name LIKE ?1 OR EXISTS (
            SELECT 1 FROM transfers t3 JOIN files f3 ON f3.id = t3.file_id
            WHERE t3.user_id = u.id AND t3.status IN ('completed','uploading') AND f3.vpath LIKE ?1))"""
        args = (f"%{search}%",)
    return _rows(conn, f"""
        SELECT u.name AS user, COUNT(*) AS uploads, COALESCE(SUM(f.size), 0) AS bytes,
               MIN(t.started_at) AS first_upload, MAX(t.started_at) AS last_upload
        {BASE} AND {DONE} {where}
        GROUP BY u.id ORDER BY {USER_SORTS.get(sort, USER_SORTS['size'])}""", args)


def user_detail(conn: sqlite3.Connection, name: str) -> dict | None:
    totals = conn.execute(f"""
        SELECT u.name AS user, SUM({DONE}) AS uploads,
               COALESCE(SUM(CASE WHEN {DONE} THEN f.size END), 0) AS bytes,
               MIN(CASE WHEN {DONE} THEN t.started_at END) AS first_upload,
               MAX(CASE WHEN {DONE} THEN t.started_at END) AS last_upload,
               SUM(t.status IN ('failed','local_error')) AS failed,
               SUM(t.status = 'cancelled') AS cancelled,
               SUM(t.status = 'denied') AS denied,
               SUM(t.status = 'queued') AS queued,
               AVG(CASE WHEN t.status='completed' AND t.speed > 0 THEN t.speed END) AS avg_speed
        {BASE} AND u.name = ?""", (name,)).fetchone()
    if totals is None or totals["user"] is None:
        return None
    files = _rows(conn, f"""
        SELECT f.id AS file_id, f.vpath, f.folder, f.name, f.size, t.started_at AS time,
               t.speed, t.status
        {BASE} AND {DONE} AND u.name = ?
        ORDER BY t.started_at DESC, t.id DESC""", (name,))
    folders: dict[str, dict] = {}
    for fr in files:
        key = fr["folder"] or ""
        d = folders.setdefault(key, {"folder": fr["folder"], "uploads": 0, "bytes": 0,
                                     "last_upload": fr["time"], "files": []})
        d["uploads"] += 1
        d["bytes"] += fr["size"] or 0
        d["files"].append(fr)
    return {**dict(totals), "folders": list(folders.values())}


def folder_detail(conn: sqlite3.Connection, folder: str) -> dict | None:
    totals = conn.execute(f"""
        SELECT f.folder, COUNT(DISTINCT t.user_id) AS users, COUNT(*) AS uploads,
               COALESCE(SUM(f.size), 0) AS bytes, MIN(t.started_at) AS first_upload,
               MAX(t.started_at) AS last_upload
        {BASE} AND {DONE} AND f.folder = ?""", (folder,)).fetchone()
    if totals is None or totals["folder"] is None:
        return None
    by_user = _rows(conn, f"""
        SELECT u.name AS user, COUNT(*) AS uploads, MAX(t.started_at) AS last_upload
        {BASE} AND {DONE} AND f.folder = ?
        GROUP BY u.id ORDER BY last_upload DESC""", (folder,))
    by_file = _rows(conn, f"""
        SELECT f.id AS file_id, f.name, f.size, COUNT(*) AS uploads, MAX(t.started_at) AS last_upload
        {BASE} AND {DONE} AND f.folder = ?
        GROUP BY f.id ORDER BY f.name""", (folder,))
    return {**dict(totals), "last_user": by_user[0]["user"] if by_user else None,
            "by_user": by_user, "files": by_file}


def file_detail(conn: sqlite3.Connection, file_id: int) -> dict | None:
    f = conn.execute("SELECT id AS file_id, vpath, folder, name, size FROM files WHERE id=?",
                     (file_id,)).fetchone()
    if f is None:
        return None
    history = _rows(conn, f"""
        SELECT u.name AS user, t.status, t.reason, t.requested_at, t.started_at, t.ended_at, t.speed
        {BASE} AND t.file_id = ? ORDER BY {WHEN} DESC, t.id DESC""", (file_id,))
    done = [h for h in history if h["status"] in ("completed", "uploading")]
    return {**dict(f), "uploads": len(done), "users": len({h["user"] for h in done}),
            "last_user": done[0]["user"] if done else None,
            "last_upload": done[0]["started_at"] if done else None, "history": history}


def search(conn: sqlite3.Connection, q: str, limit: int = 50) -> dict:
    like = f"%{q}%"
    return {
        "users": _rows(conn, f"""
            SELECT u.name AS user, COUNT(*) AS uploads {BASE} AND {DONE} AND u.name LIKE ?
            GROUP BY u.id ORDER BY uploads DESC LIMIT ?""", (like, limit)),
        "folders": _rows(conn, f"""
            SELECT f.folder, COUNT(*) AS uploads {BASE} AND {DONE} AND f.folder LIKE ?
            GROUP BY f.folder ORDER BY uploads DESC LIMIT ?""", (like, limit)),
        "files": _rows(conn, f"""
            SELECT f.id AS file_id, f.name, f.folder, COUNT(*) AS uploads
            {BASE} AND {DONE} AND f.name LIKE ?
            GROUP BY f.id ORDER BY uploads DESC LIMIT ?""", (like, limit)),
    }


def recent(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    return _rows(conn, f"""
        SELECT u.name AS user, f.id AS file_id, f.name, f.folder, f.size, t.status, t.reason,
               {WHEN} AS time, t.ended_at, t.speed
        {BASE} AND t.status != 'queued' ORDER BY {WHEN} DESC, t.id DESC LIMIT ?""", (limit,))


BUCKETS = {"day": "%Y-%m-%d", "week": "%Y-W%W", "month": "%Y-%m"}


def timeseries(conn: sqlite3.Connection, bucket: str = "day", since: str | None = None) -> list[dict]:
    fmt = BUCKETS.get(bucket, BUCKETS["day"])
    return _rows(conn, f"""
        SELECT strftime('{fmt}', t.started_at) AS bucket, COUNT(*) AS uploads,
               COALESCE(SUM(f.size), 0) AS bytes, COUNT(DISTINCT t.user_id) AS users,
               AVG(CASE WHEN t.status='completed' AND t.speed > 0 THEN t.speed END) AS avg_speed
        {BASE} AND {DONE} AND t.started_at >= ? GROUP BY bucket ORDER BY bucket""", (since or "",))


def heatmap(conn: sqlite3.Connection, since: str | None = None) -> list[dict]:
    """Uploads by weekday (0=Sunday) and hour."""
    return _rows(conn, f"""
        SELECT CAST(strftime('%w', t.started_at) AS INTEGER) AS weekday,
               CAST(strftime('%H', t.started_at) AS INTEGER) AS hour, COUNT(*) AS uploads
        {BASE} AND {DONE} AND t.started_at >= ? GROUP BY weekday, hour""", (since or "",))


def _reason_key(reason: str) -> str:
    # "Seeking to starting offset 7809910 failed" -> "Seeking to starting offset N failed"
    return re.sub(r"\d+", "N", reason or "")


def outcomes(conn: sqlite3.Connection, limit: int = 25) -> dict:
    counts: dict[tuple[str, str], int] = {}
    for r in conn.execute(f"""
            SELECT t.status, COALESCE(t.reason, '') AS reason, COUNT(*) AS count
            {BASE} AND t.status IN ('failed','local_error','cancelled','denied')
            GROUP BY t.status, reason"""):
        key = (r["status"], _reason_key(r["reason"]))
        counts[key] = counts.get(key, 0) + r["count"]
    by_reason = [{"status": s, "reason": reason, "count": c}
                 for (s, reason), c in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]]
    problem_files = _rows(conn, f"""
        SELECT f.id AS file_id, f.name, f.folder, COUNT(*) AS failures,
               COUNT(DISTINCT t.user_id) AS users
        {BASE} AND t.status IN ('failed','local_error') AND f.id IS NOT NULL
        GROUP BY f.id ORDER BY failures DESC LIMIT ?""", (limit,))
    return {"by_reason": by_reason, "problem_files": problem_files}


def unknown_sizes(conn: sqlite3.Connection, limit: int = 25) -> dict:
    """Completed uploads that don't add to the size totals, split by why."""
    no_file = _rows(conn, f"""
        SELECT u.name AS user, COUNT(*) AS uploads, MIN(t.started_at) AS first, MAX(t.started_at) AS last
        {BASE} AND {DONE} AND t.file_id IS NULL
        GROUP BY u.id ORDER BY uploads DESC""")
    not_found = _rows(conn, f"""
        SELECT f.id AS file_id, f.name, f.folder, COUNT(*) AS uploads, f.size_checked_at AS checked_at
        {BASE} AND {DONE} AND f.id IS NOT NULL AND f.size IS NULL
        GROUP BY f.id ORDER BY uploads DESC, f.name""")
    return {
        "unknown_file": {"uploads": sum(r["uploads"] for r in no_file), "by_user": no_file},
        "not_found": {"uploads": sum(r["uploads"] for r in not_found), "files": len(not_found),
                      "examples": not_found[:limit]},
    }


def hide(conn: sqlite3.Connection, kind: str, value: str) -> None:
    conn.execute("INSERT OR IGNORE INTO hidden(kind, value) VALUES(?, ?)", (kind, value))


def unhide(conn: sqlite3.Connection, kind: str, value: str) -> None:
    conn.execute("DELETE FROM hidden WHERE kind=? AND value=?", (kind, value))


def hidden(conn: sqlite3.Connection) -> list[dict]:
    return _rows(conn, "SELECT kind, value FROM hidden ORDER BY kind, value")

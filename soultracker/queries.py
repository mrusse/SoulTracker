"""Stats queries used by the API.

Totals and rankings come from the pre-computed agg_* tables (see aggregates.py), so they stay
fast with millions of uploads. Per-user/folder/file details read transfers through indexes.
Hidden users/folders/files ("Remove from stats") are excluded everywhere.
"""

from __future__ import annotations

import re
import sqlite3

from .db import CLIENTS

# SoulseekQt never logs a finish, so its started uploads count unless they fail. Nicotine+ logs every
# finish, so its uploads only count once they've actually finished.
DONE = "(t.status = 'completed' OR (t.status = 'uploading' AND t.client = 'soulseekqt'))"

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

def cf(client: str | None, alias: str = "t") -> str:
    """SQL condition limiting to one client (only known client names are ever inserted)."""
    return f" AND {alias}.client = '{client}'" if client in CLIENTS else ""


def base(client: str | None) -> str:
    return BASE + cf(client)


# time of an upload: when it started, or when it was requested (denied/cancelled/queued)
WHEN = "COALESCE(t.started_at, t.requested_at)"


def _key(client: str | None) -> str:
    """Key into the agg_* tables: one client, or '*' for all of them."""
    return client if client in CLIENTS else "*"


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def summary(conn: sqlite3.Connection, client: str | None = None) -> dict:
    key = _key(client)
    tot = conn.execute("SELECT * FROM agg_total WHERE client=?", (key,)).fetchone()
    s = {
        "uploads": tot["uploads"] if tot else 0,
        "bytes": tot["bytes"] if tot else 0,
        "uploads_unknown_size": tot["unknown_size"] if tot else 0,
        "avg_speed": (tot["speed_sum"] / tot["speed_n"]) if tot and tot["speed_n"] else None,
        "users": conn.execute("SELECT COUNT(*) FROM agg_user WHERE client=? AND uploads > 0", (key,)).fetchone()[0],
        "files": conn.execute("SELECT COUNT(*) FROM agg_file WHERE client=? AND uploads > 0", (key,)).fetchone()[0],
    }
    # outcome counts come straight off the (status) / (client, status) indexes
    counts = dict(conn.execute(
        "SELECT status, COUNT(*) FROM transfers t WHERE status IN ('failed','local_error','cancelled','denied','queued')"
        + cf(client) + " GROUP BY status").fetchall())
    s.update(failed=counts.get("failed", 0) + counts.get("local_error", 0), cancelled=counts.get("cancelled", 0),
             denied=counts.get("denied", 0), queued=counts.get("queued", 0))
    # two single-row index lookups (MIN and MAX in one query would scan the whole index)
    first = conn.execute(f"SELECT {WHEN} FROM transfers t WHERE {WHEN} IS NOT NULL{cf(client)} ORDER BY {WHEN} LIMIT 1").fetchone()
    last = conn.execute(f"SELECT {WHEN} FROM transfers t WHERE {WHEN} IS NOT NULL{cf(client)} ORDER BY {WHEN} DESC LIMIT 1").fetchone()
    s["first_seen"], s["last_seen"] = (first[0] if first else None), (last[0] if last else None)
    # newest counted upload: walk the start-time index backwards (unary + stops the planner
    # from using the status index instead, which would sort every completed upload)
    row = conn.execute(f"""
        SELECT u.name AS user, f.name AS file, f.folder, t.started_at AS time
        FROM transfers t INDEXED BY transfers_started_at
        JOIN users u ON u.id = t.user_id LEFT JOIN files f ON f.id = t.file_id
        WHERE t.started_at IS NOT NULL AND {VISIBLE} AND {DONE}{cf(client)}
        ORDER BY t.started_at DESC LIMIT 1""").fetchone()
    s["last_upload"] = dict(row) if row else None
    return s


def top_users(conn: sqlite3.Connection, by: str = "bytes", limit: int = 100, client: str | None = None) -> list[dict]:
    order = "a.bytes DESC, a.uploads DESC" if by == "bytes" else "a.uploads DESC, a.bytes DESC"
    return _rows(conn, f"""
        SELECT u.name AS user, a.uploads, a.bytes, a.last AS last_upload
        FROM agg_user a JOIN users u ON u.id = a.user_id
        WHERE a.client = ? AND a.uploads > 0 ORDER BY {order} LIMIT ?""", (_key(client), limit))


def top_folders(conn: sqlite3.Connection, by: str = "users", limit: int = 100, client: str | None = None) -> list[dict]:
    order = {"users": "a.users DESC, a.uploads DESC", "uploads": "a.uploads DESC, a.users DESC",
             "bytes": "a.bytes DESC"}.get(by, "a.users DESC, a.uploads DESC")
    return _rows(conn, f"""
        SELECT a.folder, a.users, a.uploads, a.bytes, a.last AS last_upload, u.name AS last_user
        FROM agg_folder a LEFT JOIN users u ON u.id = a.last_user_id
        WHERE a.client = ? AND a.uploads > 0 ORDER BY {order} LIMIT ?""", (_key(client), limit))


USER_SORTS = {
    "size": "a.bytes DESC, a.uploads DESC",
    "last_desc": "a.last DESC",
    "last_asc": "a.last ASC",
    "alpha": "u.name COLLATE NOCASE ASC",
    "uploads": "a.uploads DESC, a.bytes DESC",
}


def users(conn: sqlite3.Connection, sort: str = "size", search: str | None = None, client: str | None = None,
          limit: int = 5000) -> list[dict]:
    where, args = "", [_key(client)]
    if search:
        where = " AND u.name LIKE ?"
        args.append(f"%{search}%")
    return _rows(conn, f"""
        SELECT u.name AS user, a.uploads, a.bytes, a.last AS last_upload
        FROM agg_user a JOIN users u ON u.id = a.user_id
        WHERE a.client = ? AND a.uploads > 0 {where}
        ORDER BY {USER_SORTS.get(sort, USER_SORTS['size'])} LIMIT ?""", (*args, limit))


def user_detail(conn: sqlite3.Connection, name: str, client: str | None = None) -> dict | None:
    uid = conn.execute("SELECT id FROM users WHERE name=?", (name,)).fetchone()
    if uid is None:
        return None
    # look the user up by id so their uploads come straight off the (user_id, started_at) index
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
        {base(client)} AND t.user_id = ?""", (uid[0],)).fetchone()
    if totals is None or totals["user"] is None:
        return None
    files = _rows(conn, f"""
        SELECT t.id AS transfer_id, t.client, f.id AS file_id, f.vpath, f.folder, f.name, f.size, t.started_at AS time,
               t.speed, t.status
        {base(client)} AND {DONE} AND t.user_id = ?
        ORDER BY t.started_at DESC, t.id DESC""", (uid[0],))
    folders: dict[str, dict] = {}
    for fr in files:
        key = fr["folder"] or ""
        d = folders.setdefault(key, {"folder": fr["folder"], "uploads": 0, "bytes": 0,
                                     "last_upload": fr["time"], "files": []})
        d["uploads"] += 1
        d["bytes"] += fr["size"] or 0
        d["files"].append(fr)
    return {**dict(totals), "folders": list(folders.values())}


def folder_detail(conn: sqlite3.Connection, folder: str, client: str | None = None) -> dict | None:
    # one indexed pass (folder -> its files -> their uploads), summarized here
    rows = conn.execute(f"""
        SELECT u.name AS user, f.id AS file_id, f.name, f.size, t.started_at
        FROM files f JOIN transfers t ON t.file_id = f.id JOIN users u ON u.id = t.user_id
        WHERE f.folder = ? AND {VISIBLE} AND {DONE}{cf(client)}""", (folder,)).fetchall()
    if not rows:
        return None
    by_user: dict[str, dict] = {}
    by_file: dict[int, dict] = {}
    for r in rows:
        bu = by_user.setdefault(r["user"], {"user": r["user"], "uploads": 0, "last_upload": ""})
        bu["uploads"] += 1
        bu["last_upload"] = max(bu["last_upload"], r["started_at"] or "")
        bf = by_file.setdefault(r["file_id"], {"file_id": r["file_id"], "name": r["name"], "size": r["size"],
                                               "uploads": 0, "last_upload": ""})
        bf["uploads"] += 1
        bf["last_upload"] = max(bf["last_upload"], r["started_at"] or "")
    users_sorted = sorted(by_user.values(), key=lambda x: x["last_upload"], reverse=True)
    starts = [r["started_at"] for r in rows if r["started_at"]]
    return {
        "folder": folder, "users": len(by_user), "uploads": len(rows),
        "bytes": sum(r["size"] or 0 for r in rows),
        "first_upload": min(starts) if starts else None, "last_upload": max(starts) if starts else None,
        "last_user": users_sorted[0]["user"] if users_sorted else None,
        "by_user": users_sorted, "files": sorted(by_file.values(), key=lambda x: x["name"]),
    }


def file_detail(conn: sqlite3.Connection, file_id: int, client: str | None = None) -> dict | None:
    f = conn.execute("SELECT id AS file_id, vpath, folder, name, size FROM files WHERE id=?",
                     (file_id,)).fetchone()
    if f is None:
        return None
    history = _rows(conn, f"""
        SELECT u.name AS user, t.client, t.status, t.reason, t.requested_at, t.started_at, t.ended_at, t.speed
        {base(client)} AND t.file_id = ? ORDER BY {WHEN} DESC, t.id DESC""", (file_id,))
    done = [h for h in history if h["status"] == "completed" or (h["status"] == "uploading" and h["client"] == "soulseekqt")]
    return {**dict(f), "uploads": len(done), "users": len({h["user"] for h in done}),
            "last_user": done[0]["user"] if done else None,
            "last_upload": done[0]["started_at"] if done else None, "history": history}


def search(conn: sqlite3.Connection, q: str, limit: int = 50, client: str | None = None) -> dict:
    like, key = f"%{q}%", _key(client)
    return {
        "users": _rows(conn, """
            SELECT u.name AS user, a.uploads FROM agg_user a JOIN users u ON u.id = a.user_id
            WHERE a.client = ? AND a.uploads > 0 AND u.name LIKE ? ORDER BY a.uploads DESC LIMIT ?""", (key, like, limit)),
        "folders": _rows(conn, """
            SELECT a.folder, a.uploads FROM agg_folder a
            WHERE a.client = ? AND a.uploads > 0 AND a.folder LIKE ? ORDER BY a.uploads DESC LIMIT ?""", (key, like, limit)),
        "files": _rows(conn, """
            SELECT f.id AS file_id, f.name, f.folder, a.uploads FROM files f
            JOIN agg_file a ON a.file_id = f.id AND a.client = ? AND a.uploads > 0
            WHERE f.name LIKE ? ORDER BY a.uploads DESC LIMIT ?""", (key, like, limit)),
    }


def recent(conn: sqlite3.Connection, limit: int = 50, client: str | None = None) -> list[dict]:
    # ordered by the indexed expression COALESCE(started_at, requested_at), newest first
    return _rows(conn, f"""
        SELECT u.name AS user, t.client, f.id AS file_id, f.name, f.folder, f.size, t.status, t.reason,
               {WHEN} AS time, t.ended_at, t.speed
        {base(client)} AND t.status != 'queued' ORDER BY {WHEN} DESC, t.id DESC LIMIT ?""", (limit,))


BUCKETS = {"day": "%Y-%m-%d", "week": "%Y-W%W", "month": "%Y-%m"}


def timeseries(conn: sqlite3.Connection, bucket: str = "day", since: str | None = None, client: str | None = None) -> list[dict]:
    fmt = BUCKETS.get(bucket, BUCKETS["day"])
    key, start = _key(client), (since or "")[:10]
    rows = _rows(conn, f"""
        SELECT strftime('{fmt}', day) AS bucket, SUM(uploads) AS uploads, SUM(bytes) AS bytes,
               SUM(speed_sum) AS speed_sum, SUM(speed_n) AS speed_n
        FROM agg_day WHERE client = ? AND day >= ? AND uploads > 0 GROUP BY bucket ORDER BY bucket""", (key, start))
    users = dict(conn.execute(f"""
        SELECT strftime('{fmt}', day) AS bucket, COUNT(DISTINCT user_id) FROM agg_day_user
        WHERE client = ? AND day >= ? AND n > 0 GROUP BY bucket""", (key, start)).fetchall())
    for r in rows:
        r["users"] = users.get(r["bucket"], 0)
        r["avg_speed"] = r.pop("speed_sum") / r["speed_n"] if r["speed_n"] else None
        r.pop("speed_n")
    return rows


def heatmap(conn: sqlite3.Connection, since: str | None = None, client: str | None = None) -> list[dict]:
    """Uploads by weekday (0=Sunday) and hour."""
    return _rows(conn, """
        SELECT CAST(strftime('%w', day) AS INTEGER) AS weekday, hour, SUM(uploads) AS uploads
        FROM agg_hour WHERE client = ? AND day >= ? AND uploads > 0
        GROUP BY weekday, hour""", (_key(client), (since or "")[:10]))


def _reason_key(reason: str) -> str:
    # "Seeking to starting offset 7809910 failed" -> "Seeking to starting offset N failed"
    return re.sub(r"\d+", "N", reason or "")


def outcomes(conn: sqlite3.Connection, limit: int = 25, client: str | None = None) -> dict:
    counts: dict[tuple[str, str], int] = {}
    for r in conn.execute(f"""
            SELECT t.status, COALESCE(t.reason, '') AS reason, COUNT(*) AS count
            {base(client)} AND t.status IN ('failed','local_error','cancelled','denied')
            GROUP BY t.status, reason"""):
        key = (r["status"], _reason_key(r["reason"]))
        counts[key] = counts.get(key, 0) + r["count"]
    by_reason = [{"status": s, "reason": reason, "count": c}
                 for (s, reason), c in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]]
    problem_files = _rows(conn, f"""
        SELECT f.id AS file_id, f.name, f.folder, COUNT(*) AS failures,
               COUNT(DISTINCT t.user_id) AS users
        {base(client)} AND t.status IN ('failed','local_error') AND f.id IS NOT NULL
        GROUP BY f.id ORDER BY failures DESC LIMIT ?""", (limit,))
    return {"by_reason": by_reason, "problem_files": problem_files}


def unknown_sizes(conn: sqlite3.Connection, limit: int = 25, client: str | None = None) -> dict:
    """Completed uploads that don't add to the size totals, split by why."""
    no_file = _rows(conn, f"""
        SELECT u.name AS user, COUNT(*) AS uploads, MIN(t.started_at) AS first, MAX(t.started_at) AS last
        {base(client)} AND {DONE} AND t.file_id IS NULL
        GROUP BY u.id ORDER BY uploads DESC""")
    # start from the (few) files without a size, not from every upload
    not_found = _rows(conn, f"""
        SELECT f.id AS file_id, f.name, f.folder, COUNT(*) AS uploads, f.size_checked_at AS checked_at
        FROM files f JOIN transfers t ON t.file_id = f.id JOIN users u ON u.id = t.user_id
        WHERE f.size IS NULL AND {VISIBLE} AND {DONE}{cf(client)}
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


# ---- shuffle ----

SHUFFLE_EXT = (".mp3", ".flac", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".wav")  # = media.PLAYABLE
_PLAYABLE_SQL = "(" + " OR ".join(f"lower(f.name) LIKE '%{e}'" for e in SHUFFLE_EXT) + ")"
_FILE_VISIBLE = """f.folder NOT IN (SELECT value FROM hidden WHERE kind='folder')
    AND f.vpath NOT IN (SELECT value FROM hidden WHERE kind='file')"""
_NOT_EXCLUDED = "f.id NOT IN (SELECT value FROM json_each(?))"


def shuffle(conn: sqlite3.Connection, mode: str = "recent", users: int = 10, since: str | None = None,
            exclude: list[int] | None = None, count: int = 5) -> list[dict]:
    """Random playable songs from the users & files tree (hidden users/folders/files left out).

    mode "recent": pick one of the `users` most recent downloaders at random, then one of their
    songs, so a user who took 3,000 files doesn't drown out the rest. mode "random": any song.
    `since` limits it to songs uploaded from then on; `exclude` = file ids not to pick (recently
    played / already queued). Returns fewer than `count` (maybe none) when the pool runs out."""
    import json
    import random
    excl = json.dumps([int(x) for x in (exclude or [])])
    count = max(1, min(count, 50))
    if mode == "random":
        # with whoever downloaded it last, so the player can link to it in their branch
        return _rows(conn, f"""
            SELECT f.id, f.name, f.folder, (
                SELECT u.name FROM transfers t JOIN users u ON u.id = t.user_id
                WHERE t.file_id = f.id AND {DONE} AND u.name NOT IN (SELECT value FROM hidden WHERE kind='user')
                ORDER BY t.started_at DESC LIMIT 1) AS user
            FROM agg_file a JOIN files f ON f.id = a.file_id
            WHERE a.client = '*' AND a.uploads > 0 {"AND a.last >= ?" if since else ""}
              AND {_PLAYABLE_SQL} AND {_FILE_VISIBLE} AND {_NOT_EXCLUDED}
            ORDER BY RANDOM() LIMIT ?""", ((since,) if since else ()) + (excl, count))

    names = dict(conn.execute(
        "SELECT a.user_id, u.name FROM agg_user a JOIN users u ON u.id = a.user_id "
        "WHERE a.client = '*' AND a.uploads > 0 ORDER BY a.last DESC LIMIT ?", (max(1, min(users, 1000)),)).fetchall())
    pool = list(names)
    out: list[dict] = []
    taken: set[int] = set()
    cache: dict[int, list[dict]] = {}   # per user: a few random songs, drawn from as needed
    while pool and len(out) < count:
        uid = random.choice(pool)
        songs = cache.get(uid)
        if songs is None:
            songs = cache[uid] = _rows(conn, f"""
                SELECT f.id, f.name, f.folder FROM (
                    SELECT DISTINCT t.file_id FROM transfers t
                    WHERE t.user_id = ? AND {DONE} {"AND t.started_at >= ?" if since else ""}) x
                JOIN files f ON f.id = x.file_id
                WHERE {_PLAYABLE_SQL} AND {_FILE_VISIBLE} AND {_NOT_EXCLUDED}
                ORDER BY RANDOM() LIMIT ?""", (uid,) + ((since,) if since else ()) + (excl, count))
        while songs and songs[0]["id"] in taken:
            songs.pop(0)
        if not songs:
            pool.remove(uid)   # nothing (left) to play from this user
            continue
        s = {**songs.pop(0), "user": names[uid]}
        taken.add(s["id"])
        out.append(s)
    return out

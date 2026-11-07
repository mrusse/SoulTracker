"""Pre-computed totals, so the dashboard and charts stay fast with millions of uploads.

Every stats page used to aggregate the whole transfers table on every request. Instead, these
agg_* tables keep running totals per user, folder, file, day and hour, for each client and for
all clients together (client '*'). SQLite triggers on `transfers` keep them up to date whenever
any code path inserts, updates or deletes an upload, so they can't drift from the data.

An upload counts if it's "done" (SoulseekQt: started and not failed; Nicotine+: finished) and
none of its user/folder/file is hidden ("Remove from stats").

Bulk work (a big history import, `soultracker rebuild`) pauses the triggers and calls rebuild()
afterwards, which recomputes everything in a few set-based queries. Hiding/unhiding items and
sizes found later (files that weren't on the shares at first) also mark the totals for a rebuild.
"""

from __future__ import annotations

import sqlite3

DONE = "(t.status = 'completed' OR (t.status = 'uploading' AND t.client = 'soulseekqt'))"

TABLES = """
CREATE TABLE agg_state (id INTEGER PRIMARY KEY CHECK (id = 1), paused INTEGER NOT NULL DEFAULT 0);
INSERT INTO agg_state(id, paused) VALUES (1, 0);

CREATE TABLE agg_total (
    client TEXT PRIMARY KEY, uploads INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0,
    unknown_size INTEGER NOT NULL DEFAULT 0, speed_sum INTEGER NOT NULL DEFAULT 0, speed_n INTEGER NOT NULL DEFAULT 0);
CREATE TABLE agg_user (
    client TEXT NOT NULL, user_id INTEGER NOT NULL, uploads INTEGER NOT NULL DEFAULT 0,
    bytes INTEGER NOT NULL DEFAULT 0, last TEXT, PRIMARY KEY (client, user_id));
CREATE INDEX agg_user_bytes ON agg_user(client, bytes);
CREATE INDEX agg_user_uploads ON agg_user(client, uploads);
CREATE INDEX agg_user_last ON agg_user(client, last);
CREATE TABLE agg_file (
    client TEXT NOT NULL, file_id INTEGER NOT NULL, uploads INTEGER NOT NULL DEFAULT 0, last TEXT,
    PRIMARY KEY (client, file_id));
CREATE TABLE agg_folder (
    client TEXT NOT NULL, folder TEXT NOT NULL, uploads INTEGER NOT NULL DEFAULT 0,
    bytes INTEGER NOT NULL DEFAULT 0, users INTEGER NOT NULL DEFAULT 0, last TEXT, last_user_id INTEGER,
    PRIMARY KEY (client, folder));
CREATE INDEX agg_folder_users ON agg_folder(client, users, uploads);
CREATE INDEX agg_folder_uploads ON agg_folder(client, uploads);
CREATE INDEX agg_folder_bytes ON agg_folder(client, bytes);
CREATE TABLE agg_folder_user (
    client TEXT NOT NULL, folder TEXT NOT NULL, user_id INTEGER NOT NULL, n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (client, folder, user_id));
CREATE TABLE agg_day (
    client TEXT NOT NULL, day TEXT NOT NULL, uploads INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0,
    users INTEGER NOT NULL DEFAULT 0, speed_sum INTEGER NOT NULL DEFAULT 0, speed_n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (client, day));
CREATE TABLE agg_day_user (
    client TEXT NOT NULL, day TEXT NOT NULL, user_id INTEGER NOT NULL, n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (client, day, user_id));
CREATE TABLE agg_hour (
    client TEXT NOT NULL, day TEXT NOT NULL, hour INTEGER NOT NULL, uploads INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (client, day, hour));

-- for "recent activity" and first/last seen
CREATE INDEX transfers_when ON transfers(COALESCE(started_at, requested_at));
CREATE INDEX transfers_client_when ON transfers(client, COALESCE(started_at, requested_at));
CREATE INDEX transfers_started_at ON transfers(started_at);
-- the few files whose size isn't known (Diagnostics lists them)
CREATE INDEX files_no_size ON files(id) WHERE size IS NULL;
"""

AGG_TABLES = ["agg_total", "agg_user", "agg_file", "agg_folder", "agg_folder_user", "agg_day", "agg_day_user", "agg_hour"]


def _counts(r: str) -> str:
    """SQL: does row r (NEW/OLD) count toward the totals right now?"""
    return f"""(({r}.status = 'completed' OR ({r}.status = 'uploading' AND {r}.client = 'soulseekqt'))
        AND {r}.started_at IS NOT NULL
        AND (SELECT paused FROM agg_state WHERE id = 1) = 0
        AND NOT EXISTS (SELECT 1 FROM users u JOIN hidden h ON h.kind = 'user' AND h.value = u.name WHERE u.id = {r}.user_id)
        AND NOT EXISTS (SELECT 1 FROM files f JOIN hidden h ON (h.kind = 'folder' AND h.value = f.folder)
                                                         OR (h.kind = 'file' AND h.value = f.vpath)
                        WHERE f.id = {r}.file_id))"""


def _apply(r: str, s: int) -> str:
    """SQL statements adding (s=1) or removing (s=-1) row r's contribution, for its client and '*'."""
    k = f"(SELECT {r}.client AS c UNION ALL SELECT '*' AS c) k"
    size = f"(SELECT size FROM files WHERE id = {r}.file_id)"
    folder = f"(SELECT folder FROM files WHERE id = {r}.file_id)"
    day = f"date({r}.started_at)"
    clients = f"client IN ({r}.client, '*')"
    match_client = "(%s.client = '*' OR t.client = %s.client)"
    speed = f"COALESCE({r}.speed, 0)"
    stmts = [
        f"""INSERT INTO agg_total(client, uploads, bytes, unknown_size, speed_sum, speed_n)
            SELECT c, {s}, {s} * COALESCE({size}, 0), {s} * ({size} IS NULL), {s} * {speed}, {s} * ({speed} > 0) FROM {k} WHERE true
            ON CONFLICT(client) DO UPDATE SET uploads = uploads + excluded.uploads, bytes = bytes + excluded.bytes,
              unknown_size = unknown_size + excluded.unknown_size, speed_sum = speed_sum + excluded.speed_sum,
              speed_n = speed_n + excluded.speed_n""",
        f"""INSERT INTO agg_user(client, user_id, uploads, bytes, last)
            SELECT c, {r}.user_id, {s}, {s} * COALESCE({size}, 0), {r}.started_at FROM {k} WHERE true
            ON CONFLICT(client, user_id) DO UPDATE SET uploads = uploads + excluded.uploads,
              bytes = bytes + excluded.bytes, last = MAX(COALESCE(last, ''), excluded.last)""",
        f"""INSERT INTO agg_day(client, day, uploads, bytes, speed_sum, speed_n)
            SELECT c, {day}, {s}, {s} * COALESCE({size}, 0), {s} * {speed}, {s} * ({speed} > 0) FROM {k} WHERE true
            ON CONFLICT(client, day) DO UPDATE SET uploads = uploads + excluded.uploads, bytes = bytes + excluded.bytes,
              speed_sum = speed_sum + excluded.speed_sum, speed_n = speed_n + excluded.speed_n""",
        f"""INSERT INTO agg_day_user(client, day, user_id, n) SELECT c, {day}, {r}.user_id, {s} FROM {k} WHERE true
            ON CONFLICT(client, day, user_id) DO UPDATE SET n = n + excluded.n""",
        f"""UPDATE agg_day SET users = users {'+' if s > 0 else '-'} 1 WHERE day = {day} AND {clients}
            AND (SELECT n FROM agg_day_user p WHERE p.client = agg_day.client AND p.day = agg_day.day
                 AND p.user_id = {r}.user_id) = {1 if s > 0 else 0}""",
        f"""INSERT INTO agg_hour(client, day, hour, uploads)
            SELECT c, {day}, CAST(strftime('%H', {r}.started_at) AS INTEGER), {s} FROM {k} WHERE true
            ON CONFLICT(client, day, hour) DO UPDATE SET uploads = uploads + excluded.uploads""",
        # file and folder (only when the file is known)
        f"""INSERT INTO agg_file(client, file_id, uploads, last)
            SELECT c, {r}.file_id, {s}, {r}.started_at FROM {k} WHERE {r}.file_id IS NOT NULL
            ON CONFLICT(client, file_id) DO UPDATE SET uploads = uploads + excluded.uploads,
              last = MAX(COALESCE(last, ''), excluded.last)""",
        f"""INSERT INTO agg_folder(client, folder, uploads, bytes, last, last_user_id)
            SELECT c, {folder}, {s}, {s} * COALESCE({size}, 0), {r}.started_at, {r}.user_id FROM {k}
            WHERE {r}.file_id IS NOT NULL
            ON CONFLICT(client, folder) DO UPDATE SET uploads = uploads + excluded.uploads, bytes = bytes + excluded.bytes,
              last_user_id = CASE WHEN excluded.last >= COALESCE(last, '') THEN excluded.last_user_id ELSE last_user_id END,
              last = MAX(COALESCE(last, ''), excluded.last)""",
        f"""INSERT INTO agg_folder_user(client, folder, user_id, n) SELECT c, {folder}, {r}.user_id, {s} FROM {k}
            WHERE {r}.file_id IS NOT NULL
            ON CONFLICT(client, folder, user_id) DO UPDATE SET n = n + excluded.n""",
        f"""UPDATE agg_folder SET users = users {'+' if s > 0 else '-'} 1 WHERE folder = {folder} AND {clients}
            AND (SELECT n FROM agg_folder_user p WHERE p.client = agg_folder.client AND p.folder = agg_folder.folder
                 AND p.user_id = {r}.user_id) = {1 if s > 0 else 0}""",
    ]
    if s < 0:  # a removed upload may have been the latest one: recompute "last" from what's left
        stmts += [
            f"""UPDATE agg_user SET last = (SELECT MAX(t.started_at) FROM transfers t WHERE t.user_id = {r}.user_id
                  AND {DONE} AND {match_client % ('agg_user', 'agg_user')})
                WHERE user_id = {r}.user_id AND {clients}""",
            f"""UPDATE agg_file SET last = (SELECT MAX(t.started_at) FROM transfers t WHERE t.file_id = {r}.file_id
                  AND {DONE} AND {match_client % ('agg_file', 'agg_file')})
                WHERE file_id = {r}.file_id AND {clients}""",
            f"""UPDATE agg_folder SET (last, last_user_id) = (
                  SELECT t.started_at, t.user_id FROM files f JOIN transfers t ON t.file_id = f.id
                  WHERE f.folder = agg_folder.folder AND {DONE} AND {match_client % ('agg_folder', 'agg_folder')}
                  ORDER BY t.started_at DESC LIMIT 1)
                WHERE folder = {folder} AND {clients}""",
        ]
    return ";\n".join(stmts) + ";"


CHANGED = """(OLD.status IS NOT NEW.status OR OLD.speed IS NOT NEW.speed OR OLD.started_at IS NOT NEW.started_at
    OR OLD.file_id IS NOT NEW.file_id OR OLD.user_id IS NOT NEW.user_id OR OLD.client IS NOT NEW.client)"""


def _triggers() -> str:
    return f"""
CREATE TRIGGER agg_ins AFTER INSERT ON transfers WHEN {_counts('NEW')}
BEGIN {_apply('NEW', 1)} END;
CREATE TRIGGER agg_del AFTER DELETE ON transfers WHEN {_counts('OLD')}
BEGIN {_apply('OLD', -1)} END;
CREATE TRIGGER agg_upd_old AFTER UPDATE ON transfers WHEN {CHANGED} AND {_counts('OLD')}
BEGIN {_apply('OLD', -1)} END;
CREATE TRIGGER agg_upd_new AFTER UPDATE ON transfers WHEN {CHANGED} AND {_counts('NEW')}
BEGIN {_apply('NEW', 1)} END;
"""


def install(conn: sqlite3.Connection) -> None:
    conn.executescript(TABLES)
    conn.executescript(_triggers())


def set_paused(conn: sqlite3.Connection, paused: bool) -> None:
    conn.execute("UPDATE agg_state SET paused = ? WHERE id = 1", (1 if paused else 0,))


def is_paused(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("SELECT paused FROM agg_state WHERE id = 1").fetchone()[0])


def rebuild(conn: sqlite3.Connection) -> None:
    """Recompute every total from the transfers table (set-based; fine for millions of rows)."""
    for t in AGG_TABLES:
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DROP TABLE IF EXISTS temp.agg_src")
    conn.execute(f"""
        CREATE TEMP TABLE agg_src AS
        SELECT t.client AS client, t.user_id, t.file_id, f.folder, f.size, t.started_at, COALESCE(t.speed, 0) AS speed
        FROM transfers t
        JOIN users u ON u.id = t.user_id
        LEFT JOIN files f ON f.id = t.file_id
        WHERE {DONE} AND t.started_at IS NOT NULL
          AND u.name NOT IN (SELECT value FROM hidden WHERE kind = 'user')
          AND (f.id IS NULL OR (f.folder NOT IN (SELECT value FROM hidden WHERE kind = 'folder')
                                AND f.vpath NOT IN (SELECT value FROM hidden WHERE kind = 'file')))""")
    conn.execute("INSERT INTO agg_src SELECT '*', user_id, file_id, folder, size, started_at, speed FROM agg_src")
    conn.executescript("""
        INSERT INTO agg_total(client, uploads, bytes, unknown_size, speed_sum, speed_n)
          SELECT client, COUNT(*), SUM(COALESCE(size, 0)), SUM(size IS NULL), SUM(speed), SUM(speed > 0)
          FROM agg_src GROUP BY client;
        INSERT INTO agg_user(client, user_id, uploads, bytes, last)
          SELECT client, user_id, COUNT(*), SUM(COALESCE(size, 0)), MAX(started_at) FROM agg_src GROUP BY client, user_id;
        INSERT INTO agg_file(client, file_id, uploads, last)
          SELECT client, file_id, COUNT(*), MAX(started_at) FROM agg_src WHERE file_id IS NOT NULL GROUP BY client, file_id;
        INSERT INTO agg_folder(client, folder, uploads, bytes, users, last, last_user_id)
          SELECT client, folder, COUNT(*), SUM(COALESCE(size, 0)), COUNT(DISTINCT user_id), MAX(started_at), user_id
          FROM agg_src WHERE folder IS NOT NULL GROUP BY client, folder;
        INSERT INTO agg_folder_user(client, folder, user_id, n)
          SELECT client, folder, user_id, COUNT(*) FROM agg_src WHERE folder IS NOT NULL GROUP BY client, folder, user_id;
        INSERT INTO agg_day(client, day, uploads, bytes, users, speed_sum, speed_n)
          SELECT client, date(started_at), COUNT(*), SUM(COALESCE(size, 0)), COUNT(DISTINCT user_id), SUM(speed), SUM(speed > 0)
          FROM agg_src GROUP BY client, date(started_at);
        INSERT INTO agg_day_user(client, day, user_id, n)
          SELECT client, date(started_at), user_id, COUNT(*) FROM agg_src GROUP BY client, date(started_at), user_id;
        INSERT INTO agg_hour(client, day, hour, uploads)
          SELECT client, date(started_at), CAST(strftime('%H', started_at) AS INTEGER), COUNT(*)
          FROM agg_src GROUP BY client, date(started_at), CAST(strftime('%H', started_at) AS INTEGER);
        DROP TABLE temp.agg_src;
    """)


# ---- "these totals need a rebuild" (hidden items changed, sizes found late) ----

REBUILD_SYNC_LIMIT = 300_000  # below this many uploads a rebuild takes well under a second


def mark_dirty(conn: sqlite3.Connection) -> None:
    from .db import set_state
    set_state(conn, "agg_dirty", "1")


def refresh(conn: sqlite3.Connection, force: bool = False) -> bool:
    """Rebuild if marked dirty (or forced). Returns whether it rebuilt."""
    from .db import get_state, set_state
    if not force and get_state(conn, "agg_dirty") != "1":
        return False
    rebuild(conn)
    set_state(conn, "agg_dirty", None)
    conn.commit()
    return True


def refresh_soon(conn: sqlite3.Connection) -> None:
    """Rebuild now if that's quick, otherwise leave it for the background loop."""
    mark_dirty(conn)
    conn.commit()
    if conn.execute("SELECT COUNT(*) FROM transfers").fetchone()[0] <= REBUILD_SYNC_LIMIT:
        refresh(conn)

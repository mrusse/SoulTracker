"""SQLite storage.

transfers: one row per upload request/attempt. A request starts as 'queued'; when the client
starts uploading it, the row becomes 'uploading', then 'completed' / 'failed' / 'local_error'.
Requests can also end as 'denied' (queue limit etc.) or 'cancelled' (downloader declined).

An 'uploading' row that never gets a failure is a completed upload: the speed line that
normally confirms it can't always be matched to the right user (see CLAUDE.md), so stats
count status IN ('completed', 'uploading') as completed.

events: every parsed log event, so the transfers table can be rebuilt if the logic improves.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE users (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE
);

CREATE TABLE files (
    id               INTEGER PRIMARY KEY,
    vpath            TEXT NOT NULL UNIQUE,   -- @@token\\Folder\\...\\file as requested
    folder           TEXT NOT NULL,          -- vpath without the file name
    name             TEXT NOT NULL,          -- file name
    size             INTEGER,                -- bytes, from the mounted shares (NULL = unknown)
    size_checked_at  TEXT
);
CREATE INDEX files_folder ON files(folder);

CREATE TABLE transfers (
    id            INTEGER PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    file_id       INTEGER REFERENCES files(id),  -- NULL: started before its request was logged
    requested_at  TEXT,
    started_at    TEXT,
    ended_at      TEXT,
    status        TEXT NOT NULL,   -- queued|uploading|completed|failed|local_error|denied|cancelled
    reason        TEXT,
    speed         INTEGER          -- bytes/s
);
CREATE INDEX transfers_queue ON transfers(user_id, status, requested_at, id);
CREATE INDEX transfers_started ON transfers(user_id, started_at);
CREATE INDEX transfers_status_time ON transfers(status, started_at);
CREATE INDEX transfers_file ON transfers(file_id);

CREATE TABLE events (
    id      INTEGER PRIMARY KEY,
    ts      TEXT NOT NULL,
    kind    TEXT NOT NULL,
    user    TEXT,
    path    TEXT,
    detail  TEXT,
    value   INTEGER
);

-- "Remove selected" in the UI: hidden from all stats, survives rebuilds.
CREATE TABLE hidden (
    kind   TEXT NOT NULL,   -- user | folder | file
    value  TEXT NOT NULL,   -- user name | folder vpath | file vpath
    PRIMARY KEY (kind, value)
);

CREATE TABLE state (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
"""

# Upgrades for existing databases: version -> SQL that brings a database to that version.
MIGRATIONS = {
    2: """
CREATE TABLE scrobbles (
    id           INTEGER PRIMARY KEY,
    file_id      INTEGER,
    played_at    INTEGER NOT NULL,   -- unix time the track started playing
    artist       TEXT,
    title        TEXT,
    album        TEXT,
    albumartist  TEXT,
    duration     INTEGER,
    status       TEXT NOT NULL,      -- pending | sent | skipped
    error        TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX scrobbles_status ON scrobbles(status, played_at);
""",
}

CLIENTS = {"soulseekqt": "SoulseekQt", "nicotine": "Nicotine+"}


def norm_vpath(vpath: str) -> str:
    r"""Client-neutral path: SoulseekQt's "@@abcde\Music\x.mp3" -> "Music\x.mp3" (Nicotine+'s form),
    so a file uploaded by either client is the same file."""
    if vpath.startswith("@@"):
        _, sep, rest = vpath.partition("\\")
        return rest if sep else vpath
    return vpath


def _migrate_v3(conn: sqlite3.Connection) -> None:
    """Multiple clients: tag rows with their client and normalize file paths (merging duplicates)."""
    conn.execute("ALTER TABLE transfers ADD COLUMN client TEXT NOT NULL DEFAULT 'soulseekqt'")
    conn.execute("ALTER TABLE events ADD COLUMN client TEXT NOT NULL DEFAULT 'soulseekqt'")
    conn.execute("CREATE INDEX transfers_client ON transfers(client, status, started_at)")
    for fid, vpath in conn.execute("SELECT id, vpath FROM files WHERE vpath LIKE '@@%'").fetchall():
        nv = norm_vpath(vpath)
        other = conn.execute("SELECT id FROM files WHERE vpath=?", (nv,)).fetchone()
        if other:  # already there under its normalized name: keep one row
            conn.execute("UPDATE transfers SET file_id=? WHERE file_id=?", (other[0], fid))
            conn.execute("DELETE FROM files WHERE id=?", (fid,))
        else:
            folder, _, name = nv.rpartition("\\")
            conn.execute("UPDATE files SET vpath=?, folder=?, name=? WHERE id=?", (nv, folder, name, fid))
    for kind, value in conn.execute("SELECT kind, value FROM hidden WHERE kind IN ('folder','file')").fetchall():
        nv = norm_vpath(value)
        if nv != value:
            conn.execute("DELETE FROM hidden WHERE kind=? AND value=?", (kind, value))
            conn.execute("INSERT OR IGNORE INTO hidden(kind, value) VALUES(?, ?)", (kind, nv))


def _migrate_v4(conn: sqlite3.Connection) -> None:
    """Pre-computed totals (see aggregates.py), filled from the existing data."""
    from . import aggregates
    aggregates.install(conn)
    aggregates.rebuild(conn)


COMPLETED_STATUSES = ("completed", "uploading")
MIGRATIONS[3] = _migrate_v3
MIGRATIONS[4] = _migrate_v4


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(f"database schema v{version} is newer than this SoulTracker (v{SCHEMA_VERSION})")
    if version < SCHEMA_VERSION:
        if version == 0:
            conn.executescript(SCHEMA)
            version = 1
        for v in range(version + 1, SCHEMA_VERSION + 1):
            step = MIGRATIONS[v]
            if callable(step):
                step(conn)
            else:
                conn.executescript(step)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
    return conn


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    conn.execute("INSERT INTO state(key, value) VALUES(?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

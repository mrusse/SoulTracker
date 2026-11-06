"""Applies parsed events to the database.

The model (validated at 99.3% exact user+file against ground truth, see CLAUDE.md):
- Each user has a FIFO queue of requested files (transfers rows with status 'queued').
- "transfer accepted" for a user starts the oldest file in their queue.
- A start becomes failed if an abort/error/timeout names that file (matched case-insensitively,
  falling back to the user's latest start). Otherwise it is a completed upload.
- "Recording upload speed" has no user or file; it is credited to the most recently started
  user still uploading and just fills in the speed of their oldest running upload.
"""

from __future__ import annotations

import sqlite3
from collections import OrderedDict
from datetime import datetime

from . import parser as p
from .db import COMPLETED_STATUSES

LIVE = ("uploading", "completed")


def _ts(e: p.Event) -> str:
    return e.ts.strftime("%Y-%m-%d %H:%M:%S")


class Tracker:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._users: dict[str, int] = {}
        self._files: dict[str, int] = {}
        self._active: OrderedDict[str, None] = OrderedDict()  # users with an upload running
        self._last_cancel: dict[int, int] = {}                # user_id -> transfer id

    # ---- lookups ----

    def user_id(self, name: str) -> int:
        uid = self._users.get(name)
        if uid is None:
            self.conn.execute("INSERT OR IGNORE INTO users(name) VALUES(?)", (name,))
            uid = self.conn.execute("SELECT id FROM users WHERE name=?", (name,)).fetchone()[0]
            self._users[name] = uid
        return uid

    def file_id(self, vpath: str) -> int:
        fid = self._files.get(vpath)
        if fid is None:
            folder, _, name = vpath.rpartition("\\")
            self.conn.execute("INSERT OR IGNORE INTO files(vpath, folder, name) VALUES(?, ?, ?)",
                              (vpath, folder, name))
            fid = self.conn.execute("SELECT id FROM files WHERE vpath=?", (vpath,)).fetchone()[0]
            self._files[vpath] = fid
        return fid

    def _oldest_queued(self, uid: int) -> int | None:
        row = self.conn.execute(
            "SELECT id FROM transfers WHERE user_id=? AND status='queued' "
            "ORDER BY requested_at, id LIMIT 1", (uid,)).fetchone()
        return row[0] if row else None

    def _latest_start(self, uid: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT id, status, file_id FROM transfers WHERE user_id=? AND started_at IS NOT NULL "
            "ORDER BY started_at DESC, id DESC LIMIT 1", (uid,)).fetchone()

    # ---- event handling ----

    def apply(self, e: p.Event, store: bool = True) -> None:
        if store:
            self.conn.execute(
                "INSERT INTO events(ts, kind, user, path, detail, value) VALUES(?, ?, ?, ?, ?, ?)",
                (_ts(e), e.kind, e.user, e.path, e.detail, e.value))
        handler = getattr(self, "_on_" + e.kind, None)
        if handler:
            handler(e)

    def _on_client_start(self, e: p.Event) -> None:
        self._active.clear()

    def _on_queued(self, e: p.Event) -> None:
        uid, fid = self.user_id(e.user), self.file_id(e.path)
        exists = self.conn.execute(
            "SELECT 1 FROM transfers WHERE user_id=? AND file_id=? AND status='queued'",
            (uid, fid)).fetchone()
        if not exists:
            self.conn.execute(
                "INSERT INTO transfers(user_id, file_id, requested_at, status) VALUES(?, ?, ?, 'queued')",
                (uid, fid, _ts(e)))

    def _on_denied(self, e: p.Event) -> None:
        uid, fid = self.user_id(e.user), self.file_id(e.path)
        self.conn.execute(
            "INSERT INTO transfers(user_id, file_id, requested_at, ended_at, status, reason) "
            "VALUES(?, ?, ?, ?, 'denied', ?)", (uid, fid, _ts(e), _ts(e), e.detail))

    def _on_start(self, e: p.Event) -> None:
        uid = self.user_id(e.user)
        tid = self._oldest_queued(uid)
        if tid is not None:
            self.conn.execute("UPDATE transfers SET status='uploading', started_at=? WHERE id=?",
                              (_ts(e), tid))
        else:  # its request was before our history began
            self.conn.execute(
                "INSERT INTO transfers(user_id, started_at, status) VALUES(?, ?, 'uploading')",
                (uid, _ts(e)))
        self._active.pop(e.user, None)
        self._active[e.user] = None

    def _on_start_local_error(self, e: p.Event) -> None:
        row = self._latest_start(self.user_id(e.user))
        if row and row["status"] in LIVE:
            self.conn.execute(
                "UPDATE transfers SET status='local_error', ended_at=?, reason=? WHERE id=?",
                (_ts(e), "file could not be opened", row["id"]))
        self._active.pop(e.user, None)

    def _on_start_stale(self, e: p.Event) -> None:
        # The accept didn't actually start anything: undo the start.
        row = self._latest_start(self.user_id(e.user))
        if row and row["status"] in LIVE:
            if row["file_id"] is None:
                self.conn.execute("DELETE FROM transfers WHERE id=?", (row["id"],))
            else:
                self.conn.execute(
                    "UPDATE transfers SET status='queued', started_at=NULL WHERE id=?", (row["id"],))
        self._active.pop(e.user, None)

    def _on_cancelled(self, e: p.Event) -> None:
        uid = self.user_id(e.user)
        tid = self._oldest_queued(uid)
        if tid is not None:
            self.conn.execute(
                "UPDATE transfers SET status='cancelled', ended_at=?, reason=? WHERE id=?",
                (_ts(e), e.detail, tid))
            self._last_cancel[uid] = tid

    def _on_cancel_reason(self, e: p.Event) -> None:
        tid = self._last_cancel.pop(self.user_id(e.user), None)
        if tid is not None:
            self.conn.execute("UPDATE transfers SET reason=? WHERE id=?", (e.detail, tid))

    def _on_failed(self, e: p.Event) -> None:
        uid = self.user_id(e.user)
        name = (e.path or "").lower()
        rows = self.conn.execute(
            "SELECT t.id, t.status, f.name FROM transfers t LEFT JOIN files f ON f.id = t.file_id "
            "WHERE t.user_id=? AND t.started_at IS NOT NULL "
            "ORDER BY t.started_at DESC, t.id DESC LIMIT 50", (uid,)).fetchall()
        target = next((r["id"] for r in rows
                       if r["status"] in LIVE and (r["name"] or "").lower() == name), None)
        if target is None and rows and rows[0]["status"] in LIVE:
            target = rows[0]["id"]  # e.g. duplicate-capitalization files, or unknown file
        if target is not None:
            self.conn.execute(
                "UPDATE transfers SET status='failed', ended_at=?, reason=? WHERE id=?",
                (_ts(e), e.detail, target))
        self._active.pop(e.user, None)

    def _on_speed(self, e: p.Event) -> None:
        if not self._active:
            return
        user, _ = self._active.popitem(last=True)
        row = self.conn.execute(
            "SELECT id FROM transfers WHERE user_id=? AND status='uploading' "
            "ORDER BY started_at, id LIMIT 1", (self.user_id(user),)).fetchone()
        if row:
            self.conn.execute(
                "UPDATE transfers SET status='completed', ended_at=?, speed=? WHERE id=?",
                (_ts(e), e.value, row[0]))


def rebuild(conn: sqlite3.Connection) -> int:
    """Recompute transfers from the stored events (e.g. after improving the logic)."""
    conn.execute("DELETE FROM transfers")
    tracker = Tracker(conn)
    n = 0
    for row in conn.execute("SELECT ts, kind, user, path, detail, value FROM events ORDER BY id").fetchall():
        e = p.Event(datetime.strptime(row["ts"], "%Y-%m-%d %H:%M:%S"), row["kind"],
                    row["user"], row["path"], row["detail"], row["value"])
        tracker.apply(e, store=False)
        n += 1
    conn.commit()
    return n


__all__ = ["Tracker", "rebuild", "COMPLETED_STATUSES"]

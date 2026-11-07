"""Looks up file sizes on the shared folders mounted into the container.

Requests use virtual paths like  @@abcde\\Music\\Artist\\Album\\01 Track.flac
where "Music" is the name of a shared folder. SOULTRACKER_SHARES lists one or more
directories (comma separated); any folder named "Music" directly inside one of them is
used, and a listed directory can also be a shared folder itself. So mounting the parent of
all your shared folders at /shares is enough, or list each shared folder.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .db import norm_vpath

RECHECK_MISSING_AFTER = timedelta(days=1)


class Shares:
    def __init__(self, roots: list[str | Path]) -> None:
        self.roots = [Path(r) for r in roots if str(r).strip()]
        self._share_dirs: dict[str, Path] | None = None

    def _scan(self) -> dict[str, Path]:
        dirs: dict[str, Path] = {}
        # A listed directory can be a shared folder itself (matched by its own name, first)...
        for root in self.roots:
            if root.is_dir():
                dirs.setdefault(root.name.lower(), root)
        # ...or a folder that contains shared folders.
        for root in self.roots:
            try:
                for entry in os.scandir(root):
                    if entry.is_dir():
                        dirs.setdefault(entry.name.lower(), Path(entry.path))
            except OSError:
                pass
        return dirs

    def resolve(self, vpath: str) -> Path | None:
        """Virtual path -> real file, or None if it can't be found."""
        parts = norm_vpath(vpath).split(chr(92))  # first part = the shared folder's name
        if len(parts) < 2:
            return None
        if self._share_dirs is None:
            self._share_dirs = self._scan()
        base = self._share_dirs.get(parts[0].lower())
        if base is None:
            return None
        exact = base.joinpath(*parts[1:])
        if exact.is_file():
            return exact
        return _resolve_case_insensitive(base, parts[1:])

    def refresh(self) -> None:
        self._share_dirs = None

    def size_of(self, vpath: str) -> int | None:
        """Size of a file on the shares (None if it can't be found)."""
        real = self.resolve(vpath)
        try:
            return real.stat().st_size if real else None
        except OSError:
            return None

    def update_sizes(self, conn: sqlite3.Connection, limit: int = 500) -> int:
        """Fill in sizes for files not checked yet (or missing and due for a recheck).
        New files normally get their size when they're first seen (see Tracker); if a size turns
        up later for a file that's already been uploaded, the totals are marked for a rebuild."""
        if not self.roots:
            return 0
        cutoff = (datetime.now() - RECHECK_MISSING_AFTER).strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute(
            "SELECT id, vpath FROM files WHERE size_checked_at IS NULL "
            "OR (size IS NULL AND size_checked_at < ?) LIMIT ?", (cutoff, limit)).fetchall()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        late = False
        for fid, vpath in rows:
            size = self.size_of(vpath)
            conn.execute("UPDATE files SET size=?, size_checked_at=? WHERE id=?", (size, now, fid))
            if size is not None and not late:
                late = conn.execute("SELECT 1 FROM transfers WHERE file_id=? LIMIT 1", (fid,)).fetchone() is not None
        if late:
            from . import aggregates
            aggregates.mark_dirty(conn)
        return len(rows)


def _resolve_case_insensitive(base: Path, parts: list[str]) -> Path | None:
    cur = base
    for part in parts:
        try:
            names = {e.name.lower(): e.name for e in os.scandir(cur)}
        except OSError:
            return None
        real = names.get(part.lower())
        if real is None:
            return None
        cur = cur / real
    return cur if cur.is_file() else None

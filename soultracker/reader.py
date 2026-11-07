"""Follows transfers.log across rotations and restarts.

The hook rotates by renaming transfers.log to transfers.log.1 (replacing any older .1) and
starting a new transfers.log. We remember which file we were reading by a fingerprint of its
first bytes (inodes aren't reliable on Unraid's /mnt/user FUSE mounts) plus a byte offset, so:
- normal case: continue the current file from the saved offset;
- rotated since last time: finish the old file (now .1) from the offset, then read the new file;
- rotated twice (old file gone): read all of .1 and the new file.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Callable

from .db import get_state, set_state

FP_BYTES = 1024
STATE_KEY = "reader"


def _fingerprint(path: Path, length: int) -> str | None:
    try:
        with open(path, "rb") as f:
            head = f.read(length)
    except FileNotFoundError:
        return None
    if len(head) < length:
        return None
    return hashlib.sha1(head).hexdigest()


class LogFollower:
    def __init__(self, conn: sqlite3.Connection, path: str | Path,
                 on_line: Callable[[str], None]) -> None:
        self.conn = conn
        self.path = Path(path)
        self.old_path = self.path.with_name(self.path.name + ".1")
        self.on_line = on_line

    def _load(self) -> dict | None:
        raw = get_state(self.conn, STATE_KEY)
        return json.loads(raw) if raw else None

    def _save(self, path: Path, offset: int) -> None:
        length = min(FP_BYTES, offset)
        set_state(self.conn, STATE_KEY, json.dumps(
            {"fp": _fingerprint(path, length) if length else None, "fp_len": length, "offset": offset}))

    def _matches(self, path: Path, st: dict) -> bool:
        if not st["fp_len"]:
            return True  # nothing read yet from that file; any file is "it"
        return _fingerprint(path, st["fp_len"]) == st["fp"]

    def _read(self, path: Path, offset: int, to_end: bool) -> int:
        """Feed complete lines from offset; returns the new offset. If to_end, also feeds a
        trailing partial line (used for a rotated file that will never grow again)."""
        with open(path, "rb") as f:
            f.seek(offset)
            buf = f.read()
        if not buf:
            return offset
        end = len(buf) if to_end else buf.rfind(b"\n") + 1
        for raw in buf[:end].splitlines():
            self.on_line(raw.decode("utf-8", errors="replace"))
        return offset + end

    def backlog(self) -> int:
        """Roughly how many bytes are still unread (current file, plus the rest of .1 after a rotation)."""
        if not self.path.exists():
            return 0
        size = os.path.getsize(self.path)
        old = os.path.getsize(self.old_path) if self.old_path.exists() else 0
        st = self._load()
        if st is None:
            return size + old
        if self._matches(self.path, st) and size >= st["offset"]:
            return size - st["offset"]
        if self.old_path.exists() and self._matches(self.old_path, st):
            old = max(0, old - st["offset"])
        return old + size

    def poll(self) -> None:
        """Read whatever is new. Call repeatedly (e.g. every few seconds)."""
        if not self.path.exists():
            return
        st = self._load()
        if st is None:
            # First run: backfill the older file too.
            if self.old_path.exists():
                self._read(self.old_path, 0, to_end=True)
            offset = 0
        elif self._matches(self.path, st) and os.path.getsize(self.path) >= st["offset"]:
            offset = st["offset"]
        else:
            # Rotated. Finish the old file if it's the one we were reading.
            if self.old_path.exists():
                if self._matches(self.old_path, st):
                    self._read(self.old_path, st["offset"], to_end=True)
                else:
                    self._read(self.old_path, 0, to_end=True)
            offset = 0
        offset = self._read(self.path, offset, to_end=False)
        self._save(self.path, offset)

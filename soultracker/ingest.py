"""Glue: log follower -> parser -> tracker -> database."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .parser import LogParser
from .reader import LogFollower
from .tracker import Tracker


BULK = 20 * 1024 * 1024


class Ingestor:
    def __init__(self, conn: sqlite3.Connection, log_path: str | Path, size_of=None) -> None:
        self.conn = conn
        self.parser = LogParser()
        self.tracker = Tracker(conn, size_of)
        self.follower = LogFollower(conn, log_path, self._line)
        self.lines = 0
        self.events = 0

    def _line(self, line: str) -> None:
        self.lines += 1
        e = self.parser.feed(line)
        if e is not None:
            self.tracker.apply(e)
            self.events += 1

    def poll(self) -> None:
        """Read what's new. A big backlog (first start, or after being offline) is read with the
        pre-computed totals paused, then they're rebuilt once, which is far faster."""
        from . import aggregates
        bulk = self.follower.backlog() > BULK
        with self.conn:  # one transaction per poll
            if bulk:
                aggregates.set_paused(self.conn, True)
            self.follower.poll()
            if bulk:
                aggregates.set_paused(self.conn, False)
                aggregates.rebuild(self.conn)

    def run_forever(self, interval: float = 2.0) -> None:
        while True:
            self.poll()
            time.sleep(interval)

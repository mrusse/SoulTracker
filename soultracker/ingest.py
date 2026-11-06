"""Glue: log follower -> parser -> tracker -> database."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .parser import LogParser
from .reader import LogFollower
from .tracker import Tracker


class Ingestor:
    def __init__(self, conn: sqlite3.Connection, log_path: str | Path) -> None:
        self.conn = conn
        self.parser = LogParser()
        self.tracker = Tracker(conn)
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
        with self.conn:  # one transaction per poll
            self.follower.poll()

    def run_forever(self, interval: float = 2.0) -> None:
        while True:
            self.poll()
            time.sleep(interval)

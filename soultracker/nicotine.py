"""Nicotine+ upload logs.

Nicotine+ (Settings > Logging > "Log transfers") writes one file per session into its
logs/transfers folder, named uploads_YYYY-MM-DD_HH-MM-SS.log, with three kinds of lines:

    03/11/26 12:10:56 Upload started: user PillowFort, IP address None, file Music (320)\\A\\B\\08 x.mp3
    03/11/26 12:10:58 Upload finished: user PillowFort, IP address ('86.21.7.222', 50300), file Music (320)\\...
    19/07/24 14:38:01 Upload aborted, user someone file Music (320)\\...

Unlike SoulseekQt, every upload's outcome is logged with its user and file, so no queue model
is needed. Not logged: requests, queue-limit denials and cancels before an upload starts.

The timestamp is Nicotine+'s "%x %X" (locale dependent): the date order (day/month/year,
month/day/year or year/month/day) is worked out per file from the date in its file name.
The files are written readable only by Nicotine+'s user; SoulTracker runs as root, so that's fine.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from .db import get_state, norm_vpath, set_state
from .parser import Event

CLIENT = "nicotine"
STATE_KEY = "nicotine_reader"
FILE_RE = re.compile(r"^uploads_(\d{4})-(\d\d)-(\d\d)_\d\d-\d\d-\d\d\.log$")

# "<date> <time> Upload started: user U, IP address <ip>, file F" (finished is the same)
START_FINISH_RE = re.compile(
    r"^(?P<date>\S+) (?P<time>\d{1,2}:\d\d:\d\d(?: ?[AaPp]\.?[Mm]\.?)?) Upload (?P<kind>started|finished): "
    r"user (?P<user>.+?), IP address (?:None|\(.*?\)|\S+), file (?P<file>.+)$")
# "<date> <time> Upload aborted, user U file F" (no comma between user and file)
ABORT_RE = re.compile(
    r"^(?P<date>\S+) (?P<time>\d{1,2}:\d\d:\d\d(?: ?[AaPp]\.?[Mm]\.?)?) Upload aborted, user (?P<user>.+?) file (?P<file>.+)$")

STARTED, FINISHED, ABORTED = "nic_started", "nic_finished", "nic_aborted"


def _date_orders(date: str) -> dict[str, tuple[int, int, int]]:
    """Possible (year, month, day) readings of a date like 03/11/26, 11/3/2026 or 2026-11-03."""
    nums = [int(x) for x in re.split(r"\D+", date) if x]
    if len(nums) != 3:
        return {}
    a, b, c = nums
    year = lambda y: y + 2000 if y < 100 else y
    out = {}
    if a > 31:                           # year first
        out["ymd"] = (year(a), b, c)
    else:
        out["dmy"] = (year(c), b, a)
        out["mdy"] = (year(c), a, b)
    return {k: v for k, v in out.items() if 1 <= v[1] <= 12 and 1 <= v[2] <= 31}


def guess_order(first_date: str, file_date: tuple[int, int, int]) -> str:
    """Pick the date order whose reading of the file's first line is closest to (and not before)
    the date in the file name: the session started then, so its first line is that day or soon after."""
    best, best_gap = "dmy", None
    fd = datetime(*file_date)
    for order, (y, m, d) in _date_orders(first_date).items():
        try:
            gap = (datetime(y, m, d) - fd).days
        except ValueError:
            continue
        if gap >= 0 and (best_gap is None or gap < best_gap):
            best, best_gap = order, gap
    return best


def parse_time(date: str, time_: str, order: str) -> datetime | None:
    ymd = _date_orders(date).get(order)
    if not ymd:
        return None
    t = time_.strip().upper().replace(".", "")
    try:
        if t.endswith(("AM", "PM")):
            clock = datetime.strptime(t.replace(" ", ""), "%I:%M:%S%p")
        else:
            clock = datetime.strptime(t, "%H:%M:%S")
        return datetime(*ymd, clock.hour, clock.minute, clock.second)
    except ValueError:
        return None


def parse_line(line: str, order: str) -> Event | None:
    line = line.rstrip("\r\n")
    if " Upload " not in line:
        return None
    m = START_FINISH_RE.match(line) or ABORT_RE.match(line)
    if not m:
        return None
    ts = parse_time(m["date"], m["time"], order)
    if ts is None:
        return None
    kind = {"started": STARTED, "finished": FINISHED}.get(m.groupdict().get("kind") or "", ABORTED)
    return Event(ts, kind, user=m["user"], path=m["file"])


class NicotineTracker:
    """Applies Nicotine+ events: started -> uploading, finished -> completed, aborted -> failed."""

    def __init__(self, conn: sqlite3.Connection, tracker) -> None:
        self.conn = conn
        self.t = tracker  # the SoulseekQt Tracker, for its user/file lookups

    def apply(self, e: Event) -> None:
        # Nicotine+ lines aren't kept in `events`: each one maps straight onto an upload row, so
        # there's nothing to re-derive later, and with years of history the copy would be huge.
        ts = e.ts.strftime("%Y-%m-%d %H:%M:%S")
        uid, fid = self.t.user_id(e.user), self.t.file_id(norm_vpath(e.path))
        if e.kind == STARTED:
            self.conn.execute(
                "INSERT INTO transfers(user_id, file_id, requested_at, started_at, status, client) "
                "VALUES(?, ?, ?, ?, 'uploading', ?)", (uid, fid, ts, ts, CLIENT))
            return
        row = self.conn.execute(
            "SELECT id, started_at FROM transfers WHERE client=? AND user_id=? AND file_id=? AND status='uploading' "
            "ORDER BY started_at DESC, id DESC LIMIT 1", (CLIENT, uid, fid)).fetchone()
        if e.kind == FINISHED:
            speed = None
            if row:
                secs = (e.ts - datetime.strptime(row["started_at"], "%Y-%m-%d %H:%M:%S")).total_seconds()
                size = self.conn.execute("SELECT size FROM files WHERE id=?", (fid,)).fetchone()[0]
                speed = int(size / max(secs, 1)) if size else None
                self.conn.execute("UPDATE transfers SET status='completed', ended_at=?, speed=? WHERE id=?",
                                  (ts, speed, row["id"]))
            else:  # started before tracking began
                self.conn.execute(
                    "INSERT INTO transfers(user_id, file_id, started_at, ended_at, status, client) "
                    "VALUES(?, ?, ?, ?, 'completed', ?)", (uid, fid, ts, ts, CLIENT))
        elif e.kind == ABORTED and row:
            self.conn.execute("UPDATE transfers SET status='failed', ended_at=?, reason='aborted' WHERE id=?",
                              (ts, row["id"]))


CHUNK = 8 * 1024 * 1024        # bytes read per poll while catching up, so the app stays responsive
BULK = 20 * 1024 * 1024        # more than this to read = pause the totals and rebuild them at the end


class NicotineFollower:
    """Follows every uploads_*.log in Nicotine+'s transfers folder. Files only ever grow, and each
    session gets a new file, so the state is just "how far into each file we've read".

    The first time it sees the folder it imports the existing files (history), unless
    import_history is off, in which case it starts at their end. Big catch-ups (history, or a
    long time offline) are read a chunk per poll with the totals paused, then rebuilt once."""

    def __init__(self, conn: sqlite3.Connection, folder: str | Path, tracker: NicotineTracker,
                 import_history: bool = True) -> None:
        self.conn, self.folder, self.tracker = conn, Path(folder), tracker
        self.import_history = import_history
        self.lines = 0
        self.events = 0

    def _files(self) -> list[tuple[str, tuple[int, int, int]]]:
        out = []
        try:
            for name in os.listdir(self.folder):
                m = FILE_RE.match(name)
                if m:
                    out.append((name, (int(m[1]), int(m[2]), int(m[3]))))
        except OSError:
            return []
        return sorted(out)

    def poll(self) -> None:
        from . import aggregates
        raw = get_state(self.conn, STATE_KEY)
        st = json.loads(raw) if raw else None
        files = self._files()
        if st is None:
            if self.import_history:
                st = {"offsets": {}, "orders": {}}
            else:  # only follow what happens from now on
                st = {"offsets": {n: _size(self.folder / n) for n, _ in files}, "orders": {}}
                set_state(self.conn, STATE_KEY, json.dumps(st))
                return
        offsets, orders = st.setdefault("offsets", {}), st.setdefault("orders", {})

        pending = sum(max(0, _size(self.folder / n) - offsets.get(n, 0)) for n, _ in files)
        if pending > BULK and not st.get("catching_up"):
            st["catching_up"] = True
            aggregates.set_paused(self.conn, True)

        budget = CHUNK if st.get("catching_up") else None
        for name, fdate in files:
            if budget is not None and budget <= 0:
                break
            path = self.folder / name
            offset = offsets.get(name, 0)
            try:
                size = os.path.getsize(path)
                if size <= offset:
                    continue
                with open(path, "rb") as f:
                    f.seek(offset)
                    buf = f.read(budget) if budget is not None else f.read()
            except OSError:
                continue
            end = buf.rfind(b"\n") + 1
            if not end:
                continue
            if budget is not None:
                budget -= end
            for raw_line in buf[:end].decode("utf-8", errors="replace").splitlines():
                self.lines += 1
                if name not in orders:
                    m = START_FINISH_RE.match(raw_line) or ABORT_RE.match(raw_line)
                    if not m:
                        continue
                    orders[name] = guess_order(m["date"], fdate)
                e = parse_line(raw_line, orders[name])
                if e is not None:
                    self.tracker.apply(e)
                    self.events += 1
            offsets[name] = offset + end

        present = {n for n, _ in files}
        st["offsets"] = {n: o for n, o in offsets.items() if n in present}
        st["orders"] = {n: o for n, o in orders.items() if n in present}
        # Done once what's left fits in one ordinary read (a line still being written can't hold it up).
        caught_up = sum(max(0, _size(self.folder / n) - offsets.get(n, 0)) for n, _ in files) < CHUNK
        if st.get("catching_up") and caught_up:
            st["catching_up"] = False
            aggregates.set_paused(self.conn, False)
            aggregates.rebuild(self.conn)
        set_state(self.conn, STATE_KEY, json.dumps(st))


def catch_up_progress(conn: sqlite3.Connection, folder: str | Path) -> dict | None:
    """While a big catch-up (e.g. the history import) runs: {"done": bytes read, "total": bytes}."""
    raw = get_state(conn, STATE_KEY)
    st = json.loads(raw) if raw else None
    if not st or not st.get("catching_up"):
        return None
    folder = Path(folder)
    sizes = {n: _size(folder / n) for n in os.listdir(folder) if FILE_RE.match(n)}
    return {"done": sum(min(st["offsets"].get(n, 0), sz) for n, sz in sizes.items()), "total": sum(sizes.values())}


def _size(path: Path) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0

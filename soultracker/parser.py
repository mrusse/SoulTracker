"""Turns transfers.log lines into upload events.

The client writes a top-level line, sometimes followed by indented sub-step lines that only
make sense together with it (e.g. a request followed by "Queued" or "Denied..."), so the
parser keeps a little context between lines. See CLAUDE.md for the message catalog.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

# "[Thu Nov 5 17:53:15 2026] message" — day not zero-padded; sub-steps start with 3 spaces.
LINE_RE = re.compile(r"^\[\w{3} (\w{3}) +(\d{1,2}) (\d\d):(\d\d):(\d\d) (\d{4})\] ?(.*)$")

REQUEST_RE = re.compile(r"^Queue upload requested by (.+) for file (@@.+)$")
ACCEPTED_RE = re.compile(r"^Received file transfer response from user (.+) for req \d+, transfer accepted$")
NOT_ACCEPTED_RE = re.compile(r"^Received file transfer response from user (.+) for req \d+, transfer not accepted$")
SPEED_RE = re.compile(r"^Recording upload speed of (\d+)$")
ABORTED_RE = re.compile(r"^Upload of (.+) to (.+) aborted, sending upload failed notification\.?$")
TIMED_OUT_RE = re.compile(r"^Upload of (.+) to (.+) timed out in (\w+) phase")
# The real path ends in an extension; matching up to it avoids being fooled by " to " inside
# the error text ("Seeking to starting offset ...").
ERROR_RE = re.compile(r"^Error uploading (.+?\.\w{1,6}) to (.+?)\. (.*)$")
CANCEL_REASON_RE = re.compile(r"^Upload was not accepted, marking denied\. Reason: (.*)$")


@dataclass(slots=True)
class Event:
    ts: datetime
    kind: str
    user: str | None = None
    path: str | None = None    # virtual path for requests, file name or real path for failures
    detail: str | None = None
    value: int | None = None


# Event kinds
CLIENT_START = "client_start"        # client (re)started
QUEUED = "queued"                    # request accepted into the queue (detail: re-queue reason)
DENIED = "denied"                    # request denied (detail: reason)
START = "start"                      # upload of the user's next queued file is starting
START_LOCAL_ERROR = "start_local_error"  # ...but the file couldn't be opened
START_STALE = "start_stale"          # ...actually nothing started (stale accept)
CANCELLED = "cancelled"              # downloader declined the transfer
CANCEL_REASON = "cancel_reason"      # reason for the preceding CANCELLED (detail)
FAILED = "failed"                    # upload aborted/errored/timed out (path: file, detail: reason)
SPEED = "speed"                      # an upload finished at this speed (bytes/s); no user/file


def parse_timestamp(m: re.Match) -> datetime:
    return datetime(int(m[6]), MONTHS[m[1]], int(m[2]), int(m[3]), int(m[4]), int(m[5]))


def basename(path: str) -> str:
    return re.split(r"[\\/]", path)[-1]


class LogParser:
    def __init__(self) -> None:
        self._request: tuple[str, str] | None = None   # (user, vpath) awaiting its sub-step
        self._accepted: str | None = None              # user whose accept may get a sub-step
        self._declined: str | None = None              # user whose decline may get a reason

    def feed(self, line: str) -> Event | None:
        line = line.rstrip("\r\n")
        # SLSK_HOOK_VERBOSE prefix: "epoch_ms<TAB>hook<TAB>widget<TAB>text"
        if "\t" in line and line[:1].isdigit():
            line = line.split("\t", 3)[-1]
        m = LINE_RE.match(line)
        if not m:
            return None
        text = m[7]
        if text.startswith("   "):
            return self._substep(parse_timestamp(m), text.strip())

        self._request = self._accepted = self._declined = None
        c = text[:1]
        # Cheap first-character filter: almost every line is noise.
        if c not in "QRUED":
            return None
        ts = parse_timestamp(m)
        if c == "Q":
            if (x := REQUEST_RE.match(text)):
                self._request = (x[1], x[2])
        elif c == "R":
            if text.startswith("Recording upload speed"):
                if (x := SPEED_RE.match(text)):
                    return Event(ts, SPEED, value=int(x[1]))
            elif text.startswith("Received file transfer response"):
                if (x := ACCEPTED_RE.match(text)):
                    self._accepted = x[1]
                    return Event(ts, START, user=x[1])
                if (x := NOT_ACCEPTED_RE.match(text)):
                    self._declined = x[1]
                    return Event(ts, CANCELLED, user=x[1], detail="not accepted")
        elif c == "U":
            if text.startswith("Upload of "):
                if (x := ABORTED_RE.match(text)):
                    return Event(ts, FAILED, user=x[2], path=x[1], detail="aborted")
                if (x := TIMED_OUT_RE.match(text)):
                    return Event(ts, FAILED, user=x[2], path=x[1], detail=f"timed out in {x[3]} phase")
        elif c == "E":
            if text.startswith("Error uploading "):
                if (x := ERROR_RE.match(text)):
                    return Event(ts, FAILED, user=x[2], path=basename(x[1]), detail=x[3])
        elif c == "D":
            if text == "Data loaded successfully.":
                return Event(ts, CLIENT_START)
        return None

    def _substep(self, ts: datetime, step: str) -> Event | None:
        if self._request:
            user, vpath = self._request
            self._request = None
            if step == "Queued":
                return Event(ts, QUEUED, user=user, path=vpath)
            if step.endswith(", re-queueing"):
                return Event(ts, QUEUED, user=user, path=vpath, detail=step[: -len(", re-queueing")])
            if step.startswith("Denied"):
                return Event(ts, DENIED, user=user, path=vpath, detail=step)
            return None  # "Already queued" etc.
        if self._accepted:
            user = self._accepted
            if step.startswith("File could not be opened"):
                self._accepted = None
                return Event(ts, START_LOCAL_ERROR, user=user)
            if step.startswith("Upload no longer in requesting phase"):
                self._accepted = None
                return Event(ts, START_STALE, user=user)
            return None  # "Found upload", "Requesting file connection to user"
        if self._declined:
            if (x := CANCEL_REASON_RE.match(step)):
                user, self._declined = self._declined, None
                return Event(ts, CANCEL_REASON, user=user, detail=x[1])
        return None

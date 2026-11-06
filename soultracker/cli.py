"""Command line: `soultracker ingest`, `soultracker rebuild`."""

from __future__ import annotations

import argparse
import time

from .db import connect
from .ingest import Ingestor
from .tracker import rebuild


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="soultracker")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="read transfers.log into the database")
    ing.add_argument("--log", required=True, help="path to transfers.log")
    ing.add_argument("--db", required=True, help="path to the SQLite database")
    ing.add_argument("--follow", action="store_true", help="keep following the log")

    reb = sub.add_parser("rebuild", help="recompute transfers from stored events")
    reb.add_argument("--db", required=True)

    args = ap.parse_args(argv)
    conn = connect(args.db)
    if args.cmd == "ingest":
        ing = Ingestor(conn, args.log)
        t0 = time.time()
        ing.poll()
        print(f"read {ing.lines} lines, {ing.events} events in {time.time() - t0:.1f}s")
        if args.follow:
            ing.run_forever()
    elif args.cmd == "rebuild":
        print(f"replayed {rebuild(conn)} events")


if __name__ == "__main__":
    main()

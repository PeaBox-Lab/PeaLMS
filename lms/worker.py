"""Scheduled worker entry point for durable LMS jobs.

Run ``python -m lms.worker --once`` from cron or ``--poll-seconds`` for a
long-running worker. Jobs are claimed atomically, so multiple worker processes
can share the same SQLite database safely.
"""

from __future__ import annotations

import argparse
import json
import time

from . import jobs
from .config import get_settings
from .db import init_db
from .security import configure_logging


def run_once(batch_size: int = 10) -> list[dict]:
    return jobs.run_once(batch_size=batch_size)


def main(argv=None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    parser = argparse.ArgumentParser(description="Corporate LMS job worker")
    parser.add_argument("--once", action="store_true",
                        help="Drain one batch and exit")
    parser.add_argument("--poll-seconds", type=int, default=30,
                        help="Idle delay between batches (default: 30)")
    parser.add_argument("--batch-size", type=int, default=10,
                        help="Maximum jobs per batch (default: 10)")
    parser.add_argument("--worker-id", default="",
                        help="Stable worker identifier for job lock auditing")
    args = parser.parse_args(argv)
    if args.poll_seconds < 1 or args.batch_size < 1:
        parser.error("--poll-seconds and --batch-size must be positive")

    init_db()
    if args.once:
        print(json.dumps(run_once(args.batch_size), indent=2))
        return 0

    print(f"Corporate LMS worker running (poll={args.poll_seconds}s)")
    try:
        while True:
            results = jobs.run_once(args.worker_id, batch_size=args.batch_size)
            if not results:
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run a command and report wall time and peak RSS of the child process.

Useful where /usr/bin/time is unavailable:

    python3 tools/measure.py -- python3 -m dicttool build --src big.jsonl \
        --out big.mdawg --mem-budget 33554432
"""

import resource
import subprocess
import sys
import time


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        print("usage: measure.py -- CMD [ARGS...]", file=sys.stderr)
        return 2
    t0 = time.monotonic()
    proc = subprocess.run(argv)
    wall = time.monotonic() - t0
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    print(f"[measure] exit={proc.returncode} wall={wall:.2f}s "
          f"maxrss={ru.ru_maxrss / 1024:.1f}MiB "
          f"user={ru.ru_utime:.2f}s sys={ru.ru_stime:.2f}s", file=sys.stderr)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())

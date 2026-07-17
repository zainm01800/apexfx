"""Small fcntl-based advisory file lock, shared by the parquet store and the
trial ledger (D-H3 / D-H4).

A lock is a ``<file>.lock`` sidecar next to the data file. Holders serialise
load->merge->save so two processes cannot interleave a read-modify-write and
lose each other's updates (or tear the file). Locks are advisory: every writer
in this repo goes through these helpers, which is what makes them effective.

POSIX only (the engine runs on macOS/Linux). On platforms without ``fcntl``
the lock degrades to a no-op with one logged warning — writes stay atomic
(tmp + os.replace), only cross-process serialisation is lost.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None
    logger.warning("fcntl unavailable: file_lock() is a no-op on this platform")


@contextmanager
def file_lock(path: str | Path):
    """Exclusive advisory lock on ``<path>.lock`` for the duration of the block.

    Never open the same lock path twice in one thread (flock on a second fd of
    the same file deadlocks): callers structure code so nested acquisition
    cannot happen (public entry points lock; internal helpers do not).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lock_path = p.parent / (p.name + ".lock")
    fh = open(lock_path, "a+b")
    try:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield lock_path
    finally:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()

"""
batch_lock.py
=============

The machine-wide lock that keeps two unattended runs (run_batch.py) from
overlapping: they would share the same cores, memory and disk, and could
start the same recon-all twice.

It is an operating-system file lock (flock), not a "lock file exists" flag, so
a run that crashes or is killed releases it automatically: there is never a
stale lock to delete by hand. The file itself only says who holds it.

Standard library only, with no project import: the student menu uses it too,
to warn before starting a long step while a batch is running.
"""

import json
import os
import time

try:
    import fcntl
except ImportError:          # Windows: no flock, but msvcrt has an equivalent
    fcntl = None
    import msvcrt


def _try_lock(handle):
    try:
        if fcntl:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def acquire(path, info, wait_seconds=0, poll_seconds=20):
    """
    Take the lock and write `info` (a dict) into the file. Returns the open
    file, which must stay open for as long as the lock is needed (closing it,
    or the process ending, releases it). Returns None if another process still
    holds it after `wait_seconds`.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    deadline = time.time() + wait_seconds
    while not _try_lock(handle):
        if time.time() >= deadline:
            handle.close()
            return None
        time.sleep(max(0.1, min(poll_seconds, deadline - time.time())))
    handle.seek(0)
    handle.truncate()
    json.dump(info, handle)
    handle.flush()
    return handle


def holder(path):
    """What the last holder wrote ({} if unreadable). Only meaningful while
    is_held(path) is True."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def is_held(path):
    """True if a run holds the lock right now."""
    if not os.path.isfile(path):
        return False
    with open(path, "a+", encoding="utf-8") as handle:
        return not _try_lock(handle)

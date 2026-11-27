"""
heavy_lock.py
=============

ONE heavy analysis at a time on the whole machine (recon-all, SAMSEG,
SynthSeg...), whatever started it: the nightly batch, the student menu, a
script run by hand, another project or another clone of the repository.

FreeSurfer 8 asks for ~32 GB of RAM per recon-all, so two at once on a 64 GB
Mac Mini push it into swap or get processes killed. The recon-all mode is
NEVER changed to save memory (that would change the method in the middle of a
longitudinal study): when the machine does not have the RAM, this module only
refuses and reports.

HOW IT WORKS
  * A fixed folder for the whole machine (not inside a clone):
        macOS  /Users/Shared/pipeline-neuroimagem/
        other  <temp>/pipeline-neuroimagem/
    ($PIPELINE_HEAVY_LOCK_DIR overrides it; the self-tests use that.)
  * heavy.lock: an OS file lock (flock). A crashed or killed run releases it
    by itself, so there is never a stale lock to delete by hand.
  * queue/: one ticket per waiting run, named by arrival time. Runs start in
    order of arrival (first come, first served); a ticket whose process died
    is dropped.
  * Before starting, the RAM is checked: total RAM below what the analysis
    needs -> refused and reported (InsufficientRAM); free RAM below it -> the
    run keeps its turn and waits (up to --mem-wait), then is reported
    (NotEnoughFreeMemory).
  * Every run is recorded (one TSV row): when it queued, waited, started and
    ended, its duration and the peak memory of its largest process.

    python code/heavy_lock.py status      # who runs now, who is waiting

Standard library only and no project import, so every script (and the
student menu) can use it.
"""

import contextlib
import csv
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime

try:
    import fcntl
except ImportError:          # Windows: no flock (the pipeline runs on macOS)
    fcntl = None

GB = 1024 ** 3

# RAM each kind of analysis needs (GB). recon-all: what FreeSurfer 8 asks for.
NEED_GB = {"recon-all": 32.0, "samseg": 16.0, "synthseg": 16.0}
# Kept for macOS and the user's own apps on top of the analysis.
SYSTEM_RESERVE_GB = 4.0

RECORD_FIELDS = ["queued", "started", "finished", "project", "kind", "item",
                 "threads", "need_gb", "ram_total_gb", "ram_free_gb_at_start",
                 "waited_s", "duration_s", "peak_rss_gb", "result", "host"]


class InsufficientRAM(RuntimeError):
    """The machine has less RAM in total than the analysis needs."""


class NotEnoughFreeMemory(RuntimeError):
    """The machine has enough RAM, but not enough was free in time."""


# ---------------------------------------------------------------------------
# where, and the machine
# ---------------------------------------------------------------------------

def lock_dir():
    env = os.environ.get("PIPELINE_HEAVY_LOCK_DIR")
    if env:
        return env
    if sys.platform == "darwin":
        return "/Users/Shared/pipeline-neuroimagem"
    return os.path.join(tempfile.gettempdir(), "pipeline-neuroimagem")


def _poll_seconds():
    return float(os.environ.get("PIPELINE_HEAVY_POLL", "15"))


def _sysctl(key):
    try:
        out = subprocess.run(["sysctl", "-n", key], capture_output=True,
                             text=True, timeout=10).stdout.strip()
        return int(out) if out.isdigit() else None
    except Exception:
        return None


def total_ram_gb():
    """Physical RAM in GB ($PIPELINE_FAKE_RAM_GB overrides, for the tests)."""
    if os.environ.get("PIPELINE_FAKE_RAM_GB"):
        return float(os.environ["PIPELINE_FAKE_RAM_GB"])
    ram = _sysctl("hw.memsize")
    if ram is None:
        try:
            ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (ValueError, OSError, AttributeError):
            return 0.0
    return ram / GB


def free_ram_gb():
    """RAM that can be given to a new process now, in GB: on macOS the free,
    inactive, speculative and purgeable pages of vm_stat; on Linux
    MemAvailable. ($PIPELINE_FAKE_FREE_GB overrides, for the tests.)"""
    if os.environ.get("PIPELINE_FAKE_FREE_GB"):
        return float(os.environ["PIPELINE_FAKE_FREE_GB"])
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["vm_stat"], capture_output=True, text=True,
                                 timeout=10).stdout
        except Exception:
            return 0.0
        page = 4096
        pages = 0
        for line in out.splitlines():
            if "page size of" in line:
                page = int("".join(c for c in line.split("page size of")[1]
                                   if c.isdigit()) or page)
            for key in ("Pages free", "Pages inactive", "Pages speculative",
                        "Pages purgeable"):
                if line.startswith(key + ":"):
                    pages += int(line.split(":")[1].strip().rstrip("."))
        return pages * page / GB
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024 / GB
    except OSError:
        pass
    return 0.0


def performance_cores():
    return _sysctl("hw.perflevel0.physicalcpu") or os.cpu_count() or 4


def max_threads(ram_gb=None, cores=None):
    """Upper bound for the threads of ONE heavy run, from the RAM and the
    performance cores. It only caps what a script asks for; it never raises
    it (more threads would change nothing in memory but could change
    run-to-run reproducibility, which stays a human decision)."""
    ram_gb = total_ram_gb() if ram_gb is None else ram_gb
    cores = performance_cores() if cores is None else cores
    if ram_gb >= 64:
        cap = 8
    elif ram_gb >= 48:
        cap = 6
    elif ram_gb >= 32:
        cap = 4
    else:
        cap = 2
    return max(1, min(cap, cores))


# ---------------------------------------------------------------------------
# the queue
# ---------------------------------------------------------------------------

def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:          # alive, owned by another user
        return True
    except (OSError, TypeError, ValueError):
        return False
    return True


def _tickets(queue):
    """Live tickets in arrival order (dead ones are removed)."""
    live = []
    for name in sorted(os.listdir(queue)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(queue, name)
        try:
            with open(path, encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            continue                  # being written
        if _alive(info.get("pid")):
            live.append((name, info))
        else:
            with contextlib.suppress(OSError):
                os.remove(path)
    return live


def _try_lock(handle):
    if fcntl is None:
        return True
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _ensure_dirs(base):
    queue = os.path.join(base, "queue")
    os.makedirs(queue, exist_ok=True)
    # Shared by every user account of the machine (batch, students).
    for path in (base, queue):
        with contextlib.suppress(OSError):
            os.chmod(path, 0o1777)
    return queue


def record(path, row):
    """Append one run to a TSV (created with its header)."""
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    new = not os.path.isfile(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RECORD_FIELDS, delimiter="\t",
                                extrasaction="ignore", lineterminator="\n")
        if new:
            writer.writeheader()
        writer.writerow(row)


class Slot:
    """What a run gets when its turn comes: fill .result / .peak_rss_gb."""

    def __init__(self, row):
        self.row = row
        self.result = "ok"
        self.peak_rss_gb = None


@contextlib.contextmanager
def slot(kind, item, project="", threads="", need_gb=None, record_path=None,
         mem_wait_minutes=60, announce=print):
    """
    Wait for this run's turn on the machine, then hold it:

        with heavy_lock.slot("recon-all", "sub-X_ses-1", project="CBR",
                             record_path=...) as s:
            ... run it ...

    Raises InsufficientRAM (machine too small: nothing ran) or
    NotEnoughFreeMemory (free RAM did not come back in time: nothing ran).
    Both are written to record_path too.
    """
    need = NEED_GB.get(kind, 16.0) if need_gb is None else float(need_gb)
    ram = total_ram_gb()
    queued = datetime.now()
    row = {"queued": queued.isoformat(timespec="seconds"), "started": "",
           "finished": "", "project": project, "kind": kind, "item": item,
           "threads": threads, "need_gb": f"{need:g}",
           "ram_total_gb": f"{ram:.1f}", "ram_free_gb_at_start": "",
           "waited_s": "", "duration_s": "", "peak_rss_gb": "", "result": "",
           "host": socket.gethostname().split(".")[0]}

    if ram < need + SYSTEM_RESERVE_GB:
        row["result"] = "refused: insufficient RAM"
        record(record_path, row)
        raise InsufficientRAM(
            f"{kind} {item}: this machine has {ram:.0f} GB of RAM and {kind} needs "
            f"~{need:g} GB plus {SYSTEM_RESERVE_GB:g} GB for the system. Not run; "
            "the recon-all mode is never changed to save memory.")

    base = lock_dir()
    queue = _ensure_dirs(base)
    ticket = os.path.join(queue, f"{time.time_ns():020d}-{os.getpid()}-"
                                 f"{abs(hash((kind, item))) % 10**6:06d}.json")
    with open(ticket, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "kind": kind, "item": item,
                   "project": project, "queued": row["queued"]}, f)

    lock_path = os.path.join(base, "heavy.lock")
    handle = None
    told = None
    mem_deadline = None
    try:
        while True:
            live = _tickets(queue)
            names = [n for n, _ in live]
            mine = os.path.basename(ticket)
            if mine not in names:            # removed by someone: write it back
                with open(ticket, "w", encoding="utf-8") as f:
                    json.dump({"pid": os.getpid(), "kind": kind, "item": item,
                               "project": project, "queued": row["queued"]}, f)
                continue
            if names[0] == mine:
                if handle is None:
                    handle = open(lock_path, "a+", encoding="utf-8")
                    with contextlib.suppress(OSError):
                        os.chmod(lock_path, 0o666)
                    if not _try_lock(handle):
                        handle.close()
                        handle = None
                if handle is not None:
                    free = free_ram_gb()
                    if free >= need:
                        break
                    if mem_deadline is None:
                        mem_deadline = time.time() + mem_wait_minutes * 60
                        announce(f"  [queue] {kind} {item}: its turn, but only "
                                 f"{free:.0f} GB free (needs ~{need:g}); waiting")
                    if time.time() >= mem_deadline:
                        row["result"] = "refused: not enough free RAM"
                        row["ram_free_gb_at_start"] = f"{free:.1f}"
                        record(record_path, row)
                        raise NotEnoughFreeMemory(
                            f"{kind} {item}: only {free:.0f} GB of RAM free after "
                            f"{mem_wait_minutes:g} min (needs ~{need:g} GB). Not run.")
            if handle is None:               # not our turn yet
                h = holder(base)
                status = (names.index(mine) + 1, h.get("item"))
                if status != told:
                    announce(f"  [queue] {kind} {item}: waiting, position {status[0]}"
                             + (f"; running now: {h.get('kind')} {h.get('item')} "
                                f"({h.get('project')})" if h else ""))
                    told = status
            time.sleep(_poll_seconds())

        started = datetime.now()
        row["started"] = started.isoformat(timespec="seconds")
        row["ram_free_gb_at_start"] = f"{free_ram_gb():.1f}"
        row["waited_s"] = f"{(started - queued).total_seconds():.0f}"
        handle.seek(0)
        handle.truncate()
        json.dump({"pid": os.getpid(), "kind": kind, "item": item,
                   "project": project, "started": row["started"]}, handle)
        handle.flush()
        with contextlib.suppress(OSError):
            os.remove(ticket)

        s = Slot(row)
        try:
            yield s
        except BaseException:
            s.result = "error"
            raise
        finally:
            finished = datetime.now()
            row["finished"] = finished.isoformat(timespec="seconds")
            row["duration_s"] = f"{(finished - started).total_seconds():.0f}"
            row["peak_rss_gb"] = ("" if s.peak_rss_gb is None
                                  else f"{s.peak_rss_gb:.2f}")
            row["result"] = s.result
            record(record_path, row)
    finally:
        with contextlib.suppress(OSError):
            os.remove(ticket)
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.seek(0)
                handle.truncate()
            handle.close()


def holder(base=None):
    """What the current holder wrote ({} when nobody holds the lock)."""
    path = os.path.join(base or lock_dir(), "heavy.lock")
    if not os.path.isfile(path):
        return {}
    with open(path, "a+", encoding="utf-8") as handle:
        if _try_lock(handle):
            return {}                 # free
        handle.seek(0)
        try:
            return json.loads(handle.read() or "{}")
        except ValueError:
            return {}


def waiting(base=None):
    queue = os.path.join(base or lock_dir(), "queue")
    if not os.path.isdir(queue):
        return []
    return [info for _n, info in _tickets(queue)]


# ---------------------------------------------------------------------------
# running a command and measuring it
# ---------------------------------------------------------------------------

def run_measured(cmd, **popen_kwargs):
    """Run cmd; return (returncode, peak RSS in GB of its largest process).

    The peak comes from wait4(): the maximum resident set size of the child
    or of any of its descendants it waited for. For recon-all (a shell
    script) that is the largest FreeSurfer binary it ran, not the sum of
    processes running at the same time."""
    proc = subprocess.Popen(cmd, **popen_kwargs)
    try:
        _pid, status, usage = os.wait4(proc.pid, 0)
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    proc.returncode = os.waitstatus_to_exitcode(status)
    unit = 1 if sys.platform == "darwin" else 1024    # bytes on macOS, KiB on Linux
    return proc.returncode, usage.ru_maxrss * unit / GB


# ---------------------------------------------------------------------------

def main():
    if sys.argv[1:2] != ["status"]:
        sys.exit("usage: python code/heavy_lock.py status")
    base = lock_dir()
    print(f"lock folder : {base}")
    print(f"RAM         : {total_ram_gb():.0f} GB total, {free_ram_gb():.0f} GB free now")
    h = holder(base)
    print("running now : " + (f"{h.get('kind')} {h.get('item')} ({h.get('project')}), "
                              f"since {h.get('started')}" if h else "nothing"))
    queue = waiting(base)
    print(f"waiting     : {len(queue)}")
    for i, info in enumerate(queue, start=1):
        print(f"  {i}. {info.get('kind')} {info.get('item')} ({info.get('project')}), "
              f"queued {info.get('queued')}")


if __name__ == "__main__":
    main()

"""
Self-tests for heavy_lock.py (one heavy analysis at a time on the machine),
with the queue folder in a temporary directory and the RAM faked. No real
FreeSurfer: a tiny fake recon-all checks that two never run at once.

    python -m pytest tests/test_heavy_lock.py -q
"""

import csv
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synthetic as s  # noqa: E402

sys.path.insert(0, str(s.CODE))
import heavy_lock  # noqa: E402

# A process that takes a slot, notes when it got in, holds it, and leaves.
HOLDER = r'''
import sys, time
sys.path.insert(0, sys.argv[1])
import heavy_lock
name, hold, journal, record = sys.argv[2], float(sys.argv[3]), sys.argv[4], sys.argv[5]
with heavy_lock.slot("recon-all", name, project="T", record_path=record,
                     announce=lambda m: None):
    with open(journal, "a") as f:
        f.write(f"in {name} {time.time_ns()}\n")
    time.sleep(hold)
    with open(journal, "a") as f:
        f.write(f"out {name} {time.time_ns()}\n")
'''

# Fake recon-all: refuses to run if another one is running (atomic probe file).
FAKE_RECON = r'''
import os, sys, time
args = sys.argv[1:]
sid = args[args.index("-s") + 1]
probe = os.environ["PROBE"]
try:
    fd = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
except FileExistsError:
    open(probe + ".overlap", "a").write(sid + "\n")
    sys.exit(9)
time.sleep(0.4)
os.close(fd)
os.remove(probe)
scripts = os.path.join(os.environ["SUBJECTS_DIR"], sid, "scripts")
os.makedirs(scripts, exist_ok=True)
open(os.path.join(scripts, "recon-all.done"), "w").write("fake\n")
'''


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Queue folder + fake RAM for in-process calls and for child processes."""
    values = {"PIPELINE_HEAVY_LOCK_DIR": str(tmp_path / "lockdir"),
              "PIPELINE_FAKE_RAM_GB": "64", "PIPELINE_FAKE_FREE_GB": "60",
              "PIPELINE_HEAVY_POLL": "0.05"}
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    return dict(os.environ, **values)


def read_tsv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def start_holder(tmp_path, env, name, hold):
    script = tmp_path / "holder.py"
    script.write_text(HOLDER)
    return subprocess.Popen([sys.executable, str(script), str(s.CODE), name, str(hold),
                             str(tmp_path / "journal.txt"), str(tmp_path / "runs.tsv")],
                            env=env)


def test_one_at_a_time_in_order_of_arrival(tmp_path, env):
    procs = []
    for name in ("first", "second", "third"):
        procs.append(start_holder(tmp_path, env, name, 0.6))
        time.sleep(0.25)                  # arrive in this order
    for p in procs:
        assert p.wait(timeout=60) == 0
    events = [line.split() for line in (tmp_path / "journal.txt").read_text().splitlines()]
    assert [e[1] for e in events if e[0] == "in"] == ["first", "second", "third"]
    # never two inside at the same time: in/out strictly alternate
    assert [e[0] for e in events] == ["in", "out"] * 3
    rows = read_tsv(tmp_path / "runs.tsv")
    assert [r["item"] for r in rows] == ["first", "second", "third"]
    assert all(r["result"] == "ok" and r["duration_s"] != "" for r in rows)
    assert int(rows[2]["waited_s"]) >= 1      # the third one queued behind two


def test_machine_too_small_is_refused_and_reported(tmp_path, env, monkeypatch):
    monkeypatch.setenv("PIPELINE_FAKE_RAM_GB", "24")
    ran = []
    with pytest.raises(heavy_lock.InsufficientRAM) as err:
        with heavy_lock.slot("recon-all", "sub-X_ses-1", record_path=str(tmp_path / "r.tsv")):
            ran.append(True)
    assert not ran
    assert "never changed" in str(err.value)
    assert read_tsv(tmp_path / "r.tsv")[0]["result"] == "refused: insufficient RAM"


def test_waits_for_free_memory_then_gives_up_and_frees_the_turn(tmp_path, env, monkeypatch):
    monkeypatch.setenv("PIPELINE_FAKE_FREE_GB", "10")
    messages = []
    with pytest.raises(heavy_lock.NotEnoughFreeMemory):
        with heavy_lock.slot("recon-all", "a", record_path=str(tmp_path / "r.tsv"),
                             mem_wait_minutes=0.005, announce=messages.append):
            pass
    assert any("10 GB free" in m for m in messages)
    assert read_tsv(tmp_path / "r.tsv")[0]["result"] == "refused: not enough free RAM"
    # the turn was given back: the next run gets in at once
    monkeypatch.setenv("PIPELINE_FAKE_FREE_GB", "60")
    with heavy_lock.slot("recon-all", "b", announce=lambda m: None):
        assert heavy_lock.holder()["item"] == "b"
    assert heavy_lock.holder() == {}
    assert heavy_lock.waiting() == []


def test_ticket_of_a_dead_process_does_not_block(tmp_path, env):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    queue = Path(env["PIPELINE_HEAVY_LOCK_DIR"]) / "queue"
    queue.mkdir(parents=True)
    (queue / "00000000000000000001-1-000000.json").write_text(
        f'{{"pid": {dead.pid}, "kind": "recon-all", "item": "ghost"}}')
    t0 = time.time()
    with heavy_lock.slot("samseg", "real", announce=lambda m: None):
        pass
    assert time.time() - t0 < 5
    assert not any(queue.iterdir())


def test_run_measured_reports_exit_code_and_peak_memory():
    code = "b = bytearray(200 * 1024 * 1024); b[::4096] = b'x' * len(b[::4096]); raise SystemExit(3)"
    rc, peak_gb = heavy_lock.run_measured([sys.executable, "-c", code])
    assert rc == 3
    assert peak_gb > 0.15


def test_max_threads_only_caps():
    assert heavy_lock.max_threads(ram_gb=64, cores=10) == 8
    assert heavy_lock.max_threads(ram_gb=64, cores=4) == 4
    assert heavy_lock.max_threads(ram_gb=36, cores=10) == 4
    assert heavy_lock.max_threads(ram_gb=16, cores=10) == 2


def test_status_command(tmp_path, env):
    out = subprocess.run([sys.executable, str(s.CODE / "heavy_lock.py"), "status"],
                         env=env, capture_output=True, text=True)
    assert out.returncode == 0
    assert "running now : nothing" in out.stdout and "waiting     : 0" in out.stdout


def test_recon_jobs_in_parallel_still_run_one_at_a_time(tmp_path):
    nib = pytest.importorskip("nibabel")
    import numpy as np
    projects = s.make_projects(tmp_path)
    bids = projects / "CBR" / "bids"
    for sub in ("sub-CBR901", "sub-CBR902", "sub-CBR903"):
        anat = bids / sub / "ses-1" / "anat"
        anat.mkdir(parents=True)
        nib.save(nib.Nifti1Image(np.zeros((4, 4, 4), dtype=np.int16), np.eye(4)),
                 str(anat / f"{sub}_ses-1_T1w.nii.gz"))
    fake = tmp_path / "fake_recon.py"
    fake.write_text(FAKE_RECON)
    bin_dir = s.fake_bin(tmp_path, "recon-all", f'exec "{sys.executable}" "{fake}" "$@"')
    env = s.env_for(projects, extra_path=[bin_dir], PROBE=tmp_path / "probe")
    sd = bids / "derivatives" / "freesurfer"
    proc = s.run("run_recon_long.py", "CBR", env, "--input", str(bids),
                 "--subjects-dir", str(sd), "--stages", "cross",
                 "--jobs", "3", "--threads", "2")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (tmp_path / "probe.overlap").exists(), "two recon-all ran at once"
    rows = read_tsv(projects / "CBR" / "logs" / "heavy_runs.tsv")
    assert sorted(r["item"] for r in rows) == [
        "sub-CBR901_ses-1", "sub-CBR902_ses-1", "sub-CBR903_ses-1"]
    assert all(r["result"] == "ok" and r["kind"] == "recon-all" and r["threads"] == "2"
               and r["project"] == "CBR" for r in rows)
    assert all((sd / f"{sub}_ses-1" / "scripts" / "recon-all.done").is_file()
               for sub in ("sub-CBR901", "sub-CBR902", "sub-CBR903"))

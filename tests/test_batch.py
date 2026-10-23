"""
Self-tests for run_batch.py (and the pieces it relies on), on synthetic data
only: fake DICOM exams written with pydicom, the real dcm2niix, and stub
FreeSurfer commands, all under a temporary PIPELINE_PROJECTS_DIR.

    python -m pytest tests -q
"""

import csv
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synthetic as s  # noqa: E402

sys.path.insert(0, str(s.CODE))
import batch_lock  # noqa: E402
import qc_status  # noqa: E402

FAST = ["--min-free-gb", "0", "--no-caffeinate"]


# ---------------------------------------------------------------------------
# fixtures and helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def cog(tmp_path):
    """COGAGING with three CDs (two visits of GER00039, one of GER00041; the
    first carries an unknown series) and a stub FreeSurfer."""
    projects = s.make_projects(tmp_path)
    pf = projects / "COGAGING" / "Patient Files"
    s.make_exam(str(pf / s.exam_folder_name("GER00039", "20120304")), s.COG_SERIES,
                "20120304", unknown=True)
    s.make_exam(str(pf / s.exam_folder_name("GER00039", "20130506")), s.COG_SERIES,
                "20130506")
    s.make_exam(str(pf / s.exam_folder_name("GER00041", "20120708")), s.COG_SERIES,
                "20120708")
    fs = s.make_freesurfer(tmp_path)
    calls = tmp_path / "calls.txt"
    env = s.env_for(projects, fs, STUB_CALLS=calls)
    return {"root": tmp_path, "projects": projects, "env": env, "fs": fs,
            "calls": calls, "project": projects / "COGAGING"}


def batch(ctx, *args, project="COGAGING", **env):
    e = dict(ctx["env"], **{k: str(v) for k, v in env.items()})
    return s.run("run_batch.py", project, e, *FAST, *args)


def summary(ctx, project="COGAGING"):
    path = ctx["projects"] / project / "logs" / "batch" / "last_summary.tsv"
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def steps(rows):
    return {r["step"]: r["status"] for r in rows if r["section"] == "step"}


def result(rows):
    return next(r["status"] for r in rows if r["section"] == "run" and r["item"] == "result")


def snapshot(root, skip=("logs",)):
    """{relative path: (size, mtime_ns)} of every file, to prove nothing changed."""
    out = {}
    for path in Path(root).rglob("*"):
        rel = path.relative_to(root)
        if path.is_file() and rel.parts[0] not in skip:
            st = path.stat()
            out[str(rel)] = (st.st_size, st.st_mtime_ns)
    return out


def fs_done(ctx, fs_id, project="COGAGING"):
    return (ctx["projects"] / project / "bids" / "derivatives" / "freesurfer" / fs_id
            / "scripts" / "recon-all.done").is_file()


def calls(ctx):
    return ctx["calls"].read_text().splitlines() if ctx["calls"].exists() else []


# ---------------------------------------------------------------------------
# 1. first run does everything, second run does nothing
# ---------------------------------------------------------------------------

def test_first_run_processes_everything_second_does_nothing(cog):
    r = batch(cog)
    assert r.returncode == 0, r.stdout + r.stderr
    rows = summary(cog)
    assert result(rows) == "OK"
    assert steps(rows) == {"import": "skipped", "convert": "ran", "deidentify": "ran",
                           "bids": "ran", "derivatives": "ran", "samseg": "ran",
                           "recon": "ran", "stats": "ran"}
    bids = cog["project"] / "bids"
    for fs_id in ("sub-GER00039_ses-1", "sub-GER00039_ses-2", "sub-GER00041_ses-1",
                  "sub-GER00039", "sub-GER00039_ses-1.long.sub-GER00039",
                  "sub-GER00039_ses-2.long.sub-GER00039"):
        assert fs_done(cog, fs_id), fs_id
    for ses in ("sub-GER00039_ses-1", "sub-GER00039_ses-2", "sub-GER00041_ses-1"):
        assert (bids / "derivatives" / "samseg" / ses / "lesion_mask.mgz").is_file()
    assert (bids / "derivatives" / "freesurfer" / "stats" / "aseg_volumes.tsv").is_file()
    # The batch ignores the SUBJECTS_DIR that SetUpFreeSurfer.sh would set.
    assert not (cog["fs"] / "subjects").exists()
    human = [r for r in rows if r["section"] == "human"]
    assert any(r["step"] == "unknown-series" and "zzq_mystery_seq" in r["detail"]
               for r in human)
    assert len([r for r in rows if r["section"] == "qc"]) == 6
    md = (cog["project"] / "logs" / "batch" / "last_summary.md").read_text()
    assert "## Needs a person" in md and "## QC to do" in md

    before = snapshot(cog["project"])
    n_calls = len(calls(cog))
    r = batch(cog)
    assert r.returncode == 0, r.stdout + r.stderr
    rows = summary(cog)
    assert set(steps(rows).values()) == {"skipped"}
    assert snapshot(cog["project"]) == before, "a second run changed files"
    assert len(calls(cog)) == n_calls, "a second run called FreeSurfer"


def test_dry_run_changes_nothing(cog):
    before = snapshot(cog["project"], skip=())
    r = batch(cog, "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "convert      3 to do" in r.stdout
    assert snapshot(cog["project"], skip=()) == before
    assert not (cog["project"] / "logs").exists()


# ---------------------------------------------------------------------------
# 2. an injected failure is reported, then retried on the next run
# ---------------------------------------------------------------------------

def test_injected_failure_is_reported_and_retried(cog):
    r = batch(cog, STUB_FAIL="sub-GER00041_ses-1,sub-GER00039_ses-2")
    assert r.returncode == 1
    rows = summary(cog)
    assert result(rows) == "FAILED"
    assert steps(rows)["recon"] == "failed" and steps(rows)["samseg"] == "failed"
    failed = {(r["step"], r["item"]): r["detail"] for r in rows if r["section"] == "failed"}
    # Both injected ids fail in recon and in SAMSEG; GER00039's base and long
    # runs cannot run without its failed timepoint.
    assert set(failed) == {
        ("recon", "sub-GER00041_ses-1"), ("recon", "sub-GER00039_ses-2"),
        ("recon", "sub-GER00039"), ("recon", "sub-GER00039_ses-1.long.sub-GER00039"),
        ("recon", "sub-GER00039_ses-2.long.sub-GER00039"),
        ("samseg", "sub-GER00041_ses-1"), ("samseg", "sub-GER00039_ses-2")}
    assert "stub failure injected" in failed[("recon", "sub-GER00041_ses-1")]
    assert "attempt 1 of 3" in failed[("recon", "sub-GER00041_ses-1")]
    assert "stub failure injected" in failed[("samseg", "sub-GER00039_ses-2")]
    # The subject that did not fail was finished; the base of GER00039 waited
    # for its failed timepoint and that is not counted as its own failure.
    assert fs_done(cog, "sub-GER00039_ses-1")
    state = json.loads((cog["project"] / "logs" / "batch" / "state.json").read_text())
    assert state["failures"]["recon:sub-GER00041_ses-1"]["count"] == 1
    assert "recon:sub-GER00039" not in state["failures"]
    assert "attempt" not in failed[("recon", "sub-GER00039")]

    r = batch(cog)       # fixed: no more injected failure
    assert r.returncode == 0, r.stdout + r.stderr
    rows = summary(cog)
    assert result(rows) == "OK" and steps(rows)["recon"] == "ran"
    for fs_id in ("sub-GER00041_ses-1", "sub-GER00039_ses-2", "sub-GER00039",
                  "sub-GER00039_ses-2.long.sub-GER00039"):
        assert fs_done(cog, fs_id)
    # recon-all refuses -i on an existing folder: the failed one was moved aside.
    failed_dir = cog["project"] / "bids/derivatives/freesurfer/.failed/sub-GER00041_ses-1"
    assert (failed_dir / "scripts" / "recon-all.error").is_file()
    state = json.loads((cog["project"] / "logs" / "batch" / "state.json").read_text())
    assert state["failures"] == {}


def test_gives_up_after_max_attempts_until_retry_failed(cog):
    for attempt in (1, 2):
        r = batch(cog, "--max-attempts", "2", STUB_FAIL="sub-GER00041_ses-1")
        assert r.returncode == 1
    n_calls = len(calls(cog))
    r = batch(cog, "--max-attempts", "2", STUB_FAIL="sub-GER00041_ses-1")
    assert r.returncode == 0, r.stdout
    rows = summary(cog)
    assert steps(rows)["recon"] == "skipped"
    gave_up = {r["item"]: r["detail"] for r in rows
               if r["section"] == "human" and r["step"] == "gave-up"}
    assert set(gave_up) == {"recon sub-GER00041_ses-1", "samseg sub-GER00041_ses-1"}
    assert "failed 2 times" in gave_up["recon sub-GER00041_ses-1"]
    assert not any("recon-all" in c and "GER00041" in c for c in calls(cog)[n_calls:])
    r = batch(cog, "--retry-failed")
    assert r.returncode == 0, r.stdout
    assert fs_done(cog, "sub-GER00041_ses-1")


# ---------------------------------------------------------------------------
# 3. the lock: never two runs at once; a crash or stop leaves no stale lock
# ---------------------------------------------------------------------------

def start_batch(ctx, *args, **env):
    e = dict(ctx["env"], **{k: str(v) for k, v in env.items()})
    cmd = [sys.executable, str(s.CODE / "run_batch.py"), "--project", "COGAGING",
           *FAST, *args]
    return subprocess.Popen(cmd, env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, cwd=str(s.REPO))


def wait_for(cond, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


def test_lock_prevents_overlap(cog):
    lock = str(cog["projects"] / ".batch.lock")
    first = start_batch(cog, STUB_SLEEP=2)
    assert wait_for(lambda: batch_lock.is_held(lock))
    second = batch(cog, project="CBR")      # any project: the lock is per machine
    assert second.returncode == 3
    assert "ANOTHER BATCH IS RUNNING (project COGAGING" in second.stdout
    assert not (cog["projects"] / "CBR" / "logs").exists()
    waiting = start_batch(cog, "--wait", "5")
    out, _ = first.communicate(timeout=300)
    assert first.returncode == 0, out
    out, _ = waiting.communicate(timeout=300)
    assert waiting.returncode == 0, out    # it queued, then found nothing new
    assert set(steps(summary(cog)).values()) == {"skipped"}
    assert not batch_lock.is_held(lock)


def test_stopped_mid_recon_resumes_next_run(cog):
    proc = start_batch(cog, STUB_SLEEP=60)
    fsdir = cog["project"] / "bids" / "derivatives" / "freesurfer"
    assert wait_for(lambda: list(fsdir.glob("*/scripts/IsRunning*")), timeout=120)
    running = list(fsdir.glob("*/scripts/IsRunning*"))[0]
    pid = int(running.read_text().split("PROCESSID")[1].split()[0])
    proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=60)
    assert proc.returncode != 0
    assert result(summary(cog)) == "STOPPED"
    assert wait_for(lambda: not os.path.exists(f"/proc/{pid}") or
                    subprocess.run(["kill", "-0", str(pid)],
                                   capture_output=True).returncode != 0, timeout=20), \
        "recon-all kept running after the batch was stopped"
    r = batch(cog)
    assert r.returncode == 0, r.stdout + r.stderr
    assert result(summary(cog)) == "OK"
    assert fs_done(cog, running.parent.parent.name)
    assert (fsdir / ".failed" / running.parent.parent.name).is_dir()


# ---------------------------------------------------------------------------
# 4. refusals
# ---------------------------------------------------------------------------

def refused(ctx, project="COGAGING"):
    return [r["detail"] for r in summary(ctx, project) if r["section"] == "refused"]


def test_refuses_without_freesurfer_license_or_disk(cog, tmp_path):
    env = s.env_for(cog["projects"])                     # FreeSurfer not loaded
    r = s.run("run_batch.py", "COGAGING", env, *FAST)
    assert r.returncode == 2
    assert any("FreeSurfer is not loaded" in d for d in refused(cog))
    assert not (cog["project"] / "bids").exists(), "refused, yet converted"

    nolic = s.make_freesurfer(tmp_path / "nolicense", license=False)
    r = s.run("run_batch.py", "COGAGING", s.env_for(cog["projects"], nolic), *FAST)
    assert r.returncode == 2 and any("no FreeSurfer license" in d for d in refused(cog))

    r = batch(cog, "--min-free-gb", "100000000")
    assert r.returncode == 2 and any("GB free" in d for d in refused(cog))

    r = s.run("run_batch.py", "COGAGING", env, *FAST, "--no-freesurfer")
    assert r.returncode == 0, r.stdout
    rows = summary(cog)
    assert steps(rows)["convert"] == "ran" and steps(rows)["recon"] == "skipped"


def test_refuses_samseg_under_rosetta(cog):
    fakebin = s.fake_bin(cog["root"], "file", 'echo "Mach-O 64-bit executable x86_64"')
    env = dict(cog["env"], PATH=f"{fakebin}{os.pathsep}{cog['env']['PATH']}")
    code = ("import platform, runpy, sys; platform.machine = lambda: 'arm64'; "
            f"sys.argv = ['run_batch.py', '--project', 'COGAGING', *{FAST!r}]; "
            f"sys.path.insert(0, {str(s.CODE)!r}); "
            f"runpy.run_path({str(s.CODE / 'run_batch.py')!r}, run_name='__main__')")
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                       text=True, cwd=str(s.REPO))
    assert r.returncode == 2, r.stdout + r.stderr
    assert any("Rosetta" in d for d in refused(cog))


def test_keeps_the_mac_awake_only_while_running(cog):
    log = cog["root"] / "caffeinate.txt"
    fakebin = s.fake_bin(cog["root"], "caffeinate", f'echo "$@" >> "{log}"')
    env = dict(cog["env"], PATH=f"{fakebin}{os.pathsep}{cog['env']['PATH']}")
    r = s.run("run_batch.py", "COGAGING", env, "--min-free-gb", "0", "--dry-run")
    assert not log.exists(), "dry run must not call caffeinate"
    r = s.run("run_batch.py", "COGAGING", env, "--min-free-gb", "0")
    assert r.returncode == 0, r.stdout
    assert log.read_text().startswith("-is -w ")


# ---------------------------------------------------------------------------
# 5. new CDs over time: watch folder, copies in progress, session order
# ---------------------------------------------------------------------------

def test_new_cd_is_picked_up_and_copies_in_progress_wait(cog):
    assert batch(cog).returncode == 0
    pf = cog["project"] / "Patient Files"
    # Someone browses a converted CD in Finder: it must not look "being copied".
    (pf / s.exam_folder_name("GER00039", "20120304") / ".DS_Store").write_bytes(b"x")
    s.make_exam(str(pf / s.exam_folder_name("GER00042", "20140910")), s.COG_SERIES,
                "20140910", age_seconds=0)               # still "being copied"
    r = batch(cog)
    assert r.returncode == 0
    rows = summary(cog)
    assert steps(rows)["convert"] == "skipped"
    copying = [r["item"] for r in rows
               if r["section"] == "human" and r["step"] == "copy-in-progress"]
    assert copying == ["exam folder #4"]
    s.age(str(pf / s.exam_folder_name("GER00042", "20140910")), 7200)
    n_calls = len(calls(cog))
    r = batch(cog)
    assert r.returncode == 0, r.stdout
    assert steps(summary(cog))["convert"] == "ran"
    assert fs_done(cog, "sub-GER00042_ses-1")
    recon_calls = [c for c in calls(cog)[n_calls:] if c.startswith("recon-all")]
    assert recon_calls and all("GER00042" in c for c in recon_calls), recon_calls
    prov = list(csv.DictReader(open(cog["project"] / "bids/sourcedata/conversion/provenance.csv")))
    assert {(r["subject"], r["session"]) for r in prov} == {
        ("sub-GER00039", "ses-1"), ("sub-GER00039", "ses-2"), ("sub-GER00041", "ses-1"),
        ("sub-GER00042", "ses-1")}


def test_older_exam_stops_conversion_but_not_the_rest(cog):
    assert batch(cog, STUB_FAIL="sub-GER00041_ses-1").returncode == 1
    smap = cog["project"] / "sourcedata" / "session_map.tsv"
    before = smap.read_text()
    pf = cog["project"] / "Patient Files"
    s.make_exam(str(pf / s.exam_folder_name("GER00041", "20100101")), s.COG_SERIES,
                "20100101")
    r = batch(cog)
    assert r.returncode == 1
    rows = summary(cog)
    assert steps(rows)["convert"] == "failed"
    stops = [r["detail"] for r in rows if r["section"] == "human" and r["step"] == "convert-stop"]
    assert any("OLDER" in d for d in stops), stops
    assert steps(rows)["recon"] == "ran", "recon of the exams in bids/ must go on"
    assert fs_done(cog, "sub-GER00041_ses-1")
    assert smap.read_text() == before


def test_mismatch_rows_are_listed_for_a_person(cog):
    assert batch(cog).returncode == 0
    report = cog["project"] / "bids/sourcedata/conversion/validation_report.csv"
    rows = list(csv.DictReader(open(report)))
    rows[0]["status"] = "MISMATCH"
    with open(report, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    assert batch(cog).returncode == 0
    mism = [r for r in summary(cog) if r["section"] == "human" and r["step"] == "mismatch"]
    assert mism and mism[0]["item"] == f"{rows[0]['subject']}/{rows[0]['session']}"


# ---------------------------------------------------------------------------
# 6. no identifier ever leaks into an output or a log
# ---------------------------------------------------------------------------

def test_no_identifier_leaks(cog):
    out = [batch(cog, STUB_FAIL="sub-GER00041_ses-1"), batch(cog), batch(cog, "--dry-run")]
    dates = ["20120304", "20130506", "20120708", "2012-03-04", "2013-05-06",
             "2012-07-08", "19391231", "1939-12-31"]
    needles = [x.lower() for x in s.LEAK_STRINGS + dates]
    leaks = []
    for r in out:
        text = (r.stdout + r.stderr).lower()
        leaks += [f"console: {n}" for n in needles if n in text]
    for path, text in s.text_files(cog["projects"]):
        rel = path.relative_to(cog["projects"])
        # The input itself, and the date map that by design stays outside bids/.
        if "Patient Files" in rel.parts or rel.name == "session_map.tsv":
            continue
        low = text.lower()
        leaks += [f"{rel}: {n}" for n in needles if n in low]
    assert not leaks, leaks
    # The conversion step log is the one that sees the folder names: masked.
    conv_log = next((cog["project"] / "logs" / "batch").glob("*/01_convert.log"))
    assert "<exam folder>" in conv_log.read_text()


# ---------------------------------------------------------------------------
# 7. QC record (read by the summary)
# ---------------------------------------------------------------------------

def test_qc_status_record_pending_and_summary(cog):
    assert batch(cog).returncode == 0
    c = cog["project"] / "bids" / "derivatives"
    qc = str(c / "qc" / "qc_status.tsv")
    pend = qc_status.pending(str(c / "freesurfer"), str(c / "samseg"), qc)
    assert len(pend) == 6
    qc_status.record(qc, "sub-GER00039", "ses-1", "recon", "pass", "fine\twith tab", "AB")
    qc_status.record(qc, "sub-GER00041", "ses-1", "samseg", "fail", "mask on ventricle edge")
    qc_status.record(qc, "sub-GER00039", "ses-1", "recon", "fail", "changed my mind")
    rows = qc_status.load(qc)
    assert rows[("sub-GER00039", "ses-1", "recon")]["status"] == "fail"
    assert len(qc_status.pending(str(c / "freesurfer"), str(c / "samseg"), qc)) == 4
    assert (c / "qc" / "dataset_description.json").is_file()
    folder, cmd = qc_status.freeview_command(pend[0])
    assert cmd[0] == "freeview" and os.path.isdir(folder)
    with pytest.raises(ValueError):
        qc_status.record(qc, "sub-GER00039", "ses-1", "recon", "maybe")
    assert batch(cog).returncode == 0
    rows = summary(cog)
    failed = [r for r in rows if r["section"] == "human" and r["step"] == "qc-failed"]
    assert len(failed) == 2
    assert len([r for r in rows if r["section"] == "qc"]) == 4

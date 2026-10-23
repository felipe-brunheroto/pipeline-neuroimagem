"""
CBR outputs must not change. The same synthetic CBR exams are processed by
  A. the pipeline as it was before run_batch.py (BASELINE commit, step by step),
  B. the current code, step by step (the menu / README way), and
  C. run_batch.py, in two nightly runs with a CD added in between;
and the bids/ trees are compared file by file (NIfTI compared uncompressed,
text with run timestamps blanked). B must equal A exactly; C must equal A
except the logs of the runs themselves (an incremental run logs only what it
did that night).

    python -m pytest tests/test_cbr_unchanged.py -q
"""

import gzip
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synthetic as s  # noqa: E402

# The last commit before run_batch.py and the shared-code changes it needed.
BASELINE = os.environ.get("PIPELINE_BASELINE_REF", "1dbe558")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
# dcm2niix reports how long it took; that is the only other thing a run varies.
DURATION = re.compile(r"required [\d.]+ seconds \([\d.]+ for core code\)")
RUN_LOGS = {"conversion_log.txt", "deidentification_log.txt", "deface_log.txt"}

EXAMS = [("CBR-003", "20110203", True), ("CBR-003.2", "20110803", False),
         ("CBR-052", "20120901", False)]
LATER = ("CBR-011", "20121010", False)


def baseline_code(dest):
    """The code/ folder as of BASELINE, extracted from git."""
    try:
        data = subprocess.run(["git", "archive", BASELINE, "code"], cwd=str(s.REPO),
                              capture_output=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip(f"baseline commit {BASELINE} not available in this clone")
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        if hasattr(tarfile, "data_filter"):      # Python 3.12+ (and recent 3.8+)
            tar.extractall(dest, filter="data")
        else:
            tar.extractall(dest)
    return Path(dest) / "code"


def add_exam(projects, name, date, unknown):
    s.make_exam(str(projects / "CBR" / "Patient Files" / name), s.CBR_SERIES, date,
                unknown=unknown)


def manual_steps(projects, env, code_dir):
    """Every CBR step, one by one, as the menu runs them. Returns the outputs."""
    fsdir = str(projects / "CBR" / "bids" / "derivatives" / "freesurfer")
    outputs = []
    for script, args in [("dicom_to_bids.py", []), ("deidentify_sidecars.py", []),
                         ("make_bids.py", []), ("make_derivatives.py", []),
                         ("deface_anat.py", []),
                         ("run_recon_long.py", ["--subjects-dir", fsdir, "--threads", "2"]),
                         ("extract_fs_stats.py", ["--subjects-dir", fsdir,
                                                  "--output-dir", fsdir + "/stats"])]:
        r = s.run(script, "CBR", env, *args, code_dir=code_dir)
        assert r.returncode == 0, f"{script}: {r.stdout[-2000:]}{r.stderr[-2000:]}"
        outputs.append((script, normalise(r.stdout)))
    return outputs


def normalise(text):
    return DURATION.sub("required <t>", TIMESTAMP.sub("<ts>", text))


def contents(root, skip_names=()):
    """{relative path: normalised bytes} of every file under root."""
    out = {}
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file() or path.name in skip_names:
            continue
        data = path.read_bytes()
        if path.name.endswith(".gz"):
            data = gzip.decompress(data)
        else:
            data = normalise(data.decode("latin-1")).encode("latin-1")
            data = re.sub(rb"-openmp \d+", b"-openmp N", data)
        out[str(path.relative_to(root))] = data
    return out


def diff(a, b):
    problems = [f"only in A: {k}" for k in sorted(set(a) - set(b))]
    problems += [f"only in B: {k}" for k in sorted(set(b) - set(a))]
    problems += [f"differs: {k}" for k in sorted(set(a) & set(b)) if a[k] != b[k]]
    return problems


@pytest.fixture
def cbr(tmp_path):
    projects = s.make_projects(tmp_path)
    for name, date, unknown in EXAMS + [LATER]:
        add_exam(projects, name, date, unknown)
    fs = s.make_freesurfer(tmp_path)
    return projects, s.env_for(projects, fs), tmp_path


def test_cbr_outputs_unchanged(cbr):
    projects, env, tmp = cbr
    old_code = baseline_code(tmp / "baseline")
    bids = projects / "CBR" / "bids"

    old_out = manual_steps(projects, env, old_code)
    shutil.move(str(bids), str(tmp / "bids_A"))
    new_out = manual_steps(projects, env, s.CODE)
    shutil.move(str(bids), str(tmp / "bids_B"))

    a, b = contents(tmp / "bids_A"), contents(tmp / "bids_B")
    assert len(a) > 50
    assert diff(a, b) == []
    for (script, old), (_s, new) in zip(old_out, new_out):
        assert old == new, f"{script} printed something different"


def test_cbr_batch_matches_the_manual_steps(cbr):
    projects, env, tmp = cbr
    old_code = baseline_code(tmp / "baseline")
    bids = projects / "CBR" / "bids"
    manual_steps(projects, env, old_code)
    shutil.move(str(bids), str(tmp / "bids_A"))

    later = projects / "CBR" / "Patient Files" / LATER[0]
    shutil.move(str(later), str(tmp / "later"))       # not delivered yet
    batch = ["--min-free-gb", "0", "--no-caffeinate"]
    r = s.run("run_batch.py", "CBR", env, *batch)
    assert r.returncode == 0, r.stdout + r.stderr
    shutil.move(str(tmp / "later"), str(later))        # the CD arrives
    r = s.run("run_batch.py", "CBR", env, *batch)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[convert] DICOM -> NIfTI (new exams): 1 to do" in r.stdout
    r = s.run("run_batch.py", "CBR", env, *batch)
    assert r.returncode == 0 and "ran: nothing" in r.stdout

    a = contents(tmp / "bids_A", RUN_LOGS)
    c = contents(bids, RUN_LOGS)
    assert len(a) > 50
    assert diff(a, c) == []

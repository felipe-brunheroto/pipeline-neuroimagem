"""
run_batch.py
============

ONE command for unattended processing (the Mac Mini, nightly): it runs every
step that applies to the project, in order, and only for work that is new or
unfinished.

    python code/run_batch.py --project COGAGING --dry-run   # what would run
    python code/run_batch.py --project COGAGING             # run it
    python code/install_schedule.py --project COGAGING      # every night (launchd)

THE STEPS (those that apply per project.json "input" / "steps", in this order)
  import       import_bids.py             delivered sub-* not in bids/ yet   (bids_import)
  convert      dicom_to_bids.py --only-new  exam folders not converted yet   (DICOM input)
  deidentify   deidentify_sidecars.py     when provenance.csv changed
  bids         make_bids.py               idem (needs deidentify to have worked)
  derivatives  make_derivatives.py        idem (needs deidentify to have worked)
  deface       deface_anat.py             anatomicals without a defaced copy (steps.deface)
  samseg       run_samseg.py              T1w+FLAIR sessions without lesions  (steps.samseg)
  recon        run_recon_long.py          cross/base/long runs not done
  stats        extract_fs_stats.py        when the set of finished recons changed
SAMSEG does not wait for recon. A failed step stops only the steps that need
what it makes (deidentify -> bids/derivatives -> deface/SAMSEG/recon); a failed
conversion of one new CD does not stop the recon of the exams already in bids/.

SAFE TO RE-RUN AT ANY TIME
  What is pending is read from the outputs every run (provenance.csv,
  recon-all.done, samseg.stats, ...), so a run that crashed or was stopped
  half-way resumes on the next one. A recon-all folder left unfinished is moved
  to bids/derivatives/freesurfer/.failed/<id> (the latest attempt is kept for
  inspection) and started again: recon-all refuses '-i' on an existing subject.
  A run with nothing new runs nothing and changes nothing in bids/. A failed
  item is retried on the next run; a recon or SAMSEG run that failed
  --max-attempts times (default 3) is left out and listed for a person
  (--retry-failed tries again). New CD folders dropped in Patient Files/ are
  picked up by the next run; a folder still changing (being copied) waits.

ONE RUN AT A TIME
  A machine-wide lock (projects/.batch.lock, an OS file lock that a crashed or
  killed run releases by itself) refuses a second run; --wait MINUTES queues.

REFUSES TO START, with a clear message and nothing done, when
  * FreeSurfer is not loaded (FREESURFER_HOME / recon-all) or has no license,
    unless --no-freesurfer (then only the steps without FreeSurfer run);
  * SAMSEG would run on an Intel FreeSurfer under Rosetta (no AVX);
  * the disk has less than --min-free-gb free (default 50);
  * pydicom / dcm2niix are missing in a project with DICOM exams.

LOGS AND SUMMARY  (projects/<NAME>/logs/batch/, git-ignored, kept local)
  <run>/batch.log        this run, as printed
  <run>/NN_<step>.log    each step's own output
  <run>/summary.md       what ran, what was skipped, what failed and why, and
                         what needs a person (unknown series, MISMATCH rows,
                         session-order stops, QC to do)
  <run>/summary.tsv      the same facts, one per row (the student menu reads it)
  last_summary.md/.tsv   copy of the latest summary
  state.json             retry counts + the inputs each step last processed
  launchd.log            output of the scheduled runs
Only study codes appear. In projects whose exam folder names may carry a
patient name (subject_pattern), every piece of those names is masked in the
step logs as well.

KEEPING THE MAC AWAKE
  While it runs, `caffeinate -is -w <pid>` keeps the Mac from sleeping; it ends
  with the run (--no-caffeinate to skip).

PARALLELISM (sized for the 64 GB Mac Mini)
  recon-all: --jobs x --threads (default threads 2; jobs = what the performance
  cores and ~8 GB of RAM per job allow: 4-5 on an M4 Pro / 64 GB).
  SAMSEG: --samseg-jobs x --samseg-threads (default 4 threads, ~12 GB per job).
  Steps run one after the other, so the two never compete.

EXIT CODE: 0 done (items for a person may remain), 1 a step failed, 2 refused
to start, 3 another run holds the lock.
"""

import argparse
import contextlib
import csv
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import paths
import batch_lock
import qc_status
import run_recon_long as rrl
import run_samseg

GB = 1024 ** 3
STATE_FILE = paths.BATCH_DIR / "state.json"
KEEP_RUNS = 60            # run folders kept in logs/batch/
CHILD = {"proc": None}    # the step running now (stopped with the batch)

# Pieces of an exam folder name that are never masked: scanner packaging, and
# the words the batch looks for in the step logs.
SAFE_WORDS = {"dicom", "dicomdir", "exam", "exame", "study", "estudo", "cd",
              "dvd", "disc", "copy", "copia", "error", "warning", "failed", "skip"}


# ===========================================================================
#  Small helpers
# ===========================================================================

def make_redactor():
    """
    A function that masks exam-folder names in text bound for a log. Only for
    projects whose folder name carries the study code plus, possibly, a patient
    name (subject_pattern): the whole name, and each piece of it that is not
    the code (names; digit runs of 6+ such as dates), become '<masked>'.
    """
    pattern = paths.PROJECT.get("subject_pattern")
    if not pattern or not paths.PATIENT_FILES.is_dir():
        return lambda text: text
    pieces = set()
    for name in os.listdir(paths.PATIENT_FILES):
        if not (paths.PATIENT_FILES / name).is_dir():
            continue
        pieces.add(re.escape(name))
        for token in re.split(r"[^0-9A-Za-z]+", name):
            if (re.fullmatch(pattern, token, re.IGNORECASE)
                    or token.lower() in SAFE_WORDS
                    or len(token) < 3 or (token.isdigit() and len(token) < 6)):
                continue
            pieces.add(r"(?<![0-9A-Za-z])" + re.escape(token) + r"(?![0-9A-Za-z])")
    if not pieces:
        return lambda text: text
    rx = re.compile("|".join(sorted(pieces, key=len, reverse=True)), re.IGNORECASE)
    return lambda text: rx.sub("<masked>", text)


def file_signature(*files):
    """sha256 of the files that exist ('' if none): 'did this input change?'."""
    h = hashlib.sha256()
    seen = False
    for f in files:
        if os.path.isfile(f):
            seen = True
            with open(f, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest() if seen else ""


def quiet(func, *args):
    """Call a planning helper that prints notes, without the notes."""
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args)


def last_error(log_path, fallback=None):
    """The last line of a log that looks like an error (or the last line)."""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return fallback or f"no log ({os.path.basename(str(log_path))})"
    for line in reversed(lines):
        if re.search(r"error|failed|abort|not found|stopping", line, re.IGNORECASE):
            return line[:240]
    return (lines[-1][:240] if lines else fallback) or "no output"


def machine():
    """(performance cores, RAM in GB). On Apple Silicon the efficiency cores
    are left out: recon-all on them is several times slower."""
    def sysctl(key):
        try:
            out = subprocess.run(["sysctl", "-n", key], capture_output=True,
                                 text=True, timeout=10).stdout.strip()
            return int(out) if out.isdigit() else None
        except Exception:
            return None
    cores = sysctl("hw.perflevel0.physicalcpu") or os.cpu_count() or 4
    ram = sysctl("hw.memsize")
    if ram is None:
        try:
            ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (ValueError, OSError, AttributeError):
            ram = 16 * GB
    return cores, ram / GB


def auto_jobs(threads, gb_per_job):
    """How many runs fit side by side: cores / threads, and RAM (keeping 8 GB
    for the system) / gb_per_job."""
    cores, ram_gb = machine()
    return max(1, min(cores // max(1, threads), int((ram_gb - 8) // gb_per_job)))


def load_state():
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    state.setdefault("signatures", {})
    state.setdefault("failures", {})
    return state


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def read_csv(path):
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def import_problems(log_path):
    """The problems import_bids.py lists under 'Checks:' at its end."""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    tail = text.rsplit(" Checks:", 1)
    if len(tail) < 2:
        return []
    return [ln.strip()[2:] for ln in tail[1].splitlines() if ln.strip().startswith("- ")]


def running_pid(fs_subject_dir):
    """PID of a recon-all still running on this folder (its IsRunning file
    names it and the process is alive), else None."""
    for flag in Path(fs_subject_dir, "scripts").glob("IsRunning*"):
        try:
            m = re.search(r"PROCESSID\s+(\d+)", flag.read_text(errors="replace"))
        except OSError:
            continue
        if m:
            try:
                os.kill(int(m.group(1)), 0)
                return int(m.group(1))
            except (ProcessLookupError, ValueError):
                continue
            except PermissionError:
                return int(m.group(1))
    return None


# ===========================================================================
#  The steps
# ===========================================================================

class Step:
    """
    One pipeline step as the batch sees it.
      todo(run)     -> (items, note): what is still pending, read from the files
      args(run)     -> extra command-line arguments for the script
      prepare(run, items) -> items to leave out of this run (and why)
      reason(run, item, log) -> why an item is still pending after the step ran
    `retry_limited` steps stop retrying an item after --max-attempts failures
    (the long FreeSurfer runs); the others try every run.
    """
    fs = False
    requires = ()
    retry_limited = False

    def __init__(self, name, title, script):
        self.name, self.title, self.script = name, title, script

    def applies(self):
        return True

    def todo(self, run):
        return [], ""

    def args(self, run):
        return []

    def prepare(self, run, items):
        return {}

    def finish(self, run, rc):
        pass

    def reason(self, run, item, log_path):
        return last_error(log_path)


class Import(Step):
    def applies(self):
        return paths.PROJECT.get("input") == "bids" or bool(paths.PROJECT.get("bids_import"))

    def todo(self, run):
        src = paths.RECEIVED_DIR
        if not src.is_dir():
            return [], "no sourcedata/received folder"
        subs = sorted(n for n in os.listdir(src)
                      if n.startswith("sub-") and (src / n).is_dir())
        return [s for s in subs if not (paths.BIDS_ROOT / s).is_dir()], \
            f"{len(subs)} delivered subject(s)"


class Convert(Step):
    def applies(self):
        return paths.PROJECT.get("input", "dicom") == "dicom"

    def todo(self, run):
        if not paths.PATIENT_FILES.is_dir() or not any(
                p.is_dir() for p in paths.PATIENT_FILES.iterdir()):
            return [], "no exam folders"
        import dicom_to_bids as d2b
        exams = quiet(d2b.pending_exams, str(paths.PATIENT_FILES),
                      str(paths.CONVERSION_DIR), run.args.settle_minutes * 60)
        run.exams = exams
        todo = [f"{e['subject']}/{e['session'] or 'new session'}"
                for e in exams if e["state"] in ("new", "reclassify")]
        counts = {}
        for e in exams:
            counts[e["state"]] = counts.get(e["state"], 0) + 1
        return todo, ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))

    def args(self, run):
        return ["--only-new", "--settle-minutes", f"{run.args.settle_minutes:g}"]

    def reason(self, run, item, log_path):
        stops = run.errors_in(log_path)
        mine = [e for e in stops if e.startswith(item.split("/")[0] + ":")]
        return "; ".join(mine or stops) if stops else \
            "still not in provenance.csv after the conversion (see the step log)"


class Signature(Step):
    """Steps that rebuild from provenance.csv: they run when it changed since
    their last success, or when a conversion ran in this batch."""
    output = None

    def applies(self):
        return paths.PROJECT.get("input", "dicom") == "dicom"

    def signature(self):
        return file_signature(paths.CONVERSION_DIR / "provenance.csv")

    def todo(self, run):
        sig = self.signature()
        if not sig or self.name in run.done_now:
            return [], ""
        stale = (sig != run.state["signatures"].get(self.name)
                 or run.status.get("convert") == "ran"
                 or (self.output is not None and not self.output.exists()))
        return (["provenance.csv"] if stale else []), ""

    def finish(self, run, rc):
        if rc == 0:
            run.state["signatures"][self.name] = self.signature()
            run.done_now.add(self.name)


class Deidentify(Signature):
    pass


class MakeBids(Signature):
    requires = ("deidentify",)
    output = paths.BIDS_ROOT / "dataset_description.json"


class MakeDerivatives(Signature):
    requires = ("deidentify",)


class Deface(Step):
    fs = True
    requires = ("bids",)

    def applies(self):
        return bool(paths.PROJECT.get("steps", {}).get("deface", True))

    def todo(self, run):
        import deface_anat as da
        todo = []
        for nifti in da.find_anat_niftis(str(paths.BIDS_ROOT)):
            rel = os.path.relpath(str(nifti), paths.BIDS_ROOT)
            dest = (paths.BIDS_ROOT / "derivatives" / "defaced" / os.path.dirname(rel)
                    / da.insert_desc_defaced(nifti.name))
            if not dest.exists():
                todo.append(nifti.name[:-len(".nii.gz")])
        return todo, ""


class Samseg(Step):
    fs = True
    requires = ("bids",)
    retry_limited = True

    def applies(self):
        return bool(paths.PROJECT.get("steps", {}).get("samseg", False))

    def todo(self, run):
        tree = quiet(rrl.discover, str(paths.BIDS_ROOT))
        todo = []
        for sub in sorted(tree):
            for ses in sorted(tree[sub]):
                anat = tree[sub][ses]
                out = paths.SAMSEG_DIR / f"{sub}_{ses}"
                if (anat.get("T1w") and anat.get("FLAIR")
                        and not run_samseg.is_done(str(out), run_samseg.DEFAULT_THRESHOLD)):
                    todo.append(f"{sub}_{ses}")
        return todo, ""

    def args(self, run):
        return ["--jobs", str(run.samseg_jobs), "--threads", str(run.args.samseg_threads)]

    def prepare(self, run, items):
        return {i: "gave up" for i in run.given_up(self.name, items)}

    def reason(self, run, item, log_path):
        return last_error(paths.SAMSEG_DIR / item / "samseg_pipeline.log")


class Recon(Step):
    fs = True
    requires = ("bids",)
    retry_limited = True

    def plan(self):
        """[(stage, fs_id, prerequisites)] still to run."""
        tree = quiet(rrl.discover, str(paths.BIDS_ROOT))
        cfg = paths.PROJECT.get("recon", {})
        plan = quiet(rrl.build_plan, tree, str(paths.FREESURFER_DIR),
                     cfg.get("pial", "flair"), 1, cfg.get("flags", []))
        out = []
        for stage in ("cross", "base", "long"):
            for _label, fs_subject, cmd in plan[stage]:
                if rrl.recon_done(str(paths.FREESURFER_DIR), fs_subject):
                    continue
                if stage == "base":
                    needs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-tp"]
                elif stage == "long":
                    needs = cmd[cmd.index("-long") + 1:cmd.index("-long") + 3]
                else:
                    needs = []
                out.append((stage, fs_subject, needs))
        return out

    def todo(self, run):
        return [fs_subject for _s, fs_subject, _n in self.plan()], ""

    def args(self, run):
        return ["--subjects-dir", str(paths.FREESURFER_DIR),
                "--jobs", str(run.recon_jobs), "--threads", str(run.args.threads)]

    def prepare(self, run, items):
        """Leave out what gave up or is running elsewhere (and the base/long
        runs that need it); move unfinished folders aside so they restart."""
        plan = self.plan()
        left_out = {i: "gave up" for i in run.given_up(self.name, items)}
        for _stage, fs_subject, _needs in plan:
            folder = paths.FREESURFER_DIR / fs_subject
            if fs_subject in left_out or not folder.is_dir():
                continue
            if running_pid(folder):
                left_out[fs_subject] = "recon-all is running on it outside this batch"
        changed = True
        while changed:          # a left-out run also leaves out what needs it
            changed = False
            for _stage, fs_subject, needs in plan:
                if fs_subject not in left_out and any(n in left_out for n in needs):
                    left_out[fs_subject] = "needs " + ", ".join(
                        n for n in needs if n in left_out)
                    changed = True
        for _stage, fs_subject, _needs in plan:
            folder = paths.FREESURFER_DIR / fs_subject
            if fs_subject in left_out or not folder.is_dir():
                continue
            dest = paths.FREESURFER_DIR / ".failed" / fs_subject
            shutil.rmtree(dest, ignore_errors=True)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(folder), str(dest))
            run.say(f"    moved the unfinished {fs_subject} to .failed/ "
                    "(restarting it from scratch)")
        return left_out

    def reason(self, run, item, log_path):
        stage = next((s for s, f, _n in self.plan() if f == item), "cross")
        return last_error(paths.FREESURFER_DIR / "logs" / f"{item}.{stage}.log")

    def counts_as_failure(self, item):
        """A base/long run that failed because a timepoint it needs failed in
        the same batch is not its own failure."""
        needs = next((n for _s, f, n in self.plan() if f == item), [])
        return all(rrl.recon_done(str(paths.FREESURFER_DIR), n) for n in needs)


class Stats(Step):
    fs = True

    def signature(self):
        import extract_fs_stats as efs
        subs = efs.find_subjects(str(paths.FREESURFER_DIR))
        text = "\n".join(
            f"{s}:{(paths.FREESURFER_DIR / s / 'stats' / 'aseg.stats').stat().st_mtime_ns}"
            for s in subs)
        return hashlib.sha256(text.encode()).hexdigest() if subs else ""

    def todo(self, run):
        sig = self.signature()
        if not sig or self.name in run.done_now:
            return [], ""
        table = paths.FREESURFER_DIR / "stats" / "aseg_volumes.tsv"
        stale = sig != run.state["signatures"].get(self.name) or not table.exists()
        return (["tables"] if stale else []), ""

    def args(self, run):
        return ["--subjects-dir", str(paths.FREESURFER_DIR),
                "--output-dir", str(paths.FREESURFER_DIR / "stats")]

    def finish(self, run, rc):
        if rc == 0:
            run.state["signatures"][self.name] = self.signature()
            run.done_now.add(self.name)


STEPS = [
    Import("import", "Import delivered BIDS", "import_bids.py"),
    Convert("convert", "DICOM -> NIfTI (new exams)", "dicom_to_bids.py"),
    Deidentify("deidentify", "De-identify sidecars", "deidentify_sidecars.py"),
    MakeBids("bids", "Build raw BIDS", "make_bids.py"),
    MakeDerivatives("derivatives", "Build derivatives + sourcedata", "make_derivatives.py"),
    Deface("deface", "Deface anatomicals", "deface_anat.py"),
    Samseg("samseg", "SAMSEG white-matter lesions", "run_samseg.py"),
    Recon("recon", "FreeSurfer recon (cross/base/long)", "run_recon_long.py"),
    Stats("stats", "FreeSurfer tables", "extract_fs_stats.py"),
]


def project_steps():
    return [s for s in STEPS if s.applies()]


# ===========================================================================
#  One batch run
# ===========================================================================

class Run:
    def __init__(self, args):
        self.args = args
        self.started = datetime.now()
        stamp = self.started.strftime("%Y%m%d-%H%M%S")
        self.run_dir = paths.BATCH_DIR / stamp
        n = 1
        while self.run_dir.exists():
            n += 1
            self.run_dir = paths.BATCH_DIR / f"{stamp}-{n}"
        self.lines = []          # batch.log
        self.rows = []           # summary facts: (section, step, item, status, detail)
        self.status = {}         # step -> ran / skipped / failed / blocked
        self.done_now = set()    # signature steps finished in this run
        self.exams = []          # convert plan (exam folder states)
        self.result = "OK"
        self.state = load_state()
        self.redact = make_redactor()
        self.recon_jobs = args.jobs or auto_jobs(args.threads, 8)
        self.samseg_jobs = args.samseg_jobs or auto_jobs(args.samseg_threads, 12)
        self.n_logs = 0

    def say(self, msg=""):
        print(msg, flush=True)
        self.lines.append(msg)

    def fact(self, section, step="", item="", status="", detail=""):
        row = (section, step, item, status, detail)
        if row not in self.rows:
            self.rows.append(row)

    def errors_in(self, log_path):
        """'ERROR: ...' lines a step printed (session-order stops etc.)."""
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                return [m.group(1).strip() for m in
                        (re.match(r"\s*ERROR:\s*(.*)", ln) for ln in f) if m]
        except OSError:
            return []

    # --- retries -----------------------------------------------------------
    def failure_key(self, step, item):
        return f"{step}:{item}"

    def given_up(self, step, items):
        limit = self.args.max_attempts
        return [i for i in items if limit and
                self.state["failures"].get(self.failure_key(step, i), {}).get("count", 0) >= limit]

    def note_failure(self, step, item, reason):
        entry = self.state["failures"].setdefault(self.failure_key(step, item), {"count": 0})
        entry["count"] += 1
        entry["last"] = self.started.isoformat(timespec="seconds")
        entry["reason"] = reason
        return entry["count"]

    # --- running one step --------------------------------------------------
    def run_logged(self, step, cmd):
        """Run a step's script with its output (masked) in NN_<step>.log."""
        self.n_logs += 1
        log_path = self.run_dir / f"{self.n_logs:02d}_{step.name}.log"
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        with open(log_path, "w", encoding="utf-8") as log:
            log.write("$ " + " ".join(cmd) + "\n\n")
            log.flush()
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, errors="replace", env=env,
                                    cwd=str(paths.REPO_ROOT), start_new_session=True)
            CHILD["proc"] = proc
            for line in proc.stdout:
                log.write(self.redact(line))
                log.flush()
            rc = proc.wait()
            CHILD["proc"] = None
        return rc, log_path

    def do(self, step):
        if step.fs and self.args.no_freesurfer:
            self.status[step.name] = "skipped"
            self.say(f"[{step.name}] skipped (--no-freesurfer)")
            self.fact("step", step.name, "", "skipped", "--no-freesurfer")
            return
        blocked = [r for r in step.requires if self.status.get(r) in ("failed", "blocked")]
        if blocked:
            self.status[step.name] = "blocked"
            self.say(f"[{step.name}] not run: {', '.join(blocked)} failed")
            self.fact("step", step.name, "", "blocked", f"{', '.join(blocked)} failed")
            return
        if step.name == "convert":
            self.redact = make_redactor()       # include folders dropped since
            self.wait_for_copies(step)
        items, note = step.todo(self)
        left_out = step.prepare(self, items) if items else {}
        active = [i for i in items if i not in left_out]
        for item, why in sorted(left_out.items()):
            if why == "gave up":
                entry = self.state["failures"].get(self.failure_key(step.name, item), {})
                self.fact("human", "gave-up", f"{step.name} {item}", "",
                          f"failed {entry.get('count')} times, last: "
                          f"{entry.get('reason', '?')} (fix it, then run with --retry-failed)")
            else:
                self.fact("human", "left-out", f"{step.name} {item}", "", why)
        if not active:
            self.status[step.name] = "skipped"
            detail = "nothing new" + (f" ({note})" if note else "")
            if left_out:
                detail += f"; {len(left_out)} left out"
            self.say(f"[{step.name}] {detail}")
            self.fact("step", step.name, "", "skipped", detail)
            return

        self.say(f"[{step.name}] {step.title}: {len(active)} to do"
                 + (f" ({note})" if note else ""))
        for item in active[:12]:
            self.say(f"    - {item}")
        if len(active) > 12:
            self.say(f"    ... and {len(active) - 12} more")
        extra = ["--exclude", ",".join(sorted(left_out))] if left_out else []
        cmd = ([sys.executable, str(paths.CODE_DIR / step.script),
                "--project", paths.PROJECT_NAME] + step.args(self) + extra)
        t0 = time.time()
        rc, log_path = self.run_logged(step, cmd)
        step.finish(self, rc)
        still, _ = step.todo(self)
        failed = [i for i in active if i in still]
        minutes = (time.time() - t0) / 60
        status = "ran" if rc == 0 and not failed else "failed"
        self.status[step.name] = status
        detail = (f"{len(active) - len(failed)}/{len(active)} done in {minutes:.1f} min"
                  f" (log {log_path.name})")
        if rc != 0 and not failed:
            detail += f"; exit code {rc}: {last_error(log_path)}"
        self.say(f"[{step.name}] {'OK' if status == 'ran' else 'FAILED'}: {detail}")
        self.fact("step", step.name, "", status, detail)

        for item in failed:
            why = step.reason(self, item, log_path)
            if step.retry_limited and getattr(step, "counts_as_failure", lambda i: True)(item):
                n = self.note_failure(step.name, item, why)
                why += f" (attempt {n} of {self.args.max_attempts}; retried next run)"
            else:
                why += " (retried next run)"
            self.say(f"    FAILED {item}: {why}")
            self.fact("failed", step.name, item, "failed", why)
        for item in active:
            if item not in failed:
                self.state["failures"].pop(self.failure_key(step.name, item), None)
        if step.name in ("convert", "import"):
            for err in self.errors_in(log_path):
                self.fact("human", f"{step.name}-stop", "", "", err)
        if step.name == "import":
            for problem in import_problems(log_path):
                self.fact("human", "import-check", "", "", problem)

    def wait_for_copies(self, step):
        """With --settle-wait, give CD folders still being copied time to finish."""
        deadline = time.time() + self.args.settle_wait * 60
        while self.args.settle_wait and time.time() < deadline:
            step.todo(self)
            if not any(e["state"] == "settling" for e in self.exams):
                return
            self.say("    waiting for an exam folder still being copied ...")
            time.sleep(60)

    # --- what needs a person -----------------------------------------------
    def needs_person(self):
        conv = paths.CONVERSION_DIR
        for e in self.exams:
            if e["state"] == "settling":
                self.fact("human", "copy-in-progress", f"exam folder #{e['index']}", "",
                          f"changed in the last {self.args.settle_minutes:g} min; "
                          "converted on the next run")
            elif e["state"] == "no-code":
                self.fact("human", "no-code", f"exam folder #{e['index']}", "",
                          "no study code in the folder name: rename it (e.g. GER00039_...)")
        unknown = read_csv(conv / "reports" / "unknown_series_report.csv")
        for r in unknown[:20]:
            self.fact("human", "unknown-series",
                      f"{r['subject']}/{r['session']} series {r['series_number']}", "",
                      f"{r['series_description']!r}: {r['suggestion']}")
        if len(unknown) > 20:
            self.fact("human", "unknown-series", "", "", f"... and {len(unknown) - 20} more")
        mismatch = {}
        for r in read_csv(conv / "validation_report.csv"):
            if r.get("status") == "MISMATCH":
                mismatch.setdefault(f"{r['subject']}/{r['session']}", []).append(r["field"])
        for item, fields in sorted(mismatch.items()):
            self.fact("human", "mismatch", item, "",
                      f"DICOM vs JSON differ in {', '.join(sorted(set(fields)))} "
                      "(validation_report.csv)")
        for r in qc_status.failed(str(paths.QC_FILE)):
            self.fact("human", "qc-failed", f"{r['subject']}/{r['session']} {r['kind']}",
                      "", r.get("note") or "marked fail in QC")
        for item in qc_status.pending(str(paths.FREESURFER_DIR), str(paths.SAMSEG_DIR),
                                      str(paths.QC_FILE)):
            self.fact("qc", item["kind"], f"{item['subject']}/{item['session']}", "pending")

    # --- summary -----------------------------------------------------------
    def write_summary(self):
        finished = datetime.now()
        self.fact("run", "", "finished", "", finished.isoformat(timespec="seconds"))
        self.fact("run", "", "result", self.result, "")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with open(self.run_dir / "summary.tsv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter="\t", lineterminator="\n")
            w.writerow(["section", "step", "item", "status", "detail"])
            w.writerows(self.rows)
        (self.run_dir / "summary.md").write_text(self.markdown(finished), encoding="utf-8")
        (self.run_dir / "batch.log").write_text("\n".join(self.lines) + "\n",
                                                encoding="utf-8")
        for name in ("summary.md", "summary.tsv"):
            shutil.copy(self.run_dir / name, paths.BATCH_DIR / f"last_{name}")
        runs = sorted(p for p in paths.BATCH_DIR.iterdir()
                      if p.is_dir() and re.match(r"\d{8}-\d{6}", p.name))
        for old in runs[:-KEEP_RUNS]:
            shutil.rmtree(old, ignore_errors=True)

    def markdown(self, finished):
        rows = self.rows
        pick = lambda sec: [r for r in rows if r[0] == sec]
        info = {r[2]: r[4] for r in pick("run")}
        md = [f"# Batch run - {paths.PROJECT_NAME}", "",
              f"- result: **{self.result}**",
              f"- started {self.started.isoformat(timespec='seconds')}, finished "
              f"{finished.isoformat(timespec='seconds')} "
              f"({(finished - self.started).total_seconds() / 60:.1f} min)"]
        for key in ("settings", "freesurfer", "disk", "awake"):
            if key in info:
                md.append(f"- {key}: {info[key]}")
        if pick("refused"):
            md += ["", "## Refused to start", ""]
            md += [f"- {r[4]}" for r in pick("refused")]
        if pick("step"):
            md += ["", "## Steps", "", "| step | result | detail |", "|---|---|---|"]
            md += [f"| {r[1]} | {r[3]} | {r[4]} |" for r in pick("step")]
        if pick("failed"):
            md += ["", "## Failed (retried on the next run)", ""]
            md += [f"- {r[1]} `{r[2]}`: {r[4]}" for r in pick("failed")]
        human = pick("human")
        md += ["", "## Needs a person", ""]
        md += [f"- {r[1]}{' `' + r[2] + '`' if r[2] else ''}: {r[4]}" for r in human] \
            or ["- nothing"]
        qc = pick("qc")
        md += ["", "## QC to do", ""]
        if qc:
            for kind in qc_status.KINDS:
                items = [r[2] for r in qc if r[1] == kind]
                if items:
                    md.append(f"- {kind}: {len(items)} session(s): " + ", ".join(items[:30])
                              + (" ..." if len(items) > 30 else ""))
            md.append("- open them from the student menu (item C) or with freeview.")
        else:
            md.append("- nothing")
        return "\n".join(md) + "\n"


# ===========================================================================
#  Checks before starting
# ===========================================================================

def license_file(fs_home):
    for candidate in (os.environ.get("FS_LICENSE"),
                      os.path.join(fs_home, "license.txt"),
                      os.path.join(fs_home, ".license")):
        if candidate and os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
            return candidate
    return None


def preflight(args):
    """Reasons not to start (empty list = go)."""
    problems = []
    steps = project_steps()
    free_gb = shutil.disk_usage(paths.PROJECT_ROOT).free / GB
    if free_gb < args.min_free_gb:
        problems.append(f"only {free_gb:.0f} GB free on the disk of {paths.PROJECT_ROOT} "
                        f"(at least {args.min_free_gb:g} GB wanted: a recon-all session "
                        "takes ~1 GB, a CD ~1 GB). Free some space, or lower --min-free-gb.")
    if any(s.name == "convert" for s in steps) and paths.PATIENT_FILES.is_dir() \
            and any(p.is_dir() for p in paths.PATIENT_FILES.iterdir()):
        try:
            import dicom_to_bids as d2b
            if not d2b.find_dcm2niix():
                problems.append("dcm2niix not found: python3 -m pip install -r requirements.txt")
        except SystemExit:
            problems.append("pydicom is not installed: python3 -m pip install -r requirements.txt")
    if args.no_freesurfer or not any(s.fs for s in steps):
        return problems
    fs_home = os.environ.get("FREESURFER_HOME", "")
    how = ("load it first (export FREESURFER_HOME=/Applications/freesurfer/8.2.0; "
           "source $FREESURFER_HOME/SetUpFreeSurfer.sh), or run with --no-freesurfer "
           "for the steps without FreeSurfer. The nightly schedule loads it itself "
           "(install_schedule.py --freesurfer-home).")
    if not fs_home or not os.path.isdir(fs_home):
        problems.append("FreeSurfer is not loaded (FREESURFER_HOME is not set): " + how)
    elif not shutil.which("recon-all"):
        problems.append(f"recon-all is not on PATH (FREESURFER_HOME={fs_home}): " + how)
    elif not license_file(fs_home):
        problems.append(f"no FreeSurfer license: put license.txt in {fs_home} (free, "
                        "surfer.nmr.mgh.harvard.edu/registration.html) or set FS_LICENSE.")
    elif any(s.name == "samseg" for s in steps) and run_samseg.rosetta_freesurfer(fs_home):
        problems.append("this FreeSurfer is an Intel build running under Rosetta: SAMSEG "
                        "--lesion needs AVX and aborts (Abort trap: 6). Use the native "
                        "arm64 FreeSurfer of the Mac Mini.")
    return problems


def fs_version():
    return rrl.fs_build_stamp(os.environ.get("FREESURFER_HOME")) or "unknown"


# ===========================================================================
#  Main
# ===========================================================================

def dry_run(args):
    print("=" * 70)
    print(f" Batch plan (dry run) - project {paths.PROJECT_NAME}; nothing is run")
    print("=" * 70)
    if batch_lock.is_held(str(paths.BATCH_LOCK)):
        h = batch_lock.holder(str(paths.BATCH_LOCK))
        print(f" NOTE: a batch is running now (project {h.get('project')}, "
              f"since {h.get('started')}).")
    for p in preflight(args):
        print(f" WOULD REFUSE: {p}")
    run = Run.__new__(Run)
    run.args, run.status, run.done_now, run.exams = args, {}, set(), []
    run.state = load_state()
    for step in project_steps():
        if step.fs and args.no_freesurfer:
            print(f"  {step.name:<12} skipped (--no-freesurfer)")
            continue
        items, note = step.todo(run)
        gave_up = run.given_up(step.name, items) if step.retry_limited else []
        items = [i for i in items if i not in gave_up]
        print(f"  {step.name:<12} {len(items)} to do" + (f"  ({note})" if note else "")
              + (f"  [{len(gave_up)} gave up]" if gave_up else ""))
        for item in items[:8]:
            print(f"      - {item}")
        if len(items) > 8:
            print(f"      ... and {len(items) - 8} more")
    print("\n (steps after a conversion also run for what it produces)")


def stop_handler(signum, _frame):
    proc = CHILD["proc"]
    if proc and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
    raise SystemExit(128 + signum)


def main():
    parser = argparse.ArgumentParser(
        description="Run every pending pipeline step of a project, unattended.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would run; run nothing, write nothing.")
    parser.add_argument("--jobs", type=int, default=0,
                        help="recon-all runs in parallel (default: auto from cores/RAM).")
    parser.add_argument("--threads", type=int, default=2,
                        help="OpenMP threads per recon-all (default 2).")
    parser.add_argument("--samseg-jobs", type=int, default=0,
                        help="SAMSEG sessions in parallel (default: auto).")
    parser.add_argument("--samseg-threads", type=int, default=4,
                        help="threads per SAMSEG run (default 4).")
    parser.add_argument("--no-freesurfer", action="store_true",
                        help="Only the steps without FreeSurfer (conversion, BIDS).")
    parser.add_argument("--min-free-gb", type=float, default=50,
                        help="Refuse to start below this much free disk (default 50).")
    parser.add_argument("--settle-minutes", type=float, default=30,
                        help="An exam folder changed this recently is left for the "
                             "next run (still being copied?). Default 30.")
    parser.add_argument("--settle-wait", type=float, default=0,
                        help="Wait up to this many minutes for such folders instead "
                             "(default 0: next run).")
    parser.add_argument("--max-attempts", type=int, default=3,
                        help="Stop retrying a recon/SAMSEG run after this many "
                             "failures (default 3; 0 = never stop).")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Forget past failures and try every item again.")
    parser.add_argument("--wait", type=float, default=0,
                        help="If another batch is running, wait up to this many "
                             "minutes for it (default 0: refuse at once).")
    parser.add_argument("--no-caffeinate", action="store_true",
                        help="Do not keep the Mac awake while running.")
    args = parser.parse_args()

    if args.dry_run:
        dry_run(args)
        return

    lock = batch_lock.acquire(str(paths.BATCH_LOCK),
                              {"project": paths.PROJECT_NAME, "pid": os.getpid(),
                               "started": datetime.now().isoformat(timespec="seconds")},
                              wait_seconds=args.wait * 60)
    if lock is None:
        h = batch_lock.holder(str(paths.BATCH_LOCK))
        print(f"ANOTHER BATCH IS RUNNING (project {h.get('project', '?')}, pid "
              f"{h.get('pid', '?')}, since {h.get('started', '?')}). Not starting; "
              "run again later or use --wait MINUTES.")
        sys.exit(3)

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    awake = None
    if not args.no_caffeinate and shutil.which("caffeinate"):
        awake = subprocess.Popen(["caffeinate", "-is", "-w", str(os.getpid())],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if args.retry_failed:
        state = load_state()
        state["failures"] = {}
        save_state(state)

    run = Run(args)
    run.run_dir.mkdir(parents=True, exist_ok=True)
    exit_code = 0
    try:
        run.say("=" * 70)
        run.say(f" BATCH  project {paths.PROJECT_NAME}   started "
                f"{run.started.isoformat(timespec='seconds')}")
        run.say(f" logs: {run.run_dir}")
        run.say("=" * 70)
        free_gb = shutil.disk_usage(paths.PROJECT_ROOT).free / GB
        run.fact("run", "", "project", "", paths.PROJECT_NAME)
        run.fact("run", "", "started", "", run.started.isoformat(timespec="seconds"))
        run.fact("run", "", "settings", "",
                 f"recon {run.recon_jobs} job(s) x {args.threads} thread(s); SAMSEG "
                 f"{run.samseg_jobs} x {args.samseg_threads}; max attempts "
                 f"{args.max_attempts}")
        run.fact("run", "", "disk", "", f"{free_gb:.0f} GB free")
        run.fact("run", "", "awake", "", "caffeinate -is" if awake else "not kept awake")
        problems = preflight(args)
        for p in problems:
            run.say(f"REFUSED: {p}")
            run.fact("refused", "", "", "", p)
        if problems:
            run.result, exit_code = "REFUSED", 2
        else:
            if not args.no_freesurfer:
                run.fact("run", "", "freesurfer", "", fs_version())
            for step in project_steps():
                run.do(step)
            run.needs_person()
            if any(s == "failed" for s in run.status.values()):
                run.result, exit_code = "FAILED", 1
    except SystemExit as stop:
        run.result = "STOPPED"
        run.say("STOPPED (signal); the next run resumes where this one was.")
        exit_code = stop.code if isinstance(stop.code, int) else 1
    except Exception:
        import traceback
        run.result, exit_code = "FAILED", 1
        run.say(run.redact(traceback.format_exc()))
        run.fact("failed", "batch", "", "failed", "unexpected error (see batch.log)")
    finally:
        save_state(run.state)
        run.write_summary()
        if awake:
            awake.terminate()
        lock.close()
    ran = [s for s, v in run.status.items() if v == "ran"]
    print(f"\n RESULT: {run.result}.  ran: {', '.join(ran) or 'nothing'}")
    print(f" summary: {run.run_dir / 'summary.md'}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

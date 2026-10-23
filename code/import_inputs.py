"""
import_inputs.py
================

Put data that arrives from outside into THIS project's input folders, where
the batch (run_batch.py) picks it up. pipeline.py calls it after working out,
from the study code in each name, which project an item belongs to; it can
also be used alone:

    python code/import_inputs.py --project COGAGING --kind dicom /path/CD_GER00039_...
    python code/import_inputs.py --project COGAGING --kind bids  /path/sub-GER00050
    python code/import_inputs.py --project CBR --kind freesurfer /path/CBR-003.2
    python code/import_inputs.py --project COGAGING --kind session-map /old/session_map.tsv
    (--dry-run shows the plan; --move moves instead of copying, same disk only)

WHERE EACH KIND GOES (folders from paths.py)
  dicom        one exam folder         -> Patient Files/<same folder name>
  bids         one delivered sub-*     -> sourcedata/received/<sub-*>   (bids_import projects)
  freesurfer   one FINISHED FreeSurfer subject folder, renamed to this project's
               convention (sub-X_ses-N, base sub-X, long sub-X_ses-N.long.sub-X):
                 made by the FreeSurfer loaded now (same build stamp)
                     -> bids/derivatives/freesurfer/         (the batch will not redo it)
                 made by another version (e.g. 7.4.1)
                     -> bids/derivatives/freesurfer-<version>/  (never mixed into the cohort)
  session-map  an older sourcedata/session_map.tsv, when this project has none

IT NEVER
  * changes or deletes a source: it copies (verifying the copy); --move only
    renames on the same disk, and the inbox/ of pipeline.py uses that;
  * overwrites: a destination with the same content is "already there",
    with different content it is reported and nothing is done;
  * guesses: what it cannot place safely (no study code, a FreeSurfer run of a
    date-ordered project without a session in its name, an unfinished run, a
    build of the same version that differs, ...) is reported and left alone;
  * writes a source folder name, which may carry a patient name: items are
    shown by study code only. Report: logs/import/import_<time>.md.

The machine-wide batch lock is held while copying, so a batch never sees a
half-copied exam (copies are staged in projects/<NAME>/.importing/ and moved
into place in one step).
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import paths
import batch_lock
import run_recon_long as rrl
from setup_workspace import _has_dicom as has_dicom

PATTERN = paths.PROJECT.get("code_pattern") or (paths.PROJECT.get("subject_prefix", "") + r"\d+")
STAGING = paths.PROJECT_ROOT / ".importing"
SKIP_FILES = {".DS_Store", "Thumbs.db"}


def code_label(text):
    """'sub-' + the study code found in `text` ('CBR-003' -> 'sub-CBR003'), or None."""
    m = re.search(PATTERN, text or "", re.IGNORECASE)
    return "sub-" + re.sub(r"[^0-9A-Za-z]", "", m.group(0)).upper() if m else None


def fingerprint(path):
    """Hash of every file's relative path and size: 'is this the same content?'
    (cheap: no file is read)."""
    path = Path(path)
    h = hashlib.sha256()
    if path.is_file():
        h.update(f"{path.stat().st_size}".encode())
        h.update(path.read_bytes())
        return h.hexdigest()
    for f in sorted(p for p in path.rglob("*") if p.is_file() and p.name not in SKIP_FILES):
        h.update(f"{f.relative_to(path).as_posix()}\0{f.stat().st_size}\n".encode())
    return h.hexdigest()


def place(src, dest, move, dry_run):
    """Copy (or move) src to dest unless dest exists. Returns (action, detail)."""
    src, dest = Path(src), Path(dest)
    if dest.exists():
        if fingerprint(src) == fingerprint(dest):
            return "already there", ""
        return "CONFLICT", "a different item with this name is already there; nothing done"
    if dry_run:
        return ("would move" if move else "would copy"), ""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if move and os.stat(src).st_dev == os.stat(dest.parent).st_dev:
        os.rename(src, dest)
        return "moved", ""
    STAGING.mkdir(parents=True, exist_ok=True)
    stage = STAGING / (dest.name + ".part")
    if stage.exists():
        shutil.rmtree(stage) if stage.is_dir() else stage.unlink()
    if src.is_dir():
        shutil.copytree(src, stage, copy_function=shutil.copy2,
                        ignore=shutil.ignore_patterns(*SKIP_FILES))
    else:
        shutil.copy2(src, stage)
    if fingerprint(src) != fingerprint(stage):
        shutil.rmtree(stage) if stage.is_dir() else stage.unlink()
        return "FAILED", "the copy did not match the source; nothing placed"
    os.replace(stage, dest)
    return "copied", ""


# ---------------------------------------------------------------------------
# FreeSurfer subject folders
# ---------------------------------------------------------------------------

def fs_name(folder):
    """(this project's FreeSurfer id, why-not). Base and long runs must already
    use the convention (their links to the timepoints depend on the names)."""
    name = folder.name
    sub = code_label(name)
    if sub is None:
        return None, f"no {paths.PROJECT_NAME} code in its name"
    m = re.fullmatch(r"(sub-[A-Za-z0-9]+_ses-[A-Za-z0-9]+)\.long\.(sub-[A-Za-z0-9]+)", name)
    if m:
        return name, ""
    tps_file = folder / "base-tps"
    if tps_file.is_file():
        tps = tps_file.read_text(errors="replace").split()
        if re.fullmatch(r"sub-[A-Za-z0-9]+", name) and all(
                re.fullmatch(rf"{re.escape(name)}_ses-[A-Za-z0-9]+", t) for t in tps):
            return name, ""
        return None, ("a longitudinal base whose name or timepoints do not follow "
                      "sub-X / sub-X_ses-N; rename them by hand first")
    m = re.fullmatch(r"(sub-[A-Za-z0-9]+)_(ses-[A-Za-z0-9]+)", name)
    if m:
        return f"{sub}_{m.group(2)}", ""
    if paths.PROJECT.get("sessions") == "folder_suffix":
        # The study's folder convention (CBR-003.2 -> ses-2), as dicom_to_bids.py.
        import dicom_to_bids as d2b
        sub2, ses = d2b.parse_subject_session(name, d2b.load_subjects_map())
        return f"{sub2}_{ses}", ""
    return None, ("no session in its name and sessions here are numbered by exam "
                  "date: rename it to <sub>_ses-N (see sourcedata/session_map.tsv)")


def reference_stamp():
    """The FreeSurfer build the cohort uses: the one loaded now, else the one
    already in the project's FreeSurfer folder (if there is exactly one)."""
    loaded = rrl.fs_build_stamp(os.environ.get("FREESURFER_HOME"))
    if loaded:
        return loaded
    stamps = rrl.stamps_in(str(paths.FREESURFER_DIR)) if paths.FREESURFER_DIR.is_dir() else {}
    return next(iter(stamps)) if len(stamps) == 1 else ""


def version_of(stamp):
    m = re.search(r"(\d+\.\d+\.\d+)", stamp or "")
    return m.group(1) if m else "unknown"


def write_versioned_description(folder, stamp):
    desc = folder / "dataset_description.json"
    if desc.exists():
        return
    folder.mkdir(parents=True, exist_ok=True)
    desc.write_text(json.dumps({
        "Name": f"{paths.PROJECT['title']} FreeSurfer recon (imported, {version_of(stamp)})",
        "BIDSVersion": "1.9.0", "DatasetType": "derivative",
        "GeneratedBy": [{"Name": "freesurfer", "Version": stamp or "unknown",
                         "Description": "Imported finished runs made with another "
                                        "FreeSurfer version; kept apart from "
                                        "derivatives/freesurfer (one version per cohort)."}],
    }, indent="\t") + "\n", encoding="utf-8")
    (folder / ".bidsignore").write_text("sub-*/\n", encoding="utf-8")


def import_freesurfer(src, move, dry_run):
    src = Path(src)
    if not (src / "scripts" / "recon-all.done").is_file():
        return None, "LEFT ALONE", "unfinished run (no scripts/recon-all.done)"
    target, why = fs_name(src)
    if target is None:
        return code_label(src.name), "LEFT ALONE", why
    stamp_file = src / "scripts" / "build-stamp.txt"
    stamp = stamp_file.read_text(errors="replace").strip() if stamp_file.is_file() else ""
    ref = reference_stamp()
    if ref and stamp == ref:
        folder = paths.FREESURFER_DIR
    elif ref and version_of(stamp) == version_of(ref):
        return target, "LEFT ALONE", (f"FreeSurfer {version_of(stamp)} but another build "
                                      "than the one in use; decide by hand")
    else:
        folder = paths.BIDS_ROOT / "derivatives" / f"freesurfer-{version_of(stamp)}"
    action, detail = place(src, folder / target, move, dry_run)
    if action in ("copied", "moved") and folder != paths.FREESURFER_DIR:
        write_versioned_description(folder, stamp)
    where = os.path.relpath(folder / target, paths.PROJECT_ROOT)
    return target, action, detail or f"-> {where} (FreeSurfer {version_of(stamp)})"


# ---------------------------------------------------------------------------
# DICOM exams, delivered BIDS, session map
# ---------------------------------------------------------------------------

def import_dicom(src, move, dry_run):
    src = Path(src)
    label = code_label(src.name)
    if label is None:
        return None, "LEFT ALONE", f"no {paths.PROJECT_NAME} code in its name"
    if not has_dicom(src):
        return label, "LEFT ALONE", "no DICOM file inside"
    action, detail = place(src, paths.PATIENT_FILES / src.name, move, dry_run)
    return label, action, detail or "-> Patient Files/"


def import_bids(src, move, dry_run):
    src = Path(src)
    label = code_label(src.name)
    if not (paths.PROJECT.get("bids_import") or paths.PROJECT.get("input") == "bids"):
        return label, "LEFT ALONE", (f"{paths.PROJECT_NAME} does not take delivered "
                                     "BIDS (project.json bids_import)")
    if label is None or not re.fullmatch(r"sub-[A-Za-z0-9]+", src.name):
        return label, "LEFT ALONE", "not a sub-<code> BIDS subject folder"
    action, detail = place(src, paths.RECEIVED_DIR / src.name, move, dry_run)
    return label, action, detail or "-> sourcedata/received/"


def import_session_map(src, move, dry_run):
    action, detail = place(src, paths.SESSION_MAP, False, dry_run)
    if action == "CONFLICT":
        detail = ("this project already has a different session_map.tsv; compare "
                  "them by hand (session numbers must never change)")
    return "session_map.tsv", action, detail or "-> sourcedata/session_map.tsv"


KINDS = {"dicom": import_dicom, "bids": import_bids, "freesurfer": import_freesurfer,
         "session-map": import_session_map}


def main():
    parser = argparse.ArgumentParser(
        description="Place incoming data into this project's input folders.")
    parser.add_argument("--kind", required=True, choices=sorted(KINDS))
    parser.add_argument("sources", nargs="+")
    parser.add_argument("--move", action="store_true",
                        help="Move instead of copy (only on the same disk).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen; change nothing.")
    parser.add_argument("--wait", type=float, default=0,
                        help="Minutes to wait if a batch is running (default 0).")
    parser.add_argument("--json", action="store_true",
                        help="One JSON line per source (for pipeline.py).")
    args = parser.parse_args()

    lock = None
    if not args.dry_run:
        lock = batch_lock.acquire(str(paths.BATCH_LOCK),
                                  {"project": paths.PROJECT_NAME, "pid": os.getpid(),
                                   "started": datetime.now().isoformat(timespec="seconds"),
                                   "what": "import"}, wait_seconds=args.wait * 60)
        if lock is None:
            print("A BATCH IS RUNNING: not importing now (try again later, or --wait).")
            if args.json:
                for src in args.sources:
                    print(json.dumps({"kind": args.kind, "item": "", "action": "WAITING",
                                      "detail": "a batch is running; next time"}))
            sys.exit(3)
    rows, failed = [], False
    try:
        for k, src in enumerate(args.sources, start=1):
            item, action, detail = KINDS[args.kind](src, args.move, args.dry_run)
            shown = item or f"item {k}"
            rows.append((args.kind, shown, action, detail))
            if args.json:
                print(json.dumps({"kind": args.kind, "item": shown, "action": action,
                                  "detail": detail}), flush=True)
            else:
                print(f"  {args.kind:<11} {shown:<38} {action:<13} {detail}")
            failed |= action == "FAILED"
    finally:
        if STAGING.is_dir() and not any(STAGING.iterdir()):
            STAGING.rmdir()
        if lock:
            lock.close()
    if not args.dry_run and rows:
        log_dir = paths.PROJECT_ROOT / "logs" / "import"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        with open(log_dir / f"import_{stamp}_{args.kind}.md", "a", encoding="utf-8") as f:
            f.write(f"# Import into {paths.PROJECT_NAME} ({args.kind}), {stamp}\n\n"
                    "| item | result | detail |\n|---|---|---|\n")
            f.writelines(f"| {r[1]} | {r[2]} | {r[3]} |\n" for r in rows)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

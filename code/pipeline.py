"""
pipeline.py
===========

The plug-and-play front door, for every project at once. Nobody picks a
project: each item is routed by the study code in its name (project.json
"code_pattern": CBR-003... -> CBR, GER00039_... -> COGAGING). The projects are
independent of each other; they only share this code.

    python3 code/pipeline.py import [FOLDER ...]   # place new data (default: inbox/)
    python3 code/pipeline.py run                   # import inbox/, then each project's batch
    python3 code/pipeline.py schedule --at 22:30   # `run` every night (launchd)
    python3 code/pipeline.py status                # what each project holds
start.command (repository root) does it all from one double-click.

WHAT IT RECOGNISES, at any depth inside the folders it is given
  DICOM exam       a folder with a study code in its name and DICOM files inside
  delivered BIDS   sub-<code>/ folders (dcm2bids), or a BIDS dataset holding them
  FreeSurfer run   a subject folder (mri/ + scripts/)
  old pipeline     a copy of this pipeline, in any older layout: its exams,
                   delivered BIDS, session maps and FreeSurfer runs are taken;
                   what the batch rebuilds from the DICOMs (bids/ itself, the
                   conversion tree) is not
Each item then goes to its project through import_inputs.py, which decides the
exact place and never overwrites, guesses or deletes (see there).

inbox/ (next to projects/, git-ignored) is the drop box: what is dropped there
is MOVED into place by the next `import` or `run`, nightly too. Folders named
on the command line are COPIED and stay as they are. Only study codes are
printed; --show-paths (start.command, on screen only) adds where an item that
was left alone is, so a person can find it.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CODE = REPO_ROOT / "code"
# Same rule as paths.py, which needs one project; this script serves them all.
PROJECTS_DIR = Path(os.environ.get("PIPELINE_PROJECTS_DIR") or REPO_ROOT / "projects")
INBOX = PROJECTS_DIR.parent / "inbox"
FS_SKIP = {"fsaverage", "logs", "stats"}

INBOX_README = """\
DROP NEW DATA HERE / COLOQUE OS DADOS NOVOS AQUI
================================================

Anything dropped here is moved to the right project by the next run
(start.command, or the nightly job): the study code in each name decides
(CBR-003... -> CBR, GER00039_... -> COGAGING).

  * a DICOM CD folder (its name must contain the code, e.g. GER00039_...)
  * a dcm2bids subject folder (sub-GER00050)
  * a finished FreeSurfer subject folder (sub-GER00039_ses-1, CBR-003.2, ...)
  * a whole old copy of the pipeline

What cannot be placed safely stays here, and the run says why.
Patient data: this folder is on THIS computer only and is never sent to GitHub.
"""


def projects():
    """{name: project.json} of every project."""
    out = {}
    for f in sorted(PROJECTS_DIR.glob("*/project.json")):
        try:
            out[f.parent.name] = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return out


def project_of(name, confs):
    """(project, why-not) from the study code in a name."""
    hits = [p for p, c in confs.items()
            if c.get("code_pattern") and re.search(c["code_pattern"], name, re.IGNORECASE)]
    if len(hits) == 1:
        return hits[0], ""
    if hits:
        return None, "codes of more than one project in its name"
    return None, "no study code in its name (" + ", ".join(
        f"{c.get('subject_prefix', p)}... = {p}" for p, c in confs.items()) + ")"


def has_dicom(folder, limit=4000):
    """At least one file with the DICOM magic bytes (same check as setup_workspace)."""
    checked = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            checked += 1
            if checked > limit:
                return False
            try:
                with open(os.path.join(root, name), "rb") as f:
                    f.seek(128)
                    if f.read(4) == b"DICM":
                        return True
            except OSError:
                pass
    return False


def study_uid(folder):
    """StudyInstanceUID of the first DICOM in a folder ('' if none/unreadable)."""
    try:
        import pydicom
    except ImportError:
        return ""
    for root, _dirs, files in os.walk(folder):
        for name in sorted(files):
            try:
                ds = pydicom.dcmread(os.path.join(root, name), stop_before_pixels=True,
                                     specific_tags=["StudyInstanceUID"])
                return str(getattr(ds, "StudyInstanceUID", ""))
            except Exception:
                continue
    return ""


def subdirs(folder):
    try:
        return sorted(p for p in Path(folder).iterdir()
                      if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []


# ---------------------------------------------------------------------------
# finding things: -> [(kind, project, path, why-not)]
# ---------------------------------------------------------------------------

def is_fs_subject(d):
    return (d / "mri").is_dir() and (d / "scripts").is_dir()


def is_old_pipeline(d):
    return (d / "code").is_dir() and any(
        (d / n).is_dir() for n in ("projects", "Patient Files", "bids", "freesurfer_subjects"))


def fs_runs(root, confs, expected=None):
    found = []
    for d in subdirs(root):
        if d.name in FS_SKIP or not is_fs_subject(d):
            continue
        found.append(item("freesurfer", d, confs, expected))
    return found


def item(kind, path, confs, expected=None):
    project, why = project_of(path.name, confs)
    if expected and project and project != expected:
        return (kind, None, path, f"found in {expected}'s folders but its code says {project}")
    return (kind, project, path, why)


def scan_old_pipeline(d, confs):
    found = []
    for pj in subdirs(d / "projects"):
        expected = pj.name if pj.name in confs else None
        for exam in subdirs(pj / "Patient Files"):
            found += scan(exam, confs, expected=expected)
        for sub in subdirs(pj / "sourcedata" / "received"):
            if sub.name.startswith("sub-"):
                found.append(item("bids", sub, confs, expected))
        smap = pj / "sourcedata" / "session_map.tsv"
        if smap.is_file():
            found.append(("session-map", expected, smap,
                          "" if expected else f"unknown project {pj.name}"))
        for fsroot in sorted((pj / "bids" / "derivatives").glob("freesurfer*")):
            found += fs_runs(fsroot, confs, expected)
    # The older single-study layout: data folders at the top of the copy.
    for exam in subdirs(d / "Patient Files"):
        found += scan(exam, confs)
    for fsroot in [d / "freesurfer_subjects"] + sorted((d / "bids" / "derivatives").glob("freesurfer*")):
        found += fs_runs(fsroot, confs)
    return found


def scan(path, confs, depth=0, expected=None):
    path = Path(path)
    if path.is_file():
        if path.name == "session_map.tsv":
            return [("session-map", None, path, "a session map outside a project folder: "
                     "give the old pipeline folder instead")]
        if path.name.endswith((".nii", ".nii.gz")):
            return [("nifti", None, path, "a loose NIfTI: only BIDS sub-* folders are taken")]
        return []
    if not path.is_dir():
        return []
    if is_old_pipeline(path):
        return scan_old_pipeline(path, confs)
    if is_fs_subject(path):
        return [item("freesurfer", path, confs, expected)]
    if (path / "dataset_description.json").is_file():
        found = []
        if not (path / "sourcedata" / "conversion").is_dir():   # not a pipeline output
            found += [item("bids", s, confs, expected) for s in subdirs(path)
                      if s.name.startswith("sub-")]
        for fsroot in sorted((path / "derivatives").glob("freesurfer*")):
            found += fs_runs(fsroot, confs, expected)
        return found
    if path.name.startswith("sub-") and any(path.rglob("*.nii*")):
        return [item("bids", path, confs, expected)]
    project, _why = project_of(path.name, confs)
    if project and has_dicom(path):
        return [item("dicom", path, confs, expected)]
    found = []
    if depth < 5:
        for child in subdirs(path):
            found += scan(child, confs, depth + 1, expected)
    # DICOMs without any code below: if they are all one study (one exam whose
    # series sit in sub-folders), report the exam once, not each sub-folder.
    unplaced = all(kind == "dicom" and project is None for kind, project, _p, _w in found)
    if unplaced and found and len({study_uid(p) for _k, _pr, p, _w in found}) == 1:
        return [item("dicom", path, confs, expected)]
    if not found and depth > 0 and has_dicom(path):
        return [item("dicom", path, confs, expected)]
    return found


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def place(found, move, dry_run, show_paths, wait):
    """Hand each recognised item to import_inputs.py of its project. Returns
    the number of items now in place (copied, moved or already there)."""
    groups, rows = {}, []
    for kind, project, path, why in found:
        if project is None or kind == "nifti":
            rows.append((kind, "-", "?", "LEFT ALONE", why, path))
        else:
            groups.setdefault((project, kind), []).append(path)
    for (project, kind), srcs in sorted(groups.items()):
        cmd = [sys.executable, str(CODE / "import_inputs.py"), "--project", project,
               "--kind", kind, "--json", "--wait", str(wait), *map(str, srcs)]
        if move:
            cmd.append("--move")
        if dry_run:
            cmd.append("--dry-run")
        out = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
        results = [json.loads(ln) for ln in out.stdout.splitlines() if ln.startswith("{")]
        if len(results) != len(srcs):
            results = [{"item": "", "action": "FAILED",
                        "detail": (out.stdout + out.stderr).strip().splitlines()[-1:]
                        or "import_inputs.py failed"}] * len(srcs)
        for src, r in zip(srcs, results):
            if move and r["action"] == "already there":
                r["detail"] = "(the copy in inbox/ can be deleted)"
            rows.append((kind, project, r["item"] or "?", r["action"], r["detail"], src))
    print(f"\n  {'kind':<11} {'project':<9} {'item':<38} result")
    for kind, project, label, action, detail, src in rows:
        print(f"  {kind:<11} {project:<9} {label:<38} {action}  {detail}")
        if show_paths and action in ("LEFT ALONE", "CONFLICT", "FAILED"):
            print(f"  {'':<11} {'':<9} at: {src}")
    if not rows:
        print("  (nothing new found)")
    return sum(r[3] in ("copied", "moved", "already there", "would copy", "would move")
               for r in rows)


def ensure_inbox():
    INBOX.mkdir(parents=True, exist_ok=True)
    readme = INBOX / "LEIA-ME_README.txt"
    if not readme.exists():
        readme.write_text(INBOX_README, encoding="utf-8")


def inbox_items():
    ensure_inbox()
    return [p for p in sorted(INBOX.iterdir())
            if not p.name.startswith(".") and p.name != "LEIA-ME_README.txt"]


def cmd_import(args):
    confs = projects()
    sources = [Path(s).expanduser() for s in args.folders]
    found = []
    print("=" * 70)
    print(" Import: what is new, and which project it belongs to")
    print("=" * 70)
    if not sources:
        items = inbox_items()
        print(f" inbox/: {len(items)} item(s)")
        for p in items:
            found += scan(p, confs)
        return place(found, True, args.dry_run, args.show_paths, args.wait)
    for k, src in enumerate(sources, start=1):
        if not src.exists():
            print(f" source {k}: does not exist")
            continue
        print(f" source {k}: scanning ...")
        found += scan(src, confs)
    return place(found, False, args.dry_run, args.show_paths, args.wait)


def has_data(name):
    root = PROJECTS_DIR / name
    return (any(subdirs(root / "Patient Files"))
            or any(p.name.startswith("sub-") for p in subdirs(root / "sourcedata" / "received"))
            or (root / "bids").is_dir())


def cmd_run(args, extra):
    """inbox/ first, then the batch of every project that has data, in turn."""
    if inbox_items():
        args.folders, args.dry_run = [], False
        cmd_import(args)
    worst = 0
    for name in projects():
        if not has_data(name):
            print(f"\n [{name}] no data yet; nothing to run.")
            continue
        print(f"\n [{name}] batch ...", flush=True)
        r = subprocess.run([sys.executable, str(CODE / "run_batch.py"), "--project", name,
                            "--wait", str(args.wait), *extra], cwd=str(REPO_ROOT))
        worst = max(worst, r.returncode)
    return worst


def cmd_status(_args):
    for name, conf in projects().items():
        root = PROJECTS_DIR / name
        exams = len(subdirs(root / "Patient Files"))
        received = sum(p.name.startswith("sub-") for p in subdirs(root / "sourcedata" / "received"))
        fsdir = root / "bids" / "derivatives" / "freesurfer"
        done = sum((d / "scripts" / "recon-all.done").is_file() for d in subdirs(fsdir))
        result = "never run"
        summary = root / "logs" / "batch" / "last_summary.tsv"
        if summary.is_file():
            for line in summary.read_text(encoding="utf-8").splitlines():
                parts = line.split("\t")
                if parts[:3] == ["run", "", "finished"]:
                    result = "last run " + parts[4]
                if parts[:3] == ["run", "", "result"]:
                    result += f" ({parts[3]})"
        print(f" {name:<9} {conf.get('title', name)}: {exams} exam folder(s), "
              f"{received} delivered BIDS subject(s), {done} FreeSurfer run(s) done; {result}")
    print(f" inbox/: {len(inbox_items())} item(s) waiting")


def main():
    parser = argparse.ArgumentParser(
        description="Plug-and-play front door: import new data and run every project.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_imp = sub.add_parser("import", help="Place new data in its project.")
    p_imp.add_argument("folders", nargs="*", help="Folders to copy from (default: inbox/, moved).")
    p_imp.add_argument("--dry-run", action="store_true", help="Show the plan only.")
    p_imp.add_argument("--show-paths", action="store_true",
                       help="Also show where items left alone are (screen only).")
    p_imp.add_argument("--wait", type=float, default=0,
                       help="Minutes to wait if a batch is running.")
    p_run = sub.add_parser("run", help="Import inbox/, then run each project's batch.")
    p_run.add_argument("--wait", type=float, default=0,
                       help="Minutes to wait if another batch is running.")
    p_run.add_argument("--show-paths", action="store_true")
    p_sch = sub.add_parser("schedule", help="Run every night (install_schedule.py --all).")
    sub.add_parser("status", help="What each project holds.")
    args, extra = parser.parse_known_args()

    if args.command == "import":
        if extra:
            parser.error(f"unknown arguments: {' '.join(extra)}")
        cmd_import(args)
    elif args.command == "run":
        sys.exit(cmd_run(args, extra))
    elif args.command == "schedule":
        sys.exit(subprocess.run([sys.executable, str(CODE / "install_schedule.py"),
                                 "--all", *extra], cwd=str(REPO_ROOT)).returncode)
    else:
        cmd_status(args)


if __name__ == "__main__":
    main()

"""
paths.py
========

ONE place that knows the project layout.

The pipeline code is SHARED; each study keeps its data in its own folder under
``projects/``. Every script imports this module, which picks the study and
exposes the same names for its data locations. That means:

  * the scripts run the same whether you launch them from the repository root
    (``python code/dicom_to_bids.py --project CBR``) or from inside ``code/`` --
    Python always puts the script's own folder on ``sys.path``, so
    ``import paths`` resolves just like ``import series_classifier``;
  * if a dataset is ever moved or renamed, this is the only file to edit.

WHICH PROJECT? (first match wins)
  1. ``--project NAME`` (or ``--project=NAME``) on the command line. It is
     removed from ``sys.argv`` here, so each script's own argument handling
     never sees it.
  2. the ``PIPELINE_PROJECT`` environment variable.
  3. the only folder in ``projects/`` that has a ``project.json``.
With more than one project and no choice, the script stops and lists them.

THE LAYOUT

    <repository root>/
      code/                          <- the shared scripts (this file is here)
      Pipeline_Alunos/               <- guided menu for students
      projects/
        <NAME>/
          project.json               <- per-study settings (tracked in git)
          Patient Files/             <- raw DICOM input (read-only, never touched)
          sourcedata/received/       <- BIDS as delivered by another team
                                        (read-only; identifiers still inside)
          bids/                      <- the single, OpenNeuro-ready BIDS root
            sub-*/ses-*/...          <- raw BIDS (the upload target)
            sourcedata/
              conversion/            <- the working/pre-BIDS tree (descriptive
                                        names + provenance.csv + logs + reports)
              asl/ fmap/ func/ unknown/  <- scaffolds pending researcher input
            derivatives/
              scanner-derived/ defaced/ freesurfer/ samseg/ qc/
          logs/batch/                <- run_batch.py logs + summaries (kept local)
          archive/                   <- timestamped backups (kept local)
          deliverable/               <- generated PDFs / bundles (no raw data)

Nothing here creates folders or touches the filesystem except reading
``project.json``; it only names places.
"""

import json
import os
import sys
from pathlib import Path

# code/  ->  parent is the repository root that holds projects/.
CODE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CODE_DIR.parent
# PIPELINE_PROJECTS_DIR points the pipeline at another projects/ folder (used by
# the self-tests, so synthetic data never lands next to real studies).
PROJECTS_DIR = Path(os.environ.get("PIPELINE_PROJECTS_DIR") or REPO_ROOT / "projects")


def available_projects():
    """Names of the folders in projects/ that carry a project.json."""
    return sorted(p.parent.name for p in PROJECTS_DIR.glob("*/project.json"))


def _take_project_arg():
    """Pop '--project NAME' / '--project=NAME' from sys.argv; return NAME or None."""
    for i, arg in enumerate(sys.argv[1:], start=1):
        if arg == "--project" and i + 1 < len(sys.argv):
            name = sys.argv[i + 1]
            del sys.argv[i:i + 2]
            return name
        if arg.startswith("--project="):
            del sys.argv[i]
            return arg.split("=", 1)[1]
    return None


def _pick_project():
    choices = available_projects()
    name = _take_project_arg() or os.environ.get("PIPELINE_PROJECT")
    if name is None and len(choices) == 1:
        name = choices[0]
    if name is None:
        sys.exit("ERROR: choose a project with --project NAME "
                 f"(available: {', '.join(choices) or 'none in ' + str(PROJECTS_DIR)}).")
    if name not in choices:
        sys.exit(f"ERROR: unknown project '{name}' "
                 f"(available: {', '.join(choices) or 'none'}).")
    return name


PROJECT_NAME = _pick_project()

# This study's data folder and its settings (see projects/<NAME>/project.json).
PROJECT_ROOT = PROJECTS_DIR / PROJECT_NAME
with open(PROJECT_ROOT / "project.json", "r", encoding="utf-8") as _f:
    PROJECT = json.load(_f)

# Raw DICOM input: one folder per exam. Read-only; the pipeline never writes here.
PATIENT_FILES = PROJECT_ROOT / "Patient Files"

# BIDS delivered by another team (import_bids.py). Read-only: it still carries
# site identifiers; import_bids.py builds the de-identified copy.
RECEIVED_DIR = PROJECT_ROOT / "sourcedata" / "received"

# Visit number <-> exam date, for projects that number sessions by date order
# ("sessions": "date_order" in project.json). Dates are identifiers, so this
# map stays here, outside bids/, and never travels with the dataset.
SESSION_MAP = PROJECT_ROOT / "sourcedata" / "session_map.tsv"

# Study-specific series rules (same format as code/series_map.csv). Rows here
# win over the shared map, so one study's scanner names never change another's.
PROJECT_SERIES_MAP = PROJECT_ROOT / "series_map.csv"

# The single BIDS dataset root: raw + sourcedata/ + derivatives/, OpenNeuro-ready.
BIDS_ROOT = PROJECT_ROOT / "bids"

# The working/pre-BIDS conversion tree (dcm2niix output with descriptive scanner
# names, provenance.csv, validation_report.csv, logs and reports/). It is the
# source of truth the BIDS builders read from. Kept under sourcedata/ -- the
# BIDS-standard home for pre-conversion source data -- so the whole study is one
# self-contained folder.
CONVERSION_DIR = BIDS_ROOT / "sourcedata" / "conversion"

# FreeSurfer SUBJECTS_DIR, declared as a BIDS derivative.
FREESURFER_DIR = BIDS_ROOT / "derivatives" / "freesurfer"

# SAMSEG white-matter lesion runs (T1w + FLAIR), one folder per session.
SAMSEG_DIR = BIDS_ROOT / "derivatives" / "samseg"

# Generated, shareable deliverables (PDFs, handoff/export bundles). No raw data.
DELIVERABLE_DIR = PROJECT_ROOT / "deliverable"

# Timestamped backups of previous runs (patient-derived; stays on this machine).
ARCHIVE_DIR = PROJECT_ROOT / "archive"

# Unattended runs (run_batch.py): one folder per run with the step logs and the
# end-of-run summary, plus the retry state. Git-ignored; codes only, no names.
BATCH_DIR = PROJECT_ROOT / "logs" / "batch"

# One batch at a time on this machine, whatever the project: they share the
# cores, the memory and the disk.
BATCH_LOCK = PROJECTS_DIR / ".batch.lock"

# Visual QC decisions (pass/fail + note per session and kind), recorded from the
# student menu. Inside bids/derivatives so it stays with the outputs it judges.
QC_FILE = BIDS_ROOT / "derivatives" / "qc" / "qc_status.tsv"


if __name__ == "__main__":
    # `python code/paths.py --project NAME` prints this project's layout as JSON,
    # so tools outside code/ (the student menu) never re-derive a path.
    print(json.dumps({"PROJECT_NAME": PROJECT_NAME,
                      **{k: str(v) for k, v in sorted(globals().items())
                         if isinstance(v, Path)}}, indent=1))

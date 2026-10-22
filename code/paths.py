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
              scanner-derived/ defaced/ freesurfer/
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
PROJECTS_DIR = REPO_ROOT / "projects"


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

# BIDS delivered by another team (input "bids" in project.json). Read-only: it
# still carries site identifiers; import_bids.py builds the de-identified copy.
RECEIVED_DIR = PROJECT_ROOT / "sourcedata" / "received"

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

# Generated, shareable deliverables (PDFs, handoff/export bundles). No raw data.
DELIVERABLE_DIR = PROJECT_ROOT / "deliverable"

# Timestamped backups of previous runs (patient-derived; stays on this machine).
ARCHIVE_DIR = PROJECT_ROOT / "archive"

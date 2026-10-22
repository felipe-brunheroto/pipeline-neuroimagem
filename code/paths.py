"""
paths.py
========

ONE place that knows the project layout.

Every pipeline script lives in ``code/`` but reads and writes data that lives at
the PROJECT ROOT (one level up). Defining those locations here means:

  * the scripts run the same whether you launch them from the project root
    (``python code/dicom_to_bids.py``) or from inside ``code/`` -- Python always
    puts the script's own folder on ``sys.path``, so ``import paths`` resolves
    just like ``import series_classifier``;
  * if the dataset is ever moved or renamed, this is the only file to edit.

THE LAYOUT (everything for the study lives under one BIDS root, ``bids/``):

    <project root>/
      code/                          <- the scripts (this file is here)
      Patient Files/                 <- raw DICOM input (read-only, never touched)
      bids/                          <- the single, OpenNeuro-ready BIDS root
        sub-*/ses-*/...              <- raw BIDS (the upload target)
        sourcedata/
          conversion/                <- the working/pre-BIDS tree (descriptive
                                        names + provenance.csv + logs + reports)
          asl/ fmap/ func/ unknown/  <- scaffolds pending researcher input
        derivatives/
          scanner-derived/ defaced/ freesurfer/
      archive/                       <- timestamped backups (kept local)

Nothing here creates folders or touches the filesystem; it only names places.
"""

from pathlib import Path

# code/  ->  parent is the project root that holds the data folders.
CODE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CODE_DIR.parent

# Raw DICOM input: one folder per exam. Read-only; the pipeline never writes here.
PATIENT_FILES = PROJECT_ROOT / "Patient Files"

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

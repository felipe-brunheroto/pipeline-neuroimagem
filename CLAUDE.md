# Project: shared neuroimaging pipeline (CBR / NEUROCOVID and COGAGING)

This folder contains a small, local-only neuroimaging conversion pipeline.
If you are an AI assistant (e.g. Claude Code) working in this folder, READ AND
FOLLOW the rules below before doing anything.

## What this project does
One shared pipeline serves several studies. Each study has its own folder
under `projects/<NAME>/` with a `project.json` describing it:
- `projects/CBR/` — CBR / NEUROCOVID. Input: raw DICOM (one folder per exam,
  e.g. `CBR-003`) in `projects/CBR/Patient Files/`, converted to NIfTI
  (`.nii.gz`) and organized into a BIDS dataset.
- `projects/COGAGING/` — Cog-Aging. Input: raw DICOM from clinical CDs in
  `projects/COGAGING/Patient Files/` (folder names carry the code, e.g.
  `GER00039_...`; the rest of the name may hold a patient name and is never
  written out). Sessions are numbered by exam date; the date map stays in
  `projects/COGAGING/sourcedata/session_map.tsv`, outside `bids/`. Subjects
  already converted with dcm2bids are imported from
  `projects/COGAGING/sourcedata/received/` with `code/import_bids.py`.
  No defacing until the study decides about external sharing.

The two studies are INDEPENDENT: they share only the code. Neither inherits the
other's series rules (`projects/<NAME>/series_map.csv`), protocol facts
(`project.json` "acquisition"), outputs, logs or QC. New data is routed to its
study by the code in its name (`project.json` "code_pattern": CBR..., GER...).

Each study's outputs live in a single, OpenNeuro-ready BIDS dataset under
`projects/<NAME>/bids/` (raw `sub-*`, `sourcedata/`, and `derivatives/`), with
provenance/import logs. See `STRUCTURE.md` for the full layout.

Layout: all scripts live in `code/` and run as
`python code/<script>.py --project NAME`; `code/paths.py` is the single source
of truth for where data lives.

Main scripts (already written and tested):
- `code/check_environment.py` — checks that Python, dcm2niix and pydicom are ready.
- `code/diagnostico.py`        — guided check of the whole Mac, in Portuguese, for
                            students (FreeSurfer + license, disk, FileVault, the
                            nightly job, last runs); safe fixes only on a per-item yes.
- `code/dicom_to_bids.py`      — DICOM projects: reads DICOMs, converts with
                            dcm2niix, sorts outputs, writes provenance.csv and
                            validation_report.csv into `bids/sourcedata/conversion/`.
- `code/import_bids.py`        — subjects delivered already in BIDS: builds the
                            de-identified `bids/` from the delivered copy.
- `code/run_recon_long.py`     — FreeSurfer recon-all (flags per project.json).
- `code/run_samseg.py`         — SAMSEG white-matter lesions (COGAGING).
- `code/run_batch.py`          — unattended run of every pending step (lock,
                            logs + summary in `projects/<NAME>/logs/batch/`);
                            `code/install_schedule.py` schedules it nightly.
- `start.command` / `code/pipeline.py` — plug and play for all studies: update,
                            set up, import (DICOM, dcm2bids BIDS, FreeSurfer runs,
                            old pipeline copies; `inbox/` drop box) and process;
                            `code/import_inputs.py` places items, never overwriting.
- `tests/`                     — self-tests on SYNTHETIC data only (generated at
                            test time under a temp `PIPELINE_PROJECTS_DIR`).

## SAFETY AND ETHICS RULES — do not break these
1. **This is real patient MRI data.** It must stay on this computer.
   NEVER upload, copy, paste, or send DICOM files or their contents to any
   website, cloud service, or chat. All processing is LOCAL only.
2. **Never modify or delete the original data.** Treat every existing file in
   the DICOM exam folders (e.g. `projects/CBR/Patient Files/CBR-003`) and in
   delivered BIDS (`projects/*/sourcedata/received/`) as read-only. Only
   create NEW files inside `projects/<NAME>/bids/`.
3. **Keep outputs de-identified.** Output NIfTI/JSON/CSV must use only the
   project subject codes (e.g. `sub-CBR003`, `sub-GER00007`) — never patient
   IDs, birth dates, accession numbers, or institution names.
4. **Ask before each action.** Stay in the default permission mode where you
   request approval before running commands. Do NOT use any
   "skip-permissions" / auto-accept mode while patient data is present.
5. If anything is ambiguous or risky, STOP and ask the user rather than guess.

## How to run the pipeline
1. Make sure the tools are installed (Python 3, then `pip install pydicom`
   and `pip install dcm2niix`).
2. Run `python code/check_environment.py --project CBR` and confirm it says
   "GOOD TO GO".
3. DICOM projects: run `python code/dicom_to_bids.py --project CBR` (defaults
   read `projects/CBR/Patient Files/` and write to
   `projects/CBR/bids/sourcedata/conversion/`). Same for COGAGING with
   `--project COGAGING`; legacy dcm2bids subjects: run
   `python code/import_bids.py --project COGAGING`.
4. Review `bids/sourcedata/conversion/validation_report.csv` (DICOM projects)
   or the import log in `bids/sourcedata/import/` (BIDS projects) — any row
   marked `MISMATCH` or any reported problem should be checked before trusting
   that scan.

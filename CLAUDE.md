# Project: DICOM → NIfTI → BIDS-like conversion (CBR / NEUROCOVID)

This folder contains a small, local-only neuroimaging conversion pipeline.
If you are an AI assistant (e.g. Claude Code) working in this folder, READ AND
FOLLOW the rules below before doing anything.

## What this project does
Convert raw DICOM MRI data (one folder per subject, e.g. `CBR-003`) into
NIfTI (`.nii.gz`) and organize it into a simple BIDS-like structure
(`anat / dwi / fmap / perf / swi / misc`), with a provenance log and a
validation report.

Main scripts (already written and tested):
- `check_environment.py` — checks that Python, dcm2niix and pydicom are ready.
- `dicom_to_bids.py`      — the full pipeline. Reads DICOMs, converts with
                            dcm2niix, sorts outputs, writes provenance.csv and
                            validation_report.csv into a `BIDS_output/` folder.

## SAFETY AND ETHICS RULES — do not break these
1. **This is real patient MRI data.** It must stay on this computer.
   NEVER upload, copy, paste, or send DICOM files or their contents to any
   website, cloud service, or chat. All processing is LOCAL only.
2. **Never modify or delete the original DICOM files.** Treat every existing
   file in the subject folders (e.g. `CBR-003`, `CBR-053`) as read-only.
   Only create NEW files inside `BIDS_output/`.
3. **Keep outputs de-identified.** Output NIfTI/JSON/CSV must use only the
   project subject codes (e.g. `sub-CBR003`) — never patient IDs, birth dates,
   accession numbers, or institution names.
4. **Ask before each action.** Stay in the default permission mode where you
   request approval before running commands. Do NOT use any
   "skip-permissions" / auto-accept mode while patient data is present.
5. If anything is ambiguous or risky, STOP and ask the user rather than guess.

## How to run the pipeline
1. Make sure the tools are installed (Python 3, then `pip install pydicom`
   and `pip install dcm2niix`).
2. Run `python check_environment.py` and confirm it says "GOOD TO GO".
3. Run `python dicom_to_bids.py` (defaults read this folder and write to
   `BIDS_output/`).
4. Review `BIDS_output/validation_report.csv` — any row marked `MISMATCH`
   should be checked before trusting that scan.

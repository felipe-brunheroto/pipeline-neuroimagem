# CBR / NEUROCOVID — DICOM → NIfTI → BIDS-like pipeline

A small, **local-only** pipeline that converts raw DICOM MRI into NIfTI,
organizes it into a BIDS-like layout (`anat / dwi / fmap / misc`), records
provenance, validates key fields, and de-identifies the JSON sidecars to an
Open-Science standard.

> **Golden rule: code travels, data does not.** The scripts in this repo contain
> no patient data and can be shared/version-controlled freely. Raw DICOMs and
> the `BIDS_output/` folder are patient-derived and must stay on the secure
> research machine — never on personal cloud storage (OneDrive/Dropbox/Drive).

---

## Files in this pipeline (safe to transfer)

| File | What it is |
|------|------------|
| `check_environment.py` | Verifies Python, dcm2niix, pydicom are installed. Changes nothing. |
| `dicom_to_bids.py` | Main pipeline: DICOM → NIfTI, sorts to anat/dwi/fmap/misc, writes provenance + validation reports. |
| `deidentify_sidecars.py` | Post-step: strips site/scanner identifiers from JSON sidecars (DICOM PS3.15 Basic Confidentiality Profile). |
| `requirements.txt` | Python dependencies, pinned to validated versions. |
| `CLAUDE.md` | Safety/ethics rules for the project. |
| `.gitignore` | Safety net: blocks git from ever tracking patient data. |
| `README.md` | This file. |

**Never transfer via personal/cloud means:** the `CBR-*` subject folders and the
`BIDS_output/` folder.

---

## Setup on a new (workplace) computer — Windows

1. **Choose a local-only folder, NOT inside OneDrive/Dropbox/Google Drive.**
   Example: `C:\Research\CBR\`. Copy the seven pipeline files above into it.

2. **Install Python 3.12** (once):
   ```powershell
   winget install --id Python.Python.3.12 -e --source winget
   ```
   Close and reopen the terminal so `python` is on PATH.

3. **Install the Python dependencies:**
   ```powershell
   python -m pip install -r requirements.txt
   ```
   (The `dcm2niix` pip package bundles the converter binary. If your site
   prefers a managed install, use `conda install -c conda-forge dcm2niix`
   instead and drop dcm2niix from `requirements.txt`.)

4. **Verify the environment:**
   ```powershell
   python check_environment.py
   ```
   Confirm it prints **GOOD TO GO**.

5. **Place the patient DICOM data locally**, one folder per subject
   (e.g. `C:\Research\CBR\CBR-003\`), transferred by an approved institutional
   method (encrypted drive / secure share) — not personal cloud.

6. **Edit paths if needed.** Open `dicom_to_bids.py` and
   `deidentify_sidecars.py` and update the `INPUT_DIR` / `OUTPUT_DIR` /
   `OUTPUT_DIR` constants near the top to your local folder. (Or pass paths on
   the command line — see below.)

---

## Running the pipeline

```powershell
# 1. Convert + organize + validate
python dicom_to_bids.py            "C:\Research\CBR" "C:\Research\CBR\BIDS_output"

# 2. De-identify the JSON sidecars (site/scanner fields)
python deidentify_sidecars.py      "C:\Research\CBR\BIDS_output"
```

Then review, in `BIDS_output/`:
- `validation_report.csv` — any `MISMATCH` row should be checked before trusting that scan.
- `provenance.csv` — links each NIfTI back to its DICOM series.
- `deidentification_log.txt` — what the de-id step removed.
- `conversion_log.txt` — full run log.

**Treat steps 1 + 2 as one pipeline.** Data is not "shareable/de-identified"
until step 2 has run and an audit shows zero identifying fields.

---

## Known caveats

- **Diffusion `*_TRACEW` TR mismatch** in `validation_report.csv` is benign
  (dcm2niix issue #560 — it writes the estimated multi-volume TR). Image data is
  unaffected.
- **`misc/` routing**: ASL/perfusion, SWI/phase/magnitude/mIP, and ADC maps fall
  outside the simple anat/dwi/fmap keyword rules and land in `misc/`. Extend the
  keyword lists in `dicom_to_bids.py` if you want finer routing (e.g. a `perf/`
  folder for ASL).
- **Defacing**: high-resolution anatomical scans (T1/T2/FLAIR) still contain
  facial features and are re-identifiable. For data sharing beyond the secure
  machine, deface them (e.g. `pydeface`) before release.

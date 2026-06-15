# CBR / NEUROCOVID — DICOM → NIfTI → BIDS-like pipeline

A small, **local-only** pipeline that converts raw DICOM MRI into NIfTI,
organizes it into a BIDS-like layout (`anat / dwi / fmap / perf / swi / misc`),
records
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
| `dicom_to_bids.py` | Main pipeline: DICOM → NIfTI, sorts to anat/dwi/fmap/perf/swi/misc, writes provenance + validation reports. |
| `deidentify_sidecars.py` | Post-step: strips site/scanner identifiers from JSON sidecars (DICOM PS3.15 Basic Confidentiality Profile). |
| `deface_anat.py` | Post-step: defaces the anatomical scans (T1/T2/FLAIR) with FreeSurfer `mideface`. Writes defaced copies to `BIDS_output/derivatives/defaced/`; originals untouched. Needs FreeSurfer 7.3+. |
| `make_bids.py` | Post-step: builds a validator-clean, FAIR BIDS dataset of the anatomicals under `bids/` (proper `T1w`/`T2w`/`FLAIR` suffixes, `acq-` preserves protocol, plus `dataset_description.json`/`participants.tsv`/`sessions.tsv`). Reproducible, stdlib-only, never modifies `BIDS_output/`. |
| `run_recon_long.py` | Analysis driver (runs on the **FreeSurfer 8 Mac**, not the conversion Mac): plans + runs the FreeSurfer longitudinal stream (cross → within-subject base → long). `--dry-run` prints the plan with no FreeSurfer needed. |
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

Paths now default to `Patient Files/` (input) and `BIDS_output/` (output) next
to the scripts, so on the primary machine you can run them with no arguments.
Explicit paths still work as an override:

```bash
# 1. Convert + organize + validate
python dicom_to_bids.py

# 2. De-identify the JSON sidecars (site/scanner fields)
python deidentify_sidecars.py

# 3. Deface the anatomical scans (T1/T2/FLAIR) — needs FreeSurfer 7.3+ (mideface).
#    Writes defaced copies to BIDS_output/derivatives/defaced/; originals kept.
python deface_anat.py --dry-run    # preview (no FreeSurfer needed)
python deface_anat.py              # actually deface

# 4. Build the FAIR, validator-clean BIDS dataset (anatomicals) under bids/.
python make_bids.py
```

To verify `bids/` is BIDS-valid, run the official validator on a machine that
has it (e.g. the FreeSurfer/recon Mac): `deno run -A jsr:@bids/validator bids`,
or `npx @bids/validator bids`. A quick local sanity check: `pip install pybids`
then `python -c "from bids import BIDSLayout; print(BIDSLayout('bids'))"`.
The data never leaves the machine in any case.

---

## Structural analysis (FreeSurfer 8 — on the dedicated recon Mac)

Conversion/defacing/QC happen on the conversion Mac; the heavy recon runs on a
separate **M4 / 64 GB Mac with FreeSurfer 8** (native Apple-Silicon — no Rosetta;
needs a free FreeSurfer license registered to `$FREESURFER_HOME/license.txt`).

Workflow:

1. **Deface, then transfer.** Run `deface_anat.py` (this Mac or the recon Mac),
   then move the defaced anatomicals to the recon Mac by an approved local
   method — never personal cloud. Point recon at the defaced tree.
2. **Source FreeSurfer 8**, then plan and run the longitudinal stream:
   ```bash
   export FREESURFER_HOME=/Applications/freesurfer/8.0.0
   source $FREESURFER_HOME/SetUpFreeSurfer.sh

   python run_recon_long.py --dry-run                       # preview the plan
   python run_recon_long.py --input BIDS_output/derivatives/defaced \
       --subjects-dir ~/fs_subjects --jobs 4 --threads 4    # cross -> base -> long
   ```
   ~8 GB RAM + `threads` cores per job; `--jobs 4` is comfortable on 64 GB.
3. **QC** the surfaces (freeview; check the Euler number) before trusting any
   subject.
4. **Extract metrics** with `asegstats2table` / `aparcstats2table`, then fit
   mixed-effects models (region ~ clinical_status × time + (1│subject)). With
   N=4 this is a pilot — favour hypothesis-driven ROIs over whole-brain vertexwise.

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
- **`misc/` routing**: ASL/perfusion → `perf/`, the SWI family (SWI/phase/
  magnitude/mIP) → `swi/`, and ADC maps → `dwi/`. Only localizers (`AAHead_Scout`)
  now land in `misc/`. Extend the keyword lists in `dicom_to_bids.py` for finer
  routing.
- **Defacing**: run `deface_anat.py` (FreeSurfer `mideface`) on the anatomical
  scans before any sharing beyond the secure machine. Defaced copies go to
  `BIDS_output/derivatives/defaced/`; the originals are never modified. Deface
  every timepoint with the same tool to keep longitudinal comparisons consistent.

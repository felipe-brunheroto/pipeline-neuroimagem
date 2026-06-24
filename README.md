# CBR / NEUROCOVID — DICOM → NIfTI → BIDS-like pipeline

A small, **local-only** pipeline that converts raw DICOM MRI into NIfTI,
organizes it into a working layout (`anat / func / dwi / fmap / perf / swi /
misc / unknown`), records provenance, validates key fields, de-identifies the
JSON sidecars to an Open-Science standard, and curates a validator-clean BIDS
dataset (`bids/`) with proper derivatives and sourcedata. Unfamiliar series
(e.g. a future BOLD project) are classified via an editable `series_map.csv`,
and anything unrecognized is quarantined and reported rather than mis-sorted.

> **Golden rule: code travels, data does not.** The scripts in this repo contain
> no patient data and can be shared/version-controlled freely. Raw DICOMs and
> the `BIDS_output/` folder are patient-derived and must stay on the secure
> research machine — never on personal cloud storage (OneDrive/Dropbox/Drive).

---

## Files in this pipeline (safe to transfer)

| File | What it is |
|------|------------|
| `check_environment.py` | Verifies Python, dcm2niix, pydicom are installed, **and creates the `Patient Files/` input folder** (with a README) on first run so data is dropped in the right place. |
| `setup_workspace.py` | Creates/validates the `Patient Files/` drop folder and diagnoses common mistakes (pointing inside one exam, empty folder). Imported by the two above; also runnable directly. |
| `series_classifier.py` | Shared rules that decide each series' datatype + BIDS suffix. Reads `series_map.csv` first, then built-in keywords; unrecognized → `unknown`. |
| `series_map.csv` | **Operator-editable** map: teaches the pipeline new series (e.g. a future BOLD study) without code changes. |
| `dicom_to_bids.py` | Main pipeline: DICOM → NIfTI, sorts to anat/func/dwi/fmap/perf/swi/misc, writes provenance + validation + an **unknown-series report** and a readiness summary. |
| `deidentify_sidecars.py` | Post-step: strips site/scanner identifiers from JSON sidecars (DICOM PS3.15 Basic Confidentiality Profile). |
| `make_bids.py` | Builds the validator-clean, FAIR raw BIDS dataset under `bids/`: anatomicals (`T1w`/`T2w`/`FLAIR`) and functional (`bold`, when a task is known). `acq-` preserves the protocol; incomplete BOLD is held under `sourcedata/func/`. |
| `make_derivatives.py` | Builds `bids/derivatives/scanner-derived/` (ADC, TRACEW, SWI, perfusion maps) and the `bids/sourcedata/` scaffolds for raw ASL (partially filled from the DICOMs) and the phase-only field map, plus quarantined `unknown` series. |
| `deface_anat.py` | Defaces the anatomicals (T1/T2/FLAIR) with FreeSurfer `mideface`, reading `bids/` and writing `bids/derivatives/defaced/` with `desc-defaced` names + a derivatives `dataset_description.json`. Needs FreeSurfer 7.3+. Run AFTER `make_bids.py`. |
| `run_recon_long.py` | Analysis driver (runs on the **FreeSurfer 8 Mac**): plans + runs the longitudinal stream (cross → base → long) into `bids/derivatives/freesurfer/`. `--dry-run` needs no FreeSurfer. |
| `extract_fs_stats.py` | Final step: wraps `asegstats2table`/`aparcstats2table` into tidy tables under `bids/derivatives/freesurfer/stats/`. `--dry-run` prints the plan. |
| `Pipeline_Alunos/` | **Student teaching package** (Brazilian Portuguese, non-programmer-friendly): double-click `iniciar.command` → guided menu (`passos.py`) that runs each step; `GUIA_DO_ALUNO.pdf` walkthrough; FAIR/safety notes. Runs with no Claude account. |
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
# 1. Convert + organize + validate (also writes reports/unknown_series_report.csv)
python dicom_to_bids.py

# 2. De-identify the JSON sidecars (site/scanner fields)
python deidentify_sidecars.py

# 3. Build the FAIR, validator-clean raw BIDS dataset (anat + func) under bids/.
python make_bids.py

# 4. Build derivatives + sourcedata: scanner-derived maps (ADC/TRACEW/SWI/
#    perfusion) under bids/derivatives/scanner-derived/, and ASL + phase-only
#    field map scaffolds under bids/sourcedata/.
python make_derivatives.py

# 5. Deface the anatomicals (T1/T2/FLAIR) — needs FreeSurfer 7.3+ (mideface).
#    Reads bids/, writes bids/derivatives/defaced/ with desc-defaced names.
python deface_anat.py --dry-run    # preview (no FreeSurfer needed)
python deface_anat.py              # actually deface
```

**New project / unfamiliar series (e.g. BOLD):** the pipeline never crashes on
data it doesn't recognize. Any series no rule matches is converted, parked in
`bids/sourcedata/unknown/`, and listed in `reports/unknown_series_report.csv`
with the DICOM hints needed to classify it; ready-to-edit rows are written to
`reports/series_map_additions.csv`. Fill in the `datatype`/`suffix`, paste the
rows into `series_map.csv`, and re-run — no code changes. BOLD runs are detected
automatically (by name or DICOM `ImageType`); resting-state becomes
`task-rest_bold`, and task runs are held under `sourcedata/func/` until you set
`TaskName`.

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
   python run_recon_long.py --input bids/derivatives/defaced \
       --jobs 4 --threads 4                                 # cross -> base -> long
   ```
   Output lands in `bids/derivatives/freesurfer/` (a declared BIDS derivative);
   pass `--input bids/derivatives/defaced` to recon the privacy-preserving tree.
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
- **Routing & new series**: routing rules live in `series_map.csv` +
  `series_classifier.py` (no longer hardcoded in `dicom_to_bids.py`). Localizers
  (`AAHead_Scout`) land in `misc/`; anything unrecognized goes to `unknown/` and
  is reported for you to classify via `series_map.csv`.
- **Diffusion is derived only**: this protocol exports `TRACEW` + `ADC` (no
  bval/bvec), so they are curated as **derivatives** under
  `derivatives/scanner-derived/`, not raw `dwi/`.
- **ASL**: raw PASL is partially curated under `sourcedata/asl/` —
  `PostLabelingDelay` (from DICOM `InversionTime`), `M0Type:Absent`, and bolus
  cut-off fields (from `PASLType`/`BolusDuration`) are filled and annotated;
  `BackgroundSuppression`, `TotalAcquiredPairs`, and the per-volume
  `aslcontext.tsv` order must be confirmed by the researcher.
- **Field map**: only the **phase** image was acquired (no magnitude), so a valid
  BIDS fieldmap is impossible; it is kept under `sourcedata/fmap/` with a README.
- **Defacing**: run `deface_anat.py` (FreeSurfer `mideface`) **after**
  `make_bids.py`. Defaced copies go to `bids/derivatives/defaced/` with
  `desc-defaced` names; the originals are never modified. Deface every timepoint
  with the same tool to keep longitudinal comparisons consistent.

# Pipeline Neuroimagem — DICOM/BIDS → BIDS → FreeSurfer, for several studies

A small, **local-only** pipeline that converts raw DICOM MRI into NIfTI,
organizes it into a working layout (`anat / func / dwi / fmap / perf / swi /
misc / unknown`), records provenance, validates key fields, de-identifies the
JSON sidecars to an Open-Science standard, and curates a validator-clean BIDS
dataset (`bids/`) with proper derivatives and sourcedata. Unfamiliar series
(e.g. a future BOLD project) are classified via an editable `series_map.csv`,
and anything unrecognized is quarantined and reported rather than mis-sorted.

**One shared engine, one folder per study.** The scripts live in `code/`; each
study keeps its data in `projects/<NAME>/` with a `project.json` describing it:

| Project | Input | Notes |
|---|---|---|
| `CBR` (CBR / NEUROCOVID) | DICOM (`projects/CBR/Patient Files/`) | longitudinal structural MRI, SWI, clinical DWI, multi-TI PASL in some exams |
| `COGAGING` | DICOM from clinical CDs (`projects/COGAGING/Patient Files/`, folder names carry the code, e.g. `GER00039_...`); subjects already converted with dcm2bids enter via `import_bids.py` | multi-vendor; 3D T1w + FLAIR, 64-dir DWI, resting-state BOLD; sessions numbered by exam date; SAMSEG lesions; no defacing yet |

Per-study settings live in `projects/<NAME>/project.json` (input, subject code
pattern, session numbering, recon-all flags, which steps apply) and
`projects/<NAME>/series_map.csv` (that study's scanner series names, checked
before the shared `code/series_map.csv`).

Every command takes **`--project NAME`** (or set `PIPELINE_PROJECT`), e.g.
`python code/run_recon_long.py --project COGAGING --dry-run`. Paths written as
`bids/...` below are relative to that project's folder (`projects/<NAME>/bids/...`).
**Each study lives under one BIDS root** (raw `sub-*`, the working conversion
tree under `sourcedata/conversion/`, and all `derivatives/`), so each is one
self-contained, OpenNeuro-ready folder. See **[STRUCTURE.md](STRUCTURE.md)** for
the full layout, a step→folder map, and how to trace one patient. The examples
below use `--project CBR`; swap in your project's name.

> **Golden rule: code travels, data does not.** The scripts in `code/` contain
> no patient data and can be shared/version-controlled freely. Raw DICOMs and the
> `bids/` dataset (which includes the working conversion tree) are patient-derived
> and must stay on the secure research machine — never on personal cloud storage
> (OneDrive/Dropbox/Drive).

---

## Files in this pipeline (safe to transfer)

All scripts live in **`code/`** (run them as `python code/<script>.py`).
`code/paths.py` is the single source of truth for the project layout, so no
path-editing is needed when moving the project.

| File (under `code/`) | What it is |
|------|------------|
| `paths.py` | One place that defines the layout (`PATIENT_FILES`, `BIDS_ROOT`, `CONVERSION_DIR`, `FREESURFER_DIR`, …) for the project chosen with `--project`. Imported by the other scripts. |
| `check_environment.py` | Verifies Python, dcm2niix, pydicom are installed, **and creates the `Patient Files/` input folder** (with a README) on first run so data is dropped in the right place. |
| `setup_workspace.py` | Creates/validates the `Patient Files/` drop folder and diagnoses common mistakes (pointing inside one exam, empty folder). Imported by the two above; also runnable directly. |
| `series_classifier.py` | Shared rules that decide each series' datatype + BIDS suffix. Reads `series_map.csv` first, then built-in keywords; unrecognized → `unknown`. |
| `series_map.csv` | **Operator-editable** map: teaches the pipeline new series (e.g. a future BOLD study) without code changes. |
| `dicom_to_bids.py` | Main pipeline: DICOM → NIfTI, sorts to anat/func/dwi/fmap/perf/swi/misc, writes provenance + validation + an **unknown-series report** and a readiness summary. |
| `import_bids.py` | For subjects delivered **already in BIDS** (COGAGING subjects converted with dcm2bids): copies the delivered `sub-*` folders into `bids/`, numbers visits by date order (`ses-YYYYMM` or no session → `ses-1`, `ses-2`…), removes identifier fields from every sidecar, moves scanner-derived images (e.g. TRACEW) to `derivatives/scanner-derived/`, writes the dataset files and checks the result. The delivered copy is never modified. |
| `session_map.py` | Visit numbering by exam date for projects with `"sessions": "date_order"`. The date → number map stays in `projects/<NAME>/sourcedata/session_map.tsv`, outside `bids/`; numbers never change, and a new exam older than a numbered one stops the run. |
| `run_samseg.py` | White-matter lesions with FreeSurfer SAMSEG (T1w + FLAIR registered to T1, `--lesion`, threshold 0.3) into `bids/derivatives/samseg/`, plus `samseg_lesions.tsv` (mm³ and % of intracranial). Needs a native arm64 FreeSurfer (stops under Rosetta). `--dry-run` needs no FreeSurfer. |
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

**Never transfer via personal/cloud means:** the `CBR-*` subject folders
(`Patient Files/`) and the `bids/` dataset (it contains patient-derived imaging,
including the working conversion tree under `sourcedata/conversion/`).

---

## Setup on a new (workplace) computer — Windows

1. **Choose a local-only folder, NOT inside OneDrive/Dropbox/Google Drive.**
   Example: `C:\Research\CBR\`. Copy the project into it (keep the `code/`
   folder intact — the scripts import each other and `paths.py`).

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
   python code\check_environment.py
   ```
   Confirm it prints **GOOD TO GO**.

5. **Place the patient DICOM data locally**, one folder per subject
   (e.g. `C:\Research\CBR\Patient Files\CBR-003\`), transferred by an approved
   institutional method (encrypted drive / secure share) — not personal cloud.

6. **No path-editing needed.** Defaults come from `code/paths.py`
   (`Patient Files/` in, `bids/` out). To use a different location, pass paths on
   the command line — see below.

---

## Running the pipeline

Paths default (via `code/paths.py`) to `Patient Files/` (input) and the `bids/`
root (output; the working conversion tree lands in `bids/sourcedata/conversion/`),
so on the primary machine you can run them with no arguments. Run from the project
root. Explicit paths still work as an override:

```bash
# 1. Convert + organize + validate -> bids/sourcedata/conversion/
#    (also writes reports/unknown_series_report.csv there)
python code/dicom_to_bids.py --project CBR

# 2. De-identify the JSON sidecars (site/scanner fields)
python code/deidentify_sidecars.py --project CBR

# 3. Build the FAIR, validator-clean raw BIDS dataset (anat + func) -> bids/sub-*
python code/make_bids.py --project CBR

# 4. Build derivatives + sourcedata: scanner-derived maps (ADC/TRACEW/SWI/
#    perfusion) under bids/derivatives/scanner-derived/, and ASL + phase-only
#    field map scaffolds under bids/sourcedata/.
python code/make_derivatives.py --project CBR

# 5. Deface the anatomicals (T1/T2/FLAIR) — needs FreeSurfer 7.3+ (mideface).
#    Reads bids/, writes bids/derivatives/defaced/ with desc-defaced names.
python code/deface_anat.py --project CBR --dry-run    # preview (no FreeSurfer needed)
python code/deface_anat.py --project CBR              # actually deface
```

Re-running steps 3–4 is safe: each rebuilds only the subtree it owns and leaves
the FreeSurfer recon, defaced copies, and conversion source untouched.

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

### COGAGING

Same steps 1–4 with `--project COGAGING`. Each CD goes in its own folder in
`projects/COGAGING/Patient Files/`, and the folder name must contain the code
(`GER00039_...`); nothing else from the folder name is written anywhere.
Sessions are numbered by exam date. No defacing for now (study decision).
Then:

```bash
python code/run_recon_long.py --project COGAGING --dry-run   # recon-all -all -qcache -3T (Cog-Aging guide 3.2)
python code/run_recon_long.py --project COGAGING --jobs 2
python code/run_samseg.py --project COGAGING                 # lesions (guide section 4)
python code/extract_fs_stats.py --project COGAGING
```

Subjects already converted with dcm2bids: put their `sub-*` folders in
`projects/COGAGING/sourcedata/received/` (read-only from then on) and run
`python code/import_bids.py --project COGAGING`. Re-running skips subjects
already in `bids/` (`--force` rebuilds). `run_recon_long.py` refuses to mix
FreeSurfer versions in one SUBJECTS_DIR (`--allow-mixed-versions` overrides).

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

   python code/run_recon_long.py --project CBR --dry-run                  # preview the plan
   python code/run_recon_long.py --project CBR --input projects/CBR/bids/derivatives/defaced \
       --jobs 4 --threads 4                                 # cross -> base -> long
   ```
   Output lands in `bids/derivatives/freesurfer/` (a declared BIDS derivative);
   pass `--input projects/CBR/bids/derivatives/defaced` to recon the privacy-preserving tree.
   ~8 GB RAM + `threads` cores per job; `--jobs 4` is comfortable on 64 GB.
3. **QC** the surfaces (freeview; check the Euler number) before trusting any
   subject.
4. **Extract metrics** with `python code/extract_fs_stats.py --project CBR` (wraps
   `asegstats2table` / `aparcstats2table` into `bids/derivatives/freesurfer/stats/`),
   then fit mixed-effects models (region ~ clinical_status × time + (1│subject)).
   With N=4 this is a pilot — favour hypothesis-driven ROIs over whole-brain
   vertexwise.

Then review, in `bids/sourcedata/conversion/`:
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
